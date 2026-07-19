# Structure Design and Refinement

This document describes the live structure-design surface exposed by BioModStack.
It is capability-first: it focuses on workflows, model families, and launcher
surfaces that are actually present in the repo and model registry today.

## Main workflow families

### Antibody de novo and refinement

Primary workflow:

- [workflows/antibody_child.nf](../workflows/antibody_child.nf)

Entry modes include:

- `antibody_refinement_pipeline`
- `antibody_refinement_pipeline`

Current pipeline shape:

1. RFantibody backbone generation
2. sequence design with FAMPNN, AntiFold, or ProteinMPNN
3. structure validation with Boltz-2, plus workflow-level Protenix validation when explicitly selected by the antibody/structure workflows
4. downstream scoring and review
5. optional refinement/maturation branches such as PPIFlow and OpenMM

Important notes:

- this is the main staged antibody/binder refinement surface in BMS
- refinement is designed to consume upstream artifacts rather than always start
  from RFantibody
- Boltz-2 is the registry-backed validator exposed for this path; Protenix remains a workflow-level validator module/param path, not a standalone model-registry YAML in the current tracked tree
- RF3 exists as a generic structure predictor but should not be described here
  as the main antibody-validator backend

### Antibody toolkit

Primary workflow:

- [workflows/antibody_design.nf](../workflows/antibody_design.nf)

Live modes include:

- structure prediction
- inverse folding
- stability prediction
- de novo generation

This is a broader antibody-engineering surface than the staged de novo/refine
pipeline above.

### Generic structure prediction and validation

BioModStack exposes standalone prediction/validation surfaces through
[main.nf](../main.nf), the model registry under
[platform/api/config/models](../platform/api/config/models), and the structure
prediction modules.

Current registry-backed predictor families:

- [Boltz-2](../platform/api/config/models/boltz2.yaml)
- [AlphaFold2](../platform/api/config/models/af2.yaml)
- [RF3](../platform/api/config/models/rf3.yaml)
- [Fold-CP Experimental](../platform/api/config/models/boltz_cp_experimental.yaml)

Workflow-level predictor/validator modules also include [Protenix](../modules/protenix.nf),
but there is no tracked `platform/api/config/models/protenix.yaml` in the current
registry. Do not describe Protenix as a standalone registry card until that YAML
or an equivalent API registry entry exists again.

Important distinction:

- Boltz-2 / AF2 / RF3 are the normal registry-backed structure-prediction family
- Fold-CP Experimental is a specialized multi-GPU Boltz-2 context-parallel path,
  not a generic replacement for the standard launcher/runtime

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

Registry model:

- [platform/api/config/models/protein_cad_experimental.yaml](../platform/api/config/models/protein_cad_experimental.yaml)

Purpose:

- compile BMS launch inputs into native La-Proteina or DISCO requests
- run experimental non-binder de novo generation backends
- normalize outputs into BMS design IDs and manifests

Current backend coverage from the live model config:

- La-Proteina:
  unconditional generation and motif scaffolding
- DISCO:
  unconditional, ligand-conditioned, DNA-conditioned, RNA-conditioned, and
  custom-JSON launches

See [Experimental Protein CAD Workflow](Experimental_Protein_CAD_Workflow.md)
for the dependency matrix and runtime notes.

### Protein Hunter Experimental

Registry model:

- [platform/api/config/models/protein_hunter_experimental.yaml](../platform/api/config/models/protein_hunter_experimental.yaml)

Purpose:

- experimental iterative generator family using Protein Hunter
- backend selection between Boltz and Chai
- task families for protein binders, unconditional generation,
  ligand-conditioned binders, and nucleic-acid binders

This is intentionally documented as experimental launcher surface area, not a
matured canonical production pipeline.

### Caliby Experimental

Registry model:

- [platform/api/config/models/caliby_experimental.yaml](../platform/api/config/models/caliby_experimental.yaml)

Purpose:

- structure-conditioned sequence design
- ensemble-conditioned design
- sidechain packing
- optional AF2 self-consistency evaluation of designed outputs

See [Caliby Experimental Workflow](Caliby_Experimental_Workflow.md) for the
current integration state and gaps.

### Fold-CP Experimental

Registry model:

- [platform/api/config/models/boltz_cp_experimental.yaml](../platform/api/config/models/boltz_cp_experimental.yaml)

Workflow:

- [workflows/boltz_cp_experimental.nf](../workflows/boltz_cp_experimental.nf)

Purpose:

- experimental single-example Boltz-2 inference using DTensor context
  parallelism across multiple GPUs
- accepts prepared Boltz YAML / FASTA inputs or preprocessed Boltz directories
- launches the checked-out Fold-CP fork through `torch.distributed`

Current launcher/runtime constraints expressed in the workflow and model config:

- `gpu_ids` is an explicit host GPU selection string
- `size_cp` must be a perfect square
- `input_format` is `config_files` or `preprocessed`
- the surface is experimental and optimized for one large inference rather than
  broad batch semantics

See the active Fold-CP plan in [plans/README.md](plans/README.md) for current
runtime strategy notes.

### RFdiffusion and backbone generation

Generic backbone-generation support exists for RFdiffusion-style workflows and
related child/orchestrator paths.

Primary registry entry:

- [platform/api/config/models/rfdiffusion.yaml](../platform/api/config/models/rfdiffusion.yaml)

### retired binder workflow

Primary workflow:

- [workflows/retired binder workflow_design.nf](../workflows/retired binder workflow_design.nf)

Purpose:

- minibinder and peptide-binder design against a target structure

### BoltzGen

Primary registry entry:

- [platform/api/config/models/boltzgen.yaml](../platform/api/config/models/boltzgen.yaml)

Surface includes:

- ligand-aware generation
- nucleotide-aware generation
- nanobody/VHH campaigns
- scaffolded or target-conditioned all-atom generation

### Oligo Designer / RFDpoly

Primary workflow:

- [workflows/oligo_design.nf](../workflows/oligo_design.nf)

Primary registry entry:

- [platform/api/config/models/oligo_design.yaml](../platform/api/config/models/oligo_design.yaml)

Purpose:

- multi-polymer design across DNA, RNA, protein, and mixed assemblies

### Docking

Docking support includes:

- [DiffDock](../platform/api/config/models/diffdock.yaml)
- [Uni-Dock](../platform/api/config/models/unidock.yaml)
- [shared docking surface](../platform/api/config/models/docking.yaml)

## Model groups

### Backbone or generative design

- RFantibody
- RFdiffusion
- retired binder workflow
- BoltzGen
- Oligo Designer / RFDpoly
- La-Proteina
- DISCO
- Protein Hunter Experimental
- Caliby Experimental

### Sequence design and redesign

- FAMPNN
- AntiFold
- ProteinMPNN
- FrustraMPNN
- IgGM
- Caliby Experimental

### Prediction and validation

- Boltz-2
- Protenix
- AlphaFold2
- RF3
- Fold-CP Experimental

### Post-processing and scoring

- ThermoMPNN
- AntiBERTy
- OpenMM
- ANARCI / ANARCII

### Docking and ligand-aware utilities

- DiffDock
- Uni-Dock

## Internal-only orchestrator models

Some registry entries are internal child jobs rather than user-facing launch
surfaces. Examples include:

- `antibody_child`
- `rfantibody_child`
- `fampnn_child`
- `boltzgen_child`

The docs should describe them as implementation details, not as standalone user
products.

## Output and review model

These workflows are not just file dumps. The system persists:

- job-level stage state
- design-level lineage and artifact identity
- stage review metadata
- optional persisted analyses and aligned-error artifacts

See [Results and Analysis](Results_and_Analysis.md) for the data/review side.

## Reference inventory

For the broader registry-level view of model integrations, see
[docs/ai_guidance/Model_Integrations.md](ai_guidance/Model_Integrations.md).
