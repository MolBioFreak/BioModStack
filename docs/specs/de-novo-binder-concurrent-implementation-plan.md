# Binder workflow completion: implementation packet

Status: final V2 implementation packet. Replaces the historical dispatch prompts in this file, including obsolete absence claims and the superseded classification-policy work. Read the controlling [completion specification](de-novo-binder-harmonization.md), [complete acceptance scope](de-novo-binder-design-upgrade.md) and [product outline](de-novo-binder-upgrade-outline.md) together. This packet specifies implementation; it does not claim implementation or native acceptance occurred.

Binder review baseline: `77c5c5a2c952dfd70dad904352a5c4b7ce52cb13`. Final canonical/remote source: `d956efad063388d72f5a21aaf8ad1612375c6dda`, clean, with only unrelated BioXP changes since review. Implementation uses then-current `origin/test`, reconciles relevant deltas and preserves delivered BC2, selected continuation, analysis, publication and bridge behavior. Source advance alone is not deployment evidence. Do not redispatch the original eight lanes as if none of their work had landed.

## 1. Binding outcome

Deliver the entire De Novo Binder Design workflow, not just UI or a new wrapper:

- Four first-class starting generators: BC2, BoltzGen, PPIFlow and RFantibody. Wider native BoltzGen and genuinely agnostic native PPIFlow generation require backend/script/Nextflow work as well as form changes.
- PPIFlow initial generation and our narrower optional refinement-loop use are separate required deliveries. Preserve, upgrade and verify the latter while integrating the former. Neither substitutes for the other.
- Full applicable source acquisition, independently inspectable target/template/seed, full shared Mol* and synchronized sequence/chain/residue tools; populated drafts survive native route switches and save/reopen.
- Existing Project setup/launch/resource/reopen integration, exact candidate/document/state selection, independently chosen operations and repeated descendant rounds.
- Model-native settings/results and existing local/remote bridge, lifecycle and publication authorities.
- Substantive review and necessary repair of the reachable Python, scripts, Nextflow, data processing and runtime path for correctness, sane hardening, efficiency and measured speed: W01–W08 in the controlling specification.

RFD3 remains separate De Novo Design, not a fifth binder generator. Full FrustraMPNN, Caliby, FA-MPNN/ProteinMPNN, supported prediction and existing GROMACS handoff remain. Preserve supported historical meanings. Pairwise Frustratometer is deferred. Cutoff-policy CRUD, saved classification/reclassification and automatic dual-pass/fail/rejection are excluded; independent raw diagnostics remain required.

Optional to execute is not optional to deliver. Do not shrink accepted scope, invent science or add a proof-based runtime refusal. Existing native-input, ownership, integrity and lifecycle behavior remains unless a specified correction changes it; an exact additional restriction or consequential science/default change goes to Christian first. No jobs, paid rental/start, cloud attach/provision, service mutation or deployment is authorized by this document.

## 2. Interfaces and concrete route decisions

These are small extensions of current owners, not new universal frameworks. Land shared seams early; independent native/source review and UI composition can proceed concurrently. A missing scientific decision affects only that behavior, not unrelated work.

### I1. Model-owned requests and native generation

Use current `JobCreate`, model YAML, schema/compiler, requested/effective settings, placement and native publication. Keep scientific request separate from placement/Project metadata. Preserve explicit false/zero/null/empty versus omission. Do not impose BC2 preview/digest semantics on every engine.

**BoltzGen:** use the existing `boltzgen` model owner for new public native generation. Retain existing mode meanings; complete `nanobody_binder` and `peptide_binder`, add `protein_binder`, and map any additional supported antibody format to a native-confirmed schema/control path rather than treating a generic protocol name as proof of all antibody assemblies. Existing ligand/nucleotide modes and historical antibody/child routes keep their meanings. Reuse current preparation, wrapper/module and child mechanisms. A thin public entrypoint may be needed to avoid child-only `parent_job_id`/orchestrator assumptions; do not invent a parent Job merely to satisfy them.

