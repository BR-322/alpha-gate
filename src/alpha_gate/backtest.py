"""Trusted streaming backtester for sandboxed Python strategy programs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from gate_runner_core.scoring import BacktestResult, StrategyBacktester
from numpy.typing import NDArray

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
)
from alpha_gate.protocol import BarFrame, InitializeFrame, validate_target_weights

FloatArray = NDArray[np.float64]


class MarketPanel(Protocol):
    """The public Gate Runner market fields required by program evaluation."""

    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    close: FloatArray
    returns: FloatArray
    spread_bps: FloatArray
    carry_returns: FloatArray
    foreign_reference_rates_percent: FloatArray
    base_reference_rates_percent: FloatArray


@dataclass(frozen=True)
class ProgramEvaluation:
    """A candidate backtest or a fail-closed executor/source error."""

    program_sha256: str
    metadata: CandidateMetadata | None = None
    backtest: BacktestResult | None = None
    sandbox_results: tuple[SandboxResult, ...] = ()
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.backtest is not None and not self.error


@dataclass(frozen=True)
class _WindowResult:
    daily_returns: FloatArray
    turnover: float
    carry_contribution: float
    gross_exposure: FloatArray
    meaningful_active: NDArray[np.bool_]
    effective_position_count: FloatArray
    max_weight: FloatArray


class ProgramBacktester:
    """Evaluate fresh per-window strategy instances with one-session execution lag."""

    MEANINGFUL_WEIGHT = StrategyBacktester.MEANINGFUL_WEIGHT

    def __init__(
        self,
        market: MarketPanel,
        executor: SandboxExecutor,
        *,
        windows: int = 8,
        window_days: int = 42,
        warmup_days: int = 253,
        cost_bps_per_side: float = 10.0,
        seed: int = 17,
        max_gross: float = 1.0,
        max_abs_weight: float = 0.25,
        allow_short: bool = True,
        limits: ExecutionLimits | None = None,
    ) -> None:
        if windows < 4 or windows % 2:
            raise ValueError("windows must be an even integer of at least 4 for CSCV")
        if window_days < 20:
            raise ValueError("window_days must be at least 20")
        if warmup_days < 1:
            raise ValueError("warmup_days must be positive")
        if not math.isfinite(cost_bps_per_side) or cost_bps_per_side < 0.0:
            raise ValueError("cost_bps_per_side must be finite and non-negative")
        InitializeFrame(
            symbols=market.symbols,
            seed=seed,
            max_gross=max_gross,
            max_abs_weight=max_abs_weight,
            allow_short=allow_short,
        )
        self.market = market
        self.executor = executor
        self.windows = windows
        self.window_days = window_days
        self.warmup_days = warmup_days
        self.cost_bps_per_side = cost_bps_per_side
        self.seed = seed
        self.max_gross = max_gross
        self.max_abs_weight = max_abs_weight
        self.allow_short = allow_short
        self.limits = limits or ExecutionLimits()
        required_frames = warmup_days + window_days - 1
        if required_frames > self.limits.max_frames:
            raise ValueError("warm-up and window exceed limits.max_frames")

    async def evaluate(
        self,
        program: CandidateProgram,
        as_of_index: int,
    ) -> ProgramEvaluation:
        """Execute and backtest one candidate; candidate failures score invalid."""

        self._validate_horizon(as_of_index)
        try:
            metadata = CandidateValidator.validate(program)
        except CandidateSourceError as exc:
            return ProgramEvaluation(
                program_sha256=program.sha256,
                error=f"candidate source invalid: {exc}",
            )

        windows: list[_WindowResult] = []
        sandbox_results: list[SandboxResult] = []
        for window_index in range(self.windows):
            start = as_of_index + window_index * self.window_days
            end = start + self.window_days
            request = self._build_request(
                program=program,
                start=start,
                end=end,
                seed=self.seed + window_index,
            )
            sandbox = await self.executor.execute(request)
            sandbox_results.append(sandbox)
            if not sandbox.succeeded:
                detail = f": {sandbox.error}" if sandbox.error else ""
                return ProgramEvaluation(
                    program_sha256=program.sha256,
                    metadata=metadata,
                    sandbox_results=tuple(sandbox_results),
                    error=(
                        f"window {window_index} sandbox {sandbox.status.value}{detail}"
                    ),
                )
            try:
                windows.append(self._score_window(request, sandbox, start, end))
            except ValueError as exc:
                return ProgramEvaluation(
                    program_sha256=program.sha256,
                    metadata=metadata,
                    sandbox_results=tuple(sandbox_results),
                    error=f"window {window_index} executor contract violation: {exc}",
                )

        return ProgramEvaluation(
            program_sha256=program.sha256,
            metadata=metadata,
            backtest=self._summarize(windows, as_of_index),
            sandbox_results=tuple(sandbox_results),
        )

    def _validate_horizon(self, as_of_index: int) -> None:
        horizon_end = as_of_index + self.windows * self.window_days
        if as_of_index < self.warmup_days:
            raise ValueError("as_of_index does not support the required warm-up")
        if horizon_end > len(self.market.dates):
            raise ValueError("as_of_index does not support the required horizon")

    def _build_request(
        self,
        program: CandidateProgram,
        start: int,
        end: int,
        seed: int,
    ) -> SandboxRequest:
        initialization = InitializeFrame(
            symbols=self.market.symbols,
            seed=seed,
            max_gross=self.max_gross,
            max_abs_weight=self.max_abs_weight,
            allow_short=self.allow_short,
        )
        first_observation = start - self.warmup_days
        last_observation = end - 2
        bars = tuple(
            self._bar_frame(sequence, market_index)
            for sequence, market_index in enumerate(
                range(first_observation, last_observation + 1)
            )
        )
        return SandboxRequest(
            program=program,
            initialization=initialization,
            bars=bars,
            limits=self.limits,
        )

    def _bar_frame(self, sequence: int, market_index: int) -> BarFrame:
        return BarFrame(
            sequence=sequence,
            date=self.market.dates[market_index],
            close=tuple(float(value) for value in self.market.close[market_index]),
            returns_1d=tuple(
                float(value) for value in self.market.returns[market_index]
            ),
            carry_returns_1d=tuple(
                float(value) for value in self.market.carry_returns[market_index]
            ),
            spread_bps=tuple(
                float(value) for value in self.market.spread_bps[market_index]
            ),
            foreign_rate_percent=tuple(
                float(value)
                for value in self.market.foreign_reference_rates_percent[market_index]
            ),
            base_rate_percent=tuple(
                float(value)
                for value in self.market.base_reference_rates_percent[market_index]
            ),
        )

    def _score_window(
        self,
        request: SandboxRequest,
        sandbox: SandboxResult,
        start: int,
        end: int,
    ) -> _WindowResult:
        if len(sandbox.frames) != len(request.bars):
            raise ValueError("executor returned the wrong number of frames")
        validated: list[tuple[float, ...]] = []
        for expected_sequence, frame in enumerate(sandbox.frames):
            if frame.sequence != expected_sequence:
                raise ValueError("executor frame sequences are not contiguous")
            validated.append(
                validate_target_weights(frame.weights, request.initialization)
            )
        weights = np.asarray(validated[self.warmup_days - 1 :], dtype=float)
        expected_shape = (self.window_days, len(self.market.symbols))
        if weights.shape != expected_shape:
            raise ValueError("executor weights do not cover the scoring window")

        daily_returns = np.zeros(self.window_days, dtype=float)
        gross_exposure = np.zeros(self.window_days, dtype=float)
        meaningful_active = np.zeros(self.window_days, dtype=bool)
        effective_position_count = np.zeros(self.window_days, dtype=float)
        max_weight = np.zeros(self.window_days, dtype=float)
        previous_weights = np.zeros(len(self.market.symbols), dtype=float)
        total_turnover = 0.0
        total_carry_contribution = 0.0

        for offset, day_index in enumerate(range(start, end)):
            current_weights = weights[offset]
            gross = float(np.sum(np.abs(current_weights)))
            gross_exposure[offset] = gross
            meaningful_active[offset] = bool(
                np.any(np.abs(current_weights) >= self.MEANINGFUL_WEIGHT)
            )
            if gross > 0.0:
                effective_position_count[offset] = gross**2 / float(
                    np.sum(current_weights**2)
                )
                max_weight[offset] = float(np.max(np.abs(current_weights)))

            prior_index = day_index - 1
            traded_weight = np.abs(current_weights - previous_weights)
            per_side_cost = (
                self.cost_bps_per_side + self.market.spread_bps[prior_index]
            ) / 10_000.0
            transaction_cost = float(np.dot(traded_weight, per_side_cost))
            total_turnover += float(np.sum(traded_weight))

            spot_returns = self.market.returns[day_index]
            carry_returns = self.market.carry_returns[day_index]
            asset_returns = (1.0 + spot_returns) * (1.0 + carry_returns) - 1.0
            carry_component = (1.0 + spot_returns) * carry_returns
            total_carry_contribution += float(np.dot(current_weights, carry_component))
            daily_returns[offset] = max(
                -0.99,
                float(np.dot(current_weights, asset_returns)) - transaction_cost,
            )
            previous_weights = current_weights

        if np.any(previous_weights):
            liquidation_cost = float(
                np.dot(
                    np.abs(previous_weights),
                    (self.cost_bps_per_side + self.market.spread_bps[end - 1])
                    / 10_000.0,
                )
            )
            daily_returns[-1] = max(-0.99, daily_returns[-1] - liquidation_cost)
            total_turnover += float(np.sum(np.abs(previous_weights)))

        return _WindowResult(
            daily_returns=daily_returns,
            turnover=total_turnover,
            carry_contribution=total_carry_contribution,
            gross_exposure=gross_exposure,
            meaningful_active=meaningful_active,
            effective_position_count=effective_position_count,
            max_weight=max_weight,
        )

    def _summarize(
        self,
        windows: list[_WindowResult],
        as_of_index: int,
    ) -> BacktestResult:
        daily_returns = np.concatenate([window.daily_returns for window in windows])
        window_returns = np.vstack([window.daily_returns for window in windows])
        gross_exposure = np.concatenate([window.gross_exposure for window in windows])
        meaningful_active = np.concatenate(
            [window.meaningful_active for window in windows]
        )
        effective_position_counts = np.concatenate(
            [window.effective_position_count for window in windows]
        )
        max_weights = np.concatenate([window.max_weight for window in windows])
        reference_window_risk = self._reference_window_risk(as_of_index)
        daily_expected_shortfall = StrategyBacktester.expected_shortfall(daily_returns)
        reference_daily_risk = reference_window_risk / np.sqrt(self.window_days)
        average_gross_exposure = float(np.mean(gross_exposure))
        active_windows = sum(
            int(np.any(window.meaningful_active)) for window in windows
        )
        return BacktestResult(
            daily_returns=daily_returns,
            window_sharpes=np.asarray(
                [
                    StrategyBacktester.annualized_sharpe(window.daily_returns)
                    for window in windows
                ],
                dtype=float,
            ),
            window_tail_score=StrategyBacktester.lower_tail_window_score(
                window_returns,
                reference_window_risk,
            ),
            reference_window_risk=reference_window_risk,
            daily_expected_shortfall=daily_expected_shortfall,
            expected_shortfall_ratio=daily_expected_shortfall / reference_daily_risk,
            raw_sharpe=StrategyBacktester.annualized_sharpe(daily_returns),
            turnover=sum(window.turnover for window in windows),
            carry_contribution=sum(window.carry_contribution for window in windows),
            active_fraction=average_gross_exposure,
            exposure_weighted_active_fraction=average_gross_exposure,
            active_session_fraction=float(np.mean(meaningful_active)),
            average_gross_exposure=average_gross_exposure,
            median_gross_exposure=float(np.median(gross_exposure)),
            mean_active_gross_exposure=(
                float(np.mean(gross_exposure[meaningful_active]))
                if np.any(meaningful_active)
                else 0.0
            ),
            cash_fraction=float(np.mean(1.0 - np.clip(gross_exposure, 0.0, 1.0))),
            max_weight=float(np.max(max_weights, initial=0.0)),
            effective_position_count=(
                float(np.mean(effective_position_counts[meaningful_active]))
                if np.any(meaningful_active)
                else 0.0
            ),
            realized_volatility=(
                float(np.std(daily_returns, ddof=1) * np.sqrt(252.0))
                if len(daily_returns) > 1
                else 0.0
            ),
            active_windows=active_windows,
        )

    def _reference_window_risk(self, as_of_index: int) -> float:
        horizon_end = as_of_index + self.windows * self.window_days
        asset_returns = self.market.returns[as_of_index:horizon_end]
        asset_volatility = np.std(asset_returns, axis=0, ddof=1)
        finite_positive = asset_volatility[
            np.isfinite(asset_volatility) & (asset_volatility > 1e-12)
        ]
        if not len(finite_positive):
            return 1e-6
        return max(
            1e-6,
            float(np.median(finite_positive) * np.sqrt(self.window_days)),
        )
