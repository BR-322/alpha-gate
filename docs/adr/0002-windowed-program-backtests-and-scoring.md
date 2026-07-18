# ADR 0002: Windowed program backtests and authoritative scoring

- Status: Accepted
- Date: 2026-07-17

## Context

Python candidates return target weights rather than Gate Runner's
`StrategyConfig`. Gate Runner's backtester cannot consume those weights, but
its `HonestScorer` contains the benchmark's authoritative DSR, behavioral
diversity, CSCV/PBO, tail-risk, activity-gate, and reward implementation.

A program also needs historical observations to compose trailing signals. If
the complete evaluation panel were mounted or sent at once, future rows would
become a trivial look-ahead channel.

## Decision

Alpha-Gate owns a `ProgramBacktester` and reuses Gate Runner's grouped scorer
without reimplementing its statistical math.

Each scoring window is evaluated as follows:

1. Launch a fresh sandbox and construct a fresh `Strategy` instance.
2. Stream a trailing warm-up, one bar at a time. Warm-up portfolios are
   validated but not scored.
3. Apply the portfolio returned after session `d - 1` to session `d`.
4. Charge absolute target-weight changes at the fixed per-side cost plus the
   spread proxy known at `d - 1`.
5. Compound spot and carry returns in trusted code.
6. Force liquidation at the end of the window and charge absolute liquidation
   cost, including for short positions.

The final outcome row is never disclosed before its portfolio is fixed. Each
window resets state and uses a deterministic derived seed, matching Gate
Runner's independent-window discipline while still allowing stateful signals
inside a window.

## Scoring adapter

Completed program backtests are passed to Gate Runner's `HonestScorer` through
a narrow precomputed-result adapter. Invalid source, sandbox failures, and
executor contract violations become `None` trials, so they remain in the
reported and DSR-deflating trial count.

The adapter assigns Python programs a reward-bearing complexity of zero. Source
bytes and AST nodes remain explicit diagnostics in `ProgramScore`, but neither
affects reward until a separate adversarial calibration justifies a Python
complexity penalty. This avoids silently applying Gate Runner's JSON parameter
count to an unrelated representation.

## Consequences

- Gate Runner stays authoritative for group-scoring math.
- Alpha-Gate is authoritative for execution lag, program costs, and return
  construction.
- Duplicate proposals are scored as separate observed trials rather than
  collapsed by Gate Runner's deterministic-config cache.
- A full candidate evaluation launches one sandbox per scoring window. This is
  intentionally conservative and will inform later Cloud Run cost estimates.
