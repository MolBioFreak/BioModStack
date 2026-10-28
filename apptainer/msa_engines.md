# CPU family-MSA engine image (qualification recipe)

Build from the repository root with Apptainer and sufficient scratch space:

```sh
APPTAINER_TMPDIR="$SCRATCH" APPTAINER_CACHEDIR="$SCRATCH/apptainer-cache" \
  apptainer build --fakeroot "$SCRATCH/msa-engines.sif" apptainer/msa_engines.def
python3 apptainer/msa_engines_smoke.py "$SCRATCH/msa-engines.sif" --scratch "$SCRATCH"
sha256sum "$SCRATCH/msa-engines.sif"
```

This is an x86-64 CPU image, not a registered BMS runtime or deployment. It requires network access to Docker Hub, Ubuntu package repositories, the MAFFT source host and GitHub. The base image is the amd64 manifest `sha256:8a2321a92b438238f93b4346fff61bc4259c99fdd4d9b52ee86f153fee0c344f`. The Ubuntu apt repositories are **not snapshot-pinned**; the installed exact package versions, compiler, build logs, compile commands, executable hashes and TWILIGHT linkage are retained inside `/opt/msa/share/provenance`. Thus a later build can differ; record its own SIF digest and qualify it, do not assume byte reproducibility.

## Source identities

- MAFFT 7.525 without extensions: official source tarball SHA-256 `edb34ae9b26d6b55328c18fa060ed741bca8cd599c2f4f8fad0e0871c8082265`.
- FAMSA v2.5.2 commit `259841074f86361b50367bed396b4e270dfe343a`; submodules isa-l `7e39a2bab2a4a6a72865b32ec3b41a8ef0cb5c14`, libdeflate `ced051e1c260c7008f312400a0471aecf37aa1e0`, mimalloc `2765ec93026f445cad8f38e6b196dd226a1f6e61`, zlib-ng `b392dbabcb7aa4816d88c290be643e4b6515e7be`. Git records and image provenance retain submodule revisions; zlib-ng is used in this build because nasm is not installed.
- MUSCLE v5.3 commit `2cf9d33078c9a85697e38a6f3ad827e6862420df`; its external build generator is `rcedgar/vcxproj_make` commit `806d016858484236d1be4f25edb4c3b3bcac0b62`, script SHA-256 `902735703004c47705ffa389329378f237fecb154945b489edf6abe260c6694f`.
- TWILIGHT v0.3.0 source commit `4839e073048c2f60f2949a8342b6dc0a704ddbed`.

The recipe modifies the MUSCLE generator and TWILIGHT CMake source copies to replace `-march=native` with `-march=x86-64`. FAMSA uses `PLATFORM=generic`; it still compiles separately targeted SIMD objects and zlib-ng uses architecture-specific implementations. TWILIGHT's upstream CMake auto-detects GPUs even with both options OFF: the recipe disables HIP discovery and CUDA compiler discovery. It additionally disables `TALCO_SIMD`, whose x86 path uses AVX intrinsics incompatible with generic x86-64, and builds the upstream scalar branch instead. A generic baseline or successful build on this machine is not evidence of execution on all older CPUs; test target hosts.

## License and distribution status

Image includes original engine licenses and the FAMSA submodule license texts under `/opt/msa/share/licenses`; Ubuntu package copyright files remain under `/usr/share/doc`. MAFFT is BSD-style, FAMSA and MUSCLE GPLv3, TWILIGHT MIT; generator repository declares GPLv3. FAMSA includes mimalloc (MIT), libdeflate (MIT), zlib-ng (zlib), and an isa-l (BSD-style) checkout, though isa-l is not selected in this build. TWILIGHT dynamically links Ubuntu Boost, TBB, zlib, libstdc++, libgcc and glibc; MUSCLE is statically linked with the toolchain runtime. Exact package inventory is in the image. This is an inventory, **not completed legal clearance or a corresponding-source offer**: assess all system/static/transitive license texts, modification notices, source availability and GPL distribution obligations before distributing. The image does not bundle complete corresponding source; retain the exact package sources and modified build scripts as part of a distribution plan.

## BMS installation handoff

The API's frozen uv lock installs `packages/bms-alignment-core` from the same
checkout; the checkout must retain that relative package directory. For an image
already built and independently reviewed, the existing `scripts/publish_runtime_images.py`
accepts the semantic selector `BMS_RUNTIME_IMAGE_MSA_ENGINES_SIF` with its
source path and actual SHA-256. It publishes a complete lane reference set, not
an incremental update; preserve all other intended selectors in the manifest.
This only registers bytes and does not establish qualification or readiness.
The family runtime chooses that lane's registered object, then the conventional
`msa_engines.sif`, or the existing host fallback; `BMS_FAMILY_MSA_SIF` overrides
selection. Receipts record selected provenance and observed digest.

The default supported provision plan still lacks reviewed acquisition metadata for
this image; the local digest below is not a public distribution URL or approval.
A clean online production provision, release/license approval, service activation,
and target-host execution remain unverified. No SIF is uploaded by this recipe.

## Local result (2026-10-27)

Built with Apptainer 1.5.3 using `--fakeroot`; SIF SHA-256 `198186c9238df383a95215117ce3b55402cb9547f9f6ea58d2f4af25d634717a`. GCC 11.4.0, Ubuntu glibc 2.35; TWILIGHT configure selected CPU, its 20 compile commands contain no GPU source or `-march=native`, and `ldd` showed no CUDA/HIP libraries. Executable SHA-256: MAFFT launcher `52795975f34ad60b63b33f8c0cab2f4c67f34bec6c8b9782216f9655ce99ca0e`, FAMSA `63fcb5706cf8afd74f336cff2dbf386f89e0599495179edefb09a174f3cdd634`, MUSCLE `012d675d459a0b3ad9c4fd6cea4c25e17c91bb985ea41523e05cab5328807346`, TWILIGHT `584d4f954100252a78f060285d79a92762220b55d5c27c7a2e175888ccaa93fb`.

The smoke script ran synthetic inputs in an isolated container: MAFFT DNA 3x24, FAMSA and MUSCLE protein each 3x22, TWILIGHT DNA accurate with MAFFT-derived tree 3x24, and accurate TCS with supplied tree 3x24. It checked roster, rectangularity and ungapped sequence preservation. MAFFT generated leaves `1_row_A`, `2_row_B`, `3_row_C`; the initial mapped tree retained MAFFT newlines and TWILIGHT rejected it as incorrect Newick. Removing whitespace for this simple fixture allowed accurate mode to pass. The script's narrowly scoped token rewrite is **not** a production Newick parser or complete mapping implementation. Retain original tree, use a structural parser with explicit reversible mapping in the actual adapter, and qualify complex quoted names and supplied mappings separately.

This is a basic smoke only: RNA, MUSCLE Super5 and ensembles, other symbols/duplicates, many tree shapes, cross-CPU execution, benchmark/reference accuracy, complete options, package registration and BMS job integration remain untested. No inference about biological accuracy follows from this fixture. No generated SIF is committed.
