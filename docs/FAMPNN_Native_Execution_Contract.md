# FA-MPNN native execution and evidence contract

The supported installed dialect is `18363df253dbeb7b2cb963daf7a732fbaa25157d`.
`maturation_native_adapter.SOURCE_SHA256['fampnn']` pins the Python source bytes
used by the parser/writer taps. Installed sources, images and checkpoints are
never edited. Ordinary execution falls back to the installed entrypoint if the
observational adapter cannot instrument that source; existing marked analysis
contracts keep their existing source/coverage requirements.

## Masks and custody

The native positional CSV has exactly three columns: input stem, fixed sequence
positions, fixed sidechain positions. Its existing three-column header is accepted
by the installed reader as an unused input row. No fourth metadata column is sent
to pandas. Author-number masks and independent target-sidechain choices are not
changed by separating custody.

`write_constraints` also emits `<csv>.custody.json` with
`{input_id: {source_structure_sha256, prepared_sha256}}`, and an equivalent
`<prepared-stem>.fampnn_source.json` beside each prepared PDB. The first is retained
by the binder `transport` directory. `FampnnAnalysisPolicy.stagePrepared` stages
available per-input custody for ordinary/child/PLR consumers without requiring it.
The native batch/writer receipt supplies input/output residue correspondence;
filenames, sequence equality and substring matches never establish ownership.

## Typed controls

`FampnnNativeControls.arguments(params)` is the shared advanced argv renderer for
ordinary and inline FA execution. It preserves false and zero. Native nullable
`psce_threshold` uses key presence: missing uses the existing caller default,
explicit null emits Hydra `null`. Existing legacy maturation sample-count/batch
semantics are unchanged. Extra configuration is argv data, not executable shell.

Parent child construction must forward scientific `fampnn_*` settings and the
existing antibody role/mask controls (`antibody_design_mode`,
`antibody_design_loops`, `protect_vhh_tetrad`, `antibody_chains`,
`lock_target_chains`, `lock_antibody_framework`,
`manual_mutation_fixed_positions_json`, `vhh_mode`, `antibody_format`). The child
helper deliberately removes public analysis-authority fields; backend admission
continues loading that authority from the persisted parent.

## Confidence and scope

Machine pSCE reductions retain full Python numeric precision. PDB atom errors have
already been serialized by the native writer; no higher precision is claimed for
those inputs. Comma-separated analysis chain sets are supported, including PLR.
The legacy aggregate remains explicitly labelled as all emitted sidechain atoms
in policy scope, including preserved/unpredicted zeros. Its existing filter
population is not silently changed.

Source instrumentation carries the actual final native sidechain mask, excluding
provided sidechains and absent residues, as `bms_psce_observed_mask`. The exact
native writer batch emits `<pdb>.psce_scope` with version, structure SHA-256,
population and output residue identities. `fampnn_predicted_sidechain_psce` is a
separate optional reduction. Missing scope is unavailable evidence, not an
execution refusal or a fabricated zero error. Sequence-only/no-repack has no
predicted sidechains. A genuinely predicted zero remains a scored observation.

Optional probability analysis uses the native 21-token alphabet, exact tensor
axes, the same softmax row reducer as strict analysis, and sequence-design scope
minus fixed residues. Invalid/missing tensors yield unavailable evidence rather
than truncated arrays or a job gate. Zero probabilities are not replaced by
positive evidence. Shape's leaf helper attaches these reducers to exact native
sample IDs and applies only authored pSCE filters; unavailable optional evidence
does not reject a sequence.

## Bounded, source-only execution optimization

`fampnn_native_runtime` reuses one immutable parsed feature set for contiguous
repetitions of the same input; callers receive deep copies. Presort order and
native length parsing are unchanged. The seq_design-only instrumented sampler
omits unused trajectory CPU transfers/stacks, while preserving all model calls,
RNG draws, sampling updates, returned coordinates, sequences, probabilities and
pSCE. No performance percentage or inference-quality improvement is claimed.
Repeated parent/child PyRosetta restoration is not skipped: residue identity
alone does not prove repeated restoration is numerically idempotent.

## Source delivery and CPU qualification

Runtime source closure must include the new Groovy class and
`scripts/fampnn_native_runtime.py`, `scripts/fampnn_shape_analysis.py`, along with
the existing binding, preparation, probability, pSCE and maturation dependencies.
Shape and marked maturation owners call the shared leaf instrumentation/helpers.

`tests/test_fampnn_fidelity_repair_native.py` runs directly with the installed
image Python, without pytest, weights, model instantiation or GPU exposure. It
executes native CSV/mask readers, actual PDB writers and native sampler control
flow against an explicitly inert denoiser. The control test runs via the existing
API Python with `BMS_TEST_NEXTFLOW_JAR` pointing to an installed offline JAR.
Set `TMPDIR` to task-owned scratch. These are CPU integration/RNG-order proofs,
not model accuracy, PyRosetta geometry or deployed-worker acceptance.
