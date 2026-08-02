# Documentation Harmonization Strategy

## Goal

Keep BioModStack documentation aligned with the live system instead of letting
README text, subsystem docs, experimental workflow references, and dated plans
drift apart.

## Problems this work addresses

- root docs can lag behind the current runtime contract
- shell/runtime docs can drift after Electron, install-profile, or workflow-adapter changes
- experimental workflows can exist in code without being reflected in the canonical docs
- dated build/implementation plans can crowd out the docs that actually describe current behavior

## Canonical information architecture

### 1. Landing

- `README.md`

Purpose:

- explain what BMS is
- summarize the current runtime and launch surface
- show the default startup path
- route readers into the canonical docs

### 2. Canonical operator docs

- `docs/Platform_Overview.md`
- `docs/Workstation Set Up and Install Guide.md`
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
- `docs/Structure_Design_and_Refinement.md`
- `docs/Lab_Automation_MolBio_and_Sequencing.md`
- `docs/Results_and_Analysis.md`

Purpose:

- describe what the live system does
- describe how the workstation/runtime is laid out
- describe user-facing shells, workflows, and subsystem surfaces

### 3. Focused workflow/runtime references

- `docs/Experimental_Protein_CAD_Workflow.md`
- `docs/Caliby_Experimental_Workflow.md`
- `docs/Protein_Hunter_Experimental_Workflow.md`

Purpose:

- document live experimental families whose scope is too detailed for the main overview docs

### 4. Subsystem references

- `platform/api/README.md`
- `platform/frontend/README.md`
- `docs/ai_guidance/Model_Integrations.md`

Purpose:

- give deeper technical reference for the control plane, UI, and live model set

### 5. Active plans only

- `docs/plans/*.md`

Purpose:

- hold in-flight plans that still matter for incomplete work
- stay separate from canonical docs so plans do not masquerade as shipped behavior

## Rules going forward

### Keep canonical docs date-neutral

If a document describes current behavior, it should not need a date in the
filename.

### Prune stale planning docs

If a dated plan/spec is obsolete, superseded, or fully absorbed into canonical
docs, delete it from the tracked repo-facing docs surface instead of keeping it
as passive clutter.

### Keep README thin

The root README should summarize and route. It should not become the full manual
or a stale copy of subsystem documentation.

### Update docs when contracts change

These changes should trigger doc review in the same branch:

- runtime ownership changes
- install-profile or path-resolution changes
- new launch surfaces or shell integration contracts
- workflow-adapter changes
- new workflow entrypoints or major mode changes
- new API routers or user-facing endpoints
- new experimental workflow families that surface in the launcher/results UI

### Prefer capability docs over implementation speculation

Docs should state what is live, what is experimental, and what is internal-only.
Do not imply support merely because an older plan once existed.

## What was executed in this pass

- rewrote the root README around the current runtime and launch surface
- updated the docs index to distinguish canonical docs from active plans
- rewrote the desktop/runtime doc around the actual browser/Electron/Android-compatible service model
- updated platform/runtime/workflow docs for install profiles, workflow adapter, and experimental workflows
- aligned the API/frontend READMEs with the current shell/runtime contracts
- pruned older dated build/implementation planning docs that were cluttering the repo-facing surface

## Deferred follow-up

- add automated stale-doc and broken-link checks in CI
- add per-workflow operator runbooks when individual experimental families stabilize
- keep `docs/plans/README.md` curated so only active plans remain there
