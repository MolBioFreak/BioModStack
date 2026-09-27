# RFD3 Shape / CAD completion specification

Final implementation scope · 27 September 2026

**Design direction approved by Christian:** “this is much better. finalize docs to get this done”. This finalization adopts the workspace hierarchy and existing-component reuse shown in the mockups. It does not approve changed scientific defaults, new runtime gates, or omissions hidden by representative mockup fields.

**Implementation status:** Not implemented by this documentation task. The scope and handoff are ready; native field-to-consumer inventory and code verification remain assigned implementation deliverables, not completed evidence.

**Companion handoff:** [Implementation packet](../plans/2026-09-27-rfd3-shape-cad-implementation.md).

**Visual reference:** [Approved design source and screen index](../design/rfd3-shape-cad/README.md).

Baseline: `61a06cdc19a311308f74e04f39c57fd93c3fe175` in the managed Development checkout. The current application was inspected read-only. This package does not implement or deploy the proposed changes, submit a Job, or provision a worker.

Packaging recheck: Development advanced independently to `58c13e575979452395113a7547a3ce8822867a56`. The targeted diff leaves the reviewed Shape request, workflow and authoring owners unchanged. It adds shared viewer theme integration and a docked, non-overlay workbench. Reuse that delivered repair; it is not new CAD work in this spec. This task did not make those application changes.

Finalization base: `f9bd7c729aa6c225e1c4ef54aabf08da14bc9d24`. Its intervening changes repair the shared viewer's caption layout and its release binding; they do not change the reviewed Shape owners. Preserve that repair too. This documentation commit is rebased onto that tip, with no application-source diff.

## 1. Outcome and boundary

Finish the existing Shape mode of De Novo Protein Design so an operator can import or reopen a CAD mesh, understand its physical scale, configure RFD3, choose ProteinMPNN, FA-MPNN or Caliby for sequence design, inspect prediction settings, launch through the existing local/remote path, and review and continue from the exact resulting documents.

This is a completion and UI-composition project, not a new CAD application, generic form framework, model upgrade or binder workflow. Keep the existing route, geometry materialization, model owners, scheduler, Project infrastructure and result workbench. General protein design must not acquire binder targets, CDR rules, inferred A/B roles or a universal binder score.

The approved UI direction uses the existing BMS theme and section/panel/run vocabulary. It replaces the crowded “Optional next steps” surface with separate Sequence design and Prediction sections. Sections are freely accessible views of one retained draft, not a gated wizard.

### Non-negotiable behavior preservation

- Preserve the current Auto, Skip and explicit-engine sequence-policy meanings, including zero-sequence behavior. Do not silently convert Shape into the initial-generation follow-on round workflow.
- Keep the current guidance profiles and their scientific values. Profile-fixed values are visible and read-only; operator-owned values become genuinely editable. Do not invent a custom guidance profile.
- Preserve current fresh-form, API-omission and historical-request defaults separately until an explicit change is approved. The current UI starts with one backbone and one sequence; the submitted API has different omission defaults. Do not silently harmonize them.
- Preserve Shape’s contextual FA-MPNN defaults rather than substituting standalone defaults: sequence-only on, final repacking off, cysteine exclusion off, and an initial batch derived from sequence count unless explicitly edited.
- Retain the existing ESMFold2 baseline and current predictor selection behavior. Exposing Protenix sample/MSA controls does not authorize changing their defaults.
- Do not add new admission criteria, score cutoffs, proof receipts, required diagnostic runs, section-completion checks or restart requalification. Existing model-input, ownership, integrity and controller/runtime checks remain with their existing owners. Missing observations are reported, not converted into new refusals.
- A native prediction is computational evidence, not experimental validation. A good shape fit does not establish folding, stability, binding or function.

## 2. Current-state crosswalk

### CAD-01 · Geometry workspace — present, presentation/recovery work

`ShapeBlueprintTemplate.tsx` already imports OBJ and ASCII/binary STL, offers source units, saved geometry, canonical surface and point-pool preview, dimensions and geometry detail. `CanonicalMeshPreview` already has rotation/reset/download controls. Preserve these mechanisms.

The selector currently leads with opaque IDs; scale and useful controls compete with implementation prose. Loading, failed reads, successful emptiness and missing saved selection need separate presentations. A transient read failure must not say that the library is empty. Earlier live requests returned 500 before recovering; the current source also needs mounted-query recovery qualification. The transient cause is not established by this spec.

