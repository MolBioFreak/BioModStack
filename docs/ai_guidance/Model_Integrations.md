# BioModStack Model Integrations

This file is the live registry-oriented documentation for model and workflow
integrations in BMS. It replaces the older plan-heavy version that pointed at
missing docs.

## How To Read This File

- “Live” means the model or workflow is wired into the current codebase and
  surfaced through `main.nf`, the model registry, or the frontend launcher.
- “Internal” means child/orchestrator entries that exist for execution but are
  not intended as standalone user launch targets.
- dated spec/plan docs elsewhere under `docs/` are implementation history, not
  the authoritative integration list.

## Integration Layers

Every model integration usually touches some subset of:

- `platform/api/config/models/*.yaml`
- `platform/api/services/nextflow.py`
- [main.nf](../../main.nf)
- one or more `workflows/*.nf` or `modules/*.nf` files
- frontend submission surfaces under `platform/frontend/src/components`

## Live User-Facing Workflow Families

### Antibody and binder workflows

- `antibody_denovo`
  primary de novo binder workflow for nanobody/VHH campaigns. Launch RFantibody, BoltzGen nanobody mode, or seeded PPIFlow generation, then reopen selected outputs in Antibody Refinement for loop-focused sequence redesign, PPIFlow reattempt/maturation, structure validation, and review.
- `antibody_design`
  antibody toolkit / shell-driven modes for template-guided setup, nanobody/VHH refinement, ImmuneBuilder prediction, inverse-folding redesign, and resume/review flows.
- `bindcraft`
  generic de novo minibinder / peptide binder generation against hotspot-conditioned targets.
- `protein_local_redesign`
  constrained local remodel + redesign workflow for existing binders or complexes.
- `protein_cad_experimental`
  experimental La-Proteina / DISCO non-binder protein CAD family.
- `caliby_experimental`
  experimental structure-conditioned sequence-design surface that also appears as an optional redesign backend in nanobody refinement.
- `protein_hunter_experimental`
  early experimental broad protein-hunting / exploration surface.
- `oligo_design`
  oligomer / nucleic-acid-aware generation surface (Oligo Designer / RFDpoly).

### Prediction, redesign, docking, and sequencing surfaces

- `boltz2`
- `protenix`
- `af2`
- `rf3`
- `fampnn`
- `proteinmpnn`
- `ligandmpnn`
- `diffdock`
- `unidock`
- `docking`
- `boltzgen`
- `mutagenesis`
- `nanopore`

## Live Model Inventory By Function

### De novo binder generation and nanobody refinement loop

- RFantibody
- BoltzGen nanobody mode
- seeded PPIFlow generation / backbone-refine / maturation stages
- FAMPNN
- AntiFold
- ProteinMPNN
- Caliby experimental redesign option
- Boltz-2
- Protenix
- OpenMM
- ANARCI / ANARCII

### Generic binder generation and redesign

- BindCraft
- RFdiffusion
- BoltzGen
- Protein Local Redesign
- Mutagenesis

### Structure prediction

- Boltz-2
- Protenix
- AlphaFold2
- RF3
- ImmuneBuilder-facing antibody structure prediction surface

### Sequence design and redesign

- FAMPNN
- ProteinMPNN
- LigandMPNN
- AntiFold
- FrustraMPNN
- Caliby

### Docking

- DiffDock
- Uni-Dock
- generic docking wrapper

### Experimental protein CAD

- La-Proteina
- DISCO
- Protein Hunter
- Caliby Experimental

### Sequencing and molecular biology operations

- Oxford Nanopore launch / review
- Dorado-aligned nanopore outputs under the nanopore family
- Oligo Designer / RFDpoly

## Integration Notes That Matter

### Antibody validation backends

The current antibody de novo/refinement pipeline documents:

- Boltz-2
- Protenix

as the live validator backends.

RF3 exists in the codebase as a generic predictor, but should not be documented
as a production antibody validator surface unless the workflow and control-plane
integration explicitly say so.

### Caliby is both standalone and nanobody-facing

`Caliby` is wired in two ways:

- as `caliby_experimental`, a standalone experimental workflow family
- as an experimental sequence-design option in the main nanobody workflow

Document it as an experimental sequence-design backend, not as a backbone
generator or validator.

### Protein Hunter is standalone experimental generation only

`Protein Hunter` is wired as `protein_hunter_experimental`.

Document it as:

- an experimental generator family
- broad binder / unconditional exploration
- Boltz or Chai backend driven

Do not document it as an antibody-native workflow or as part of the nanobody
refinement loop.

### OpenMM is live

OpenMM should be documented as an active integrated post-processing/refinement
component, not as a future plan.

### Internal child models

These registry entries are internal orchestration surfaces:

- `antibody_child`
- `rfantibody_child`
- `fampnn_child`
- `boltzgen_child`

Document them as internal unless the UI deliberately exposes them.

## Canonical Docs For Operators

- [Structure Design and Refinement](../Structure_Design_and_Refinement.md)
- [API README](../../platform/api/README.md)
- [Frontend README](../../platform/frontend/README.md)

## Maintenance Rule

Whenever a new model is added or a validator/entrypoint contract changes,
update:

1. the model YAML
2. the relevant workflow/nextflow routing
3. this file
4. any operator doc affected by the change
