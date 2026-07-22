# ADR 0003: Cumulative local evolution and an append-only ledger

- Status: Accepted
- Date: 2026-07-17

## Context

Alpha-Gate needs a cloud-free control evolver and one experiment loop shared by
that control and AlphaEvolve. A naive generation loop would score only the new
batch, resetting DSR's trial count every generation and understating the search
pressure applied to the grader.

The experiment must also distinguish one candidate evaluation from its
multiple sandbox-window launches. Otherwise a nominally matched candidate
budget could hide materially different execution cost.

## Decision

`Evolver.propose` is an asynchronous, finite batch contract. It receives the
generation, deterministic seed, seed programs, ranked parents, requested batch
size, and every previously observed source hash. It returns source proposals
and lineage metadata but never evaluates them.

`LocalEvolver` is the reference control. It deterministically mutates numeric
literals only inside the same `EVOLVE-BLOCK` markers consumed by AlphaEvolve.
This simple baseline measures improvement over reproducible local source
mutation. It is not equivalent to a coding model.

The runner backtests each proposal exactly once and retains its raw
`ProgramEvaluation`. After each batch it rescores every evaluation observed in
the run as one cumulative group. The DSR trial count includes every proposal,
including invalid programs. PBO and behavioral-diversity diagnostics are
recomputed from all valid results observed so far. The evaluation budget is an
exact proposal count; the final batch is truncated rather than allowed to
overshoot.

## Ledger

Exactly one `TrialRecord` is appended for every proposal. It includes:

- complete candidate source and SHA-256;
- generation, global evaluation index, lineage, and mutation description;
- the cumulative score snapshot when its batch completed;
- source diagnostics and candidate error;
- every sandbox-window status and duration plus aggregate output bytes.

The JSONL implementation fsyncs each record, refuses duplicate experiment/
evaluation-index pairs, validates existing records before appending, and never
truncates an existing file. Reusing an experiment ID through the CLI is
rejected before any sandbox work begins.

At successful completion, the CLI writes a separate, exclusive-create summary
JSON. It contains the complete experiment and evaluator configuration plus
every candidate source, lineage, executor accounting, and final cumulative
score in ranked order. The summary is separate because final-score update
events must not rewrite or blur the event-time snapshots in the append-only
ledger. Resume support is deferred; the ledger is an audit trail, not a
checkpoint format.

## Consequences

- Local and AlphaEvolve searches share the same budget and scoring semantics.
- Scores from early generations may differ from their final cumulative scores;
  the ledger explicitly labels them as evaluation-time snapshots and the final
  summary provides the directly comparable terminal scores.
- A candidate budget of `N` can require `N × windows` sandbox launches. The
  ledger makes that multiplier visible for Cloud Run cost estimation.
- Reproducible local reports require the pinned Python runtime, seed source,
  experiment configuration, market snapshot, and ledger.
