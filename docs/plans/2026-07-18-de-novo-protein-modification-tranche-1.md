# De Novo Design Experimental — Tranche 1 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the independent Protein CAD Experimental and Protein Local Redesign products with one experimental `protein_modification_experimental` product that exposes truthful `de_novo_design` and `region_redesign` modes while reusing the existing engines.

**Architecture:** The parent product is canonical at the UI/API/provenance level. Tranche one does not force two mature, structurally different Nextflow implementations into one cosmetic file: model+mode routing selects the existing La-Proteina/DISCO generation entrypoint or RFdiffusion3 local-redesign entrypoint. Historical IDs remain readable and canonicalize to the parent, but are not exposed as fresh launcher products.

**Tech Stack:** React/TypeScript, FastAPI/Python service routing, YAML model/template registry, Nextflow DSL2, pytest, Node TAP tests.

---

## Product contract

Canonical product:

- ID: `protein_modification_experimental`
- Name: **De Novo Design**
- Experimental: `true`

Modes:

1. `de_novo_design`
   - Purpose: generate new protein structures or scaffold motifs/ligands/nucleic acids.
   - Engines: `disco`, `laproteina`.
   - Existing implementation owner: `workflows/protein_cad_experimental.nf` and `modules/protein_cad_experimental.nf`.
2. `region_redesign`
   - Purpose: modify selected regions of an existing protein/complex while preserving selected context.
   - Engines: RFdiffusion3 → FAMPNN/ProteinMPNN → optional structure validation.
   - Existing implementation owner: `workflows/protein_local_redesign.nf`.

This product does **not** claim that de novo generation and regional modification are interchangeable algorithms. They share product ownership, artifact/provenance conventions, review surfaces, and future expansion—not one parameter schema.

Future expansion targets protein design/modification engines whose contracts fit this workbench, including candidates such as RFD3, FrustraMPNN, and LigandMPNN. Protein Hunter is explicitly excluded: its primary product target is the general de novo binder workflow.

## Historical compatibility contract

- `protein_cad_experimental` historical jobs/results remain readable.
- `protein_local_redesign` historical jobs/results remain readable.
- Clone/retry/deep-link behavior canonicalizes old IDs to `protein_modification_experimental` plus the appropriate mode.
- Old IDs are absent from active launcher cards. The old CAD template may remain
  compatibility-only until the parent launcher reaches advanced-control parity.
- Existing internal workflow filenames may remain in tranche one as mode-specific implementation details.
- New artifacts record:
  - `workflow: protein_modification_experimental`
  - `modification_mode: de_novo_design | region_redesign`
  - `generator_family` or `engine_family` retaining the actual engine identity.

## Explicit non-goals

Deferred to the later first-class Protein Modification Suite spec:

- unified 3D region/motif/constraint editor;
- common generative-region intermediate representation;
- insertion/deletion/loop grafting suite;
- iterative design-scoring optimization loops;
- sequence-only editing and language-model mutation engines;
- common candidate comparison workbench;
- production graduation from experimental;
- replacing the two internal Nextflow implementations with a single generalized orchestrator.

Caliby and Protein Hunter integration are **not** implemented in this tranche. They receive a separate end-to-end integration spec after their runtime and artifact semantics are audited.

---

### Task 1: Add RED product-boundary contracts

**Objective:** Prove the old products are independently launchable before implementing the parent.

**Files:**
- Create: `platform/api/tests/test_protein_modification_product_boundary.py`
- Create: `platform/frontend/tests/proteinModificationProductBoundary.test.ts`

**Assertions:**

- canonical model has exactly `de_novo_design` and `region_redesign`;
- both canonical modes resolve to existing internal entrypoints;
- old launcher-card IDs are forbidden and compatibility templates are hidden;
- one experimental launcher card exists;
- historical IDs normalize to the canonical product/mode;
- de novo mode exposes DISCO/La-Proteina;
- regional mode reuses the visual local-redesign launcher;
- result provenance preserves canonical workflow plus actual engine/mode.

**RED command:**

```bash
PYTHONPATH=platform/api platform/api/.venv/bin/python -m pytest -q \
  platform/api/tests/test_protein_modification_product_boundary.py
```

Expected: failure because the canonical parent does not exist and old launch products remain.

### Task 2: Add canonical model metadata

**Objective:** Define the experimental product and its two stable modes.

**Files:**
- Create: `platform/api/config/models/protein_modification_experimental.yaml`
- Modify: `platform/api/config/models/protein_cad_experimental.yaml`

**Implementation:**

- Add canonical product metadata with both mode IDs.
- Keep old model metadata explicitly compatibility-only; do not advertise it in launcher inventory.
- Reuse existing parameter names for tranche one; do not invent a premature universal schema.

