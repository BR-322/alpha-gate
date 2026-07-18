from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from gate_runner_core.market import MarketData

from alpha_gate.backtest import ProgramBacktester
from alpha_gate.candidate import CandidateProgram
from alpha_gate.evaluator import ProgramGroupEvaluator
from alpha_gate.executors.base import (
    FrameOutcome,
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)

from .conftest import SAFE_SOURCE


def _market(
    *,
    spot_return: float = 0.001,
    carry_return: float | None = None,
    spread_bps: float = 0.0,
) -> MarketData:
    count = 1_600
    dates = tuple(
        (date(2010, 1, 1) + timedelta(days=index)).isoformat() for index in range(count)
    )
    close = np.ones((count, 2), dtype=float) * 100.0
    close[:, 0] *= np.power(1.0 + spot_return, np.arange(count))
    spread = np.full_like(close, spread_bps)
    if carry_return is None:
        return MarketData(
            dates=dates,
            symbols=("A", "B"),
            close=close,
            spread_bps=spread,
            source_label="test panel",
        )
    carry = np.full_like(close, carry_return)
    rates = np.zeros_like(close)
    return MarketData(
        dates=dates,
        symbols=("A", "B"),
        close=close,
        spread_bps=spread,
        source_label="test carry panel",
        carry_returns=carry,
        foreign_reference_rates_percent=rates,
        base_reference_rates_percent=rates,
        rate_source_label="test rates",
    )


class RecordingExecutor(SandboxExecutor):
    def __init__(
        self,
        weights: tuple[float, ...] = (0.25, 0.0),
        *,
        status: SandboxStatus = SandboxStatus.COMPLETED,
        drop_last_frame: bool = False,
    ) -> None:
        self.weights = weights
        self.status = status
        self.drop_last_frame = drop_last_frame
        self.requests: list[SandboxRequest] = []

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        frame_count = len(request.bars) - int(self.drop_last_frame)
        frames = tuple(
            FrameOutcome(
                sequence=sequence,
                weights=self.weights,
                duration_seconds=0.0,
            )
            for sequence in range(frame_count)
        )
        return SandboxResult(
            status=self.status,
            program_sha256=request.program.sha256,
            frames=frames,
            duration_seconds=0.0,
            error="deadline" if self.status is SandboxStatus.TIMEOUT else "",
        )


def _backtester(
    market: MarketData,
    executor: SandboxExecutor,
    *,
    cost_bps_per_side: float = 10.0,
    seed: int = 11,
) -> ProgramBacktester:
    return ProgramBacktester(
        market=market,
        executor=executor,
        windows=4,
        window_days=20,
        warmup_days=5,
        cost_bps_per_side=cost_bps_per_side,
        seed=seed,
    )


@pytest.mark.asyncio
async def test_weights_are_lagged_costed_and_liquidated_per_window() -> None:
    market = _market()
    executor = RecordingExecutor()
    evaluation = await _backtester(market, executor).evaluate(
        CandidateProgram(source=SAFE_SOURCE),
        as_of_index=300,
    )

    assert evaluation.succeeded
    assert evaluation.backtest is not None
    result = evaluation.backtest
    assert len(executor.requests) == 4
    assert [request.initialization.seed for request in executor.requests] == [
        11,
        12,
        13,
        14,
    ]
    first_request = executor.requests[0]
    assert len(first_request.bars) == 24
    assert first_request.bars[0].date == market.dates[295]
    assert first_request.bars[-1].date == market.dates[318]
    assert market.dates[319] not in {bar.date for bar in first_request.bars}

    expected_middle_return = 0.25 * 0.001
    for window in range(4):
        values = result.daily_returns[window * 20 : (window + 1) * 20]
        assert values[0] == pytest.approx(0.0, abs=1e-12)
        assert values[-1] == pytest.approx(0.0, abs=1e-12)
        assert values[1:-1] == pytest.approx(expected_middle_return)
    assert result.turnover == pytest.approx(2.0)
    assert result.average_gross_exposure == pytest.approx(0.25)
    assert result.mean_active_gross_exposure == pytest.approx(0.25)
    assert result.max_weight == pytest.approx(0.25)
    assert result.effective_position_count == pytest.approx(1.0)
    assert result.active_windows == 4


