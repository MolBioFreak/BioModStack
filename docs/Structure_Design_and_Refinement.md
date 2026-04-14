# Structure Design and Refinement

This document describes the live structure-design surface exposed by BMS. It is
capability-first: it lists what the system actually runs today, not every dated
idea note in `docs/`.

## Main Workflow Families

### Antibody de novo and refinement

Primary workflow:
- [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf)

Entry modes:
- `antibody_denovo_pipeline`
- `antibody_refinement_pipeline`

Current pipeline shape:

1. RFantibody backbone generation
2. sequence design with FAMPNN, AntiFold, or ProteinMPNN
3. structure validation with Boltz-2 or Protenix
4. downstream scoring and review
5. optional refinement/maturation branches such as PPIFlow and OpenMM

Important notes:

- this is the main staged antibody/binder refinement surface in BMS
- the refinement mode is designed to accept selected upstream artifacts rather
  than always starting from RFantibody
- Boltz-2 and Protenix are the live validator backends for this path
- RF3 exists as a generic structure predictor but is not documented here as an
  antibody validator backend

### Antibody toolkit

Primary workflow:
- [workflows/antibody_design.nf](../workflows/antibody_design.nf)

Live modes:

- structure prediction
- inverse folding
- stability prediction
- de novo generation

This is a broader antibody engineering surface than the staged de novo/refine
pipeline above.

### Generic structure prediction and validation

The codebase exposes standalone prediction and validation surfaces for:

- Boltz-2
- Protenix
- AlphaFold2
- RF3

These are wired through [main.nf](../main.nf), the model registry under
[platform/api/config/models](../platform/api/config/models), and the structure
prediction modules.

### Protein local redesign

Primary workflow:
- [workflows/protein_local_redesign.nf](../workflows/protein_local_redesign.nf)

Purpose:

- remodel a limited window of an existing structure
- merge the edited region back into the original context
- redesign sequence with FAMPNN or ProteinMPNN
- optionally validate the resulting complex

### Experimental protein CAD

Primary workflow:
- [workflows/protein_cad_experimental.nf](../workflows/protein_cad_experimental.nf)

Purpose:

- compile BMS launch inputs into native La-Proteina or DISCO requests
- run experimental non-binder de novo generation backends
- normalize outputs into BMS design IDs and manifests

Current backend coverage:

- La-Proteina:
  unconditional generation and motif scaffolding
- DISCO:
  unconditional, ligand-conditioned, DNA-conditioned, RNA-conditioned, and
  custom JSON launches

See [Experimental Protein CAD Workflow](Experimental_Protein_CAD_Workflow.md)
for the dependency matrix, current limits, and iteration plan.

### RFdiffusion / backbone generation

Generic backbone-generation support exists for RFdiffusion-style workflows and
related child/orchestrator paths.

### BindCraft

Primary workflow:
- [workflows/bindcraft_design.nf](../workflows/bindcraft_design.nf)

Purpose:
- minibinder and peptide-binder design against a target structure

### BoltzGen

BoltzGen is exposed for generative binder/scaffold campaigns including:

- ligand-aware generation
- nucleotide-aware generation
- nanobody/VHH campaigns
- scaffolded or target-conditioned all-atom generation

### Oligo Designer / RFDpoly

Primary workflow:
- [workflows/oligo_design.nf](../workflows/oligo_design.nf)

Purpose:
- multi-polymer design across DNA, RNA, protein, and mixed assemblies

### Docking

Docking support includes:

- DiffDock
- Uni-Dock
- combined comparison/consensus docking surfaces

## Model Groups

### Backbone or generative design

- RFantibody
- RFdiffusion
- BindCraft
- BoltzGen
- RFDpoly / Oligo Designer
- La-Proteina
- DISCO

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

### Post-processing and scoring

- ThermoMPNN
- AntiBERTy
- OpenMM
- ANARCI / ANARCII

### Docking and ligand-aware utilities

- DiffDock
- Uni-Dock

## Internal-Only Orchestrator Models

Some registry entries are internal child jobs rather than user-facing launch
targets. Examples include:

- `antibody_child`
- `rfantibody_child`
- `fampnn_child`
- `boltzgen_child`

The docs should describe them as implementation details, not as standalone user
products.

## Output And Review Model

The structure workflows are not just file dumps. The system persists:

- job-level stage state
- design-level lineage and artifact identity
- stage review metadata
- optional persisted analyses and aligned-error artifacts

See [Results and Analysis](Results_and_Analysis.md) for the data/review side.

## Reference Inventory

For the live registry-level view of model integrations, see
[docs/ai_guidance/Model_Integrations.md](ai_guidance/Model_Integrations.md).
