# Agnostic Binder Design: Concurrent Implementation SOW

**Status:** Implementation SOW prepared after four read-only technical planning reviews, with consequential source findings independently checked. This document is not a claim that implementation, model execution or deployment has occurred. It supplies the dispatch contracts; implementation dispatch still binds the then-current source revision, exact write paths and authorized execution resources.

**Authority:** Christian's latest three-tranche direction controls this plan. Read it together with the [implementation specification](de-novo-binder-design-upgrade.md) and [product outline](de-novo-binder-upgrade-outline.md). This plan replaces the specification's earlier workstream allocation. Its Phase 3 replaces the earlier ordinary-redesign interpretation of LigandMPNN. It does not shrink the remaining workflow scope.

**Verified planning baseline:** canonical Development, `origin/test` and live API agreed on `96923d9470d86219451d6caf88f63308a369c007` at review start. During preparation, canonical source and `origin/test` advanced to `7d6d31e68849cb19140bd061b2c2c47b86c7eb77`; the intervening diff touches BioXP frontend feature recovery and its runtime authority, not the binder/bridge owners reviewed here. Documentation started at `7c767fd036c25331e3e9d6128313a2ca06b8b20c`. BC2 scientific baseline is `PacesaLab/BindCraft2@d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f`, package `1.0.1`. Implementation must branch from the then-current `origin/test`, not the older documentation worktree. Recheck deployment identity when performing live acceptance; source advancement alone is not that proof. Reconcile relevant source changes rather than restarting the entire audit.

## 1. Binding scope and definition of completion

### Phase 1: the entire existing workflow, generalized and repaired

Refactor the de novo nanobody product into agnostic **De Novo Binder Design**. Keep its generators, functional scientific operations and model-native execution; remove antibody-only assumptions from genuinely shared paths. Preserve RFantibody as an antibody-specific option, BoltzGen's wider supported formats, visibly seeded PPIFlow, and existing applicable general-design/RFD3 routes without duplicating their implementation.

Upgrade the optional refinement loop, candidate selection, repeated rounds, per-model results, independent structure validation, existing analyses and alternative methods. Repair the existing C01–C14 defects in the specification. Integrate generation and every selected continuation with the existing remote bridge. Correct warm-launch waste at its current owner. This is not a label change, an RFantibody pipeline rename, or a BC2-only preparatory shell.

Retain the agreed FA-MPNN, independent repack/anchor operations, supported PPIFlow, prediction, FrustraMPNN, Caliby and complex-contact-frustration obligations. Actual checkpoint/format limits remain explicit. Do not invent generic PPIFlow capability or promote binder-local FrustraMPNN to a cross-chain metric.

### Phase 2: full BC2, a first-class peer generator

Integrate the complete pinned native BC2 scientific surface: modalities, objectives, targets, presets, relevant settings, native stages, sweeps, workers, adaptive attempts, native acceptance/ranking, records, structures, optional outputs and continuation. Deliver typed browser/agent parity, native result interpretation, local/remote placement and handoff to compatible refinement/validation.

BC2 is an option, not a mandatory upstream step or a replacement for existing generators. A binder-only, VHH-only, raw-JSON-only, local-only or ranked-PDB-only integration FAILS this SOW.

### Phase 3: experimental LigandMPNN sanity-check validation

Deliver two independently selectable candidate assessments: LigandMPNN target amino-acid/structural-context compatibility on supplied geometry, and blind structure/pose verification through the existing suitable predictor. They may be requested separately or together; neither is mandatory for generator results or a refinement round. Preserve the selected binder/target patch and declared structural context, masking held-out identities/atoms for the LigandMPNN assessment. This is NOT satisfied by adding another routine sequence-redesign selector or by relabeling a supplied pose as recovered.

Run either assessment on selected candidates after a round, or select a subset for a later bounded assessment/refinement round. Preserve both checks' separate raw results and the original candidates. Operator-defined cutoffs are applied after inspecting exploratory results, with a saved policy/version and explicit re-evaluation of existing evidence; no cutoff is hard-coded to make tests green. Only if the operator selects the dual-failure policy, both checks are applicable and valid, and both fail their selected criteria is the candidate **computationally rejected by that policy**. Unrun, unsupported, unqualified or broken runs are not failures. Passing either check is not proof of binding.

LigandMPNN by itself evaluates/designs sequence on supplied backbone geometry; it does not generate an absent partner backbone or independently recover a binding pose. Label its evidence as context/sequence compatibility, not pose prediction or affinity. A new geometry generator, protein-interaction search service or generic scoring platform is NOT authorized by this phase. Reusing an already in-scope generator for a specifically agreed experiment can be proposed separately without replacing the required LigandMPNN diagnostic.

### Completion rule

All three phases are mandatory engineering deliverables and may be developed concurrently. Optional means that an operator can leave an operation off. It does not mean that an agent may omit its implementation, qualification or result route.

Neither implementation sequencing nor an unavailable selector changes the completion denominator. A genuinely unresolved scientific choice blocks the affected scientific behavior only; it does not authorize inventing that choice or marking the feature complete.

## 2. Non-negotiable SOW rules for every agent

These instructions MUST accompany every implementation dispatch, together with the relevant lane prompt and linked specification sections.

1. **MUST implement only the assigned lane and its explicit acceptance conditions. MUST NOT redesign the product, redefine the three phases, drop a generator, narrow BC2, or demote remote execution or optional-loop delivery.**
2. **MUST preserve model-native scientific semantics. MUST NOT silently change a model, checkpoint, preset, chain role, residue mask, sample count, validation method, filter or effective setting to make a test pass.** Remove the specifically identified unsafe fallbacks; do not preserve bugs as compatibility.
3. **MUST reuse existing execution, settings, artifacts, selection, results and bridge owners. MUST NOT create a universal workflow/operation engine, second scientific settings authority, parallel candidate database, custom BC2 remote runner, duplicate Foundry runner, independent scheduler, new recovery daemon or redundant cache/receipt system.**
4. **MUST stay inside assigned write ownership.** Another lane's file is read-only. Request a precise owner change or submit a proposed hunk to its owner. Do not opportunistically edit it, restore it, reformat it or claim a symbol without agreement. No simultaneous writers in shared hubs.
5. **MUST separate engineering validity, scientific outcome and availability.** An error is not a nonbinder; zero accepted BC2 yield is not necessarily failed execution; a passing mock is not native execution; source presence is not a ready runtime.
6. **MUST keep warm startup lean.** No per-job dependency installation, unchanged image/weight transfer, off-stage preparation, heavy model imports on form/API startup, or repeated whole-tree checks of unchanged assets when established immutable evidence suffices. Do not weaken real request, corruption, source-ownership, reconnect or process-lifetime boundaries.
7. **MUST remove replaced active code and stale tests/imports after checking references.** Keep at most the required bounded historical reader. No permanent dual write paths, generic fallback scanners, speculative abstraction layers or archaeology copies.
8. **MUST keep scope focused.** No unrelated Project/NGS programme, generic workbench rewrite, global telemetry/dashboard, new approval bureaucracy, cosmetic overhaul, unrelated dependency upgrade or opportunistic repository cleanup. If a shared repair is necessary, name its direct in-scope consumer and smallest change.
9. **MUST test at the existing owner and report actual evidence.** Focused tests plus required integrated/native acceptance, not repeated full-suite runs or an exhaustive Cartesian product. Never suppress failures, loosen scientific thresholds, delete acceptance tests or fabricate native fixtures/results to obtain green output.
10. **MUST escalate consequential science choices.** State the precise choice, evidence, affected behavior and bounded options to the integrator. The two independent candidate checks, optional execution and user-defined post-review cutoffs are already decided; do not reopen them as a planning prerequisite. Only Christian changes product scope or approves an unresolved scientific method. Unrelated lanes continue.
11. **MUST respect execution permissions.** No child push, deploy, service restart, live database mutation, paid rental/start, cloud attach/provisioning or live scientific campaign unless that exact action is separately delegated after authorization. Do not manipulate active jobs or unrelated hardware.
12. **MUST return a bounded handoff:** base/commit, owned changed paths, requirements satisfied, exact test commands and result files/counts/skips, native/live evidence if authorized, unresolved items and deletion summary. A prose claim without reproducible evidence is NOT accepted. Only the parent closes requirements.

