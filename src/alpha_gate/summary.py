"""Durable final-score summaries kept separate from the event ledger."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha_gate.constants import ALPHA_EVOLVE_COMMIT, GATE_RUNNER_COMMIT
from alpha_gate.experiment import ExperimentConfig, ExperimentResult

SummarySchemaVersion = Literal["alpha-gate.summary.v1"]


class EvaluationProtocol(BaseModel):
    """Evaluator and runtime inputs needed to reproduce one experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dataset: str = Field(min_length=1, max_length=128)
    source_label: str = Field(min_length=1, max_length=1_024)
    rate_source_label: str | None = Field(default=None, max_length=1_024)
    symbols: tuple[str, ...] = Field(min_length=2)
    first_scored_date: str = Field(min_length=1, max_length=32)
    last_scored_date: str = Field(min_length=1, max_length=32)
    windows: int = Field(ge=4)
    window_days: int = Field(ge=20)
    warmup_days: int = Field(ge=1)
    cost_bps_per_side: float = Field(ge=0.0)
    runtime: Literal["docker", "podman"]
    runtime_path: str = Field(min_length=1, max_length=4_096)
    image: str = Field(min_length=1, max_length=1_024)
    image_id: str = Field(min_length=1, max_length=1_024)

    @model_validator(mode="after")
    def finite_cost_and_unique_symbols(self) -> EvaluationProtocol:
        if not math.isfinite(self.cost_bps_per_side):
            raise ValueError("cost_bps_per_side must be finite")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("evaluation symbols must be unique")
        return self


