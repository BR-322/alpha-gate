"""Minimal JSONL worker copied into the sandbox image.

This module intentionally contains no market data, scoring logic, credentials,
or cloud client. Candidate code can inspect it without learning the evaluator.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

PROTOCOL_VERSION = "alpha-gate.strategy.v1"


class StrategyInstance(Protocol):
    def on_bar(self, bar: dict[str, object]) -> object: ...


def _emit(payload: dict[str, object]) -> None:
    protocol_output = sys.__stdout__
    if protocol_output is None:
        raise RuntimeError("protocol output is unavailable")
    protocol_output.write(json.dumps(payload, separators=(",", ":")) + "\n")
    protocol_output.flush()


def _load_module(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location("candidate_strategy", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("candidate module could not be loaded")
    module = importlib.util.module_from_spec(specification)
    with contextlib.redirect_stdout(sys.stderr):
        specification.loader.exec_module(module)
    return module


def _construct_strategy(
    module: ModuleType,
    symbols: tuple[str, ...],
    seed: int,
) -> StrategyInstance:
    strategy_type = getattr(module, "Strategy", None)
    if not isinstance(strategy_type, type):
        raise TypeError("candidate must define a Strategy class")
    with contextlib.redirect_stdout(sys.stderr):
        instance = strategy_type(symbols=symbols, seed=seed)
    if not callable(getattr(instance, "on_bar", None)):
        raise TypeError("Strategy must define callable on_bar")
    return cast(StrategyInstance, instance)


def _coerce_weights(value: object) -> list[float]:
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    if not isinstance(value, list | tuple):
        raise TypeError("on_bar must return a list, tuple, or one-dimensional array")
    weights: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise TypeError("weights must be real numbers")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("weights must be finite")
        weights.append(number)
    return weights


def _message_error(exc: BaseException) -> str:
    detail = str(exc).replace("\n", " ")[:400]
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def run(candidate_path: Path) -> int:
    try:
        module = _load_module(candidate_path)
    except BaseException as exc:
        _emit({"type": "error", "sequence": None, "message": _message_error(exc)})
        return 1

    strategy: StrategyInstance | None = None
    for line in sys.stdin:
        sequence: int | None = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("request frame must be an object")
            frame_type = payload.get("type")
            if frame_type == "initialize":
                if payload.get("protocol_version") != PROTOCOL_VERSION:
                    raise ValueError("unsupported protocol version")
                raw_symbols = payload.get("symbols")
                seed = payload.get("seed")
                if not isinstance(raw_symbols, list) or not all(
                    isinstance(symbol, str) for symbol in raw_symbols
                ):
                    raise TypeError("symbols must be a string list")
                if isinstance(seed, bool) or not isinstance(seed, int):
                    raise TypeError("seed must be an integer")
                strategy = _construct_strategy(module, tuple(raw_symbols), seed)
                _emit({"type": "ready", "protocol_version": PROTOCOL_VERSION})
                continue
            if frame_type == "bar":
                if strategy is None:
                    raise RuntimeError("initialize must precede bars")
                raw_sequence = payload.get("sequence")
                if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                    raise TypeError("bar sequence must be an integer")
                sequence = raw_sequence
                with contextlib.redirect_stdout(sys.stderr):
                    value = strategy.on_bar(cast(dict[str, object], payload))
                _emit(
                    {
                        "type": "weights",
                        "sequence": sequence,
                        "weights": _coerce_weights(value),
                    }
                )
                continue
            if frame_type == "stop":
                _emit({"type": "stopped"})
                return 0
            raise ValueError("unknown request frame type")
        except BaseException as exc:
            _emit(
                {
                    "type": "error",
                    "sequence": sequence,
                    "message": _message_error(exc),
                }
            )
            return 1
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: worker.py /candidate/strategy.py\n")
        return 2
    return run(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
