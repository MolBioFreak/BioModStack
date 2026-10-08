# Protein Hunter Experimental Workflow

This document describes the live experimental `Protein Hunter` integration in
BMS.

The intended role is a standalone experimental **generator family** for broad
protein-binder exploration. It is not folded into the nanobody-specific
generator/refinement loop, and it is not presented as a production-ready design
workflow.

## What Is Live

The current integration surface is:

- model registry entry:
  [platform/api/config/models/protein_hunter_experimental.yaml](../platform/api/config/models/protein_hunter_experimental.yaml)
- launcher template:
  [platform/api/config/templates/protein_hunter_experimental.yaml](../platform/api/config/templates/protein_hunter_experimental.yaml)
- standalone workflow:
  [workflows/protein_hunter_experimental.nf](../workflows/protein_hunter_experimental.nf)
- execution module:
  [modules/protein_hunter_experimental.nf](../modules/protein_hunter_experimental.nf)
- request compiler:
  [scripts/prep_protein_hunter_request.py](../scripts/prep_protein_hunter_request.py)
- runtime wrapper:
  [scripts/run_protein_hunter_inference.py](../scripts/run_protein_hunter_inference.py)
- container recipe:
  [apptainer/protein_hunter.def](../apptainer/protein_hunter.def)

The live workflow does three things end to end:

1. compile BMS-facing generator inputs into a normalized Protein Hunter request
2. run the selected Protein Hunter backend in its upstream-documented generator mode
3. normalize selected outputs into stable BMS PDB artifacts and metadata sidecars

## Upstream Contract We Follow

Primary upstream sources:

- Protein Hunter repo: https://github.com/yehlincho/Protein-Hunter
- Protein Hunter paper: https://www.biorxiv.org/content/10.1101/2025.10.10.681530v2.full.pdf

Important upstream facts that drive the BMS integration:

- upstream exposes two generator backends:
  - `Boltz`
  - `Chai`
- upstream generator tasks are broad rather than antibody-specific:
  - protein binders
  - unconditional monomers
  - ligand binders
  - nucleic-acid binders
  - seeded redesign via an initial binder sequence
- upstream setup is `conda`-centric, but BMS intentionally ports it to a `uv`
  runtime
- upstream supports optional AF3 + PyRosetta downstream validation, but AF3 is
  not required for the generator loop itself

## Runtime Decisions In BMS

Container spec:
- [apptainer/protein_hunter.def](../apptainer/protein_hunter.def)

Recommended image target:
- `/mnt/BioModStack/apptainer/protein_hunter.sif`

Recommended runtime roots for local BMS execution:

- `container_dir=/mnt/BioModStack/apptainer`
- `weights_root=/mnt/BioModStack/weights`
- `cache_root=/mnt/BioModStack/cache`

BMS intentionally diverges from upstream in four ways:

1. the runtime is built with `uv`, not `conda`
2. the runtime targets CUDA `12.8` so the same image is viable on both
   `Ampere` and `Blackwell` systems
3. AF3 validation is left disabled in the BMS launch surface; Protein Hunter is
   integrated here as an experimental generator family, not as an AF3 wrapper
4. the upstream `boltz_ph` package is installed **non-editably** inside the
   image so `numba` function caching works under Apptainer; editable install
   breaks the Boltz backend at import time

Current runtime stance:

- Python `3.10`
- `uv` environment bootstrap and install
- Torch from the `cu128` wheel index
- non-editable `boltz_ph` install for stable `numba` caching
- shared cache roots under the BMS cache tree
- mounted BMS Boltz weights under `/weights/boltz`
- LigandMPNN model parameters staged inside the image
- no live AF3 dependency in the launcher contract

## What Is In Scope

### Boltz backend

Current BMS scope:

- protein binder generation
- unconditional generation
- ligand binder generation
- nucleic-acid binder generation
- optional seeded redesign through an initial binder sequence
- optional target contact conditioning
- optional target template guidance

### Chai backend

Current BMS scope:

- protein binder generation
- unconditional generation
- ligand binder generation
- optional seeded redesign through an initial binder sequence
- optional target-PDB guided setup

## What Is Intentionally Out Of Scope

The first BMS cut does **not** claim:

- AF3-backed validation inside the live launcher
- a PyRosetta validation or repair stage inside the BMS workflow graph
- nanobody-specific framework / CDR semantics
- direct handoff into the antibody refinement loop
- a review/resume gate comparable to the nanobody generator workflows

