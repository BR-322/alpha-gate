from __future__ import annotations

import os

import pytest

from alpha_gate.executors.base import SandboxStatus
from alpha_gate.executors.cloud_run import CloudRunExecutor, CloudRunExecutorConfig

from .executor_contract import assert_adversarial_executor_contract


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_deployed_broker_runs_reference_candidate_lockstep(
    sandbox_request,
) -> None:
    service_url = os.environ.get("ALPHA_GATE_CLOUD_RUN_URL")
    if not service_url:
        pytest.skip("set ALPHA_GATE_CLOUD_RUN_URL to test a deployed broker")

    result = await CloudRunExecutor(
        CloudRunExecutorConfig(service_url=service_url)
    ).execute(sandbox_request)

    assert result.status is SandboxStatus.COMPLETED
    assert len(result.frames) == 1
    assert result.frames[0].weights == (0.0, 0.0)


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_deployed_broker_passes_adversarial_contract(
    sandbox_request,
) -> None:
    service_url = os.environ.get("ALPHA_GATE_CLOUD_RUN_URL")
    if not service_url:
        pytest.skip("set ALPHA_GATE_CLOUD_RUN_URL to test a deployed broker")

    await assert_adversarial_executor_contract(
        CloudRunExecutor(CloudRunExecutorConfig(service_url=service_url)),
        sandbox_request,
    )
