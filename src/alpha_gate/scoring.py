"""Gate Runner scoring adapter for precomputed Python-program backtests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from gate_runner_core.config import StrategyConfig
from gate_runner_core.scoring import (
    BacktestResult,
    HonestScore,
    HonestScorer,
    StrategyBacktester,
)

from alpha_gate.backtest import ProgramEvaluation


@dataclass(frozen=True)
class ProgramScore:
    """A Gate Runner honest score paired with Python-source diagnostics."""

    evaluation: ProgramEvaluation
    score: HonestScore

    @property
    def reward(self) -> float:
        return self.score.reward

    def to_dict(self) -> dict[str, object]:
        metadata = self.evaluation.metadata
        return {
            "program_sha256": self.evaluation.program_sha256,
            "reward": float(self.score.reward),
            "metrics": self.score.metrics(),
            "source_bytes": metadata.source_bytes if metadata is not None else 0,
            "ast_nodes": metadata.ast_nodes if metadata is not None else 0,
            "error": self.evaluation.error,
        }


@dataclass(frozen=True)
class _PrecomputedStrategy:
    key: str
    result: BacktestResult
    normalized_complexity: float = 0.0
    parameter_count: int = 0

    def canonical_json(self) -> str:
        return self.key


class _PrecomputedBacktester:
    def evaluate(
        self,
        strategy: _PrecomputedStrategy,
        as_of_index: int,
    ) -> BacktestResult:
        del as_of_index
        return strategy.result


class ProgramHonestScorer:
    """Reuse Gate Runner's exact group math with complexity reward disabled."""

    def score_group(
        self,
        evaluations: Sequence[ProgramEvaluation],
    ) -> tuple[ProgramScore, ...]:
        if not evaluations:
            raise ValueError("program evaluation group must not be empty")
        candidates = [
            (
                _PrecomputedStrategy(
                    key=f"{index}:{evaluation.program_sha256}",
                    result=evaluation.backtest,
                )
                if evaluation.backtest is not None
                else None
            )
            for index, evaluation in enumerate(evaluations)
        ]
        # Gate Runner's scorer is intentionally duck-typed here: its score_group
        # only needs canonical_json, complexity diagnostics, and evaluate(). This
        # keeps its DSR/PBO/diversity/reward math authoritative without pretending
        # Python programs are StrategyConfig instances.
        backtester = cast(StrategyBacktester, _PrecomputedBacktester())
        strategies = cast(list[StrategyConfig | None], candidates)
        scores = HonestScorer(backtester).score_group(strategies, as_of_index=0)
        return tuple(
            ProgramScore(evaluation=evaluation, score=score)
            for evaluation, score in zip(evaluations, scores, strict=True)
        )