**PPIFlow:** add the currently absent `platform/api/config/models/ppiflow.yaml` with separate `protein_binder`, `antibody_binder` and `nanobody_binder` generation modes. These are proposed BMS route identifiers for the native-confirmed binder and antibody/nanobody generators, not claims of an existing API. Use new `workflows/ppiflow_generation.nf` for initial generation; retain `workflows/ppiflow_generator_design.nf` and historical `antibody_denovo/generator_backbone_refine` interpretation for their actual seed/partial-flow use. Add only narrow preparation/invocation adapters needed to call native entrypoints. Do not reimplement the sampler or force generation through `RunPartialFlow`.

B inventories exact native settings/defaults/inputs/checkpoints and supplies the closed per-mode contract before A builds dependent scientific controls. Parent registers the precise routes and schema; G supplies selected runtime closure. Native paths and source identity are already recorded in the controlling specification and external PPIFlow correction. Runtime completeness and execution still require evidence.

**Concrete existing blocker:** `resolve_nextflow_entrypoint()` currently rejects model IDs `boltzgen` and `ppiflow` before consulting the mode map; related normalization/catalogue/tests retain internal-engine restrictions. Parent replaces that blanket restriction only for the implemented typed mode paths, updates discovery/normalization consistently and preserves genuinely unsupported/retired-route behavior. Adding YAML or a map entry behind the existing guard is not a route. Update the corresponding assertions in `test_antibody_remote_closeout.py` and `test_workflow_capability_boundaries.py` deliberately; do not simply delete their unsupported-request coverage.

One generation request must trace through browser/agent → normalization → registry → exact Nextflow entrypoint → native consumer → producer publication → results/Project → compatible selection. No frontend-only format rename or fallthrough to unrelated `protein_design.nf`.

### I2. Workspace/source handoff

A creates `BinderWorkflowWorkspace.tsx` as presentation-only chrome with `title`, `description`, `sections`, `activeSection`, `onSectionChange`, `engineChooser`, `children`, `summary`, `executionControls`, `submitControls`, `library` slots/callbacks. The controlling spec fixes sections U01–U10. No step-completion gates or universal scientific request.

Extend current `BinderNativeRoute` rather than adding a second route coordinator. Keep its native `{modelId, mode}` or `{templateId}` destination and optional typed source/draft handoff. Reuse materialized source references, role selections and per-model drafts; do not persist File or Mol* objects. Destination draft takes precedence; only untouched compatible fields inherit source context. Keep pending-route/revision protection against late URL/load responses and deliberate-clear overwrite.

A owns generic acquisition/full-view adapters; E retains BC2-specific conversion and controls. Extend `SelectedTarget`/existing source owners for Project and exact saved document/state because `upload|run|preset|rcsb` does not already represent these. Reuse governed file/RCSB/Job/library/Project APIs, preserve native bytes and role maps, and do not introduce a universal BC2 request type.

### I3. Exact selected documents, not a new candidate store

Retain `POST /api/binder-continuation/selected`, existing operations and current fields. Add optional `candidate_documents: dict[str, CandidateDocument]`, keyed by selected Design ID, using the existing shape `{artifact_id?: str, target_state?: str}`. Omission preserves current primary-document selection. An explicit selector resolves the producer-bound owned artifact/state before immutable snapshotting. Refactor reusable resolution out of its diagnostic-specific owner only as needed; do not duplicate its scientific-identity logic.

Preserve current same-root ownership semantics, per-item parent and native/derived maps. One explicit document per Design per request retains unique-ID behavior; distinct state invocations may be separate child requests. Do not loosen legacy antibody-only routes or admit unrelated global IDs merely because a picker returned them. Native row navigation targets exact document/Design identities.

Use the existing `NativeInvocation`/`SelectedExecutionPlan`, request/result contracts and publication authorities for local/remote execution. Prepared review retains materialized bytes, allocated output and exact requests; no resnapshot/reselection on approval. Sequence/coordinate changes create descendants with fresh assessment state; read-only diagnostics retain originals. Valid zero yield and unresolved association remain observations, not fabricated Designs or new refusal flags.

### I4. Project and fan-out

