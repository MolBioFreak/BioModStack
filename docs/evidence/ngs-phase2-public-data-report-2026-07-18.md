# ONT/NGS Phase 2 public-data evidence packet (2026-07-18)

Machine-readable evidence and the exact recovered command/script text are in [`ngs-phase2-public-data-report-2026-07-18.json`](ngs-phase2-public-data-report-2026-07-18.json).

## Claim boundary first

| Claim | Evidence-backed conclusion |
|---|---|
| Software correctness | **Partial; not qualified as a reproducible PASS.** Two real host-local Nextflow runs completed and emitted fail-closed `REVIEW`/`FAIL` outputs. However, the complete dirty-worktree source bytes used at execution were not frozen or hashed, and the BC115 alignment statistics mixed read and alignment-record counts. The packet proves recorded execution and no false scientific `PASS`, not fully reproducible software correctness. |
| Technical concordance | **Descriptive, reference-conditioned only.** BC114 reached 99.8478% identity to the deposited Sanger record and retained one insertion with exact `T`-allele support of 93.1818% (246/264); the older 94.3182% value counted all insertion alleles/lengths and is not an exact-allele support claim. The same reference selected the 283 reads, so this is not an unbiased concordance estimate. |
| Biological accuracy | **NOT QUALIFIED.** The insertion cannot be adjudicated as ONT error, Sanger error, or biological difference from these deposits, and the full benchmark was not run. |
| Thresholds | **Experimental.** Profile `plasmid_strict_v1` has `public_accuracy_validated=false`; no production threshold promotion is authorized. |

## Source registries and retrieval

