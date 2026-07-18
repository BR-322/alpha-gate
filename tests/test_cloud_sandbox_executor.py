from __future__ import annotations

from pathlib import Path

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import ExecutionLimits, SandboxStatus
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
