# BMS Remote Bridge 1.0 — final specification

**Status:** Final 1.0 product/design baseline, incorporating Christian's directional approval and explicit interim MSA decision. This is a specification, not a claim of implementation, deployment or acceptance.

**Approved direction:** one shared local/remote component runtime; consolidation of FrustraMPNN orchestration; legacy dynamic-child workflows brought into the common standard rather than accommodated through bridge exceptions; checkpoint-based review with explicitly authorized review-artifact retrieval; complete worker-side science and native host integration. Local MSA search is disabled in this specification. ColabFold API is the interim search backend; long-term MSA infrastructure is outside this release's design decision.

**Change control:** preserve scientific settings, canonical runners, required analysis and provenance. Changes to scientific grouping, requiredness, partial-failure policy or accepted model combinations must be explicitly reviewed, not hidden within orchestration cleanup. No production configuration or scientific data is changed by finalizing this document.

**Baseline reviewed:** `5964d755740458b866ef1ef293a420980245904d`, `wt-remote-worker-complete`. This is a source-review baseline, not the live service revision. Evidence: `/home/dalab/bms-remote-bridge-1.0-review.md` and its detailed reports.

## 1. Product objective

Local BMS is the control, operator-interface and durable data-management host. On-demand remote instances supply execution capacity. Operators select and provision remote capacity, submit complete existing BMS workflows, observe their progress, and retrieve outputs into normal BMS data/workbench surfaces as smoothly as local execution.

Nextflow remains the DAG/process execution engine. Apptainer and existing scientific runners remain the model execution environments. Remote placement must not introduce a second scientific implementation, settings schema, batching policy or reduced result viewer.

## 2. Scope and non-goals

### Required for 1.0
- Version-pinned critical worker runtime bootstrap and reconciliation.
- Observed per-worker image/model/weight inventory and explicit readiness.
- Workflow dependency provisioning and independent image/model provisioning.
- Shared execution-plan/compiler contracts for local and remote placement.
- Worker-capable component coordination without mandatory workstation callbacks during computation.
- Multiple attached workers with independent placement/resource ownership.
- Durable progress, failure, cancellation and restart/reboot reconciliation.
- Complete relevant output retention, manual-default retrieval, opt-in automatic retrieval and lossless native BMS integration.
- Exact-release local/remote conformance and real remote acceptance.

### Outside 1.0
- Additional cloud providers beyond Vast.
- Advanced transfer minimization, cache-locality optimization, peer-to-peer distribution and speculative prefetching.
- Cross-worker splitting/migration of a single running DAG; baseline placement assigns a whole workflow attempt and its computational descendants to one worker. Independent workflows can use different workers.
- Automatic purchasing/destroying of instances or price bidding. Existing owned/rented instances are discoverable and attachable; paid lifecycle automation requires separate explicit scope.
- Universal checkpoint restart where the underlying scientific runtime does not support it.

Transfer integrity, safe reuse/reclamation of interrupted transfer data, disk budgeting and crash recovery are baseline correctness, not deferred optimization.

## 3. Non-negotiable invariants

**INV-01 Scientific authority:** Existing global typed model settings and native runners are authoritative. Requested and effective values, seeds, candidate identity/order, grouping, requiredness and native output contracts must survive placement, retry and clone. No hidden setting drops or remote-only scientific defaults.

**INV-02 Shared plan:** Preview, provisioning, local/remote execution and result validation consume one versioned plan authority. Different projections of a plan may be used, but no independent remote recipe compiler.

**INV-03 No fallback:** Missing dependencies, incompatible capability or unavailable remote capacity must block/wait explicitly. They must not silently run science locally or skip required stages.

**INV-04 Autonomous computation:** Loss of the host control connection must not stop an already-running noninteractive workflow. Required component scheduling and result resolution run on the worker. Selected external scientific services remain explicit dependencies; no-host-callback does not mean no-network.

**INV-05 Interactive authority:** Human decisions never receive automatic approval to make a workflow autonomous. Interactive workflows pause durably and await an explicit decision. Gate decisions bind the checkpoint and selected artifact identities. Scientific behavior changes remain subject to change control.

