# Remote Bridge — published-code audit and rectification specification

**Status:** Implementation-ready rectification specification from a read-only audit. Not an implementation, deployment or live acceptance claim.

**Audited published `origin/test`:** `2c90d14f5bee5aa66f741e89ad641bc7663dd0b3`. Running Development reported the same revision at audit start.

**Published-head recheck:** `54bba2116b051acad15a38a7398b18177c3a3ccc`. No source file in the verified citation set changed. Intervening changed paths: `platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json`, `platform/frontend/src/components/BioXpCockpit.tsx`, `platform/frontend/tests/vitest/bioxpCockpitAdmissionFanoutMounted.test.tsx`. Tests remain pinned to the audited revision, not rerun on the intervening revision.

**Source worktree:** `/home/dalab/biomodstack/wt-remote-published-audit-20260912`.

**Evidence directory:** `/home/dalab/audits/remote-published-20260912`.

**Controlling product spec:** `docs/specs/remote-bridge-1.0.md`, interpreted with Christian’s subsequent scope restrictions: existing science remotely executed, existing native data returned for BMS use. Neurosnap remains in scope under section 17.

## 1. Exact conclusion and classification

The bridge is substantially implemented and published, but worker attachment is **not** the only remaining work.

- **13 confirmed code/integration gaps:** R01–R13 below. These are conditional or shared-interface defects, not thirteen wholly missing features. Each has a trigger, current failure, source references, correction boundary and acceptance.
- **Two separate publication/verification obligations:** SCOPE-01 (separate non-transport MD changes) and TEST-01 (migrate stale regression fixtures). Neither is a new model/science project.
- **Two observed setup prerequisites:** CFG-01 worker attachment and CFG-02 the selected ColabFold controller configuration. Neurosnap credential presence is credited, not authentication acceptance.
- **Explicit unresolved verification questions:** U01–U07 are not counted as defects or automatically authorized implementation work.
- **Live qualification:** A01–A06 has not been performed on this audited release. Offline doubles and HTTP health are not substitute acceptance.

No claim is made that every possible scientific setting combination was exhaustively tested. This is a pinned, source-referenced inventory of confirmed gaps and identified verification limits—not an unsupported percentage-complete estimate.

## 2. Scope and invariants

1. Reuse existing native workflows, typed settings, canonical inference/runners, seeds, candidate ordering/grouping, required/optional child semantics, filtering, analysis contracts and native importers. Placement must not change science.
2. Keep one shared compiler/selected plan, component runtime, ownership/lease authority, checkpoint authority and transfer/publication lane. Native format adapters may differ; scheduling and lifecycle may not become family-specific remote infrastructure.
3. Full existing GROMACS MD remains preparation → replicas → exact aggregation → mandatory analysis → native completion. Neither replica-only execution nor a new OpenMM/MMGBSA route substitutes for it.
4. Fix Frustra invocation/input custody/return integration only. Do not redesign inference, guidance, ranking, grouping semantics or scientific analysis.
5. No oligo work, new OpenMM/MMGBSA integration, new standalone LigandMPNN/ProteinHunter science, or broad model-parameter completion. No unrelated NGS/MolBio/robot work. Existing working combinations remain authoritative; declarations alone do not establish a functioning native workflow.
6. Preserve existing approved host-to-worker push and authentication. No new release host, authentication redesign, credential propagation to workers or implicit reinstall. Shared immutable image lifecycle remains governed by `docs/Shared_Scientific_Runtime_Images.md`; no parallel image store or task-local SIF copies to make admission pass.
7. MSA uses the explicitly selected supported ColabFold or Neurosnap provider and existing shared controller/cache. No local search, multi-IP workaround, silent provider substitution, dropped MSA stage or changed pairing. Generated-candidate MSA is an explicit external scientific-service boundary—not permission to add workstation callbacks to every component. Preview never submits sequences.
8. Manual result return remains default; automatic return is explicit and persisted. Preserve full relevant native data, raw/rejected artifacts and provenance under existing contracts. Local analysis/reopening must not depend on remote filesystem paths. Failure diagnostics must remain failures.
9. Reuse existing critical artifact/log surfaces; do not add noncritical logs, validator dumps, redundant per-run audits, percentage claims or a new validation framework. Integrity checks at actual custody/publication boundaries remain required.
10. This document authorizes no instance acquisition/start, external sequence submission, code deployment, service restart or data mutation. Those actions require their own applicable owner authorization during implementation/acceptance.

## 3. Existing mechanisms credited

Source and selected offline checks establish implementation of shared native invocation/plan projection, pinned critical release/inventory/provisioning, typed target selection, source/attempt/lease fences, worker-local SQLite/coordinator descendants, quiescent sealing, manual/automatic shared return, contained native output mapping, and calls into existing native import/analysis consumers. These mechanisms must be repaired, not replaced.

The reviewed full MD orchestrator and its replica/analysis collectors are present. Public FAMPNN/ProteinMPNN sequence wrapper wiring, BoltzGen child collection, antibody maturation/annotation, CM backend joins, PPIFLOW/local-redesign gates, direct MSA handoff and native host return calls are present. The confirmed conditional failures below still prevent a blanket usability claim. Dedicated MD, antibody, local-redesign, CM and generic launchers already have target selection; adding another selector is not a missing work package.

Source anchors: `platform/api/services/remote_execution/executor.py:554-688,2241-2388`; `scripts/lib/component_adapter.py:272-484`; `platform/api/component_runtime.py:765-989`; `workflows/experimental/molecular_dynamics/orchestrator.nf:198-327`. Detailed credited paths and their limits are in the five area reports.

## 4. Confirmed gap ledger

Severity describes consequence, not a fabricated work estimate. “Source-confirmed” does not imply the defect was exercised on a live worker.

| ID | Required correction | Affected boundary | Severity |
|---|---|---|---|
| R01 | Selected-only dependency/input transport | blocks affected placements on a minimal/clean host | high |
| R02 | Boltz MSA selected-plan admission | blocks Boltz2 MSA-enabled placement even after preparation | high |
| R03 | MSA delivery for generated candidates | blocks cited generated-candidate Protenix validation on cache miss | high |
| R04 | Frustra child-input custody | blocks mandatory/selected Frustra parent fan-out | high |
| R05 | Canonical prequeue plan/preview binding | late failure and missing approved-plan admission; execution remains guarded | high |
| R06 | Recover durable cancellation delivery | failure/restart cancellation can leave worker science running | high |
| R07 | Recover checkpoint continuation handoff | rejected/interrupted continuation can strand a job and reservation | high |
| R08 | Persist checkpoint boot identity | first observation at a review gate can make continuation impossible | medium |
| R09 | Cancel the actual result-transfer writer | operator cancellation does not stop controller-side download | medium |
| R10 | Read returned failed-attempt artifacts | diagnostics are locally retained but disconnected from normal reads | medium |
| R11 | Read the current generation’s logs | continued job can show stale or absent logs after successful return | medium |
| R12 | Qualify bootstrap helper Python | conditional bootstrap failure on an older accepted system Python | medium |
| R13 | Check the selected provider’s real state root | conditional false configured-readiness for ColabFold | low |

### R01 — Selected-only dependency/input transport

**Audit identity:** `PI-02`. **Classification:** confirmed_bridge_defect. **Evidence qualification:** high.

**Trigger:** A non-generic-sequence, non-Protenix workflow (e.g. ESMFold2 or Boltz no-MSA) has its selected assets installed, but unrelated rfd_models/af2_models/alphafold_params or msa_local_db defaults are absent or outside input roots.

