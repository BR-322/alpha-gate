from __future__ import annotations

import numpy as np
import pytest
from gate_runner_core.scoring import BacktestResult

from alpha_gate.backtest import ProgramEvaluation
from alpha_gate.candidate import CandidateProgram, CandidateValidator
from alpha_gate.scoring import ProgramHonestScorer

from .conftest import SAFE_SOURCE


def _backtest(scale: float = 1.0) -> BacktestResult:
    pattern = np.asarray([0.001, -0.0004, 0.0008, -0.0002] * 20) * scale
    window_returns = pattern.reshape(4, 20)
    window_sharpes = np.asarray(
        [
            np.mean(values) / np.std(values, ddof=1) * np.sqrt(252.0)
            for values in window_returns
        ]
    )
    return BacktestResult(
        daily_returns=pattern,
        window_sharpes=window_sharpes,
        window_tail_score=0.1,
        reference_window_risk=0.02,
        daily_expected_shortfall=0.0004 * scale,
        expected_shortfall_ratio=0.5,
        raw_sharpe=float(np.mean(pattern) / np.std(pattern, ddof=1) * np.sqrt(252.0)),
        turnover=2.0,
        carry_contribution=0.0,
        active_fraction=0.5,
        exposure_weighted_active_fraction=0.5,
        active_session_fraction=1.0,
        average_gross_exposure=0.5,
        median_gross_exposure=0.5,
        mean_active_gross_exposure=0.5,
        cash_fraction=0.5,
        max_weight=0.25,
        effective_position_count=2.0,
        realized_volatility=float(np.std(pattern, ddof=1) * np.sqrt(252.0)),
        active_windows=4,
    )


def test_program_scoring_preserves_trials_and_disables_complexity_reward() -> None:
    program = CandidateProgram(source=SAFE_SOURCE)
    valid = ProgramEvaluation(
        program_sha256=program.sha256,
        metadata=CandidateValidator.validate(program),
        backtest=_backtest(),
    )
    invalid = ProgramEvaluation(program_sha256="invalid", error="sandbox timeout")

    scores = ProgramHonestScorer().score_group((valid, invalid))

    assert len(scores) == 2
    assert scores[0].score.validity == 1.0
    assert scores[0].score.trial_count == 2.0
    assert scores[0].score.complexity == 0.0
    assert scores[0].score.parameter_count == 0.0
    assert scores[1].score.validity == 0.0
    assert scores[1].score.reward == 0.0
    assert scores[1].score.trial_count == 2.0
    payload = scores[0].to_dict()
    assert payload["source_bytes"] == valid.metadata.source_bytes
    assert payload["ast_nodes"] == valid.metadata.ast_nodes
    assert payload["error"] == ""


def test_group_diagnostics_use_all_valid_programs() -> None:
    evaluations = (
        ProgramEvaluation(program_sha256="one", backtest=_backtest(1.0)),
        ProgramEvaluation(program_sha256="two", backtest=_backtest(-0.5)),
    )

    scores = ProgramHonestScorer().score_group(evaluations)

    assert scores[0].score.trial_count == 2.0
    assert scores[1].score.trial_count == 2.0
    assert scores[0].score.pbo == scores[1].score.pbo
    assert scores[0].score.behavioral_effective_rank == pytest.approx(
        scores[1].score.behavioral_effective_rank
    )
    assert scores[0].score.mean_pairwise_absolute_correlation == pytest.approx(1.0)


def test_empty_program_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ProgramHonestScorer().score_group(())
