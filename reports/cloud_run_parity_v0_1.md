# Cloud Run sandbox parity v0.1

- Date: 2026-07-18
- Outcome: Passed after adversarial iteration
- Project: `YOUR_GCP_PROJECT_ID`
- Region: `us-east1`
- Service: `alpha-gate-broker`
- Passing revision: `YOUR_CLOUD_RUN_REVISION`
- Private service URL: `https://YOUR_CLOUD_RUN_SERVICE_URL`
- Pushed OCI index digest:
  `sha256:d99cf7b0b275d38072d3998a38b0fd8d5d1167c97fa19473fd472f88f06b40ea`
- Resolved amd64 runtime manifest:
  `sha256:c9f94bcebed0ad1ae46b2332aa1a1d7f27c312de7c6cc412fd459e063a82ccba`

## Result

The Cloud Run broker implements the same typed `SandboxExecutor` contract as
the Docker/Podman reference without exposing the complete market panel to the
candidate. The trusted broker holds the request and streams observations one
at a time to an ephemeral nested sandbox.

The final revision passed three consecutive cloud-only parity runs and then
the complete repository suite:

- cloud-only run 1: 2 passed in 10.04 seconds;
- cloud-only run 2: 2 passed in 9.82 seconds;
- cloud-only run 3: 2 passed in 10.60 seconds;
- final full run: 69 passed in 17.44 seconds;
- Ruff: passed;
- MyPy strict mode: passed.

The shared adversarial contract verified all of these outcomes on both the
local container and Cloud Run backends:

1. reference candidate completes with the expected zero-weight frame;
2. candidate exception becomes `runtime_error`;
3. out-of-bounds weights become `protocol_error`;
4. an infinite loop becomes `timeout` and does not block the next request;
5. output flooding is capped and becomes `protocol_error`.

Both backends invoke the worker with Python user-site isolation, bytecode
disabled, and `PYTHONHASHSEED=0`; the local `-I` flag was replaced because it
silently ignores that deterministic hash-seed environment variable.

## Frozen deployment controls

- Cloud Run second-generation execution environment with sandbox launcher;
- one vCPU, 512 MiB memory, request concurrency one;
- service-level minimum zero and maximum one instance;
- 360-second broker request timeout;
- nested sandbox launcher omits egress, writable overlay, mounts, and secrets;
- dedicated runtime identity
  `YOUR_BROKER_SERVICE_ACCOUNT_EMAIL`;
- runtime identity has no project IAM roles and no user-managed keys;
- service IAM grants `roles/run.invoker` only to
  `user:YOUR_CLOUD_IDENTITY_EMAIL`;
- broker image omits scoring, backtest, evaluator, market-data, report, and
  repository-history modules;
- $50 monthly budget alert remains active at 50%, 75%, 90%, 100%, and 100%
  forecasted spend.

Revision v5 produced no warning-or-higher Cloud Run log entries during the
passing runs.

## Findings from failed revisions

The failed attempts were useful system tests and remain part of the record:

- v1 showed that terminating `sandbox do` before its cleanup completed could
  strand capacity after an abnormal candidate exit.
- v2 added graceful launcher shutdown and an explicit named-sandbox delete.
- v2 also exposed that `tempfile` names can contain underscores, while Cloud
  Run sandbox session IDs accept only alphanumerics and hyphens.
- v3 moved names to UUID hex, then exposed a timeout-only descriptor leak: a
  killed launcher could leave the sandbox holding stderr open, preventing the
  broker request from returning.
- v4 bounds stderr-drain shutdown, preserves partial output accounting, and
  always reaches force-delete cleanup. The authenticated client also retries
  only Cloud Run's pre-dispatch HTTP 429 response, under one overall transport
  deadline; it never retries a candidate request after broker dispatch.
- v5 pins the already-tested `python:3.12-slim` multi-architecture base image
  digest. The resolved amd64 runtime manifest is byte-identical to v4.

These failures confirm that HTTP availability alone is not parity. Sequential
adversarial execution, especially timeout followed by a clean candidate, is a
required deployment gate.

## Decision

Cloud Run sandboxes are accepted as Alpha-Gate's cloud executor for the next
small AlphaEvolve integration milestone. The feature remains Pre-GA, so the
local container is still the public reference and GKE Autopilot with gVisor
remains the fallback if isolation or lifecycle semantics regress.

No AlphaEvolve candidate-generation experiment or evaluation budget was used
during this parity gate.
