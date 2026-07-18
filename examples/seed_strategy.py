"""Deterministic momentum seed for the first Alpha-Gate experiments."""


# EVOLVE-BLOCK-START
class Strategy:
    """Rank trailing returns and hold a small market-neutral portfolio."""

    def __init__(self, symbols: tuple[str, ...], seed: int) -> None:
        self.symbols = symbols
        self.seed = seed
        self.lookback = 20
        self.closes = [[] for _ in symbols]

    def on_bar(self, bar: dict[str, object]) -> list[float]:
        current = list(bar["close"])
        for history, value in zip(self.closes, current, strict=True):
            history.append(float(value))
            if len(history) > self.lookback:
                del history[0]

        if any(len(history) < self.lookback for history in self.closes):
            return [0.0 for _ in self.symbols]

        scores = [history[-1] / history[0] - 1.0 for history in self.closes]
        ranked = sorted(
            range(len(self.symbols)),
            key=lambda index: (scores[index], self.symbols[index]),
        )
        per_side = min(2, len(self.symbols) // 2)
        weights = [0.0 for _ in self.symbols]
        for index in ranked[:per_side]:
            weights[index] = -0.25
        for index in ranked[-per_side:]:
            weights[index] = 0.25
        return weights


# EVOLVE-BLOCK-END
