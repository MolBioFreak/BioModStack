# Remote resource ownership — visibility/control handoff

Status: backend first-pass source connection; **not F2 acceptance**. Resource
visibility and settings/control surfaces are deliberately assigned to another
worker. This pin specifies shared states and ownership seams, not a proposed UI.

## Preserve these distinct states

| State | Meaning / ownership |
|---|---|
| Capability unavailable / preparing / verified | Authenticated setup can create, verify and destroy a transient delegated systemd cgroup. Verification is not admission. No persistent service, CPU/RAM reservation, cpuset partition, memory protection or parent/slice quota is installed. |
| Policy published | Existing `resource_admission_policy` identifies target, version and CPU-thread/DRAM-byte/disk-byte bounds. Publication does not allocate them. Physical readiness and policy are separate from current free/active use. |
| Active attempt | Existing global `derived_resource_reservations` owns admitted CPU/RAM/disk. The sealed remote envelope binds that receipt before remote staging. Scientific launch uses an enrollment ACK and parent-owned launch gate before exec. |
| Execution uncertain | No remote quiescence proof exists. Controller PID/flock/heartbeat/lease expiry cannot establish remote process death. Do not label absent telemetry as zero usage. |
| Compute quiescent / results waiting | An authenticated, envelope-bound owned-cgroup receipt permits zero CPU/RAM charges and release of the exclusive execution slot. Delivery remains independently attempt/provenance-CAS-owned. Remote exit zero is not scientific acceptance. |
| Retained disk | Actual attempt-tree logical resident bytes remain charged after compute release. Source/runtime copies now live inside the attempt namespace; shared historical generations are untouched. The result tree is not automatically deleted on success, failure, cancellation or pull. |
| Observed disk overage | Extra retained liability uses a separate immutable reservation row because existing reservations only shrink. This is observed debt, not retroactive admission or proof of kernel quota enforcement. |

## Controls and read models the next worker must define

- Show actual execution target, authenticated machine/boot/device, capability
  backend and unsupported reason separately from effective policy/version.
- Distinguish policy ceiling, admitted capacity, kernel usage, current physical
  availability and retained bytes. Keep CPU quota, memory max/no swap, exact
  kernel peaks, logical disk observation and sample-abort semantics explicit.
- Provide typed global-policy inspection/change through the existing owner;
  preserve operator/agent parity and do not introduce NGS-only limits. Define
  concurrency/shrink refusal and admission refresh semantics before controls.
- Route cancel to the existing attempt owner. Show whether cancellation,
  descendant quiescence, compute release, delivery and scientific validation
  have independently completed. Explicit pull must not reacquire a compute slot.
- Define an authorized, generation-bound **explicit** remote retained-storage
  reconciliation/removal API before offering cleanup controls. Never infer disk
  deletion from result delivery or a terminal Job. No deletion API is added here.
- A historical receipt is provenance, not a reusable admission credential.

## Shared seams (reuse, do not fork)

- `global_resource_admission`: `publish_execution_target_readiness`,
  `reserve_remote_attempt`, `retain_remote_attempt`,
  `abandon_unstaged_remote_attempt`; existing store/transaction and immutable
  identity triggers, no worker experiment DB or second scheduler.
- `remote_execution.targets` owns authenticated supported setup/readiness and
  runner hash publication. The supported backend currently requires a reachable
  system systemd manager with cgroup v2 delegation, root/noninteractive sudo and
  the required kernel counters. It does not install a manager as PID 1 inside an
  incompatible container or alter an unrelated parent hierarchy.
- `bms_remote_worker.OwnedBoundary` owns transient services and scientific
  descendants; `remote_resource_evidence` validates the distinct versioned
  producer contract. Local systemd evidence retains its existing validator.
- `remote_execution.executor` owns envelope/manifest byte binding, durable
  attempt CAS, release and explicit delivery. `stage_reporter` journals actual
  remote terminal reports without requiring an API callback.
- Native completion/reopen consumes the returned receipt and stage journal,
  preserving sealed settings/input binding and existing scientific validators.

## Remaining implementation/qualification — do not hide behind the UI handoff

1. No project tests, build, lint, import probes or live manager/scientific jobs
   have run for this source package. Only source review and `git diff --check`
   are authorized here. New regressions are definitions, not passing evidence.
2. Pre-boundary failures and a crash between allocation insertion and durable
   Job/envelope publication still require an explicit nonexecution/recovery
   protocol. A crash during boundary creation before its identity journal is
   written remains conservative. Ambiguous staging failures retain allocation;
   a finer separation of justified staging ownership from scientific CPU/RAM
   is still needed. Do not claim all failure/restart release paths are finished.
3. After a recorded boundary exists, recovery checks invocation, boot, cgroup
   path and inode; it observes natural emptiness or cancels only that boundary.
   Lost/bad counters fail closed. Qualify interrupted receipt publication,
   manager stop failure and same-attempt cancellation interleavings live later.
4. Disk is periodically observed with abort-on-observed-overrun, not aggregate
   filesystem quota enforcement. Bootstrap/shared Nextflow cache storage is
   target infrastructure, not per-attempt scientific measurement. Qualify disk
   growth during staging and final metadata writes and explicit cleanup later.
5. Adapt the older remote runner/attachment/lifecycle fixtures to the new
   capability/admission/receipt contracts before the authorized suite run.
   New tests cover shared native receipt dispatch plus FASTQ-QC projection and
   callback-free stage reporting, not full scientific acceptance of every lane.
6. Portable immutable native descriptors and original-path consumer migration
   remain separate F2 work. Receipt/input boundary hashing does not solve
   same-inode mutation or replace that work. F3–F5 remain outside this package.
