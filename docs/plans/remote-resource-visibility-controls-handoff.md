# Remote resource ownership — visibility/control handoff

Status: backend source implementation, **not F2 acceptance**. Visibility and UI
controls remain assigned to the other worker. Tests below are deferred definitions,
not passing evidence. No deployment, manager qualification or science was run.

## Preserve these distinct states

| State | Meaning / ownership |
|---|---|
| Capability unavailable / preparing / verified | Authenticated setup creates, verifies and destroys only a transient delegated systemd cgroup. No persistent service, CPU/RAM reservation, cpuset partition, memory protection or parent/slice quota is installed. Verification is not admission. |
| Policy published | Existing `resource_admission_policy` identifies target, version and CPU-thread/DRAM-byte/disk-byte bounds. Publication does not allocate them. |
| Durable staging intent | Job provenance `remote_resource_intent` binds target/machine/device, attempt path, both future reservation IDs and staging disk budget **before either allocation insertion or remote staging**. Staging charges only input/runtime/envelope bytes plus explicit protocol metadata headroom; CPU/RAM are zero. The execution slot is released during remote staging. |
| Compute admitted / launch requested | After staging, the existing target slot is acquired with a complete Job/attachment CAS and fresh physical observation. The existing global owner atomically transfers staging disk to a separate immutable compute reservation. The compute envelope is Job-bound before remote prepare/arm/run. This is imminent execution admission, not idle capability reservation. |
| Execution uncertain | Busy receiver/supervisor, unavailable endpoint, invalid evidence or unproven descendants is not zero usage. Controller PID/flock/heartbeat/age alone never releases remote science. |
| Generation fenced, nonexecuting | Authenticated worker-exclusive ownership excludes receivers/supervisors; a durable external tombstone prevents delayed receive/start/supervisor execution. No scientific authorization journal means nonexecution, **not fabricated zero kernel counters or scientific success**. Missing allocation IDs are also inserted as immutable released tombstones in the existing resource table, blocking a delayed allocation insert. |
| Compute quiescent / results waiting | Original invocation/boot/cgroup/inode and counter evidence, plus the new generation fence, permit compute/slot release. Delivery is independently attempt/provenance-CAS-owned. Remote exit zero is not scientific acceptance. |
| Boundary retirement pending | Science can be proven nonexecuting/quiescent while stopping the manager's idle anchor fails. Compute is released using the independent proof; `fence_receipt.boundary_cleanup` remains `pending`. Explicit payload deletion refuses pending/ambiguous boundary retirement and retries the same original invocation. |
| Retained disk | Actual quiescent attempt-tree logical bytes **and permanent owner metadata** remain charged. Source/runtime copies stay in the attempt namespace. No result payload deletion follows success, failure, cancellation or pull. |
| Observed disk overage | Extra observed liability uses additional immutable rows because reservations only shrink. This is disk debt, not retroactive admission or kernel quota enforcement. Interrupted local receive also retains partial/metadata bytes and observed overage. |
| Explicit payload removal | `removed` means the exact attempt payload was deleted and parent directory synced. The permanent generation tombstone/lock metadata deliberately remains and is still charged; do not display zero remote bytes merely because the payload was removed. |

## Backend control seam now implemented (UI deferred)

`POST /api/jobs/{job_id}/remote-storage/reconcile` has a closed request:

- `attempt_id`: exact current remote generation;
- `intent_sha256`: canonical digest of the server-persisted intent;
- `remove`: boolean, default `false`; only `true` requests deletion.

The route uses the existing mutation-principal and result-owner authorization
checks. The backend rejects changed identities, active/ambiguous storage, missing
owner protocol and changed endpoint/runner identity. It revalidates authenticated
remote ownership even when the Job already carries historical quiescence evidence.
No client can supply a pathname, allocation or purported quiescence receipt.

The response includes Job/attempt/intent identity, storage state, remaining logical
bytes and `payload_removed`. The service uses the existing controller guard and
Job/provenance CAS, never a compute slot. Remote read/receive processes participate
in the permanent owner operation lock; deletion cannot cross an active transfer.
A crash during deletion leaves `removing`; retry continues the same namespace.
A crash after deletion before controller accounting re-observes the tombstone and
reconciles only matching reservations. Stale pre-removal accounting is rejected.

