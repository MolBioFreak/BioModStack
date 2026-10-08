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
  staged antibody de novo and refinement pipeline
- `antibody_design`
  antibody toolkit modes
- `bindcraft`
  minibinder / peptide binder design
- `protein_local_redesign`
  constrained remodel + redesign workflow

### Generic prediction / design / docking

- `rfdiffusion`
- `boltz2`
- `protenix`
- `af2`
- `rf3`
- `fampnn`
- `proteinmpnn`
- `diffdock`
- `unidock`
- `docking`
- `boltzgen`
- `oligo_design`
- `mutagenesis`

### Sequencing

- `nanopore`

## Live Model Inventory By Function

### Backbone and generative design

- RFantibody
- RFdiffusion
- BindCraft
- BoltzGen
- RFDpoly / Oligo Designer

### Sequence design and redesign

- FAMPNN
- AntiFold
- ProteinMPNN
- FrustraMPNN
- IgGM

### Prediction and validation

- Boltz-2
- Protenix
- AlphaFold2
- RF3
- ImmuneBuilder-facing antibody structure prediction surface

### Post-processing and scoring

- ThermoMPNN
- AntiBERTy
- OpenMM
- ANARCI / ANARCII

### Docking

- DiffDock
- Uni-Dock

## Integration Notes That Matter

### Antibody validation backends

The current antibody de novo/refinement pipeline documents:

- Boltz-2
- Protenix

as the live validator backends.

RF3 exists in the codebase as a generic predictor, but should not be documented
as a production antibody validator surface unless the workflow and control-plane
integration explicitly say so.

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