**Current failure:** Shared compiler emits all these default path arguments; only generic sequence wrappers omit unrelated defaults, and remote omission is Protenix-specific. _input_assets recursively treats absolute native parameter strings as input candidates unless exact system roots or selected runtime members. Unselected weight subdirectories do not match those exclusions and fail unavailable/managed-input checks, or get swept into biological input transfers if inside data storage. Offline execution of the real _input_assets AST reproduces RemoteBundleError for an absent, unselected rfantibody default.

**Required outcome:** A complete selected dependency closure must not require, copy, or classify another model’s unused default weights/local-MSA databases as biological inputs. Genuine selected/native input fields still fail closed.

**Smallest correction:** Use existing shared selected dependency and native input roles to distinguish runtime selectors/defaults from scientific input assets at placement. Remove or bind only unselected system runtime defaults, not operator science; do not add unrelated assets or re-enable local MSA search to pass admission.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:41-43`; `docs/specs/remote-bridge-1.0.md:63-74`; `docs/specs/remote-bridge-1.0.md:115-118`.

**Published source:**
- `platform/api/services/nextflow.py:5160-5174` — Builds unrelated default runtime paths
- `platform/api/services/nextflow.py:5211-5237` — Emits defaults except generic sequence special case
- `platform/api/services/remote_execution/bundle.py:618-639` — Remote default omission scoped only to Protenix
- `platform/api/services/remote_execution/bundle.py:449-499` — Absolute string walk; exact system-root/selected-runtime exclusions only
- `platform/api/services/remote_execution/bundle.py:917-919` — runtime_paths only selected-plan assets
- `platform/api/services/remote_execution/bundle.py:1018-1025` — Production consumes that candidate walker

**Acceptance:**
- Real compiler-produced ESMFold2 and Boltz no-MSA bundles with only selected assets, split weights/input roots, unrelated defaults absent, and local MSA database absent must package successfully and transfer no unselected trees. Genuine missing selected inputs/assets must remain rejected. Check local/remote scientific settings identities unchanged.

### R02 — Boltz MSA selected-plan admission

**Audit identity:** `PI-01`. **Classification:** confirmed_bridge_missing_implementation. **Evidence qualification:** high.

**Trigger:** An existing Boltz2 predict or complex invocation selects boltz_use_msa=true, including supplied alignment or successful controller-prepared package.

**Current failure:** Registry adds boltz2:msa external_service_roles blocker whenever MSA is enabled; sequence search also adds GenerateLocalMSA blocker. Controller materializes MSA and _bind_protenix_msa_transport adds directory/digest, but only Protenix calls _bind_prepared_protenix_plan. Boltz retains its original incomplete metadata. compile_remote_dependencies rejects plan.complete=false before transfer/execution. Successful preparation or credentials cannot clear this source gate. This is separate from the stale transport fixtures.

**Required outcome:** Verified existing Boltz native MSA package/task/chain authority closes its own selected-plan transport roles without changing provider, alignment pairing, requested/effective settings or inference semantics.

**Smallest correction:** Extend the existing trusted MSA plan-binding step to validate/bind Boltz native task/chain package identities and clear only discharged external-service blockers. Retain no-local-search/no-fallback policy; no scientific redesign or second compiler.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:63-74`; `docs/specs/remote-bridge-1.0.md:224-248`; `docs/specs/remote-bridge-1.0.md:261-268`.

**Published source:**
- `platform/api/model_registry.py:837-855` — Adds sequence generated-MSA blocker
- `platform/api/model_registry.py:897-919` — Adds Boltz MSA blocker even for supplied paths
- `platform/api/services/nextflow.py:2780-2804` — Production compiles once then prepares and retains only transport fields
- `platform/api/services/nextflow.py:4175-4216` — Only Protenix resolves metadata; Boltz plan reused unchanged
- `platform/api/component_runtime.py:279-294` — Any blocker makes complete false
- `platform/api/services/remote_execution/bundle.py:586-605` — Actual remote consumer rejects incomplete plan

**Acceptance:**
- Use real compile_job_nextflow_invocation -> controller preparation using existing supplied/cached fixture -> _compile_launch_nextflow_invocation -> prepare_remote_bundle for Boltz predict and complex, MSA enabled, separate host/worker roots. Assert complete selected metadata after verified binding, unchanged requested/effective hashes, original document bytes/chain pairing/order retained, corrupt or incomplete handoffs rejected. Then separately approved live scientific acceptance.

### R03 — MSA delivery for generated candidates

**Audit identity:** `PROVISION-MSA-01`. **Classification:** confirmed_missing_implementation. **Evidence qualification:** high for the cited batch producer/consumer gap; broader workflow roster coverage deferred to parent.

**Trigger:** An existing antibody validation workflow selects Protenix with protenix_use_msa=true and generates candidate chains lacking supplied/already-cached alignments. This is the generated-candidate branch, not a direct protenix Job with known input roster.

**Current failure:** The shared native plan declares protenix:generated_msa as planned_from_generated_candidates. Launch preparation calls prepare_launch_msa with the top-level model ID; antibody_denovo/antibody_child (and conformational_mapping) return unchanged at the unsupported-model branch. The concrete BatchProtenixValidation consumer builds input.json from worker-generated PDBs, invokes prepare_protenix_msa.py without --prepared-inputs/--prepared-sha256, and the latter raises Missing controller-prepared MSA inputs on a cache miss. The portable bridge maps msa_cache_dir to an attempt-local worker cache. Searches found no operational consumer of planned_from_generated_candidates outside declaration/serialization; the production external_services update in nextflow.py concerns direct protenix:msa, not this generated intent. This establishes an unimplemented producer-to-provider-to-worker handoff for the cited branch, not failure of the existing direct Protenix/Boltz preparation path. The pure extracted dispatcher probe returns unchanged requests for these top-level IDs.

**Required outcome:** Existing MSA-enabled generated-candidate validation must receive controller-produced, chain/settings/digest-bound portable alignments before native inference, without local search or silently disabling MSA.

**Smallest correction:** Connect the existing generated ExternalServiceIntent at the shared component boundary: persist the actual native candidate roster, prepare through the existing controller provider service, and deliver verified portable inputs to the assigned worker before admitting that validator. Reuse existing custody/transfer/checkpoint authorities; do not add per-workflow worker scheduling or host callbacks for ordinary science. Update the native validator to consume the existing prepared input contract. Until connected, preview should identify this exact required external-service gap rather than equate a declared intent with execution support. Parent should reconcile the service-boundary design against the no-new-orchestration scope before implementation.

