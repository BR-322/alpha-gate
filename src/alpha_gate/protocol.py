"""Lockstep messages exchanged with an untrusted strategy process."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from alpha_gate.constants import STRATEGY_PROTOCOL_VERSION, StrategyProtocolVersion


class StrictModel(BaseModel):
    """Reject coercion and undeclared protocol fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InitializeFrame(StrictModel):
    type: Literal["initialize"] = "initialize"
    protocol_version: StrategyProtocolVersion = STRATEGY_PROTOCOL_VERSION
    symbols: tuple[str, ...] = Field(min_length=2, max_length=256)
    seed: int
    max_gross: float = Field(default=1.0, gt=0.0, le=2.0)
    max_abs_weight: float = Field(default=0.25, gt=0.0, le=1.0)
    allow_short: bool = True

    @model_validator(mode="after")
    def unique_symbols(self) -> InitializeFrame:
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        if any(not symbol or len(symbol) > 64 for symbol in self.symbols):
            raise ValueError("symbols must contain 1 to 64 characters")
        return self


class BarFrame(StrictModel):
    type: Literal["bar"] = "bar"
    sequence: int = Field(ge=0)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    close: tuple[float, ...]
    returns_1d: tuple[float, ...]
    carry_returns_1d: tuple[float, ...]
    spread_bps: tuple[float, ...]
    foreign_rate_percent: tuple[float, ...]
    base_rate_percent: tuple[float, ...]

    @model_validator(mode="after")
    def finite_rectangular_vectors(self) -> BarFrame:
        vectors = (
            self.close,
            self.returns_1d,
            self.carry_returns_1d,
            self.spread_bps,
            self.foreign_rate_percent,
            self.base_rate_percent,
        )
        lengths = {len(vector) for vector in vectors}
        if len(lengths) != 1 or not lengths or next(iter(lengths)) < 2:
            raise ValueError("bar vectors must have one shared length of at least two")
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise ValueError("bar vectors must contain only finite values")
        if any(value <= 0.0 for value in self.close):
            raise ValueError("close prices must be positive")
        if any(value < 0.0 for value in self.spread_bps):
            raise ValueError("spread_bps must be non-negative")
        return self


class StopFrame(StrictModel):
    type: Literal["stop"] = "stop"


RequestFrame = Annotated[
    InitializeFrame | BarFrame | StopFrame,
    Field(discriminator="type"),
]
REQUEST_FRAME_ADAPTER: TypeAdapter[RequestFrame] = TypeAdapter(RequestFrame)


class ReadyResponse(StrictModel):
    type: Literal["ready"] = "ready"
    protocol_version: StrategyProtocolVersion = STRATEGY_PROTOCOL_VERSION


class WeightsResponse(StrictModel):
    type: Literal["weights"] = "weights"
    sequence: int = Field(ge=0)
    weights: tuple[float, ...]

    @model_validator(mode="after")
    def finite_weights(self) -> WeightsResponse:
        if any(not math.isfinite(value) for value in self.weights):
            raise ValueError("weights must contain only finite values")
        return self


def validate_target_weights(
    weights: tuple[float, ...],
    initialization: InitializeFrame,
) -> tuple[float, ...]:
    """Validate an executor portfolio before trusted scoring consumes it."""

    if len(weights) != len(initialization.symbols):
        raise ValueError("weight count does not match symbol count")
    if any(not math.isfinite(weight) for weight in weights):
        raise ValueError("weights must contain only finite values")
    tolerance = 1e-12
    if not initialization.allow_short and any(
        weight < -tolerance for weight in weights
    ):
        raise ValueError("negative weights are disabled")
    if any(
        abs(weight) > initialization.max_abs_weight + tolerance for weight in weights
    ):
        raise ValueError("a position exceeds max_abs_weight")
    gross = math.fsum(abs(weight) for weight in weights)
    if gross > initialization.max_gross + tolerance:
        raise ValueError("portfolio gross exposure exceeds max_gross")
    return weights


class CandidateErrorResponse(StrictModel):
    type: Literal["error"] = "error"
    sequence: int | None = Field(default=None, ge=0)
    message: str = Field(min_length=1, max_length=512)


class StoppedResponse(StrictModel):
    type: Literal["stopped"] = "stopped"


ResponseFrame = Annotated[
    ReadyResponse | WeightsResponse | CandidateErrorResponse | StoppedResponse,
    Field(discriminator="type"),
]
RESPONSE_FRAME_ADAPTER: TypeAdapter[ResponseFrame] = TypeAdapter(ResponseFrame)