**Automatic rejection of a patch:** scope reduction; invented scientific defaults; hidden model substitution; second execution/result authority; unauthorized shared-file edits; per-job heavyweight setup; counterfeit science; or completion claims based on an unexecuted/incomplete path. The parent returns the patch to its owner for correction, not a new framework to accommodate it.

## 3. Parallel organization and ownership

The parent is the integrator, not a ninth independent implementation competing over shared files. Dispatch eight bounded implementation lanes from one verified base. Use one short-lived worktree per lane. No child spawns its own unbounded agent tree. A focused adversarial reviewer is dispatched after a coherent combined slice exists, not as a standing paperwork-only lane.

- **A — Launcher and request experience:** Phase 1, consuming Phase 2/3 model-owned controls.
- **B — Refinement transformations and blind structure validation:** Phase 1.
- **C — Candidate identity, selection and shared result publication:** Phase 1; shared support for all phases.
- **D — Existing alternative methods and analyses:** Phase 1; complex-contact method is separate from FrustraMPNN's single-residue prediction.
- **E — BC2 settings and native campaign execution:** Phase 2.
- **F — BC2 native publication and result projection:** Phase 2.
- **G — Bridge placement, runtime closure and warm-start performance:** all phases.
- **H — Experimental LigandMPNN interface-context validator:** Phase 3; B owns the separately selectable blind pose assessment.

The integrator records exact existing files/symbols and final new-file names before writers start. The paths below are ownership anchors, not permission to edit an entire directory. A path assigned to one lane cannot also be assigned to another. New files use existing repository conventions; internal lane/phase names MUST NOT become public product IDs or arbitrary schema versions.

This split is intentional: A/B/C separate the main UI, scientific execution and publication writers; D removes independently reusable leaf-method work from B's large workflow edit; E/F separate BC2's producer contract from its consumer; G is one shared bridge lane across all phases; H owns the distinct experimental scientific question. Do not multiply these into a horizontal agent per tiny file or combine them into one agent that serializes all three phases.

### Shared hubs and sole writers

- Parent only: `platform/api/routers/jobs.py`, `platform/api/routers/user_templates.py`, `platform/api/schemas.py`, `platform/api/model_registry.py`, `platform/api/antibody_pipeline_contract.py`, `platform/api/services/workflow_request_types.py`, `platform/api/services/nextflow.py`, `platform/api/component_runtime.py`, `platform/api/native_components.py`, `nextflow_schema.json`, touched shared scheduler/resource-policy functions, and any database-schema migration. Model lanes supply schemas/adapter exports and exact registration requirements. Parent wires them without duplicating their semantics. In `jobs.py`, this includes normalization, preview/submission binding and ordinary iteration/manual-mutation selection integration; in `user_templates.py`, keep antibody legacy repair separate from lossless new generic save/load.
- A only: `platform/frontend/src/components/AntibodyDenovoTemplate.tsx` replacement/extraction, touched `platform/frontend/src/components/JobSubmission.tsx`, `platform/frontend/src/components/workflowModelInventory.ts`, `platform/frontend/src/lib/launcherCatalog.ts`, `platform/api/config/models/antibody_denovo.yaml`, and assigned request-hydration helpers. `launcherCatalog.ts` owns the visible dedicated card's nanobody label/stages. There is no active `antibody_denovo` template YAML in `platform/api/config/templates/`; its existing `binder_design.yaml` is disabled and describes a different historical RFdiffusion/ProteinMPNN/AF2 pipeline. Do not repurpose or enable it as the new product by name alone. If an actual workflow-template edit is needed, the parent first identifies its current owner and scientific route. E/H may own isolated model form components, never edit the shared shell.
- B only: shared existing refinement workflow/stage orchestration, touched `workflows/antibody_denovo.nf`, `workflows/ppiflow_generator_design.nf`, `modules/ppiflow.nf`, relevant preparation/validation adapters and shared refinement controls outside A's shell. D supplies leaf-operation interfaces; B wires their calls in the shared parent workflow.
- C only: `platform/api/services/result_ingester.py`, `platform/api/services/stage_review.py`, `platform/api/services/core_protein_result_contract.py`, `platform/api/services/result_contracts.py`, assigned selection/lineage services, shared artifact/result projections and touched `platform/frontend/src/components/ResultsViewer.tsx` and `platform/frontend/src/components/JobDetailsPanel.tsx`. The latter currently limits `ExecutionSettingsPanel` to ESMFold2 and antibody IDs; C must admit BC2's actual model-owned effective-settings readback there or an explicit equivalent job-detail route. F/H/D supply model-specific readers/components and explicit registration hooks. C does not reinterpret their scientific metrics. C supplies precise selection changes for parent-owned `jobs.py`; it does not edit that hub itself.
- D only: assigned Caliby/FrustraMPNN/complex-contact leaf schemas, `modules/caliby.nf`, `scripts/run_caliby_sequence_design.py`, assigned existing FrustraMPNN services/modules, and model-specific adapters/presentation. B alone wires those leaf interfaces into shared parent workflows. No shared parent workflow or generic ingester edits.
- E only: BC2 schema, native settings bridge, BC2 entrypoint/module, reproducible image recipe, necessary narrow producer hooks, model form and native execution tests. G owns shared runtime installation/placement registration; F owns output parsing.
- F only: BC2 native output parser, model-specific typed datasets/result components and fixtures. No BC2 producer source patch or shared ingester edits; request producer additions from E.
- G only: assigned runtime dependency/portable-plan owners, `services/remote_execution/` functions, worker/runtime helpers, image/asset installation mappings and related tests. Ownership includes only changes justified by this workflow, not a blanket bridge rewrite. Parent-only hubs stay parent-only.
- H only: scoped changes to `platform/api/config/models/ligandmpnn.yaml`, necessary scoped changes to `apptainer/foundry.def`, the new Foundry-owned diagnostic wrapper/leaf entrypoint, diagnostic scientific contract, model-specific controls/result reader and tests. The existing chemistry-oriented LigandMPNN modes and their saved request meaning are not the new binder diagnostic; preserve their contract while adding a distinct experimental operation and truthful executable/readiness state. Do not advertise either as ready merely because the existing YAML says `enabled: true, experimental: false`. B consumes the new optional-stage interface; C consumes publication; G consumes runtime closure. No second LigandMPNN implementation. Coordinate a changed shared Foundry image with G and qualify affected existing consumers without launching an unrelated Foundry rewrite.

If a listed file has moved at implementation baseline, replace its entry with the actual owner and record that substitution once. Do not copy obsolete code from the documentation tree.

### Existing execution routes that must survive

The reviewed `MODEL_MODE_WORKFLOW_ENTRYPOINTS` routes the RFantibody parent/default/refinement to `workflows/antibody_denovo.nf`; `antibody_denovo/nanobody_binder` to `workflows/protein_design.nf`; and `antibody_denovo/generator_backbone_refine` to `workflows/ppiflow_generator_design.nf`. BoltzGen children for nanobody, peptide and protein binder use `workflows/boltzgen_child.nf`; general `protein_modification_experimental/de_novo_design` uses `workflows/protein_design.nf`; public sequence-design modes use `workflows/protein_sequence_design.nf`.