### CAD-02 · RFD3 settings — connected sampler, incomplete operator surface

The form exposes length policy, candidate count, seed and two guidance profiles. The executable graph passes `shape_rfd3_num_timesteps` outside the form. Profile metadata already fixes guidance weight, scale, schedule and interior target count. Inventory the actual pinned Shape sampler, not unrelated training configuration or every generic RFD3 capability. Expose every applicable inference setting through its model owner and transport it to the consumed invocation.

### CAD-03 · Sequence design — two limited embedded engines; Caliby absent

The current Shape discovery provides five ProteinMPNN fields and seven FA-MPNN fields. `SubmittedShapeRequest`, `ShapeSequenceEngine`, the workflow branch and the embedded runner accept only ProteinMPNN/FA-MPNN. FA-MPNN checkpoint selection is hardcoded in the Shape runner. Caliby’s standalone typed `ensemble_design` contract exists, but that does not connect it to Shape.

Replace the narrow settings projection with an applicability projection of the existing native contracts. Complete the actual Shape stage for all three designers. A new selector alone does not satisfy this item.

### CAD-04 · Prediction — executable peers, hidden settings

The current graph runs ESMFold2 and optional Boltz2/Protenix V2 peers. Predictor selection is already visible and submitted; older prose calling the entire selection hidden is stale. The Protenix wrapper fixes one sample, MSA off and templates off. Boltz fixes one diffusion sample and empty MSA, with partly wrapper-owned sampling/recycling settings. ESMFold2's Shape branch also has pipeline-only controls and assumes one sample. The request lacks complete per-predictor configuration.

Expose and consume typed model-owned settings without changing defaults or adding a predictor. Supporting more than one sample also requires a real publication change: the current joins assume one ESMFold structure per sequence, and the peer wrapper chooses a latest confidence file. Replace that one-sample assumption with explicit sequence → predictor → native sample identity at the existing native publisher. Retain every emitted supported sample and bind its metrics/document together. Never choose the last file or manufacture a best-sample aggregate. Historical one-sample records keep their existing meaning.

### CAD-05 · Drafts, placement and lifecycle — existing remote path; confirmed Project binding gap

The current editor retains a Shape draft and uses the existing submission helper, execution-target picker and remote approval transport. Extend those paths rather than creating a second launcher.

Project launch is concrete missing integration, not just unperformed testing. Shape's submitted request and `shape_job_request` omit `launch_context_id`; an ambient browser header alone does not select the canonical Jobs Project branch. Simply adding the field still fails: Shape calls canonical Jobs with `_commit=False`, which the Project branch rejects. Project also owns a reserved scheduler Job ID, while Shape preallocates a different deterministic ID.

Repair the existing Shape → canonical Jobs → Project transaction and identity handoff. Preserve standalone idempotency. Carry the explicit context in the typed request, with the existing header/body match. For Project-originated launches use the canonical committed Project submission and its reserved identity; link `ShapeDesignRequest.job_id` to the actual returned identity rather than the standalone UUID. Reconcile a committed Job after a crash before the Shape link is saved, through the existing attempt/request association, without creating a duplicate. Retain current standalone transaction behavior. Existing model registries already admit the Shape model; do not create another Project adapter framework.

Saved configurations, clone, retry, Project setup preparation, approved remote launch and returned-result reuse require actual owner-level verification for every new envelope. Preparation success or the browser's return-to-Project navigation does not prove bound Job insertion.

### CAD-06 · Results — existing native/Design material and viewer; composition and bindings need work

`StructureViewerPane.tsx` already shows shape total, outside penalty, inside fraction, source RMSD and pLDDT; it supports canonical-point and source-backbone comparisons. Its Shape branch disables several general workbench panels and omits some governed workbench context. Investigate and repair exact document/metric bindings before reconnecting applicable shared tools. Do not simply remove guards or substitute a new viewer.

The desired full Results landing is a cohort overview with native-stage measurements and a usable table, then exact candidate inspection. A geometry-focused strip buried inside a viewer is not the complete result experience. Preserve initial/backbone, sequence and post-refold identities and status meanings.

## 3. Screen contract

### Geometry