**INV-06 Retained results:** Releasing compute ownership does not delete outputs. Destruction or cleanup with unreturned outputs must warn/block unless separately authorized.

**INV-07 Honest state:** Provider running, execution-runtime ready, artifact present, verified artifact, model ready, science complete, bytes returned and native import complete are distinct states.

## 4. Shared registry and execution plan

Extend existing model/workflow definitions, rather than create a remote-only catalog. New metadata is system-owned and references existing scientific schemas.

### Plan contents
- Schema version, immutable plan digest and source/release identity.
- Canonical workflow, mode and selected conditional components.
- Full requested/effective settings and their digests; model/runtime contract versions.
- Static DAG plus permitted typed dynamic component templates/expansion rules. A future candidate ID need not be known at preview, but its generating rule and identity contract must be bound.
- Immutable dependencies: SIFs, external weights, reference databases, helper Python/tools, critical runtime and compatibility requirements.
- Typed input/output references, required output categories, semantic owners and publication rules.
- Per-component CPU/RAM/GPU/scratch requirements, concurrency/grouping rules and aggregate admission constraints.
- External service requirements; cancellation, failure, retry, checkpoint and interactive-gate policies.
- Result validation/import contract and retrieval policy identity.

### Compilation and admission
- Compile from the existing validated request; do not reconstruct scientific authority heuristically from shell argv.
- Preview reports effective science, dependencies, missing/incompatible assets, estimated transfer/storage needs, selected placement and blockers.
- Launch/provision mutations bind the approved preview/plan digest. Changed request, source or meaningful plan invalidates preview.
- Unknown/unreviewed entrypoint/mode/component combinations fail before queue insertion; launch revalidates capability.
- A temporary explicit unsupported status is a safety boundary, not fulfillment of all-workflow 1.0 coverage.

### Artifact references
References carry logical ID, owner/job/component lineage, role, digest, size, format/schema and a contained relative path or immutable object reference. Local and worker bindings are separate from scientific identity. References nested inside manifests must be resolved through typed adapters, not global text replacement. Archived native bytes remain immutable; legacy absolute paths require validated derived bindings, not silent manifest rewriting.

## 5. Shared component runtime

Provide a lightweight interface for compile/validate, resolve/materialize artifact, submit component, await/join components, cancel, allocate/release resources, emit progress and seal results.

Local and worker adapters call the same scientific compiler/runners and shared candidate/grouping logic. The worker holds a durable attempt-local component ledger; the host projects logical children and events into its own database. A host database row is not a prerequisite for a worker to execute its next required component.

Nextflow owns task execution. The component coordinator owns logical component identities and dynamic child lifecycle where needed. Resource ownership must prevent competing parent/child schedulers and double allocation. A parent reservation may be subdivided locally; descendants cannot escape the chosen worker without an explicit future policy.

Do not install a duplicate full interactive BMS server/database merely to imitate host HTTP callbacks. Do not replace scientific subworkflows with generic shell execution supplied by the browser.

**Integration boundary:** Approved orchestration migrations are listed in section 13. Implementations must document and test local/remote parity; authorization to standardize orchestration does not authorize unreviewed changes to science.

## 6. Worker bootstrap and inventory

### Critical runtime release
A versioned release manifest declares runner/coordinator, Nextflow, support Python, Apptainer compatibility, OS/architecture/driver constraints, artifact digests and authorized acquisition sources. Bootstrap verifies content before atomic activation; incomplete releases never become ready. Preserve prior installed identity for rollback/recovery where feasible.

**Owner clarification:** retain on-command host-to-worker push through the existing working authentication as the current provisioning direction. No authentication redesign or new release-hosting prerequisite is authorized. Continue using the existing transfer and runtime lifecycle authorities, with digest verification and pinned identities. Worker-side acquisition from other approved immutable sources remains distinct from host push; it must not be invented or used to block implementation of the selected push path. This clarification does not waive readiness, complete dependency provisioning, or autonomous computation after setup.

Reboot reports boot identity and desired/observed runtime state. Reboot does not implicitly upgrade code, restart science or certify readiness.

