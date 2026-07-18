"""Budget-bounded local experiment loop with cumulative honest scoring."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha_gate.backtest import ProgramEvaluation
from alpha_gate.candidate import CandidateProgram
from alpha_gate.evolution.base import (
    Evolver,
    ParentCandidate,
    Proposal,
    ProposalRequest,
)
from alpha_gate.ledger import MemoryLedger, TrialLedger, TrialRecord
from alpha_gate.scoring import ProgramScore


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    experiment_id: str = Field(
        default="local-baseline",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    as_of_index: int = Field(ge=0)
    seed: int = 17
    generations: int = Field(default=8, ge=1, le=1_000)
    batch_size: int = Field(default=8, ge=1, le=256)
    evaluation_budget: int = Field(default=64, ge=1, le=100_000)
    elite_count: int = Field(default=4, ge=1, le=256)

    @model_validator(mode="after")
    def elite_count_fits_batch(self) -> ExperimentConfig:
        if self.elite_count > self.batch_size:
            raise ValueError("elite_count must not exceed batch_size")
        return self


class CumulativeScorer(Protocol):
    def score_group(
        self,
        evaluations: Sequence[ProgramEvaluation],
    ) -> tuple[ProgramScore, ...]: ...


class BatchEvaluator(Protocol):
    @property
    def scorer(self) -> CumulativeScorer: ...

    async def backtest(
        self,
        programs: Sequence[CandidateProgram],
        as_of_index: int,
    ) -> tuple[ProgramEvaluation, ...]: ...


@dataclass(frozen=True)
class CandidateTrial:
    candidate_id: str
    evaluation_index: int
    generation: int
    proposal: Proposal


@dataclass(frozen=True)
class CandidateOutcome:
    trial: CandidateTrial
    evaluation: ProgramEvaluation
    score: ProgramScore


@dataclass(frozen=True)
class ExperimentResult:
    config: ExperimentConfig
    outcomes: tuple[CandidateOutcome, ...]
    records: tuple[TrialRecord, ...]
    generations_completed: int
    stop_reason: Literal["evaluation_budget", "generation_limit"]

    @property
    def evaluations_used(self) -> int:
        return len(self.outcomes)

    @property
    def ranked_outcomes(self) -> tuple[CandidateOutcome, ...]:
        return tuple(
            sorted(
                self.outcomes,
                key=lambda outcome: (
                    -outcome.score.reward,
                    -outcome.score.score.passed,
                    -outcome.score.score.validity,
                    outcome.trial.candidate_id,
                ),
            )
        )


class ExperimentRunner:
    """Propose, execute once, cumulatively rescore, and append audit records."""

    def __init__(
        self,
        config: ExperimentConfig,
        evolver: Evolver,
        evaluator: BatchEvaluator,
        ledger: TrialLedger | None = None,
    ) -> None:
        self.config = config
        self.evolver = evolver
        self.evaluator = evaluator
        self.ledger = ledger or MemoryLedger()

    async def run(
        self,
        seed_programs: Sequence[CandidateProgram],
    ) -> ExperimentResult:
        if not seed_programs:
            raise ValueError("seed_programs must not be empty")
        seeds = tuple(seed_programs)
        trials: list[CandidateTrial] = []
        evaluations: list[ProgramEvaluation] = []
        records: list[TrialRecord] = []
        parents: tuple[ParentCandidate, ...] = ()
        final_scores: tuple[ProgramScore, ...] = ()
        generations_completed = 0

        for generation in range(self.config.generations):
            remaining = self.config.evaluation_budget - len(trials)
            if remaining <= 0:
                break
            batch_size = min(self.config.batch_size, remaining)
            request = ProposalRequest(
                generation=generation,
                batch_size=batch_size,
                seed=self.config.seed + generation,
                seed_programs=seeds,
                parents=parents,
                seen_program_sha256=frozenset(
                    trial.proposal.program.sha256 for trial in trials
                ),
            )
            proposals = await self.evolver.propose(request)
            if len(proposals) != batch_size:
                raise ValueError("evolver returned the wrong proposal count")

            first_index = len(trials)
            new_trials = tuple(
                CandidateTrial(
                    candidate_id=(
                        f"{self.config.experiment_id}:g{generation:04d}:"
                        f"t{first_index + offset:06d}"
                    ),
                    evaluation_index=first_index + offset,
                    generation=generation,
                    proposal=proposal,
                )
                for offset, proposal in enumerate(proposals)
            )
            new_evaluations = await self.evaluator.backtest(
                tuple(proposal.program for proposal in proposals),
                self.config.as_of_index,
            )
            if len(new_evaluations) != batch_size:
                raise ValueError("evaluator returned the wrong evaluation count")
            trials.extend(new_trials)
            evaluations.extend(new_evaluations)
            final_scores = self.evaluator.scorer.score_group(evaluations)
            if len(final_scores) != len(trials):
                raise ValueError("scorer returned the wrong cumulative score count")
            if any(
                score.evaluation.program_sha256 != evaluation.program_sha256
                for score, evaluation in zip(
                    final_scores,
                    evaluations,
                    strict=True,
                )
            ):
                raise ValueError("scorer changed cumulative evaluation ordering")
            if any(
                score.score.trial_count != float(len(trials)) for score in final_scores
            ):
                raise ValueError("scorer did not preserve cumulative trial count")

            for trial, evaluation, score in zip(
                new_trials,
                new_evaluations,
                final_scores[first_index:],
                strict=True,
            ):
                record = self._record(trial, evaluation, score)
                self.ledger.append(record)
                records.append(record)
            parents = self._select_parents(trials, evaluations, final_scores)
            generations_completed += 1

        outcomes = tuple(
            CandidateOutcome(trial=trial, evaluation=evaluation, score=score)
            for trial, evaluation, score in zip(
                trials,
                evaluations,
                final_scores,
                strict=True,
            )
        )
        stop_reason: Literal["evaluation_budget", "generation_limit"] = (
            "evaluation_budget"
            if len(trials) >= self.config.evaluation_budget
            else "generation_limit"
        )
        return ExperimentResult(
            config=self.config,
            outcomes=outcomes,
            records=tuple(records),
            generations_completed=generations_completed,
            stop_reason=stop_reason,
        )

    def _select_parents(
        self,
        trials: Sequence[CandidateTrial],
        evaluations: Sequence[ProgramEvaluation],
        scores: Sequence[ProgramScore],
    ) -> tuple[ParentCandidate, ...]:
        ranked = sorted(
            zip(trials, evaluations, scores, strict=True),
            key=lambda item: (
                -item[2].reward,
                -item[2].score.passed,
                -item[2].score.validity,
                item[0].candidate_id,
            ),
        )
        return tuple(
            ParentCandidate(
                program=trial.proposal.program,
                reward=score.reward,
                passed=bool(score.score.passed),
                validity=score.score.validity,
            )
            for trial, _evaluation, score in ranked[: self.config.elite_count]
        )

    def _record(
        self,
        trial: CandidateTrial,
        evaluation: ProgramEvaluation,
        score: ProgramScore,
    ) -> TrialRecord:
        metadata = evaluation.metadata
        sandbox_results = evaluation.sandbox_results
        durations = tuple(result.duration_seconds for result in sandbox_results)
        total_duration = math.fsum(durations)
        return TrialRecord(
            experiment_id=self.config.experiment_id,
            candidate_id=trial.candidate_id,
            evaluation_index=trial.evaluation_index,
            generation=trial.generation,
            program_sha256=trial.proposal.program.sha256,
            program_source=trial.proposal.program.source,
            parent_sha256=trial.proposal.parent_sha256,
            origin=trial.proposal.origin,
            mutation=trial.proposal.mutation,
            reward=score.reward,
            passed=bool(score.score.passed),
            validity=score.score.validity,
            trial_count_at_evaluation=int(score.score.trial_count),
            source_bytes=metadata.source_bytes if metadata is not None else 0,
            ast_nodes=metadata.ast_nodes if metadata is not None else 0,
            metrics=score.score.metrics(),
            error=evaluation.error,
            executor_window_statuses=tuple(
                result.status.value for result in sandbox_results
            ),
            executor_window_durations_seconds=durations,
            executor_total_duration_seconds=total_duration,
            executor_stdout_bytes=sum(
                result.stdout_bytes for result in sandbox_results
            ),
            executor_stderr_bytes=sum(
                result.stderr_bytes for result in sandbox_results
            ),
        )