**Service boundary constraint:** use the already declared external-service intent and controller provider authority. A generated candidate may pause/wait for that explicit service, but ordinary worker science must remain autonomous. Do not inject provider keys or build a bespoke antibody scheduler. If a proposed patch changes this authority model, resolve that precise conflict with Christian before implementation.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:63-67`; `docs/specs/remote-bridge-1.0.md:189-196`; `docs/specs/remote-bridge-1.0.md:236-240`; `docs/specs/remote-bridge-1.0.md:261-268`.

**Published source:**
- `platform/api/native_components.py:559-570` — Generated candidate intent declaration, required role, and planned-only state.
- `platform/api/native_components.py:624-645` — Existing antibody validator registers the generated MSA intent.
- `platform/api/services/nextflow.py:2780-2804` — Preparation dispatch receives top-level model_id before remote launch.
- `platform/api/services/model_msa_handoff.py:67-96` — Only supported direct IDs are handled; top-level workflow IDs return unchanged.
- `modules/antibody_batch.nf:328-343` — Native batch consumer creates candidate-chain input on the execution side.
- `modules/antibody_batch.nf:393-429` — MSA-enabled native batch calls consumer without the portable prepared-input arguments.
- `scripts/prepare_protenix_msa.py:852-889` — Portable hydration exists; absent prepared/supplied/cache data fails closed.
- `platform/api/services/remote_execution/bundle.py:1071-1076` — MSA cache is relocated to per-attempt worker storage.

**Acceptance:**
- On the frozen source use an offline provider double at the real shared provider boundary and a native compiler-produced generated-candidate validator invocation, with different controller/worker roots and an empty worker MSA cache. Verify exact candidate/task/chain identity, effective pairing/settings and digests; the validator must hydrate without worker provider calls. Exercise cache hit/miss, pending ticket recovery, cancellation, and a changed candidate roster. Later live acceptance requires owner approval. Do not count a fail-closed rejection as functional support.

### R04 — Frustra child-input custody

**Audit identity:** `WW-01`. **Classification:** confirmed_defect. **Evidence qualification:** high.

**Trigger:** An admitted component-runtime parent reaches SchedulerFrustraMPNNParentFanout with at least one candidate (protein_design run_frustrampnn=true; antibody_denovo selected Frustra; conformational_mapping mandatory Frustra). Normal remote work/results directories are siblings.

**Current failure:** StageFrustraMPNNParentCandidate creates candidate_*/source.* in its task directory. SpawnWaitFrustraMPNNParentChildren uses stageInMode copy, so source.resolve() remains in task work. execute_parent_fanout computes source.resolve().relative_to(runtime.artifact_root.resolve()), but remote artifact_root is attempt/results and task work is attempt/work. ValueError occurs at line 223 before child submission. The unchanged AST function reproduced the rejection in a bounded path-only probe. Local component attempts with separated work/results have the same integration defect; this is not an inference defect.

**Required outcome:** Materialize the exact produced source bytes into a retained, attempt-owned input/reference location before recording a result-root-relative component input; keep candidate identity, ordering, settings and requiredness unchanged.

**Smallest correction:** Use the existing shared artifact materialization/immutable-copy authority at the task-output to retained-component-input boundary. Bind digest/size and candidate identity, then submit the retained relative reference. Do not loosen containment, move all task work into returned artifacts, add host callbacks, skip Frustra, or change inference/grouping.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:41-47`; `docs/specs/remote-bridge-1.0.md:82-90`; `docs/specs/remote-bridge-1.0.md:189-196`.

**Published source:**
- `modules/frustrampnn_parent_fanout.nf:3-57` — Real two-process producer/consumer, copied task staging and fanout argv
- `scripts/stage_frustrampnn_parent_candidate.py:36-42` — Relative candidate output and real copy, not a retained-result reference
- `scripts/run_frustrampnn_parent_fanout.py:216-234` — Invalid source-root assumption before submit_child_job
- `platform/api/services/remote_execution/bundle.py:1054-1076` — Remote results and work are distinct sibling directories
- `platform/api/services/remote_execution/bundle.py:1166-1187` — Component context binds artifact_root to remote results
- `platform/api/services/nextflow.py:2290-2315` — Context keeps root argv and requested artifact root without work-root relocation
- `workflows/protein_design.nf:1293-1328` — Protein design actual selected caller
- `workflows/antibody_denovo.nf:3382-3403` — Antibody actual selected caller
- `workflows/conformational_mapping.nf:78-124` — CM mandatory fanout and downstream analysis dependency

**Acceptance:**
- Run native Nextflow StageFrustraMPNNParentCandidate -> SpawnWaitFrustraMPNNParentChildren with distinct work/results roots using exact compiler argv. Assert required children are submitted using immutable contained sources, grouped and singleton receipts return, and native parent result collection completes for protein_design, antibody_denovo and all CM backends. Reject source mutation/foreign paths; prove no HTTP/DB access during worker preparation/fanout. Follow with owner-approved exact-release live execution; AST probe alone is not that acceptance.

### R05 — Canonical prequeue plan/preview binding

**Audit identity:** `ADMISSION-01`. **Classification:** confirmed_missing_admission_integration. **Evidence qualification:** High for source-level absence on the complete ordinary producer and schema/caller chain; no mounted HTTP/DB enqueue reproduction was performed..

**Trigger:** A normal direct browser/agent JobCreate without an optional Project launch_context_id selects a ready remote execution target.

**Current failure:** JobCreate has placement and scientific settings, but no shared execution-plan preview identity. Its extra fields are forbidden. create_job routes requests without Project context directly to _create_job. That producer validates target eligibility, source identity, MSA policy/provider prerequisites and model-specific/schema requirements, then constructs and commits queued Job rows. Inspection of the complete producer's call graph and the registry validator finds no shared selected execution-plan completeness or approved preview-digest check. The shared selected plan is constructed later for dispatch; remote bundle admission then rejects incomplete plans. The optional Project context is not a mandatory general bridge preview contract. This is a missing prequeue/preview integration, not a claim that scientific parameter validation is absent or that incomplete plans are allowed to execute.

**Required outcome:** The selected shared-plan capability and an approved preview binding are checked at canonical submission before queue insertion, while dispatch retains freshness checks. Required unresolved external-service stages need an explicit resolvable contract, not a blanket bypass or an online MSA submission during preview.

**Smallest correction:** Connect the existing shared compiler/preview authority to the canonical Job admission boundary and its actual browser/agent callers. Bind the approved request/source/placement/meaningful plan identity; reject stale approval or unsupported combinations before publishing queued jobs. Reuse the existing compiler and account explicitly for plan transitions from declared external-service input preparation to admitted execution. Do not add a remote recipe compiler, change science or weaken dispatch guards.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:63-74`.

**Published source:**
- `platform/api/schemas.py:43-107` — Typed direct request has no execution-plan preview identity; Project context optional and extra fields forbidden.
- `platform/api/routers/jobs.py:7020-7056` — No-context requests route directly to the canonical producer.
- `platform/api/routers/jobs.py:5706-5789` — Producer policy, target and source checks.
- `platform/api/routers/jobs.py:5691-5703` — Normalization ends in schema validation, not selected-plan completeness.
- `platform/api/model_registry.py:487-601` — Complete validator checks model/mode, fields and native alias routing; no selected-plan preview or completeness.
- `platform/api/routers/jobs.py:6531-6577` — Queued Job construction and session insertion.
- `platform/api/routers/jobs.py:6624-6644` — Commit precedes later scheduler launch.
- `platform/api/services/nextflow.py:4662-4684` — Actual shared selected-plan construction.
- `platform/api/services/remote_execution/bundle.py:586-605` — Dispatch-side guard exists and must remain; it is not prequeue enforcement.

**Acceptance:**
- Exercise real direct Job HTTP admission on task-owned independent-connection storage: absent/stale preview approval and unsupported selected combinations must fail before queued row/lease creation. Do not stub the shared compiler.
- A valid preview and unchanged request/source/placement must admit through browser and agent callers; change a meaningful field/source and require renewed approval. Preserve requested/effective scientific settings and explicit provider selection.
- Prove provider preparation is not invoked by read-only preview; legitimate prepared-input rebinding retains original approved scientific identity. Dispatch must still reject stale or incomplete executable authority.

### R06 — Recover durable cancellation delivery

**Audit identity:** `LIFECYCLE-01`. **Classification:** confirmed_defect. **Evidence qualification:** high.

**Trigger:** cancel_job_lineage commits queue_status=cancelling, then the API dies before cancel_nextflow_job, or the first SSH cancel fails before reaching the worker. The original worker remains running and later SSH status succeeds.

**Current failure:** The recovery poller selects the job, but the cancelling branch only observes status and publishes remote_state. It never sends the outstanding cancel command. Science continues until natural completion (or another explicit operator cancel), despite durable cancellation intent. A long-running job can keep consuming the leased worker indefinitely. Offline actual-function branch probe records only a status transport call.

**Required outcome:** On reconnect/restart, durable cancellation intent must drive idempotent delivery to the original attempt and bounded stop/join, retaining the lease until proved quiescent. Observing running is not fulfillment of pending intent.

**Smallest correction:** In the existing remote reconciliation cancellation lane, retry the original attempt cancel under the persisted job/source/lease fence when it remains nonterminal. Re-read authority around I/O and keep stop delivery separate from terminal publication. Reuse worker cancel; do not create a second scheduler, kill by unverified PID, or release on transport failure.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:130-139`.

