from __future__ import annotations

import shutil
import subprocess

import pytest

from alpha_gate.executors.base import SandboxStatus
from alpha_gate.executors.container import ContainerExecutor, ContainerExecutorConfig


def _available_runtime() -> str | None:
    return shutil.which("docker") or shutil.which("podman")


@pytest.mark.container
@pytest.mark.asyncio
async def test_reference_container_runs_lockstep(sandbox_request) -> None:
    runtime_path = _available_runtime()
    if runtime_path is None:
        pytest.skip("Docker or Podman is not installed")
    runtime = "podman" if runtime_path.endswith("podman") else "docker"
    inspected = subprocess.run(
        [runtime_path, "image", "inspect", "alpha-gate-sandbox:dev"],
        capture_output=True,
        check=False,
    )
    if inspected.returncode != 0:
        pytest.skip("build containers/sandbox/Dockerfile as alpha-gate-sandbox:dev")

    executor = ContainerExecutor(
        ContainerExecutorConfig(runtime=runtime, runtime_path=runtime_path)
    )
    result = await executor.execute(sandbox_request)

    assert result.status is SandboxStatus.COMPLETED
    assert len(result.frames) == 1
    assert result.frames[0].weights == (0.0, 0.0)
