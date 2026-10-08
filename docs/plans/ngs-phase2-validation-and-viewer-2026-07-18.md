# ONT/NGS Phase 2 validation and viewer acceptance plan (2026-07-18)

## Scope and evidence policy

This document defines Phase 2 validation and the repaired raw-read/alignment review surface. It does not qualify Phase 3 upstream workflow releases, basecaller/model overrides, image pinning, or runtime-patch retirement.

Three claims are always reported separately:

1. **Software correctness** — deterministic execution, schema validity, digest binding, fail-closed policy, and artifact completeness.
2. **Technical concordance** — agreement with an independent method or benchmark tool on the same data.
3. **Biological accuracy** — agreement with independently deposited biological truth such as Sanger references or validated control genomes.

A successful run is not biological validation. Missing, malformed, contradictory, or untrusted evidence cannot produce `PASS`.

## Public benchmark ladder

### P2-A — primary nightly accuracy benchmark

**Dataset:** Amplicon_sorter ONT amplicons, Figshare article `16627654` v1, DOI `10.6084/m9.figshare.16627654.v1`, CC0. A separate Zenodo archival record `6554346` carries the Dryad DOI `10.5061/dryad.zgmsbccd0`; it must not be called a byte-identical Figshare mirror.

| Figshare artifact/key | Size | Figshare-published MD5 |
|---|---:|---|
| `HAC_data.zip` | 311,967,930 B | `1edfb94faca5833a7a7f3257b4aeddca` |
| `supHAC_data.zip` | 216,334,298 B | `7a7a62704fac9baccff16479ddfce21b` |
| `Sanger_references.fasta` | 36,470 B | `a8a1a82d0e6f8f81ceba70d00f2bf68` |
| `barcode_sequences.xlsx` | 18,467 B | `e74720abbde0667ec82c231e78f5b02b` |
| `barcodes_species.xlsx` | 12,654 B | `14cbf55db1245a9134dc5a396ab2ead5` |

The recorded 2026-07-18 subsets used the Zenodo files named exactly `barcode_sequences.xlsx` (18,475 B; MD5 `a7a0bd1fcd9585a2444894fd8a9d818c`) and `barcodes_species.xlsx` (12,657 B; MD5 `a18413f4e6fd121891f3a1e2929f55f6`). Their BC114/BC115 rows agree with Figshare, but the workbook bytes differ because the Zenodo files are later Excel saves with small row/header/metadata changes. Reproduction must bind the registry and digest, not only the filename.

The deposits contain 29 Sanger reference records, 568–14,200 bp, plus barcode-to-sample maps. Those references are an independent-method deposit, but any subset selected against its scoring reference (as BC114 was in the recorded technical fixture) is reference-conditioned and cannot yield an unbiased concordance or biological-accuracy estimate. The paper's 98.2–100% identities are comparator results, not predeclared BioModStack expectations.

Required outputs, separately for HAC and SupHAC:

- per-barcode expected-target recovery;
- unexpected/cross-sample assignment rate;
- chimera and unassigned rates;
- circular/orientation-normalized consensus identity;
- normalized substitution, insertion, and deletion counts;
- coverage/read-support distributions;
- complete command/tool/version/input-digest provenance;
- software, concordance, and biological-accuracy conclusions in separate sections.

### P2-B — long-plasmid stress benchmark

**Dataset:** `plasmidsaurus/PLASMIDS01`, ENA study `PRJEB89039`, BioProject `PRJEB89039`, BioSample `SAMEA129836538`, CC0 metadata.

- 29 circular plasmids; PCR/Sanger-confirmed starting constructs.
- 68.7 GB raw data across 92 runs, about 8.9 million reads, construct sizes about 2.8–54.4 kb.
- Published aggregate benchmark: 27/29 error-free; GWHBAVO01000000/GenBank accessions provide deposited assemblies.
- DOI: `10.1038/s41597-025-05980-9`.

