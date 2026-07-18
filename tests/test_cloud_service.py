from __future__ import annotations

from http import HTTPStatus

import pytest

from alpha_gate.cloud.service import execute_payload
from alpha_gate.executors.base import (
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)


class StubExecutor(SandboxExecutor):
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.requests: list[SandboxRequest] = []

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("private failure")
        return SandboxResult(
            status=SandboxStatus.COMPLETED,
            program_sha256=request.program.sha256,
            duration_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_broker_validates_and_executes_request(sandbox_request) -> None:
    executor = StubExecutor()

    status, body = await execute_payload(
        sandbox_request.model_dump_json().encode(),
        executor,
    )

    assert status is HTTPStatus.OK
    assert executor.requests == [sandbox_request]
    assert SandboxResult.model_validate_json(body).status is SandboxStatus.COMPLETED


@pytest.mark.asyncio
async def test_broker_rejects_invalid_payload_without_execution() -> None:
    executor = StubExecutor()

    status, body = await execute_payload(b'{"program":{}}', executor)

    assert status is HTTPStatus.BAD_REQUEST
    assert body == b'{"error":"invalid sandbox request"}'
    assert executor.requests == []


@pytest.mark.asyncio
async def test_broker_does_not_expose_internal_exception(sandbox_request) -> None:
    status, body = await execute_payload(
        sandbox_request.model_dump_json().encode(),
        StubExecutor(raises=True),
    )

    assert status is HTTPStatus.INTERNAL_SERVER_ERROR
    assert body == b'{"error":"sandbox broker failed"}'
    assert b"private failure" not in body
