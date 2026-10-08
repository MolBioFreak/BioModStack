# Remote Bridge 1.0 — controlling implementation sequence

## Authority and execution limits

This plan executes `remote-bridge-1.0.md`, including section 17, and the global model configuration/operator/agent parity policy. It does not replace or amend either. Christian requested a complete, bounded phase plan and execution confined to it. If this plan conflicts with the approved specification, the specification wins; report and correct the conflict explicitly rather than invent a workaround. No feature, architectural deviation, reduced scope or changed scientific behavior may be added without explicit authorization.

Primary agent only. No subagents, test runs or review/audit passes until explicitly reauthorized. Necessary source reading and ordinary edit/commit integrity checks are not permission for an audit campaign. Tests and live acceptance below are planned gates, not current authorization. No paid instances, billable provider evaluation, production changes or destructive lifecycle actions without the required explicit authorization. Missing permission blocks that gate, not unrelated implementation. No unsupported completion claim.

## Fixed architecture

Validated existing request -> shared versioned plan -> local or worker placement adapter -> same component coordinator and Nextflow DAG -> existing Apptainer/native scientific runners -> sealed native artifacts -> authorized shared return/publication -> existing host importers and workbenches.

The worker owns attempt-local orchestration and resources, not model science. One workflow attempt and its descendants stay on one worker. Multiple workers execute independent attempts. Dynamic scientific workflows may declare typed expansion rules and native adapters; they do not receive separate remote schedulers, compilers, stores or settings schemas. Host database projections are not prerequisites for worker computation. Interactive decisions remain explicit durable checkpoints. External MSA preparation is an explicit service/input boundary, not a generic host callback escape hatch.

## Execution discipline and phase accounting

- Execute P0 through P8 in order. Within each phase, retain and connect conforming existing code; implement only missing behavior. Do not rewrite an existing subsystem merely because acceptance is unestablished.
- P9 validation and P10 promotion require their stated permissions. P11 provider extension may proceed after P8 as implementation-only work while validation permission is unavailable; it must not delay an otherwise qualified core development release.
- Record each change against a phase item, spec section and existing owner. Unknown implementation status is not proof of absence.
- Maintain one workflow coverage table with fields: workflow/mode/conditional-stage rule, canonical request/compiler, native runner, dependencies, dynamic components/gates, input/result adapters, implementation state, local/remote connection state, exact-release evidence and blocker. Do not create a parallel executable catalog. Use parametrized coverage rules for continuous settings; enumerate distinct executable combinations and explicit incompatibilities, not arbitrary numeric permutations.
- Phase implementation exit means the listed source and consumer wiring exist; behavioral acceptance remains separate. A phase cannot be marked accepted without its evidence. Do not stop at helper-only or selector-only support while callers still use the old path.
- Retire superseded code only after caller/migration safety is established under the repository retirement rule. If the required review/validation is prohibited, leave retirement explicitly blocked rather than delete blindly or claim consolidation complete.
- Changes outside this plan require an explicit user decision. Discovered in-scope defects belong to their existing phase, not a new architecture or open-ended cleanup campaign.

## P0 — Bind the implementation to existing authorities

Spec: sections 3–5, 12–15. No review campaign or science execution.

1. Populate the coverage table from the actual existing workflow/model registry and request entrypoints as implementation prerequisites, including all advertised scientific families, not only design workflows.
2. Locate and record the current owners of plan compilation, component lifecycle, runtime storage/provisioning, placement, return/import and UI/API contracts. Starting anchors include `platform/api/component_runtime.py`, `platform/api/model_registry.py`, `platform/api/services/nextflow.py`, `platform/api/routers/queue.py`, `platform/api/services/remote_execution/`, `platform/api/services/result_ingester.py`, existing workflows/modules and native scripts. These are anchors, not a claim that every edit belongs there.
3. Classify existing native design additions as shared infrastructure, native scientific adapter, consumer wiring, or duplicate infrastructure. Retain scientific runners and correctly implemented shared behavior.
4. Record unresolved source/acquisition, provider-policy and intentional scientific-semantic decisions. Do not invent answers.