**Published source:**
- `platform/api/services/job_control.py:156-225` — Cancellation intent commits before external stop; the crash/delivery gap is real.
- `platform/api/services/nextflow.py:6259-6269` — Remote run IDs route through cancel_remote_run_id.
- `platform/api/services/remote_execution/executor.py:2417-2443` — Transport failures return false; no durable retry task is registered here.
- `platform/api/services/gpu_orchestrator.py:3270-3281` — Production per-target recovery invokes reconcile_remote_job.
- `platform/api/services/gpu_orchestrator.py:3320-3359` — Cancelling jobs are selected, but remote jobs are removed from local completion handling.
- `platform/api/services/remote_execution/executor.py:1590-1623` — Reconciler cancelling branch performs observation/publication only.
- `platform/api/tools/bms_remote_worker.py:1007-1027` — Worker cancel provides an existing serialized/idempotent durable intent mechanism to reuse.

**Acceptance:**
- Independent-session test: commit cancellation intent, terminate the controller before actuator delivery, restart poller, prove cancel reaches the same attempt and completes only after quiescence.
- SSH double rejects the first cancel before delivery; reconnect must cause status plus cancel, not status-only forever. Verify target lease remains held until the worker quiescent receipt.
- Race a successor/lease change during cancel retry and prove no successor publication/release.

### R07 — Recover checkpoint continuation handoff

**Audit identity:** `LIFECYCLE-02`. **Classification:** confirmed_defect. **Evidence qualification:** high.

**Trigger:** A caller supplies a nonempty checkpoint_decision accepted by the resume route but rejected by the worker compiler (for example {"continue":true}, or a foreign selected_artifacts path); alternatively continuation capacity is insufficient. Host has already committed generation g+1 and a new lease while worker stays at paused generation g.

**Current failure:** The host advances generation and marks running before SSH. Worker validation rejects before changing the ledger/status, and host labels the failure checkpoint_resume_uncertain. Every normal reconciliation then rejects generation g in remote_status before reaching checkpoint recovery. The same resume endpoint refuses a new decision because the job is no longer awaiting_input. No durable host decision/operation replay protocol analogous to component_retry is present. Operator cancellation may recover the target, but destroys the continue path rather than allowing correction of the rejected decision. Worker SQLite resume authorization and status/reset/spawn are also separate crash boundaries, not one recoverable operation.

**Required outcome:** A definitively rejected prestart continuation must preserve the review checkpoint and permit corrected explicit continuation after safe reservation reconciliation. Ambiguous delivery must retain its fence but support observing/replaying the same durable operation without issuing duplicate science; predecessor status alone must neither release nor permanently prevent recovery.

**Smallest correction:** Extend the existing checkpoint command with a persisted operation identity and decision/resource binding, plus source-bound observation of accepted/rejected/claimed/spawned state before ordinary generation comparison. Persist intent separately from accepted generation. Reuse component-retry-style same-operation replay and worker start fencing. Reconcile definitive prestart rejection back to the quiescent checkpoint with a fenced lease release; never infer no-delivery merely from an old status. Make ledger/status/reset crash boundaries recoverable by that same operation.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:49-49`; `docs/specs/remote-bridge-1.0.md:130-139`; `docs/specs/remote-bridge-1.0.md:193-193`.

**Published source:**
- `platform/api/routers/jobs.py:9523-9541` — Route accepts arbitrary nonempty decision dict and later requires awaiting_input for another attempt.
- `platform/api/services/remote_execution/executor.py:1085-1137` — Host obtains capacity observation, acquires lease, increments receipt generation and clears awaiting_input before worker validation; errors become uncertain.
- `platform/api/services/nextflow.py:4540-4567` — Worker native compiler rejects unsupported decision shape/selection.
- `platform/api/services/nextflow.py:2392-2429` — Actual edge CPU/RAM/scratch admission can reject after host minimal observation.
- `platform/api/tools/bms_remote_worker.py:435-483` — Worker compiler/ledger validation precedes status reset and spawn; errors leave predecessor or intermediate state.
- `platform/api/component_runtime.py:991-1036` — Checkpoint decision and resume_ready commit in SQLite separately from worker status/start claim.
- `platform/api/services/remote_execution/executor.py:1213-1223` — Strict generation and continuation lease checks reject predecessor observations.
- `platform/api/services/remote_execution/executor.py:1552-1563` — Component retries have a dedicated pre-generation observation lane; checkpoints do not.
- `platform/api/services/remote_execution/executor.py:1590-1616` — Checkpoint recovery runs only after ordinary remote_status generation checks.
- `platform/api/services/remote_execution/executor.py:990-1072` — Existing component retry protocol supplies a reusable pattern for durable operation identity/replay.

**Acceptance:**
- Call real resume route with a valid checkpoint and {"continue":true}; prove a clear rejection leaves a correctable awaiting checkpoint and no stranded target lease.
- Repeat with foreign selection and insufficient continuation CPU/RAM; then submit a valid decision and execute exactly one continuation.
- Kill at host post-intent/pre-SSH, worker post-ledger/pre-status, post-status/pre-claim and post-claim/pre-spawn; recover by observing/replaying the same operation without automatic scientific approval or duplicate spawn.
- Lost-response success case must recover g+1; reject attempts to replace operation identity/decision while delivery is uncertain.

### R08 — Persist checkpoint boot identity

**Audit identity:** `LIFECYCLE-03`. **Classification:** confirmed_defect. **Evidence qualification:** high.

**Trigger:** Host/API loses the initial started response (or restarts before storing it), and the autonomous workflow reaches a quiescent review gate before the first successful observation. Persisted remote receipt is still the staging/launch receipt with generation 0 and no boot_id.

**Current failure:** The awaiting_input reconciliation branch stores checkpoint payload and releases the target lease but does not update remote_execution_receipt from the worker status. That initial receipt does not include boot_id. Later request_remote_checkpoint_resume always rejects with "Checkpoint attempt/boot/lease identity is incomplete" even though the authenticated worker status supplied a valid boot identity. Offline actual-function probe reproduces awaiting_input plus released lease and receipt still lacking boot_id.

**Required outcome:** A valid first observation at a quiescent checkpoint must persist the same attempt boot/generation/start identity that a normal running receipt would establish, so explicit review/continue works after a host outage.

**Smallest correction:** In the existing awaiting_input CAS, merge validated worker attempt status identity into remote_execution_receipt (at least boot_id, generation, continuation identity and appropriate observed paths/started time) atomically with checkpoint projection and lease release. Retain old/source identity fences; never source boot from current provider inventory.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:49-49`; `docs/specs/remote-bridge-1.0.md:130-139`; `docs/specs/remote-bridge-1.0.md:193-193`.

