# Alpha-Gate local baseline v0.2

This is the frozen, fully rescored local-evolver reference for the first
AlphaEvolve preflight. It validates the experiment loop and establishes a
fixed candidate budget; it is not evidence of a tradable strategy or the final
multi-seed bake-off.

## Frozen protocol

- Alpha-Gate implementation commit: `04e3bdc`.
- Dataset: carry-aware public ECB FX panel, 28 pairs.
- Scored dates: 2020-09-22 through 2022-01-10.
- Search: deterministic `LocalEvolver`, seed 17, four generations, eight
  proposals per generation, exact budget of 32 candidate evaluations.
- Evaluation: eight sequential 42-session windows, 253-session warm-up, and
  10 bps per side in addition to observed spread.
- Isolation: Docker `ContainerExecutor`, fresh strategy instance per window,
  no network, read-only root, and fail-closed portfolio validation.
- Candidate budget: 32. Maximum sandbox-window budget: 256.

Reproduce the run after building `alpha-gate-sandbox:dev`:

```bash
uv run alpha-gate run-local examples/seed_strategy.py \
  --experiment-id local-baseline-ecb-carry-s17-b32-v2 \
  --dataset ecb_fx_carry \
  --as-of-index 3000 \
  --seed 17 \
  --generations 4 \
  --batch-size 8 \
  --evaluation-budget 32 \
  --elite-count 2 \
  --windows 8 \
  --window-days 42 \
  --warmup-days 253 \
  --cost-bps-per-side 10 \
  --runtime docker \
  --ledger reports/runs/local-baseline-ecb-carry-s17-b32-v2.jsonl
```

The generated terminal summary is tracked as
[`local_baseline_v0_2.json`](local_baseline_v0_2.json). It contains every
candidate source and lineage record in final 32-trial rank order. Its SHA-256
is `f328184f65b041e0a2077efd9ad2a6ee19442d90a1ef84d634c2c1499ed6011a`.
The ignored event ledger has SHA-256
`1db24fd81f1902e8670c39d75699486b7f628b8cd5ed2c365775e15fbee3ef12`.

## Result

The local search used all 32 evaluations and completed four generations. Of
the 32 source-unique programs, 21 completed a backtest and 11 failed closed in
window zero. Seven exceeded maximum gross exposure and four exceeded maximum
single-position weight. Fail-fast behavior reduced actual sandbox launches
from 256 to 179: 168 completed windows and 11 protocol errors. Aggregate
sandbox execution time was 57.62 seconds.

No candidate passed. The best final candidate was `b3092128db0c...` with
reward `0.100044`, raw Sharpe `0.804`, DSR `0.000114`, diagnostic DSR `0.0754`,
PBO `0.0429`, window-tail score `-0.540`, and expected-shortfall ratio `3.17`.
Its eight window Sharpes were:

```text
-0.211, -1.782, 0.546, 2.568, -1.608, -2.725, 1.538, 2.545
```

The reward remains essentially at Gate Runner's valid-program floor. Four
negative windows, the negative tail score, and the near-zero DSR make the
rejection unambiguous.

The seed finished with reward `0.1`, raw Sharpe `-3.116`, DSR `0.0`, tail score
`-3.700`, expected-shortfall ratio `4.26`, and turnover `189.5`. The winner cut
turnover to `18.0`, average gross exposure from `1.0` to `0.5`, and effective
positions from four to two.

## Winner inspection

The winning lineage made three source changes:

1. `del history[0]` became `del history[1]`, retaining the oldest warm-up price
   as a long-lived ranking anchor.
2. The score expression subtracted `0.8` instead of `1.0`. This is behaviorally
   neutral because the same constant is subtracted for every asset before
   ranking.
3. Positions per side fell from two to one, reducing gross exposure and
   turnover.

This is a crude long-horizon momentum variant, not an observed grader exploit.
A fresh strategy is created for every window, so the retained anchor cannot
cross a window boundary or observe hidden outcomes. The honesty gate correctly
rejects the uneven return stream.

## Reproducibility and audit findings

The v2 run exactly reproduced v1 for every non-timing field in all 32 event
records: proposal order, source, hash, lineage, score snapshot, error, and
executor status. The winner, reward, validity count, and window-launch count
were identical.

Nine candidates' terminal rewards differed from their event-time snapshots.
That is expected cumulative DSR behavior and confirms why the terminal summary
is necessary. Every candidate in the v2 summary has `final_trial_count = 32`;
the ranking is now directly comparable without rewriting the event ledger.

The 21 valid source programs collapsed to only 10 distinct behavioral
signatures in the v1 audit. Source novelty is therefore not a substitute for
behavioral diversity, and literal-only mutation remains deliberately modest.

## Decision and next gate

Accept v0.2 as the frozen one-seed local preflight at a 32-candidate budget. Do
not recalibrate the honesty grader from this run. The next engineering gate is
Cloud Run executor parity against the same protocol and adversarial tests.
After parity, the first tiny AlphaEvolve experiment should use this exact
candidate budget, seed, dataset, cutoff, and window protocol.

The eventual bake-off still needs multiple fixed seeds and cutoffs. That larger
matrix is deferred until the cloud executor and AlphaEvolve adapter both pass
the bounded preflight.
