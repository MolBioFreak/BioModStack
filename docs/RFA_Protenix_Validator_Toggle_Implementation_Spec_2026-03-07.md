# RFA Antibody Workflow: Protenix Validator Toggle and Interactive Validation Bridge

Date: 2026-03-07
Status: planning
Owner: antibody workflow / platform integration

## 1. Scope

This spec covers the next implementation step for the antibody workflow:

- add Protenix as a selectable structure-validation task inside `workflows/antibody_denovo.nf`
- make structure validation selectable between `Boltz2` and `Protenix`
- generalize the current Boltz-only validation stage into a validator-aware stage
- add a real post-validation pause point for interactive review
- preserve compatibility with the current static antibody workflow

This spec does not cover:

- simultaneous dual validation inside the antibody workflow (`Boltz2` + `Protenix` in one antibody run)
- the full campaign/round/evaluation schema redesign
- replacement of the existing standalone `structure_prediction` workflow

## 2. Current State

### 2.1 What already exists

- `modules/protenix.nf` already implements Protenix prediction for single-chain and complex inputs.
- `modules/structure_prediction.nf` already routes between `boltz`, `rf3`, `protenix`, `both`, and `all`.
- `platform/api/services/result_ingester.py` already parses Protenix confidence outputs into `Design` metrics.
- the platform frontend already exposes Protenix in the standalone structure-prediction template.
- the antibody workflow already has one interactive gate: `post_fampnn`.

### 2.2 What is still hardwired to Boltz

- `workflows/antibody_denovo.nf` step 3 is explicitly Boltz-only.
- `workflows/antibody_child.nf` is Boltz-only.
- `modules/antibody_batch.nf` is Boltz-only.
- antibody template text, settings, defaults, and filters are Boltz-specific.
- stage names and stage reporting are Boltz-specific.
- post-validation maturation is named `run_post_boltz_maturation`.

### 2.3 Important format gap

Protenix outputs `mmCIF`.

Several downstream antibody steps still assume `PDB` input:

- post-validation `PPIFlow`
- `OpenMM`
- some structure-oriented downstream scoring/filtering

The current platform can ingest `mmCIF`, but the antibody workflow still needs a structure-normalization step for downstream tools that require `PDB`.

## 3. Implementation Decisions

### 3.1 Validator selector

Add a new antibody workflow parameter:

- `structure_validator`
  - allowed values: `boltz2`, `protenix`
  - default: `boltz2`

This parameter is antibody-specific. Do not reuse `pred_method` directly in the antibody template.

Rationale:

- the standalone structure-prediction workflow supports broader predictor combinations
- the antibody workflow currently needs one validator choice for one validation stage
- keeping the antibody contract narrow reduces ambiguity and avoids dragging `rf3/both/all` logic into the antibody pipeline

### 3.2 Validation stage naming

Introduce a logical stage name:

- `structure_validation`

Introduce a gate name:

- `post_structure_validation`

Keep predictor identity in params and gate payload:

- `structure_validator=boltz2|protenix`

Do not keep baking `boltz2` into user-facing stage names for new antibody jobs.

Backward compatibility:

- historical jobs may still show `boltz2`
- legacy params and stage display logic must continue to render old jobs correctly

### 3.3 Exclusive validator mode for antibody workflow phase 1

For this implementation, the antibody workflow will select exactly one validator:

- `boltz2`
- `protenix`

Do not implement `both` inside `antibody_denovo.nf` in this phase.

Rationale:

- the current `Design` model stores one primary structure path and one primary confidence payload
- simultaneous dual-validator comparison inside antibody jobs should wait for a proper evaluation model
- forcing `both` into the current antibody schema would create ambiguity and bug-prone overwrite behavior

### 3.4 Post-validation maturation naming

Add a new logical parameter:

- `run_post_validation_maturation`

Backward compatibility alias:

- if `run_post_boltz_maturation` is present and `run_post_validation_maturation` is unset, treat it as the same setting

The downstream step remains the same conceptually:

- run `PPIFlow` on validated structures

The validated structures may now come from either Boltz2 or Protenix.

## 4. Target Behavior

### 4.1 Static mode

The antibody workflow should continue to support a fully automatic run:

- `RFA -> FAMPNN -> optional pre-validation filters -> selected validator -> optional post-validation maturation -> rest of pipeline`

### 4.2 Interactive mode

The antibody workflow should support:

- `post_fampnn`
- `post_structure_validation`

At `post_structure_validation`, the workflow pauses after validator output ingestion and metric availability.

The user reviews results in the existing data viewer / results surfaces and decides what to do next.

### 4.3 Validation output behavior

Regardless of selected validator:

- validation artifacts must be staged in a predictable output location
- parsed confidence metrics must be available on `Design`
- downstream structure-based tools must receive normalized `PDB` inputs where required
- original raw artifacts must be preserved

## 5. Required Workflow Changes

### 5.1 `workflows/antibody_denovo.nf`

Add antibody-specific validator defaults:

- `structure_validator`
- `run_post_validation_maturation`
- `interactive_gate_stage` support for `post_structure_validation`