Membership uses current `JobCreate.launch_context_id`, not a new `project_id`. Carry an optional destination `launch_context_id` through single-Job selected/lifecycle handoffs and their prepared remote request. Parent extends existing Project preparation to allocate distinct run-attempt contexts for fan-out, one per resulting Job. Never reuse an already-consumed parent context. Do not encode hierarchy inside scientific `params` or infer it from imported sources.

Project-originated setup/drafts open the same native UI and report settings/source updates through existing callbacks. Parent extends capability/native-owner registrations and native publication-backed resource adapters. A relays draft/handoff in `JobSubmission`; parent owns Project pages/services. Preserve standalone work and explicit cross-project destination. Native campaigns and diagnostics with zero Designs need native result adapters, not fake Designs or weakened generic Design-set validation.

### I5. Producer/publication boundary

B/E/H/D supply actual model-owned output identities and readers to C/F. Separate attempt/sample/candidate/state, native rank, native/derived format and intentionally absent artifacts. Parent scientific ancestry, scheduler parent and Project membership are different fields. Shared envelope connects results; native readers remain numerical authorities.

Preserve primary publication under optional failure through the real finalizer/manual return/retry. Local and returned remote bytes use the same parser. BC2 nested campaign root, explicit draw-to-retained joins, hidden resume state and historical publication inventory stay with existing E/F owners. No reimplementation of these delivered contracts.

## 3. Sole-writer map

Letters identify accountable owners, not a requirement to launch eight agents or create eight worktrees. Reuse one short-lived integration checkout where possible; disjoint writers may share it only with explicit path ownership and no resets/rebases/staging by children. If isolation is necessary, give it a named reason and integrate/remove it afterward. Canonical deployment checkout remains read-only. Verify every writer's real base/root/status; a prompt does not create isolation.

Files below are exact anchors. A new file must be assigned before creation; moved paths are substituted once after checking the implementation baseline. No directory grant or parallel edits to the same file. Tests follow their sole writer. Parent alone stages exact paths, integrates, commits and closes acceptance.

### Parent: shared contracts, Project and integration

Sole writer for `platform/api/routers/jobs.py`, `routers/user_templates.py`, `schemas.py`, `model_registry.py`, `antibody_pipeline_contract.py`, `services/workflow_request_types.py`, `services/nextflow.py`, `component_runtime.py`, `native_components.py`, `nextflow_schema.json`, shared scheduler registrations and any justified migration.

Project ownership: `platform/api/services/protein_project_capabilities.py`, `workflow_adapter_registry.py`, `global_experiments/workflow_setups.py`, `launch_contexts.py`, `project_datasets.py`, `adapters.py`, `result_surfaces.py`; `platform/api/routers/project_manager.py`; `platform/frontend/src/components/project-manager/ProjectWorkflowSetup.tsx`, `ProteinProjectWorkspace.tsx` and directly affected existing Project helpers. A alone edits shared `JobSubmission.tsx`.

Own combined API boundary/Project/registry tests, frontend test registration/build configuration and packet reconciliation. Import exact adapter exports from others; no second default/identity/placement implementation. The existing generic continuation already works: do not restart the obsolete antibody-root generalization plan.

### A: authoring, source tools and selected workspace

Sole writer for `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`, `BinderGeneratorChooser.tsx`, `JobSubmission.tsx`, `workflowModelInventory.ts`, `deNovoGeneratorSelection.ts`, `TargetAntigenSelector.tsx`, `EpitopeMolstarViewerImpl.tsx`, `BinderSelectedControls.tsx`; `platform/frontend/src/lib/launcherCatalog.ts`, `binderContinuation.ts`; new presentation-only `BinderWorkflowWorkspace.tsx` and assigned native BoltzGen/PPIFlow form/draft helpers.

Own only directly affected shared source/full-workbench adapters after naming their paths; E owns BC2-specific source internals, H/D their scientific controls. Parent owns frontend runner registration. Model YAML stays with B/E/D/H, not A. Requests derive from model schemas.

Deliver U01–U04 and U05–U08 presentation plus Project handoff, truthful collapsed generator choice, exact document selection, ESMFold2 saved hydration and real source controls. Remove RFD3 from binder generation without changing its product. W02/W05: eliminate duplicated new-write/hydration logic, races and unnecessary parse/viewer work. No null-picker tests as source-parity evidence.

