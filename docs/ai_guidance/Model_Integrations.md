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
- `protein_cad_experimental`
  experimental La-Proteina / DISCO non-binder design workflow
- `caliby_experimental`
  experimental structure-conditioned sequence design workflow
- `protein_hunter_experimental`
  experimental broad binder and unconditional generator workflow

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
- `protein_cad_experimental`
- `caliby_experimental`
- `protein_hunter_experimental`

### Sequencing

- `nanopore`

## Live Model Inventory By Function

### Backbone and generative design

- RFantibody
- RFdiffusion
- BindCraft
- BoltzGen
- Protein Hunter
- RFDpoly / Oligo Designer
- La-Proteina
- DISCO

### Sequence design and redesign

- FAMPNN
- AntiFold
- ProteinMPNN
- Caliby
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