The existing generic continuation blocker is upstream of those workflows: `routers/jobs.py` resolves only antibody-looking iteration roots, treats BoltzGen as eligible only in nanobody/antibody modes and forces continuation to `template_antibody_denovo/antibody_refinement_pipeline`. `model_registry.py` also requires antibody `target_pdb` and `epitope_residues` for its full-root mode. The parent must explicitly choose model-owned generation routes versus any generalized continuation route, repair the ownership/admission/launch boundary for real non-antibody inputs, and retain strict antibody requirements only on actual antibody requests. B's downstream workflow edits and A's visible controls alone cannot make generic selection runnable. Prove non-antibody source-to-descendant continuation at the API boundary without relabeling it as antibody.

B is the sole writer for necessary changes to these existing shared scientific workflow entrypoints, including `workflows/maturation_child.nf` and shared antibody validation/batch finalization. Parent owns routing changes. Keep useful existing supported sequence-design/ancillary alternatives where actually in use; do not resurrect an unused or retired model merely because a source file or historical registry row remains.

### Concrete bridge touchpoints

G owns scoped edits to `platform/api/services/remote_execution/bundle.py`, particularly `compile_remote_dependencies`, `_input_assets`, `_write_portable_bindings`, `_staged_source_archive` and `prepare_remote_bundle`; affected `cache.py`, `images.py`, `managed_inventory.py`, `critical_runtime.py`, `executor.py`, `result_generation.py` and current transfer owners under that directory; and, only for reproduced defects, `platform/api/tools/bms_remote_worker.py`, `platform/api/tools/bms_artifact_cache.py`, `platform/api/tools/bms_container.py` and `scripts/lib/runtime_image_views.py`. These are existing owners, not instructions to modify every file.

The parent integrates G's contract changes into `platform/api/component_runtime.py` (`SelectedExecutionPlan`, `NativeInvocation`), `native_components.py`, `model_registry.py` and the selected-plan compiler in `services/nextflow.py`. G does not add model defaults, native output interpretation or scientific verdicts there.

## 4. Small interface handshake, then concurrent implementation

This is an engineering dependency, not another approval ceremony. The parent and lane owners settle four contracts using the existing types. Do not create four new frameworks or duplicate JSON catalogues.

Before dependent agents edit their production adapters, the parent records the concrete field names/ownership and one representative typed request, selected candidate, native publication and local/remote compiled plan in the dispatch packet or existing contract tests. Land the minimal parent-owned hub hooks on the shared integration base and give consumers that exact revision/interface. Inventory, fixtures and independent leaf work can start concurrently; eight divergent provisional implementations of I1–I4 cannot. Refresh the packet if the implementation base changes a relevant owner. This is an executable handoff, not a new runtime schema store.

### I1 — Scientific request and operation reference

One model-owned typed schema, relevant settings/defaults, requested versus effective values, supported input/operation modes, native compiler, runtime dependency declarations and output producer identity. The common launcher references these. Placement does not redefine science. Existing preview/approval binding remains the single launch authority.

### I2 — Immutable selected candidate/document

Existing source/root Job and Design/subject references, producer identity, exact structure document/state, artifact digest/reference, chain/residue/role map, current sequence and round/parent association. Include only fields the operation actually consumes or existing lineage requires. Support a subset selected after a completed round, including non-antibody roots, as a new bounded child round with exact parent for each source, even when sources span generators or states that are individually compatible with the chosen operation. No filename/stem joins and no parallel candidate store. Distinguish an acceptance rank from a candidate identity.

### I3 — Native publication and optional-stage outcome

A model-owned publication references exact input and output artifacts, settings/runtime identity, native rows/metrics, sample/state association and terminal outcome. Primary verified results survive optional-stage failure. Scientific check outcome is separate from runtime outcome. C owns shared publication; E/F/H/D own native semantics.

### I4 — Placement and dependency closure

The same compiled scientific request enters `NativeInvocation`/`SelectedExecutionPlan` for local and remote execution. Only selected operations contribute assets/preparation. Declare portable inputs, native resume state, output roles, resources and compatible persistent caches. Reuse bridge ownership/journaling, not model-specific transfer or lifecycle services.

Placement parity means identical scientific intent, effective settings, identity and native result semantics; it does not require bitwise-equal stochastic predictions on different hardware.

A/E/H can build isolated model forms against I1; B/D against I2/I3; E/F can agree the BC2 producer/publication boundary; G can register the selected closure and benchmark the existing path. An unresolved optional scientific threshold does not prevent settings/result plumbing or unrelated generator work.

Contract changes after this handshake are small explicit owner-to-consumer updates with focused tests. Do not let each lane maintain a divergent temporary production contract. Test doubles are labeled and never counted as live/native completion.

## 5. Lane A prompt: agnostic launcher and request fidelity

### Copy-ready assignment

> Implement Phase 1's modality-aware De Novo Binder Design authoring and continuation experience. Preserve all existing generator choices and supported capabilities; consume BC2 and LigandMPNN model-owned components as they land. Do not rename RFantibody's scientific workflow into a universal engine. Preserve model drafts and existing saved jobs. Eliminate duplicated new-write selectors/defaults and hidden coercion. Your write scope is the A-owned launcher/template/request-hydration files; parent owns API hubs. Deliver mounted UI/request tests and exact parent integration requirements. Apply every rule in section 2. Do not redesign the product or defer remote controls, refinement, BC2 access or experimental validation.

### Required work

- Modality/objective first; compatible model options and relevant controls thereafter. RFantibody remains antibody-specific; seeded PPIFlow requires a seed. Do not hide legitimate BoltzGen/BC2 modes to match RFantibody.
- Reuse model schemas/forms; preserve native advanced settings without a raw-JSON-only escape hatch.
- Keep generation-only exit and candidate-selected refinement rounds obvious. After each completed round, permit a user-selected compatible subset to be requeued with separately chosen refinement, LigandMPNN context and/or blind pose checks (or none), including from non-antibody roots. Display selections, alternatives, exact source/state and requested checks without auto-enabling stages or repeating off-operations.
- Replace the current `deNovoDownstreamLocked` and BoltzGen/PPIFlow generator-only restoration restrictions with supported selection/continuation. Preserve their separate scientific routes; do not work around a frontend lock by merging those models into the RFantibody DAG.
- Preserve save/load/clone/retry and separate model drafts. Changes identify incompatible settings instead of silently dropping/relabeling them.
- Treat `routers/user_templates.py` as a parent-owned API dependency: its antibody-template normalizer currently rewrites framework paths, residue lists and `selected_chain` on writes and reads. New generic templates must round-trip without that coercion; only bounded documented historical antibody repair may use it. A frontend hydration test alone is insufficient.
- Preserve target selection, execution target/bridge preview, refusal rendering and existing review/reopen behavior. Do not fetch all models' assets or initialize their runtimes when rendering.
- Update `platform/frontend/tests/retiredWorkflowRemovalContract.test.ts` deliberately when BC2 first appears in `JobSubmission.tsx`. Its current substring assertion rejects even `bindcraft2`; retain the exact retired v1 route/identifier prohibition while admitting the new BC2 identity and display label. Do not disguise BC2 to make the old assertion pass.

### Pass/fail

**PASS:** mounted requests for retained generators and BC2 use the correct model-owned contracts; unsupported modality combinations are explained; advanced native fields round-trip; explicit values, including `false` and zero, survive presets/hydration; agent/browser equivalence is demonstrated at the existing compiler; edits invalidate the approved preview; generator-only and post-round *subset* requeue with either/both/no optional checks remain usable; off-operations add no selected dependency; a refusal is visible.

**FAIL:** rename-only UI; VHH defaults leak into generic requests; generator disappears; unknown selector becomes another engine; typed settings missing; raw JSON is the only complete path; saved request changes silently; new frontend scientific authority; render triggers runtime setup.

Dependencies: I1/I2 and E/H model components. A must not wait for native GPU acceptance to implement/test the shell. C owns shared results display; B owns execution semantics.

## 6. Lane B prompt: refinement core and independent structure check

