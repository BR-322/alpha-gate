"""Small local CLI that never launches a cloud experiment implicitly."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from gate_runner_core.market import MarketData

from alpha_gate.backtest import ProgramBacktester
from alpha_gate.candidate import (
    CandidateProgram,
    CandidateSourceError,
    CandidateValidator,
)
from alpha_gate.constants import ALPHA_EVOLVE_COMMIT, GATE_RUNNER_COMMIT
from alpha_gate.evaluator import ProgramGroupEvaluator
from alpha_gate.evolution import LocalEvolver
from alpha_gate.executors.container import ContainerExecutor, ContainerExecutorConfig
from alpha_gate.experiment import ExperimentConfig, ExperimentRunner
from alpha_gate.ledger import JsonlLedger
from alpha_gate.summary import EvaluationProtocol, ExperimentSummary


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


def _load_market(dataset: str, seed: int) -> MarketData:
    if dataset == "synthetic":
        return MarketData.synthetic(seed=seed)
    if dataset == "ecb_fx":
        return MarketData.ecb_fx()
    if dataset == "ecb_fx_carry":
        return MarketData.ecb_fx(include_carry=True)
    raise ValueError(f"unsupported dataset: {dataset}")


def _summary_path(ledger: Path, requested: Path | None) -> Path:
    return requested or ledger.with_suffix(".summary.json")


async def _run_local_async(arguments: argparse.Namespace) -> int:
    try:
        summary_path = _summary_path(arguments.ledger, arguments.summary)
        if summary_path.resolve() == arguments.ledger.resolve():
            raise ValueError("summary and ledger paths must be different")
        if summary_path.exists():
            raise ValueError("summary path already exists; choose a new output path")
        program = CandidateProgram(
            source=arguments.seed_program.read_text(encoding="utf-8")
        )
        CandidateValidator.validate(program)
        executor = ContainerExecutor(
            ContainerExecutorConfig(
                runtime=arguments.runtime,
                runtime_path=arguments.runtime_path,
                image=arguments.image,
            )
        )
        runtime_path = executor.runtime_path()
        if runtime_path is None:
            raise ValueError(f"{arguments.runtime} executable was not found")
        image_check = subprocess.run(
            [
                runtime_path,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                arguments.image,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if image_check.returncode != 0:
            raise ValueError(
                f"sandbox image {arguments.image!r} is not available in "
                f"{arguments.runtime}"
            )
        image_id = image_check.stdout.strip()
        if not image_id:
            raise ValueError("sandbox image inspection returned an empty image ID")
        ledger = JsonlLedger(arguments.ledger)
        if ledger.has_experiment(arguments.experiment_id):
            raise ValueError(
                "ledger already contains experiment_id; choose a new experiment id"
            )
        config = ExperimentConfig(
            experiment_id=arguments.experiment_id,
            as_of_index=arguments.as_of_index,
            seed=arguments.seed,
            generations=arguments.generations,
            batch_size=arguments.batch_size,
            evaluation_budget=arguments.evaluation_budget,
            elite_count=arguments.elite_count,
        )
        market = _load_market(arguments.dataset, arguments.seed)
        backtester = ProgramBacktester(
            market=market,
            executor=executor,
            windows=arguments.windows,
            window_days=arguments.window_days,
            warmup_days=arguments.warmup_days,
            cost_bps_per_side=arguments.cost_bps_per_side,
            seed=arguments.seed,
        )
        result = await ExperimentRunner(
            config=config,
            evolver=LocalEvolver(),
            evaluator=ProgramGroupEvaluator(backtester),
            ledger=ledger,
        ).run((program,))
        summary = ExperimentSummary.from_result(
            result,
            EvaluationProtocol(
                dataset=arguments.dataset,
                source_label=market.source_label,
                rate_source_label=market.rate_source_label,
                symbols=market.symbols,
                first_scored_date=market.dates[arguments.as_of_index],
                last_scored_date=market.dates[
                    arguments.as_of_index
                    + arguments.windows * arguments.window_days
                    - 1
                ],
                windows=arguments.windows,
                window_days=arguments.window_days,
                warmup_days=arguments.warmup_days,
                cost_bps_per_side=arguments.cost_bps_per_side,
                runtime=arguments.runtime,
                runtime_path=runtime_path,
                image=arguments.image,
                image_id=image_id,
            ),
        )
        summary.write_json(summary_path)
    except (
        OSError,
        CandidateSourceError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, indent=2))
        return 2

    best = result.ranked_outcomes[0]
    valid_candidates = sum(
        int(outcome.score.score.validity > 0.0) for outcome in result.outcomes
    )
    print(
        json.dumps(
            {
                "completed": True,
                "experiment_id": config.experiment_id,
                "evaluations_used": result.evaluations_used,
                "generations_completed": result.generations_completed,
                "stop_reason": result.stop_reason,
                "valid_candidates": valid_candidates,
                "ledger": str(arguments.ledger),
                "summary": str(summary_path),
                "best": {
                    "candidate_id": best.trial.candidate_id,
                    "program_sha256": best.evaluation.program_sha256,
                    "reward": best.score.reward,
                    "passed": bool(best.score.score.passed),
                    "error": best.evaluation.error,
                },
            },
            indent=2,
        )
    )
    return 0 if valid_candidates else 1


def _run_local(arguments: argparse.Namespace) -> int:
    return asyncio.run(_run_local_async(arguments))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha-gate")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="statically validate a candidate")
    validate.add_argument("path", type=Path)
    commands.add_parser("preflight", help="show local runtime and upstream pins")
    local = commands.add_parser(
        "run-local",
        help="run a bounded local mutation experiment in Docker or Podman",
    )
    local.add_argument("seed_program", type=Path)
    local.add_argument("--experiment-id", required=True)
    local.add_argument("--ledger", type=Path, default=Path("reports/runs/trials.jsonl"))
    local.add_argument(
        "--summary",
        type=Path,
        help="final cumulative ranking JSON (defaults beside the ledger)",
    )
    local.add_argument(
        "--dataset",
        choices=("synthetic", "ecb_fx", "ecb_fx_carry"),
        default="synthetic",
    )
    local.add_argument("--as-of-index", type=int, default=1_000)
    local.add_argument("--seed", type=int, default=17)
    local.add_argument("--generations", type=int, default=1)
    local.add_argument("--batch-size", type=int, default=4)
    local.add_argument("--evaluation-budget", type=int, default=4)
    local.add_argument("--elite-count", type=int, default=2)
    local.add_argument("--windows", type=int, default=4)
    local.add_argument("--window-days", type=int, default=20)
    local.add_argument("--warmup-days", type=int, default=253)
    local.add_argument("--cost-bps-per-side", type=float, default=10.0)
    local.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    local.add_argument("--runtime-path")
    local.add_argument("--image", default="alpha-gate-sandbox:dev")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "validate":
        return _validate(arguments.path)
    if arguments.command == "preflight":
        return _preflight()
    if arguments.command == "run-local":
        return _run_local(arguments)
    raise AssertionError(f"unexpected command: {arguments.command}")