### Inventory API/dashboard
For every worker show:
- Provider identity/state, connectivity, boot identity and observation freshness.
- Critical runtime desired/observed release and compatibility blockers.
- Images and external weights/databases by semantic release and verified digest.
- Missing, partial, corrupt, incompatible, unverified and ready states.
- Current provisioning/execution/return operations and retained outputs.
- Resource availability and reservations.

Image presence alone cannot certify a model with missing external weights. Stale observations must be labeled, not displayed as current readiness. Scientific readiness checks are bounded and explicitly distinguished from actual scientific inference acceptance.

## 7. Provisioning experience

Two entrypoints use the same plan/compiler:
1. Select a workflow/request and provision its full dependency closure without launching it or requiring an existing saved Job.
2. Select individual catalog images/model releases and provision them independently.

Both offer exact dependency preview, destination, missing assets, storage/transfer estimate and blockers. Provisioning does not stage biological inputs, run inference or pull outputs. Arbitrary browser paths, URLs, runtime digests and shell commands are not accepted acquisition authority.

Operations persist per-artifact status and actual progress. Completed verified cache objects survive retry. Cancellation stops underlying transport before releasing ownership. Restart reconciles completed/incomplete artifacts and exposes explicit retry/recovery. No fake percentage/ETA or filename advances ahead of real activity.

## 8. Multiworker placement

Multiple workers may be attached and ready simultaneously. Operators can choose a worker or an eligible pool; deterministic fit-based placement considers required capabilities and GPU/CPU/RAM/scratch reservations. Cache-aware cost optimization is deferred.

Every workflow attempt is bound to one target, source/runtime/plan and lease generation. Independent workers can run independent workflows concurrently. Initial conservative serialization within a worker is acceptable only when surfaced honestly and sufficient for declared resource requirements; no claim of fine-grained concurrency without evidence.

Provider endpoint/host identity, boot identity and lease ownership are checked independently. Detach/stop recovery must not strand a leased attempt behind ordinary admission restrictions. Preserve uncertain-start fences: ambiguous transport failure must not issue duplicate science.

## 9. Progress, cancellation and recovery

Durable events include operation/component IDs, monotonic sequence, phase, actual artifact or batch, byte counters when available, and terminal reason with secrets excluded. Host reconnect projects missed events idempotently. Telemetry is advisory, never scientific-success authority.

Handle separately:
- Host/API restart: reconcile operations without duplicate submission/import.
- SSH loss: continue worker science; restore control observation.
- Worker reboot: detect process/boot epoch change, mark lost/interrupted honestly, retain data; checkpoint resume only under supported explicit policy.
- Provider replacement/host-key change: stop admission and require identity resolution, never bypass trust checks.
- Cancellation: durable intent, bounded process termination, terminal diagnostics, no release while predecessor can still mutate shared state.

## 10. Output return and native integration

### Completeness
The shared result contract defines required scientific artifacts, structures/sequences/arrays/trajectories/alignments where applicable, provenance, component receipts and relevant logs. Each model retains its native schema and shared native importer. Task diagnostics must be intentionally published with secret filtering; do not sweep arbitrary caches or all work directories.

Worker seals a hash-bound immutable manifest after required semantic checks. Byte completeness alone is not scientific success. Failed/lost attempts have a separately labeled diagnostic sealing path after proving former owners cannot mutate the data.

### Retrieval policy
Default: manual pull. Optional: explicit persisted automatic return after successful completion. Browser and agent use the same typed policy. Scope is per-job, with an operator-selected default for new jobs; clone/retry must display the policy and never infer opt-in from browser polling. Changing/revoking policy cannot erase an already completed transfer; in-flight cancellation uses the normal safe cancellation path. Failure diagnostics remain explicit unless a separately visible diagnostic policy is approved.

Automatic and manual return call the same attempt/manifest-bound transfer/import pipeline. Background policy execution belongs to the controller, not a mounted browser effect.