**Published source:**
- `platform/api/services/remote_execution/executor.py:310-372` — Initial remote receipt records no boot_id.
- `platform/api/services/remote_execution/executor.py:440-465` — Normal started receipt stores boot_id; missing this path is the trigger.
- `platform/api/services/remote_execution/executor.py:659-671` — Ambiguous initial start keeps existing receipt and marks launch_uncertain.
- `platform/api/tools/bms_remote_worker.py:793-809` — Autonomous worker can reach and publish awaiting_input while host is disconnected.
- `platform/api/services/remote_execution/executor.py:1624-1643` — Checkpoint projection/release omits receipt update.
- `platform/api/services/remote_execution/executor.py:1079-1084` — Continuation requires persisted receipt.boot_id.
- `platform/api/services/gpu_orchestrator.py:3320-3343` — Ordinary paused/released checkpoint is no longer polled solely for lifecycle, so no later automatic running-receipt repair.

**Acceptance:**
- Lose initial run response, let worker reach a real durable checkpoint, reconnect directly to awaiting_input. Assert receipt contains worker boot, lease is released, review retrieval succeeds, and valid explicit continuation reacquires same target and succeeds.
- Repeat after API restart and with a checkpoint reached before run RPC returns. A foreign attempt/boot or stale generation must still fail closed.

### R09 — Cancel the actual result-transfer writer

**Audit identity:** `RN-03`. **Classification:** confirmed missing control integration. **Evidence qualification:** high source-chain confidence; no live or DB/rsync cancellation probe executed. Existing guards prevent stale publication when cancellation wins. No data overwrite/byte deletion claim..

**Trigger:** A non-MD remote job is returning through a long-running host rsync after its worker science has already sealed and become quiescent; the operator cancels through DELETE /jobs/{job_id}. Applies to manual and automatic pulls through their shared collector. MD-specific actuator admission is not independently qualified here.

**Current failure:** The API commits cancellation intent, then cancel_job_lineage calls cancel_nextflow_job -> cancel_remote_run_id -> cancel_remote_job. That function only invokes the worker cancel command. The already-terminal worker returns its quiescent status without stopping the separate host rsync session. The host transfer task has no job-addressed cancellation handle wired into this API: manual pulls are BackgroundTasks; automatic tasks are retained in an unkeyed set but never canceled by job cancellation. collect_remote_results has no cancellation-state check while awaiting rsync. The job can be marked cancelled while transfer continues to ordinary completion/error or its default 3600-second timeout.

**Required outcome:** Normal operator cancellation of an in-flight return signals the actual controller transport owner, awaits or reports its quiescence, and preserves safely reusable received/staged bytes. Cancellation completion must not imply that the local transfer writer stopped based only on worker-science quiescence.

**Smallest correction:** Connect the existing durable cancellation intent to the attempt/manifest-bound host transport owner, shared by manual and automatic return. Reuse the supervisor pipe/quiescence protocol instead of inventing a new transfer mechanism. Resolve across controller processes/restarts using the existing ownership identity; an unkeyed process-local task set alone is insufficient. Keep publication CAS and retained staging protections.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:139-149`.

**Published source:**
- `platform/api/routers/jobs.py:7963-7975` — Real cancellation route calls lineage control
- `platform/api/services/job_control.py:156-239` — Durable intent then external-owner cancellation; only cancel_nextflow_job is invoked
- `platform/api/services/job_control.py:241-360` — Terminal cancellation is published after that worker-only stop evidence
- `platform/api/services/nextflow.py:6259-6269` — Remote run IDs route directly to cancel_remote_run_id
- `platform/api/services/remote_execution/executor.py:2417-2462` — Cancellation sends worker cancel only; no local pull cancellation
- `platform/api/tools/bms_remote_worker.py:1007-1013` — Quiescent terminal worker returns immediately
- `platform/api/services/remote_execution/executor.py:1516-1548` — Automatic return task retention has no per-job cancel integration
- `platform/api/services/remote_execution/executor.py:1771-1817` — Manual/automatic reservation schedules shared BackgroundTasks worker
- `platform/api/services/remote_execution/executor.py:1393-1445` — Collector waits for selected rsync without lifecycle polling
- `platform/api/services/remote_execution/transport.py:121-167` — Actual transport supports safe cancellation when coroutine is canceled
- `platform/api/services/remote_execution/transport.py:396-435` — Selected download delegates to owned transport with 3600-second default
- `platform/api/services/remote_execution/executor.py:1910-1954` — Post-transfer status checks and DB publication fence reject cancellation winner

**Acceptance:**
- Block a real controlled result transport in progress, cancel through the actual job API, and prove local writer termination/quiescence within the supported bound without waiting for full transfer/timeout.
- Exercise manual and automatic return, cancellation before and during transfer, and controller restart; never signal another job/attempt writer.
- When cancellation wins before import, assert no native publication/rows and prior good outputs unchanged. If publication committed first, preserve its terminal outcome rather than overwrite it.
- Do not reclaim staged bytes until supervisor quiescence is established; preserve received bytes and require explicit safe retry according to existing policy.

### R10 — Read returned failed-attempt artifacts

**Audit identity:** `RN-01`. **Classification:** confirmed producer/consumer mismatch and missing read integration. **Evidence qualification:** high: exact-body offline log consumer probe reproduces null logs; source proves archive/UI/root disconnect; no live API/browser acceptance claimed.

**Trigger:** A failed/cancelled remote attempt seals a manifest; explicit diagnostic pull succeeds and persists remote_diagnostics.state=returned. The ordinary job output directory has no corresponding returned diagnostic logs.

**Current failure:** The producer publishes to DATA_ROOT/remote-execution/diagnostics/<job>/<attempt>/<manifest> and leaves scientific output_dir untouched. GET job logs ignores remote_diagnostics.output_dir, reads only job.output_dir/_remote and reports remote_pending/null logs. The diagnostics UI renders the absolute pathname explicitly as not a download link. The normal file router accepts only named allowed roots, with no diagnostic archive authority/root in get_allowed_roots. Repository-wide remote_diagnostics searches found no alternative archive reader in API production code.

**Required outcome:** An explicitly returned, verified diagnostic archive is browsable/exportable from the authorized job surface, with current attempt/manifest binding and unchanged failed/cancelled scientific status; its returned logs are readable without worker access.

**Smallest correction:** Extend the existing job-scoped artifact/log authority with a verified diagnostic-archive locator bound to the persisted current attempt and manifest. Connect a manifest-listed browse/download action and job log reader to that locator. Reuse containment/hash/read controls; do not expose DATA_ROOT wholesale or move diagnostics into successful scientific outputs.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:144-161`.

**Published source:**
- `platform/api/services/remote_execution/executor.py:2005-2010` — Archive destination differs from scientific result tree
- `platform/api/services/remote_execution/executor.py:2087-2141` — Archive rename and returned provenance commit; native success importer not called
- `platform/api/services/remote_execution/executor.py:2032-2041` — Diagnostic projection is lineage-only
- `platform/api/routers/jobs.py:10175-10207` — Existing log consumer reads only job.output_dir/_remote
- `platform/frontend/src/components/RemoteDiagnosticsPrompt.tsx:68-78` — Returned UI shows controller pathname, no browse/export control
- `platform/api/paths.py:243-278` — Generic file roots and containment do not declare diagnostic archives
- `platform/api/routers/files.py:493-517` — Generic download requires an allowed path

