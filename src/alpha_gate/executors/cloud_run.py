"""Authenticated client for the trusted Alpha-Gate Cloud Run broker."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from alpha_gate.candidate import CandidateSourceError, CandidateValidator
from alpha_gate.executors.base import (
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)


class CloudRunExecutorConfig(BaseModel):
    """Remote broker endpoint and bounded transport behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service_url: str = Field(min_length=1)
    audience: str | None = None
    auth: Literal["gcloud", "google", "none"] = "gcloud"
    transport_overhead_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    max_response_bytes: int = Field(default=2_097_152, ge=1024, le=16_777_216)
    throttle_retries: int = Field(default=3, ge=0, le=8)
    throttle_retry_seconds: float = Field(default=0.25, ge=0.0, le=5.0)

    @model_validator(mode="after")
    def valid_service_url(self) -> CloudRunExecutorConfig:
        parsed = urlsplit(self.service_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("service_url must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("service_url must not contain a query or fragment")
        return self

    @property
    def endpoint(self) -> str:
        return f"{self.service_url.rstrip('/')}/v1/execute"

    @property
    def token_audience(self) -> str:
        return self.audience or self.service_url.rstrip("/")


IdentityTokenProvider = Callable[[str], str]


class HttpTransport(Protocol):
    def __call__(
        self,
        url: str,
        payload: bytes,
        headers: dict[str, str],
        timeout: float,
        max_response_bytes: int,
    ) -> bytes: ...


def google_identity_token(audience: str) -> str:
    """Fetch an ID token from Application Default Credentials lazily."""

    try:
        transport_module = importlib.import_module("google.auth.transport.requests")
        id_token_module = importlib.import_module("google.oauth2.id_token")
    except ImportError as exc:
        raise RuntimeError(
            "Google authentication is unavailable; install the cloud extra"
        ) from exc
    request_factory = cast(Callable[[], object], transport_module.Request)
    fetch_token = cast(
        Callable[[object, str], str],
        id_token_module.fetch_id_token,
    )
    return fetch_token(request_factory(), audience)


def gcloud_identity_token(_audience: str) -> str:
    """Fetch a developer ID token from the active gcloud identity."""

    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise RuntimeError("gcloud executable was not found")
    completed = subprocess.run(
        [gcloud, "auth", "print-identity-token", "--quiet"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30.0,
    )
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise RuntimeError("gcloud could not mint an identity token")
    return token


def _http_post(
    url: str,
    payload: bytes,
    headers: dict[str, str],
    timeout: float,
    max_response_bytes: int,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = cast(bytes, response.read(max_response_bytes + 1))
    if len(body) > max_response_bytes:
        raise ValueError("remote broker response exceeded max_response_bytes")
    return body


class CloudRunExecutor(SandboxExecutor):
    """Send a complete request to a trusted broker, never to candidate code."""

    def __init__(
        self,
        config: CloudRunExecutorConfig,
        *,
        token_provider: IdentityTokenProvider | None = None,
        transport: HttpTransport = _http_post,
    ) -> None:
        self.config = config
        self._token_provider = token_provider or self._default_token_provider()
        self._transport = transport
        self._cached_token = ""
        self._token_expiry = 0.0

    def _default_token_provider(self) -> IdentityTokenProvider:
        if self.config.auth == "gcloud":
            return gcloud_identity_token
        return google_identity_token

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        try:
            CandidateValidator.validate(request.program)
        except CandidateSourceError as exc:
            return SandboxResult(
                status=SandboxStatus.INVALID,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=str(exc),
            )

        try:
            body = await asyncio.to_thread(self._execute_sync, request)
            result = SandboxResult.model_validate_json(body)
        except urllib.error.HTTPError as exc:
            return self._unavailable(
                request,
                started,
                f"Cloud Run broker returned HTTP {exc.code}",
            )
        except (
            OSError,
            RuntimeError,
            TimeoutError,
            urllib.error.URLError,
            ValidationError,
            ValueError,
        ) as exc:
            return self._unavailable(
                request,
                started,
                f"Cloud Run broker failed: {type(exc).__name__}",
            )

        if result.program_sha256 != request.program.sha256:
            return SandboxResult(
                status=SandboxStatus.PROTOCOL_ERROR,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error="Cloud Run broker returned a mismatched program hash",
            )
        return result

    def _execute_sync(self, request: SandboxRequest) -> bytes:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.config.auth != "none":
            headers["Authorization"] = f"Bearer {self._identity_token()}"
        timeout = (
            request.limits.timeout_seconds + self.config.transport_overhead_seconds
        )
        deadline = time.monotonic() + timeout
        payload = request.model_dump_json().encode("utf-8")
        for attempt in range(self.config.throttle_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("Cloud Run broker transport deadline expired")
            try:
                return self._transport(
                    self.config.endpoint,
                    payload,
                    headers,
                    remaining,
                    self.config.max_response_bytes,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == self.config.throttle_retries:
                    raise
                delay = self.config.throttle_retry_seconds * (2**attempt)
                time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        raise AssertionError("unreachable throttle retry state")

    def _identity_token(self) -> str:
        now = time.time()
        if self._cached_token and self._token_expiry > now + 60.0:
            return self._cached_token
        token = self._token_provider(self.config.token_audience)
        if not token:
            raise RuntimeError("identity token provider returned an empty token")
        self._cached_token = token
        self._token_expiry = _jwt_expiry(token) or now + 300.0
        return token

    @staticmethod
    def _unavailable(
        request: SandboxRequest,
        started: float,
        error: str,
    ) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.UNAVAILABLE,
            program_sha256=request.program.sha256,
            duration_seconds=time.monotonic() - started,
            error=error,
        )


def _jwt_expiry(token: str) -> float | None:
    try:
        encoded = token.split(".")[1]
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        expiry = payload.get("exp")
    except (IndexError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(expiry, bool) or not isinstance(expiry, int | float):
        return None
    return float(expiry)
