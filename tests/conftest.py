from __future__ import annotations

import pytest

from alpha_gate.candidate import CandidateProgram
from alpha_gate.executors.base import SandboxRequest
from alpha_gate.protocol import BarFrame, InitializeFrame

SAFE_SOURCE = """
class Strategy:
    def __init__(self, symbols, seed):
        self.count = len(symbols)
        self.seed = seed

    def on_bar(self, bar):
        return [0.0] * self.count
""".strip()


def make_bar(sequence: int = 0, *, width: int = 2) -> BarFrame:
    return BarFrame(
        sequence=sequence,
        date="2026-01-02",
        close=tuple(1.0 + index for index in range(width)),
        returns_1d=tuple(0.001 for _ in range(width)),
        carry_returns_1d=tuple(0.0001 for _ in range(width)),
        spread_bps=tuple(1.0 for _ in range(width)),
        foreign_rate_percent=tuple(2.0 for _ in range(width)),
        base_rate_percent=tuple(3.0 for _ in range(width)),
    )


@pytest.fixture
def initialization() -> InitializeFrame:
    return InitializeFrame(symbols=("EURUSD", "USDJPY"), seed=7)


@pytest.fixture
def sandbox_request(initialization: InitializeFrame) -> SandboxRequest:
    return SandboxRequest(
        program=CandidateProgram(source=SAFE_SOURCE),
        initialization=initialization,
        bars=(make_bar(),),
    )
