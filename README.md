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
pluggable `SandboxExecutor`, and a trusted windowed `ProgramBacktester`. The
reference executor targets Docker or Podman. A Cloud Run code-execution-
sandbox backend will implement the same contract after the offline
adversarial suite is green.

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

The build context is intentionally limited by `.dockerignore` to the protocol
worker and Dockerfile. Never run generated candidates directly on the host;
the example seed is repository-authored test code, not an isolation boundary.

Cloud dependencies are optional:

```bash
uv sync --extra cloud --group dev
```

## Next milestone

The next vertical slice is the evolver-neutral experiment loop: a batch
`Evolver` contract, a deterministic local source-mutation baseline, and an
evaluation ledger that counts every proposed program exactly once while
recording its per-window sandbox cost. That local loop will produce the first
reproducible report before either the Cloud Run executor or AlphaEvolve adapter
is allowed to spend cloud budget.

## Pinned upstreams

- Gate Runner: `274870d57a235355e12338cc3e18d1bd5d682788`
- AlphaEvolve client: `8693985fa0eebf1a3b8fe2a64b7594e74ddb6557`

Both upstream projects and Alpha-Gate use the Apache License 2.0.