### Copy-ready assignment

> Upgrade the existing optional refinement loop for exact selected candidate/state inputs from every generator, subject to real model compatibility. Separate repack, read-only anchor identification, partial flow, sequence design and independent structure validation. Preserve their useful existing science, fix C02/C03/C05/C06/C07 and the native-sample portion of C04, and remove unsafe fallback/implicit stage behavior. Own only B-assigned workflows/adapters. Consume D/H leaf operations and C selection/publication; request API hub changes from the parent. Do not invent generic checkpoints, a new DAG engine, automatic optimization or new science. Return operation-by-operation regression evidence and the blind-check input contract.

### Required work

- General chain/region/protected-position controls for compatible generic operations; antibody conveniences only for antibody inputs.
- Separate repack from read-only anchors and flow. Repack-off preserves source coordinates; anchor-only is not a hidden mutation.
- Use generic constrained FA-MPNN where appropriate. Post-flow redesign happens only when selected. Preserve all sampled descendants and exact parent/state mappings.
- Retain actual PPIFlow checkpoint limits. Correct role disagreement instead of using the first chain or dropping requested chains. Report zero eligible anchors/seeds explicitly.
- Remove synthetic sequence extraction; parse real structure identity including insertion codes. Pair every structure-validator sample with its own metrics. Preserve existing validator choice.
- Any changed sequence/coordinates produces a descendant with fresh assessment state. Do not reuse parent acceptance/confidence for changed bytes. Post-round subset requeue must materialize a new child operation from exact immutable selected candidates, not rerun every source or mutate the finished parent round.
- Verify what the current end-of-loop blind check actually receives. Candidate interface coordinates or equivalent restraints must not be hidden inputs when advertising independent pose recovery. Required target structural context may remain if explicitly declared. If the native route cannot be blind, expose that fact and refer the exact scientific choice; do not silently change the predictor or claim independence.
- Preserve requested sample budgets, output multiplicity, relevant predictor settings and native scoring. No automatic reruns until a candidate passes.

### Pass/fail

**PASS:** generation-only, individual operations and supported compositions run through existing orchestration; a generic binder avoids antibody masks; compatible antibody candidates from different generators reach the same sampler; repack-off/anchor-only invariance holds; all samples retain pairing/lineage; changed artifacts invalidate previous checks; blind/nonblind input provenance is truthful; multiple rounds preserve originals.

**FAIL:** only BC2 candidates work; generic chains relabeled as antibody to bypass a checkpoint; no-op success on zero seeds; hidden redesign/repack; dropped samples; fabricated sequences; post-change inherited validation; claimed blind recovery with the candidate pose supplied as an answer.

Dependencies: C's I2/I3; D/H leaf contracts. B can test selected compositions with labeled fixtures while real model readiness proceeds independently.

## 7. Lane C prompt: identity, selection, native-result integration

### Copy-ready assignment

> Repair shared candidate/document selection, lineage, state-aware review and publication for the agnostic workflow. Reuse Job/Design, scientific datasets/artifacts and current review mechanisms. Own C-assigned shared result/selection files, including the sole edits to result_ingester.py and stage_review.py. Integrate F/H/D model-owned readers without flattening their science. Close C08/C09/C10/C11 and shared result portions of C04/C06/C07. No universal binder score, second candidate store, wholesale PDB-column migration or filename-based lineage. Return precise corruption/ownership/round/failure-isolation tests.

### Required work

- Bind selection to source/root, candidate/document/state, immutable bytes and roles. A selected subset from a completed round keeps every chosen item's exact root/parent/state and results, including when different compatible sources are selected for the same operation; reject incompatible mixtures explicitly. Ordinary iteration/manual mutation must not accept a foreign global ID.
- Use producer identities for sample/parent/state joins. Native CIF and derived PDB are related documents, not duplicates to pick by filename preference. Different target states must not collapse.
- Preserve native CIF/mmCIF; repair actual consumers. Convert only at a genuinely PDB-only operation with a loss/map contract. Structureless records belong in native datasets, not fabricated Designs.
- Primary generator and earlier-round results survive failed optional analyses/validators. Retain explicit failed stage state and allow bounded retry without duplicate candidates.
- Publish sequence/coordinate-changing descendants separately. Read-only Phase 3 diagnostics attach to the exact assessed candidate and never overwrite its sequence, native acceptance or structure.
- Consume model-specific views/exports using existing global facilities; verify reopen after restart/worker loss. Do not rebuild unrelated analytics or add log/validation dashboards.
- Make BC2's effective/per-attempt settings discoverable from the actual job-detail readback. `JobDetailsPanel.tsx` currently gates `ExecutionSettingsPanel` on ESMFold2 and antibody IDs; add the BC2 route only if that panel can represent its native campaign/attempt settings faithfully, otherwise mount F's model-owned readback there. Do not show one campaign default as every adapted attempt's effective science.

### Pass/fail

**PASS:** foreign/mismatched selections reject once at the owner; same-stem states/samples survive; native/derived mapping is reversible where required; subset requeue from completed generic and antibody rounds preserves distinct source/root/parent/state identities, prior assessments and untouched siblings; optional failure cannot roll back primary candidates; re-ingestion is idempotent; native data and assessments reopen without the worker.

**FAIL:** `.first()`/stem/path guessing establishes scientific identity; target states counted as independent accepted sequences; sequence-only join loses distinct samples; mutable artifact passes as approved input; fake PDB; failed optional stage erases valid primary output; changed candidate keeps its parent's verdict.

Dependencies: F supplies BC2 native publication, H diagnostic rows, D analysis rows. C owns only shared mechanics, never their thresholds/rank semantics.

## 8. Lane D prompt: existing alternatives and analyses

### Copy-ready assignment

> Preserve and qualify the existing Caliby and FrustraMPNN paths and the already-agreed complex-contact-frustration work within the agnostic optional loop. Reuse current leaf implementations and model-owned settings/results; do not build another analysis runner or pretend a Project catalogue gap proves no core execution. Your files are D-assigned model-specific adapters/modules/forms/results, not the parent workflow or generic ingester. B/C integrate your explicit exports. Resolve real modality/checkpoint limits and report consequential scientific choices. Do not choose a new contact-frustration method/reference ensemble without Christian's decision.

### Required work

