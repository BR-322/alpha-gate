from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxResult,
    SandboxStatus,
)
from alpha_gate.executors.cloud_sandbox import (
    CloudRunSandboxConfig,
    CloudRunSandboxExecutor,
)


def test_command_keeps_nested_sandbox_networkless_and_read_only(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "strategy.py"
    executor = CloudRunSandboxExecutor(
        CloudRunSandboxConfig(
            launcher_path="/launcher/sandbox",
            python_path="/usr/bin/python3",
        )
    )

    command = executor.build_command(
        "/launcher/sandbox",
        candidate,
        ExecutionLimits(),
        "alpha-gate-test123",
    )

    assert command[:2] == ("/launcher/sandbox", "do")
    assert command[2:4] == ("--sandbox-name", "alpha-gate-test123")
    assert "--allow-egress" not in command
    assert "--write" not in command
    assert "--mount" not in command
    assert "PYTHONHASHSEED=0" in command
    assert command[-1] == str(candidate)


@pytest.mark.asyncio
async def test_invalid_source_is_rejected_before_launcher_lookup(
    sandbox_request,
) -> None:
    request = sandbox_request.model_copy(
        update={"program": CandidateProgram(source="import os")}
    )

    result = await CloudRunSandboxExecutor().execute(request)

    assert result.status is SandboxStatus.INVALID
    assert "import of os is not allowed" in result.error


@pytest.mark.asyncio
async def test_missing_launcher_is_typed_unavailable(sandbox_request) -> None:
    executor = CloudRunSandboxExecutor(
        CloudRunSandboxConfig(launcher_path="/definitely/missing/sandbox")
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error == "Cloud Run sandbox launcher was not found"


@pytest.mark.asyncio
async def test_request_cannot_exceed_fixed_broker_envelope(sandbox_request) -> None:
    executor = CloudRunSandboxExecutor(
        CloudRunSandboxConfig(cpu_ceiling=0.5, memory_ceiling_mb=256)
    )

    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error == "requested CPU exceeds the Cloud Run broker ceiling"


@pytest.mark.asyncio
async def test_timeout_always_deletes_named_cloud_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_request,
) -> None:
    executor = CloudRunSandboxExecutor(CloudRunSandboxConfig(launcher_path="/bin/sh"))
    timeout = SandboxResult(
        status=SandboxStatus.TIMEOUT,
        program_sha256=sandbox_request.program.sha256,
        duration_seconds=1.0,
        error="deadline",
    )
    executor._process_driver.execute = AsyncMock(return_value=timeout)
    delete = AsyncMock()
    monkeypatch.setattr(executor, "_delete_sandbox", delete)
    monkeypatch.setattr(
        "alpha_gate.executors.cloud_sandbox.uuid.uuid4",
        lambda: type("UUID", (), {"hex": "test123"})(),
    )

    result = await executor.execute(sandbox_request)

    assert result is timeout
    delete.assert_awaited_once_with("/bin/sh", "alpha-gate-test123")


@pytest.mark.asyncio
async def test_cancellation_still_deletes_named_cloud_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_request,
) -> None:
    executor = CloudRunSandboxExecutor(CloudRunSandboxConfig(launcher_path="/bin/sh"))
    executor._process_driver.execute = AsyncMock(side_effect=asyncio.CancelledError)
    delete = AsyncMock()
    monkeypatch.setattr(executor, "_delete_sandbox", delete)

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(sandbox_request)

    delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_sandbox_delete_retries_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode: int | None = None
            self._final_returncode = returncode

        async def wait(self) -> int:
            self.returncode = self._final_returncode
            return self._final_returncode

        def kill(self) -> None:
            self.returncode = -9

    create_process = AsyncMock(side_effect=[CleanupProcess(1), CleanupProcess(0)])
    monkeypatch.setattr(
        "alpha_gate.executors.cloud_sandbox.asyncio.create_subprocess_exec",
        create_process,
    )

    await CloudRunSandboxExecutor._delete_sandbox(
        "/launcher/sandbox",
        "alpha-gate-test123",
    )

    assert create_process.await_count == 2
