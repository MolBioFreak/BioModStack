# Caliby Experimental Workflow

This document describes the live experimental `Caliby` integration in BMS and
the concurrent experimental `Caliby` sequence-design lane inside the de novo
nanobody workflow.

## What Is Live

The current integration has two surfaces:

- standalone experimental workflow:
  [platform/api/config/models/caliby_experimental.yaml](../platform/api/config/models/caliby_experimental.yaml)
- experimental launcher template:
  [platform/api/config/templates/caliby_experimental.yaml](../platform/api/config/templates/caliby_experimental.yaml)
- standalone workflow:
  [workflows/caliby_experimental.nf](../workflows/caliby_experimental.nf)
- standalone module:
  [modules/caliby_experimental.nf](../modules/caliby_experimental.nf)
- nanobody sequence-design module:
  [modules/caliby.nf](../modules/caliby.nf)
- nanobody refinement workflow:
  [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf)

The live standalone scope is intentionally narrow:

1. single-structure sequence design
2. ensemble-conditioned sequence design
3. sidechain packing

The live nanobody scope is more feature-complete than the first experimental
drop, but it is still scoped to sequence design rather than full Caliby-native
iteration:

1. `Caliby` appears as an experimental sequence designer in the de novo /
   refinement workflow
2. it uses the existing antibody CDR/framework constraint semantics already
   defined in BMS
3. it now compiles those antibody semantics directly into a Caliby-native
   positional constraint CSV rather than going through a lossy FAMPNN bridge
4. it can run optional AF2 self-consistency evaluation and optional
   pre-validation filtering on Potts energy / self-consistency metrics
5. it publishes a first-class `post_caliby` review gate so interactive review,
   shortlist selection, and resume semantics do not masquerade as `FAMPNN`

## Upstream Contract We Follow

Primary upstream sources:

- Caliby repo: https://github.com/ProteinDesignLab/caliby
- Python API guide: https://github.com/ProteinDesignLab/caliby/blob/main/python_api.md
- preprint DOI: https://doi.org/10.1101/2025.09.30.679633

Important upstream facts that drive the BMS integration:

- upstream prefers `uv`
- Python requirement is `>=3.12`
- upstream exposes a direct Python API:
  `load_model`, `clean_pdbs`, `sample`, `ensemble_sample`, `score`,
  `score_ensemble`, `sidechain_pack`, `make_constraints`,
  `make_ensemble_constraints`
- `CalibyModel.sample()` already returns designed structures via `out_pdb`
- `soluble_caliby_v1` is explicitly the interface-trained sequence model

## Runtime Decisions In BMS

Container spec:
- [apptainer/caliby.def](../apptainer/caliby.def)

Recommended image target:
- `/mnt/BioModStack/apptainer/caliby.sif`

BMS diverges from the bare upstream `build_apptainer.sh` in one important way:

- we target a CUDA `12.8` runtime and install Torch from the `cu128` wheel
  index so the same runtime is viable on both `Ampere` and `Blackwell`
  systems

Intentional runtime choices:

- Python `3.12`
- `uv` environment bootstrap and install
- `torch>=2.7,<2.9` from the `cu128` index
- `MODEL_PARAMS_DIR=/weights/caliby/model_params`
- Hugging Face / Triton / general cache roots under the shared BMS cache tree

This should be treated as the canonical BMS runtime stance for Caliby unless
upstream publishes a stronger Blackwell-first recommendation later.

## Runtime Build / Deployment

Canonical build command:

```bash
apptainer build /mnt/BioModStack/apptainer/caliby.sif apptainer/caliby.def
```

The image intentionally uses:

- `uv` for environment bootstrap and package installation
- Python `3.12`
- Torch wheels from the `cu128` index
- CUDA `12.8` as the shared runtime floor for both `Ampere` and `Blackwell`

Expected shared runtime mounts / paths:

- image: `/mnt/BioModStack/apptainer/caliby.sif`
- model cache: `/weights/caliby/model_params`
- Hugging Face cache: `/cache/huggingface`
- Triton cache: `/cache/triton`

## Weight / Cache Expectations

Caliby uses upstream model names such as:

- `soluble_caliby_v1`
- `soluble_caliby`
- `caliby`
- `caliby_packer_010`

The BMS runtime expects upstream model parameters to resolve through the
standard Caliby / Hugging Face download path into:

- `/weights/caliby/model_params`

If a deployment needs fully offline execution, pre-stage those weights into the
shared model-parameter directory before launch.

## Standalone BMS Contract