### B: wider native generation and retained refinement

Sole writer for `platform/api/config/models/boltzgen.yaml`, new `ppiflow.yaml`, `antibody_denovo.yaml`, `binder_refinement.yaml`; `platform/api/services/boltzgen_request_compatibility.py`, `boltzgen_scaffolding.py`; `platform/api/routers/boltzgen.py`; `scripts/prep_boltzgen.py`, `scripts/lib/boltzgen_inputs.py`, `scripts/run_boltzgen_wrapper.py`; `workflows/boltzgen_child.nf`, `modules/boltzgen.nf` and any assigned thin public BoltzGen entrypoint.

PPIFlow/refinement: `workflows/ppiflow_generation.nf` (new), `workflows/ppiflow_generator_design.nf`, `workflows/binder_refinement.nf`, `workflows/antibody_denovo.nf`, `workflows/maturation_child.nf`, `modules/ppiflow.nf`; `scripts/prepare_ppiflow_maturation.py`, `validate_ppiflow_roles.py`, `validate_ppiflow_masks.py`, `ppiflow_sample_identity.py`, `ppiflow_coordinate_changes.py`, `prep_binder_fampnn_constraints.py`; `apptainer/ppiflow.def`. Assign narrow new native-generation adapters here before writing them. C owns `publish_binder_refinement.py`.

Complete agnostic generation and separately retain/upgrade/verify refinement. Preserve actual checkpoint/role limits, independently selected operations, native samples and fresh descendant state. Fix target omission, forced VHH/light-chain mismatch, case-changing chains and truthful cardinality. W01–W04/W07/W08 apply to the entire reachable path, including retained ancillary/IgGM/prediction branches; a source audit is not permission to drop them. H owns separate blind-pose diagnostics; B owns predictor stages embedded in its workflows.

### C: selection, shared publication and result navigation

Sole writer for `platform/api/routers/binder_continuation.py`; `platform/api/services/binder_continuation.py`, `binder_diagnostic_selection.py`, `result_ingester.py`, `stage_review.py`, `result_state_integrity.py`, `core_protein_result_contract.py`, `result_contracts.py`; `scripts/publish_binder_refinement.py`; `platform/frontend/src/components/ResultsViewer.tsx`, `JobDetailsPanel.tsx` and assigned shared result-navigation helpers.

Implement I3 and consume parent-owned Project context interfaces. Retain actual ownership, snapshots, native formats, primary isolation and exact result identity. Integrate model-owned readbacks without flattening metrics. W01/W02/W04/W07/W08: review transaction/finalizer boundaries, replay/return, duplicate ingestion, directory scans, bounded queries/memory and native zero-Design behavior. Supply Project adapters with existing native readers; parent owns Project adapter files.

### D: retained analysis/alternative owners

Sole writer for `platform/api/config/models/caliby_binder.yaml`, `workflows/caliby_binder.nf`, `modules/caliby.nf`, `scripts/run_caliby_sequence_design.py`, `scripts/prep_caliby_binder_constraints.py` and explicitly named affected existing FrustraMPNN settings/jobs/fan-out/result owners. Existing `platform/api/services/frustrampnn/jobs.py` belongs to D when touched; parent owns shared Project adapters and hubs.

Preserve full global FrustraMPNN and selected Caliby, relevant UI/API settings, masks, runtime inputs, native outputs and workbench. Resolve current FrustraMPNN placement at its own scheduler/bridge owner with G rather than forcing a generic target override. No local reduced analysis or cross-chain reinterpretation. W01–W04/W06–W08 apply to affected methods. B alone wires shared parent workflows; C alone edits generic ingestion.

### E: BC2 native request, controls and lifecycle

