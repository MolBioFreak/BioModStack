# FrustraMPNN completion implementation plan

> **For Hermes:** implement this plan completely in the listed vertical slices. It finishes FrustraMPNN only. Do not implement, repair, expose, or refactor ProteinMPNN, LigandMPNN, FA-MPNN, ThermoMPNN, or a generalized model plugin framework in this tranche.

**Status:** corrected implementation-ready specification for all remaining FrustraMPNN work  
**Feature branch:** `feat/global-model-analysis-config-20260802`  
**Committed source pin before current Slice 0 edits:** `a918daedbea8ff2e6f15e71806c6874f217b912d`  
**Target:** `test` and canonical Development only; `main` and production remain owner-controlled  
**Open PR:** [#52](https://github.com/MolBioFreak/BioModStack/pull/52)

**Goal:** finish FrustraMPNN as a globally configured, scheduler-owned scientific capability that can be requested from appropriate workflows, produces exact auditable frustration maps and substitution landscapes, supports structure interpretation and rational mutagenesis, and is visibly proven through one real Development owner path.

**Architecture:** retain the already implemented canonical FrustraMPNN runtime, manifests, ingestion, child jobs, exact residue mapping, governed APIs, and viewer. Add only the missing global configuration/presentation contract, migrate reachable endpoint controls to it, correct result classification, close remaining workflow/default/state gaps, and qualify one real card→scheduler→GPU→result→Mol* path. Do not build a generic model runner or duplicate FrustraMPNN execution.

**Testing policy:** focused changed-owner contract tests only, followed by one bounded live owner path. No broad repository suites, ten-case campaign, browser matrix, or repeated stochastic qualification.

---

## 1. Product semantics

1. The capability name is **Frustration analysis**.
2. **FrustraMPNN** is the model identity shown only after the capability is enabled.
3. FrustraMPNN is not universally a QC stage. Its principal product uses are:
   - mapping residue-level energetic frustration on predicted or designed structures;
   - inspecting complete amino-acid substitution landscapes;
   - supporting scientifically rational mutagenesis and avoiding harmful substitutions;
   - aiding structure/model review where a workflow explicitly interprets frustration that way.
4. Workflow cards own whether the capability is offered and whether the operator requests it for a job.
5. Global FrustraMPNN configuration owns stable operator wording, model/checkpoint label, scientific summary, workflow-specific defaults/context, safe launch exposure, and resource-profile reference.
6. Numerical scores, missingness, classes, threshold policy, and interpretations remain backend-owned. The frontend displays typed metadata and does not reimplement thresholds.
7. Enabled execution is required/fail closed. Disabled execution is explicit `not_requested`, never “success with no metrics”.
8. Only the BioModStack scheduler assigns physical GPU identity.

## 2. Existing implementation that must be preserved

The following are already implemented and are not to be rewritten unless a focused defect blocks acceptance:

- `modules/frustrampnn.nf` canonical scheduler-owned component;
- `workflows/frustrampnn_analysis.nf` child/artifact-analysis path;
- `scripts/run_frustrampnn_component.py` canonical adapter;
- `platform/api/services/frustrampnn/` contracts, runtime, structure normalization, analysis, manifests, jobs, persistence and APIs;
- immutable component request/result/receipt/result-manifest bundles;
- exact `(auth_asym_id, auth_seq_id, insertion_code)` structure mapping;
- backend-owned FrustraMPNN threshold/classification metadata;
- scheduler child jobs and governed uploaded-artifact analysis;
- `FrustraMpnnResultsViewer.tsx` exact landscape viewer;
- disabled parent persistence hardening in `result_ingester.py`;
- retirement scanner `scripts/check_frustrampnn_retirement.py`;
- existing parent wiring in Structure Prediction, Complex Prediction, Protein Design, Antibody and Conformational Mapping;
- historical read compatibility without new legacy writes.

Existing runtime identity remains authoritative unless live preflight proves drift:

- image: `/mnt/BioModStack/apptainer/frustrampnn.sif`;
- checkpoint: `/opt/frustrampnn_weights/megascale.ckpt`;
- canonical CLI inside the image;
- scheduler-selected physical GPU through task isolation and task-visible CUDA index `0`.

## 3. Remaining gaps

1. PR #52’s global configuration foundation is incomplete and currently uncommitted after an interrupted implementation worker.
2. Registry integration metadata needs strict bounded validation.
3. Public model routes must not expose FrustraMPNN as a generic direct-launch model, while the bounded integration endpoint must remain available to workflow UIs.
4. Structure Prediction must apply the configured default exactly once without a delayed API response overwriting an explicit saved or operator choice.
5. Only Structure Prediction currently consumes the shared `ModelIntegrationControl`.
6. Antibody de novo/refinement still has duplicated local FrustraMPNN toggles/copy.
7. Results Viewer actions and any reachable Protein Design/Complex Prediction controls need consistent central presentation metadata without changing their backend request semantics.
8. Conformational Mapping should display central model/checkpoint identity while retaining CM-owned analysis/ranking semantics.
9. `platform/api/services/result_contracts.py` incorrectly classifies FrustraMPNN as sequence design.
10. No genuine enabled Structure Prediction result has yet been accepted on the latest global-config code.
11. The prior Structure Prediction job `4df37353-755f-4065-b627-7f402d04f143` is diagnostic-only: it was effectively disabled and failed old ingestion behavior.
12. The existing ten-case Phase 6 harness is disproportionate for this completion tranche; it remains an optional diagnostic asset, not the mandatory release denominator.

## 4. Minimal global FrustraMPNN configuration contract

### 4.1 Backend model registry

Modify only the existing registry:

- `platform/api/model_registry.py`
- `platform/api/config/models/frustrampnn.yaml`

Retain:

```python
public_launch: bool = True
integration: Optional[ModelIntegration] = None
```

FrustraMPNN record:

```yaml
id: frustrampnn
name: FrustraMPNN
category: scientific_analysis
public_launch: false
enabled: true
integration:
  stage_parameter: run_frustrampnn
  operator_label: Frustration analysis
  checkpoint_label: MegaScale-trained checkpoint
  model_summary: >
    Maps residue-level energetic frustration and amino-acid substitution
    landscapes for structure interpretation and scientifically guided mutagenesis.
  semantic_roles:
    - structure_interpretation
    - mutagenesis_guidance
    - workflow_specific_quality_control
```

Workflow records may exist only for current FrustraMPNN-enabled parents:

- `structure_prediction`;
- `complex_prediction`;
- `protein_design`;
- `antibody_design`;
- `conformational_mapping`.

Validation requirements:

- `operator_label`, `model_summary`, every workflow summary and every semantic role are nonblank;
- semantic roles are unique after whitespace normalization;
- workflow IDs are drawn from the five-item set above;
- `stage_parameter` is exactly `run_frustrampnn` for model `frustrampnn`;
- malformed integration configuration fails registry load with a bounded actionable error;
- do not add registry abstractions for other MPNNs in this tranche;
- no runtime host path, image digest, checkpoint path, command argv, or physical GPU ID is exposed through presentation metadata.

### 4.2 Public API exposure

Modify:

- `platform/api/routers/models.py`;
- `platform/frontend/src/lib/api.ts`.

Contract:

- `GET /api/models` excludes disabled and `public_launch: false` records;
- `GET /api/models/frustrampnn` returns 404 because generic launch is forbidden;
- `GET /api/models/frustrampnn/integration` returns only:
  - model ID/name/version;
  - stage parameter;
  - operator label;
  - checkpoint label;
  - model summary;
  - semantic roles;
  - workflow defaults/summaries;
- the integration endpoint returns 404 for missing, disabled or unconfigured records;
- generic job validation uses the public-launch lookup and cannot launch FrustraMPNN directly;
- scheduler-backed `/api/frustrampnn/*` artifact actions remain the only standalone analysis action surface.

## 5. Shared enabled-only frontend control

Files:

- `platform/frontend/src/components/ModelIntegrationControl.tsx`;
- `platform/frontend/src/components/modelIntegrationControlState.ts`;
- `platform/frontend/src/components/StructurePredictionTemplate.tsx`;
- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`;
- `platform/frontend/src/components/ResultsViewer.tsx`;
- only reachable FrustraMPNN surfaces in `JobSubmission.tsx`;
- Conformational Mapping model-information surface only where it already exists.

Behavior:

1. Disabled state shows only **Frustration analysis** and the checkbox/control.
2. Enabled state adds one compact highlighted block:
   - `FrustraMPNN`;
   - `MegaScale-trained checkpoint`;
   - workflow-specific scientific summary.
3. Do not show the model block while disabled.
4. Do not describe the capability universally as QC.
5. Do not show numerical thresholds in workflow cards.
6. Do not expose physical GPU selection.
7. Query failures retain the fallback label and the explicit local selection; they do not silently disable or enable execution.
8. A configured default is applied at most once and only if no saved/template/request boolean exists.
9. Any operator click marks the selection explicit before a delayed config response can arrive.
10. Existing saved jobs/templates preserve their explicit `run_frustrampnn` boolean.

### 5.1 Structure Prediction

- Keep `run_frustrampnn` request compilation.
- Keep the control excluded from the Boltz API-import path.
- Global workflow default may remain enabled for canonical local Structure Prediction.
- Explicit false opt-out must survive preview, template save/load and submission.
- Enabled model failure blocks terminal parent success.

### 5.2 Antibody de novo/refinement

- Replace duplicated local presentation blocks with one shared configured control per reachable operator surface.
- Preserve refinement presets, downstream locks, effective-run calculations, template hydration and request compilation.
- Do not allow a late config response to override a preset/template/operator decision.
- Preserve the existing rejection of FrustraMPNN on stale post-IgGM structures.
- Antibody-specific filtering/ranking policy remains outside FrustraMPNN.

### 5.3 Results Viewer and artifact actions

- Use global label/model/checkpoint text for reanalyze/selected-artifact actions.
- Preserve selected Design/artifact IDs and immutable hashes.
- The action queues a persisted scheduler child job and never calls Apptainer from the browser/request thread.
- Open the dedicated FrustraMPNN viewer for canonical child results.

### 5.4 Protein Design, Complex Prediction and Conformational Mapping

- Do not add new cards solely for symmetry.
- Where a reachable existing FrustraMPNN control exists, switch its presentation/default to the central integration record without changing canonical backend request keys.
- Conformational Mapping retains its own ensemble comparison, support, ranking, resampling and mutagenesis handoff semantics.
- Central metadata supplies only FrustraMPNN identity/checkpoint/purpose.

## 6. Result-contract correction

Modify `platform/api/services/result_contracts.py`.

1. Remove `frustrampnn` from `sequence_design_v1`; FrustraMPNN does not create sequences.
2. Add `frustration_analysis_v1` with:
   - model/stage ID `frustrampnn`;
   - required artifacts: source/normalized structure authority as applicable, exact structure map, raw output, complete landscape, summary, execution receipt and result manifest;
   - viewer capabilities: exact residue landscape, residue mapping, structure viewer, content-addressed download;
   - no sequence-design artifact class or FASTA requirement.
3. Keep Conformational Mapping’s own result contracts intact; its FrustraMPNN-derived artifacts remain CM-owned projections where already required.
4. Historical summary-only rows remain readable but are explicitly legacy and cannot be expanded into fabricated N×20 rows.

Update ingestion only if this contract correction reveals a real mismatch:

- `platform/api/services/result_ingester.py`;
- existing FrustraMPNN manifest-first handlers.

No broad refactor is authorized. Preserve exact disabled-state enforcement:

```json
"stage_outputs": {"frustrampnn": []}
"provenance": {
  "stage_terminal_states": {
    "frustrampnn": {"status": "not_requested", "outputs": []}
  }
}
```

Aliases, duplicates, malformed forms, missing paired state and contradictory output remain rejected.

## 7. Scheduler/runtime invariants

No new runtime architecture is required. Verify and preserve:

- one canonical FrustraMPNN component and one canonical adapter;
- scheduler-assigned physical GPU only;
- exact image/executable/checkpoint identities in receipts;
- immutable input/source hashes;
- exact request/result/manifest closure;
- strict nonzero-exit handling;
- exactly 20 canonical amino-acid slots per scoreable residue;
- explicit missingness/non-finite rejection;
- exact source-to-normalized-to-model-to-author-residue mapping;
- required execution failure prevents parent terminal success;
- disabled state schedules zero FrustraMPNN tasks;
- no direct request-thread, browser-thread, unmanaged subprocess or acceptance-script inference.

Do not change runtime code merely to make it look symmetrical with registry code.

## 8. Minimal test denominator

This is the complete required automated denominator. Do not silently expand it.

### 8.1 Registry/API owner

File:

- `platform/api/tests/test_model_registry.py`.

Required cases:

- internal FrustraMPNN excluded from list/direct detail;
- bounded integration projection available;
- disabled integration unavailable;
- unknown workflow rejected;
- wrong stage parameter rejected;
- blank/duplicate semantic roles rejected;
- blank label/model/workflow summary rejected.

### 8.2 Frontend owner

Files:

- `platform/frontend/tests/modelIntegrationControl.test.ts`;
- existing Structure Prediction static contract.

Required cases:

- disabled hides model/checkpoint/summary;
- enabled exposes model/checkpoint/workflow summary;
- delayed default applies once;
- explicit saved false is preserved;
- operator choice before query resolution is preserved;
- Structure Prediction still emits explicit true/false and hides the control on the Boltz API path;
- no `FrustraMPNN QC` wording.

Use a pure state helper plus static source contract if the repository lacks a lightweight React renderer. Do not add a test dependency.

### 8.3 Existing backend FrustraMPNN owners

Run only focused existing owners affected by final edits:

- parent wiring/disabled persistence tests;
- result-contract test;
- ingestion tests only if ingestion changed;
- retirement scanner;
- the smallest Nextflow syntax/static contract only if workflow files changed.

Do not run the earlier broad backend 326-test set, broad frontend suite, ten-case Phase 6 harness or whole repository suite.

## 9. Real Development acceptance

One genuine enabled owner path is mandatory after exact code is merged to `test` and deployed to canonical Development.

### 9.1 Required successful path

Use Structure Prediction with the governed 1UBQ fixture or the existing immutable equivalent:

1. Confirm deployed source/build SHA, API/frontend listener owners, target branch and Development DB.
2. Open the canonical Structure Prediction card.
3. Confirm **Frustration analysis** is visible.
4. Confirm enabled-only FrustraMPNN/checkpoint/context copy.
5. Submit through the governed card/API.
6. Prove the parent scheduler job and FrustraMPNN child lineage.
7. Prove scheduler-assigned physical GPU and task-visible device receipt.
8. Prove exact image/executable/checkpoint/input hashes and command argv.
9. Prove successful canonical result manifest and all artifact hashes.
10. For 1UBQ, require exactly `76 × 20 = 1,520` unique finite slots and exactly one native slot per residue.
11. Prove persisted API projection and governed downloads.
12. Open the FrustraMPNN viewer and prove Mol* coloring/table selection maps by exact `(auth_asym_id, auth_seq_id, insertion_code)`.
13. Capture one browser screenshot and one machine-readable acceptance receipt.

### 9.2 Disabled path

Use one request-level/card-level opt-out without model inference:

- explicit `run_frustrampnn: false`;
- zero FrustraMPNN child/task;
- exact paired `not_requested`/empty-output persistence;
- ordinary parent result ingestion succeeds.

This may be demonstrated with the smallest deterministic contract/fixture rather than a second expensive predictor run if the persisted parent fixture is exact and current-build bound.

### 9.3 Failure path

A fresh model failure is required only if no current-build evidence already proves enabled required failure blocks parent success. Reuse exact current-build evidence when valid; do not rerun failures for ceremony.

Live acceptance fails rather than skips or fabricates evidence when the managed services, scheduler, GPU or fixture are unavailable.

## 10. Retirement and negative proof

After successful owner-path acceptance, scan production source for and remove only remaining contradictory FrustraMPNN paths:

- `Run FrustraMPNN QC` / `FrustraMPNN QC` user-visible naming;
- direct browser/request-thread `/api/frustrampnn/analyze` inference;
- unmanaged `run_batch_frustrampnn` execution;
- stale `containers/frustrampnn.sif` execution path;
- duplicate frontend threshold constants;
- direct FrustraMPNN Apptainer execution outside the canonical runtime adapter and explicit preflight tooling;
- loose `*_frustration.csv` ingestion authority;
- basename-only candidate joins;
- placeholder success or header-only model outputs.

Historical database read fields and migration compatibility may remain. Git history is the archive for deleted implementation paths.

## 11. Ordered implementation slices

### Slice 0 — finish global configuration foundation

- Reconcile interrupted worker edits against this specification.
- Complete registry/API/frontend focused tests.
- Spec review, then code-quality review.
- Commit and push PR #52.
- No endpoint migration yet.

**Gate:** central FrustraMPNN integration metadata is safe, validated and correctly consumed by Structure Prediction.

### Slice 1 — result contract and endpoint convergence

- Correct `result_contracts.py`.
- Migrate Antibody duplicated presentation.
- Migrate reachable Results Viewer/action copy.
- Integrate central metadata into existing Protein Design/Complex/CM surfaces only where already present.
- Run only affected focused tests.

**Gate:** all reachable presentation is neutral, enabled-only and globally sourced without changing backend scientific policy.

### Slice 2 — exact-tree review and Development deployment

- Rebase/reconcile current `origin/test`.
- Run the minimal denominator.
- Obtain independent spec-compliance and code-quality approval on the exact candidate.
- Merge to `test`.
- Fast-forward canonical Development and restart only managed API/frontend services.
- Prove deployed revision and health.

**Gate:** exact tested candidate is live in Development; `main` untouched.

### Slice 3 — bounded live acceptance

- Run the successful enabled owner path.
- Prove disabled exact state.
- Reuse or produce one valid fail-closed receipt as required.
- Capture hashes, lineage, GPU receipt, manifest/cardinality, API and Mol* evidence.

**Gate:** one real visible frustration map exists from the governed product path.

### Slice 4 — retirement and final report

- Perform bounded negative scans.
- Remove only contradictions found.
- Re-run changed-owner checks if source changes.
- Publish final evidence packet and completion accounting.

**Gate:** no duplicate/direct/fail-open path remains; historical reads intact.

## 12. Definition of done

FrustraMPNN is complete only when:

- the global registry is the single source for its stable presentation/default metadata;
- generic direct model launch is blocked;
- all reachable workflow controls use **Frustration analysis**;
- model/checkpoint/scientific information appears only when enabled;
- explicit saved/operator choices cannot be overwritten by delayed config;
- result contracts classify FrustraMPNN as analysis, not sequence design;
- enabled execution remains scheduler-owned and fail closed;
- disabled execution persists the exact paired `not_requested` state and schedules no model task;
- exact author-residue mapping and backend classification remain authoritative;
- one current-build Structure Prediction result produces a genuine visible frustration map and exact 1UBQ cardinality if that fixture is used;
- governed manifests, receipts, hashes, GPU lineage, API projection and Mol* rendering are retained;
- direct/unmanaged/duplicate/placeholder paths are absent;
- only focused tests and bounded live acceptance were used;
- exact accepted code is on `origin/test` and canonical Development;
- `main` and production remain untouched.

## 13. Completion reporting

Report separate percentages for:

- specification/contract;
- source implementation;
- focused automated verification;
- Development deployment;
- live scientific owner-path qualification;
- explicitly deferred optional endpoint conveniences.

Do not count this document as implementation. Do not count optional support for other MPNN-family models against FrustraMPNN completion.