Those are deliberate scope limits for this experimental family.

## BMS Integration Contract

The workflow compiles one stable internal contract:

- `ph_backend`
- `ph_task`
- `ph_num_designs`
- `ph_num_cycles`
- `ph_min_protein_length`
- `ph_max_protein_length`
- `ph_percent_x`
- `ph_seed_binder_sequence`
- `ph_target_protein_sequences`
- `ph_target_pdb`
- `ph_target_pdb_chain`
- `ph_target_template_path`
- `ph_target_template_chain_id`
- `ph_ligand_smiles`
- `ph_ligand_ccd`
- `ph_nucleic_sequence`
- `ph_nucleic_type`
- `ph_contact_residues`
- `ph_cyclic`
- `ph_alanine_bias`
- `ph_temperature`
- `ph_high_iptm_threshold`
- `ph_high_plddt_threshold`
- `ph_msa_mode`
- `ph_boltz_model_version`
- `ph_boltz_model_path`
- `ph_boltz_ccd_path`
- `ph_chai_hysteresis_mode`
- `ph_chai_num_recycles`
- `ph_chai_num_diff_steps`
- `ph_chai_repredict`

This contract is translated by
[platform/api/services/nextflow.py](../platform/api/services/nextflow.py)
into the Nextflow param surface consumed by
[workflows/protein_hunter_experimental.nf](../workflows/protein_hunter_experimental.nf).

Normalized outputs are published as:

- `pdb_files/<design_id>.pdb`
- `pdb_files/confidence_<design_id>.json`
- `metadata/design_manifest.json`

## Output Normalization Rules

The BMS wrapper intentionally prefers the **top cohort** emitted by Protein
Hunter rather than scraping every intermediate cycle artifact.

Normalization rules:

- prefer `summary_high_iptm.csv` plus `high_iptm_*` structures when present
- fall back to per-run best structures only when no high-ipTM cohort exists
- emit one stable BMS design ID per normalized structure
- preserve upstream metrics such as:
  - `iptm`
  - `plddt`
  - `iplddt` or `ipae`
  - `alanine_count`
  - designed sequence
  - upstream backend identity

This is the correct integration shape for an experimental generator because it
gives BMS a cohort of candidate structures without pretending the intermediate
trajectory artifacts are already curated review outputs.

## Verification Checklist

Minimum validation after changing the integration:

1. `python3 -m py_compile` on the touched backend scripts and routers
2. `npm run build` in `platform/frontend`
3. `nextflow run main.nf -stub-run -profile apptainer,protein_hunter_experimental --container_dir /mnt/BioModStack/apptainer --weights_root /mnt/BioModStack/weights --cache_root /mnt/BioModStack/cache --rfd_mode protein_hunter_experimental`
4. `apptainer exec --bind /tmp/phcache:/cache /mnt/BioModStack/apptainer/protein_hunter.sif python /opt/Protein-Hunter/boltz_ph/design.py --help`
5. `apptainer exec --bind /tmp/phcache:/cache /mnt/BioModStack/apptainer/protein_hunter.sif python /opt/Protein-Hunter/chai_ph/design.py --help`
6. one real small Boltz-backed run with local weights, for example:

```bash
nextflow run main.nf -ansi-log false -profile apptainer,protein_hunter_experimental \
  --container_dir /mnt/BioModStack/apptainer \
  --weights_root /mnt/BioModStack/weights \
  --cache_root /mnt/BioModStack/cache \
  --rfd_mode protein_hunter_experimental \
  --name protein_hunter_real_smoke \
  --job_id protein-hunter-real-smoke \
  --out_dir /tmp/bms_protein_hunter_real_smoke \
  --ph_backend boltz \
  --ph_task protein_binder \
  --ph_target_protein_sequences ACDEFGHIKLMNPQRSTVWY \
  --ph_num_designs 1 \
  --ph_num_cycles 1 \
  --ph_min_protein_length 20 \
  --ph_max_protein_length 24 \
  --ph_percent_x 50 \
  --ph_msa_mode single
```

## Maintenance Rule

If the Protein Hunter integration changes, update all of:

1. `apptainer/protein_hunter.def`
2. the config/template/workflow/module/scripts listed above
3. `platform/api/services/nextflow.py`
4. `main.nf`
5. `nextflow.config`
6. this document