Refactor step 3 from Boltz-only logic into validator dispatch:

- shared input assembly from design sequences
- branch on `structure_validator`
- sequential path:
  - `boltz2` -> existing Boltz path
  - `protenix` -> Protenix prediction path
- exploration path:
  - pass validator choice into child validation jobs

Generalize stage reporting:

- logical stage = `structure_validation`
- payload includes selected validator name

Add a second pause condition:

- if interactive mode is enabled and gate stage is `post_structure_validation`, pause after validator outputs are ingested and normalized

Generalize post-validation maturation:

- consume normalized validated structures regardless of validator
- use `run_post_validation_maturation`

### 5.2 `workflows/antibody_child.nf`

Extend child workflow to accept validator choice.

Replace Boltz-only batch validation assumption with validator dispatch:

- `boltz2` -> existing batch validation path
- `protenix` -> new batch Protenix validation path

Downstream scoring stages (`AntiBERTy`, `ThermoMPNN`) continue to consume validated `PDB`s after normalization.

### 5.3 `modules/antibody_batch.nf`

Keep existing `BatchBoltzValidation`.

Add:

- `BatchProtenixValidation`

Responsibilities:

- stage input design PDBs
- extract sequence + stable design name for each input
- run Protenix prediction in batch-compatible form
- preserve raw Protenix `mmCIF` + confidence outputs
- align / compare validated structures back to source design structures
- emit normalized validated `PDB`s plus aligned JSON metrics for antibody downstream use

This module must not discard raw Protenix artifacts.

### 5.4 `modules/protenix.nf`

Prefer reuse over duplication.

Required review/change items:

- confirm output naming is stable enough for antibody workflow aggregation
- ensure model settings needed by antibody jobs are exposed cleanly
- ensure the emitted confidence artifacts are consistent with the result ingester

No major redesign is required here unless batch naming or output collection proves insufficient.

## 6. Required Structure-Normalization Step

### 6.1 Why it is required

Protenix emits `mmCIF`.

Antibody downstream steps still expect `PDB`.

### 6.2 Required behavior

Introduce a workflow-side structure-normalization step after validation output collection and before downstream structure-based consumers.

The normalization step must:

- accept `PDB` or `mmCIF`
- emit canonical downstream `PDB`
- preserve chain ids and residue numbering as far as possible
- retain original source artifact path in metadata

### 6.3 Existing code to reuse

The platform already has `convert_cif_to_pdb()` in `platform/api/services/structure_utils.py`.

Implementation should reuse the same conversion logic in a workflow-safe script, not invent a second incompatible conversion rule.

Recommended new script:

- `scripts/normalize_validated_structures.py`

Responsibilities:

- convert Protenix `mmCIF` to `PDB`
- copy existing Boltz `PDB` through unchanged
- emit a manifest mapping:
  - source artifact
  - normalized artifact
  - predictor
  - design name

## 7. Required API / Backend Changes

### 7.1 Job params normalization

Add normalization support in job creation/resume for:

- `structure_validator`
- `run_post_validation_maturation`

Backward-compatible param alias handling:

- map `run_post_boltz_maturation` -> `run_post_validation_maturation` if needed

### 7.2 Stage metadata

Update stage computation and display logic to support:

- `structure_validation`
- `post_structure_validation`
- validator metadata in stage payload

### 7.3 Result ingestion

No new Protenix parser is required; Protenix parsing already exists.

Required backend work:

- ensure antibody jobs using Protenix land their normalized outputs and raw confidence artifacts where the ingester can find them
- ensure downstream display and filters do not assume Boltz-specific field naming

## 8. Required Frontend Changes

### 8.1 `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`

Add validator selector:

- `Boltz2`
- `Protenix`

Submit:

- `structure_validator`
- Protenix settings when selected
- Boltz settings when selected

Do not expose `rf3` or `all` in the antibody template.

### 8.2 `platform/frontend/src/components/QualitySettingsPanel.tsx`

Refactor the current Boltz-only validation settings into:

- shared structure-validation section
- conditional Boltz settings panel
- conditional Protenix settings panel

Preserve existing Boltz defaults.

Add Protenix controls reused from the standalone structure-prediction UI:

- model weights
- seeds
- sample count
- step count
- cycle count
- use MSA
- use template

### 8.3 Validation filtering labels

Replace Boltz-specific wording in the antibody template/UI where the logic is now validator-agnostic.

Examples:

- `Boltz2 validation` -> `structure validation`
- `post-Boltz filtering` -> `post-validation filtering`

### 8.4 Results / data viewer

Do not add a new screen.

Use the existing results/data viewer surfaces.

Required UI changes:

- show validator identity on antibody validation outputs
- expose `post_structure_validation` pause state
- ensure plot/filter surfaces can use Protenix-ingested metrics the same way they use Boltz metrics

## 9. Post-Validation Filtering Contract

The current antibody workflow uses Boltz-named thresholds.

For this implementation:

- filtering should operate on generic ingested `Design` metrics
- do not make the filtering logic predictor-specific unless the metric is truly predictor-exclusive

