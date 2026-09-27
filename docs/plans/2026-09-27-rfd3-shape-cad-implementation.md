# RFD3 Shape / CAD implementation packet

Final handoff · 27 September 2026 · documentation only

Scope authority: [final specification](../specs/2026-09-27-rfd3-shape-cad-completion.md). Visual authority: [approved workspace](../design/rfd3-shape-cad/README.md). Path ownership is also available as [JSON](2026-09-27-rfd3-shape-cad-ownership.json); it is implementation coordination data, not a runtime registry.

## Start here

Christian approved the design direction and requested document finalization. Implement the six existing CAD work items and all twelve acceptance items, not another discovery-only audit and not a fresh product redesign. Product implementation, deployment and scientific execution have not been performed by this documentation task.

Begin from current `origin/test`. The source review is `58c13e575979452395113a7547a3ce8822867a56`; finalization is rebased onto `f9bd7c729aa6c225e1c4ef54aabf08da14bc9d24`, whose changes are confined to shared viewer caption repair and release binding. Compare only affected owners if the tip has advanced again. Preserve the already delivered shared Mol* theme, docked-workbench and caption-layout repairs. Use isolated worktrees, exact scoped diffs and one shared-file integrator; never edit the managed Development checkout or copy an old whole file over a newer owner.

No new settings defaults, guidance science, admission rules, proof requirements, diagnostic cutoffs, automatic mesh transforms, binder assumptions, or native-model upgrades are part of this assignment. Ask Christian for the exact change if one becomes necessary. Existing source, model-input and runtime checks remain intact. Development evidence is not a new launch gate.

## Delivery increments

### Increment 1: connect the contract, without inventing a platform

I extends the current Shape request/discovery/materialization owners using specification section 4. Freeze the native-derived schema projection, actual submitted types, v3 requested/effective representation, v2 compatibility reader and publication interface in one bounded change. Native field applicability is inventoried against the pinned consumed entrypoints as part of this implementation increment. Do not restart an unrelated upstream-model survey.

The inventory uses one row per applicable field with native source/version, type/default/units/authority, UI group/control, wire key, compiler/consumer and readback. Record a reason for excluded training/internal/inapplicable fields. Preserve complete nested schemas, file-picker semantics and explicit false/zero/null/empty values. Snapshot this development evidence outside application source; model owners remain authoritative.

I also registers the two new mounted suites in the real Vitest include list. C can build source/workspace composition and recovery immediately against the fixed public envelopes; B can work on existing native invocation seams, and D can compose native-stage cohort results. They do not wait for a separate design approval cycle.

### Increment 2: parallel complete repairs

- B connects the actual Caliby stage, every newly exposed native parameter, exact selected assets, and per-sample publication through the existing Shape graph. I handles shared native owners and compiler/descriptor edits requested by B.
- C replaces the cluttered composition with the approved freely navigable sections, shared components, three designer variants, typed expert groups and retained drafts. Real geometry tools stay mounted; do not replace them with the illustrative SVG.
- D reconnects the existing native result/analytics/table/workbench owners. Preserve scope and selection across drilldown, exact documents and model/stage labels; do not build another numerical score store.
- I completes Project body-context, canonical committed submission, Project-reserved Job identity and crash/retry reconciliation, alongside native publication/readback and returned-input portability.

### Increment 3: integrate and exercise the receiving owners

I reviews each scoped diff against the six CAD items, imports without dropping concurrent changes, and runs the actual intersecting owners. Exercise the real parent form, actual scratch-store Job insertion, Nextflow/native command transport and production result finalizer with clearly labeled inert science fixtures. Keep those results separate from real GPU execution.

Use existing preparation and remote review for both standalone and Project launches. Do not weaken header/body matching, Project reservation, immutable preparation or scientific equality to make a fixture pass. A crash after canonical Job commit but before Shape linkage must reconcile the existing attempt, not create another Job. Explicit Local remains independent of source ancestry and old worker placement.

