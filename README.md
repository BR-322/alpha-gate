# Alpha-Gate

Can an AI coding agent discover trading strategies that hold up under rigorous
evaluation, or will it learn to game the evaluator?

Alpha-Gate connects an evolver to a controlled evaluation pipeline. The
evolver proposes small Python strategies. Alpha-Gate runs each strategy in an
isolated sandbox, streams historical market observations one session at a time,
and evaluates the resulting portfolio decisions with controls for repeated
testing, tail risk, trading activity, and costs.

The goal is not to build a trading product. It is to test whether open-ended
code generation can produce credible strategies when the evaluation process is
designed to reveal overfitting and common forms of reward hacking.

Alpha-Gate is experimental research software. It is not investment advice or a
trading system.

## How the pieces fit

Alpha-Gate separates strategy generation, candidate execution, and evaluation.
That separation lets the project compare different evolvers without giving
generated code access to the scorer or future market data.

### 1. Generate candidate strategies

An `Evolver` proposes Python source code containing a `Strategy` class.

- `LocalEvolver` is the reproducible local control. It changes numeric literals
  inside marked `EVOLVE-BLOCK` regions of a seed program.
- The planned AlphaEvolve adapter will connect the same interface to
  [AlphaEvolve](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/overview),
  Google's evolutionary coding agent. That integration is not yet implemented.

The local control is simple by design. It establishes a known baseline for the
experiment loop; it is not intended to match AlphaEvolve's ability to rewrite
code.

### 2. Execute each candidate in isolation

A candidate receives one market bar at a time and returns target portfolio
weights. A bar contains the current date, prices, one-day spot and carry
returns, spread estimates, and public reference-rate features.

The portfolio returned after session `d - 1` is applied to session `d`. The
executor waits for that response before sending the next bar. Each scoring
window starts a new sandbox and `Strategy` instance, preceded by a trailing
warm-up period.

Docker and Podman implement the local reference path. A Cloud Run broker
implements the same `SandboxExecutor` contract with an ephemeral nested
sandbox.

### 3. Evaluate the resulting portfolio behavior

Trusted code outside the candidate process validates portfolio weights, applies
spot and carry returns, charges transaction and end-of-window liquidation
costs, and constructs the return series. Alpha-Gate then passes the complete
candidate group to Gate Runner's `HonestScorer` through a narrow adapter.

