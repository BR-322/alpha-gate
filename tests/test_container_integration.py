from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import SandboxStatus
from alpha_gate.executors.container import ContainerExecutor, ContainerExecutorConfig

from .executor_contract import assert_adversarial_executor_contract


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


@pytest.mark.container
@pytest.mark.asyncio
async def test_reference_container_passes_adversarial_contract(
    sandbox_request,
) -> None:
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

    await assert_adversarial_executor_contract(
        ContainerExecutor(
            ContainerExecutorConfig(runtime=runtime, runtime_path=runtime_path)
        ),
        sandbox_request,
    )


@pytest.mark.container
@pytest.mark.asyncio
async def test_timeout_does_not_leave_running_container(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_request,
) -> None:
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

    marker = uuid.uuid4().hex
    container_name = f"alpha-gate-{marker}"
    monkeypatch.setattr(
        "alpha_gate.executors.container.uuid.uuid4",
        lambda: type("UUID", (), {"hex": marker})(),
    )
    request = sandbox_request.model_copy(
        update={
            "program": CandidateProgram(
                source="""
class Strategy:
    def __init__(self, symbols, seed):
        pass

    def on_bar(self, bar):
        while True:
            pass
""".strip()
            ),
            "limits": sandbox_request.limits.model_copy(
                update={"timeout_seconds": 1.0}
            ),
        }
    )
    executor = ContainerExecutor(
        ContainerExecutorConfig(runtime=runtime, runtime_path=runtime_path)
    )

    try:
        result = await executor.execute(request)
        remaining = subprocess.run(
            [runtime_path, "container", "inspect", container_name],
            capture_output=True,
            check=False,
        )
    finally:
        subprocess.run(
            [runtime_path, "rm", "--force", container_name],
            capture_output=True,
            check=False,
        )

    assert result.status is SandboxStatus.TIMEOUT
    assert remaining.returncode != 0
