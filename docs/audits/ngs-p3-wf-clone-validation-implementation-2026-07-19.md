# P3 pinned `wf-clone-validation` implementation audit — 2026-07-19

## Scope and decision

This change implements P3 only. It does not add P4 FAST5, chemistry/model discovery, duplex, barcode, or model-download behavior. The implementation candidate is locally verified; independent review and a fresh full production execution of the rewritten outer wrapper remain release gates.

Upstream exit status and `sample_status.txt` are execution evidence only. They never create a BioModStack biological PASS. The adapter always hands the upstream final assembly to the existing Phase-2 verifier, which remains the sole scientific-verdict authority.

## Immutable identities

- Upstream repository: `https://github.com/epi2me-labs/wf-clone-validation.git`
- Upstream release/commit/tree: `v1.8.4`, `b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42`, `9cc0a24beee74eccdb07765b755fa64e04bd8141`
- Patched source path: `/mnt/BioModStack/ngs/wf-clone-validation/v1.8.4-bms.1`
- Patched commit/tree: `7e6b7f0dfe31ee855ec1342c5ea8c5a73021d5a4`, `6d76e709d6ba599f30854fc0478da555c924e18e`
- Compatibility patch SHA-256: `a7c0cc4a19d5bc3195c96a8fb842bb5031adb37ab6a126f33669b020f756bc95`
- Nextflow: `/usr/local/bin/nextflow`, version `25.10.0`, build `10289`
- Accepted upstream model identity: `dna_r10.4.1_e8.2_400bps_hac@v5.0.0`
- Model store: `/mnt/BioModStack/models/dorado/1.3.1`
- Runtime policy: `NXF_OFFLINE=true`; no runtime source/image/model provisioning

Locked image files and live SHA-256 values:

| URI | SHA-256 |
|---|---|
| `docker://ontresearch/wf-clone-validation:sha0ebc91d22c0ea5183272af8bf2b96ca51e88ad5d` | `28d79c632f990c4a220b0d9d55fa7af1684320f77c8f907dcd37c803205eb8cc` |
| `docker://ontresearch/canu:sha50e56c57b7dfcc28ea176895c6ad98b43c607df2` | `fd26bc095b1970900d60b9a3a81f925218da39934ecad04054e1ffe9773762f1` |
| `docker://ontresearch/medaka:shacf8338462607b17b1d68dbce212cb93daea50bad` | `abb862dd733af213eb874600f314aa58b22b8d4c7f5eb62bfe385a32731cea54` |
| `docker://ontresearch/wf-common:shafdd79f8e4a6faad77513c36f623693977b92b08e` | `8cacb6a3c0a9a1ad3dd0f0e0624e7da512ae6d247bb878d16b3b8457d8342262` |
| `docker://ontresearch/plannotate:shae4901fb4353581a26049f564d279edd81fe38805` | `46ed6a2c15c986f9d04a452199f741b84e06d48dd5d16ced20a095b4cdbaf3b1` |

The canonical deployment lock is `config/ngs/wf_clone_validation_v1.8.4.lock.json`. Its schema is `schemas/ngs/wf_clone_validation_lock.schema.json`. The checked-in patch is the byte-for-byte output of:

```bash
git -C /mnt/BioModStack/ngs/wf-clone-validation/v1.8.4-bms.1 \
  diff b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42 \
       7e6b7f0dfe31ee855ec1342c5ea8c5a73021d5a4 -- main.nf
```

It contains only the `metadata` to `metadata_json` local-variable rename and the matching report here-document reference.

## Runtime and provisioning

`scripts/validate_wf_clone_runtime.py` fails closed on lock/schema policy, source HEAD/tree/dirty state, patch digest/result identity, Nextflow version/build, all five image files/digests, and selected exact model identity/store presence. Success writes deterministic `biomodstack.wf_clone_validation_runtime_provenance.v1` JSON.

Network-enabled provisioning is explicit and separate from runtime:

```bash
python3 scripts/provision_wf_clone_runtime.py \
  --lock config/ngs/wf_clone_validation_v1.8.4.lock.json
```

Provisioning builds in a sibling temporary directory, reproduces the reviewed compatibility commit with pinned commit metadata, verifies the resulting tree/commit, atomically renames the source, and atomically installs each verified image. It returns without mutation for exact existing assets and refuses any existing mismatched source or image.

The runtime wrapper validates first, invokes the absolute patched source directly, preserves exactly `flye` or `canu`, accepts only the locked model ID, sets offline/cache variables, and performs no pull, clone, source copy, patch, rewrite, model substitution, or assembly fallback.

## Adapter and Phase-2 bridge

`scripts/adapt_wf_clone_validation.py` emits `biomodstack.wf_clone_validation_adapter.v1` and validates/inventories final FASTA, assembly stats, BAM/BAI, full-reference BCF/CSI, `sample_status.txt`, upstream report, emitted Plannotate artifacts, runtime provenance, and remaining supporting upstream outputs. It rejects symlinks/escapes, ambiguous candidates, absent companions, malformed FASTA/tabular/BCF/JSON data, and sample/length/status contradictions. Every inventoried file has a SHA-256 digest.

`CloneValidationAdapter` derives Phase-2 per-base support and alignment counts from the authoritative original analysis BAM/reference. The upstream assembly-aligned BAM is supporting evidence only. `ConstructVerify` receives the upstream final FASTA as observed consensus and remains responsible for the canonical `biomodstack.construct_verification.v2` verdict/artifacts.

The upstream workflow does not provide a verifier-recomputable original source-read binding through this adapter. That is recorded as `SOURCE_READ_PROVENANCE_UNAVAILABLE`; consequently an otherwise successful upstream run remains REVIEW rather than receiving fabricated trust.

## Real offline evidence

The already completed production-like offline run is retained at `/tmp/wf-clone-v184-bms1-offline-run`. It used the pinned patched source and cached images. All trace tasks completed, and `sample02` assembled successfully at 3,037 bp. The P3 adapter was run directly over those outputs with the live-validated runtime provenance:

```text
schema: biomodstack.wf_clone_validation_adapter.v1
execution.status: SUCCEEDED
execution.exit_code: 0
upstream sample status: completed
scientific_verdict: REVIEW
scientific reasons: CANONICAL_PHASE2_VERIFICATION_REQUIRED, SOURCE_READ_PROVENANCE_UNAVAILABLE
```

The official demo archive remains `/tmp/wf-clone-validation-demo-v1.8.4.tar.gz` with SHA-256 `d81dc4c61c34047633d13ad56fd1c29379d047cb8af7a4bd57fcad8832408a09`.

## Verification evidence and caveats

RED evidence is `/tmp/codex-p3-red.txt`. Final command evidence and counts are summarized in `/tmp/codex-p3-summary.txt`.

Remaining caveats:

- The successful offline run predates this outer-wrapper rewrite, although it used the exact source/tree/images now locked and its outputs pass the new adapter.
- No automatic scientific PASS is expected from this integration while source-read provenance is unavailable and the current Phase-2 profile remains uncalibrated.
- Independent review and a fresh end-to-end production run of the rewritten outer wrapper remain release/commit gates.