Explicit pull continues through the existing pull route, independently of the
remote compute slot. An explicitly removed unpulled payload is no longer available
for pull. Existing ingested local results are not removed by remote cleanup.

## Controls/read models the next worker must define

- Show actual target, authenticated machine/boot/device, backend and unsupported
  reason separately from policy/version and physical availability.
- Distinguish staging disk, admitted compute, kernel usage, observed disk,
  retained owner metadata and manager-anchor retirement. Do not flatten an
  execution uncertainty or missing historical ownership protocol to zero.
- Provide typed global-policy inspection/change through its existing owner;
  preserve operator/agent parity, concurrency/shrink refusal and refresh semantics.
- Route cancellation to the attempt owner. Show cancellation, descendant
  quiescence, compute release, delivery and scientific validation separately.
- Connect the implemented exact-generation cleanup endpoint to an explicit
  confirmation/control surface. Never infer deletion from delivery or Job status.
- Historical receipts are provenance, not reusable admission credentials.

## Shared seams (reuse, do not fork)

- `global_resource_admission`: `publish_execution_target_readiness`,
  `reserve_remote_attempt`, `retain_remote_attempt`, `reconcile_remote_storage`;
  existing store/transactions and immutable identity/monotonic lifecycle triggers.
  No new scheduler, schema migration or worker experiment database.
- `remote_execution.targets`: authenticated supported setup/readiness and runner
  hashes. Requires reachable system systemd/cgroup v2 delegation and required
  counters with root/noninteractive sudo. Never modifies an unrelated hierarchy.
- `bms_remote_worker`: external attempt-owner journal/locks; stage/read-command,
  intent/arm/fence/remove-storage protocol; `OwnedBoundary` and ACK/launch gate.
  Boundary-creation intent precedes systemd-run. Description marker and boot bind
  interrupted creation; the original InvocationID is durably recorded before
  configuring the science child. Recovery never stops a replacement by name alone.
- `remote_resource_evidence`: strict scientific usage contract remains distinct
  from `bms.remote-attempt-fence.v1`. A digest binds bytes; SSH/runner verification
  is authentication, not the digest itself.
- `remote_execution.executor`: durable Job intent/attempt CAS, separate staging
  and compute admission, recovery, release, explicit delivery and cleanup.
- Native completion/reopen and callback-free stage journaling retain their
  existing sealed settings/input/scientific validation authority.

## Remaining qualification and genuine residual scope

1. **No project tests/build/lint/import probes were run.** Source edits received
   the editing tools' automatic diagnostics; project execution remains deferred.
   `git diff --check` is the authorized source check, not runtime acceptance.
   Deferred tests cover allocation-before/after-publication crashes, zero staging
   compute, slot-independent delivery, delayed launches/receivers, partial boundary
   creation, original invocation refusal, active cleanup, interrupted/idempotent
   deletion, immutable accounting and local receive overage. Older process,
   attachment and publication fixtures now explicitly separate simulated control
   evidence from live cgroup qualification.
2. Qualify the complete protocol on a supported manager: kill controllers and
   supervisors at each durable boundary, exercise rsync descriptor inheritance,
   concurrent cancellation/pull/cleanup, stop failure and receipt/metadata writes.
   Lost/bad counters after scientific authorization still fail closed; no new
   mechanism infers remote death from a reboot, clock or local lock alone.
3. Disk remains logical observation plus sampled abort, **not aggregate kernel
   filesystem quota enforcement**. Directory allocation blocks and shared target
   bootstrap/Nextflow infrastructure are not claimed as per-attempt scientific
   bytes. Qualify large staging/receive growth and overrun behavior live.
4. Historical generations created without the durable intent protocol are not
   silently adopted or deleted. The explicit API binds the current Job attempt;
   cleanup discovery/adoption of older superseded or legacy generations needs a
   separately authorized ownership/recovery operation. New recovery never rewrites
   a future retry's identity or touches another attempt/hierarchy.
5. Portable immutable native descriptors/original-path consumer migration remain
   separate F2 work. Receipt/input hashing does not solve same-inode mutation.
   F3–F5 and visibility/UI implementation are outside this backend package.
