# Alpha-Gate local baseline v0.1

This is the first bounded local-evolver preflight on public data. It is a
control-run artifact, not evidence of a tradable strategy and not yet the
multi-seed baseline for the AlphaEvolve bake-off.

## Protocol

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
  --experiment-id local-baseline-ecb-carry-s17-b32-v1 \
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
  --ledger reports/runs/local-baseline-ecb-carry-s17-b32-v1.jsonl
```

The ignored raw ledger has SHA-256
`a7119ace6b53ece0140246344f05f1cdbf9074a1e1786e206748ec88b70c4c33`.
The machine-readable configuration and results are in
[`local_baseline_v0_1.json`](local_baseline_v0_1.json).

## Result

The local search used all 32 evaluations and completed four generations. Of
the 32 source-unique programs, 21 completed a backtest and 11 failed closed in
window zero. Seven exceeded maximum gross exposure and four exceeded maximum
single-position weight. Fail-fast behavior reduced actual sandbox launches
from the maximum 256 to 179: 168 completed windows and 11 protocol errors.

No candidate passed the final honesty gate. The best final candidate was
`b3092128db0c...` with reward `0.100044`, raw Sharpe `0.804`, DSR `0.000114`,
diagnostic DSR `0.0754`, PBO `0.0429`, window-tail score `-0.540`, and
expected-shortfall ratio `3.17`. Its eight window Sharpes were:

```text
-0.211, -1.782, 0.546, 2.568, -1.608, -2.725, 1.538, 2.545
```

Four negative windows, a negative tail score, and weak DSR make the rejection
unambiguous. The reward sits essentially at Gate Runner's valid-program floor;
it is not a near-pass.

The seed was poor on this period: raw Sharpe `-3.116`, tail score `-3.700`,
expected-shortfall ratio `4.26`, and turnover `189.5`. Every seed window had a
negative Sharpe. The best mutation reduced turnover to `18.0`, average gross
exposure from `1.0` to `0.5`, and effective positions from four to two.

## Winner inspection

The winning lineage made three source changes:

1. `del history[0]` became `del history[1]`, retaining the oldest warm-up price
   as a long-lived ranking anchor.
2. The score expression subtracted `0.8` instead of `1.0`. This is behaviorally
   neutral because the same constant is subtracted for every asset before
   ranking.
3. Positions per side fell from two to one, reducing gross exposure and
   turnover.

This looks like a crude long-horizon momentum variant, not reward hacking. A
fresh strategy is created for every window, so its retained anchor cannot
cross a window boundary or observe hidden outcomes. The honesty gate correctly
rejects the uneven return stream.

## Search diagnostics

The 21 valid source programs collapsed to only 10 distinct behavioral
signatures using raw Sharpe, turnover, carry, volatility, activity, exposure,
and maximum weight. Duplicate group sizes were `6, 4, 3, 2` plus six singletons.
This is an expected weakness of literal-only mutation and a useful control for
AlphaEvolve: source novelty must not be mistaken for behavioral diversity.

Carry contribution was negative for the seed (`-0.0116`) and even more
negative for the winner (`-0.0436`). The improvement came from spot-ranking
behavior and lower trading, not from exploiting the carry proxy.

## Limitations and decision

This result is a preflight baseline because it uses one seed, one cutoff, and
an uncommitted working tree. The JSONL ledger also stores evaluation-time score
snapshots: generation-zero rows were scored at eight cumulative trials, while
generation-three rows were scored at 32. The CLI reports the final best after
rescoring all candidates, but it does not yet persist the complete final
32-trial ranking.

Before the budget-matched AlphaEvolve comparison:

1. Persist a final rescored summary for every candidate, separate from the
   append-only evaluation-event ledger.
2. Commit the local-loop implementation and rerun this exact configuration as
   the frozen reference.
3. Expand the final bake-off to fixed seeds and cutoffs, keeping candidate and
   sandbox-window budgets explicit.

The current run is sufficient to validate the local evolution path, sandbox
accounting, fail-closed constraints, and honesty-gate behavior. It is not
sufficient to recalibrate the grader or claim a comparative AlphaEvolve result.
