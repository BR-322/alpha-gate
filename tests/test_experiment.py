from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from gate_runner_core.scoring import HonestScore
from pydantic import ValidationError

from alpha_gate.backtest import ProgramEvaluation
from alpha_gate.candidate import CandidateProgram, CandidateValidator
from alpha_gate.evolution import LocalEvolver
from alpha_gate.executors.base import SandboxResult, SandboxStatus
from alpha_gate.experiment import ExperimentConfig, ExperimentRunner
from alpha_gate.ledger import JsonlLedger, MemoryLedger, TrialRecord
from alpha_gate.scoring import ProgramScore
from alpha_gate.summary import EvaluationProtocol, ExperimentSummary

SEED_PATH = Path(__file__).parents[1] / "examples" / "seed_strategy.py"


class FakeCumulativeScorer:
    def __init__(self) -> None:
        self.group_sizes: list[int] = []

    def score_group(
        self,
        evaluations: Sequence[ProgramEvaluation],
    ) -> tuple[ProgramScore, ...]:
        self.group_sizes.append(len(evaluations))
        trial_count = float(len(evaluations))
        return tuple(
            ProgramScore(
                evaluation=evaluation,
                score=HonestScore(
                    reward=int(evaluation.program_sha256[:8], 16) / 0xFFFFFFFF,
                    validity=1.0,
                    trial_count=trial_count,
                ),
            )
            for evaluation in evaluations
        )


class FakeBatchEvaluator:
    def __init__(self) -> None:
        self.scorer = FakeCumulativeScorer()
        self.batch_sizes: list[int] = []
        self.as_of_indices: list[int] = []

    async def backtest(
        self,
        programs: Sequence[CandidateProgram],
        as_of_index: int,
    ) -> tuple[ProgramEvaluation, ...]:
        self.batch_sizes.append(len(programs))
        self.as_of_indices.append(as_of_index)
        return tuple(self._evaluation(program) for program in programs)

    @staticmethod
    def _evaluation(program: CandidateProgram) -> ProgramEvaluation:
        sandbox_results = (
            SandboxResult(
                status=SandboxStatus.COMPLETED,
                program_sha256=program.sha256,
                duration_seconds=0.1,
                stdout_bytes=10,
                stderr_bytes=2,
            ),
            SandboxResult(
                status=SandboxStatus.COMPLETED,
                program_sha256=program.sha256,
                duration_seconds=0.2,
                stdout_bytes=20,
                stderr_bytes=3,
            ),
        )
        return ProgramEvaluation(
            program_sha256=program.sha256,
            metadata=CandidateValidator.validate(program),
            sandbox_results=sandbox_results,
        )