Native execution and managed deployment are the later authorized delivery lane, not silently waived completion criteria. No rental/start is authorized by this packet. If no worker is authorized, continue all independent software work and report native execution as unperformed, not as missing implementation and not as a runtime lock.

## Frozen handoffs

### I → C: one form contract

Use existing `ShapeLaunchRequest` and the Shape settings discovery projection. Public envelopes are `rfd3_settings`, `sequence_settings`, `sequence_input_settings`, `validator_settings`, and optional `launch_context_id`; exact meanings and draft names are fixed in specification 4.1. The existing designer discovery route remains compatible. Keep native schemas and authority metadata available to both UI and agents. No shape-specific copied default registry, raw-JSON-only form or generic model-settings framework.

The active scientific request contains only selected/applicable settings. Authoring retains inactive model drafts separately. A settings refresh fills untouched fields only; a user-cleared input is not an absent value. Profile-fixed controls are visible read-only values with reasons, not silently editable overrides.

### I ↔ B: retained request, selected graph and native outputs

B consumes the v3 immutable request prepared by I and continues reading v2. Keep `sequences_per_backbone` as the one count source and native batch size separate. The ordinary Caliby adapter creates one source-bound ensemble per generated backbone and uses `ensemble_design`; no binder or sidechain-only replacement.

Extend the existing sequence-record/validator-evidence owners with explicit version handling where new identity fields are needed. Publish source candidate, designed sequence, predictor and native sample identities with exact native documents/metrics. Caliby CIFs remain CIFs; compatibility PDB derivatives remain separately identified. Neither emitted file order nor matching sequence is a scientific join.

The existing baseline post-refold path and native no-yield disposition stay unchanged. Optional predictor samples are not promoted into another producer's acceptance semantics. Selected dependency closure and remote output return must match the actual graph, including all newly consumed native assets and output directories.

### I ↔ D: one native readback, not a second results database

D receives existing persisted candidate/sequence/prediction identities, the explicit native sample/document selector, model/stage-qualified metrics and missingness. I extends the current Shape result contract/readback only where those facts are actually absent. D must request a missing producer field through I rather than infer it from filenames or manufacture a generic score.

Cohort summaries/charts/table/export use the same explicit scope. Native initial geometry, sequence output and post-refold evidence remain distinct. Metric units and predictor identity are carried into display and comparison. Missing optional observations do not remove independently available structure inspection or create a new selection refusal.

Selected-document continuation uses the existing receiving editor and source-materialization owner. It retains the exact artifact, model/author identity and source ancestry, but the new Job's Project destination and execution target are explicit independent intent. Test the actual receiving parent, not just a URL builder.

## Sole-writer assignments

The exact path map below is authoritative for this handoff. New paths are intentionally marked as not yet implemented. Read any other source as needed, but return an unexpected write dependency to I. Transferring a file requires the old writer to stop before the new writer starts. Changes to model YAML, runtime manifests or shared publication code must be limited to demonstrated Shape wiring; being listed is not permission for unrelated refactoring.

### I · Integrator: shared contract, Project and publication boundaries

Existing sole-writer paths:

- `platform/api/config/models/rfdiffusion.yaml`
- `nextflow_schema.json`
- `nextflow.config`
- `platform/api/tests/test_shape_geometry.py`
- `platform/api/config/models/esmfold2.yaml`
- `platform/api/config/models/boltz2.yaml`
- `platform/api/config/models/protenix.yaml`
- `platform/api/services/shape_requests.py`
- `platform/api/routers/shape_blueprint.py`
- `platform/api/routers/jobs.py`
- `platform/api/services/nextflow.py`
- `platform/api/native_components.py`
- `platform/api/services/result_contracts.py`
- `platform/api/services/result_ingester.py`
- `platform/api/services/result_state_integrity.py`
- `platform/api/services/global_experiments/launch_contexts.py`
- `platform/api/services/workflow_adapter_registry.py`
- `platform/api/services/caliby_native.py`
- `platform/api/services/remote_execution/contracts.py`
- `platform/api/config/models/proteinmpnn.yaml`
- `platform/api/config/models/fampnn.yaml`
- `platform/api/config/models/caliby_experimental.yaml`
- `modules/esmfold2_experimental.nf`
- `modules/caliby_native.nf`
- `scripts/run_caliby_experimental.py`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/vitest.md.config.ts`
- `platform/api/tests/test_shape_submission.py`
- `platform/api/tests/test_shape_remote_preparation.py`
- `platform/api/tests/test_shape_result_ingestion.py`

Planned new paths, only where needed:

- `platform/api/tests/test_shape_project_launch.py`

Acceptance coverage: A02, A03, A04, A05, A06, A07, A09, A10, A11, A12.

### B · Execution: Shape graph and native producers

Existing sole-writer paths:

- `workflows/shape_blueprint_design.nf`
- `modules/shape_blueprint.nf`
- `scripts/shape_blueprint/run_shape_rfd3.py`
- `scripts/shape_blueprint/run_shape_sequence.py`
- `scripts/shape_blueprint/run_shape_validator_suite.py`
- `scripts/shape_blueprint/validate_bundle.py`
- `scripts/shape_blueprint/plan_rfd3_batches.py`
- `scripts/shape_blueprint/prepare_shape_backbone.py`
- `scripts/shape_blueprint/build_rfd3_aggregate.py`
- `scripts/shape_blueprint/build_shape_result.py`
- `scripts/shape_blueprint/build_shape_skip_bundle.py`
- `scripts/shape_blueprint/attach_shape_post_refold.py`
- `platform/api/tests/test_shape_scientific.py`
- `platform/api/tests/test_shape_result_builder.py`

Planned new paths, only where needed:

- `scripts/shape_blueprint/tests/test_settings_transport.py`

Acceptance coverage: A02, A03, A04, A07, A12.

### C · Authoring: approved workspace and retained draft

Existing sole-writer paths:

- `platform/frontend/src/components/ShapeBlueprintTemplate.tsx`
- `platform/frontend/src/components/CanonicalMeshPreview.tsx`
- `platform/frontend/src/components/ProteinDesignWorkflow.tsx`
- `platform/frontend/src/components/ProteinModificationTemplate.tsx`
- `platform/frontend/src/components/JobSubmission.tsx`
- `platform/frontend/src/components/SequenceDesignerSettings.tsx`
- `platform/frontend/tests/shapeBlueprintLauncherBehavior.test.ts`

Planned new paths, only where needed:

- `platform/frontend/src/components/ShapeNativeSettings.tsx`
- `platform/frontend/tests/vitest/shapeAuthoringCompletionMounted.test.tsx`

Acceptance coverage: A01, A02, A03, A04, A05, A10, A11.

### D · Results: cohort workspace and exact-document continuation

Existing sole-writer paths:

- `platform/frontend/src/components/ResultsViewer.tsx`
- `platform/frontend/src/components/StructureViewerPane.tsx`
- `platform/frontend/src/components/AnalyticsDashboard.tsx`
- `platform/frontend/tests/shapeMetricsLayout.test.ts`

Planned new paths, only where needed:

- `platform/frontend/src/lib/shapeResultsView.ts`
- `platform/frontend/tests/vitest/shapeResultsCompletionMounted.test.tsx`

Acceptance coverage: A07, A08, A09, A11.

## Focused execution recipe

These are implementation commands, not tests claimed to have run during document finalization. Use the repository's locked environments. Keep root script tests separate from the API conftest. Extend the owning suites with real boundary assertions; no acceptance by field-count-only tests.

From `platform/api`:

```sh
uv run --frozen --group dev python -m pytest -q \
  tests/test_shape_geometry.py tests/test_shape_scientific.py \
  tests/test_shape_submission.py tests/test_shape_remote_preparation.py \
  tests/test_shape_result_builder.py tests/test_shape_result_ingestion.py \
  tests/test_shape_project_launch.py
