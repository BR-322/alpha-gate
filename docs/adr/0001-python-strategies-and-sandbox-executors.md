# ADR 0001: Python strategies and pluggable sandbox executors

- Status: Accepted
- Date: 2026-07-17

## Context

Gate Runner asks a solver to compose a bounded JSON strategy from curated
signals. Alpha-Gate instead lets AlphaEvolve write Python, including custom
signals and stateful logic. This permits a broader range of strategies, but it
also gives the evolver more ways to exploit weaknesses in the evaluation.

The official AlphaEvolve client separates managed generation from the
customer-supplied evaluator. Alpha-Gate can therefore keep candidate execution
and scoring in its own environment.

Gate Runner's current `StrategyBacktester` accepts a strict `StrategyConfig`.
It cannot execute arbitrary Python without changing its public contract.
Alpha-Gate will not disguise Python as JSON or fork Gate Runner silently.

## Decision

Alpha-Gate owns four separable interfaces:

1. An `Evolver` proposes candidate source code.
2. A `SandboxExecutor` runs a candidate through a lockstep observation
   protocol.
3. A trusted program backtester validates weights and constructs net return
   streams.
4. An honesty scorer evaluates a complete candidate group, preserving trial
   counts and cross-candidate diagnostics.

The local executor is Docker/Podman-compatible. The Cloud Run backend uses the
same request and result models. GKE Autopilot with gVisor remains the fallback
if Cloud Run's preview feature becomes unsuitable.

## Candidate protocol

A candidate is a Python module containing a `Strategy` class:

```python
class Strategy:
    def __init__(self, symbols: tuple[str, ...], seed: int) -> None:
        ...

    def on_bar(self, bar: dict[str, object]) -> list[float]:
        ...
```

The trusted worker sends an initialization frame, then sends exactly one bar
and waits for exactly one weight response before revealing the next bar. The
bar contains the current date, prices, one-day spot and carry returns, spread
proxies, and public reference-rate features. Candidates maintain any trailing
history they need in their own process.

The returned vector is a target portfolio for the next session. Trusted code
enforces finite values, symbol count, per-position bounds, and a gross-exposure
cap before using it. Signed weights are allowed so the Python search space is
not artificially restricted to Gate Runner's current long-only grammar.

The lockstep protocol prevents look-ahead access. Mounting a complete
evaluation panel inside the candidate container would expose future rows even
if the backtester applied the correct execution lag.

## Trust boundary

Candidate sandboxes receive:

- candidate source;
- the fixed runtime and protocol worker;
- one observation at a time;
- non-secret limits and a deterministic seed.

They do not receive:

- future observations;
- scoring code or reward thresholds;
- sealed evaluation outputs;
- the source repository;
- host or cloud credentials;
- a service-account token or metadata-server identity;
- the container runtime socket;
- outbound network access.

Static AST checks reject common nondeterministic and host-interaction APIs, but
they are not a sandbox. Container or Cloud Run isolation remains mandatory for
untrusted candidates.

## Resource and protocol limits

Every executor must enforce equivalent bounds for wall time, CPU, memory,
process count, writable temporary storage, response size, and total frames.
Candidate stdout and stderr are untrusted and capped. Failures produce typed
results rather than a caller exception, so malformed programs remain part of
the evolutionary trial count.

## Scoring consequences

Gate Runner's DSR, behavioral-diversity, CSCV/PBO, lower-tail, expected-
shortfall, cost, and activity concepts remain the starting point. Its JSON
parameter-count complexity measure does not transfer to Python. Alpha-Gate
will report source bytes and AST nodes first, then calibrate any reward-bearing
complexity penalty against adversarial examples before enabling it.

No candidate-supplied metric is trusted. Candidates return weights only; the
trusted evaluator computes every return and score.

## Reproducibility

The local executor is the public reference backend and must run without Google
Cloud. The Cloud Run backend must pass the same protocol and adversarial test
suite. Bake-off reports record the executor, image digest, upstream commits,
candidate group size, seeds, evaluation counts, and all truncation limits.