[Gate Runner](https://github.com/BR-322/gate-runner) remains the source of the
group-scoring implementation. Alpha-Gate owns candidate execution, execution
lag, cost application, and return construction.

## What the experiment measures

Alpha-Gate is designed around three questions:

1. Can AlphaEvolve find stronger strategies than the local control when both
   receive the same candidate-evaluation budget?
2. Can generated programs exploit the evaluator instead of improving the
   underlying strategy?
3. Which execution, scoring, and candidate-accounting choices remain reliable
   when an evolver can rewrite Python?

Several mechanisms make those questions measurable:

- **Deflated Sharpe Ratio (DSR):** adjusts risk-adjusted performance for the
  number of strategies attempted. Invalid programs and sandbox failures remain
  in that trial count.
- **Sequential evaluation windows:** measure performance across multiple fixed
  periods with a strict one-session execution lag. Future observations are not
  sent to the candidate process. These windows are reused during a search, so
  they are not a final untouched holdout set.
- **Tail and activity checks:** lower-tail window performance, expected
  shortfall, activity, and exposure affect the pass decision and reward.
- **Trading frictions:** spot and carry returns are reduced by transaction,
  spread, and end-of-window liquidation costs before scoring.
- **Probability of Backtest Overfitting (PBO):** reported as a group diagnostic
  for selection risk. PBO is not currently a direct reward term.
- **Complexity diagnostics:** source bytes and Python AST nodes are recorded.
  They do not affect reward until a Python-specific complexity penalty can be
  calibrated against adversarial examples.

The experiment uses the same fixed evaluation windows throughout a run. A
separate outer holdout will be needed before making a stronger out-of-sample
claim about a discovered strategy.

## Current status

The local experiment path is implemented and baseline-tested:

- Python candidate validation and the lockstep bar/weight protocol;
- Docker- and Podman-compatible sandbox execution;
- trusted windowed backtesting and Gate Runner scoring;
- exact candidate budgets, cumulative trial accounting, and an append-only
  audit ledger; and
- a deterministic 32-candidate local baseline.

The private Cloud Run broker has passed the same adversarial executor contract
as the local backend. The suite covers a valid candidate, candidate runtime
failure, invalid weights, timeout cleanup, and output flooding. Cloud Run
sandboxes remain a Preview feature, so the local container executor is still
the public reference.

The next milestone is the AlphaEvolve adapter and a small, budget-matched smoke
experiment. No AlphaEvolve strategy-generation experiment has been run yet.

Evidence:

- [Local baseline v0.2](reports/local_baseline_v0_2.md)
- [Cloud Run parity v0.1](reports/cloud_run_parity_v0_1.md)

## Security model

Generated Python is untrusted. Static source validation rejects known unsafe or
nondeterministic APIs, but validation is not an isolation boundary. Generated
candidates must run in a sandbox.

The candidate process receives its own source, the protocol worker, non-secret
limits, a deterministic seed, and one observation at a time. It does not
receive:

- future observations;
- scorer code or reward thresholds;
- the source repository;
- host environment variables or cloud credentials;
- the container runtime socket; or
- outbound network access.

The trusted evaluator remains outside the candidate process. It validates
weights, applies costs, constructs returns, and computes every score.

See [ADR 0001](docs/adr/0001-python-strategies-and-sandbox-executors.md) for
the full candidate protocol and threat model. Cloud-specific controls are
documented in
[ADR 0004](docs/adr/0004-cloud-run-nested-sandbox-broker.md).

## Getting started

Requirements:

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Docker or Podman for sandbox and container integration tests

Install the development environment and run the local checks:

```bash
uv sync --group dev
uv run ruff check .
uv run mypy src
uv run pytest
```

Show the pinned upstream commits and available container runtimes:

```bash
uv run alpha-gate preflight
```

Validate candidate source without executing it:

```bash
uv run alpha-gate validate path/to/strategy.py
```

Build and test the reference sandbox:

```bash
docker build -f containers/sandbox/Dockerfile -t alpha-gate-sandbox:dev .
uv run pytest -m container
```

Use `podman` in place of `docker` if that is your local runtime.

Run a four-candidate smoke experiment after building the image:

```bash
uv run alpha-gate run-local examples/seed_strategy.py \
  --experiment-id local-smoke-001 \
  --as-of-index 1000 \
  --generations 1 \
  --batch-size 4 \
  --evaluation-budget 4 \
  --ledger reports/runs/local-smoke-001.jsonl
```

The CLI checks the runtime and sandbox image before using the evaluation
budget. It also rejects an experiment ID already present in the ledger. Each
JSONL record contains the candidate source, lineage, score snapshot, error, and
per-window sandbox accounting. A sibling `*.summary.json` file records the
final cumulative ranking and the configuration needed to reproduce the run.

Cloud dependencies are optional:

```bash
uv sync --extra cloud --group dev
```

Provisioning and parity-test commands are in the
[Cloud Run broker guide](docs/CLOUD_RUN_BROKER.md).

## Design records

- [ADR 0001: Python strategies and pluggable sandbox executors](docs/adr/0001-python-strategies-and-sandbox-executors.md)
- [ADR 0002: Windowed program backtests and scoring](docs/adr/0002-windowed-program-backtests-and-scoring.md)
- [ADR 0003: Cumulative local evolution and append-only ledger](docs/adr/0003-cumulative-local-evolution-and-ledger.md)
- [ADR 0004: Cloud Run nested-sandbox broker](docs/adr/0004-cloud-run-nested-sandbox-broker.md)

## Research background

- Bailey and López de Prado,
  [*The Deflated Sharpe Ratio*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Bailey, Borwein, López de Prado, and Zhu,
  [*The Probability of Backtest Overfitting*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- Novikov et al.,
  [*AlphaEvolve: A coding agent for scientific and algorithmic discovery*](https://arxiv.org/abs/2506.13131)

## Pinned upstreams

- Gate Runner: `274870d57a235355e12338cc3e18d1bd5d682788`
- AlphaEvolve client: `8693985fa0eebf1a3b8fe2a64b7594e74ddb6557`

Alpha-Gate, Gate Runner, and the AlphaEvolve client use the Apache License 2.0.