```

The last file is the new I-owned Project regression suite. Include actual bound insertion, canonical committed identity, remote approval/cancel, crash reconciliation and standalone regression rather than testing only a header or preparation receipt.

From the repository root, using the same locked API interpreter with root collection:

```sh
uv run --project platform/api --frozen --group dev python -m pytest -q \
  scripts/shape_blueprint/tests
```

Add real pinned Nextflow graph/shell execution with only the science executables inert to the existing owner tests. A successful `nextflow inspect` is compilation evidence, not native sampling or final publication.

From `platform/frontend`:

```sh
pnpm exec tsx --test tests/shapeBlueprintLauncherBehavior.test.ts tests/shapeMetricsLayout.test.ts
pnpm exec vitest run --config vitest.md.config.ts \
  tests/vitest/shapeAuthoringCompletionMounted.test.tsx \
  tests/vitest/shapeResultsCompletionMounted.test.tsx \
  tests/vitest/generalSequenceDesignMounted.test.tsx \
  tests/vitest/molstarViewerLoadStateMounted.test.tsx
pnpm exec tsc -b
```

The two Shape mounted files are new and must first be included by I in `vitest.md.config.ts`. Verify collected file identities and nonzero case counts. Run additional existing native designer, Project or result suites only for changed shared consumers; do not substitute a large unrelated passing count for the missing receiving-path assertion.

After software integration, inspect ordinary entry, save/reopen/clone, designer switches, error recovery, remote review and an actual completed result in the served build. Test desktop/narrow supported themes with expanded groups and retained viewer identity. Mockup browser checks are only design-artifact checks, not those production checks.

## Completion crosswalk and report

Every acceptance below remains open implementation evidence at document finalization. The report must attach exact commands/results or run/artifact handles and say whether evidence is fixture, native execution or deployed behavior. No acceptance is closed by merely checking off a lane.

- A01: C; geometry acquisition, scale, preview controls and retained camera/source state.
- A02: I + B + C; complete applicable RFD3 discovery, typed control, request and consumed invocation.
- A03: I + B + C; all three designers from selected request through actual stage and returned native publication.
- A04: I + B + C; predictor settings and all supported emitted samples, no one-file collapse.
- A05: I + C; parent-level switch/save/reopen/clone/retry and explicit-value preservation.
- A06: I; real standalone/Project insertion, review/approval/cancel, reserved ID and source-offline replay.
- A07: I + B + D; real builder/ingester/finalizer/readback, nonzero/zero yield and historical/partial evidence.
- A08: D; full cohort scope, analytics, bounded table, cross-page selection/export and return state.
- A09: I + D; exact selected-document handoff, deliberate destination and format preservation.
- A10: I + C; cold/stale read failure and mounted recovery without request mutation or new locks.
- A11: I + C + D; served hierarchy, theme/contrast/narrow bounds, applicable tools and navigation.
- A12: I + B; separately authorized native/local/remote execution and returned results at the delivered revision.

Do not say “complete except testing” if an adapter, setting mapping, Project insertion or result action is absent. Missing worker authorization is an evidence limit, not a reason to stop independent implementation or add a product gate.

## Copy-ready lane brief

For each future implementation agent, provide the current verified base/worktree, this packet, the final specification and approved prototype. State its lane letter and the exact paths from the JSON. Require:

1. Verify actual Git root, HEAD and dirty state before editing.
2. Implement the assigned complete repair and its receiving-boundary tests; do not return another broad audit or a schema-only stub.
3. Preserve existing scientific settings/behavior, native identity and all reusable UI mechanisms. Do not implement another workflow or new gate to satisfy development evidence.
4. Return any unexpected shared-file dependency to I; do not broaden ownership silently.
5. Return touched paths, scoped diff, commands and results, explicit fixture/native limits, and remaining concrete dependencies. No staging all files, reset/rebase of shared work, deployment, worker starts or live science by a leaf lane.

The parent I remains responsible for integrated correctness, remote/source readback, final publication/reopen and the complete A01–A12 denominator.