Sole writer for `platform/api/config/models/bindcraft2.yaml`; `platform/api/services/bindcraft2_launch.py`, `bindcraft2_runtime.py`, `bindcraft2_native.py` and assigned typed resolver/inventory owners; `platform/frontend/src/components/BindCraft2Campaign.tsx`, `BindCraft2Settings.tsx`, `BindCraft2StructureInputs.tsx`; `platform/frontend/src/lib/bindcraft2Lifecycle.ts`, `bindcraft2StructureInputs.ts`; `workflows/bindcraft2.nf`, BC2 module/scripts and `apptainer/bindcraft2.def` when a demonstrated correction requires them.

Retain delivered inventory, generated/typed overlay separation, launch/action/resume and source tools. Finish full-view/source/Project interfaces with A/parent and native feature-family evidence, not another engine wrapper. F owns native results/publication; G owns shared runtime registration. Review changed native boundaries under W01–W08 without altering native scheduling/filter/rank semantics or inventing startup proofs.

### F: BC2 publication and native review

Sole writer for `platform/api/services/bindcraft2_publication.py`, `bindcraft2_result_readback.py` and assigned BC2 output parser owners; `platform/frontend/src/components/BindCraft2NativeResults.tsx`, `BindCraft2JobResults.tsx` and model-native readback helpers/tests.

Preserve delivered nested roots, explicit producer joins, campaign/draw/retained/state accounting and historical inventories. Finish exact row/document navigation, usable settings/native review and Project zero-yield adapter exports. Review native processing/query efficiency and lifecycle evidence with C/G. Do not change E's producer source or C's shared ingestion directly.

### G: existing bridge/runtime and performance

Sole writer for affected functions in `platform/api/services/remote_execution/bundle.py`, `cache.py`, `images.py`, `managed_inventory.py`, `critical_runtime.py`, `executor.py`, `result_generation.py`; `scripts/lib/portable_inputs.py`, `runtime_image_views.py`; `platform/api/tools/bms_remote_worker.py`, `bms_artifact_cache.py`, `bms_container.py`, and exact shared runtime/dependency manifests assigned at dispatch.

Extend selected closure for native PPIFlow generation, wider BoltzGen, selected documents and Project/prepared actions. Parent integrates descriptors into shared compiler/registry hubs. B/E/H own their native image definitions. Preserve current process ownership, private writes, return journals and native resume; no second scheduler, cache, transfer service or watchdog.

W01/W03/W04/W06–W08: measure and repair demonstrated staging/extraction/scan/hash/initialization waste. Distinguish cold, warm, native compute and return. No universal timing promise or proof-based startup refusal. Verify final process names/I/O against `NativeInvocation`/`SelectedExecutionPlan`, not just total assets.

### H: independent raw diagnostics

Sole writer for `platform/api/routers/ligandmpnn_interface_context.py`, `binder_blind_pose.py`; assigned model-owned diagnostic services/readers; `scripts/run_ligandmpnn_interface_context.py`, `stage_ligandmpnn_interface_context.py`, `run_binder_blind_pose.py`; `workflows/ligandmpnn_interface_context.nf`, `binder_blind_pose.nf` and corresponding modules; `platform/frontend/src/components/BlindPoseSelectedControls.tsx`, `BinderDiagnosticRawResults.tsx`; scoped diagnostic fields in `platform/api/config/models/ligandmpnn.yaml` and `apptainer/foundry.def` only if required.

Credit existing routes. Finish actual conditioning/visibility, raw native execution/publication, selected document and Project interfaces with C/parent. Preserve existing chemistry-context design meanings. No ordinary redesign substituted for diagnostic, no affinity claim or supplied-pose recovery claim, no classifier/threshold policy. Scientific methods not already established require Christian's decision rather than an invented masking/comparison protocol. W01–W04/W06–W08 apply at these boundaries.

## 4. Work order and handoffs

