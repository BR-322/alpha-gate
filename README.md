# Alpha-Gate

Alpha-Gate is an open experiment in honesty-shaped evolutionary search. It
lets a local baseline evolver or Google Cloud AlphaEvolve propose Python
trading strategies, executes those strategies as untrusted programs, and
scores their behavior with Gate Runner-inspired walk-forward discipline.

The research questions are deliberately adversarial:

1. Can AlphaEvolve discover credible strategies at a matched evaluation
   budget?
2. Can it reward-hack a grader designed to punish backtest overfitting?
3. Which evaluator and candidate-management ideas survive contact with a
   hostile Python search space?

This repository is experimental research infrastructure, not investment
advice or a trading system.

## Current status

The local reference path now defines the Python candidate protocol, a
pluggable `SandboxExecutor`, a trusted windowed `ProgramBacktester`, and a
budget-bounded experiment loop. The deterministic `LocalEvolver` is the
cloud-free control; it mutates numeric literals inside AlphaEvolve-compatible
evolve blocks. The reference executor targets Docker or Podman.

AlphaEvolve generation runs in Google Cloud, but its controller and evaluator
are client-side. Alpha-Gate therefore keeps candidate execution and scoring
under this repository's control.

## Security boundary

Generated Python is untrusted. A strategy receives market observations one
bar at a time and returns portfolio weights. It never receives future bars,
the scorer, cloud credentials, host environment variables, or network access.
Static source validation is defense in depth; only the sandbox is a security
boundary.

The trusted evaluator remains outside the candidate process. It validates
weights, applies trading costs, constructs return streams, and computes the
honesty-shaped score.

See [ADR 0001](docs/adr/0001-python-strategies-and-sandbox-executors.md) for
the candidate decision and threat model, and [ADR 0002](docs/adr/0002-windowed-program-backtests-and-scoring.md)
for execution-lag, window-reset, cost, and scorer-adapter semantics.
See [ADR 0003](docs/adr/0003-cumulative-local-evolution-and-ledger.md) for
cumulative trial accounting, exact evaluation budgets, and the audit ledger.

The response produced after observing session `d - 1` is the target portfolio
for session `d`. Each scoring window receives a fresh strategy instance and a
trailing warm-up; the candidate never receives the final outcome row before
its portfolio is fixed. Invalid programs and sandbox failures remain part of
the group trial count. Python source size and AST nodes are reported but do
not affect reward.

## Development

Requirements:

- Python 3.12
- `uv`
- Docker or Podman only for container integration tests

```bash
uv sync --group dev
uv run ruff check .
uv run mypy src
uv run pytest
```

Validate a candidate without executing it:

```bash
uv run alpha-gate validate path/to/strategy.py
```

Build and exercise the reference sandbox after installing Docker or Podman:

```bash
docker build -f containers/sandbox/Dockerfile -t alpha-gate-sandbox:dev .
uv run pytest -m container
```

Run a four-candidate smoke experiment after the image is built:

```bash
uv run alpha-gate run-local examples/seed_strategy.py \
  --experiment-id local-smoke-001 \
  --as-of-index 1000 \
  --generations 1 \
  --batch-size 4 \
  --evaluation-budget 4 \
  --ledger reports/runs/local-smoke-001.jsonl
```

The CLI checks both the runtime and sandbox image before consuming the
evaluation budget. It refuses to reuse an experiment ID already present in the
ledger. Every JSONL row contains the complete candidate source, its score
snapshot, lineage, error, and per-window sandbox accounting.
After a successful run, a sibling `*.summary.json` file contains the complete
final cumulative ranking and reproduction configuration. Use `--summary` to
choose a different path; existing summaries are never overwritten.

The build context is intentionally limited by `.dockerignore` to the protocol
worker and Dockerfile. Never run generated candidates directly on the host;
the example seed is repository-authored test code, not an isolation boundary.

Cloud dependencies are optional:

```bash
uv sync --extra cloud --group dev
```

## Next milestone

The frozen 32-candidate local baseline is documented in
[`reports/local_baseline_v0_2.md`](reports/local_baseline_v0_2.md), with its
complete final ranking in
[`reports/local_baseline_v0_2.json`](reports/local_baseline_v0_2.json). Next,
implement the Cloud Run executor behind the existing `SandboxExecutor`
contract and make it pass the same adversarial suite. The AlphaEvolve adapter
comes after that parity check and starts with a separately approved, tiny
evaluation budget matched to the local baseline.

## Pinned upstreams

- Gate Runner: `274870d57a235355e12338cc3e18d1bd5d682788`
- AlphaEvolve client: `8693985fa0eebf1a3b8fe2a64b7594e74ddb6557`

Both upstream projects and Alpha-Gate use the Apache License 2.0.
