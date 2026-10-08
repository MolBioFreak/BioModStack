# Caliby and Protein Hunter — De Novo Binder First-Class Integration Plan

> **Status:** Corrected product taxonomy. Implementation must use RED→GREEN slices and must not expose either engine in the general binder launcher until its full runtime, result, and review contract passes.

## Goal

Make Caliby and Protein Hunter first-class capabilities of the **general de novo binder workflow**.

## Product boundaries

### General de novo binder workflow — primary target

Canonical workflow ID currently available for migration: `binder_design`.

The future parent workflow should own a coherent campaign graph:

1. target/epitope definition;
2. binder backbone or sequence–structure generation;
3. fixed-backbone sequence design where applicable;
4. independent complex validation;
5. interface ranking with authoritative metrics;
6. canonical Design-row ingestion and review.

Planned engine roles:

- **Protein Hunter:** iterative whole-binder sequence–structure co-generation/search engine.
- **Caliby:** fixed-backbone sequence designer for binder backbones generated upstream.
- Other existing/future engines remain candidates only when their scientific contract fits the same campaign stages.

### De Novo Design — separate protein-design/modification workbench

Canonical compatibility ID: `protein_modification_experimental`.

Product name: **De Novo Design**.

Current modes:

- `de_novo_design` — DISCO / La-Proteina fresh protein generation;
- `region_redesign` — RFdiffusion3-driven local/region redesign.

Future candidates include protein-modification/design tools such as RFD3, FrustraMPNN, LigandMPNN, and similar engines. **Protein Hunter is not a mode of this product.**


Protein Hunter is not antibody-aware and must not be presented as a nanobody/antibody generator without explicit framework, numbering, CDR, chain-role, and preservation semantics.

## Caliby acceptance contract for general binder integration

Caliby may be exposed in `binder_design` only after all of the following pass:

1. Binder workflow provides one or more canonical source backbones plus binder/target chain roles.
2. Runtime preflight occurs before any Caliby package import or model load.
3. Only verified installed checkpoints are selectable; no implicit network download occurs.
4. Output sidecars preserve source-backbone lineage and artifact identity.
5. Selection semantics identify Caliby Potts energy correctly; AF3Score/PPIFlow semantics are not fabricated.
6. One canonical Design row is emitted per accepted sequence-designed complex.
7. Independent complex validation follows sequence design.
8. Browser-visible results show source backbone, sequence mutations, Caliby score provenance, validator provenance, and interface metrics.
9. Stub, managed Nextflow preview, ingestion, and real GPU acceptance pass.

## Protein Hunter acceptance contract for general binder integration

Protein Hunter may be exposed in `binder_design` only after all of the following pass:

1. Preserve one canonical candidate lineage per requested trial; cycle snapshots are lineage artifacts, not duplicate top-level Designs.
2. Preserve structure, full PAE, chain IDs, and binder/target roles together.
3. Compute BioModStack's approved **ipSAE** from authoritative aligned-error artifacts.
4. Do not select Boltz-2 binder candidates by iPTM as a substitute for ipSAE.
5. Separate generator-internal triage metrics from independent validation metrics.
6. Emit canonical generated-complex producer metadata and authoritative Design rows.
7. Preserve explicit false/zero launch values.
8. Browser-visible results expose Protein Hunter provenance, generation cycle lineage, validator provenance, and ipSAE.
9. Stub, managed preview, ingestion, and real GPU acceptance pass.

The current packaged Protein Hunter runtime sets `write_full_pae: False`; therefore fresh launches remain fail-closed.

## Migration sequence

### Phase 1 — stabilize product taxonomy

- Rename the `protein_modification_experimental` product label to **De Novo Design**.
- Remove the misplaced Protein Hunter mode/card/topic from that product.
- Keep legacy Protein Hunter launch surfaces hidden and fail-closed.
- Keep internal Protein Hunter workflow/module/runtime files available for development.

### Phase 2 — specify canonical binder contracts

- Inventory the current `binder_design` template and runtime honestly.
- Define target, binder, chain-role, lineage, validation, and result schemas.
- Define engine-neutral stage contracts for generation and fixed-backbone sequence design.
- Decide whether `binder_design` remains the canonical ID or receives a migration alias; do not create a second competing binder product.

### Phase 3 — integrate Caliby

- Add Caliby as a sequence-design choice after compatible binder backbones exist.
- Reuse the hardened Caliby runtime/preflight and producer-sidecar code.
- Add binder-specific constraints and independent complex validation.
- Run real GPU acceptance before exposing the choice.

### Phase 4 — integrate Protein Hunter

- Rebuild/patch runtime to preserve full PAE.
- Normalize candidate and cycle lineage.
- Compute ipSAE and make it authoritative for Boltz-2 binder interface selection.
- Add canonical result ingestion and browser review.
- Run real GPU acceptance before exposing the choice.

### Phase 5 — secondary uses

- Preserve the validated Caliby nanobody sequence-design path.
- Evaluate non-binder Caliby packing/scoring modes separately.
- Add Protein Hunter secondary modes only where their scientific contracts are explicit and fully tested.

## Non-negotiable gates

- No iPTM substitution for Boltz-2 ipSAE.
- No fresh launcher card for a runtime that lacks authoritative result ingestion.
- No mode placement based only on implementation convenience.
- No historical ID deletion that breaks old-job reads.
- No real GPU claim without a recorded real runtime result and browser-visible verification.
