# ProtonPottsMPNN native pH redesign runtime

This is the native **post-candidate, fixed-backbone sequence redesign** runtime, not a new candidate generator, refolding engine, or binding-validation claim. It calls `PHDesignCriteria` and `PottsMPNNPHEngine.run_ph_redesign` from [ProtonPottsMPNN](https://github.com/christian-creator/ProtonPottsMPNN), revision `09682abfa7d20e0abcdeea0490b7a4b1c190aee3`. Nothing changes global protonation dH calculations, mutation budgets, objectives, featurization, or RNG. API, workflow, typed operator UI and result integration are separate owners; this runtime alone is not complete BMS integration.

## Immutable inputs and build

Run from the repository root:

```bash
mkdir -p "$TMPDIR/protonpottsmpnn-build"
APPTAINER_TMPDIR="$TMPDIR/protonpottsmpnn-build" apptainer build \
  /mnt/BioModStack/apptainer/protonpottsmpnn-09682ab-cpu-locked-20261003.sif \
  apptainer/protonpottsmpnn.def
```

The definition fixes the Python 3.12.13/amd64 base by OCI digest, Debian packages by the `20260805T000000Z` snapshot, upstream and HBPLUS source by revisions **and archive hashes**, the shipped checkpoint by SHA256, the observational patch by SHA256, and all 138 resolved Python dependencies by versions and package hashes. It installs the native Foundry package editable with **no dependency resolution and no build isolation** so EV6's native model pickles and threshold/assets remain available. `editables`, Hatchling and hatch-vcs are explicitly locked build dependencies. There is no RF3 dependency extra or refolding installation.

The lock is the native `foundry/pyproject.toml` dependency closure plus inference preparation needs: FLAML 2.6.0 (AutoML), XGBoost 2.1.4, LightGBM 4.7.0, scikit-learn 1.8.0, pandas 2.x, ipdb and propka. The broad notebook extras are deliberately not installed: no JupyterLab/server, nbconvert or SHAP. Native Foundry itself declares ipykernel; its declared dependencies are not rewritten. The standard XGBoost 2.1.4 wheel brings NCCL as a distribution dependency; that does not require GPU execution or a driver. Torch is `2.14.1+cpu`.

FLAML 2.7 and XGBoost 3.4 resolved from upstream's broad README ranges, but XGBoost 3.4 exposed missing estimator attributes when loading the shipped older pickles. Selecting FLAML 2.6.0 and XGBoost 2.1.4 avoids that incompatible estimator API. Models and thresholds were **not** modified or reserialized. Full EV6 prediction on PD-L1, not merely import checks, is covered by the native tests.

To re-resolve intentionally, compile the pinned native Foundry manifest and inference dependencies with the existing lock as constraints using `uv pip compile --python-version 3.12 --generate-hashes --torch-backend cpu`; do not let floating README extras silently replace a tested lock. This is source/input reproducibility, not a claim that two independently built SIF files necessarily have the same timestamp/UUID or byte hash. The release image must be hashed separately.

## HBPLUS acquisition and licensing evidence

The [EMBL-EBI author's software page](https://www.ebi.ac.uk/thornton-srv/software/HBPLUS/) states that HBPLUS 3.06 is available free via [RomanLas/HBPLUS](https://github.com/RomanLas/HBPLUS). The definition uses revision `0bd4df6aab49054322c23c4dcfcc07075a84bc05`, builds the original C source without patches, and retains the original bundled manual/copyright with the executable. Its nested source tar SHA256 is `937467447bd2e429630cc9a226d7d967f728ca2d39bac3ffc70eb378f498fa20`.

**Free download is not proof of unrestricted licensing.** The bundled manual's section 5 still contains confidentiality and academic-only/non-commercial provisions (notably clause 7). Deployment for commercial/industrial use needs the owner's licensing clarification/permission. No replacement geometry, numerical fallback, trained-model edit, or runtime admission gate was added. This evidence does not resolve commercial redistribution permission.

## Invocation and request boundary

```bash
apptainer run --cleanenv \
  --env OMP_NUM_THREADS=1,OPENBLAS_NUM_THREADS=1 \
  --bind "$PWD:$PWD" \
  /mnt/BioModStack/apptainer/protonpottsmpnn-09682ab-cpu-locked-20261003.sif \
  --request /absolute/path/request.json \
  --input /absolute/path/candidate-complex.pdb \
  --out /absolute/path/output --device cpu --n-jobs 1
```

No `--nv` is needed for CPU qualification. `--n-jobs` consumes scheduler-owned CPU solve concurrency; it is not a scientific control or a hardcoded sweep expansion. Device selection and native root/checkpoint paths are runtime-owned CLI settings.

The request has:

- `contract: protonpottsmpnn_design.v1`;
- `source: {requested_path, sha256}` (preserved exactly);
- `options.binder_chain`: explicit binder chain from the supplied candidate complex;
- `options.criteria`: native `PHDesignCriteria` objects, passed directly with **all 42 native fields supported**, including placements/regions/contrasts, whole-chain and placement methods, mutation scope, objectives, normalization, trajectory controls and native solver knobs;
- `options.seed`: native default 0;
- `options.initial_sequences`: native optional list of candidate binder sequences; native criteria `seed_source=inverse` redesign each externally supplied seed, while `native` uses the complex's binder sequence;
- `options.engine_options`: `field_source=self_edge`, `etab_source=null`, `etab_hidden=null`, `field_hidden=null` are editable inherited native defaults. The selected shipped-checkpoint profile visibly fixes `extended_vocab=v6`; alternative source/head architectures must actually match the selected checkpoint (native strict state-dict loading is unchanged).
- `options.write_fasta` and `write_states_fasta`: canonical and microstate FASTA exports (default true).

`run_ph_redesign` does not call the inherited engine's structure or FASTA export method. The adapter writes native sequences into FASTA without modifying science. The inherited `write_structures` constructor flag is accepted and recorded, but **native pH redesign does not produce structures**: `runtime.structure_output` explicitly states unsupported/not produced/not refolded. The UI/API owner should not advertise native structure output for this method. No unchanged backbone is emitted as a refolded/validated design. CIF input uses first model and author chain identifiers, matching explicit binder-chain selection. Full criteria/defaults and architecture/source choices remain native/operator-owned; the example parameters are not hidden runtime overrides.

## Manifest and observational sweep fix

`<out>/protonpottsmpnn_design/manifest.json` contains exactly:

- contract, original source, **full original request**;
- `designs`: stable per-source/criteria/native-sample `design_id`, `criteria_index`, original `native_design_id`, and **full** `dataclasses.asdict(PHDesignOutput)`, including every trajectory step;
- native `seed_energies` for supplied initial sequences;
- source/checkpoint/module/dependency and effective native configuration identity under `runtime`;
- artifact paths relative to `<out>` (manifest and enabled FASTA files).

The native engine previously deduped a whole sweep by `design_id()`, which does not encode every criterion. The tiny source patch adds **observational `criteria_index` attribution after the solve** and keys deduplication by `(criteria_index, original_native_id)`. Native IDs, per-criterion numerical computations, ordering within solves, RNG seeding (`base_seed + 1009*ci`) and mutation semantics are unchanged. Identical criteria at indices 0 and 1 now retain both independently seeded outputs. Serial and CPU fork-pool outputs compare exactly in qualification tests. Existing deduplication within one criterion remains intact.

The adapter never uses lossy native `to_metadata()` in place of the full dataclass. The scalar global protonation dH is the native report, not a new binding cutoff, target pH interpretation, or validation condition.

## Native acceptance

With a task-local inference environment containing the pinned native package:

```bash
PROTONPOTTSMPNN_ROOT=/path/to/patched/native/checkout \
HBPLUS_PATH=/path/to/original/hbplus \
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
python -m pytest tests/test_protonpottsmpnn_native_runtime.py -q -rs
```

The tests exercise the actual checkpoint, full five-His/five-acid EV6 ensembles and HBPLUS geometry, repeated-criteria collision preservation, serial/parallel equality, external sequence seed energies, canonical/state FASTA, complete manifest/request readback, trajectories and CIF author-chain parsing. Without an explicit native root, native tests skip; a skipped test is not native qualification.

Real PD-L1 example acceptance used the upstream `inference/design_ph.py` criterion (block descent, three centres, two samples, temperature 0.05, lambda 0.3, block size 3, native seed, trajectory enabled). It returned two length-114 designs:

| Sample | Potts H | Selective energy | Global protonation dH | Trajectory steps |
| --- | ---: | ---: | ---: | ---: |
| 0 | -51643.1875 | -21.901519775390625 | 26.55859375 | 77 |
| 1 | -51642.31640625 | -22.120574951171875 | 26.01953125 | 83 |

An independent unpatched upstream run was compared to the patched runtime outputs: **all original dataclass fields, sequences, energies and every trajectory step were exactly equal**. Native test execution passed four tests without skips. Evidence (requests, actual result manifests/FASTA, unpatched outputs, native test log and image build logs) lives outside Git in the task evidence directory. API/UI integration and commercial HBPLUS licensing remain separate acceptance items.

The exercised release SIF is `protonpottsmpnn-09682ab-cpu-locked-20261003.sif`, SHA256 `9e1c6762b39c509983015f4f860a03cf414a8ec08982c12bf07db7ae7a7aeb5d` (1,442,050,048 bytes). `apptainer/protonpottsmpnn-runtime.lock.json` records its identity and source/checkpoint/recipe hashes. Its embedded definition was compared directly to this checkout. Both the native example and a two-criteria external-seed sweep executed inside this SIF. The latter retained two distinct criteria-attributed outputs and native seed energies (`potts_energy=-51627.765625`, `global_protonation_dH=29.265625`) with scheduler concurrency 2. The final SIF example matched all scratch-native dataclass outputs and trajectories exactly.
