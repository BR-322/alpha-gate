"""Backend-neutral sandbox request and result models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha_gate.candidate import CandidateProgram
from alpha_gate.protocol import BarFrame, InitializeFrame


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutionLimits(StrictModel):
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    cpu_cores: float = Field(default=1.0, gt=0.0, le=4.0)
    memory_mb: int = Field(default=512, ge=64, le=4096)
    pids: int = Field(default=64, ge=8, le=512)
    tmpfs_mb: int = Field(default=64, ge=8, le=1024)
    max_output_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    max_frames: int = Field(default=4096, ge=1, le=65_536)


class SandboxRequest(StrictModel):
    program: CandidateProgram
    initialization: InitializeFrame
    bars: tuple[BarFrame, ...] = Field(min_length=1)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def consistent_stream(self) -> SandboxRequest:
        if len(self.bars) > self.limits.max_frames:
            raise ValueError("bar count exceeds limits.max_frames")
        symbol_count = len(self.initialization.symbols)
        for expected_sequence, bar in enumerate(self.bars):
            if bar.sequence != expected_sequence:
                raise ValueError("bar sequences must be contiguous and start at zero")
            if len(bar.close) != symbol_count:
                raise ValueError("every bar vector must match initialization.symbols")
        return self


class SandboxStatus(StrEnum):
    COMPLETED = "completed"
    INVALID = "invalid"
    PROTOCOL_ERROR = "protocol_error"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class FrameOutcome(StrictModel):
    sequence: int = Field(ge=0)
    weights: tuple[float, ...]
    duration_seconds: float = Field(ge=0.0)


class SandboxResult(StrictModel):
    status: SandboxStatus
    program_sha256: str
    frames: tuple[FrameOutcome, ...] = ()
    duration_seconds: float = Field(ge=0.0)
    stdout_bytes: int = Field(default=0, ge=0)
    stderr_bytes: int = Field(default=0, ge=0)
    stderr_tail: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status is SandboxStatus.COMPLETED


class SandboxExecutor(ABC):
    """Execute candidate frames without exposing future frames lockstep."""

    @abstractmethod
    async def execute(self, request: SandboxRequest) -> SandboxResult:
        """Return a typed result for every candidate failure mode."""
