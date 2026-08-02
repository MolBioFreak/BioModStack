# Experimental Protein CAD Workflow

This document describes the live experimental `Protein CAD` workflow now wired
into BMS. It is not presented as a production-ready design product. It is the
starting integration layer for future constraint-driven protein design work
using `La-Proteina` and `DISCO`.

## What Is Live Now

The current workflow family is exposed through:

- model registry entry:
  [platform/api/config/models/protein_cad_experimental.yaml](../platform/api/config/models/protein_cad_experimental.yaml)
- launcher template:
  [platform/api/config/templates/protein_cad_experimental.yaml](../platform/api/config/templates/protein_cad_experimental.yaml)
- standalone workflow:
  [workflows/protein_cad_experimental.nf](../workflows/protein_cad_experimental.nf)
- execution module:
  [modules/protein_cad_experimental.nf](../modules/protein_cad_experimental.nf)

The live workflow does three things end to end:

1. compiles BMS-facing inputs into backend-native request formats
2. runs `La-Proteina` or `DISCO` in documented upstream inference modes
3. normalizes outputs into stable BMS design IDs, manifests, and metadata

This is intentionally narrower than the long-term CAD goal. The current runtime
does not yet claim interactive geometric sketching, reward-guided steering from
drawn 3D constraints, or a full refinement loop for these models.

## Backend Scope

### La-Proteina

Current BMS integration scope:

- unconditional generation
- atomistic motif scaffolding

Current launch surface:

- documented unconditional presets:
  `ucond_tri`, `ucond_notri`, `ucond_notri_long`
- documented motif presets:
  `motif_idx_aa`, `motif_idx_tip`, `motif_uidx_aa`, `motif_uidx_tip`
- upstream motif task keys from `configs/generation/motif_dict.yaml`
- custom motif PDB + contig string injection through a BMS-generated config overlay

### DISCO

Current BMS integration scope:

- unconditional generation
- ligand-conditioned generation
- DNA-conditioned generation
- RNA-conditioned generation
- direct custom JSON passthrough

Current launch surface:

- experiment presets:
  `designable`, `diverse`
- effort presets:
  `fast`, `max`
- compiled masked-chain JSON generation from BMS lengths / ligand / NA inputs
- direct handoff to upstream Hydra inference for custom JSON users

Important rule:

- BMS promotes conditional DISCO jobs to `effort=max` when the user selects
  `fast`, because upstream explicitly recommends `max` for conditional runs

## Dependency Requirements

### La-Proteina runtime

Container spec:
- [apptainer/laproteina.def](../apptainer/laproteina.def)

Primary runtime requirements from upstream:

- Python `3.11`
- PyTorch `2.7.0` with CUDA `11.8`
- `graphein==1.7.7`
- `torch_geometric`, `torch_scatter`, `torch_sparse`, `torch_cluster`
- `hydra-core`, `lightning`, `dm-tree`, `biotite`, `biopython`, and the rest
  of the upstream environment surface

External assets:

- latent-diffusion checkpoints under a La-Proteina checkpoint directory
- matching autoencoder checkpoints
- optional `DATA_PATH` for motif/data helpers

Default BMS expectation:

- checkpoints mounted under `${BMS_WEIGHTS}/laproteina`

### DISCO runtime

Container spec:
- [apptainer/disco.def](../apptainer/disco.def)

Primary runtime requirements from upstream:

- Python `3.11`
- `uv` workspace install
- PyTorch with a CUDA backend compatible with the host GPUs
- `deepspeed`, `lightning`, `hydra-core`, `transformers`, `openfold`,
  `LigandMPNN`, and the workspace packages shipped by upstream

External assets:

- `DISCO.pt` checkpoint
- optional `CUTLASS_PATH` if DeepSpeed EvoformerAttention is enabled

Default BMS expectation:

- checkpoint mounted under `${BMS_WEIGHTS}/disco/DISCO.pt`

## BMS Integration Contract

The workflow is intentionally compiled through one stable internal contract:

- `pcad_backend`
- `pcad_task`
- `pcad_num_designs`
- `pcad_target_lengths`
- backend-specific overrides under `pcad_laproteina_*` and `pcad_disco_*`

This contract is translated in
[platform/api/services/nextflow.py](../platform/api/services/nextflow.py)
into the Nextflow param surface used by
[workflows/protein_cad_experimental.nf](../workflows/protein_cad_experimental.nf).

Normalized outputs are emitted as:

- `pdb_files/<design_id>.pdb`
- `pdb_files/confidence_<design_id>.json`
- `metadata/design_manifest.json`

The raw generator metadata is intentionally written as `generator_*.json`
before finalization so it does not collide with BMS ingester heuristics until
the workflow explicitly publishes final artifacts.

## Current System Requirements

For this workflow to behave like a real BMS feature, these integration points
must stay aligned:

1. `platform/api/config/models/*.yaml`
2. `platform/api/config/templates/*.yaml`
3. [platform/frontend/src/components/JobSubmission.tsx](../platform/frontend/src/components/JobSubmission.tsx)
4. [platform/api/services/nextflow.py](../platform/api/services/nextflow.py)
5. [main.nf](../main.nf)
6. [nextflow.config](../nextflow.config)
7. the workflow/module/scripts listed above

If any one of those changes without the others, the feature stops being a real
launcher-integrated workflow and becomes a drifted prototype.

## What Is Still Iterative

The current workflow is the correct base layer, not the final CAD product.
Still deferred:

- automatic refolding / validation loops for La-Proteina and DISCO outputs
- reward-guided design steering from a user-defined constraint schema
- direct 3D sketch / ghost-object / pseudo-ligand authoring in the launcher
- clustering, novelty, compactness, symmetry, and pocket-analysis review cards
- an editor that compiles drawn geometry into model-native constraints

## Recommended Next Iteration Order

1. add backend-specific validation / refolding stages without breaking current
   raw-generator artifact identity
2. add a formal `constraint schema` that can be emitted from future CAD UIs
3. expand launcher ergonomics for motif libraries, ligand sets, and nucleic
   acid presets
4. add results-side scoring and comparison views specific to non-binder design
5. prototype CAD-style spatial constraints against DISCO and La-Proteina in a
   separate experimental surface, then fold the proven pieces back here