1. **Bind current source and shared seams.** Parent checks current base and exact path owners, replaces blanket engine restrictions only for the intended typed contracts, and lands minimal request/source/document/Project extensions. B provides native settings/mode contracts; C/parent settle document and fan-out context transport. No broad restart of the completed audit.
2. **Parallel coherent slices.** A handles shared source/workspace/drafts; B implements separate PPIFlow generation and wider BoltzGen while retaining refinement; C/parent finish exact selected/native/Project links. D/E/F/H correct and qualify their existing owners rather than rebuilding delivered paths. G works selected closure and measured existing-path costs concurrently. Dispatch only bounded ready slices, not every letter mechanically.
3. **Integrate early and exercise real consumers.** Whole-file ownership remains stable. Integrate native contracts with mounted request tests and actual compiler/graph tests promptly. Per-owner W review follows the same path, produces necessary fixes and deletions, and supplies before/after evidence. Do not leave efficiency as an unassigned final audit.
4. **Combined verification.** Run affected suites on one combined revision; verify collected files/test cases, inspect differences against base and cover intersecting consumers. One bounded cross-lane review checks delivered scope/identity/Project/graph/performance; avoid repeated broad audits.
5. **Authorized native/lifecycle/deployment evidence.** Use approved benign inputs/resources through real model-owned paths. Missing authorization holds only the relevant execution lane. When authorized, parent follows current Development release procedures, verifies source/API/frontend/consumed worker separately and reopens returned native/Project results. No implicit `main`/Production changes or new timers.

Every handoff records base/commit, exact owned paths/deletions, interfaces, requirement IDs, actual test commands/results/skips and native Job/artifact references if run. Child claims are not completion evidence until checked. Do not copy an old whole file over a newer shared owner: compare bases and merge scoped changes. Children never push/deploy, change science or close tracked work.

## 5. Focused test owners and executable checks

These existing paths were checked at the source baseline. Extend the affected suite rather than build a general harness. Recheck current runner registration before adding a new file. Commands below are implementation verification instructions, not results of this documentation pass.

- **A:** `platform/frontend/tests/vitest/binderAuthoringMounted.test.tsx`, `binderAuthoringShell.test.tsx`, `binderSelectedControlsMounted.test.tsx`; `platform/frontend/tests/antibodyTargetParseLifecycle.test.ts`, `antibodyDenovoBoltzgenScaffold.test.ts`. Add real populated source → engine/mode/section switch → emitted native request → save/reopen cases, late-load/clear races, exact document selection and visible full viewer. Parent registers new suites.
- **B:** `platform/api/tests/test_binder_refinement_harmonization.py`, `test_boltzgen_request_compatibility.py`, `test_boltzgen_native_wrapper_chain.py`, `test_boltzgen_runtime_regressions.py`, `test_ppiflow_coordinate_changes.py`; `tests/test_binder_structure_roles.py`, `test_boltzgen_native_transport.py`, `test_antibody_denovo_precollected_maturation_contract.py`; `scripts/test_ppiflow_maturation_contract.py`. Add new initial-generation contract/transport tests separate from retained refinement coverage, with per-mode schema/native differential coverage.
- **C:** `platform/api/tests/test_binder_continuation.py`, `test_binder_selected_design_ownership.py`, `test_binder_diagnostic_selection.py`, `test_binder_shared_publication.py`, `test_selected_binder_publication.py`, `test_lineage_native_authority.py`. Cover native CIF/derivative and alternate-state snapshots, same-root siblings/unrelated source behavior, optional failure through finalization/manual return and repeat-round/replay identity. When changing ingestion ownership, run the full affected ingestion component suite, not only a new finalizer case.
- **D:** `platform/api/tests/test_caliby_sequence_design_regressions.py`, `test_frustrampnn_child_jobs.py`, `test_frustrampnn_parent_wiring.py`, `test_frustrampnn_global_request_contract.py`, `test_frustrampnn_result_ingestion.py`, `test_remote_frustrampnn_self_contained.py`; affected full FrustraMPNN workbench suites, not reduced binder-only mocks.
- **E:** `platform/api/tests/test_bindcraft2_typed.py`, `test_bindcraft2_native_boundary.py`, `test_bindcraft2_launch.py`, `test_bindcraft2_lifecycle.py`, `test_binder_bc2_job_lifecycle.py`; `platform/frontend/tests/vitest/bindcraft2CampaignWorkspace.test.tsx`, `bindcraft2StructureInputs.test.tsx`, `bindcraft2AuthoringUpgradeMounted.test.tsx`, `bindcraft2SettingsDesigned.test.tsx`, `bindcraft2SettingsRepresentations.test.tsx`. Preserve native source-qualified inventory and real preview/action binding.
- **F:** `platform/api/tests/test_bindcraft2_publication.py`, `test_bindcraft2_nested_publication.py`, `test_bindcraft2_native_results.py`, `test_bindcraft2_finalizer_binding.py`, `test_bindcraft2_native_readback_route.py`, `test_bindcraft2_selected_cif.py`; `platform/frontend/tests/bindcraft2NativeResults.test.tsx`, `tests/vitest/bindcraft2JobMounted.test.tsx`. Extend real declared materializer layout, history, zero yield and exact candidate navigation.
- **G:** `platform/api/tests/test_binder_selected_remote_closure.py`, `test_binder_remote_harmonization.py`, `test_binder_bc2_remote_lifecycle.py`, `test_remote_workflow_closure.py`, `test_remote_independent_provisioning.py`, `test_remote_warm_work.py`, `test_remote_result_generation.py`, `test_remote_transport_recovery.py`, `test_remote_worker_lifecycle_gaps.py`, `test_resume_remote_placement.py`. Parent owns `test_component_runtime.py` and compiler/hub changes.
- **H:** `platform/api/tests/test_ligandmpnn_interface_context.py`, `test_binder_blind_pose_selected.py`; `tests/test_binder_blind_pose.py`; `platform/frontend/tests/vitest/binderDiagnosticSelection.test.tsx`. Distinguish input/transport/result tests from native execution or scientific interpretation evidence; separate operations remain separately selectable.
- **Parent:** `platform/api/tests/test_binder_harmonization_compiler.py`, `test_selected_binder_compiler_profiles.py`, `test_binder_iteration_api_parity.py`, `test_component_runtime.py`, `test_antibody_remote_closeout.py`, `test_workflow_capability_boundaries.py`, `test_project_workflow_setups.py`, `test_project_workflow_setup_routes.py`, `test_project_manager_adapters.py`, `test_project_manager_resource_dispatch.py`; `platform/frontend/tests/vitest/projectWorkflowSetup.test.tsx`, `projectNativeOwnersMounted.test.tsx`, `proteinProjectReopen.test.tsx`. Cover standalone and Project setup save/reopen, exact source import/destination, fan-out contexts, prepared remote actions and native zero-Design results.

