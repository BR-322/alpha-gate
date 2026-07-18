"""Grouped program evaluation that preserves the full proposed trial count."""

from __future__ import annotations

from collections.abc import Sequence

from alpha_gate.backtest import ProgramBacktester
from alpha_gate.candidate import CandidateProgram
from alpha_gate.scoring import ProgramHonestScorer, ProgramScore


class ProgramGroupEvaluator:
    """Backtest every proposal before scoring the complete group together."""

    def __init__(
        self,
        backtester: ProgramBacktester,
        scorer: ProgramHonestScorer | None = None,
    ) -> None:
        self.backtester = backtester
        self.scorer = scorer or ProgramHonestScorer()

    async def evaluate(
        self,
        programs: Sequence[CandidateProgram],
        as_of_index: int,
    ) -> tuple[ProgramScore, ...]:
        if not programs:
            raise ValueError("program group must not be empty")
        evaluations = [
            await self.backtester.evaluate(program, as_of_index) for program in programs
        ]
        return self.scorer.score_group(evaluations)
