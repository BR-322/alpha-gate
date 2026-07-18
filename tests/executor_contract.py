from __future__ import annotations

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxExecutor,
    SandboxRequest,
    SandboxStatus,
)


async def assert_adversarial_executor_contract(
    executor: SandboxExecutor,
    reference_request: SandboxRequest,
) -> None:
    safe = await executor.execute(reference_request)
    assert safe.status is SandboxStatus.COMPLETED, (safe.status, safe.error)
    assert len(safe.frames) == 1
    assert safe.frames[0].weights == (0.0, 0.0)

    runtime_failure = await executor.execute(
        _with_source(
            reference_request,
            """
class Strategy:
    def __init__(self, symbols, seed):
        self.count = len(symbols)

    def on_bar(self, bar):
        return [1 / 0] * self.count
""".strip(),
        )
    )
    assert runtime_failure.status is SandboxStatus.RUNTIME_ERROR, (
        runtime_failure.status,
        runtime_failure.error,
    )

    invalid_weights = await executor.execute(
        _with_source(
            reference_request,
            """
class Strategy:
    def __init__(self, symbols, seed):
        self.count = len(symbols)

    def on_bar(self, bar):
        return [1.0] * self.count
""".strip(),
        )
    )
    assert invalid_weights.status is SandboxStatus.PROTOCOL_ERROR, (
        invalid_weights.status,
        invalid_weights.error,
    )

    timeout_request = _with_source(
        reference_request,
        """
class Strategy:
    def __init__(self, symbols, seed):
        pass

    def on_bar(self, bar):
        while True:
            pass
""".strip(),
    ).model_copy(
        update={
            "limits": reference_request.limits.model_copy(
                update={"timeout_seconds": 1.0}
            )
        }
    )
    timed_out = await executor.execute(timeout_request)
    assert timed_out.status is SandboxStatus.TIMEOUT, (
        timed_out.status,
        timed_out.error,
    )

    output_request = _with_source(
        reference_request,
        """
class Strategy:
    def __init__(self, symbols, seed):
        self.count = len(symbols)

    def on_bar(self, bar):
        print("x" * 2048)
        return [0.0] * self.count
""".strip(),
    ).model_copy(
        update={
            "limits": ExecutionLimits(
                timeout_seconds=reference_request.limits.timeout_seconds,
                cpu_cores=reference_request.limits.cpu_cores,
                memory_mb=reference_request.limits.memory_mb,
                pids=reference_request.limits.pids,
                tmpfs_mb=reference_request.limits.tmpfs_mb,
                max_output_bytes=1024,
                max_frames=reference_request.limits.max_frames,
            )
        }
    )
    flooded = await executor.execute(output_request)
    assert flooded.status is SandboxStatus.PROTOCOL_ERROR, (
        flooded.status,
        flooded.error,
    )
    assert flooded.stderr_bytes >= 2048


def _with_source(request: SandboxRequest, source: str) -> SandboxRequest:
    return request.model_copy(update={"program": CandidateProgram(source=source)})
