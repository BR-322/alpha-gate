from __future__ import annotations

import runpy
from pathlib import Path
from typing import Protocol, cast

SEED_STRATEGY = Path(__file__).parents[1] / "examples" / "seed_strategy.py"


class SeedStrategy(Protocol):
    def on_bar(self, bar: dict[str, object]) -> list[float]: ...


def _load_strategy() -> type[SeedStrategy]:
    namespace = runpy.run_path(str(SEED_STRATEGY))
    return cast(type[SeedStrategy], namespace["Strategy"])


def test_seed_strategy_is_deterministic_and_bounded() -> None:
    symbols = ("A", "B", "C", "D")
    first = _load_strategy()(symbols=symbols, seed=23)
    second = _load_strategy()(symbols=symbols, seed=23)

    first_weights: list[float] = []
    second_weights: list[float] = []
    for sequence in range(20):
        close = [
            1.0 + sequence * 0.04,
            1.0 + sequence * 0.02,
            1.0 - sequence * 0.01,
            1.0 - sequence * 0.02,
        ]
        bar: dict[str, object] = {"sequence": sequence, "close": close}
        first_weights = first.on_bar(bar)
        second_weights = second.on_bar(bar)

    assert first_weights == second_weights == [0.25, 0.25, -0.25, -0.25]
    assert max(abs(weight) for weight in first_weights) <= 0.25
    assert sum(abs(weight) for weight in first_weights) <= 1.0
