from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import ExecutionLimits, SandboxRequest
from alpha_gate.executors.process import ProtocolViolation, validate_weights
from alpha_gate.protocol import BarFrame, InitializeFrame, WeightsResponse

from .conftest import SAFE_SOURCE, make_bar


def test_bar_vectors_must_be_rectangular_and_finite() -> None:
    values = make_bar().model_dump()
    values["returns_1d"] = (0.0,)

    with pytest.raises(ValidationError, match="shared length"):
        BarFrame.model_validate(values)

    values = make_bar().model_dump()
    values["close"] = (1.0, float("nan"))
    with pytest.raises(ValidationError, match="only finite values"):
        BarFrame.model_validate(values)


def test_initialization_rejects_duplicate_symbols() -> None:
    with pytest.raises(ValidationError, match="symbols must be unique"):
        InitializeFrame(symbols=("EURUSD", "EURUSD"), seed=1)


def test_request_requires_contiguous_sequences(
    initialization: InitializeFrame,
) -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        SandboxRequest(
            program=CandidateProgram(source=SAFE_SOURCE),
            initialization=initialization,
            bars=(make_bar(sequence=1),),
        )


def test_request_requires_symbol_width(initialization: InitializeFrame) -> None:
    with pytest.raises(ValidationError, match=r"match initialization\.symbols"):
        SandboxRequest(
            program=CandidateProgram(source=SAFE_SOURCE),
            initialization=initialization,
            bars=(make_bar(width=3),),
        )


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        ((0.1,), "weight count"),
        ((0.3, 0.0), "max_abs_weight"),
        ((0.2, 0.2, 0.2, 0.2, 0.2, 0.2), "weight count"),
    ],
)
def test_weight_validation_rejects_invalid_portfolios(
    initialization: InitializeFrame,
    weights: tuple[float, ...],
    message: str,
) -> None:
    response = WeightsResponse(sequence=0, weights=weights)
    with pytest.raises(ProtocolViolation, match=message):
        validate_weights(response, initialization)


def test_weight_validation_rejects_excess_gross() -> None:
    initialization = InitializeFrame(
        symbols=("A", "B", "C", "D"),
        seed=1,
        max_gross=0.5,
        max_abs_weight=0.25,
    )
    response = WeightsResponse(sequence=0, weights=(0.2, -0.2, 0.2, -0.2))

    with pytest.raises(ProtocolViolation, match="gross exposure"):
        validate_weights(response, initialization)


def test_weight_validation_can_disable_shorts() -> None:
    initialization = InitializeFrame(
        symbols=("A", "B"),
        seed=1,
        allow_short=False,
    )
    response = WeightsResponse(sequence=0, weights=(-0.1, 0.1))

    with pytest.raises(ProtocolViolation, match="negative weights"):
        validate_weights(response, initialization)


def test_limits_bound_frames(initialization: InitializeFrame) -> None:
    with pytest.raises(ValidationError, match=r"exceeds limits\.max_frames"):
        SandboxRequest(
            program=CandidateProgram(source=SAFE_SOURCE),
            initialization=initialization,
            bars=(make_bar(0), make_bar(1)),
            limits=ExecutionLimits(max_frames=1),
        )
