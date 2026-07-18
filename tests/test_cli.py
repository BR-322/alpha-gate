from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha_gate.cli import _run_local, _summary_path, build_parser
from alpha_gate.executors.container import ContainerExecutor

SEED_PATH = Path(__file__).parents[1] / "examples" / "seed_strategy.py"


def test_local_command_has_bounded_defaults(tmp_path: Path) -> None:
    ledger = tmp_path / "trials.jsonl"
    arguments = build_parser().parse_args(
        [
            "run-local",
            str(SEED_PATH),
            "--experiment-id",
            "smoke",
            "--ledger",
            str(ledger),
        ]
    )

    assert arguments.generations == 1
    assert arguments.batch_size == 4
    assert arguments.evaluation_budget == 4
    assert arguments.windows == 4
    assert arguments.runtime == "docker"
    assert arguments.summary is None
    assert _summary_path(ledger, arguments.summary) == tmp_path / "trials.summary.json"


def test_local_command_refuses_missing_runtime_before_ledger_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "trials.jsonl"
    arguments = build_parser().parse_args(
        [
            "run-local",
            str(SEED_PATH),
            "--experiment-id",
            "no-runtime",
            "--ledger",
            str(ledger),
        ]
    )
    monkeypatch.setattr(ContainerExecutor, "runtime_path", lambda _self: None)

    status = _run_local(arguments)

    assert status == 2
    assert json.loads(capsys.readouterr().out) == {
        "completed": False,
        "error": "docker executable was not found",
    }
    assert not ledger.exists()
    assert not _summary_path(ledger, arguments.summary).exists()