A source panel and a large persistent geometry canvas. Upload and saved-library entry are explicit alternatives. Show a readable source label, format, source-unit conversion and X/Y/Z dimensions. Historical sources without a retained name use an honest format/short-ID fallback; do not fabricate filenames.

Uploaded mesh units are editable before import. An admitted geometry’s scale and identity are immutable; changing scale creates a new explicitly imported geometry, never silently mutates the old record. Keep the current supported unit choices and no automatic “fit protein” resizing. Keep scale observations informational under existing rules.

Surface/points, rotation, reset and download stay within their current preview owners. Expanded inspection is a small approved presentation addition for the mesh preview, not a claim that its current canvas already has a full CAD/Mol* toolbar. Identity and geometry metadata are collapsed details. Empty preview copy must describe an empty preview, not a legacy identity problem. Changing sections or expanding inspection must not discard camera state, re-fetch unchanged geometry or remount the viewer unnecessarily.

### RFD3

Primary controls: guidance profile, length policy and appropriate length fields, backbone count, sampling timestep count and seed. Length-range fields are shown only for the chosen existing policy. Do not parse a temporarily cleared numeric input into a replacement default.

Collapsed groups contain applicable native sampler/checkpoint settings and visible profile-fixed values with their reason. Typed controls and precise native metadata remain accessible; labels are readable rather than repeated prefixed identifiers. No arbitrary slider bounds and no invented speed/quality presets.

### Sequence design

First row: policy. Explicit engine exposes ProteinMPNN, FA-MPNN and Caliby as equal selectable options, not ranked recommendations. Primary values are sequences per backbone and sampling temperature. Each engine keeps its own saved draft.

- ProteinMPNN groups: model/checkpoint, sequence constraints, noise, applicable relaxation, execution/reproducibility.
- FA-MPNN groups: sequence versus side-chain behavior, denoising/checkpoint, constraints, batch/reproducibility. Shape’s contextual initial values remain visible and editable.
- Caliby groups: model/checkpoint, generated-state constraints, Potts sampling, side chains, conformer perturbations and execution/reproducibility.

Skip is compact. It retains editable saved settings behind a collapsed access point without submitting inactive settings. Re-enabling restores them. Auto continues selecting ProteinMPNN only under existing rules.

Each generated backbone is one explicitly identified primary state for Caliby `ensemble_design`; different generated candidates are not silently combined into a multi-state ensemble. Fixed-sequence packing is not advertised as sequence design. Model-native nested constraint editors preserve native author-residue semantics and existing limitations. The source path and producer IDs are generated by the workflow, not entered by the operator.

### Prediction

Each predictor gets a named row with its own retained native settings. The existing required ESMFold2 baseline is clearly identified; optional peers retain their current selections. Show output count and the applicable primary sampling/MSA choices before expert groups.

MSA selection uses the shared supported provider/cache policy, including Neurosnap where required. No independent local search, duplicate credentials, or new provider setup. “MSA off” is not a synonym for a scientific blind-prediction guarantee. Do not claim independent prediction if the effective invocation is conditioned on generated coordinates.

### Run

Use `ProteinDesignRun`, existing transfer policy, execution-target picker, destination context and approval continuation. Show the geometry, selected stages, counts and chosen destination in a short summary. Advanced requested/effective settings are collapsed. Only an existing remote-review requirement invokes review; the redesign adds no required local approval screen.

The Run section is navigation, not a prerequisite. Keep one scientific submission owner and prevent duplicate mutation while pending. Preserve retry identity according to the existing contract. Explicit Local remains explicit and unavailable remote selection never falls back to Local.

### Results and candidate inspection

Land on a scoped cohort summary, measurement availability and meaningful native metrics, followed by a bounded table. Keep initial geometry and post-refold stages distinguishable. Metric choice, filtering, sort, selected IDs and export scope must agree across pages; do not silently chart only the displayed page. Partial-page fetch failure retains usable rows and labels partial statistics.

Reuse the current chart/table infrastructure. Initial geometry permits shape total, outside penalty and inside fraction; prediction confidence uses the exact predictor’s native semantics and scale. Source RMSD requires its actual published comparison. Missing measurements remain null/unavailable, never zero. No manufactured combined quality score or binding metric. Existing “accepted” labels must state which native stage made that decision, not imply experimental validation.