**Acceptance:**
- Offline/API: return an archive with declared logs and a partial scientific file, remove worker access, list and download only declared current-identity files; compare hashes.
- GET job logs must return archived logs with returned diagnostic provenance, while job remains failed/cancelled.
- Mounted UI must offer archive/log access after return; foreign attempts, manifest changes, traversal and symlinks must fail closed.

### R11 — Read the current generation’s logs

**Audit identity:** `RN-02`. **Classification:** confirmed producer/consumer mismatch. **Evidence qualification:** high: exact-body consumer probe returns fixture prior-generation bytes with remote_returned; matching-root control returns current bytes.

**Trigger:** A remote checkpoint/component continuation returns successfully with native_output_directory beneath the original result root and both original-generation and current-generation logs retained.

**Current failure:** Worker selects the continuation output directory and writes _remote logs there. Host finalization stores the selected native root in child_output_dir and receipt.published_output_dir while preserving original envelope custody. GET job logs nevertheless uses only job.output_dir. If prior logs exist it returns those stale bytes and labels them remote_returned; otherwise it returns remote_pending even though current logs are local.

**Required outcome:** Job logs select the authenticated current native generation and label generation/attempt provenance accurately; retained history remains distinguishable and accessible without replacing sealed bytes.

**Smallest correction:** Use the same trusted result-generation locator for native log reads as native ingestion, with archive/current/history distinctions shared with RN-01. Do not rewrite original logs or infer current generation from an arbitrary supplied filesystem path.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:144-161`.

**Published source:**
- `platform/api/tools/bms_remote_worker.py:559-569` — Continuation envelope selects contained native output
- `platform/api/tools/bms_remote_worker.py:580-604` — Diagnostics are written beneath selected envelope output
- `platform/api/tools/bms_remote_worker.py:851-855` — Sealing invokes selected diagnostics then root transport manifest
- `platform/api/services/remote_execution/executor.py:2174-2197` — Host maps authenticated native_output_directory into local artifact root
- `platform/api/services/remote_execution/executor.py:2273-2276` — Current child_output_dir gets selected native root
- `platform/api/services/remote_execution/executor.py:2304-2318` — published_output_dir tracks selected local native output
- `platform/api/routers/jobs.py:10175-10207` — Log reader ignores child_output_dir and published native root

**Acceptance:**
- Publish distinct log bytes in generation zero and a contained continuation; after current generation import GET logs must return current bytes and current generation identity.
- With only continuation logs present, GET must not report remote_pending.
- Retained prior-generation logs remain readable only under explicitly selected historical identity; failed import must preserve prior good data.

### R12 — Qualify bootstrap helper Python

**Audit identity:** `PROVISION-MSA-02`. **Classification:** confirmed_defect. **Evidence qualification:** high (source/language compatibility); live worker reproduction not performed.

**Trigger:** An otherwise compatible Ubuntu/Debian NVIDIA worker has python3 resolving to Python 3.8 (or earlier accepted by command presence); bootstrap does not upgrade an existing interpreter.

**Current failure:** bootstrap_worker.sh checks command presence only for python3. finish_activation calls managed_inventory.helper_call(action=boot) before caching/activating the relocated support interpreter. helper_call executes the helper under bare python3; bms_managed_runtime.main ends every boot request with result | {boot_id: before}, requiring Python 3.9+ dict union. Python 3.8 therefore fails before the newer support interpreter can be delivered/used. Other helper features may demand a higher floor; this audit proves the 3.8 counterexample and does not certify 3.9 as sufficient. No old-Python interpreter or worker was run.

**Required outcome:** Bootstrap must establish a system helper interpreter compatible with all code it executes, or fail in the initial check with an explicit supported-version prerequisite before installation/projection work.

**Smallest correction:** Declare and enforce one minimum helper-Python compatibility contract at the existing bootstrap check. Use an already-supported package/install path when needed, or reject with an actionable message; ensure later helper/envelope invocations use the qualified interpreter. Do not introduce a new release-hosting/authentication prerequisite.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:92-110`.

**Published source:**
- `platform/api/services/remote_execution/bootstrap_worker.sh:17-26` — Ubuntu/Debian accepted without OS-version bound; python3 checked solely by presence.
- `platform/api/services/remote_execution/targets.py:557-595` — Bootstrap precedes boot helper; boot is required before critical asset transfer.
- `platform/api/services/remote_execution/managed_inventory.py:125-136` — The helper always runs under system python3.
- `platform/api/tools/bms_managed_runtime.py:603-627` — Even the minimal boot path executes dictionary union before returning JSON.
- `platform/api/services/remote_execution/transport.py:276-286` — Provisioning envelope also binds system python3 rather than support binding.

**Acceptance:**
- Run bounded bootstrap/helper tests with interpreter matrices including Python 3.8 and the selected supported floor. An old interpreter must be explicitly blocked before the first helper call or replaced through the authorized bootstrap path; action=boot, admit, install and provisioning run/quiesce must succeed on the supported interpreter. Then qualify a clean approved worker separately.

### R13 — Check the selected provider’s real state root

**Audit identity:** `PROVISION-MSA-03`. **Classification:** confirmed_defect. **Evidence qualification:** high.

**Trigger:** The machine/egress-qualified controller config has an absolute state_dir that is a regular file or is otherwise not writable, while the separate MSA cache and BMS_MSA_API_STATE_ROOT are valid.

**Current failure:** provider_readiness checks cache_root and BMS_MSA_API_STATE_ROOT. For ColabFold it calls validate_controller_config, which checks state_dir only for absoluteness; it never checks directory type/ownership/writability there. The returned configured=true can therefore coexist with an unusable outer-controller directory. prepare then mkdir/opens submission.lock in that different directory and fails before an API request. This is a configuration-validation omission, not evidence credentials or egress are missing on the live host. The AST-extracted validator probe demonstrates acceptance without any state_dir filesystem inspection.

**Required outcome:** A non-submitting readiness projection must include every state directory actually needed by its selected provider and report concrete unusable-root blockers, while retaining authentication and live acceptance as not checked.

**Smallest correction:** Reuse the non-mutating owned-directory/parent-readiness check for the validated controller config state_dir as well as existing roots. Report a sanitized actionable blocker; do not read keys, create directories, authenticate, or send provider requests from setup.

**Controlling specification:** `docs/specs/remote-bridge-1.0.md:72-74`; `docs/specs/remote-bridge-1.0.md:264-268`.

**Published source:**
- `platform/api/services/msa_provider_setup.py:101-150` — Readiness validates only cache and inner client state roots, then delegates config validation.
- `biomodstack_msa_controller.py:58-77` — Actual outer state_dir is validated only as absolute.
- `biomodstack_msa_controller.py:92-100` — Production preparation mkdir/opens the omitted root.
- `platform/api/routers/msa.py:42-46` — Public provider readiness surfaces this projection.

**Acceptance:**
- Provider readiness with valid cached/inner roots and outer state_dir as file, inaccessible parent, or disallowed link reports configured=false without writes/HTTP. A valid outer root leaves configured=true, authentication/live acceptance explicitly unverified.

## 5. SCOPE-01 — separate non-transport MD edits from bridge delivery

A read-only diff of pre-bridge published `e88feb458ccc438cf3c98004b5009089fbc6bb3c` against bridge publication `a3971bd9d5f2a21af071050702e40494fa07a4fb` contains placement-independent MD changes, not merely path transport:

- `scripts/bms_md/runner.py:27-74`: non-minimization MDP `dt` changes from fixed `.002` to `production.timestep_fs / 1000`.
- `scripts/bms_md/openmm_pipeline.py:81-108,147-148,182-207`: immutable preparation-copy handling, integrator timestep and checkpoint cadence change. The immutable preparation-copy hunk must be distinguished from scientific/control changes, not blindly reverted with the entire file.
- `scripts/bms_md/contract.py:197-214` and `platform/api/services/md/starting_structures.py:1653-1659`: MD checkpoint/admission handling changes, including placement-independent rejection of `neutralize=false`.

**Qualification:** reviewed typed GROMACS profiles fix `timestep_fs=2.0`, and typed admission enforces the profile; the timestep diff does not establish a numerical difference for those fixed-profile launches. The prepared-system schema permits other positive timestep values (`schemas/md_job_v1.schema.json:226-238`), so the change is not proven globally inert. No numerical or live scientific regression is asserted.

**Required disposition:** before rectification publication, remove/separate bridge-origin non-transport hunks from this delivery or cite their separate explicit owner approval. Do not implement new physics, weaken chemistry admission, blindly revert whole MD files, or undo unrelated subsequent Development fixes. Retain independently necessary immutable-input/output custody changes. Record the hunk-level disposition in the implementation diff; do not use this audit to broaden bridge scope.

**Acceptance:** bridge diff contains only the approved transport/orchestration/admission/result corrections and separately approved science. Existing typed and prepared GROMACS request/rerun behavior remains covered. Raw historical diff: `published-md-nontransport-delta.patch` in the evidence directory.

## 6. TEST-01 — repair regression consumers without weakening production

The failing test count is **not** a count of production defects. Current published regression fixtures have not all migrated to the newer shared interfaces:

| Failure group | Cases | Proven cause / required fixture correction |
|---|---:|---|
| Bundle path/runtime fixtures | 13 | Hand-built selected plans omit reviewed descriptors/dependency/artifact authority. Use real compiler metadata or a faithful bounded plan for the layer being tested. |
| Independent provisioning catalog | 1 | Test still asserts Boltz2 must be absent, but current catalog intentionally includes it. Update the contract expectation. |
| Independent provisioning runtime selector | 1 | Test invokes `_runtime_assets` without its required matching selected plan. Use the actual selected authority. |
| Live staging producer fixture | 1 | Admission mock lacks the production `resource_monitor` keyword, failing before its staging event. Update the double and join/cleanup failed producer tasks. |
| After-rename diagnostic recovery fixtures | 3 | Test replaces producer provenance with a short receipt missing `remote_attempt_dir`; real receipt producer supplies it. Reproduce failed/cancelled/lost manifest and terminal identity faithfully, while retaining missing-authority rejection. |

Sources: `platform/api/tests/test_remote_bundle_runtime_gaps.py:153-165`; `platform/api/tests/test_remote_cache_integration.py:20-29`; `platform/api/tests/test_remote_independent_provisioning.py:53,145`; `platform/api/tests/test_remote_lifecycle_authority.py:123-158`; `platform/api/tests/test_remote_return_policy_recovery.py:145-155`; `platform/api/services/remote_execution/executor.py:310-372,2032-2084,2174-2177`.

All observed failures have a source-backed first-failure disposition; the formerly unreachable downstream assertions remain unqualified until rerun. Correcting a fixture is not evidence its entire test now passes. Do not remove production plan, identity, containment or quiescence checks to get green tests.

**Acceptance:** migrate direct/aliased/thread-forwarded mocks and shared fixtures once, rerun these exact cases and their dependants on one frozen candidate; preserve original failed records. Add defect-specific real producer→consumer integration cases for R01–R13. Use independent physical DB connections for races and separately qualify the deployed database engine/HTTP serialization. Native-recorder tests must use actual compiler argv, not test-only science/settings/library repairs.

## 7. Configuration prerequisites — not missing source implementations

### CFG-01 — attach an approved worker

The audited live `GET /api/execution-targets` returned an empty list. This proves no worker is registered in this Development instance, **not** that Christian has no owned/rented instance. Reuse/discover an existing approved target and working credentials before asking for new instances or authentication. With applicable live-use approval, attach and qualify pinned critical runtime plus the selected workflow’s dependency closure; do not start science merely to provision. No implicit rental or start.

### CFG-02 — configure the explicitly selected provider

The live default is `colabfold_api`, with a blocker requiring `BMS_MSA_CONTROLLER_CONFIG` and qualified single-egress configuration. Supply that existing controller configuration for ColabFold, or have the operator explicitly choose the configured Neurosnap provider for a compatible new request. Do not silently mutate saved/queued jobs or use alternate providers as fallback.

Neurosnap setup reports existing credentials, no configuration blockers, `authentication=not_checked` and `live_acceptance=not_checked_by_setup`. Reuse the credential; do not claim authentication failure, demand a replacement key, or claim successful provider/model acceptance without the bounded live check.

The provisioning API returns 16 image/model selections representing 8 distinct model IDs, not 16 independently verified scientific runtimes. Live read-only evidence: `live-readonly-state.json` and `operational-state-report.json`.

## 8. Unresolved verification questions — not confirmed defects

These questions remain visible because they were not fully qualified. They are **test/trace obligations**, not permission to implement speculative replacements. Promote a concrete failure to the ledger only with a reproduced trigger and minimal correction; otherwise close the question with evidence.

- **U01 Diagnostic power-loss durability:** diagnostic rename/DB publication lacks the same explicit fsync sequence used by successful generation publication. Test the actual storage boundary; no observed byte loss is claimed. `platform/api/services/remote_execution/executor.py:2087-2141`; `platform/api/services/remote_execution/result_generation.py:211-227` (both under `platform/api/services/remote_execution/`).
- **U02 Completed-generation restart cleanup:** determine whether a crash after DB commit but before journal cleanup is discovered for a completed, unleased job; pure journal repair already works. Prove discoverability and impact before changing it. `platform/api/services/remote_execution/executor.py:1943-1946`; `platform/api/services/gpu_orchestrator.py:3320-3343`.
- **U03 Prepared-start admission freshness:** ordinary launch refreshes capacity; prepared restart recovery takes a different lane. Exercise changed physical devices/capacity before recovered start and prove no stale admission. `platform/api/services/remote_execution/executor.py:639-646,1646-1670`. No capacity corruption is asserted.
- **U04 Waiting MSA-controller cancellation:** the outer controller uses blocking `flock`; test a queued second operation’s cancellation and actual thread join without touching the active operation. `biomodstack_msa_controller.py:98-100`; `platform/api/services/nextflow.py:2156-2169`.
- **U05 Provider-rejection versus ambiguous-submission recovery:** verify supported provider throttle/rejection states and allowed retry policy against the existing operation/ticket authority. Never turn an ambiguous POST into a blind duplicate submission. `biomodstack_msa_api.py:492-493,533-576`.
- **U06 Native selected-gate/publication ordering and import effects:** run actual PPIFLOW/native gate ordering and all selected native import consumers with pre/post package identity and reopen checks. Source concern is not a proved race or byte rewrite. Do not treat the disabled legacy local-MSA manifest writer as a required supported producer.
- **U07 Source-owned scientific fixture paths:** `_input_assets` skips code-root paths while typed reference discovery uses managed input roots. Determine whether any existing supported caller relies on source-owned biological input; otherwise keep managed-input restrictions. Do not broaden allowed roots speculatively. `platform/api/services/remote_execution/bundle.py:449-555`.

## 9. Implementation sequence and closure gates