The standalone experimental workflow compiles one stable internal contract:

- `caliby_task`
- `caliby_input_pdb_dir`
- `caliby_conformer_dir`
- `caliby_pdb_name_list`
- `caliby_pos_constraint_csv`
- `caliby_model_name`
- `caliby_packer_model_name`
- `caliby_num_seqs_per_pdb`
- `caliby_batch_size`
- `caliby_num_workers`
- `caliby_clean_num_workers`
- `caliby_temperature`
- `caliby_omit_aas`
- `caliby_run_self_consistency_eval`
- `caliby_self_consistency_*`
- `caliby_sampling_overrides_json`

That contract is translated by
[platform/api/services/nextflow.py](../platform/api/services/nextflow.py)
into the Nextflow param surface consumed by
[workflows/caliby_experimental.nf](../workflows/caliby_experimental.nf).

Normalized outputs are published as:

- `pdb_files/<design_id>.pdb`
- `pdb_files/confidence_<design_id>.json`
- `metadata/design_manifest.json`

## Nanobody Workflow Contract

The nanobody workflow now exposes `Caliby` as an experimental sequence design
option. It does **not** introduce a second antibody constraint system.

Instead it reuses the existing antibody design semantics already implemented in
[scripts/prep_antibody_constraints.py](../scripts/prep_antibody_constraints.py),
then compiles those semantics directly into Caliby’s positional constraint
schema through
[scripts/prep_caliby_antibody_constraints.py](../scripts/prep_caliby_antibody_constraints.py).

That means the following existing workflow concepts still control Caliby:

- design mode (`cdr_only`, `cdr_selective`, `framework_allowed`, `full_design`)
- selected CDR loop scope
- target-chain locking
- framework locking
- manual fixed-position overrides
- expert Caliby-only positional controls such as `fixed_pos_override_seq`,
  `pos_restrict_aatype`, and `symmetry_pos`

This is the correct integration shape because it preserves one antibody
constraint authority inside BMS while allowing multiple sequence-design
backends to consume it.

## Review / Resume Semantics

Interactive Caliby runs publish a real `post_caliby` review gate.

This matters because:

- results review should show `Caliby`, not `FAMPNN`
- paused review continuation should resume from sequence-designed inputs
- downstream PPIFlow / validation stages should receive a true
  `sequence_designed_complex` identity

The relevant control-plane pieces are:

- [platform/api/services/stage_review.py](../platform/api/services/stage_review.py)
- [platform/api/routers/jobs.py](../platform/api/routers/jobs.py)
- [platform/api/antibody_pipeline_contract.py](../platform/api/antibody_pipeline_contract.py)

## Nanobody Parity Features

The nanobody `Caliby` lane now has the following parity / exceedance features
relative to the older thin experimental branch:

- direct Caliby-native antibody constraint compilation
- optional AF2 self-consistency evaluation inside the nanobody workflow
- optional pre-validation pruning on:
  - Potts energy
  - self-consistency pLDDT
  - self-consistency RMSD
- review/resume awareness of both raw `collected/caliby_raw` and filtered
  `collected/caliby` candidate sets
- Results Viewer exposure of:
  - model name
  - Potts energy
  - self-consistency metrics
- stage-setting persistence for Caliby-specific filter and expert constraint
  options

## Verification Checklist

Minimum validation after changing the integration:

1. `python3 -m py_compile` on the touched backend scripts and routers
2. `pytest -q platform/api/tests/test_review_payload_and_fampnn_ingest.py platform/api/tests/test_resume_identity.py`
3. `npm run build` in `platform/frontend`
4. `nextflow run main.nf -stub-run -profile docker --rfd_mode caliby_experimental`
5. `nextflow run main.nf -stub-run -profile docker --rfd_mode template_antibody_denovo --seq_design_caliby true`

The nanobody path should verify both:

- direct `Caliby Experimental` standalone launch
- `Caliby` as a sequence-design backend inside the shared refinement workflow

## Current Scope Limits

The current BMS integration does **not** claim all of upstream Caliby yet.

Still deferred:

- standalone scoring-only launch surfaces
- Protpardelle ensemble generation as a first-class BMS stage
- Caliby-specific analytics panes beyond the current review/results parity
- a direct `Caliby redesign` iteration action in the Results Viewer
- orchestrated child-job fanout for very large Caliby nanobody batches

## Maintenance Rule

If the Caliby integration changes, update all of:

1. `apptainer/caliby.def`
2. the standalone config/template/workflow/module
3. the nanobody workflow path
4. the review/resume identity surfaces
5. this document