- Caliby: inspect existing parent runner, complete relevant controls/masks/context/output identity and selected runtime requirements; qualify rather than rebuild.
- FrustraMPNN: consume the **complete existing global implementation** (pinned native settings, typed UI/API parity, scheduler fan-out, 20-substitution rows, statistics, structure/sequence-linked workbench, exports, captures, persistence) without a reduced workflow-local runner or viewer. Bind exact chain/document/parent identity, preserving intrachain multi-domain context where present. A binder designed against a target can carry target-specific design history in its sequence/structure, so its binder-local landscape is useful exploratory candidate evidence. Native inference nevertheless parses each chain alone: unchanged binder atoms produce the same prediction if only the target partner changes. Label the distinction; never present it as direct target-conditioned inference, binding evidence, or cross-chain contact frustration. Do not fake a single chain by concatenating binder and target; a properly qualified joint-chain model/input is future research outside this SOW.
- Complex-contact analysis: retain the required work item and isolate the exact method/reference decision. The [FrustraMPNN preprint](https://doi.org/10.64898/2026.01.22.701012) and [repository](https://github.com/RosettaCommons/frustraMPNN) predict per-residue single-mutation frustration, **not** pairwise cross-chain contact frustration; the preprint distinguishes physics-based configurational/mutational pairwise analyses and calls pairwise prediction a future extension. For empirical interface precedent, [Ma et al. 2025](https://doi.org/10.1038/s41467-025-63713-7) and [Wei et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12262341/) use mutational pairwise Frustratometer/FrustratometeR indices on protein–protein interfaces. Present mutational versus configurational pairwise methods and exact decoy/reference meanings to Christian; do not claim approval of one. FrustraPy is an *unofficial* reimplementation whose own [README](https://github.com/engelberger/frustrapy) cautions verification against FrustratometeR, not proof that FrustraMPNN supports pairwise mode. Build reusable input/output plumbing without substituting another metric. Complete the agreed scientific method after that decision, not an unavailable placeholder.
- All analyses remain optional to execute and failure-isolated. Their result routes must work locally/remotely through the common bridge.

### Pass/fail

**PASS:** relevant per-model controls and native cardinality survive; actual selected leaf paths execute when qualified; exact parent/document association is retained; local-chain and cross-chain evidence is labeled correctly; off methods do nothing; failure leaves primary results available.

**FAIL:** source implementation called live-qualified; Caliby reimplemented unnecessarily; FrustraMPNN presented as cross-chain contact energetics; missing scientific method filled with a different score; unavailable selector called delivered; analysis runtime copied into a second owner.

Dependencies: B/C integration, G runtime closure. Only the unchosen complex-contact scientific implementation waits for its decision; other D work proceeds.

## 9. Lane E prompt: full BC2 settings and campaign execution

### Copy-ready assignment

> Integrate the complete pinned BC2 scientific engine as a first-class peer model. Own its native inventory/schema/resolver bridge, typed model form, dedicated Nextflow wrapper, reproducible image definition and necessary narrow producer metadata hooks. Reuse native campaign stages, workers, adaptive behavior, sweeps, filtering and ranking. Do not fork those semantics into BMS or stop at a VHH/binder demo. Coordinate the exact producer publication with F and runtime closure with G; parent wires shared registries. Apply specification section 4 in full. Return generated coverage evidence, differential native/BMS settings tests and actual execution evidence separately.

### Required work

- Inventory pinned installed code, defaults, profiles, every modality/property/target preset, CLI, loss/filter/metric registries and accepted nested/preset-only fields. Generate coverage evidence from the actual authoritative schema rather than editing a second catalogue.
- Cover `binder`, `large_binder`, `peptide`, `cyclic_peptide`, `homo_oligomer`, `multidomain`, `VHH`, `ARP`, `scFv`, `Fab`, `induced_fit`, `fold_switch`, multi-positive/detarget inputs and all relevant native controls/actions. Preserve actual native limitations without deleting the modes.
- Use native settings resolution/preflight without GPU initialization on discovery/render/API startup. Test resolution differences including preset order, target accumulation, implicit fields, nested settings and aliases.
- Preserve finite effective budgets, actual sweep allowance, trajectory-only and zero accepted yield. Expose accepted targets separately from attempt limits.
- Preserve full per-attempt effective scientific settings, including adaptive changes. Native recipe hash is not complete settings identity.
- Capture an explicit scored `_candidateN` to retained `_seqN` association at the producer boundary for F. A narrow metadata hook must not change filtering/ranking/scientific computation.
- Reproducible runtime with selected GPU isolation, shared weights, persistent compatible compilation cache and declared native resume state. BC2 itself adds no external MSA provider requirement.
- Same-campaign continuation requires identical scientific identity; changed science becomes a new campaign. Do not promise exact replay of interrupted native scheduling.

### Pass/fail

**PASS:** every relevant native field has UI/API/compiler/persistence coverage or a specific proven system-internal classification; differential resolution agrees; no hidden override; native stages remain native; worker allocation respects scheduler limits; output includes F's joins/effective settings; bounded zero-yield/trajectory-only/sweep/resume paths are handled honestly; runtime is reproducible and G can place it locally/remotely.

**FAIL:** unexplained native omissions; raw JSON as sole complete control; one modality labeled full integration; retired v1 image/code reused; duplicate attempt scheduler/autotuner; first-run downloads in a ready launch; native recipe hash substituted for complete settings; ordinal join invented; filters loosened to get an accepted smoke candidate.

Dependencies: I1/I4; E and F agree emitted metadata before implementation of the consuming parser. E does not wait for final generic UI or result workbench to execute a native example when authorized.

## 10. Lane F prompt: BC2-native publication and review

### Copy-ready assignment

> Implement BC2's model-owned native result parser, typed datasets and model-specific views/components under specification section 5. Keep campaign/arm, attempt, scored draw, retained sequence and target-state structure distinct. Consume E's explicit producer joins and attempt-settings capture. Supply C with one publication interface used for local output and returned remote output. You do not edit generic ingestion/selection or upstream BC2 producer code. No metric flattening, filename identity, fake Designs or second result database.

### Required work

- Account separately for claims, emitted trajectories, scored/passing draws and retained sequences. Preserve failures, reasons, native rank, missingness and optional/suppressed artifacts.
- Preserve target ordering/weights from actual emitted metadata, multichain sequences, per-state native CIF and ancillary declared documents.
- Keep rank as publication metadata, not identity; preserve explicit draw-to-retained association. Ambiguous historical associations remain unknown rather than guessed.
- Map reviewable structure-bearing subjects into existing Designs with C; keep structureless native rows in scientific datasets. Multiple states do not inflate accepted sequence counts.
- Support native rank/filter publication without mutating a sealed parent result or discarding its source records.
- Bounded native table/detail, state/structure selection, settings/attempt context and applicable export/comparison/reopen through existing views.

### Pass/fail

**PASS:** native fixture membership/cardinality reconciles at every level; draw/retained joins survive sorting/truncation; duplicate names and multiple target states stay distinct; suppressed outputs do not create false errors; rank/filter/re-ingestion preserve original identity; local and remote-returned bytes produce the same publication semantics; compatible candidates reach C/B without filename reconstruction.

**FAIL:** `_candidate1` assumed to mean `_seq0`; failed attempts omitted from accounting or turned into structures; state metrics flattened/ordered by the wrong target list; input/output counts invented; accepted count derived from number of CIFs; results only work while the worker is live.

Dependencies: E producer contract, C publication, G exact returned native assets. Label hand-authored fixtures as parser tests; qualify against real native output before claiming integration complete.

## 11. Lane G prompt: existing bridge and startup performance

### Copy-ready assignment

> Deliver existing remote-bridge integration for retained generators, upgraded optional refinement, full BC2 and the experimental LigandMPNN validator. Reuse NativeInvocation, SelectedExecutionPlan, runtime closure, asset stores, worker lifecycle and publication journals. Repair only demonstrated in-scope mapping/lifecycle/warm-path defects. Own the assigned bridge/runtime files; parent handles registry hubs and model lanes own science. Measure cold preparation separately from warm dispatch/native initialization. Do not create a binder-specific runner, transfer path, second cache, watchdog or permanent telemetry subsystem. Do not rent/start/attach/provision/run a worker without the separately delegated authorization.

### Required work

- Explicit selected model/stage dependencies, images, weights, support runtime, portable source inputs/role maps, native output and resume packages. An optional off-stage contributes none.
- Same scientific request locally/remotely; placement may change paths/resources, not scientific settings. Use declared model resource requirements and actual selected allocation.
- Reuse prepositioned immutable assets and compatible runtime/compile caches. Remove demonstrated redundant heavyweight work using current identity/ownership evidence, not weaker reconnect/corruption checks.
- Keep attempt-owned writable space and sealed parent results. Distinguish same-worker continuation, new-worker native resume, review continuation and a new refinement job.
- Preserve stop/cancel ownership through actual writer quiescence, result-return journal recovery and idempotent retry. A dead supervisor PID is not proof its descendants are dead.
- Return native outputs completely, including BC2 hidden resume state when continuation is promised. Reopen results and diagnostics after the worker is unavailable.

### Behavioral pass/fail

**PASS:** every requested supported path compiles the correct selected closure; same input/settings/native output contract survives either placement; no off-stage preparation; unchanged warm assets reused; same-campaign continuation is truthful; returned result ingestion/reopen succeeds; cancellation and interrupted publication retain correct ownership and immutable science.

**FAIL:** local succeeds but remote silently alters settings or drops state; duplicate model-specific remote path; controller paths leak into worker inputs; mandatory whole-workflow asset pack for one selected generator; repeated warm installation/download; shared writable campaign/source; failed stop mistaken for quiescence; transport receipt alone counted as scientific completion.

### Performance pass/fail

Measure on the same qualified worker/storage/network and selected workload before and after the touched path. Use existing bounded task/phase timestamps and a temporary acceptance probe. Distinguish controller compilation, source/input staging, asset/image-view work, dispatch, model initialization/compilation, native work and return. Record bytes/files only where needed to identify the cost; do not add a logging product.

Hard behavioral failures do not depend on a timing threshold: repeated dependency installation; unchanged image/weight payload delivery; unrelated preparation; unnecessary reconstruction of unchanged durable assets; or heavy initialization during form/API discovery.

Before candidate qualification, freeze finite numeric, workload-qualified budgets for the affected warm phases using the observed baseline and identified avoidable cost; state the regression allowance and targeted improvement. The integrator must obtain Christian's decision if the budget changes user expectations; agents MUST NOT invent a universal seconds target, choose a permissive number after seeing candidate results, or omit performance acceptance because no number was previously written. Missing measurements or an unset budget leave performance acceptance BLOCKED. Passing requires removal of the demonstrated waste, meeting the agreed measured budget, and no regression of ownership/reconnect correctness. Native compilation for a genuinely new shape/device remains separately measured, not hidden inside or excluded from the report without explanation.

Use repeated matched warm runs only as needed to distinguish noise from the claimed improvement; do not run an exhaustive performance campaign. An absent approved worker means native/remote acceptance is BLOCKED, not PASS and not permission to rent one.

Dependencies: E/H/D/B model manifests and C exact publication requirements. Existing-path measurement and closure repair start immediately; no reason to wait for BC2's final UI.

## 12. Lane H prompt: LigandMPNN experimental sanity-check validator

### Copy-ready assignment

> Implement Phase 3 as the optional experimental LigandMPNN target amino-acid/structural-context compatibility check, not ordinary binder redesign. B separately owns the optional blind pose/structure check. Make the missing diagnostic executable under Foundry's sole LigandMPNN execution ownership; reuse shared selection/placement/publication, not a nonexistent runner. Preserve declared binder and target patches, evaluate held-out sequence/context on supplied geometry using an explicit leakage-safe policy, and retain the original candidate unchanged. Both assessments must be independently selectable after any round and applicable to user-selected subsets for requeue; their results can optionally be combined under section 13. Do not claim LigandMPNN independently predicts a pose, identifies natural partners, measures affinity or proves binding. Do not invent the remaining native metric, masking policy or negative controls. Preserve raw evidence so the operator can inspect results and define cutoffs afterward. Complete engineering plumbing in parallel; calibration is not a reason to reclassify this as a future feature.

### Required work

- Inspect the actual Foundry/core-job runner and missing typed execution/result pieces. A registry row is not a runnable adapter, and a Project catalogue denial is not sufficient evidence of a missing core path. Complete the existing owner rather than duplicating it.
- Distinguish two independent contracts sharing Foundry: this new binder interface-context diagnostic and the existing chemistry-context LigandMPNN sequence-design obligation in `docs/specs/2026-08-12-protein-in-silico-global-project-integration-sow.md`. Do not claim completion of that Project/ordinary-design path from a working diagnostic, or count ordinary redesign as Phase 3 acceptance. Reuse the sole scientific execution owner where possible; do not import unrelated Project adapter work into this binder SOW.
- The checked baseline contains a chemistry-oriented `ligandmpnn.yaml` with `enabled: true` and `experimental: false`, but no explicit LigandMPNN route in the reviewed entrypoint map or invocation in `workflows/` or `modules/`. `apptainer/foundry.def` installs unpinned `rc-foundry[all]` and permits deferred checkpoint download. These do not establish a ready diagnostic. H must implement the missing executable diagnostic path under Foundry, pin its actual scientific/runtime capability, expose the new operation as experimental and complete selected asset readiness with G. Preserve the existing ligand/metal/nucleotide/DNA mode semantics rather than silently reclassifying or overwriting them; any advertised but unexecutable legacy mode needs its own truthful admission/availability handling at the existing owner. Do not inherit an unrelated registry row's claimed status or first-run download behavior.
- Input binds exact source candidate/state/artifact and round, fixed patch/roles, scoring region, visible sequence/backbone/atomic context, checkpoint, sampling/scoring settings and comparator identity. A later user-selected verdict policy is versioned separately from immutable raw evidence. Keep the surrounding structure when needed; a small scoring region does not imply an excised free peptide.
- Distinguish recovering opposing sequence/context on supplied geometry from generating new geometry. Report exactly which information was supplied and which held out.
- Prevent answer leakage: the residue identities/side-chain atoms being recovered cannot also be exposed as the answer in conditioning. Keep declared context and masking consistent across matched comparisons. Avoid scoring the whole fold and presenting it as interface-specific evidence.
- Verify actual preprocessing/model visibility, not merely a UI residue mask. H must distinguish hiding an entire evaluated patch from single-position scoring while other native patch residues remain visible. A model's single-position conditional score MUST NOT be relabeled blind whole-patch recovery. Packing with withheld identities must not leak their geometry into diagnostic inputs; any such derivative is separately identified. If the pinned Foundry path cannot express the chosen diagnostic, report that exact blocker instead of silently approximating it or adding another implementation.
- Preserve native probabilities/scores/sample outcomes, meaningful missingness and variability. Any sampled sequences are diagnostic artifacts, not automatically adopted descendants. Do not mutate the actual target or binder to manufacture recovery.
- Use an explicitly selected, bounded diagnostic. No recursive generate/rescore-until-pass loop; no best-sample cherry-picking without the declared policy and full sample record.
- Integrate a model-owned typed UI/API and experimental result section in existing candidate review. Offer the LigandMPNN check and B's blind pose check as separate optional actions after a round; allow selecting any compatible subset and requeuing selected assessment or refinement operations as a new child round without overwriting completed results. The same selected operations run locally/remotely using G's closure; disabled means no assets/preparation/run.
- Attach separate execution, qualification and scientific outcomes. C/B combine exact candidate/check identities using section 13 without inheriting conclusions across changed sequences/structures or mismatched target states.

### Pass/fail

**Engineering PASS:** declared masking/context inputs reach native execution unchanged; original candidate is byte-identical; all requested outputs retain identity; native result is parsed and reviewable; exact local/remote request/result parity; off-stage exclusion; runtime errors stay errors; dual-check decision cases are covered; no duplicate runner or mandatory diagnostic.

**Scientific qualification PASS:** verify that the selected, pinned checkpoint and Foundry invocation support the actual protein–protein interface conditioning/held-out region being claimed; the existing YAML's ligand/metal/nucleotide/DNA modes are not such evidence. Show native metric direction, conditioning, controls, and a bounded benign reference set containing meaningful binding and nonbinding/incorrect-context cases, not just trivial gross clashes. Operators can examine exploratory/calibration results *before* choosing score cutoffs; raw metrics and qualification are delivered without a default pass/fail threshold. A user-defined, versioned cutoff then classifies the selected candidate evidence, including explicit re-evaluation of already completed results. Any later claim of independently tested classification must freeze the chosen policy before a distinct held-out assessment, not reuse the exploratory/calibration cases as such evidence; account for related targets/scaffolds and plausible training overlap. Report false rejection and false support where evaluated; do not multiply the two checks as statistically independent probabilities. If the checkpoint cannot support the chosen task, report evidence as inconclusive rather than a binary verdict. This is bounded experimental qualification, not an open-ended benchmarking programme or a universal binder classifier.

**FAIL:** routine redesign presented as the requested validator; leaked held-out residues; supplied pose called recovered; likelihood labeled affinity; runtime error or unrun operation treated as negative evidence; arbitrary built-in threshold chosen for green tests; raw result hidden until a threshold exists; claimed validation based on synthetic fixtures; all candidates pass because one best generated sample was retained; changed candidate inherits another assessment.

If checkpoint applicability cannot be established, preserve raw diagnostic evidence labeled inconclusive and report that scientific qualification is unresolved. Do not pretend a user cutoff cures an unsupported model. In contrast, an applicable, runnable, honestly characterized optional assessment with raw results is a delivered feature *before* the operator chooses a cutoff; no arbitrary binary policy is a delivery gate.

Dependencies: C exact candidate/document and subset/round identity, B actual separately selectable blind-check contract, G runtime closure. Foundry plumbing, typed controls, raw output schema and exploratory execution proceed without a threshold.

## 13. Phase 3 decision semantics

This is a small optional candidate-review rule using existing result owners, not a new scoring engine. The two checks are independently selectable; each records its raw metric/distribution, execution status, applicability/qualification, and exact assessed artifact/state. Without a user-defined cutoff it is **unclassified**, not pass or fail. A later saved cutoff/policy with provenance classifies current or retained prior evidence without changing the underlying scientific artifact or pretending the cutoff preceded the exploratory run.

“Qualified” here is explicitly scoped experimental qualification of the chosen inputs, masking, recovery rule and bounded reference cases. It is not a requirement for a universal classifier, a clinical validation programme, proof of affinity, an invented accuracy target, or proof that LigandMPNN outperforms every existing score. Engineering correctness and the observed scientific usefulness must be reported separately. Do not convert uncertainty into an endless benchmarking prerequisite or silently relax the agreed check to force favorable results.

Apply a combined conclusion only when the user selects both checks and their dual policy, each is classified under its saved cutoff, and both refer to the same candidate scientific identity and declared target context. If either is unclassified, stale, attached to another state, unqualified, unavailable or not validly completed, do not treat it as a failed check.

- **Blind recovery PASS; LigandMPNN context PASS:** both selected checks support the candidate. No claim of experimental binding or calibrated affinity.
- **Blind recovery PASS; LigandMPNN context FAIL:** discordant/mixed evidence; retain both results for review. Do not automatically reject under the dual-failure rule or hide the failed check.
- **Blind recovery FAIL; LigandMPNN context PASS:** discordant/mixed evidence; retain both results. A context score does not erase failed pose recovery.
- **Blind recovery FAIL; LigandMPNN context FAIL:** computationally reject under the selected experimental sanity-check policy. Retain the candidate and evidence; exclusion from the selected shortlist is not deletion of scientific records.
- **Either check unclassified (no operator cutoff), inconclusive, unqualified, skipped, unsupported, cancelled or errored:** combined assessment incomplete/inconclusive, not dual-failure rejection. Preserve whatever valid single-check evidence exists.
- **Both checks off:** unassessed, not failed. Generator-native acceptance remains visible separately.

Do not force both checks merely to inspect or retain a candidate. A single chosen check's raw evidence and optional user-classified result remain useful on their own. If the operator requests the dual-check policy, both checks must complete validly and be classified under that policy before it can produce a definitive dual-check conclusion. Do not turn “one passed” into a universal validation pass.

### Native method verification and operator cutoff controls

H and B produce one concise decision packet, not another general review cycle:

1. Verify the patch/direction(s), fixed geometry/sequence, held-out information and native model visibility for the operator-selected region. Support antibody and generic region selection without guessing a universal CDR policy.
2. Expose the native context-recovery/compatibility metric and matched comparator, sample distribution and limitations. Exact sequence recovery alone is not automatically a calibrated binding metric; no composite affinity score.
3. Verify the blind predictor's input and identify a pose/context comparison that does not feed the candidate pose as its own answer; preserve independently selectable controls and raw outputs.
4. Show exploratory/calibration results first. Provide controls and bounded held-out cases to characterize each check; let the operator define/version cutoffs afterward, then explicitly classify/reclassify evidence under the selected policy. Freeze that policy before any later *independent* held-out claim. No default pass/fail threshold is a prerequisite for the feature or for subset requeue.

The optional dual-failure policy, separate selectable checks, later user-defined cutoffs and post-round subset requeue are already Christian's direction. These questions characterize evidence and executable model limits; they are not permission to postpone the workflow.

## 14. Execution order with real concurrency

### Start together

Parent verifies current base and assigns exact ownership. A/B/C/D start Phase 1 refactor/defect fixtures. E/F start the full BC2 inventory/native boundary. G starts existing bridge mapping and measured baseline preparation. H starts Foundry/diagnostic contract inspection and masked-context plumbing. No lane waits for all of Phase 1 to finish.

At the same early handoff, D presents concrete pairwise contact method/reference options and their literature basis to Christian. B/H verify native input visibility and produce the separate optional check/result contracts. Do not invent a binary default verdict or substitute an unrelated metric. Native raw results, subset requeue and remote-bridge work do not wait for user-defined cutoffs.

### Integrate contracts early

Parent resolves I1–I4 with the relevant owners and lands the smallest shared hooks before dependent production adapters diverge. E/F close their explicit native metadata association. C's immutable selection/publication work unlocks common end-to-end flow without waiting for every model's runtime.

### Merge coherent slices continuously

Parent integrates tested source changes onto a current shared integration worktree. Respect whole-file owners. Consumers exercise real exported interfaces promptly; do not postpone all integration to the end or run independent forks that only meet in the final merge.

E/F/G qualify BC2's native-to-returned-result route while A/C integrate its controls/review. B/D/C qualify retained methods and multiround continuation. H/B/C/G qualify the experimental checks after the scientific choices. None becomes a second application.

### Combined verification, authorized native acceptance and deployment

Run the focused combined-tree suites, one bounded adversarial cross-lane review, authorized native local/remote acceptance and measured warm-path checks. Correct findings within the same owners. No repeated broad audit unless a material architecture/science change warrants it.

BC2 and all supported selected workflow operations must run on **our existing remote bridge** using installed/downloaded pinned model assets; the worker's rental status does not by itself make our internal run a third-party hosted product. Do not defer remote execution or impose a fictitious internal-remote licensing blocker. If the BMS API/UI is actually offered to third-party users, separately review upstream/dependency terms and verify access controls before enabling that audience. When implementation/deployment is authorized, parent alone reconciles current `origin/test`, follows the existing runtime authority freeze/bind and Development sync procedure, verifies remote ref plus canonical/API/frontend and the worker release actually consumed, and reopens the returned results. Do not enable an automatic sync timer or touch `main`/Production. No deployment while active staging makes it unsafe. Preserve a known-good rollback revision without weakening runtime authority checks.

One integrated release can include all three phases. Incremental code integration is allowed; declaring the overall upgrade complete before all three acceptance gates close is not.

## 15. Requirement coverage and acceptance evidence

Use one compact requirement-to-test/evidence list in the implementation handoff or existing tracker, not a new runtime ledger/telemetry service. Each item has one accountable owner even when another lane supplies a dependency. Existing specification acceptance A01–A14 remains required, with LigandMPNN updated to Phase 3 here.

### Phase 1 gate

- **P1.1 (A):** agnostic modality/generator authoring with every retained route and model-correct controls; no silent coercion.
- **P1.2 (B):** genuinely optional, separable, repeated refinement on a post-round selected subset; real checkpoint limits; native sample/structure/metric fidelity.
- **P1.3 (C):** exact immutable subset selection, native-format/state identity, per-item parent lineage, failure-isolated publication and reopen; untouched siblings remain untouched.
- **P1.4 (D):** retained alternative methods/analyses integrated and qualified, with complex-contact science explicitly resolved rather than substituted.
- **P1.5 (G):** whole selected workflow/continuation supported by the existing bridge; lean warm operation and measured behavior.
- **P1.6 (parent):** all existing defect items C01–C14 closed with focused integrated evidence; non-antibody source selection passes the server's root/ownership/admission/launch boundary, saved generic requests survive server template read/write, and no existing supported generator/operation is lost.

Defect ownership: C01 parent+A; C02 B; C03 B; C04 B+A+C; C05 B; C06 B+C; C07 B+C; C08 C+D; C09 C+parent; C10 C; C11 C+B; C12 A+parent; C13 G; C14 A+B+C+D+G with parent accountable. An overlap names producer/consumer cooperation, not simultaneous write permission.

### Phase 2 gate

- **P2.1 (E):** complete native field/action/modality/objective inventory and typed UI/API/compiler/persistence coverage.
- **P2.2 (E):** faithful bounded native execution, GPU ownership, adaptive settings, sweeps, optional outputs and truthful resume.
- **P2.3 (F):** exact attempt/draw/retained/state publication, joins, native formats and native rank/filter semantics.
- **P2.4 (A+C+G; parent accountable):** first-class launcher, review/selection/refinement of a provenance-bound real native BC2 candidate, effective/attempt-settings readback from the job detail surface, full selected runtime closure and identical local/remote publication semantics. A zero-yield BC2 run establishes execution accounting only; a candidate from another generator cannot substitute for BC2 handoff evidence.

No unexplained native field exclusions. Exercise all declared feature mappings and material incompatibilities with generated/differential tests. Real native acceptance covers materially distinct execution/input/output families, with every supported modality assigned explicit evidence; shared-path coverage must state why it applies. Do not demand every combination of every setting as a separate GPU campaign, and do not use one successful VHH run as evidence for all branches.

### Phase 3 gate

- **P3.1 (H):** selected masked-context diagnostic is actually native-executable, not a redesign placeholder.
- **P3.2 (H+B):** separate LigandMPNN context and blind pose assessment inputs/independence/leakage claims verified; selected checkpoint's protein-interface applicability demonstrated, raw metrics and calibration evidence available before operator threshold choice. A user-specified policy/version can later classify retained raw results. Ordinary Foundry sequence-design availability is a separate acceptance record, not a substitute or an added binder prerequisite.
- **P3.3 (C+H):** independent optional native diagnostic and pose evidence, selected subsets and descendant rounds, and any user-classified/dual-check outcomes persist/reopen with exact identity; runtime failure never becomes a scientific reject.
- **P3.4 (G+A):** optional typed UI/API, placement parity, off-stage exclusion and no mandatory startup/setup costs for unselected diagnostics.

### Cross-phase gate

- **X.1:** no competing scientific authority, new lifecycle service or duplicate result store.
- **X.2:** focused suites executed on the combined revision; exact skips and pre-existing failures disclosed by test case, not concealed in aggregate counts.
- **X.3:** authorized native request-to-result runs and remote result readback, not merely provisioning, mocks, an enabled registry or a successful subprocess exit.
- **X.4:** cold versus warm measurements and the agreed workload-qualified warm budget; no false speed claim from fixture timings.
- **X.5:** stop/interruption/return/re-ingestion/continuation tests at changed boundaries; necessary ownership/integrity guarantees preserved.
- **X.6:** source, deployed service and consumed worker artifact identities verified separately when deployed; all surviving results reopen without the worker. Internal use through our remote bridge is required, not a third-party-hosting exception. If third-party API/UI access is actually enabled, separately establish licensing and audience controls before enabling it.

A valid bounded generation run may produce zero accepted candidates. That can satisfy execution/failure-accounting checks, but does not exercise candidate selection/refinement. For BC2 handoff acceptance, use a real, provenance-bound BC2 structure-bearing candidate from the pinned native engine (accepted or explicitly reviewable under the declared policy); follow its exact state through selection and a compatible refinement/validation operation. A different model's candidate cannot fill this gap. Do not fabricate one or loosen native filters to conceal the gap.

## 16. Evidence standard and review economy

For each owner, record only what permits verification: requirement IDs, exact changed paths/commit, focused commands, machine-readable test output including skips, real native job/artifact references when run, and measured launch-phase evidence where relevant. Do not commit generated scientific outputs, local databases, caches, credentials or logs into the repository.

Small fixtures test parsing, identity, request serialization and fault handling. They are explicitly not evidence that a model, remote bridge or scientific criterion works. Actual native acceptance must exercise the released adapters and data formats; verify the exact result target after external writes.

Use checks at their owners: request admission, selected immutable artifact, native publication and remote transition. Reuse their established results. No cascades of duplicate validators, full-tree hashes at every layer, repetitive approval digests or broad audits after every small patch.

Parent accepts a child patch only after inspecting the diff and reproducing consequential tests against the integrated tree. Child completion is a report, not closure authority. A blocked native test stays blocked until executed or Christian explicitly changes the requirement.

### Reuse these focused test owners

Paths verified during planning; extend only those affected by the change. These are starting points, not a mandatory command to run every listed file after every patch.

- A: `platform/frontend/tests/antibodyDenovoBoltzgenScaffold.test.ts`, `platform/frontend/tests/antibodyTargetParseLifecycle.test.ts`, `platform/frontend/tests/retiredWorkflowRemovalContract.test.ts` when BC2 is surfaced, mounted launcher-card and settings/round-trip suites. Parent adds focused `jobs.py` generic-source admission/launch and `user_templates.py` save/load regressions. Preserve the test file's configured runner; do not assume every frontend suite uses the same config.
- B: `tests/test_antibody_denovo_precollected_maturation_contract.py`, `tests/test_antibody_fampnn_native.py`, `scripts/test_ppiflow_maturation_contract.py`, and focused native sample-pairing regressions at the changed adapter.
- D: `platform/api/tests/test_caliby_sequence_design_regressions.py` and existing FrustraMPNN parent/component tests; B owns shared workflow assertions, C shared publication assertions.
- G: `platform/api/tests/test_remote_workflow_closure.py`, `test_remote_independent_provisioning.py`, `test_component_runtime.py`, `test_remote_warm_work.py`, `test_resume_remote_placement.py`, `test_remote_worker_lifecycle_gaps.py`, `test_remote_result_generation.py` and `test_remote_transport_recovery.py` under that same test directory.
- C/E/F/H add focused identity/native-publication/diagnostic cases to the current subsystem test layout, including job-detail effective/attempt-settings visibility and the separate Foundry-design versus diagnostic status. Separate field/compiler coverage from parser fixtures and from actual native acceptance. A new test harness or generic validation service is not required.

The API's documented focused entrypoint is `uv run --frozen --group dev python -m pytest <affected tests>` from `platform/api`; use the established isolated test environment and machine-readable results where required. Read the owning frontend/test configuration before invoking its focused runner. Do not alter runtime behavior to compensate for a missing test dependency.

## 17. Dispatcher packet and closeout

Every implementation dispatch includes:

- Current verified base, assigned worktree/branch and explicit no-push/no-deploy boundary.
- This document's scope/rules and the lane's complete prompt, required work and pass/fail block.
- Exact file/symbol ownership, parent-owned hubs and dependent lane contacts/interfaces.
- Relevant specification sections, C/A/P/X acceptance identifiers and existing evidence to reuse.
- Which tests are authorized, which native inputs/hardware are approved, and which scientific choices remain undecided.
- Required handoff format from section 2. No request to independently declare the entire project complete.

Final parent closeout reports Phase 1, Phase 2 and Phase 3 separately as implemented, integrated, native-tested, remote-tested, deployed and scientifically qualified where applicable. An outstanding mandatory gate means unfinished work, not permission to issue a smaller completion claim.

**Strict final instruction:** implement Christian's workflow and these three tranches. Simplify implementation machinery, not the agreed outcome. Do not add unrelated work, invent missing science, or call an unproved path complete.