### Durable transfer/publication/import
- Persist transfer identity tied to attempt/manifest and destination.
- Safely reuse compatible partial bytes or reclaim them; never silently accumulate unreachable staging.
- Verify complete declared files, hashes, containment and required artifact categories.
- Stage validation before visible publication where possible; use durable generation/publication journal and restart reconciliation for filesystem plus database transitions.
- Repeated import of one result identity is idempotent; no duplicate scientific rows or repeated quarantine chains.
- Preserve prior good data if new import fails. Make received-but-unimported distinct and retryable without rerunning science.
- Native BMS views, downstream workflows, exports, analysis and provenance must work without remote-path dependencies.
- Verified diagnostic archives must be browsable/exportable through normal authorized BMS surfaces without changing scientific failure to success.

## 11. Security and compatibility

Preserve provider ownership checks, strict SSH host identity, server-owned secrets, digest verification, containment, source/attempt fences and safe cache materialization. Workers receive only credentials needed for approved acquisition/services, not general host administrative authority. Logs/events/artifacts must not expose secrets.

Version every contract and declare host/worker compatibility. Incompatible historical results remain preserved and explicitly blocked or handled by a tested migration/import adapter; upgrading BMS must not silently reinterpret archived science. Inventory and results are not deleted by routine release reconciliation.

## 12. Acceptance and completion

Required evidence matrix covers every advertised workflow/mode/selected-stage combination, with approved workflow adaptations tracked separately. No all-workflow claim from a small transport fixture suite.

- Clean worker: critical bootstrap, inventory and independent image/model provisioning agree with actual bytes and compatibility.
- Shared-plan conformance: same local/remote scientific settings, effective arguments, identity, component dependencies, candidate ordering/grouping and native contracts. Use scientifically justified tolerances for nondeterminism, not unsupported byte-identical predictions.
- Real complete workflow: exact deployed code/runtime, no required workstation callback, complete outputs and native import/downstream usability.
- Two workers: concurrent independent workflows with isolated ownership and no local fallback.
- Manual return: no scientific transfer before authorization; auto return: explicit policy works without browser presence.
- Corrupt/missing assets, insufficient resources, failed model, cancellation, interrupted provisioning/pull, API restart, SSH loss and worker reboot all have tested state/data preservation.
- Publication crash injection before/between/after file and DB transitions recovers to an unambiguous visible generation.
- Lost/failed diagnostics remain recoverable and never imply scientific success.
- Existing local execution remains regression-tested; no remote migration changes local science silently.

1.0 is complete only when the agreed coverage matrix is accepted. Narrowing scope requires an explicit owner decision, not relabeling blocked workflows as supported.

## 13. Workflow adaptations and safeguards

The shared-runtime approach and modernization of legacy child workflows are directionally approved. Source evidence is recorded in `/home/dalab/bms-1.0-workflow-change-proposals.md`; its older approval labels are historical and superseded by this specification.

- **Shared FrustraMPNN:** converge local/remote submission, preparation, candidate ordering/grouping, joins, receipts and artifact resolution. Reuse canonical inference. Establish one scientifically authoritative grouping contract, with any intentional change reviewed explicitly.
- **Protein design:** include BoltzGen child orchestration and direct database-ingestion removal from the worker computation path, not only FrustraMPNN.
- **Antibody de novo:** bring legacy spawners/waiters, maturation/validation, annotations and gates into the shared standard. Necessary core orchestration refactors are allowed; do not reproduce legacy bad practices as bridge compatibility requirements. Required computational/review annotations remain worker-produced.
- **MD:** preserve preparation → replicas → aggregation → mandatory analysis → completion, including replica/seed identity, exact-set joins and native analysis contracts. Replica-only execution is not completion.
- **Local redesign / PPIFLOW / other gates:** retain a durable logical checkpoint rather than an indefinitely waiting process. Explicitly retrieving the declared review set registers it for native review without treating the full workflow as complete. Selection and continuation are bound to checkpoint identity. Release unused compute reservations after safe quiescence; retain worker storage and reacquire compatible resources for continuation. This does not automatically stop a paid instance or migrate checkpoints across workers.
- **MSA:** portable artifact references remain necessary, but computation policy is governed separately by section 16. No local-search fallback.

Scientific settings, runners, analysis, filters and result contracts remain authoritative. Existing collectors that skip missing children require an explicit required/optional result contract; do not silently change accepted partial-failure semantics. Host-only DB writes become native host projection after authorized return; worker-required computation cannot be deferred as UI metadata.

