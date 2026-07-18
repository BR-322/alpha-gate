"""Alpha-Gate's backend-neutral candidate and sandbox contracts."""

from alpha_gate.candidate import (
    CandidateMetadata,
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)

__all__ = [
    "CandidateMetadata",
    "CandidateProgram",
    "CandidateSourceError",
    "CandidateValidator",
    "ExecutionLimits",
    "SandboxExecutor",
    "SandboxRequest",
    "SandboxResult",
    "SandboxStatus",
]

__version__ = "0.1.0"
