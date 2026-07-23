# ADR 0004: Cloud Run nested-sandbox broker

- Status: Accepted; live parity passed 2026-07-18
- Date: 2026-07-18

## Context

An ordinary Cloud Run job cannot implement Alpha-Gate's executor contract. A
job accepts startup arguments and environment overrides, runs to completion,
and has no interactive request channel. Passing a complete market panel to the
candidate would break the lockstep no-lookahead boundary.

Cloud Run sandboxes entered public preview in July 2026. A Cloud Run service
can now launch an interactive nested sandbox with the injected `sandbox` CLI.
By default, the nested sandbox has no outbound network, parent environment,
secrets, or metadata-server access. Its root filesystem is read-only but can
read the host container image.

## Decision

Use an IAM-protected Cloud Run service as a trusted broker. The local
`CloudRunExecutor` sends a complete, strictly validated `SandboxRequest` to
the broker over HTTPS. The request contains market observations, so it never
goes directly to candidate code.

The broker launches one ephemeral nested sandbox and drives the same JSONL
worker used by Docker and Podman. The shared process driver sends one bar,
waits for one validated response, and only then sends the next bar. The nested
sandbox receives candidate source, the protocol worker, and the current frame;
future frames stay in broker process memory.

The broker image contains only the candidate validator, protocol models,
process driver, worker, Pydantic, and NumPy. It excludes scoring code, reward
thresholds, market loaders, reports, repository history, and cloud credentials.

The launcher command does not opt into `--allow-egress`, `--write`, bind
mounts, snapshots, or inherited environment. Cloud Run request concurrency is
one. The service uses a dedicated service account with no project roles, is
not publicly invokable, scales from zero, and is capped at one instance during
the parity milestone.

## Resource envelope

The shared process driver enforces wall-clock, frame-count, response-size, and
protocol limits. Cloud Run fixes CPU and memory at the broker revision, and
the broker rejects requests above those configured ceilings. The nested
sandbox gets no writable overlay, which is stricter than the local temporary
filesystem allowance.

The preview CLI does not expose per-sandbox CPU, memory, or PID flags. Host
resource limits and concurrency one still bound aggregate consumption, while
static validation rejects process and host-interaction imports. Live parity
must include timeout, output-flood, runtime-error, and malformed-portfolio
tests. If the preview's platform bounds or isolation prove insufficient, ADR
0001's GKE Autopilot with gVisor fallback remains active.

## Failure and preview policy

Transport, authentication, malformed broker responses, and unavailable
sandbox launchers become typed executor results. They do not disappear from
the experimental trial count.

Every nested sandbox receives a unique name. The broker force-deletes that
exact sandbox in a cancellation-shielded `finally` path after success,
candidate failure, protocol failure, timeout, or request cancellation. Delete
operations have a five-second deadline and three bounded attempts. The live
parity suite's timeout case is therefore also a cleanup regression gate. The
service-level `--min=0`, `--max=1`, concurrency-one, and 360-second request
timeout remain independent cost bounds if nested-sandbox cleanup ever fails.

Cloud Run sandboxes are a Pre-GA feature and may change without normal
compatibility guarantees. Alpha-Gate keeps the local container executor as
the reproducible reference and records deployed image digests in its reports.
The Cloud Run backend passed the live parity suite on 2026-07-18 and was
accepted for the first bounded AlphaEvolve integration. See
[`reports/cloud_run_parity_v0_1.md`](../../reports/cloud_run_parity_v0_1.md).

## References

- [Cloud Run code execution and default sandbox isolation](https://docs.cloud.google.com/run/docs/code-execution)
- [Cloud Run sandbox CLI reference](https://docs.cloud.google.com/run/docs/reference/sandbox-cli)
- [Cloud Run service-to-service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