Run API tests from `platform/api`, for example:

```sh
uv run --frozen --group dev python -m pytest tests/test_binder_continuation.py tests/test_binder_diagnostic_selection.py -q -rs
```

Run root/script suites from the repository root using the established locked interpreter and their own test environment. Do not put root script tests under API `conftest.py`; unrelated guards can manufacture failures. Use the current pinned Nextflow JAR for `inspect`/compile and bounded non-science transport fixtures. Compilation alone does not exercise staged files/channel execution, and fixture execution does not prove sampling.

Frontend commands from `platform/frontend`:

```sh
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/binderAuthoringMounted.test.tsx tests/vitest/binderAuthoringShell.test.tsx tests/vitest/binderSelectedControlsMounted.test.tsx
pnpm exec tsx --test tests/antibodyTargetParseLifecycle.test.ts tests/antibodyDenovoBoltzgenScaffold.test.ts
pnpm exec tsc -b
```

The mounted config uses an explicit include list; verify every intended suite is collected. Current package `test` combines Node/tsx tests and the mounted config; do not assume every test uses Vitest. Mock network/WebGL internals where needed, not the source controls/state boundaries being tested. Exercise the served controls after async loading and capture every applicable section/source role in both themes and narrow/desktop layouts. Existing mockups are directional references; the seeded-only PPIFlow and incomplete source/Project depictions are superseded.

## 6. Required integrated acceptance scenarios

Keep each scenario tied to original A/C/P/X identifiers and U/W requirements in the controlling ledger. These are engineering evidence requirements, not runtime prerequisites or new product gates.

