"""Alpha-Gate's backend-neutral candidate and sandbox contracts."""

from alpha_gate.backtest import ProgramBacktester, ProgramEvaluation
from alpha_gate.candidate import (
    CandidateMetadata,
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.evaluator import ProgramGroupEvaluator
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from alpha_gate.scoring import ProgramHonestScorer, ProgramScore

__all__ = [
    "CandidateMetadata",
    "CandidateProgram",
    "CandidateSourceError",
    "CandidateValidator",
    "ExecutionLimits",
    "ProgramBacktester",
    "ProgramEvaluation",
    "ProgramGroupEvaluator",
    "ProgramHonestScorer",
    "ProgramScore",
    "SandboxExecutor",
    "SandboxRequest",
    "SandboxResult",
    "SandboxStatus",
]

__version__ = "0.1.0"
