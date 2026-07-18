from __future__ import annotations

from pathlib import Path

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import ExecutionLimits, SandboxStatus
from alpha_gate.executors.container import ContainerExecutor, ContainerExecutorConfig


def test_command_hardens_and_minimizes_container_mounts(tmp_path: Path) -> None:
    candidate = tmp_path / "strategy.py"
    executor = ContainerExecutor(
        ContainerExecutorConfig(runtime="podman", image="sandbox@sha256:digest")
    )

    command = executor.build_command(
        "/usr/bin/podman",
        candidate,
        ExecutionLimits(
            cpu_cores=0.5,
            memory_mb=256,
            pids=32,
            tmpfs_mb=16,
        ),
    )

    assert command[:3] == ("/usr/bin/podman", "run", "--rm")
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--pids-limit=32" in command
    assert "--memory=256m" in command
    assert "--cpus=0.5" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--user=65532:65532" in command
    assert "--tmpfs=/tmp:rw,noexec,nosuid,size=16m" in command
    assert "--log-driver=none" in command
    assert command.count("--mount") == 1
    mount = command[command.index("--mount") + 1]
    assert f"source={candidate.resolve()}" in mount
    assert "target=/candidate/strategy.py,readonly" in mount


@pytest.mark.asyncio
async def test_invalid_source_is_rejected_before_runtime_lookup(
    sandbox_request,
) -> None:
    request = sandbox_request.model_copy(
        update={"program": CandidateProgram(source="import os")}
    )

    result = await ContainerExecutor().execute(request)

    assert result.status is SandboxStatus.INVALID
    assert "import of os is not allowed" in result.error


@pytest.mark.asyncio
async def test_missing_runtime_is_typed_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_request,
) -> None:
    monkeypatch.setattr(
        "alpha_gate.executors.container.shutil.which", lambda _runtime: None
    )

    result = await ContainerExecutor().execute(sandbox_request)

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error == "docker executable was not found"