Deliverable/exit: a finite coverage denominator and ownership map feeding the following implementation items; no success percentage or generic audit report substituted for code work.

## P1 — Complete the single plan and artifact contracts

Spec: sections 3–4; parameter parity policy.

1. Extend existing versioned plan schemas to bind source/release, requested/effective settings and digests, native runtime contracts, selected static stages and permitted typed dynamic expansion.
2. Declare immutable SIF/weight/database/helper/runtime dependencies, compatibility, component resources/concurrency, external services, retry/failure/requiredness, checkpoints, result validation and retrieval policy identity.
3. Compile from validated global requests. Replace heuristic remote argv reconstruction with plan projections; argv is output, not scientific authority.
4. Complete typed portable input/output references, including nested native manifest adapters; preserve archived original bytes and candidate/chain/sequence identity.
5. Connect preview, provisioning, local launch, remote launch and result validation to the same plan digest. Invalidate changed previews; revalidate capability before queue insertion.
6. Preserve typed browser/API discovery, effective settings, saved jobs, clone/retry and receipts. No new remote-only settings schema or browser-owned runtime path.

Exit: every downstream consumer has one declared plan authority; unsupported coverage rows remain visibly incomplete until migrated, not relabeled supported.

## P2 — Complete the common component coordinator

Spec: sections 3, 5, 9, 13.

1. Complete shared interfaces for materialization, submit, await/exact-set join, cancellation, resource acquisition/release, events and sealing.
2. Persist attempt/component identities and state transitions on the worker. Reconnect host projections idempotently without requiring host child Job rows to start descendants.
3. Implement typed dynamic expansion through shared lifecycle functions; bind candidate identities, order/grouping and required/optional child policy to the scientific contract.
4. Keep Nextflow task execution authoritative. Subdivide parent reservations without double CPU/GPU allocation or competing child schedulers; descendants cannot escape the worker.
5. Implement common durable checkpoint/decision/continuation contracts with artifact identity, safe quiescence, retained storage and compatible resource reacquisition.
6. Implement common cancellation, retry fencing, uncertain-start handling and durable events; no speculative resubmission after ambiguous transport failures.
7. Route existing native family helpers through these interfaces. Preserve only genuine native scientific transformations; do not add per-family remote lifecycle engines.

Exit: local and worker adapters use the same lifecycle/scientific authority, with worker-local execution independent of workstation connectivity.

## P3 — Bind provisioning and worker readiness to the plan

Spec: sections 6–7, 11, 15; shared image lifecycle policy.

1. Complete the pinned critical release manifest, approved acquisition sources, compatibility requirements, digest checks and atomic activation/recovery. Resolve actual publication/credential prerequisites rather than fabricate endpoints.
2. Reuse the canonical immutable runtime store and existing lifecycle authority, with separate licensed weights; no competing caches or task-local SIF restoration.
3. Complete observed inventory of release, boot identity, freshness, images, weights/databases, compatibility, partial/corrupt/unverified assets, retained output and reservations.
4. Connect both workflow-without-Job dependency provisioning and independent catalog provisioning to the same dependency authority and supported human/agent setup interfaces.
5. Persist real per-artifact progress, disk budgeting, transport cancellation, verified-object reuse and incomplete-transfer recovery. Provisioning must not stage biological inputs or run science.

Exit: readiness and provisioning use actual observed assets and the same plan requirements; provider-running/image-present are not mislabeled model-ready.

## P4 — Complete shared placement, recovery and checkpoint control

Spec: sections 8–9, 11, 13.

1. Connect explicit-worker and eligible-pool selection to deterministic fit and CPU/RAM/GPU/scratch reservation authorities.
2. Bind each attempt to target, plan, source/runtime, lease generation and boot/host identities; preserve independent concurrent worker ownership.
3. Reconcile API restart, SSH loss, worker reboot and provider identity change distinctly. Preserve running science on host disconnect and retained data after loss; do not silently resume unsupported checkpoints.
4. Connect durable cancellation to bounded process termination and diagnostic finalization; do not release ownership while old processes can mutate state.
5. Expose common checkpoint retrieval, native review registration and identity-bound continuation through typed UI/API operations. Releasing compute neither destroys the instance nor deletes its storage.

