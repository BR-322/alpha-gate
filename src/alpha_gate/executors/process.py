"""Shared lockstep JSONL driver for isolated candidate processes."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from alpha_gate.executors.base import (
    FrameOutcome,
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
    validate_target_weights,
)


class ProtocolViolation(ValueError):
    """The candidate process failed the JSONL request/response contract."""


class CandidateFailure(RuntimeError):
    """The candidate explicitly reported an execution failure."""


@dataclass
class _StreamCounters:
    stdout_bytes: int = 0


@dataclass
class _StderrCapture:
    total: int = 0
    tail: bytearray = field(default_factory=bytearray)


def validate_weights(
    response: WeightsResponse,
    initialization: InitializeFrame,
) -> tuple[float, ...]:
    """Validate a candidate portfolio before trusted code can consume it."""

    try:
        return validate_target_weights(response.weights, initialization)
    except ValueError as exc:
        raise ProtocolViolation(str(exc)) from exc


class JsonlProcessDriver:
    """Drive one isolated worker without revealing the next observation early."""

    async def execute(
        self,
        command: tuple[str, ...],
        request: SandboxRequest,
        *,
        started: float,
        unavailable_label: str,
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
                error=f"{unavailable_label} failed to start: {type(exc).__name__}",
            )

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_capture = _StderrCapture()
        stderr_task = asyncio.create_task(
            self._drain_stderr(
                process.stderr,
                limits.max_output_bytes,
                stderr_capture,
            )
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
                            weights=validate_weights(
                                response,
                                request.initialization,
                            ),
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

        try:
            await asyncio.wait_for(stderr_task, timeout=0.5)
        except TimeoutError:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
        stderr_bytes = stderr_capture.total
        stderr_tail = stderr_capture.tail.decode("utf-8", errors="replace")
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
        capture: _StderrCapture,
    ) -> None:
        retained = min(tail_limit, 8192)
        while chunk := await stream.read(8192):
            capture.total += len(chunk)
            capture.tail.extend(chunk)
            if len(capture.tail) > retained:
                del capture.tail[:-retained]

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
            return
        except TimeoutError:
            pass
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            process.kill()
            await process.wait()