Candidate drilldown opens the exact selected producer document in the shared Mol* workbench, with source/geometry overlays, native metric panels, sequence/residue tools and applicable measurement/capture/export. Restore tools only where the existing document/metric contract supports them; report unavailable observations without blocking independent inspection.

Continuation into Redesign structure must carry the exact selected document and ancestry, preserve native CIF/PDB identity, and use checked conversion only at a genuinely PDB-only receiving boundary. Source ancestry does not impose a scheduler parent or destination worker. Returning to the cohort preserves selection and analytical context.

### Recovery, accessibility and narrow layout

Separate initial loading, successful empty library, failed read with cached data, failed cold read and a genuinely missing saved record. Retry the failed read only. Preserve source choice, per-model values, placement and request identity. Keep existing launch validation; do not create an additional “all queries green” requirement.

Use the actual theme tokens, 4.5:1 minimum body/help contrast, visible keyboard focus, field labels, semantic units and touch-sized primary controls. At narrow widths stack the panels, bring a selected geometry preview ahead of its secondary source details, keep navigation accessible and constrain table scrolling to the table. Expanded expert controls must not overflow the page. Avoid hardcoded dark-only text and translucent warning text that fails on a light theme.

## 4. Request and execution contract

Keep the existing Shape endpoint and `SubmittedShapeRequest` as the orchestration owner. The following interface additions are fixed for implementation at that owner, not as a parallel public workflow:

1. Preserve `sequence_policy`, `sequence_engine`, `sequences_per_backbone`, geometry identity, length policy and seed. Extend the existing engine enum with exact model ID `caliby_experimental`.
2. Add a typed `rfd3_settings` projection of applicable installed sampler settings. Exclude scheduler paths, private tensor state and profile-fixed internals from editable fields; retain fixed values in effective readback.
3. Keep `sequence_settings` as the selected designer’s model-owned parameter object, extending beyond the current scalar-only transport where the native schema requires lists/objects/nulls. Add a separate typed `sequence_input_settings` projection for generated-source-independent Caliby conformer constraints. Generated source identity never comes from these operator fields.
4. Add `validator_settings`, keyed by the exact selected predictor IDs, with each value validated and compiled by its existing native owner. UI drafts may retain settings for unselected predictors, but inactive settings do not leak into scientific execution.
5. Maintain omitted/explicit false/zero/null/empty distinctions and preserve the existing native aliases during historical normalization. Do not route malformed new native fields through a permissive generic dictionary.
6. Store per-engine draft maps and UI section/source-view state in existing authoring persistence, outside the active scientific request. Hydrate before child change callbacks. Async inventory refresh may fill only untouched fields.
7. Materialize the same immutable scientific request used by local launch and remote review. Extend the existing selected asset/input inventory for only the chosen engines and predictors. Preserve prepared bytes through approval and relocate existing path slots rather than re-acquiring old controller paths.
8. Reuse the current Nextflow graph and existing native executable owners. For Caliby reuse `services/caliby_native.py`, `modules/caliby_native.nf` and `scripts/run_caliby_experimental.py`, not `caliby_binder` or antibody constraint preparation. Add the Shape adapter at the sequence-stage seam, materialize one source-bound ensemble, and adapt its producer output to the current Shape sequence-record/publication seam. Do not introduce another round coordinator or second results database.
9. Preserve explicit backbone → sequence → prediction identity through native records and artifact publication. Keep Caliby native CIF documents distinct from compatibility derivatives. Do not derive joins from basename, ordinal, matching sequence or guessed chain labels.
10. The current staged request schema is `bms_shape_design_request_v2`. New expanded requests use `bms_shape_design_request_v3`; v2 readers and replay remain supported with their original defaults and numerical meanings. Update the existing owning serializers/readers together. Do not rewrite sealed old evidence or create a requalification requirement.

### 4.1 Fixed request, discovery and persistence envelopes

Continue using `POST /api/shape-blueprint/requests`; do not add a parallel Job endpoint. Retain all existing required geometry and retry-identity fields. Extend its closed submitted model with:

- `rfd3_settings`: model-owned typed object; default omitted/empty means the current Shape sampler defaults. `num_timesteps` is the native runner's existing consumed name and maps to the already present `shape_rfd3_num_timesteps` compiler parameter. Remaining applicable keys are projected from the pinned consumer, not invented here.
- `sequence_settings`: active selected-model object. ProteinMPNN and FA-MPNN retain current native keys and Shape contextual initialization. Caliby uses the ordinary `ensemble_design` settings; generated `ensembles`, state IDs and paths are not caller-authored fields.
- `sequence_input_settings`: typed Caliby conformer constraint projection with `fixed_pos_seq`, `fixed_pos_scn`, `fixed_pos_override_seq`, `pos_restrict_aatype`, `symmetry_pos`. Other designers use their own native constraint controls in `sequence_settings`; do not force them through Caliby semantics.
- `validator_settings`: selected predictor ID to that predictor's mode-owned typed object, with exactly `esmfold2`, `boltz2`, `protenix_v2` as the existing roster IDs. An omitted entry preserves the current effective invocation. Unselected drafts stay outside the request. These fields do not turn baseline selection into an interchangeable-primary policy.
- `launch_context_id`: optional existing opaque Project context identity; standalone remains absent/null. The existing header/body equality applies. Scientific materialization does not absorb placement or Project reservation metadata into a second scientific identity.

Retain `sequences_per_backbone` as the single count authority. Compile it to native ProteinMPNN `num_seq_per_target`, FA-MPNN `num_seqs_per_pdb`, and Caliby `num_seqs_per_pdb`. A native batch size remains a separate setting. Never expose two independently editable copies of the count or silently replace a saved batch value.

Use the existing Shape settings discovery route as a projection of model-owned metadata. Add a read-only `GET /api/shape-blueprint/settings` orchestration discovery endpoint only to bundle the applicable RFD3 and predictor projections and the existing designer endpoints under one form owner. It returns native schemas/defaults/applicability/authority metadata, not a second numerical definition or readiness policy. Keep the old designer discovery response backward-compatible; extend it with native schema metadata where nested editors require it. Browser and agent discovery serialize the same closed contract.

The new immutable request retains requested/effective `rfd3_settings`, `sequence_settings`, `sequence_input_settings` and `validator_settings` in the existing Shape request object and digest. Use `requested_*` fields for the caller's supported intent and the unprefixed fields for model-owned effective values, preserving absence versus explicit values. Retain current generated source bindings and profile identity. Do not add a parallel receipt store.

Authoring uses the current `shape_*` draft namespace, extending it with `shape_rfd3_settings`, `shape_sequence_input_settings_by_engine`, and `shape_validator_settings_by_engine`; retain `shape_sequence_settings_by_engine`. A saved active request and its inactive drafts remain distinct. I owns these wire names; C owns presentation/hydration. Do not change them independently in a leaf lane.

### 4.2 Publication and sample identity

Extend the current Shape sequence-record and validator-evidence owners, with explicit versioned readers when a stored row gains new identity dimensions. Each new prediction record names its source sequence producer key, predictor ID, native sample key, exact output document and associated metrics. Preserve a producer seed/sample ID when emitted; capture identity at emission when the native writer would otherwise overwrite a filename. No last-file, lexical-order or best-score inference may join records.

The existing baseline post-refold path remains the baseline. If its native producer supports multiple outputs, retain their sample identity rather than collapsing them under one sequence-name join. If a producer genuinely supports only one sample, expose that native constraint rather than simulate sampling by repeated runs. Optional peer samples remain separate native evidence and do not acquire another model's admission or post-refold result. The selected-document readback resolves the exact sample/format and source association through current artifact authority.

Preserve `ShapeNoCandidates`, current initial/post-refold disposition, old one-sample records and existing optional-stage failure behavior. The completeness tests exercise these cases; they do not change runtime classification or block inspection because a newly added observation is missing.

## 5. Implementation ownership and order

The [implementation packet](../plans/2026-09-27-rfd3-shape-cad-implementation.md) is the sole path-level assignment. It removes the review draft's overlapping ownership of the Shape router, shared API types and result ingestion.

- **Integrator I:** shared contracts, request/materialization, canonical Project launch, compiler/dependency closure, API types, publication/readback integration, test registration and final release integration.
- **Execution B:** existing Shape Nextflow graph, model-native invocation adapters and native producer/result-builder transport. Reuse the ordinary Caliby implementation, not a binder adapter.
- **Authoring C:** approved Geometry/RFD3/Sequence design/Prediction/Run composition, actual parent hydration, source preview and saved-configuration lifecycle.
- **Results D:** scoped cohort overview, shared analytics/table/inspector, exact-document continuation and matching browser tests.