class FinalCandidateSummary(BaseModel):
    """One candidate paired with its final cumulative score snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rank: int = Field(ge=1)
    candidate_id: str = Field(min_length=1, max_length=256)
    evaluation_index: int = Field(ge=0)
    generation: int = Field(ge=0)
    program_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    program_source: str = Field(min_length=1, max_length=131_072)
    parent_sha256: tuple[str, ...]
    origin: str = Field(min_length=1, max_length=128)
    mutation: str = Field(min_length=1, max_length=512)
    reward: float
    passed: bool
    validity: float = Field(ge=0.0, le=1.0)
    final_trial_count: int = Field(ge=1)
    source_bytes: int = Field(ge=0)
    ast_nodes: int = Field(ge=0)
    metrics: dict[str, float]
    error: str = ""
    executor_window_statuses: tuple[str, ...]
    executor_window_durations_seconds: tuple[float, ...]
    executor_total_duration_seconds: float = Field(ge=0.0)
    executor_stdout_bytes: int = Field(ge=0)
    executor_stderr_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def finite_and_aligned(self) -> FinalCandidateSummary:
        numeric = (
            self.reward,
            self.validity,
            self.executor_total_duration_seconds,
            *self.executor_window_durations_seconds,
            *self.metrics.values(),
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("final candidate metrics must be finite")
        if len(self.executor_window_statuses) != len(
            self.executor_window_durations_seconds
        ):
            raise ValueError("executor window statuses and durations must align")
        if not math.isclose(
            self.executor_total_duration_seconds,
            math.fsum(self.executor_window_durations_seconds),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("executor total duration does not match window durations")
        if self.metrics.get("reward", self.reward) != self.reward:
            raise ValueError("candidate reward does not match metrics")
        if self.metrics.get("trial_count", float(self.final_trial_count)) != float(
            self.final_trial_count
        ):
            raise ValueError("candidate final trial count does not match metrics")
        return self


class ExperimentSummary(BaseModel):
    """Complete final ranking for one finished experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: SummarySchemaVersion = "alpha-gate.summary.v1"
    gate_runner_commit: str = GATE_RUNNER_COMMIT
    alpha_evolve_commit: str = ALPHA_EVOLVE_COMMIT
    experiment: ExperimentConfig
    evaluation: EvaluationProtocol
    generations_completed: int = Field(ge=0)
    stop_reason: Literal["evaluation_budget", "generation_limit"]
    evaluations_used: int = Field(ge=1)
    valid_candidates: int = Field(ge=0)
    passed_candidates: int = Field(ge=0)
    sandbox_window_launches: int = Field(ge=0)
    sandbox_total_duration_seconds: float = Field(ge=0.0)
    candidates: tuple[FinalCandidateSummary, ...] = Field(min_length=1)

    @classmethod
    def from_result(
        cls,
        result: ExperimentResult,
        evaluation: EvaluationProtocol,
    ) -> ExperimentSummary:
        candidates: list[FinalCandidateSummary] = []
        for rank, outcome in enumerate(result.ranked_outcomes, start=1):
            metadata = outcome.evaluation.metadata
            sandbox_results = outcome.evaluation.sandbox_results
            durations = tuple(sandbox.duration_seconds for sandbox in sandbox_results)
            candidates.append(
                FinalCandidateSummary(
                    rank=rank,
                    candidate_id=outcome.trial.candidate_id,
                    evaluation_index=outcome.trial.evaluation_index,
                    generation=outcome.trial.generation,
                    program_sha256=outcome.trial.proposal.program.sha256,
                    program_source=outcome.trial.proposal.program.source,
                    parent_sha256=outcome.trial.proposal.parent_sha256,
                    origin=outcome.trial.proposal.origin,
                    mutation=outcome.trial.proposal.mutation,
                    reward=outcome.score.reward,
                    passed=bool(outcome.score.score.passed),
                    validity=outcome.score.score.validity,
                    final_trial_count=int(outcome.score.score.trial_count),
                    source_bytes=metadata.source_bytes if metadata is not None else 0,
                    ast_nodes=metadata.ast_nodes if metadata is not None else 0,
                    metrics=outcome.score.score.metrics(),
                    error=outcome.evaluation.error,
                    executor_window_statuses=tuple(
                        sandbox.status.value for sandbox in sandbox_results
                    ),
                    executor_window_durations_seconds=durations,
                    executor_total_duration_seconds=math.fsum(durations),
                    executor_stdout_bytes=sum(
                        sandbox.stdout_bytes for sandbox in sandbox_results
                    ),
                    executor_stderr_bytes=sum(
                        sandbox.stderr_bytes for sandbox in sandbox_results
                    ),
                )
            )
        return cls(
            experiment=result.config,
            evaluation=evaluation,
            generations_completed=result.generations_completed,
            stop_reason=result.stop_reason,
            evaluations_used=result.evaluations_used,
            valid_candidates=sum(candidate.validity > 0.0 for candidate in candidates),
            passed_candidates=sum(candidate.passed for candidate in candidates),
            sandbox_window_launches=sum(
                len(candidate.executor_window_statuses) for candidate in candidates
            ),
            sandbox_total_duration_seconds=math.fsum(
                candidate.executor_total_duration_seconds for candidate in candidates
            ),
            candidates=tuple(candidates),
        )

    @model_validator(mode="after")
    def complete_and_ranked(self) -> ExperimentSummary:
        if len(self.candidates) != self.evaluations_used:
            raise ValueError("final candidate count must equal evaluations_used")
        if [candidate.rank for candidate in self.candidates] != list(
            range(1, self.evaluations_used + 1)
        ):
            raise ValueError("final candidate ranks must be contiguous")
        if len({candidate.candidate_id for candidate in self.candidates}) != len(
            self.candidates
        ):
            raise ValueError("final candidate IDs must be unique")
        if any(
            candidate.final_trial_count != self.evaluations_used
            for candidate in self.candidates
        ):
            raise ValueError("every final candidate must use the complete trial count")
        if self.valid_candidates != sum(
            candidate.validity > 0.0 for candidate in self.candidates
        ):
            raise ValueError("valid candidate aggregate does not match candidates")
        if self.passed_candidates != sum(
            candidate.passed for candidate in self.candidates
        ):
            raise ValueError("passed candidate aggregate does not match candidates")
        if self.sandbox_window_launches != sum(
            len(candidate.executor_window_statuses) for candidate in self.candidates
        ):
            raise ValueError("sandbox launch aggregate does not match candidates")
        if not math.isclose(
            self.sandbox_total_duration_seconds,
            math.fsum(
                candidate.executor_total_duration_seconds
                for candidate in self.candidates
            ),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("sandbox duration aggregate does not match candidates")
        return self

    def write_json(self, path: str | Path) -> None:
        """Write once so a completed summary is never silently replaced."""

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump_json(indent=2) + "\n"
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
