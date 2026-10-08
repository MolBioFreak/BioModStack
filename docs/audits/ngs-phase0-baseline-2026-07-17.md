# ONT/NGS Phase 0 Baseline — 2026-07-17

Captured at `2026-07-18T01:45:26Z` for the Phase 0–3 work authorized by Christian on 2026-07-17.

## Immutable implementation base

| Field | Value |
|---|---|
| Audit checkout | `/home/dalab/biomodstack/biomodstack` (preserved; heavily dirty) |
| Clean implementation worktree | `/home/dalab/worktrees/biomodstack-ngs-phase0-3-20260717` |
| Branch | `feat/ngs-phase0-3-20260717` |
| Approved base commit | `a69711f7e55786f3867e3952b546b3d6b8c48c11` |
| Remote baseline ref | `origin/feat/ngs-phase0-3-20260717` |
| Remote ref at gate entry | `a69711f7e55786f3867e3952b546b3d6b8c48c11` |
| Clean status at gate entry | 0 porcelain status lines |
| Authoritative specification | `docs/plans/ngs-completion-audit-and-spec-2026-07-17.md` |
| Specification SHA-256 | `56611d51acc2f2063a5a6244b36cf7fb140cb1c23fb83829f2b0df79a44f0621` |

The audited SHA was not on any remote ref before Phase 0. It was pushed to a dedicated feature ref without moving `test` or `main`; the clean worktree was then created from that exact remote-backed SHA. `origin/test` was not used because it was 15 commits behind the audited checkout and omitted nine committed NGS files.

## Baseline test evidence

### Agreed focused contracts — green

```text
/home/dalab/biomodstack/biomodstack/platform/api/.venv/bin/python -m pytest -q \
  platform/api/tests/test_ont_ngs_submission.py \
  platform/api/tests/test_ont_ngs_contract.py \
  platform/api/tests/test_ont_ngs_workflow_products.py \
  platform/api/tests/test_nanopore_nextflow.py

76 passed in 1.36s
```

```text
cd platform/frontend
npx --yes --package=tsx@4.8.1 tsx --test \
  tests/nanoporeTemplateContract.test.ts \
  tests/sequenceQcManifestContract.test.ts

15 tests; 15 pass; 0 fail
```

### Baseline invocation problems — not hidden and not treated as workflow evidence

1. The first API invocation used Hermes's Python and stopped during test collection because that environment does not contain SQLAlchemy. The repository API virtualenv was then used; the agreed suite passed 76/76.
2. The first frontend invocation used Node 20 directly. Node 20 cannot load `.ts` tests. The command then pinned `tsx@4.8.1` explicitly; the agreed suite passed 15/15.
3. `test_ngs_fastq_runtime_smoke.py` did **not** reach workflow execution. The resolved executable was `/home/dalab/.local/bin/nextflow`, a Docker wrapper whose repository mount is hardcoded to the dirty audit checkout. It does not mount the clean implementation worktree passed by the test, so the requested working directory and workflow files are unavailable inside the launcher container. The observed output also contained a curl trust-anchor warning and `.nextflow/history.lock (No such file or directory)`; Phase 0 did not isolate those secondary symptoms and does not attribute them to a missing or invalid CA file. This is a clean-worktree launcher incompatibility, not scientific or runtime validation. Phase 1 must use an explicit clean-worktree launcher and retain command/output evidence.

## Runtime and container inventory

### Host tools

| Tool | Version/status |
|---|---|
| Python | 3.11.14 |
| Nextflow | 25.10.1 build 10547 |
| Java | repository launch uses Temurin 17 |
| minimap2 | 2.31-r1302 at `/home/dalab/micromamba/bin/minimap2` |
| samtools | 1.23.1 at `/home/dalab/micromamba/bin/samtools` |
| bgzip/tabix | htslib 1.23.1 at `/home/dalab/micromamba/bin` |
| bcftools | missing |
| Apptainer | 1.3.0 |
| Dorado | 1.1.1+3c7eef9 at `/usr/bin/dorado` |
| modkit | missing on host PATH |
| Node | v20.19.4 |

### NGS images

