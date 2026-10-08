# Fold-CP runtime

`apptainer/fold_cp.def` pins NVIDIA-BioNeMo/boltz-cp to
`dedfdba3f33fd650aeb8f2da1005b44ebd03f95e` (merged 1D CP support).
The tracked `prediction-cli-cp-topology.patch` only exposes the existing native
Python `run_predict(cp_topology=...)` parameter through Click, forwards it, and
corrects the CP-rank help text. Choices are `2d` and `1d`; default remains `2d`.
No inference defaults, outputs, confidence behavior, or calculations are patched.
BMS uses `cp_topology`; Nextflow uses `bcp_cp_topology`; native CLI uses
`predict --cp_topology`.

## Inputs and upstream review

The local-image parent is the existing BoltzGen SIF, with exact SHA256
`a62b63635a53f18f3bbb66585817bdd0425f58799696b2a1c19124bd6023a672`.
Verify those bytes before rebuilding; the recipe does not install or upgrade
Python packages or the accelerator stack. The inherited dependency environment
is part of this input, not a newly resolved upstream installation.

The original tracked Fold-CP pin was `f76f37a77c854b56b6250b426af8c2d63b501f7f`.
The consumed Development image inspected during qualification instead carried
`17235b41c5c91aadee7e5b30c6e7fbb912e1df3b`, digest
`06e170407c21601ffd93ba0d483ce2255b9db591fad7f0101063c01ef98ef4a9`.
Its embedded definition named the same BoltzGen parent digest, which still matched
actual parent bytes. Therefore the existing parent recipe is retained rather than
introducing a derived Fold-CP parent or unrelated dependency changes.

Both prior revisions are ancestors of the new pin. The original-to-new interval
includes the agentic plugin, CP confidence training, data retry/error propagation,
featurizer/crop/augmentation fixes, rank-offset prediction seeding, distributed
manager fixes, Triton steering/clash kernels, and the 1D model/data/loss/training
implementation. These are upstream changes in the explicitly approved forward
pin, not BMS calculation backports. The consumed-to-new interval is the 1D merge:
flat mesh dispatch, 1D data/model classes, shared atom/token placement handling,
encoder/trunk redistribution and backend setters, and CPU-safe optional Triton
attention import. `pyproject.toml` and `scripts/process/requirements.txt` are
byte-unchanged across both intervals. There is no upstream dependency lockfile.

Upstream warns that 1D confidence at CP > 1 diverges from serial. That warning and
confidence outputs remain untouched. It is an evidence limitation, not a new
BMS execution restriction.

## Build and native qualification

Run from the repository root so `%files` consumes the tracked patch:

```bash
sha256sum /mnt/BioModStack/apptainer/boltzgen.sif
# Compare with the exact parent digest above before building.
export APPTAINER_TMPDIR="$TMPDIR/foldcp-1d-build/tmp"
export APPTAINER_CACHEDIR="$TMPDIR/foldcp-1d-build/cache"
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
apptainer build --fakeroot /mnt/BioModStack/apptainer/<new-name>.sif apptainer/fold_cp.def
```

Use a new filename, never overwrite an existing SIF or move an active alias.
The recipe verifies the upstream revision and exact patch digest, checks forward
and reverse patch application, compiles the CLI, imports the actual native CLI
and predictor, and checks both CLI help entrypoints.

Exercise the built image (no source bind or substituted packages):

```bash
apptainer exec --cleanenv --bind "$PWD/tests:/runtime-tests:ro" \
  --env BMS_TEST_FOLD_CP_NATIVE=1 --env TMPDIR="$TMPDIR" \
  <new.sif> python3 /runtime-tests/test_fold_cp_native_cli.py
apptainer run --cleanenv <new.sif> predict --help
sha256sum <new.sif>
```

The native test imports the actual installed modules and intercepts only
`run_predict` after CLI parsing to avoid downloading weights or starting inference.
It verifies omitted/explicit 2D, explicit 1D with three CP ranks, invalid-choice
handling, help, preserved defaults, and identical non-topology forwarding including
confidence-related output switches. This CPU-harmless boundary harness is not a
prediction, GPU kernel, numerical parity, or multi-GPU qualification.
Without `BMS_TEST_FOLD_CP_NATIVE=1`, host test discovery explicitly skips these
image-only tests. Local NVIDIA driver availability must be reported separately;
no driver repair, rental, publication, adoption, or service restart is performed
by this runtime build.