1. Each of the four starting generators: applicable source mechanisms, independent target/template/full view, typed complete native request, saved/Project round-trip, native output and compatible selection. Wider BoltzGen and PPIFlow cannot remain VHH/seeded-only wrappers.
2. PPIFlow initial generation: starts from target and applicable native template, not a previously generated candidate; both general binder and antibody/nanobody native families have explicit mapping/evidence. Separately, our selected refinement path preserves requested operation semantics, source roles, samples and descendant output. No shared completion checkbox.
3. Full BC2 denominator: generated/differential coverage for every relevant field/action/input family plus representative materially distinct native execution families. Native zero yield is valid accounting but not handoff proof. A real provenance-bound BC2 candidate traverses exact state selection and compatible continuation, then another selected descendant round.
4. Independent optional operations: off-stage absence in the graph/closure, coordinate invariance for read-only/off modes, native sample/metric pairing, complete FrustraMPNN/Caliby/MD/model-native results and honest diagnostic inputs. No hidden redesign, automatic rerun-until-pass or new classifier.
5. Identity/failure: native CIF/checked derivative, states/duplicate basenames/multisample, valid zero yield, missing output, optional failure, replay/manual return and parent preservation. Results remain inspectable without generic Designs or a live worker when supported by their native contract.
6. Project: standalone preserved; Project-originated native setup saves/reopens; source imported from another Project retains provenance and explicit destination; each actual fan-out Job gets the proper context; prepared remote/lifecycle and returned native zero-Design results reopen from Project.
7. Existing bridge: exact selected inputs/settings/resources/output/resume contract survives local and remote placement for generation and selected operations. Transport/asset receipt is not native acceptance. FrustraMPNN uses its actual owner-correct placement path, not an omitted exception.
8. Efficiency/hardening: W01–W08 dispositions, fixes and owner tests; matched before/after phase measurements and resource evidence. No speed claim from fewer samples or changed science. No redundant warm installs/transfers/off-stage prep/heavy discovery. Native initialization and genuinely new compilation remain separately reported.
9. Approved lifecycle: stop, writer quiescence, interrupted staging/return, replay, controller/worker loss and true native resume/reopen at changed boundaries. Do not manufacture native continuation when state is unavailable.

Use bounded approved benign inputs/resources. Disclose exact skipped/unrun cases and remaining native/scientific uncertainty. Neither source reading nor synthetic parser output closes native evidence. Scientific changes, supported-method retirement, disruptive tests, paid resources and deployment retain their explicit authorization boundaries; unrelated implementation continues.

## 7. Requirement ownership and closeout

The original identifiers remain unchanged and are reconciled in the controlling specification:

- P1.1 A; P1.2 B; P1.3 C; P1.4 D; P1.5 G; P1.6 parent.
- P2.1/P2.2 E; P2.3 F; P2.4 parent with A/C/G.
- P3.1/P3.2 H; P3.3 C with H; P3.4 A/G. H owns both distinct raw diagnostic adapters; B supplies only embedded predictor interfaces where relevant.
- X.1–X.6 parent, with the named execution/publication/performance owners.
- U01–U04 A; U05/U06 A+B; U07 A+E; U08 A+C; U09 C+F; U10 parent with A/C/D/E/F/H.
- W01–W08 apply to every changed owner; parent reconciles the complete reachable path, G leads runtime measurements and each code/data/UI owner measures its own affected phase.

One accountable lead per acceptance item: A01 parent; A02 E; A03 parent; A04 F; A05 C; A06 C; A07 B; A08 B; A09 D (H supplies diagnostics); A10 C; A11 parent; A12 G; A13 G (code/UI/data owners supply their measurements); A14 parent. Correction leads: C01 parent; C02 B; C03 B; C04 B (A supplies hydration, C publication); C05 B; C06 B; C07 B; C08 C; C09 C; C10 C; C11 C; C12 parent; C13 G; C14 parent. Named collaborators in the controlling ledger are dependencies, not competing closure owners.

One accountable owner does not allow simultaneous writes to shared files. Requirement completion is reported against the combined tree, not an average of lane percentages. Final delivery includes implemented changes and removed active duplication, exact test/skipped/native/remote evidence, performance measurements, separately read-back deployment/worker identities if authorized, and remaining scientific limits. Do not add generated artifacts/logs/databases to source. Do not call the product finished until its retained scope is actually verified or Christian explicitly changes it.
