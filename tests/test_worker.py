from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from alpha_gate.constants import STRATEGY_PROTOCOL_VERSION

WORKER = Path(__file__).parents[1] / "src" / "alpha_gate" / "runtime" / "worker.py"


def _run_worker(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    candidate = tmp_path / "strategy.py"
    candidate.write_text(source, encoding="utf-8")
    requests = [
        {
            "type": "initialize",
            "protocol_version": STRATEGY_PROTOCOL_VERSION,
            "symbols": ["EURUSD", "USDJPY"],
            "seed": 17,
        },
        {
            "type": "bar",
            "sequence": 0,
            "date": "2026-01-02",
            "close": [1.1, 150.0],
            "returns_1d": [0.01, -0.01],
            "carry_returns_1d": [0.0, 0.0],
            "spread_bps": [1.0, 1.0],
            "foreign_rate_percent": [2.0, 1.0],
            "base_rate_percent": [3.0, 3.0],
        },
        {"type": "stop"},
    ]
    return subprocess.run(
        [sys.executable, "-I", str(WORKER), str(candidate)],
        input="".join(json.dumps(frame) + "\n" for frame in requests),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_worker_keeps_candidate_stdout_off_protocol(tmp_path: Path) -> None:
    source = """
print("module chatter")

class Strategy:
    def __init__(self, symbols, seed):
        print("constructor chatter")
        self.count = len(symbols)

    def on_bar(self, bar):
        print("bar chatter")
        return [0.1] * self.count
"""

    completed = _run_worker(tmp_path, source)

    assert completed.returncode == 0
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses == [
        {"type": "ready", "protocol_version": STRATEGY_PROTOCOL_VERSION},
        {"type": "weights", "sequence": 0, "weights": [0.1, 0.1]},
        {"type": "stopped"},
    ]
    assert completed.stderr.splitlines() == [
        "module chatter",
        "constructor chatter",
        "bar chatter",
    ]


def test_worker_converts_candidate_exception_to_error_frame(tmp_path: Path) -> None:
    source = """
class Strategy:
    def __init__(self, symbols, seed):
        pass

    def on_bar(self, bar):
        raise RuntimeError("nope\\nsecond line")
"""

    completed = _run_worker(tmp_path, source)

    assert completed.returncode == 1
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["type"] == "ready"
    assert responses[1] == {
        "type": "error",
        "sequence": 0,
        "message": "RuntimeError: nope second line",
    }


def test_worker_rejects_non_numeric_weights(tmp_path: Path) -> None:
    source = """
class Strategy:
    def __init__(self, symbols, seed):
        pass

    def on_bar(self, bar):
        return [True, 0.0]
"""

    completed = _run_worker(tmp_path, source)

    assert completed.returncode == 1
    response = json.loads(completed.stdout.splitlines()[1])
    assert response["type"] == "error"
    assert response["message"] == "TypeError: weights must be real numbers"