Exit: transport and lifecycle control are generic, restart-safe contracts, not workflow-specific remote branches.

## P5 — Complete the shared result return and native projection pipeline

Spec: sections 10–11.

1. Bind required artifact categories, native semantic validation, receipts and intentionally filtered diagnostics to the plan's result contract.
2. Seal immutable attempt/manifest identities; distinguish successful science from byte completeness and failed/lost diagnostic archives.
3. Connect manual-default and persisted opt-in auto-return, clone/retry visibility and safe revocation to one controller-owned transfer pipeline. Browser presence is not an execution trigger.
4. Persist transfer identity, compatible partial reuse/reclamation, containment/hash/category validation and received-but-unimported retry state.
5. Complete generation/publication journal recovery across filesystem/database transitions, idempotent imports and preservation of prior good data.
6. Use existing native importers and full BMS workbenches/downstream/export/analysis surfaces; native format adapters may differ, transfer/publication infrastructure must not.

Exit: no result needs workstation-specific or remote absolute paths after import; diagnostics remain accessible without changing failure to success.

## P6 — Migrate every workflow consumer; close bespoke paths

Spec: sections 4–5, 13, 198 coverage requirement. Depends on common contracts P1–P5.

1. Migrate existing static workflows to declared dependency/input/result bindings without rewriting their science. Include the entire P0 registry denominator, including non-design scientific families and selected analysis stages.
2. FrustraMPNN: converge local/remote preparation, candidate order/grouping, submit/join, receipts and artifact resolution through P2; reuse canonical inference.
3. Protein design: complete non-BoltzGen generation dependency bindings; connect all selected stages. Converge BoltzGen direct/child campaign expansion and collection through P2; remove host DB dependencies from computation through P5 projection.
4. Antibody de novo: migrate spawning/waiting, maturation/validation, required annotations and gates to P2/P4. Preserve required worker computation.
5. Local redesign and PPIFLOW: replace host-dependent/indefinite waiting with the shared checkpoint/review/continuation contracts, preserving review semantics.
6. MD: connect preparation, replicas, exact-set aggregation, mandatory analysis and completion with preserved replica/seed identity.
7. CM, Fold-CP and every remaining coverage row: complete native input/result adapters and conditional dependency binding; do not treat existing partial integration as accepted.
8. Remove workflow denylist exceptions only when that row's complete implementation is connected. Preserve explicit failure for unsupported scientific combinations; do not use exclusions to shrink the promised scope.
9. Retire superseded remote orchestration/callers and their stale interfaces once retirement prerequisites are satisfied. Do not remove canonical runners or native result schemas.

Exit: every advertised executable combination has a shared-path implementation; adding a workflow requires native declarations/adapters, not changes to worker scheduling/transfer machinery. Behavioral acceptance is still P9.

## P7 — Complete the existing MSA service/input boundary

Spec: sections 16–17; no local search.

1. Enforce rejection of local MSA search across UI/API, saved-job replay, preview, scheduler and runtime. Preserve verified alignments and explicit model-supported no-MSA modes.
2. Retain visibly selected ColabFold service behavior and provider settings/provenance. Implement durable submit/poll/download/validate, ticket recovery, bounded retries and explicit throttling/cancellation/ambiguous submission.
3. Establish compliant fleet admission through the existing controller preparation/egress boundary or authorized provider arrangement. No independent worker fan-out or IP rotation.
4. Deliver typed verified alignment artifacts before inference independently of final-output retrieval policy; preserve sequence/chain/pairing identities across host/worker roots.
5. Preserve shared MSA cache authority across supported models/providers with identity-safe keys, not new per-workflow caches or silent cross-provider reuse.

Exit: MSA is an explicit preparation dependency with portable inputs, not a reason for bespoke worker orchestration or local fallback. Live provider compliance evidence belongs to authorized acceptance.

## P8 — Close operator/API and lifecycle integration

Spec: sections 6–11; global parity policy.

