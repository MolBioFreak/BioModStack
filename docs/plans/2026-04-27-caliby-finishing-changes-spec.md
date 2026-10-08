# Caliby Finishing Changes Specification

> For Hermes: Use subagent-driven-development skill to implement this plan task-by-task. Use strict TDD for every code behavior change.

Goal: Finish Caliby from "experimental path with proven sequence-design smoke" to "reliable BioModStack operator feature" without pretending untested modes are production-ready.

Architecture: Keep the existing Nextflow Caliby profile and label as the canonical runtime path. Add runtime preflight, API/template contract validation, supported-weight gating, provenance, and an opt-in real container smoke. Do not use AF3Score/PPIFlow terminology for Caliby selection semantics.

Tech stack: Nextflow DSL2, Apptainer, Python runtime scripts, FastAPI launch layer, YAML model/template registry, Vitest/pytest-style contract tests.

---

## Current evidence snapshot

1. Canonical Nextflow wiring exists in live root config:
   - `nextflow.config:1210-1227` defines `profiles.caliby_experimental` defaults.
   - `nextflow.config:1695-1704` defines `withLabel: Caliby` with `caliby.sif`, `/weights` bind, writable tmpfs, cache env vars, and `MODEL_PARAMS_DIR=/weights/caliby/model_params`.
2. API command synthesis maps launcher params to Caliby params:
   - `platform/api/services/nextflow.py:2282-2284` maps `caliby_experimental/design` to profile `caliby_experimental`.
   - `platform/api/services/nextflow.py:2804-2836` maps `task`, `input_pdb_dir`, `model_name`, etc. to `caliby_*` params.
3. Direct model-load with Nextflow-equivalent options passed:
   - Evidence: `docs/evidence/caliby_model_load_nextflow_opts_20260427_081959.log`
   - Result: `loaded_type= CalibyModel`, `exit_status=0`.
4. A prior real sequence-design smoke succeeded under Nextflow/Apptainer:
   - `work/aa/f954c15117229f4c9c99f15fd3496a/.command.run:103` shows the actual `caliby.sif` launch with `/weights` bind and cache/env settings.
   - `work/aa/f954c15117229f4c9c99f15fd3496a/caliby.log:5-50` shows model load and one-sequence sampling completed.
   - `work/aa/f954c15117229f4c9c99f15fd3496a/raw/metadata/generator_caliby_real_smoke_0001.json:2-11` shows a normalized Caliby design with sequence and `caliby_potts_energy`.
5. Current lightweight helper tests pass:
   - Command run: `python3 -m pytest platform/api/tests/test_caliby_sequence_design_regressions.py -q`
   - Result: `3 passed`.
6. Current installed checkpoint surface is narrower than the UI/template claims:
   - Present: `soluble_caliby_v1.ckpt` under both `/mnt/BioModStack/weights/caliby/model_params` and `/home/dalab/.biomodstack/weights/caliby/model_params`.
   - Not observed in those stores: `caliby.ckpt`, `soluble_caliby.ckpt`, `caliby_packer_000.ckpt`, `caliby_packer_010.ckpt`, `caliby_packer_030.ckpt`.
   - Therefore `sidechain_pack` and alternate sequence-model enum values are not production-proven unless weights are installed and smoked.

Important correction: the earlier direct read-only failure is best explained by an invalid direct invocation missing the canonical `/weights` bind and `MODEL_PARAMS_DIR` contract. The real finishing work is not "make Caliby load at all"; it is "make only the proven/correct contract launchable, validated, documented, and continuously smoke-tested."

---

## Definition of done

Caliby is finishable when all of these pass:

1. A self-serve launch cannot enter the queue with impossible Caliby inputs.
2. A launch cannot select a Caliby model/checkpoint that is absent locally unless explicit download mode is enabled.
3. `sequence_design` with `soluble_caliby_v1` runs through the API-generated Nextflow command and produces at least one PDB plus generator metadata containing `source=caliby`, `generator_mode=sequence_design`, `caliby_model`, and `caliby_potts_energy`.
4. Unsupported modes/models are either hidden/disabled or have installed weights and passing smoke evidence.
5. Logs and output metadata make clear that Caliby uses Caliby Potts/self-consistency metrics, not AF3Score/PPIFlow rank scores.
6. Docs show the canonical invocation; no docs tell operators to run the broken ad-hoc container command.

