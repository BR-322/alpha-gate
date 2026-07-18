from __future__ import annotations

import base64
import json
import time
import urllib.error

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import SandboxResult, SandboxStatus
from alpha_gate.executors.cloud_run import CloudRunExecutor, CloudRunExecutorConfig


def _token(expiry: float) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expiry}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


@pytest.mark.asyncio
async def test_authenticated_remote_result_round_trips_and_reuses_token(
    sandbox_request,
) -> None:
    calls: list[tuple[str, dict[str, str], float, bytes]] = []
    token_calls: list[str] = []

    def provide_token(audience: str) -> str:
        token_calls.append(audience)
        return _token(time.time() + 3600)

    def transport(
        url: str,
        payload: bytes,
        headers: dict[str, str],
        timeout: float,
        _max_response_bytes: int,
    ) -> bytes:
        calls.append((url, headers, timeout, payload))
        return SandboxResult(
            status=SandboxStatus.COMPLETED,
            program_sha256=sandbox_request.program.sha256,
            duration_seconds=0.01,
        ).model_dump_json().encode("utf-8")

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(service_url="https://broker.example.run.app"),
        token_provider=provide_token,
        transport=transport,
    )

    first = await executor.execute(sandbox_request)
    second = await executor.execute(sandbox_request)

    assert first.status is SandboxStatus.COMPLETED
    assert second == first
    assert token_calls == ["https://broker.example.run.app"]
    assert len(calls) == 2
    assert calls[0][0] == "https://broker.example.run.app/v1/execute"
    assert calls[0][1]["Authorization"].startswith("Bearer ")
    assert calls[0][2] == pytest.approx(60.0, abs=0.01)
    assert b'"type":"initialize"' in calls[0][3]


@pytest.mark.asyncio
async def test_no_auth_mode_never_requests_a_token(sandbox_request) -> None:
    def reject_token(_audience: str) -> str:
        raise AssertionError("token provider must not be called")

    def transport(
        _url: str,
        _payload: bytes,
        headers: dict[str, str],
        _timeout: float,
        _max_response_bytes: int,
    ) -> bytes:
        assert "Authorization" not in headers
        return SandboxResult(
            status=SandboxStatus.COMPLETED,
            program_sha256=sandbox_request.program.sha256,
            duration_seconds=0.01,
        ).model_dump_json().encode()

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(
            service_url="http://127.0.0.1:8080",
            auth="none",
        ),
        token_provider=reject_token,
        transport=transport,
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.COMPLETED


@pytest.mark.asyncio
async def test_mismatched_remote_hash_is_protocol_error(sandbox_request) -> None:
    def transport(
        _url: str,
        _payload: bytes,
        _headers: dict[str, str],
        _timeout: float,
        _max_response_bytes: int,
    ) -> bytes:
        return SandboxResult(
            status=SandboxStatus.COMPLETED,
            program_sha256="wrong",
            duration_seconds=0.01,
        ).model_dump_json().encode()

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(
            service_url="http://127.0.0.1:8080",
            auth="none",
        ),
        transport=transport,
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.PROTOCOL_ERROR
    assert "mismatched program hash" in result.error


@pytest.mark.asyncio
async def test_transport_failure_is_typed_and_sanitized(sandbox_request) -> None:
    def transport(
        _url: str,
        _payload: bytes,
        _headers: dict[str, str],
        _timeout: float,
        _max_response_bytes: int,
    ) -> bytes:
        raise OSError("sensitive remote detail")

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(
            service_url="http://127.0.0.1:8080",
            auth="none",
        ),
        transport=transport,
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error == "Cloud Run broker failed: OSError"
    assert "sensitive" not in result.error


@pytest.mark.asyncio
async def test_pre_dispatch_throttling_retries_within_one_deadline(
    sandbox_request,
) -> None:
    attempts = 0

    def transport(
        url: str,
        _payload: bytes,
        _headers: dict[str, str],
        _timeout: float,
        _max_response_bytes: int,
    ) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.HTTPError(url, 429, "throttled", {}, None)
        return SandboxResult(
            status=SandboxStatus.COMPLETED,
            program_sha256=sandbox_request.program.sha256,
            duration_seconds=0.01,
        ).model_dump_json().encode()

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(
            service_url="http://127.0.0.1:8080",
            auth="none",
            throttle_retry_seconds=0.0,
        ),
        transport=transport,
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.COMPLETED
    assert attempts == 3


@pytest.mark.asyncio
async def test_invalid_source_is_rejected_before_transport(
    sandbox_request,
) -> None:
    request = sandbox_request.model_copy(
        update={"program": CandidateProgram(source="import os")}
    )

    def transport(*_args: object) -> bytes:
        raise AssertionError("transport must not be called")

    executor = CloudRunExecutor(
        CloudRunExecutorConfig(
            service_url="http://127.0.0.1:8080",
            auth="none",
        ),
        transport=transport,
    )

    result = await executor.execute(request)

    assert result.status is SandboxStatus.INVALID
