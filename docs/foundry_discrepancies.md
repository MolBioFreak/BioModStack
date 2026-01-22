# Foundry (RFD3/RF3) discrepancies vs upstream docs

Date: 2026-01-22
Scope: This compares our Foundry integration (Nextflow + scripts) against
the official RFdiffusion3 docs (foundry 0.1.7) and the Foundry README.

## Discrepancies

1) RFD3 CLI entrypoint
- Docs: run jobs with a command that starts with `rfd3 design ...`.
- Ours: `modules/rfd3.nf` invokes `rfd3 inputs=... out_dir=...` without the
  `design` subcommand.
- Impact: if the CLI expects the subcommand, this will fail at runtime.

2) Design-count plumbing
- Docs: total designs = `n_batches * diffusion_batch_size` (default 1 * 8).
- Ours: `rfd3_batches_per_design` maps to `n_batches`, but `rfd_num_designs`
  is not mapped to `diffusion_batch_size` or `n_batches`.
- Impact: user-specified design counts are ignored for RFD3 (still 8 per batch).

3) Binder settings recommended by docs are not applied
- Docs: binder design requires `input`, `contig`, `infer_ori_strategy`,
  `select_hotspots`; recommended `is_non_loopy: true`.
- Docs (PPI tutorial): recommended CLI overrides
  `inference_sampler.step_scale=3` and `inference_sampler.gamma_0=0.2`.
- Ours: `prep_rfd3_input.py` sets `infer_ori_strategy` but never sets
  `is_non_loopy`; `modules/rfd3.nf` does not apply the recommended overrides.

4) Hotspot atom specificity is reduced
- Docs: hotspots include residue-specific atom lists (e.g., `E64: CD2,CZ`).
- Ours: `prep_rfd3_input.py` parses `[A56,A115]`-style inputs and converts to
  CA-only hotspots (`{"A56": "CA"}`), losing atom specificity.

5) InputSpecification coverage is incomplete
- Docs: Input specs can include `length`, `select_unfixed_sequence`, `ligand`,
  `select_fixed_atoms`, and more.
- Ours: `prep_rfd3_input.py` only emits `dialect`, `contig`, optional `input`,
  `select_hotspots`, and `infer_ori_strategy`.
- Impact: users cannot express several documented constraints without code changes.

6) RFD3 CLI options not surfaced
- Docs: `skip_existing`, `global_prefix`, `prevalidate_inputs`, etc. are
  supported via CLI args.
- Ours: these are not exposed as first-class Nextflow params. They can only
  be passed via `rfd3_extra_config` (string), which is easy to miss.

7) Checkpoint discovery is inconsistent with Foundry README
- Docs: Foundry searches `~/.foundry/checkpoints` plus
  `$FOUNDRY_CHECKPOINT_DIRS`.
- Ours:
  - `nextflow.config` does not bind `~/.foundry` or set `FOUNDRY_CHECKPOINT_DIRS`
    for the Foundry container.
  - RF3 uses hard-coded `ckpt_path` values:
    `/foundry/checkpoints/...` in `scripts/run_rf3.py` and
    `/root/.foundry/checkpoints/...` in `modules/structure_prediction.nf`.
- Impact: checkpoint discovery may fail or be inconsistent across paths.

8) Output naming vs metadata parsing (potential mismatch)
- Docs: output files are named
  `<input_file>_<settings_group>_<batch>_model_n.*`.
- Ours: `scripts/metadata_converter.py` tries to derive `fold_id` from filenames
  containing `fold_`, which is not part of the documented naming scheme.
- Impact: `fold_id` may be missing unless present in JSON content.

## References
- RFdiffusion3 docs (Inference basics): https://rosettacommons.github.io/foundry/models/rfd3/intro_inference_calculations.html
- RFdiffusion3 input spec & CLI: https://rosettacommons.github.io/foundry/models/rfd3/input.html
- RFD3 PPI tutorial (binder settings): https://rosettacommons.github.io/foundry/models/rfd3/ppi_design_tutorial.html
- RFD3 protein binder examples: https://rosettacommons.github.io/foundry/models/rfd3/protein_binder_design.html
- Foundry README (checkpoint search): https://github.com/RosettaCommons/foundry