I publishes the bounded discovery/request/publication handshake in the first implementation increment. C can implement composition and recovery immediately against the fixed envelopes; B and D can implement independent native/output and presentation work. Numerical applicability comes from pinned native owners, not screenshot samples. No separate generic audit, new workflow coordinator or serial design-review ritual is required.

Integrate from current `origin/test` in an isolated worktree, not the deployment-owned canonical checkout. Preserve the shared viewer theme/dock repair already delivered. Run focused intersecting tests and then the normal managed release process when implementation is authorized. This finalization authorizes no Job submission, rental, runtime change or deployment.

## 6. Acceptance evidence

The following are engineering completion criteria, not new runtime gates.

- **A01 / CAD-01:** Ordinary entry, explicit Shape link, source upload/library selection, saved reopen, unit conversion and surface/point controls work through the real owners. Camera/source state survives section changes. Existing mesh checks are unchanged.
- **A02 / CAD-02:** Every applicable RFD3 control has a discovered schema, typed UI, real request mapping and consumed invocation. Profile-fixed values remain exact. Native omission/default behavior is preserved.
- **A03 / CAD-03:** All three designers pass actual normalization, source preparation, compiled execution mapping and returned publication. Native sampling evidence is recorded separately from inert transport fixtures. Include Caliby’s ordered single-state source and nested constraints.
- **A04 / CAD-04:** Every selected predictor receives its actual requested parameters. Verify the wrapper command/effective native request rather than a mounted checkbox. Exercise more-than-one-sample transport and returned publication with explicit native sample joins, so a latest-file read or one-record join cannot silently discard outputs. Inactive predictor settings remain retained but unsubmitted.
- **A05 / CAD-05:** Edit/switch/save/unmount/reopen/clone/retry preserves values, including false, zero, null, empty and explicitly cleared numeric drafts. Test the real parent and shared submit/interceptor path, not only a leaf component or function spy.
- **A06 / CAD-05:** Scratch-store actual Job insertion covers standalone and Project-originated launch, prepared remote review/approval/cancel, explicit Local and source-offline relocation. A normalized preview or mocked insertion is not this evidence.
- **A07 / CAD-06:** Native records and artifacts reopen through their actual readers/finalizer with correct counts, source identities, formats and stage labels. Exercise nonzero yield, zero yield, historical result and failed/missing peer evidence under the existing result semantics.
- **A08 / CAD-06:** Full Results landing shows cohort overview and table before deep provenance. Filters, plots, cross-page selection/export and exact document drilldown agree. Use a labeled large-cohort fixture without creating live scientific records. Returning from inspection retains state.
- **A09 / CAD-06:** Actual selected-document continuation reaches Redesign structure with retained source identity, deliberate destination and no implicit primary/worker fallback. CIF is not renamed PDB.
- **A10 / cross-cutting:** Cold error, stale-data refetch error and recovery preserve the draft and source; a failed read never becomes an empty response. The screen recovers after the endpoint recovers, without a scientific resubmission or new lock.
- **A11 / UI:** Verify settled served pages at desktop and narrow widths and supported themes, including expanded advanced groups. Measure contrast/overflow and separately review hierarchy and useful interactions. Field counts alone are insufficient.
- **A12 / execution:** After authorized resources exist, run the native source-bound Shape path through each selected designer and predictor configuration, including local/remote return and result reopen. Keep fixture, CPU orchestration, real GPU, deployment and experimental evidence distinct. Do not rent or start a worker without Christian’s approval.

Existing focused owners include API `test_shape_geometry.py`, `test_shape_scientific.py`, `test_shape_submission.py`, `test_shape_result_builder.py`, `test_shape_remote_preparation.py`, `test_shape_result_ingestion.py`, frontend Shape launcher/metrics tests and the applicable native designer suites. Verify test-runner registration; extend the actual owner tests rather than accumulating disconnected field-presence tests.

## 7. Decisions reserved for Christian

The UI direction is approved; no scientific change is implied. Separately ask before changing existing guidance profiles, acceptance/admission rules, predictor roster/defaults, sampling defaults, sequence-policy semantics, or introducing arbitrary multi-state Caliby conditioning. Changing the behavior of existing evidence-related failures also needs an exact proposal; the default here is to preserve behavior, not add a stricter success condition.

