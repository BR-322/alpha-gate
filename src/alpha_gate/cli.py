"""Small local CLI that never launches a cloud experiment implicitly."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from alpha_gate.candidate import (
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.constants import ALPHA_EVOLVE_COMMIT, GATE_RUNNER_COMMIT


def _validate(path: Path) -> int:
    try:
        program = CandidateProgram(source=path.read_text(encoding="utf-8"))
        metadata = CandidateValidator.validate(program)
    except (OSError, CandidateSourceError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"valid": True, **metadata.model_dump(mode="json")}, indent=2))
    return 0


def _preflight() -> int:
    payload = {
        "gate_runner_commit": GATE_RUNNER_COMMIT,
        "alpha_evolve_commit": ALPHA_EVOLVE_COMMIT,
        "container_runtimes": {
            runtime: shutil.which(runtime) for runtime in ("docker", "podman")
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha-gate")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="statically validate a candidate")
    validate.add_argument("path", type=Path)
    commands.add_parser("preflight", help="show local runtime and upstream pins")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "validate":
        return _validate(arguments.path)
    if arguments.command == "preflight":
        return _preflight()
    raise AssertionError(f"unexpected command: {arguments.command}")