### Task 3: Add canonical API routing and fail-closed legacy normalization

**Objective:** Route parent modes to existing internal implementations without preserving standalone products.

**Files:**
- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/tests/test_nextflow_entrypoint_registry.py`

**Routing:**

```text
protein_modification_experimental + de_novo_design
  -> workflows/protein_cad_experimental.nf
  -> profile protein_cad_experimental

protein_modification_experimental + region_redesign
  -> workflows/protein_local_redesign.nf
  -> profile protein_local_redesign
```

**Normalization:**

- canonical de novo mode reuses the current `pcad_*` mapping;
- canonical regional mode reuses the current `plr_*` mapping;
- legacy model IDs are accepted only through an explicit compatibility canonicalizer;
- unknown modes fail rather than falling through to `main.nf` or another product.

### Task 4: Canonicalize result provenance

**Objective:** Make new jobs belong to the parent while preserving engine identity.

**Files:**
- Modify: `platform/api/services/result_ingester.py`
- Test: `platform/api/tests/test_protein_modification_product_boundary.py`

**Required fields:**

```json
{
  "workflow": "protein_modification_experimental",
  "modification_mode": "de_novo_design",
  "engine_family": "disco"
}
```

or:

```json
{
  "workflow": "protein_modification_experimental",
  "modification_mode": "region_redesign",
  "engine_family": "rfdiffusion3"
}
```

Historical source fields may remain nested as source provenance but may not replace the canonical product identity.

### Task 5: Replace two launcher products with one experimental launcher

**Objective:** Present one truthful product card and mode chooser.

**Files:**
- Create: `platform/frontend/src/components/ProteinModificationTemplate.tsx`
- Modify: `platform/frontend/src/components/JobSubmission.tsx`
- Modify: `platform/frontend/src/components/jobSubmissionTemplateState.ts`
- Modify narrowly: `platform/frontend/src/components/ProteinLocalRedesignTemplate.tsx`
- Retain hidden: `platform/api/config/templates/protein_cad_experimental.yaml`

**Behavior:**

- Experimental inventory contains one **De Novo Design** card.
- Parent mode chooser explains the distinction between fresh de novo generation and regional redesign.
- `region_redesign` delegates to the existing visual local-redesign component with model/mode overrides.
- `de_novo_design` exposes the current backend/task/design-count/length controls and submits the canonical model/mode.
- Existing local-redesign component gains optional `submissionModelId` and `submissionMode` props; defaults preserve historical/internal use.
- Old launcher IDs and compatibility templates do not render as product cards.

### Task 6: Update model inventory and documentation links

**Objective:** Document engines under the parent product instead of as independent products.

**Files:**
- Modify: `platform/frontend/src/components/workflowModelInventory.ts`
- Modify: `platform/frontend/tests/modelDocumentationLinkoutsContract.test.ts`

**Expected parent topics:**

- `laproteina`
- `disco`
- `rfdiffusion`
- `fampnn`
- `proteinmpnn`
- `boltz2`

### Task 7: Verify mode-specific Nextflow entrypoints

**Objective:** Prove both parent modes compile through their actual internal entrypoints.

**Files:**
- Modify: `platform/api/tests/test_nextflow_lint_regressions.py`

**Gates:**

- managed Nextflow lint for both internal entrypoints;
- `-preview -offline` for canonical de novo mode;
- `-preview -offline` for canonical region-redesign mode with a minimal source fixture;
- explicit writable `-w` directory;
- no GPU tasks executed during preview.

### Task 8: Complete non-runtime acceptance

**Objective:** Freeze tranche one before Caliby/Protein Hunter integration begins.

**Commands:**

```bash
# Backend focused contracts
PYTHONPATH=platform/api platform/api/.venv/bin/python -m pytest -q \
  platform/api/tests/test_protein_modification_product_boundary.py \
  platform/api/tests/test_nextflow_entrypoint_registry.py \
  platform/api/tests/test_nextflow_lint_regressions.py

# Frontend
cd platform/frontend
pnpm test
pnpm exec tsc -b --pretty false
pnpm exec vite build --outDir /tmp/bms-protein-modification-build --emptyOutDir

# Hygiene
cd ../..
git diff --check -- <tranche-one-path-allowlist>
```

**Acceptance:**

- one launcher/product ID;
- two explicit modes;
- zero old fresh-launch cards/templates;
- both parent modes resolve and preview;
- historical reads remain supported;
- no claim that the later full Protein Modification Suite exists.

## Commit boundary

One focused tranche-one commit may include only the product-boundary tests, canonical model/API routing, merged launcher, compatibility normalization, provenance updates, model inventory, and this spec. Caliby/Protein Hunter integration belongs to the next separately reviewed commit/spec.
