"""Docker/Podman implementation of the lockstep sandbox protocol."""

from __future__ import annotations

import asyncio
import math
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alpha_gate.candidate import CandidateSourceError, CandidateValidator
from alpha_gate.executors.base import (
    ExecutionLimits,
    FrameOutcome,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from alpha_gate.protocol import (
    RESPONSE_FRAME_ADAPTER,
    CandidateErrorResponse,
    InitializeFrame,
    ReadyResponse,
    RequestFrame,
    ResponseFrame,
    StopFrame,
    StoppedResponse,
    WeightsResponse,
)


class ContainerExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    runtime: Literal["docker", "podman"] = "docker"
    runtime_path: str | None = None
    image: str = Field(default="alpha-gate-sandbox:dev", min_length=1)
    worker_path: str = "/opt/alpha-gate/worker.py"


class ProtocolViolation(ValueError):
    """The candidate process failed the JSONL request/response contract."""


class CandidateFailure(RuntimeError):
    """The candidate explicitly reported an execution failure."""


@dataclass
class _StreamCounters:
    stdout_bytes: int = 0


def validate_weights(
    response: WeightsResponse,
    initialization: InitializeFrame,
) -> tuple[float, ...]:
    """Validate a candidate portfolio before trusted code can consume it."""

    weights = response.weights
    if len(weights) != len(initialization.symbols):
        raise ProtocolViolation("weight count does not match symbol count")
    tolerance = 1e-12
    if not initialization.allow_short and any(
        weight < -tolerance for weight in weights
    ):
        raise ProtocolViolation("negative weights are disabled")
    if any(
        abs(weight) > initialization.max_abs_weight + tolerance for weight in weights
    ):
        raise ProtocolViolation("a position exceeds max_abs_weight")
    gross = math.fsum(abs(weight) for weight in weights)
    if gross > initialization.max_gross + tolerance:
        raise ProtocolViolation("portfolio gross exposure exceeds max_gross")
    return weights


class ContainerExecutor(SandboxExecutor):
    """Run one candidate in a networkless, read-only Linux container."""

    def __init__(self, config: ContainerExecutorConfig | None = None) -> None:
        self.config = config or ContainerExecutorConfig()

    def runtime_path(self) -> str | None:
        if self.config.runtime_path is not None:
            return self.config.runtime_path
        return shutil.which(self.config.runtime)

    def build_command(
        self,
        runtime_path: str,
        candidate_path: Path,
        limits: ExecutionLimits,
    ) -> tuple[str, ...]:
        mount = (
            f"type=bind,source={candidate_path.resolve()},"
            "target=/candidate/strategy.py,readonly"
        )
        return (
            runtime_path,
            "run",
            "--rm",
            "--interactive",
            "--pull=never",
            "--network=none",
            "--read-only",
            f"--pids-limit={limits.pids}",
            f"--memory={limits.memory_mb}m",
            f"--cpus={limits.cpu_cores}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65532:65532",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={limits.tmpfs_mb}m",
            "--log-driver=none",
            "--env=PYTHONHASHSEED=0",
            "--mount",
            mount,
            self.config.image,
            "python",
            "-I",
            self.config.worker_path,
            "/candidate/strategy.py",
        )

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        try:
            CandidateValidator.validate(request.program)
        except CandidateSourceError as exc:
            return SandboxResult(
                status=SandboxStatus.INVALID,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=str(exc),
            )

        runtime_path = self.runtime_path()
        if runtime_path is None:
            return SandboxResult(
                status=SandboxStatus.UNAVAILABLE,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=f"{self.config.runtime} executable was not found",
            )

        with tempfile.TemporaryDirectory(prefix="alpha-gate-") as temporary:
            candidate_path = Path(temporary) / request.program.filename
            await asyncio.to_thread(
                candidate_path.write_text,
                request.program.source,
                encoding="utf-8",
            )
            command = self.build_command(runtime_path, candidate_path, request.limits)
            return await self._execute_process(command, request, started)

    async def _execute_process(
        self,
        command: tuple[str, ...],
        request: SandboxRequest,
        started: float,
    ) -> SandboxResult:
        limits = request.limits
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=limits.max_output_bytes + 1,
            )
        except (FileNotFoundError, OSError) as exc:
            return SandboxResult(
                status=SandboxStatus.UNAVAILABLE,
                program_sha256=request.program.sha256,
                duration_seconds=time.monotonic() - started,
                error=f"container runtime failed to start: {type(exc).__name__}",
            )

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(
            self._drain_stderr(process.stderr, limits.max_output_bytes)
        )
        counters = _StreamCounters()
        frames: list[FrameOutcome] = []
        status = SandboxStatus.COMPLETED
        error = ""
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                response = await self._exchange(
                    process,
                    request.initialization,
                    counters,
                    limits.max_output_bytes,
                )
                if isinstance(response, CandidateErrorResponse):
                    raise CandidateFailure(response.message)
                if not isinstance(response, ReadyResponse):
                    raise ProtocolViolation("initialization did not return ready")

                for bar in request.bars:
                    frame_started = time.monotonic()
                    response = await self._exchange(
                        process,
                        bar,
                        counters,
                        limits.max_output_bytes,
                    )
                    if isinstance(response, CandidateErrorResponse):
                        raise CandidateFailure(response.message)
                    if not isinstance(response, WeightsResponse):
                        raise ProtocolViolation("bar did not return weights")
                    if response.sequence != bar.sequence:
                        raise ProtocolViolation(
                            "response sequence does not match request"
                        )
                    frames.append(
                        FrameOutcome(
                            sequence=bar.sequence,
                            weights=validate_weights(response, request.initialization),
                            duration_seconds=time.monotonic() - frame_started,
                        )
                    )

                response = await self._exchange(
                    process,
                    StopFrame(),
                    counters,
                    limits.max_output_bytes,
                )
                if not isinstance(response, StoppedResponse):
                    raise ProtocolViolation("stop did not return stopped")
                process.stdin.close()
                await process.wait()
                if process.returncode != 0:
                    raise CandidateFailure(
                        f"sandbox worker exited with status {process.returncode}"
                    )
        except TimeoutError:
            status = SandboxStatus.TIMEOUT
            error = f"candidate exceeded {limits.timeout_seconds:g}s wall-clock limit"
        except CandidateFailure as exc:
            status = SandboxStatus.RUNTIME_ERROR
            error = str(exc)
        except (ProtocolViolation, ValidationError, ValueError) as exc:
            status = SandboxStatus.PROTOCOL_ERROR
            error = str(exc)
        finally:
            await self._stop_process(process)

        stderr_bytes, stderr_tail = await stderr_task
        if (
            status is SandboxStatus.COMPLETED
            and counters.stdout_bytes + stderr_bytes > limits.max_output_bytes
        ):
            status = SandboxStatus.PROTOCOL_ERROR
            error = "candidate output exceeded max_output_bytes"
        return SandboxResult(
            status=status,
            program_sha256=request.program.sha256,
            frames=tuple(frames),
            duration_seconds=time.monotonic() - started,
            stdout_bytes=counters.stdout_bytes,
            stderr_bytes=stderr_bytes,
            stderr_tail=stderr_tail,
            error=error,
        )

    async def _exchange(
        self,
        process: asyncio.subprocess.Process,
        frame: RequestFrame,
        counters: _StreamCounters,
        max_output_bytes: int,
    ) -> ResponseFrame:
        assert process.stdin is not None
        assert process.stdout is not None
        payload = frame.model_dump_json().encode("utf-8") + b"\n"
        process.stdin.write(payload)
        await process.stdin.drain()
        try:
            line = await process.stdout.readline()
        except ValueError as exc:
            raise ProtocolViolation("candidate response line is too large") from exc
        if not line:
            raise ProtocolViolation("candidate closed the protocol stream")
        counters.stdout_bytes += len(line)
        if counters.stdout_bytes > max_output_bytes:
            raise ProtocolViolation("candidate output exceeded max_output_bytes")
        try:
            return RESPONSE_FRAME_ADAPTER.validate_json(line)
        except ValidationError as exc:
            raise ProtocolViolation(
                "candidate returned an invalid response frame"
            ) from exc

    @staticmethod
    async def _drain_stderr(
        stream: asyncio.StreamReader,
        tail_limit: int,
    ) -> tuple[int, str]:
        total = 0
        tail = bytearray()
        retained = min(tail_limit, 8192)
        while chunk := await stream.read(8192):
            total += len(chunk)
            tail.extend(chunk)
            if len(tail) > retained:
                del tail[:-retained]
        return total, tail.decode("utf-8", errors="replace")

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            process.kill()
            await process.wait()