Recommended common filter surface:

- `rmsd_binder`
- `iptm`
- `plddt_overall`
- `conf_score`
- `has_clash`

Backward compatibility:

- accept old Boltz-named params
- internally map them to the generic validation-filter logic

## 10. Interactive Bridge Changes

### 10.1 New gate

Implement:

- `post_structure_validation`

Gate payload should include:

- validator used
- normalized output directory
- raw artifact directory
- candidate count
- available metric keys
- summary statistics for major metrics

### 10.2 Why this gate matters

This is the first stage where:

- structure confidence is available
- self-consistency / interface metrics are available
- PPIFlow, FrustraMPNN, and further redesign choices can be made on validated structures instead of raw sequence designs

## 11. File Touchpoints

Primary files expected to change:

- `workflows/antibody_denovo.nf`
- `workflows/antibody_child.nf`
- `modules/antibody_batch.nf`
- `modules/protenix.nf` (review / light touch only unless output collection requires more)
- `scripts/normalize_validated_structures.py` (new)
- `platform/api/routers/jobs.py`
- `platform/api/services/nextflow.py`
- `platform/api/run_migration.py` if new persisted fields are added
- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
- `platform/frontend/src/components/QualitySettingsPanel.tsx`
- `platform/frontend/src/components/dashboard/JobQueueTable.tsx`
- `platform/frontend/src/constants/displayNames.ts`

Secondary files likely affected:

- result-ingestion path handling
- stage display helpers
- docs / workflow review note

## 12. Migration / Compatibility Rules

- default validator remains `boltz2`
- old antibody jobs remain viewable as Boltz-backed jobs
- old `run_post_boltz_maturation` remains accepted
- existing standalone Protenix workflow remains unchanged
- no simultaneous `boltz2 + protenix` antibody validation in this phase

## 13. Acceptance Criteria

The implementation is complete when all of the following are true:

- antibody template lets the user choose `Boltz2` or `Protenix`
- submitted antibody jobs persist validator choice
- sequential antibody validation respects validator choice
- exploration-mode antibody child validation respects validator choice
- Protenix antibody outputs are ingested and visible in the existing results/data viewer
- Protenix validated structures are normalized to downstream `PDB` where required
- `post_structure_validation` works as a real pause point
- post-validation `PPIFlow` can consume structures validated by either predictor
- existing Boltz-backed antibody behavior remains intact

## 14. Explicit Deferred Work

The following are intentionally deferred to the next architecture phase:

- dual-validator antibody runs (`boltz2` and `protenix` in one antibody job)
- dedicated `design_evaluation` table
- campaign / round / selection-set schema
- action launching from the viewer for arbitrary next-round jobs

These are compatible with this implementation and should build on top of it rather than replace it.

## 15. Implementation Status Update 2026-03-08

This plan is no longer speculative. Most of the scoped validator-toggle bridge is now implemented.

Implemented:

- antibody launcher selector for `Boltz2` vs `Protenix`
- generic `structure_validation` stage naming across queue/UI
- `post_structure_validation` pause point
- sequential and child-job validator dispatch for `Protenix`
- Protenix output normalization into downstream `PDB` plus preserved raw confidence artifacts
- result ingestion for antibody Protenix outputs
- viewer-launched follow-on antibody actions from selected results

Important implementation detail that differs from the earlier plan:

- the antibody workflow now uses a dedicated pre-resolution step for Protenix MSA handling rather than relying on Protenix's long-polling web updater during child validation.
- this is implemented through `scripts/prepare_protenix_msa.py`.
- supported modes:
  - `protenix_msa_backend=colabfold_api`
  - `protenix_msa_backend=local`
  - `protenix_msa_backend=auto`
- current `auto` policy:
  - small jobs -> `colabfold_api`
  - larger jobs -> `local`

Additional implementation work completed outside the original narrow validator toggle:

- RFantibody target normalization for raw RCSB multi-model inputs
- true SAbDab-to-HLT framework conversion for RFantibody compatibility
- antibody template routing / save-load repair
- stale antibody-template normalization on backend access
- MSA server GPU pinning propagation into actual MSA launch scripts

Current remaining gap:

- the new antibody `Protenix + prepare_protenix_msa.py` path still needs a clean end-to-end proof run after the latest Groovy module helper fix.
- the most recent live failure before this update was a Groovy helper parse bug in `modules/antibody_batch.nf` / `modules/protenix.nf`, not a Protenix scientific/runtime failure.

Acceptance checklist, current status:

- antibody template lets the user choose `Boltz2` or `Protenix` — done
- submitted antibody jobs persist validator choice — done
- sequential antibody validation respects validator choice — done
- exploration-mode antibody child validation respects validator choice — done
- Protenix antibody outputs are ingested and visible in the existing results/data viewer — implemented
- Protenix validated structures are normalized to downstream `PDB` where required — done
- `post_structure_validation` works as a real pause point — implemented
- post-validation `PPIFlow` can consume structures validated by either predictor — implemented in workflow wiring
- existing Boltz-backed antibody behavior remains intact — partially regression-checked, still needs more live-run coverage
