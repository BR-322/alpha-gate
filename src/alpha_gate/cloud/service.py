"""Minimal HTTP broker that keeps future observations outside candidate sandboxes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

from pydantic import ValidationError

from alpha_gate.executors.base import SandboxExecutor, SandboxRequest
from alpha_gate.executors.cloud_sandbox import (
    CloudRunSandboxConfig,
    CloudRunSandboxExecutor,
)

MAX_REQUEST_BYTES = 16_777_216


async def execute_payload(
    payload: bytes,
    executor: SandboxExecutor,
) -> tuple[HTTPStatus, bytes]:
    """Validate an HTTP payload and return a stable JSON response."""

    try:
        request = SandboxRequest.model_validate_json(payload)
    except ValidationError:
        return _error_response(HTTPStatus.BAD_REQUEST, "invalid sandbox request")
    try:
        result = await executor.execute(request)
    except Exception:
        logging.exception("sandbox executor raised unexpectedly")
        return _error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "sandbox broker failed",
        )
    return HTTPStatus.OK, result.model_dump_json().encode("utf-8")


def _error_response(status: HTTPStatus, message: str) -> tuple[HTTPStatus, bytes]:
    return status, json.dumps(
        {"error": message},
        separators=(",", ":"),
    ).encode("utf-8")


class BrokerRequestHandler(BaseHTTPRequestHandler):
    """One-request-at-a-time HTTP adapter for the trusted broker."""

    executor: ClassVar[SandboxExecutor]
    server_version = "AlphaGateBroker/1"
    sys_version = ""

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send(*_error_response(HTTPStatus.NOT_FOUND, "not found"))
            return
        self._send(
            HTTPStatus.OK,
            b'{"status":"ok","protocol":"alpha-gate.strategy.v1"}',
        )

    def do_POST(self) -> None:
        if self.path != "/v1/execute":
            self._send(*_error_response(HTTPStatus.NOT_FOUND, "not found"))
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "")
        except ValueError:
            self._send(
                *_error_response(HTTPStatus.LENGTH_REQUIRED, "invalid content length")
            )
            return
        if length < 1 or length > MAX_REQUEST_BYTES:
            self._send(
                *_error_response(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "request body exceeds broker limit",
                )
            )
            return
        payload = self.rfile.read(length)
        status, body = asyncio.run(execute_payload(payload, self.executor))
        self._send(status, body)

    def _send(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status.value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logging.info("broker request: " + format, *args)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8080"))
    BrokerRequestHandler.executor = CloudRunSandboxExecutor(
        CloudRunSandboxConfig(
            cpu_ceiling=float(os.environ.get("ALPHA_GATE_CPU_CEILING", "1")),
            memory_ceiling_mb=int(
                os.environ.get("ALPHA_GATE_MEMORY_CEILING_MB", "512")
            ),
        )
    )
    server = HTTPServer(("0.0.0.0", port), BrokerRequestHandler)
    logging.info("Alpha-Gate broker listening on port %d", port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