This is a scheduled/staging benchmark, not a per-commit job. Validate long-plasmid recovery, multimer handling, contamination evidence, topology evidence, and per-sample sequence concordance. Do not treat the paper's aggregate 27/29 result as a BioModStack pass threshold.

### P2-C — barcode/demultiplex concordance

**Dataset:** Oxford Nanopore OnRamp 16S tutorial, ENA study `PRJEB77421`, BioProject `PRJEB77421`, 29 barcode runs, 8.7 GB.

- Run accessions `ERR13297530`–`ERR13297558`.
- Published tutorial summary: 10/11 samples had more than 90% correctly assigned reads; some false positives were reported.

Use for demultiplexing and contamination/cross-assignment concordance. It does not provide construct-level biological truth and cannot qualify construct identity.

### P2-D — canonical regression/control genome

**Dataset:** Klever Lab ONT *E. coli* K-12 control dataset, `https://doi.org/10.17605/OSF.IO/MN9G2`, CC BY 4.0.

- Expected chromosome: RefSeq `NC_000913.3`.
- Includes raw POD5, Dorado FASTQ/BAM, and Canu/Flye assemblies.

Use for reproducible download/basecall/alignment/assembly smoke tests and reference concordance. It is a control-genome regression fixture, not plasmid topology truth.

### Lower-priority fixtures

- `ERR3817487` / ENA `PRJEB36276` is a lightweight read-QC regression fixture without independent construct truth.
- ONT DuBA/Genome-Assembly-Contest public FASTQs are runtime/interoperability fixtures until their expected references and checkable gold outputs are curated.

## Benchmark acquisition contract

Every dataset directory must contain:

- source URL, DOI/accession, retrieval timestamp, license, expected byte size, and published checksum where available;
- locally computed SHA-256 for every acquired object;
- a machine-readable sample/barcode-to-reference mapping;
- an immutable subset manifest if the full dataset is not used;
- an explicit truth-grade field: `independent`, `comparator_only`, or `none`.

Checksum mismatch, incomplete mapping, or ambiguous truth grade aborts the benchmark before scientific scoring.

## Metrics and reporting

### Software correctness gates

- all required Phase 2 manifests validate against the versioned JSON schema;
- server-controlled reference sequence digest matches the staged expected reference;
- all required artifacts exist, are regular files inside the job root, and match recorded SHA-256/size;
- rerunning the verifier on identical inputs produces an equivalent scientific payload;
- malformed/missing/untrusted inputs produce `REVIEW`; contradictory evidence produces `FAIL`;
- synthetic circular rotation and reverse-complement controls do not generate variants;
- synthetic SNV/INS/DEL controls produce stable normalized coordinates and alleles.

### Technical-concordance metrics

- consensus identity and edit counts versus an independent aligner/comparator;
- read/barcode assignment agreement and confusion matrix;
- coverage and mapped/unmapped read-count agreement;
- variant coordinate/allele agreement after circular normalization;
- topology/contamination agreement where an independent method exists.

### Biological-accuracy metrics

- exact expected-target recovery rate;
- per-sample sequence identity against independent truth;
- SNV, insertion, and deletion false-positive/false-negative counts;
- barcode cross-assignment and unexpected-organism/construct rate;
- confidence intervals and explicit excluded/ambiguous samples.

No production threshold may be promoted from `experimental` to `qualified` solely from synthetic fixtures. Threshold promotion requires review of at least P2-A and independent scientific approval recorded in the acceptance ledger.

## Repaired alignment viewer: Phase 2 contract

### Immediate repair retained in Phase 2

Embedded IGV.js remains the locus/alignment engine, but source selection must obey these rules:

1. The default `primary` session excludes paths identified as dimer, multimer, or concatemer evidence.
2. Dimer-candidate alignments are available only through an explicit `dimer_candidates` mode.
3. A BAM is usable only with its exact `.bai` or `.csi`; an unrelated generic index cannot satisfy readiness.
4. The reference and optional `.fai` are bound to the selected session.
5. Missing alignment, index, or reference is shown as an explicit non-ready state; no green/verified presentation is allowed.
6. Manifest verdict/check/variant rendering precedes path-scraped legacy reports.
7. Variant/check navigation must target the same manifest-bound reference coordinate system.

### Target job-scoped session architecture

The existing broad filesystem stream route and stage-output scraping are transitional. The accepted architecture is a job-scoped, server-authoritative viewer contract:

- `GET /api/jobs/{job_id}/alignment-sessions`
- `GET /api/jobs/{job_id}/alignment-sessions/{session_id}`
- `GET /api/jobs/{job_id}/alignment-artifacts/{artifact_id}` with HTTP range support
- `GET /api/jobs/{job_id}/reads?contig=&start=&end=&q=&cursor=&limit=` for paginated FASTQ/BAM read inspection
- optional later raw-signal endpoint for POD5/SLOW5 after a bounded server-side slice implementation exists

A session response contains opaque artifact IDs and same-origin URLs, not client-resolved filesystem paths. It binds:

- mode (`primary`, `dimer_candidates`, or another explicit scientific role);
- BAM/index/reference/index identities and digests;
- optional coverage/GC/split/soft-clip/junction tracks;
- manifest and threshold-profile identity;
- authorization scope, byte size, MIME type, and range capability;
- explicit unavailable reasons.

The backend must resolve canonical paths, enforce job ownership/authorization, reject symlink/path escape and special files, validate BAM/index/reference semantic compatibility, and stream with range/ETag/conditional-request support.

### Raw-read review requirements

The Phase 2 raw-read panel must support:

- paginated read name, length, mean quality, alignment locus/strand/MAPQ/CIGAR, and flag display;
- sequence and quality-string inspection on demand without loading the full FASTQ into browser memory;
- filtering by read ID and current locus;
- jump from a reported variant or failed check to the bound IGV locus;
- copy/download of one selected read through the job-scoped API;
- explicit distinction between basecalled FASTQ/BAM evidence and optional raw-signal availability.

POD5 electrical-signal visualization is not required for the initial Phase 2 acceptance because it needs bounded server-side signal slicing and basecall-move-table alignment. The UI must not imply that raw signal is available when only basecalled reads exist.

## Viewer acceptance tests

### Backend/security

- unauthorized/cross-job artifact access returns 403/404 without path disclosure;
- traversal, absolute-path injection, symlink escape, FIFO/device/socket, and stale artifact IDs fail closed;
- byte-range and conditional requests are correct;
- BAM/index/reference digests and semantic pairing are validated before `ready=true`;
- missing or malformed viewer-session metadata cannot influence construct `PASS`.

### Frontend/scientific behavior

- primary session never silently selects dimer/multimer/concatemer evidence;
- dimer mode is explicit and independently bound;
- mismatched index/reference yields a visible non-ready state;
- five verification checks, authoritative verdict/reason codes, normalized variants, and threshold profile are visible;
- variant-to-locus navigation uses the same coordinate system as the manifest;
- large files are range-loaded/paginated; no full BAM/FASTQ browser fetch;
- older runs retain an explicit legacy/unavailable state rather than appearing failed or verified.

## Gate ordering

1. Pure verifier/topology/input-bundle tests.
2. Schema and API/provenance/security contracts.
3. Frontend manifest/viewer source-selection contracts and TypeScript.
4. Nextflow preview/parse checks.
5. Synthetic runtime with exact-match, rotation, reverse complement, SNV, insertion, deletion, contamination, incomplete coverage, missing topology, and malformed evidence.
6. P2-A public benchmark and separate correctness/concordance/accuracy report.
7. Optional P2-B staging run.
8. Acceptance-ledger update, immutable candidate freeze, independent software/security/scientific review, and one scoped Phase 2 commit.