All advertised modes/stages require a coverage row and acceptance evidence. An unreviewed workflow is not implicitly supported because it avoids a known callback or filename denylist.

## 14. Implementation work packages

A. Formalize registry/plan/artifact/runtime contracts and capability preview/admission.
B. Implement/test shared coordinator and placement adapters against isolated conformance fixtures.
C. Implement release acquisition/bootstrap, observed inventory and independent provisioning surfaces.
D. Implement multiworker routing/leases and boot-aware reconciliation.
E. Implement durable return/import/diagnostics and typed auto-return policy.
F. Integrate the approved shared-runtime workflow adaptations under section 13 safeguards; qualify every advertised workflow/mode and perform exact-release live acceptance.
G. Disable local MSA search and implement the explicit ColabFold-only interim policy, admission, provenance and failure tests in section 16.

These are dependency-oriented work packages, not delivery dates. Work may overlap where contracts are stable. Integration must use the approved development/release path; no direct production edits or deployment implied by this specification.

## 15. Engineering decisions and release evidence still required

The product scope is finalized; the following are implementation/acceptance prerequisites, not reasons to invent behavior:
- Host-to-worker push with existing authentication is the selected current provisioning path. Additional release hosting/direct-download credentials are not an open prerequisite for that path; no changes are authorized merely by the earlier direct-pull discussion.
- Establish the complete advertised workflow/mode denominator and exact-release coverage evidence.
- Document intentional scientific grouping/failure-semantic changes for separate review.
- Verify provider lifecycle/storage behavior before offering destructive actions.
- Resolve compliant ColabFold API admission/egress for multiple workers before enabling concurrent MSA requests; see section 16.

## 16. Interim MSA policy — ColabFold API only

### Scope
**Disable local MSA search across supported BMS UI, typed API, preview, scheduler and runtime selection.** Do not provision or launch local MMseqs2/search database pipelines on the host or rented BMS workers as an implicit alternative. Long-term MSA servers/database infrastructure will be designed separately. Existing local database files are not deleted, and historic results/settings remain readable.

ColabFold API is the sole enabled newly generated MSA search backend for 1.0. It is an external scientific service dependency, not a worker-to-workstation computation callback. Existing user-supplied or already-generated, identity-verified alignments are input artifacts, not local MSA search; retain their supported use. An explicitly chosen and model-supported no-MSA mode remains distinct. Never silently disable MSA when search fails.

### UI/API/migration
- Label the backend “ColabFold API — external service”; remove or disable local-search controls with an explanation.
- Requests explicitly selecting local search fail validation with an actionable message. Saved jobs/clones that request it require an explicit re-preview/change to the API backend; no silent scientific mutation.
- An `auto` selector, if retained for compatibility, resolves visibly to the sole enabled API backend and records the effective backend before launch. It cannot invoke local search.
- Record sequence/input identity, service identity, settings and pairing mode, request/result digests, retrieval time and available database/version metadata. Unknown provider database versions remain unknown rather than fabricated.
- Surface third-party sequence disclosure and provider availability/rate-limit constraints before submission. Do not route private sequences to another provider without explicit approval.

### Operational behavior
Treat submit/poll/download/validate as durable external-service operations, with bounded retries, backoff, provider ticket identity and cancellation semantics. API outage or throttling creates a visible retryable blocked state; no local fallback, alternate-provider fallback, changed pairing or dropped MSA stage.

**Public-service usage constraint:** ColabFold's upstream README asks for serial queries from a single IP and says not to query from multiple computers. Multiple BMS workers must not independently fan out requests or rotate addresses. A compliant single submission/egress authority or explicit server-operator arrangement must be established and tested before fleet MSA is enabled. Merely adding one rate limiter per worker is insufficient. This authority must not become mandatory host callbacks for every scientific component; the implementation must define the MSA preparation/service boundary and typed artifact delivery explicitly. Until resolved, block noncompliant submission rather than bypass provider restrictions.

MSA artifacts required for downstream inference must be delivered to the assigned worker as pipeline inputs, independent of final-result pull policy. Host result retrieval remains separately authorized. Paths in manifests must use typed portable references; reverse-path string replacement is not scientific identity.

