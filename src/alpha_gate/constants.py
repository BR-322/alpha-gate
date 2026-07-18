"""Reproducibility pins shared by reports and preflight checks."""

from typing import Final, Literal

StrategyProtocolVersion = Literal["alpha-gate.strategy.v1"]

GATE_RUNNER_COMMIT: Final = "274870d57a235355e12338cc3e18d1bd5d682788"
ALPHA_EVOLVE_COMMIT: Final = "8693985fa0eebf1a3b8fe2a64b7594e74ddb6557"
STRATEGY_PROTOCOL_VERSION: Final[StrategyProtocolVersion] = "alpha-gate.strategy.v1"
