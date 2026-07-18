"""Alpha-Gate's backend-neutral candidate and sandbox contracts."""

from alpha_gate.backtest import ProgramBacktester, ProgramEvaluation
from alpha_gate.candidate import (
    CandidateMetadata,
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.evaluator import ProgramGroupEvaluator
from alpha_gate.evolution import Evolver, LocalEvolver
from alpha_gate.executors.base import (
    ExecutionLimits,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from alpha_gate.executors.cloud_run import CloudRunExecutor, CloudRunExecutorConfig
from alpha_gate.executors.cloud_sandbox import (
    CloudRunSandboxConfig,
    CloudRunSandboxExecutor,
)
from alpha_gate.experiment import ExperimentConfig, ExperimentResult, ExperimentRunner
from alpha_gate.ledger import JsonlLedger, MemoryLedger, TrialRecord
from alpha_gate.scoring import ProgramHonestScorer, ProgramScore
from alpha_gate.summary import (
    EvaluationProtocol,
    ExperimentSummary,
    FinalCandidateSummary,
)

__all__ = [
    "CandidateMetadata",
    "CandidateProgram",
    "CandidateSourceError",
    "CandidateValidator",
    "CloudRunExecutor",
    "CloudRunExecutorConfig",
    "CloudRunSandboxConfig",
    "CloudRunSandboxExecutor",
    "EvaluationProtocol",
    "Evolver",
    "ExecutionLimits",
    "ExperimentConfig",
    "ExperimentResult",
    "ExperimentRunner",
    "ExperimentSummary",
    "FinalCandidateSummary",
    "JsonlLedger",
    "LocalEvolver",
    "MemoryLedger",
    "ProgramBacktester",
    "ProgramEvaluation",
    "ProgramGroupEvaluator",
    "ProgramHonestScorer",
    "ProgramScore",
    "SandboxExecutor",
    "SandboxRequest",
    "SandboxResult",
    "SandboxStatus",
    "TrialRecord",
]

__version__ = "0.1.0"