1. Connect existing browser and typed agent API surfaces to shared preview, provisioning, placement, inventory, progress, cancellation, checkpoints, return/import and diagnostic contracts.
2. Show provider state, runtime readiness, model readiness, science completion, retained results, transfer and native import distinctly, with freshness and actionable blockers.
3. Complete saved request/clone/retry and retrieval-policy preservation; retain server-owned resource/path/credential authority.
4. Wire cleanup/detach warnings and ownership-safe actions for retained/unreturned outputs. No automatic purchasing/destruction or unapproved provider lifecycle behavior.
5. Complete operator documentation of the actual supported interfaces and remaining authorization gates. No illustrative UI or fixture evidence labeled live capability.

Exit: backend capabilities have their required supported human/agent consumers; no reduced remote-only result surface.

## P9 — Authorized exact-release validation and acceptance

Spec: section 12 plus 16/17 additions. BLOCKED until Christian reauthorizes tests; live workers/provider requests additionally require explicit applicable authorization.

1. Freeze the candidate release/plan/runtime identities and populate evidence for every P0 coverage row, not just transport fixtures.
2. Execute focused contract and local/remote conformance checks for settings/arguments, candidate order/grouping, identities, dependency resolution, native outputs and local regressions; preserve approved nondeterminism tolerances.
3. Exercise clean-worker bootstrap, observed inventory, independent/workflow provisioning and actual complete scientific workflows through return/import/downstream use on the exact candidate release.
4. Exercise two-worker isolation and manual/automatic return without browser presence.
5. Exercise missing/corrupt assets, resource shortage, scientific failure, cancellation, interrupted provisioning/pull, API restart, SSH loss, reboot and uncertain starts.
6. Exercise publication crash points and diagnostic recovery, preserving prior good data and preventing duplicate science/import.
7. Record failures against their owning phase and fix them there. No new architecture, unrelated broad testing or silent semantic changes.

Exit: accepted exact-release evidence covers the agreed denominator. Until then report implemented but unaccepted, not complete.

## P10 — Development release and deployment closeout

Spec: sections 12, 14; AGENTS.md development path. Depends on applicable authorized validation; no bypass of repository release requirements.

1. Reconcile the candidate with current `origin/test`, port logical current changes, and perform the required authorized affected validation after reconciliation.
2. Push only a fast-forward development update and verify remote identity; use the existing managed development deployment path.
3. Verify deployed source/runtime/service ownership and perform the authorized deployed acceptance portions of P9 against that exact revision. Predeployment candidate checks and deployed acceptance are distinct.
4. Close the coverage record only with actual evidence; clean up worktrees/obsolete artifacts only safely and within authorization. Production promotion remains separate.

Exit: verified development deployment and accepted coverage, not merely a branch commit or successful push.

## P11 — Neurosnap extension through the same MSA interface

Spec: section 17. Implementation may follow P8 without blocking a qualified core deployment. Never silently remove this amendment from full-scope completion.

1. Establish actual authoritative API schema, capabilities, authentication, outputs and lifecycle; reuse already conforming integration rather than recreate it.
2. Connect managed server-owned credential references and supported setup/readiness with human/agent parity; no secrets in bundles, logs or receipts.
3. Preserve explicit provider selection and complete supported provider settings through typed UI/API, preview, persistence, clone/retry and provenance; no fallback/disclosure to another provider.
4. Reuse P7 controller preparation, ticket recovery, cancellation/throttling/error states, validation, cache and portable artifact delivery.
5. Validate native A3M query/chain identity and pairing/model contracts; unsupported combinations remain explicitly unaccepted rather than coerced into false compatibility.
6. Only after authorization: contract checks, separately bounded live provider acceptance, and the P10 development release path for the extension.

Exit: implemented and accepted bounded provider compatibility with unchanged generic worker architecture. A key alone is not acceptance.

## Final completion rule

All required implementation and advertised workflow coverage, exact-release acceptance and development deployment must be established; the Neurosnap amendment remains separately visible until fulfilled. No phase count, commit count or partial working path substitutes for that result. No automatic claim that unknown existing subsystems need rebuilding. No work outside these phase items without explicit user authorization.