---

## P0 finishing changes

### Task 1: Add Caliby runtime preflight helper

Objective: Fail early with actionable errors when env/weights/cache are wrong, before Caliby tries to download into a read-only or unintended path.

Files:
- Modify: `scripts/caliby_runtime.py`
- Modify: `scripts/run_caliby_sequence_design.py`
- Modify: `scripts/run_caliby_experimental.py`
- Test: `scripts/test_caliby_runtime_preflight.py`

Behavior to add:
- Add a registry matching Caliby's runtime registry:
  - `caliby -> caliby/caliby.ckpt`
  - `soluble_caliby -> caliby/soluble_caliby.ckpt`
  - `soluble_caliby_v1 -> caliby/soluble_caliby_v1.ckpt`
  - `caliby_packer_000 -> caliby/caliby_packer_000.ckpt`
  - `caliby_packer_010 -> caliby/caliby_packer_010.ckpt`
  - `caliby_packer_030 -> caliby/caliby_packer_030.ckpt`
- Add `resolve_expected_caliby_checkpoint(model_name: str, model_params_dir: Path) -> Path`.
- Add `preflight_caliby_runtime(task, model_name, packer_model_name, allow_download=False) -> dict` that checks:
  - `MODEL_PARAMS_DIR` is set.
  - selected checkpoint exists for `sequence_design` / `ensemble_design`.
  - selected packer checkpoint exists for `sidechain_pack`.
  - `HF_HOME`, `XDG_CACHE_HOME`, `TRITON_CACHE_DIR` parent dirs are writable when set.
  - if checkpoint is absent and `allow_download` is false, raise a clear `RuntimeError` naming the missing file and selected task.
- Call preflight immediately before `load_caliby_model()` in both runtime entry scripts.
- Allow explicit `CALIBY_ALLOW_DOWNLOAD=1` for operator-directed one-time download, but default to no implicit download.

RED tests:
- Missing `MODEL_PARAMS_DIR` fails before importing `caliby`.
- `sequence_design + soluble_caliby_v1` passes when a temp ckpt exists.
- `sidechain_pack + caliby_packer_010` fails when only `soluble_caliby_v1.ckpt` exists.
- Bad cache dir path fails with a message naming the unwritable env var.

Commands:
- `python3 -m pytest scripts/test_caliby_runtime_preflight.py -q`
- `python3 -m pytest platform/api/tests/test_caliby_sequence_design_regressions.py scripts/test_caliby_runtime_preflight.py -q`

Acceptance:
- Local preflight tests pass.
- Direct wrong runtime now fails with an explanatory BMS error, not a HuggingFace/read-only traceback.

### Task 2: Gate the exposed model/mode surface to installed and smoked capabilities

Objective: Stop advertising modes or model choices that are not supported by the installed weights.

Files:
- Modify: `platform/api/config/templates/caliby_experimental.yaml`
- Modify: `platform/api/config/models/caliby_experimental.yaml`
- Possibly modify: `platform/frontend/src/components/JobSubmission.tsx` if the generic template renderer needs disabled-option support.
- Test: `platform/api/tests/test_caliby_experimental_contract.py`

Decision point:
- Production-minimal option: expose only `sequence_design` and `model_name=soluble_caliby_v1` until packer and alternate sequence checkpoints are installed.
- Full-surface option: install all advertised checkpoints, then keep `ensemble_design` and `sidechain_pack` exposed only after smoke evidence exists for each.

Recommended first pass:
- Keep `sequence_design` exposed.
- Keep `ensemble_design` hidden/experimental unless a real conformer fixture smoke is added.
- Disable or hide `sidechain_pack` because packer checkpoints are not currently present.
- Restrict model enum to `soluble_caliby_v1` unless missing checkpoints are installed.