Use the shared authorities already present. Packages express dependencies, not dates or a percentage estimate.

1. **WP0 — publication scope and test baseline:** dispose SCOPE-01; migrate TEST-01 fixtures without weakening authority. Preserve this published snapshot and raw failed evidence. This makes future regression results interpretable; it is not a prerequisite to reading/reproducing defects.
2. **WP1 — placement and input boundaries:** R01, R02, R04 and R05. Use shared selected-plan/input/custody authorities. Test pure/read-only preview before admission and exact native argv after relocation. R05’s final service-state contract must agree with WP2.
3. **WP2 — external-service boundary and setup:** R03, R12, R13. Wire generated native candidate inputs through the existing MSA service contract, not a new workflow scheduler. Resolve U04/U05 here where feasible; configure CFG-02 only for the explicitly chosen provider.
4. **WP3 — durable control:** R06, R07, R08 and R09. Reuse existing intent, operation identity, start fencing, leases and transport supervisor. Test lost responses, rejection and process crashes with independent sessions; old evidence must never authorize a successor.
5. **WP4 — native reads and durable return:** R10/R11 using one trusted current/history/diagnostic locator. Resolve U01/U02 with shared publication machinery only if demonstrated necessary. Retained failure artifacts and current scientific outputs remain separate.
6. **WP5 — integrated qualification/publication:** one exact candidate source/runtime identity; affected local and remote workflow acceptance, native downstream reopening, then the approved Development publication path. Recheck changes since this audited SHA rather than overwriting newer Development. No ad-hoc direct edits to the running service.

A finding closes only when its listed acceptance succeeds through the real entrypoint/consumer and evidence identifies source/runtime, selected workflow/settings, result identity and observed behavior. Unit/AST tests may prove a branch, not a whole workflow. Reusing existing runtime/cache objects must be demonstrated through actual consumers; selector tests alone do not certify the clean-worker path.

## 10. Live acceptance still required

Perform only after relevant code corrections and applicable explicit live-use/provider approval. Prefer existing owned resources. Tests can share runs when their source, workflow and boundary coverage genuinely coincide; do not add redundant validation loops.

- **A01 Clean worker and independent provisioning:** a compatible worker without preexisting BMS SIF/cache/home tools; pinned release, support Python, Nextflow and selected immutable images/weights installed through the existing authority. Restart/reboot inventory is honest; no science or biological staging during provisioning. Qualification is not merely a healthy API or catalog response.
- **A02 Existing simple and native multi-input workflows:** exact compiler-produced invocation, selected assets only, distinct host/worker roots; single/batch/complex inputs as supported; full returned native files/identity and existing BMS read/export/analysis. Include a no-MSA control so provider setup is not confused with transport correctness.
- **A03 Explicit provider contract:** separately bounded ColabFold/Neurosnap as selected and approved; real submit/ticket/poll/download, query/chain/pairing/settings/cache identity, native consumer and meaningful authentication/throttle/cancellation behavior. Also exercise generated-candidate delivery. Do not claim providers are interchangeable.
- **A04 Existing hierarchical science:** full GROMACS preparation/replicas/aggregation/required analysis/completion; actual selected Frustra grouped and singleton child paths in protein design/antibody and mandatory CM; required/optional joins and retained artifacts unchanged. Other advertised in-scope stages obtain rows in the same coverage ledger, not new bespoke adapters or science.
- **A05 Review and ownership/recovery:** native local-redesign/PPIFLOW supported gates with explicit artifact-bound review/continue; cancel delivery loss, rejected continuation, missed first running receipt, controller restart, SSH loss, worker reboot, interrupted return and two-target lease isolation. Do not auto-approve decisions or restart science from ambiguity.
- **A06 Return and downstream use:** manual default causes no unsolicited pull; explicit automatic policy uses the same custody path. Reopen/download/analyze the returned native result after worker access is removed. Exercise current-generation versus historical logs, separately returned failed/cancelled/lost artifacts, corrupt/missing packages, retry/clone, and existing downstream workflows. Failed scientific verdicts/partial native outputs remain distinguishable from transfer success.

The existing spec’s full acceptance obligations remain, bounded by the explicit scope exclusions above. No live workflow/provider acceptance was performed during this audit; no remote instance was started or rented.

## 11. Audit verification record
- Main published-source regression: **516 cases — 489 passed, 19 failed, 8 skipped, 0 errors**. All failures were traced to the first failing fixture/interface contract as documented in TEST-01; corrected downstream tests have not been claimed passed.
- Supplemental run: **8 previously skipped real Nextflow DSL handoff cases passed**, using the existing pinned Nextflow 25.10.1 JAR and explicitly labeled scientific/provider doubles. No live inference or hosted request. Case identity reconciliation yields **497 passing and 19 failing latest outcomes across the same 516 cases**, with no remaining skips. This is not an all-project suite.
- Parent independently reran exact-source offline probes reproducing unselected runtime-input rejection, Frustra task-work containment failure, cancellation redelivery absence, checkpoint generation dead end, missed boot receipt and wrong diagnostic/log consumers. Positive portable-binding/tamper controls passed. These use task-owned files and explicit infrastructure doubles—not live DB/HTTP/process/scientific acceptance.
- Automated source-reference verification: **341 range records across 114 immutable source files** checked against the audited Git bytes. Reported reviewer read ranges were merged/deduplicated mechanically; these counts are audit coverage bookkeeping, not a claim that every file or parameter combination was exhaustively reviewed.
- Raw JUnit/logs and reproducible runners: `offline-regression/`, `offline-nextflow/`, `run_offline_regression.py`, `run_offline_nextflow.py`. Bounded source probes and five area reports are in the evidence directory.
- Machine-readable authoritative ledger: `rectification-findings.json`. Source range/hash validation: `source-reference-verification.json`. Inventory: `workflow-inventory.json` and `workflow-mode-inventory.csv`.

## 12. Catalog reconciliation and exclusions

The mechanical inventory covers **30 model definitions, 68 declared mode rows and 10 compiler-only alias/internal rows**. These are declaration/routing counts, not runnable or accepted scientific coverage. Exactly named rows and current classifications are in the companion CSV/JSON. Of the declared rows, 51 have a native route but are not live-qualified; all other classifications remain separate.

- Disabled/retired declarations and trusted-parent internal operations are not public standalone bridge promises. BindCraft is retired; antibody de novo is not.
- ProteinHunter is rejected both in pre-bridge published source and this baseline. Standalone LigandMPNN declarations lack a pre-bridge functioning native route. Neither becomes new required scientific development under this spec.
- The generic ProteinMPNN multichain guard is not established as a regression of a working pre-bridge generic contract; existing antibody/native multichain paths must remain. Do not create a new general multichain contract to close the bridge.
- Explicitly unsupported native RFD3 interactive/sequence combinations and opaque custom-runtime configurations must be represented honestly, not silently enabled or counted as accepted.
- Oligo/new OpenMM/MMGBSA and unrelated new workflows remain excluded. Confirmed cross-cutting bridge faults apply to existing admitted workflows where their triggers occur; catalog exclusions do not excuse those faults.

## 13. Handoff and change control

This spec is a new documentation artifact in the audit worktree; executable source and the running deployment were not modified. Implementation must carry the R01–R13, SCOPE-01 and TEST-01 disposition alongside exact acceptance evidence. CFG prerequisites, unresolved questions and live acceptance remain separately labeled. Never report a defect fixed because it is hidden, a failed fixture green because its assertion was removed, or a workflow usable solely because a route returned HTTP 200.
