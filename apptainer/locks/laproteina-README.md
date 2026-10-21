# La-Proteina sampling runtime

The recipe preserves upstream source `cde5de3ead6e4d76f367da6dc5174be9913ef6ca`, its scientific presets, and the existing BMS build seams. No checkpoint, training corpus or optional oracle is baked into the image.

## Dependency resolution

`laproteina-cu128.in` retains the official Python requirements and torch 2.7.0, selecting its official CUDA 12.8 distribution instead of CUDA 11.8 for Ampere-through-Blackwell hosts. The PyG extensions are the matching official pt27cu128 wheels. This changes the **numerical backend identity**, not the model or sampling defaults; bitwise scientific equivalence with cu118 is not asserted.

Resolve intentionally, from the repository root (uv 0.11.26 was used):

```sh
uv pip compile apptainer/locks/laproteina-cu128.in \
  --python-version 3.11 --python-platform x86_64-unknown-linux-gnu \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --find-links https://data.pyg.org/whl/torch-2.7.0+cu128.html \
  --index-strategy unsafe-best-match --generate-hashes --no-annotate --no-header \
  -o apptainer/locks/laproteina-cu128.lock
```

The committed hash lock, not re-resolution, is the build input. Bootstrap and runtime both use setuptools 80.9.0. NumPy/Cython are installed from that lock before building cpdb-protein without isolated, unpinned build dependencies. Compiler/system packages come from the dated Debian snapshot; their installed inventory is `/opt/installed-system-packages.txt`. `/opt/installed-packages.txt` and `/opt/installed-artifacts.json` retain installed Python versions and source artifact metadata.

## Build and qualification

Build a sandbox first with `apptainer build --fakeroot --sandbox <private-sandbox> apptainer/laproteina.def`, then pack it with `apptainer build --mksquashfs-args '-processors 4 -mem 2G' <private-output.sif> <private-sandbox>`. Place temporary/build state outside Git. The pinned Docker digest intentionally has no tag: Apptainer rejects tag-plus-digest references.

The fail-fast `%test` imports the real native entrypoint and CUDA extension modules and uses the actual native parser to compose all seven presets, checking LD/AE pairings and LD3 noise defaults. It does not load checkpoints or generate proteins. Run `apptainer test <image>` on the packed artifact as well.

Official torch cu128 includes sm80/sm86/sm90/sm100/sm120. Matching PyG scatter/sparse/cluster wheels include Ampere/Hopper cubins and forward-compatible PTX. A tiny host CUDA kernel probe is distinct from checkpoint-backed scientific qualification; neither CPU composition nor architecture metadata alone establishes scientific inference parity.