Automatic mesh repair/resizing, a new CAD editor, new protein generators/designers, a new round coordinator, model upgrades, new binding analyses and a Project platform rebuild are outside this completion package.

## 8. Mockup package and interpretation

The [prototype](../design/rfd3-shape-cad/prototype.html) is self-contained and non-submitting. It demonstrates section navigation, per-engine settings retention, policy switching, dark/light themes and illustrative results navigation. Action buttons that would acquire data, run models or export scientific artifacts explicitly remain prototype-only.

The accompanying booklet and original PNGs cover geometry, upload/scale, RFD3, each of the three sequence designers, prediction, Run, Results, candidate inspection and read recovery. The example mesh, filenames, settings selections and result values are illustrative. The drawings are not Mol* execution or generated structures. Representative visible fields show hierarchy; the inventory/acceptance requirements above, not the screenshot’s field count, define complete model coverage.

The example draft uses 12 backbones and 3 sequences per backbone to show count hierarchy. Those are not proposed fresh defaults. Mockup results are a separate illustrative cohort, not purported output from that draft.

## Source anchors

- `platform/frontend/src/components/ShapeBlueprintTemplate.tsx`: current authoring, defaults, hydration, source state, launch and preview.
- `platform/frontend/src/components/ProteinDesignWorkflow.tsx`: existing section/panel/run primitives.
- `platform/frontend/src/components/GeneralSequenceDesignSettings.tsx` and `SequenceDesignerSettings.tsx`: existing native-field grouping; reuse presentation, not unrelated orchestration/defaults.
- `platform/frontend/src/components/StructureViewerPane.tsx`, Shape branch around lines 2931–3040: current metrics, overlays and disabled workbench context.
- `platform/api/services/shape_requests.py`: settings projection, submitted request and materialization.
- `workflows/shape_blueprint_design.nf`: current stage graph and two-engine branch.
- `modules/shape_blueprint.nf`: actual process arguments and sampler timestep transport.
- `scripts/shape_blueprint/run_shape_rfd3.py`, `run_shape_sequence.py`, `run_shape_validator_suite.py`: consuming native invocations.
- `config/shape_blueprint/rfd3_profiles.json`: existing profile authority.
- `docs/Model_Configuration_Operator_Control_and_Agent_Parity.md`: model-owned settings and reusable results policy.
- External design-package `evidence/*-discovery.json`: retained read-only native discovery, not proof of native execution. The source prototype is checked in; generated screenshots, PDF exports and evidence stay outside application source.

## Existing SOW reconciliation

This is a bounded Shape completion addendum to the current product policy, not a replacement for the global Protein Project program. No standalone Shape spec was found in the inspected repository. The relevant existing SOW is `docs/specs/2026-08-12-protein-in-silico-global-project-integration-sow.md`. Its broader phases are not reopened by this package.

- Section 3.3 item 19 previously described validator selection as hidden. It now credits the delivered selector. CAD-04 covers the missing native settings and sample-level publication instead.
- Section 6.2, P7 and the section 20 definition of done now credit the visible roster and assign complete native parameters, persistence, invocation and readback under CAD-02–04 and A02–04. Do not redispatch an already delivered selector.
- The sequence-producer matrix's old Shape hide/disable instruction is removed from the antibody restriction. Shape's three-designer lineup and unchanged execution behavior are governed here, not by an unrelated historical antibody matrix.
- Sections 6.1 and 20 require prepared, explicit, idempotent Project launch and exact reopening. CAD-05 / Integrator I / A06 name the concrete missing Shape body context, transaction and reserved-identity connection; UI return navigation is not closure.
- Native result/viewer closure at section 20 maps to CAD-06 / A07–09. Existing Shape publication and overlays are retained; new sample/engine documents and result composition are completion work.
- This addendum does not import new historical classification, proof-receipt, resource or diagnostic restrictions from unrelated old work. Existing deployed behavior remains the baseline; exact behavior changes require a separate decision.

The companion global SOW is reconciled by this documentation change: its stale hidden-validator statement and Shape completion assignments now point to this final scope and implementation packet. Other workflows remain out of scope. The deployment-owned checkout is not edited directly.