### Acceptance additions
- Local selection rejected through browser/API/saved-job replay and scheduler/runtime paths; absent local databases never trigger installation.
- API request settings and alignment/paired-chain identities preserved into the scientific component receipt.
- API success, throttle, timeout, cancellation and controller/worker reconnection produce coherent state without duplicate submissions where provider ticket identity allows recovery.
- Verified existing alignments remain usable; explicitly supported no-MSA modes remain explicit.
- Fleet admission honors current public-service policy; no multi-IP evasion or parallel-worker submission.
- Downstream execution uses portable MSA artifacts and succeeds with different host/worker roots.

### Alternatives researched, not selected or integrated

**Scope amendment approved by Christian:** Neurosnap/keyed MSA API integration is now part of the bridge work package alongside the currently enabled ColabFold API. This supersedes the earlier deferral, not ColabFold's current operating policy. Section 17 defines the bounded extension. Paired-MSA and scientific output compatibility remain unvalidated; inclusion in scope is not provider acceptance or authorization for an unbounded paid evaluation.
- **Tamarind Bio:** hosted MMseqs2 MSA offering; its tool description says searches run in Tamarind's environment rather than an external server. Evaluate API access, database/pairing/output compatibility, pricing, terms and privacy before any adoption.
- **Neurosnap:** advertises MMseqs2 MSA generation through its API. Evaluate database versions, pairing, retention/privacy, quotas and contract compatibility; it is not certified as a ColabFold drop-in here.
- **NVIDIA MSA Search NIM:** documented deployable API server software. Self-hosting is a long-term infrastructure candidate, not a confirmed public hosted replacement in this review.

Sources: https://github.com/sokrypton/ColabFold (README usage constraint); https://app.tamarind.bio/tools/msa ; https://docs.tamarind.bio/tasks/structure-prediction ; https://neurosnap.ai/service/mmseqs2+MSA+Generation ; https://docs.nvidia.com/nim/bionemo/msa-search/2.5.0/api-reference.html . Public documentation/search evidence only; no provider account, billable request, sequence submission or integration test performed. Dynamic pages were not all directly extractable; hosted claims are attributed, not acceptance-certified.

## 17. Keyed MSA API extension — in scope, not yet accepted

Add Neurosnap as an explicitly selected keyed provider alongside ColabFold using the existing controller preparation and portable-input handoff. Do not redesign worker orchestration, silently replace the current provider, or re-enable local search. References above to ColabFold-only describe the currently enabled implementation; this section expands the implementation scope.

- Discover the provider's actual API/service schema, authentication, supported database/pairing controls, native output and operation lifecycle from authoritative evidence; do not invent endpoint or compatibility claims.
- Add the provider through the existing supported configuration/setup interface, with server-owned credential references and human/agent setup parity. Readiness reports missing credentials/runtime or unsupported capabilities without exposing secret values. Never commit credentials or include them in worker bundles/receipts.
- Preserve complete provider-specific scientific settings through the existing typed schema, browser/API selection, preview, persisted request, clone/retry and provenance. Existing ColabFold jobs keep their selected provider; no automatic cross-provider fallback or sequence disclosure.
- Reuse controller-side submit/poll/download/validation and worker artifact delivery. Provider ticket recovery, throttling, authentication failure, cancellation and ambiguous submission remain explicit; do not claim cancellation stopped remote work unless the provider proves it.
- Preserve and validate A3M query/chain identities, pairing semantics and native model input contracts. Unsupported provider/model/workflow combinations fail closed rather than coercing paired data or declaring universal compatibility.
- Verify contract tests first, then a separately bounded live provider check once approved credentials and scientific acceptance inputs are available. A provider key alone is not an accepted scientific/runtime contract.
- Continue incremental deployment of the ready bridge independently; the new provider does not block that deployment or imply the broader MSA coverage is already implemented.

This finalized specification is the product/design baseline. It does not assert implemented features, live deployment, performance or scientific acceptance. The visual prototype remains illustrative; its older discussion labels do not override this specification. The section 17 scope amendment supersedes earlier provider deferral statements.