@pytest.mark.asyncio
async def test_short_liquidation_is_a_cost_not_a_credit() -> None:
    executor = RecordingExecutor(weights=(-0.25, 0.0))
    evaluation = await _backtester(_market(), executor).evaluate(
        CandidateProgram(source=SAFE_SOURCE),
        as_of_index=300,
    )

    assert evaluation.backtest is not None
    values = evaluation.backtest.daily_returns[:20]
    assert values[0] == pytest.approx(-0.0005)
    assert values[-1] == pytest.approx(-0.0005)
    assert values[1:-1] == pytest.approx(-0.00025)


@pytest.mark.asyncio
async def test_spot_and_carry_are_compounded_in_trusted_code() -> None:
    executor = RecordingExecutor()
    evaluation = await _backtester(
        _market(spot_return=0.01, carry_return=0.02),
        executor,
        cost_bps_per_side=0.0,
    ).evaluate(CandidateProgram(source=SAFE_SOURCE), as_of_index=300)

    assert evaluation.backtest is not None
    result = evaluation.backtest
    assert result.daily_returns == pytest.approx(np.full(80, 0.25 * 0.0302))
    assert result.carry_contribution == pytest.approx(80 * 0.25 * 1.01 * 0.02)


@pytest.mark.asyncio
async def test_sandbox_failure_fails_closed_and_stops_remaining_windows() -> None:
    executor = RecordingExecutor(status=SandboxStatus.TIMEOUT)
    evaluation = await _backtester(_market(), executor).evaluate(
        CandidateProgram(source=SAFE_SOURCE),
        as_of_index=300,
    )

    assert not evaluation.succeeded
    assert evaluation.backtest is None
    assert evaluation.error == "window 0 sandbox timeout: deadline"
    assert len(executor.requests) == 1


@pytest.mark.asyncio
async def test_executor_contract_violation_fails_closed() -> None:
    executor = RecordingExecutor(drop_last_frame=True)
    evaluation = await _backtester(_market(), executor).evaluate(
        CandidateProgram(source=SAFE_SOURCE),
        as_of_index=300,
    )

    assert not evaluation.succeeded
    assert evaluation.backtest is None
    assert "wrong number of frames" in evaluation.error


@pytest.mark.asyncio
async def test_invalid_source_never_reaches_executor() -> None:
    executor = RecordingExecutor()
    evaluation = await _backtester(_market(), executor).evaluate(
        CandidateProgram(source="import os"),
        as_of_index=300,
    )

    assert not evaluation.succeeded
    assert "candidate source invalid" in evaluation.error
    assert executor.requests == []


def test_rejects_horizon_without_warmup_or_future_sessions() -> None:
    backtester = _backtester(_market(), RecordingExecutor())

    with pytest.raises(ValueError, match="required warm-up"):
        backtester._validate_horizon(4)
    with pytest.raises(ValueError, match="required horizon"):
        backtester._validate_horizon(1_550)


@pytest.mark.asyncio
async def test_group_evaluator_counts_invalid_programs_as_trials() -> None:
    executor = RecordingExecutor()
    evaluator = ProgramGroupEvaluator(_backtester(_market(), executor))

    scores = await evaluator.evaluate(
        (
            CandidateProgram(source=SAFE_SOURCE),
            CandidateProgram(source="import os"),
        ),
        as_of_index=300,
    )

    assert len(scores) == 2
    assert scores[0].score.validity == 1.0
    assert scores[1].score.validity == 0.0
    assert scores[0].score.trial_count == scores[1].score.trial_count == 2.0
    assert "candidate source invalid" in scores[1].evaluation.error
    assert len(executor.requests) == 4


@pytest.mark.asyncio
async def test_group_evaluator_rejects_empty_group() -> None:
    evaluator = ProgramGroupEvaluator(_backtester(_market(), RecordingExecutor()))

    with pytest.raises(ValueError, match="must not be empty"):
        await evaluator.evaluate((), as_of_index=300)