| Field | Value |
|---|---|
| Configured default | `/mnt/BioModStack/apptainer/dorado.sif` |
| Resolved target | `/home/dalab/biomodstack/biomodstack/apptainer/dorado.sif` |
| SHA-256 | `2af01c5973eb86736949ea7d29342bb9f24611036906266c35e27c54d2032fad` |
| Size | 4,962,971,648 bytes |
| Dorado | 1.3.1+7c84b01de |
| modkit | 0.6.1 |
| minimap2 | 2.24-r1122 |
| samtools | 1.13 |
| bcftools/bgzip/tabix | missing |
| Python | missing |

The configured path is a symlink into the dirty audit checkout, not an isolated worktree asset. Its bytes are inventoried, but it is **not** accepted as immutable production evidence. Phase 1/3 runtime gates must use a content-addressed copy or another explicitly pinned image location independent of the dirty checkout.

The existing `wf-clone-validation` cache was also inventoried. The `sha…` portion of each filename is upstream's cached OCI content identifier; the final column is the SHA-256 of the local image bytes.

| Cached image | Local SHA-256 |
|---|---|
| `ontresearch-canu-sha50e56c57b7dfcc28ea176895c6ad98b43c607df2.img` | `fd26bc095b1970900d60b9a3a81f925218da39934ecad04054e1ffe9773762f1` |
| `ontresearch-medaka-sha447c70a639b8bcf17dc49b51e74dfcde6474837b.img` | `6e0625797352b47b5894c036443c71f782f5304e898d42dac0a565240fc4aa39` |
| `ontresearch-plannotate-shae4901fb4353581a26049f564d279edd81fe38805.img` | `46ed6a2c15c986f9d04a452199f741b84e06d48dd5d16ced20a095b4cdbaf3b1` |
| `ontresearch-wf-clone-validation-sha0ebc91d22c0ea5183272af8bf2b96ca51e88ad5d.img` | `28d79c632f990c4a220b0d9d55fa7af1684320f77c8f907dcd37c803205eb8cc` |
| `ontresearch-wf-common-sha72f3517dd994984e0e2da0b97cb3f23f8540be4b.img` | `f10ab74d9639f3110e107afbca5e9efa149a2c08f50fd67f6350db82ccf36de8` |

### Dorado model directories

Directory digests use a deterministic tar stream (`--sort=name`, epoch mtime, numeric owner/group 0).

> **Erratum to the retained audit snapshot:** the authoritative report states at line 117 that no Dorado model files were found under `/mnt/BioModStack`. The Phase 0 live inventory found the three populated directories below, with file timestamps predating this audit. That snapshot statement was incorrect or searched the wrong path. The report remains the requirements authority, while this baseline supersedes that one inventory fact.

| Model directory | Files | Bytes | Directory SHA-256 |
|---|---:|---:|---|
| `dna_r10.4.1_e8.2_400bps_sup@v5.2.0` | 158 | 315,286,691 | `0b494c86f0b61d973ca879cad89e472b2b84e2b2b322e301eee80f20120b36cb` |
| `dna_r10.4.1_e8.2_400bps_sup@v5.2.0_4mC_5mC@v1` | 24 | 41,181,653 | `55ac6f9bd6c18b3e297e12c4f8d8ea3c208d2955a9eda5fdd57c7f5f35212ce5` |
| `dna_r10.4.1_e8.2_400bps_sup@v5.2.0_6mA@v1` | 24 | 41,180,085 | `22e303b7df1123d61768150cb2a0d49e9c4e7e9cb1adbf453823e48ee1215184` |

## Phase 0 acceptance decision

- **Base choice:** approved audited SHA, remote-backed on a dedicated feature branch.
- **Source worktree isolation:** PASS. Runtime launcher/image isolation is explicitly not claimed and remains a Phase 1/3 prerequisite.
- **Baseline focused contracts:** PASS (76 API + 15 frontend).
- **Scientific automatic PASS:** remains disabled; Phase 0 grants no scientific-verification claim.
- **Runtime smoke:** BLOCKED before workflow execution by a launcher that is incompatible with the clean worktree and therefore excluded from positive evidence.
- **Phase 0 result:** PASS for entering Phase 1 source implementation, subject to the acceptance ledger and scoped review before commit. No production-like or scientific runtime gate is approved.