RED tests:
- Registry/template contract test asserts `sidechain_pack` is not available unless packer checkpoint evidence exists or an explicit feature flag is set.
- Registry/template contract test asserts `soluble_caliby_v1` remains available.

Commands:
- `python3 -m pytest platform/api/tests/test_caliby_experimental_contract.py -q`

Acceptance:
- UI/API no longer implies unsupported packer models are ready.
- If full-surface option is chosen, each advertised model has an installed checkpoint and smoke record.

### Task 3: Add server-side conditional validation before Nextflow launch

Objective: Reject bad Caliby jobs before they become Nextflow failures.

Files:
- Create: `platform/api/services/caliby_contract.py`
- Modify: `platform/api/services/nextflow.py`
- Test: `platform/api/tests/test_caliby_experimental_contract.py`

Behavior:
- Add `validate_caliby_params(params: dict) -> dict`.
- For `task in {'sequence_design', 'sidechain_pack'}` require `input_pdb_dir`/`caliby_input_pdb_dir` and path existence.
- For `task == 'ensemble_design'` require `conformer_dir`/`caliby_conformer_dir` and path existence.
- Validate `sampling_overrides_json` is either empty or valid JSON object.
- Validate numeric ranges match template constraints.
- Normalize source keys to `caliby_*` keys once, using the same mapping as `build_nextflow_command`.

RED tests:
- `sequence_design` with no input dir raises a validation error before command creation.
- `ensemble_design` with only input PDB dir raises a validation error.
- invalid `sampling_overrides_json` raises a validation error.
- valid minimal sequence-design params produce a command containing `-profile caliby_experimental,workstation_ryzen7960x` and `--caliby_input_pdb_dir`.

Commands:
- `python3 -m pytest platform/api/tests/test_caliby_experimental_contract.py -q`

Acceptance:
- User-facing error says exactly what to fix.
- Bad Caliby jobs do not enter the queue.

---

## P1 finishing changes

### Task 4: Add a real, opt-in Caliby container smoke runner

Objective: Preserve the proof that the deployed container, weights, cache dirs, and Nextflow command work together.

Files:
- Create: `scripts/smoke_caliby_experimental.py`
- Create: `platform/api/tests/fixtures/caliby/README.md`
- Optionally create fixture: `platform/api/tests/fixtures/caliby/rfantibody_real_smoke_input.pdb` if acceptable to keep a small real RFantibody-derived PDB in repo.
- Test: `platform/api/tests/test_caliby_smoke_contract.py` for command construction only; full smoke remains opt-in.

Behavior:
- Script creates a temp input dir, copies a real valid PDB fixture, invokes `build_nextflow_command('caliby_experimental', 'design', ...)` with:
  - `task=sequence_design`
  - `model_name=soluble_caliby_v1`
  - `num_seqs_per_pdb=1`
  - `batch_size=1`
  - `num_workers=1`
  - `clean_num_workers=1`
- Runs command only when `BMS_RUN_CALIBY_SMOKE=1` is set.
- Validates output files:
  - `metadata/design_manifest.json`
  - at least one `pdb_files/*.pdb`
  - at least one `pdb_files/confidence_*.json`
  - metadata contains `source=caliby`, `generator_family=caliby`, `generator_mode=sequence_design`, and numeric `caliby_potts_energy`.
- Writes timestamped evidence to `docs/evidence/` or an operator-specified output path.

Commands:
- Unit/contract: `python3 -m pytest platform/api/tests/test_caliby_smoke_contract.py -q`
- Real smoke: `BMS_RUN_CALIBY_SMOKE=1 python3 scripts/smoke_caliby_experimental.py --gpu-id 3 --output-root /mnt/BioModStack/bms_results/caliby_smoke`

Acceptance:
- Contract test is cheap and default.
- Real smoke is opt-in, reproducible, and produces durable evidence.