- Paper: *Amplicon_sorter*, DOI [`10.1002/ece3.8603`](https://doi.org/10.1002/ece3.8603).
- Figshare: article `16627654`, v1, DOI [`10.6084/m9.figshare.16627654.v1`](https://doi.org/10.6084/m9.figshare.16627654.v1), API [`https://api.figshare.com/v2/articles/16627654`](https://api.figshare.com/v2/articles/16627654), CC0.
- Zenodo: record `6554346`, concept record `6554345`, API [`https://zenodo.org/api/records/6554346`](https://zenodo.org/api/records/6554346), CC0. Its record DOI is `10.5061/dryad.zgmsbccd0` and it is in the Dryad community. It is therefore described here as a Zenodo archival record, **not** as a byte-identical Figshare mirror.
- Registry metadata was rechecked at `2026-07-19T00:08:43Z`.

The large-file acquisition command was submitted at `2026-07-18T23:02:39.782211Z`. Local completion mtimes were `2026-07-18T23:03:21.969817Z` for `supHAC_data.zip` and `2026-07-18T23:03:23.282822Z` for `Sanger_references.fasta`. The small-file command was submitted at `2026-07-18T23:10:46.310164Z` and its result was recorded at `2026-07-18T23:10:50.715919Z`. These are Hermes/local-filesystem acquisition timestamps; no server-attested HTTP receipt timestamp was retained.

### Exact acquired objects

| Exact local/Zenodo key | Zenodo file ID | Direct URL | Bytes | Published/local MD5 | Local SHA-256 |
|---|---|---|---:|---|---|
| `supHAC_data.zip` | `4060e2b5-b4bd-423c-8c65-2c34bd8f9596` | [content](https://zenodo.org/api/records/6554346/files/supHAC_data.zip/content) | 216,334,298 | `7a7a62704fac9baccff16479ddfce21b` | `1fda95558cfcd6562beea9ae2ac870b920d304682b8e9ceabe8468613eb28a19` |
| `Sanger_references.fasta` | `8ef1f9e3-6a37-4ed3-9446-0db550a6acf6` | [content](https://zenodo.org/api/records/6554346/files/Sanger_references.fasta/content) | 36,470 | `a8a1a82d0e6f8f81ceba70d00f2bf68c` | `d32759ef411d32b3ccfd245b5eea37f41908490910993f660d1c99ec422e20fc` |
| `barcode_sequences.xlsx` | `ca0bed82-de89-426b-a66d-b1b5005300e0` | [content](https://zenodo.org/api/records/6554346/files/barcode_sequences.xlsx/content) | 18,475 | `a7a0bd1fcd9585a2444894fd8a9d818c` | `7a96490f7b87596a9bd6685afe25a2e5fe15d9c6e87e578cde642b5f5ec745fc` |
| `barcodes_species.xlsx` | `bf362237-8492-452e-a227-668294dd1902` | [content](https://zenodo.org/api/records/6554346/files/barcodes_species.xlsx/content) | 12,657 | `a18413f4e6fd121891f3a1e2929f55f6` | `f541111ed2147042f24bf471ae38652ee57a04035d5484c89853d0e9734fbfa8` |
| `README.txt` | `3edf5b71-9984-4659-a126-a6cead0e35e1` | [content](https://zenodo.org/api/records/6554346/files/README.txt/content) | 910 | `8489632fac4f487765b3ffec1817cadc` | `e984bcd069ecc4369ff5c2e2022701c236fe7be84545a43035069d5cef038312` |

`supHAC_data.zip` contains one member, `supHAC.fastq` (538,913,817 uncompressed bytes), with 238,407 FASTQ reads.

### Exact acquisition commands

```bash
set -o pipefail
root=/tmp/biomodstack-phase2-public
mkdir -p "$root"
curl -fL --retry 3 --retry-delay 2 -C - \
  'https://zenodo.org/api/records/6554346/files/supHAC_data.zip/content' \
  -o "$root/supHAC_data.zip"
printf '7a7a62704fac9baccff16479ddfce21b  %s\n' "$root/supHAC_data.zip" | md5sum -c -
sha256sum "$root/supHAC_data.zip" | tee "$root/supHAC_data.zip.sha256"
curl -fL --retry 3 \
  'https://zenodo.org/api/records/6554346/files/Sanger_references.fasta/content' \
  -o "$root/Sanger_references.fasta"
printf 'a8a1a82d0e6f8f81ceba70d00f2bf68c  %s\n' "$root/Sanger_references.fasta" | md5sum -c -
sha256sum "$root/Sanger_references.fasta" | tee "$root/Sanger_references.fasta.sha256"
```

```bash
root=/tmp/biomodstack-phase2-public
for name in README.txt barcode_sequences.xlsx barcodes_species.xlsx; do
  curl -fsSL "https://zenodo.org/api/records/6554346/files/$name/content" -o "$root/$name"
done
sha256sum "$root"/README.txt "$root"/barcode_sequences.xlsx "$root"/barcodes_species.xlsx \
  | tee "$root/small-files.sha256"
```

## Which barcode maps were actually used

The subset scripts used the **Zenodo bytes** named exactly:

1. `barcode_sequences.xlsx` — workbook `barcodes_andy1`.
2. `barcodes_species.xlsx` — workbook `Sheet1`.

Selected authoritative rows:

| Workbook/sheet/row | Values |
|---|---|
| `barcode_sequences.xlsx` / `barcodes_andy1` / row 16 | `BC114`, `AACGAGTCTCTTGGGACCCATAGA`, `ACGACGTTGTAG`, `AACGAGTCTCTTGGGACCCATAGA`, `GATGGTCGATGA ` |
| `barcode_sequences.xlsx` / `barcodes_andy1` / row 17 | `BC115`, `AGGTCTACCTCGCTAACACCACTG`, `ACGACGTTGTAG`, `AGGTCTACCTCGCTAACACCACTG`, `GATGGTCGATGA ` |
| `barcodes_species.xlsx` / `Sheet1` / row 15 | `BC114`, `G. kinzelbachi COI` → Sanger record `Gomphus_kinzelbachi_COI` |
| `barcodes_species.xlsx` / `Sheet1` / row 16 | `BC115`, `G. pulchellus COI` → Sanger record `Gomphus_pulchellus_COI` |

Barcode sequences were read directly by `openpyxl 3.1.5` under Python `3.11.14`. Species-to-Sanger names were manually resolved from the workbook and hard-coded in the recorded scripts; an acquisition-time normalized mapping file was not retained.

### Figshare versus Zenodo workbook bytes

The Figshare workbooks were downloaded separately for comparison at `2026-07-19T00:04:51Z`:

| Workbook | Figshare file ID / direct URL | Figshare bytes / MD5 / SHA-256 | Zenodo result |
|---|---|---|---|
| `barcode_sequences.xlsx` | `30793699` / [download](https://ndownloader.figshare.com/files/30793699) | 18,467 / `e74720abbde0667ec82c231e78f5b02b` / `2236aeb26acaa00608bc55a4b05c83630989e9cc9041d9471ea277c640b577f8` | Not byte-identical. Ordered non-empty rows are identical, including BC114/BC115. Zenodo is a later Excel save that removes one blank row before the IT-barcode section and changes workbook metadata/view XML. |
| `barcodes_species.xlsx` | `30793702` / [download](https://ndownloader.figshare.com/files/30793702) | 12,654 / `14cbf55db1245a9134dc5a396ab2ead5` / `26e77d511211e2726d6293f81ff522ce7c15c9d0257a23c21a56b4838bb03f48` | Not byte-identical. BC114/BC115 rows are identical. Zenodo adds `-` to header cell E1, removes a blank row before `no barcode`, and changes styles/metadata. |

The Zenodo workbook core properties show 2022-01-19 modifications, whereas Figshare exposes the 2021 files. The run must therefore be reproduced with the recorded Zenodo hashes, not with the Figshare workbook MD5s printed in the original plan.

## Subset construction

### BC115 exact-barcode subset

- Rule: exact `AGGTCTACCTCGCTAACACCACTG` or its reverse complement in the concatenated first 140 and last 140 read bases.
- Source: 238,407 reads; selected: 1,623.
- FASTQ: 3,075,082 bytes; SHA-256 `1b949778749de2b8a1898573fe5206b244d50bb21edeb9b9280518a470bba844`.
- Reference: `Gomphus_pulchellus_COI`, 657 bp; FASTA SHA-256 `cf6c1f75760be325c4309439768dad45fa6c256c9786ba3802a66e6fe3a214d4`; normalized sequence SHA-256 `84357c33c9391d3b8cd7f48f533b1af9177ec0f4df648d3d5a510d5ca887c3b2`.
- Subset manifest: 755 bytes; SHA-256 `92a8fd6b4bff8a10372415c22de3fcedbec1ea15f9ed040c25a834f7c16c9875`.

The complete executable Python text is retained in the JSON packet under `subset_construction.BC115_exact_barcode.script_text`.

### BC114 reference-conditioned subset

The exact-barcode stage produced 1,758 reads. Primary alignments were generated with:

```bash
/home/dalab/micromamba/bin/minimap2 -x map-ont --secondary=no \
  /tmp/biomodstack-phase2-public/BC114.reference.fasta \
  /tmp/biomodstack-phase2-public/BC114.supHAC.exact-barcode.fastq \
  > /tmp/biomodstack-phase2-public/BC114.paf \
  2> /tmp/biomodstack-phase2-public/BC114.minimap2.log
```

Reads were retained when PAF identity was at least 0.90, query coverage at least 0.70, and aligned block length at least 550 bp. This left 283 reads.

- PAF: 250,208 bytes; SHA-256 `97931cded8e726ffbecbf940c99f5776ce77a2b557057def2474dd8d3499c7df`.
- Selected FASTQ: 502,803 bytes; SHA-256 `0c0d88cc3ffb4029ba5b984faffcf2d0449ff1ba9c0d528267b41ba7e3e60407`.
- Reference: `Gomphus_kinzelbachi_COI`, 656 bp; FASTA SHA-256 `4c7774a8d1d422ed3f918520673dd0801b751ac22bd6226e4fc75187109bdb0b`; normalized sequence SHA-256 `a463eab9ddfe0c9b9e6f15a76d17fc6e16f9a8aaccee213084792920623ba567`.
- Subset manifest: 724 bytes; SHA-256 `209bf757906874ff1fad5e30dc796c216d35388596b7643d53d18c042a2abeb6`.

The complete filter script is retained in the JSON packet. Because the Sanger reference conditioned selection, this subset cannot support an unbiased biological-accuracy or concordance-rate claim.

## Runtime identity and thresholds

Both runs used the Nextflow **local** executor. No Apptainer/Docker profile was selected, so no container or image digest exists.

- Nextflow `25.10.0` build `10289`; workflow revision reported as `f88ceab55b`.
- Groovy `4.0.28`; execution JVM OpenJDK `17.0.19+10-1-22.04.2-Ubuntu`.
- Host kernel `7.0.11-76070011-generic`.
- Samtools/htslib `1.23.1`; minimap2 `2.31-r1302`.
- Verifier Python `3.11.14`; `biomodstack-construct-verifier 0.2.0`.
- Experimental profile `plasmid_strict_v1` v`1.0.0`. The two initial public runs used manifest profile SHA-256 `8956b4daa174c9f678d5556ae979f2a4c913c746cd903366ab08c871b776745b`. The final hardening replay used SHA-256 `90fad5ea643fc6509cd174020a52563c0a0ec4d38836328cd4bdc7eed9015553` after adding explicit `automatic_pass_eligible=false`. Both identities have `public_accuracy_validated=false`; historical run identities are retained per run rather than rewritten.
- Thresholds: coverage ≥0.99; depth ≥20; low-depth fraction ≤0.01; major allele fraction ≥0.90; variant support ≥0.80; strand dominance ≤0.95; unmapped fraction ≤0.10; ambiguous bases ≤0.001; origin-spanning reads ≥2; secondary anomaly fraction ≤0.02; zero variants required for `PASS`.

The run logs retain generated task scripts and the Nextflow revision, but not a complete content-addressed snapshot of the dirty worktree. The repository changed after execution. Current source hashes are therefore **not** presented as if they were historical run identities.

## Recorded runs

### BC115 — unfiltered exact-barcode smoke

- UTC marker: `2026-07-18T23:12:41Z` to `23:12:45Z`; Nextflow session `b75338b2-64c4-439c-84cb-014b9d619f23`, run `big_kilby`.
- Exit `0`; five tasks succeeded; Nextflow peak two running tasks/two CPUs. No trace captured CPU time or peak RSS.
- Verdict `REVIEW`: `CONTAMINATION_SCREEN_UNAVAILABLE`, `MIXED_ALLELES_DETECTED`, `VARIANT_SUPPORT_AMBIGUOUS`.
- Identity 85.0837%; 99 variants; coverage 100%; mean depth 1,010.5723×.
- `fastq_alignment_stats.tsv` reports 1,623 reads but 1,113 mapped plus 529 unmapped alignment records (1,642 total), including supplementary alignment semantics. The verifier correctly rejected those counts for contamination scoring instead of producing a false `PASS`.
- Manifest: 39,271 bytes; SHA-256 `e9857ab733766f1822d6393efc6f80250672f0f4603d5ab1d8befad50dee5b35`.
- Summary: 248 bytes; SHA-256 `aa9c3e7963528ea03dbb3ee96337168072b6a14a5dd9cea46d807a15c82b5775`.

### BC114 — reference-conditioned agreement

- UTC marker: `2026-07-18T23:19:33Z` to `23:19:37Z`; Nextflow session `33260103-bb78-4171-a517-e3a3a3df9655`, run `friendly_swartz`.
- Exit `0`; five tasks succeeded; Nextflow peak two running tasks/two CPUs. No trace captured CPU time or peak RSS.
- Verdict `FAIL`: `MIXED_ALLELES_DETECTED`, `VARIANTS_DETECTED`.
- Consensus 657 bp against a 656 bp Sanger record; identity 99.8478%.
- Variant: position 9, `A→AT`, depth 264, support 94.3182%.
- Coverage 100%; mean depth 279.9329×; 283/283 reads mapped.
- Manifest: 12,592 bytes; SHA-256 `0d28c5a656ef381087310c24772886afbdf27920cb83594df6a1c105efab7d95`.
- Summary: 207 bytes; SHA-256 `b204ceba4f9f7a01797570e99df1332016e02eb7b7a7b4fe97bce904eb1b53c1`.
- Consensus FASTA: 692 bytes; SHA-256 `fbb5192db3052dd49f31366a8f09844b077c315c80a94fe99f43edc6408d50ee`.

## Explicitly unavailable or unrun evidence

- No HAC download/run and no full HAC/SupHAC per-barcode confusion matrix.
- No immutable complete source snapshot for the executed dirty worktree.
- No container image identity because execution was host-local.
- No Nextflow trace, per-process CPU time, or peak RSS.
- No independent adjudication of the BC114 insertion.
- No unbiased BC114 concordance estimate because selection used the scoring reference.
- No production-threshold qualification.
- No long-plasmid benchmark or other scheduled public benchmark execution.

These gaps are why the packet records observed fail-closed execution while withholding a reproducible software-correctness PASS and all biological-accuracy qualification.

## Final hardening replay

After the scientific and alignment-session hardening, BC114 was replayed through the corrected exact-insertion-support workflow in `/tmp/biomodstack-phase2-public/BC114-final9-run`.

- Nextflow exit: **0** (`reverent_kirch`, Nextflow 25.10.0); final output was written `2026-07-19T03:38:06Z`.
- Verification verdict: **FAIL**.
- Reason codes: `MIXED_ALLELES_DETECTED`, `TOPOLOGY_EVIDENCE_INSUFFICIENT`, `VARIANTS_DETECTED`.
- Sequence identity: **0.9984779299847792**.
- Supported insertion: position 9, `A→AT`, exact inserted-allele support **0.9318181818181818** (**246/264**), depth **264**. The verifier now derives insertion allele and length directly from BAM CIGAR/query sequence rather than treating all insertions at the anchor as support for `T`.
- Coverage: **1.0**; unmapped fraction: **0.0**.
- The support-table/BAM recomputation gate and observed-consensus-to-BAM support binding were valid; no malformed-support or aggregate-insertion fallback was used.
- The experimental profile remained ineligible for automatic `PASS`, and insufficient topology evidence remained visible rather than being inferred from ordinary full-length linear alignments.

Final-replay SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `verification/qc_manifest.json` | `e3a7cedbc9c2a675cc8e66a367a10c934a5cdccc6342b89c83bf95850e117791` |
| `verification/verification_summary.tsv` | `dbc105f3327e6bc51ff119b1de1ba2d9db4a242217370514e1d7da5c4250b113` |
| `fastq_qc/fastq_alignment_stats.tsv` | `07366897fd3592aaab6bba08cc7c15cb1b462f04158256fe5f7de7e48b11a5b5` |
| `fastq_qc/fastq_consensus.fasta` | `fbb5192db3052dd49f31366a8f09844b077c315c80a94fe99f43edc6408d50ee` |
| `fastq_qc/per_base_support.tsv` | `285c08096fe6ba10618f951ab7a3d1d1e44ea277ac9af4e865426960bca03e0d` |

The final alignment-session probe returned primary `ready=true` with authoritative contig `Gomphus_kinzelbachi_COI`; manifest digest/size checks and the normalized-reference binding succeeded even though the minimap2 BAM lacked `@SQ M5`. The explicit `dimer_candidates` session remained `ready=false` because this sample emitted no dimer-candidate BAM/BAI; it did not fall back to primary artifacts.

This replay was host-local and did not capture a Nextflow trace, independent UTC timing marker, peak RSS, or CPU time. It therefore strengthens the fail-closed execution evidence but does **not** upgrade software correctness to a reproducible public-data `PASS`, technical concordance to an unbiased estimate, or biological accuracy to qualified status.