def _seed() -> CandidateProgram:
    return CandidateProgram(source=SEED_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_experiment_honors_exact_budget_and_cumulative_trials(
    tmp_path: Path,
) -> None:
    evaluator = FakeBatchEvaluator()
    ledger_path = tmp_path / "run.jsonl"
    runner = ExperimentRunner(
        config=ExperimentConfig(
            experiment_id="budget-test",
            as_of_index=300,
            generations=5,
            batch_size=4,
            evaluation_budget=10,
            elite_count=2,
            seed=23,
        ),
        evolver=LocalEvolver(),
        evaluator=evaluator,
        ledger=JsonlLedger(ledger_path),
    )

    result = await runner.run((_seed(),))

    assert result.evaluations_used == 10
    assert result.generations_completed == 3
    assert result.stop_reason == "evaluation_budget"
    assert evaluator.batch_sizes == [4, 4, 2]
    assert evaluator.as_of_indices == [300, 300, 300]
    assert evaluator.scorer.group_sizes == [4, 8, 10]
    assert len(result.records) == 10
    assert [record.evaluation_index for record in result.records] == list(range(10))
    assert [record.trial_count_at_evaluation for record in result.records] == [
        4,
        4,
        4,
        4,
        8,
        8,
        8,
        8,
        10,
        10,
    ]
    assert all(
        record.executor_window_durations_seconds == (0.1, 0.2)
        for record in result.records
    )
    assert all(
        record.executor_total_duration_seconds == pytest.approx(0.3)
        for record in result.records
    )
    assert all(record.executor_stdout_bytes == 30 for record in result.records)
    assert all(record.executor_stderr_bytes == 5 for record in result.records)
    assert (
        len({outcome.trial.proposal.program.sha256 for outcome in result.outcomes})
        == 10
    )
    assert all(outcome.score.score.trial_count == 10.0 for outcome in result.outcomes)
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 10

    summary = ExperimentSummary.from_result(
        result,
        EvaluationProtocol(
            dataset="synthetic",
            source_label="deterministic test panel",
            symbols=("A", "B"),
            first_scored_date="2026-01-01",
            last_scored_date="2026-12-31",
            windows=4,
            window_days=20,
            warmup_days=253,
            cost_bps_per_side=10.0,
            runtime="docker",
            runtime_path="/usr/local/bin/docker",
            image="alpha-gate-sandbox:dev",
            image_id="sha256:test",
        ),
    )
    summary_path = tmp_path / "run.summary.json"
    summary.write_json(summary_path)
    reloaded = ExperimentSummary.model_validate_json(
        summary_path.read_text(encoding="utf-8")
    )
    assert reloaded == summary
    assert len(summary.candidates) == 10
    assert [candidate.rank for candidate in summary.candidates] == list(range(1, 11))
    assert all(candidate.final_trial_count == 10 for candidate in summary.candidates)
    with pytest.raises(FileExistsError):
        summary.write_json(summary_path)


@pytest.mark.asyncio
async def test_experiment_is_reproducible_with_same_seed() -> None:
    config = ExperimentConfig(
        experiment_id="repro",
        as_of_index=300,
        generations=2,
        batch_size=3,
        evaluation_budget=6,
        elite_count=2,
        seed=101,
    )
    first = await ExperimentRunner(
        config,
        LocalEvolver(),
        FakeBatchEvaluator(),
    ).run((_seed(),))
    second = await ExperimentRunner(
        config,
        LocalEvolver(),
        FakeBatchEvaluator(),
    ).run((_seed(),))

    assert [record.model_dump() for record in first.records] == [
        record.model_dump() for record in second.records
    ]


def _record(index: int = 0) -> TrialRecord:
    source = "class Strategy: pass"
    return TrialRecord(
        experiment_id="ledger",
        candidate_id=f"ledger:g0000:t{index:06d}",
        evaluation_index=index,
        generation=0,
        program_sha256=hashlib.sha256(source.encode()).hexdigest(),
        program_source=source,
        parent_sha256=(),
        origin="seed",
        mutation="unmodified seed",
        reward=0.1,
        passed=False,
        validity=1.0,
        trial_count_at_evaluation=1,
        source_bytes=10,
        ast_nodes=2,
        metrics={"reward": 0.1},
        executor_window_statuses=(),
        executor_window_durations_seconds=(),
        executor_total_duration_seconds=0.0,
        executor_stdout_bytes=0,
        executor_stderr_bytes=0,
    )


def test_memory_ledger_rejects_duplicate_evaluation() -> None:
    ledger = MemoryLedger()
    ledger.append(_record())

    with pytest.raises(ValueError, match="already contains"):
        ledger.append(_record())


def test_jsonl_ledger_reloads_without_truncating(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ledger.jsonl"
    JsonlLedger(path).append(_record(0))
    reopened = JsonlLedger(path)
    reopened.append(_record(1))

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    with pytest.raises(ValueError, match="already contains"):
        JsonlLedger(path).append(_record(1))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"program_sha256": "b" * 64}, "does not match program_source"),
        (
            {"executor_window_statuses": ("completed",)},
            "statuses and durations must align",
        ),
        (
            {"executor_total_duration_seconds": 1.0},
            "total duration does not match",
        ),
    ],
)
def test_trial_record_rejects_inconsistent_audit_fields(
    update: dict[str, object],
    message: str,
) -> None:
    values = _record().model_dump()
    values.update(update)

    with pytest.raises(ValidationError, match=message):
        TrialRecord.model_validate(values)