### Task 5: Add Caliby provenance and score semantics to generated metadata

Objective: Prevent AF3Score/PPIFlow carryover confusion in downstream review/selection.

Files:
- Modify: `scripts/caliby_runtime.py`
- Test: `platform/api/tests/test_caliby_sequence_design_regressions.py`

Metadata additions in `normalize_sampling_results()`:
- `score_family: caliby`
- `selection_metric: caliby_potts_energy`
- `selection_direction: lower_is_better`
- `af3score_used: false`
- `upstream_ppiflow_rank_score_used: false`
- `caliby_checkpoint_name`
- optionally `caliby_checkpoint_path`
- optionally `caliby_checkpoint_sha256` for smoked production runs

RED tests:
- Normalization test with dummy `results` asserts these provenance fields exist.
- Filter tests still pass and use Caliby-specific fields.

Commands:
- `python3 -m pytest platform/api/tests/test_caliby_sequence_design_regressions.py -q`

Acceptance:
- Result JSONs can be audited without guessing whether AF3Score/PPIFlow ranking leaked into Caliby output selection.

### Task 6: Update docs to show only canonical supported commands

Objective: Remove operator drift that caused the bad direct-read-only interpretation.

Files:
- Modify: `docs/Caliby_Experimental_Workflow.md` if present; otherwise create it.
- Modify: any launcher docs that mention Caliby direct validation.

Docs must say:
- Canonical path is API-generated Nextflow / `-profile caliby_experimental,workstation_ryzen7960x`.
- Direct container smoke must bind weights and set `MODEL_PARAMS_DIR`:
  - `/mnt/BioModStack/weights:/weights`
  - `MODEL_PARAMS_DIR=/weights/caliby/model_params`
  - cache dirs under `/cache`
- Direct command without those binds is invalid and may produce HuggingFace/read-only cache errors.
- Only `sequence_design + soluble_caliby_v1` is currently production-smoked unless the full-surface option is completed.

Acceptance:
- A new operator can reproduce the same successful model-load smoke from docs.

---

## P2 finishing changes

### Task 7: Add result-ingester and Results Viewer parity checks

Objective: Make standalone Caliby artifacts as inspectable as embedded sequence-design artifacts.

Files:
- Inspect/modify: `platform/api/services/result_ingester.py`
- Inspect/modify: `platform/frontend/src/components/ResultsViewer.tsx`
- Test: add or extend result-ingester/frontend contract tests.

Behavior:
- Ingest `metadata/design_manifest.json` and Caliby confidence JSONs.
- Show `caliby_potts_energy`, self-consistency pLDDT/RMSD if present, and model name.
- Do not label Caliby Potts energy as AF3Score, ipTM, or PPIFlow rank score.

Acceptance:
- Standalone Caliby job appears in Results Viewer with Caliby-specific metrics and provenance.

### Task 8: Promote or leave experimental based on smoke matrix

Objective: Make the UI maturity label match reality.

Files:
- Modify: `platform/api/config/templates/caliby_experimental.yaml`
- Modify: `platform/api/config/models/caliby_experimental.yaml`

Promotion rule:
- Do not flip `experimental: false` until P0 and P1 pass.
- If only `sequence_design + soluble_caliby_v1` is smoked, promote only that narrow mode or keep the card explicitly experimental.
- If `ensemble_design` and `sidechain_pack` remain exposed without weights/smokes, keep the whole card experimental.

Acceptance:
- UI label accurately reflects supported capability; no hidden beta features are represented as production.

---

## Suggested implementation order

1. P0 Task 1: runtime preflight.
2. P0 Task 3: server-side contract validation.
3. P0 Task 2: supported surface gating.
4. P1 Task 4: opt-in real smoke runner.
5. P1 Task 5: provenance/score semantics.
6. P1 Task 6: docs.
7. P2 Task 7: results parity.
8. P2 Task 8: promotion decision.

Do not start by changing containers or rebuilding `caliby.sif`; current evidence shows the deployed container can load and sample when launched with the canonical Nextflow options.
