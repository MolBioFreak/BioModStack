# BioModStack MolBio Read-QC Harmonization Implementation Plan

> **For Hermes:** Use `subagent-driven-development` before turning any phase below into code. This document is a roadmap/spec, not a direct implementation log.

**Goal:** Expand BioModStack's molecular biology and nanopore surfaces into a unified, read-aware construct validation workbench that can inspect individual reads, summarize high-coverage evidence, expose modified DNA calls, and generate Plasmidsaurus-class QC/report bundles without fake or placeholder deliverables.

**Architecture:** Keep the current role split: SeqViz remains the construct/map editor, IGV.js remains the read-pileup viewer, and the API/workflow layer becomes responsible for typed sequencing artifact manifests, per-read/per-base evidence tables, variant/consequence summaries, modified-base tracks, and report bundles. The NGS page and any harmonized MolBio read-evidence surface should consume the same typed read-QC contract instead of duplicating path scraping, but the existing standalone MolBio Toolkit must remain a stable production surface.

**Tech Stack:** React 19, SeqViz, IGV.js, Plotly, FastAPI, SQLAlchemy, Nextflow, Dorado, modkit, minimap2, samtools, Biopython, existing BMS job/stage artifact services.

**Plan placement:** This file belongs in `docs/plans` under the policy in `docs/plans/README.md`: canonical product/runtime behavior remains in `docs/*.md`, and this file is an active rollout/spec artifact until implemented or archived.

**Non-disruption rule:** `/designer`, `MolBioToolkitV2.tsx`, `SequenceViewer.tsx`, the existing MolBio panels, `/api/molbio`, and `/api/sequences` are production contracts. No harmonization phase may remove controls, change default behavior, or make the current standalone MolBio viewer/tools depend on read-QC artifacts. If integration risks state, layout, routing, or API compatibility, duplicate the necessary viewer/panel shell under an experimental namespace/route and keep it opt-in until promoted after regression tests and explicit approval.

**2026-04-27 NGS TLC audit delta:** Phase 0 is now partly implemented and locally smoke-tested. The current repo has a `qc_manifest.json` generator/parser/API surface, sequence-QC OpenAPI routes, FASTQ support-table/script tests, and the NGS page polling/navigation fixes captured in `docs/reports/2026-04-27-ngs-toolkit-tlc-audit.md`. The next execution tranche should therefore start with manifest-first UI consumption, explicit artifact-state semantics, and component decomposition before adding larger read-ledger/report features.

---

## 1. Current source-grounded inventory

### 1.1 Existing product surfaces

The canonical docs already say BioModStack has both MolBio and sequencing surfaces:

- `docs/Lab_Automation_MolBio_and_Sequencing.md:6-35` describes `/designer`, `MolBioToolkitV2.tsx`, `/api/sequences`, and `/api/molbio` as the current construct-editing and operation surface.
- `docs/Lab_Automation_MolBio_and_Sequencing.md:37-66` describes `/ngs`, `NGSToolkit.tsx`, `NanoporeTemplate.tsx`, POD5/BAM/FASTQ inputs, Dorado, modkit summaries, FASTQ plasmid QC, and IGV-ready artifacts.
- `platform/frontend/src/App.tsx:24-27` exposes `/designer` for MolBioToolkit and `/ngs` for NGSToolkit.

### 1.2 MolBio Toolkit: strong construct editor, not yet read-QC workbench

Existing frontend foundation:

- `platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx` is the unified workbench with construct shelf, import/paste flows, panels for alignment, assembly, edit, digest, PCR, primers, features, history, and RNA structure.
- `platform/frontend/src/components/MolBioToolkit/SequenceViewer.tsx` wraps SeqViz and renders sequence, annotations, primers, translations, selection, linear/circular/both viewer modes, and highlighted regions.
- `platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx` uses Plotly for sequence-level analytics.
- `platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx` calls `alignMolBioSequences(...)`, displays pairwise alignment output, and can annotate detected variants as features.

Existing backend foundation:

- `platform/api/routers/nucleotide_sequences.py:25-75` defines feature, primer, and analysis-track schemas.
- `platform/api/routers/nucleotide_sequences.py:77-152` defines create/update/response schemas for nucleotide sequence records.
- `platform/api/routers/nucleotide_sequences.py:228-253` normalizes per-base `analysis_tracks` arrays, which is useful for small construct-scale tracks but not sufficient for high-depth read evidence.
- `platform/api/routers/molbio_ops.py:258-320` defines sequence-alignment request/response schemas, including variants.
- `platform/api/routers/molbio_ops.py:872-896` exposes `POST /api/molbio/alignment`.
- `platform/api/services/sequence_alignment.py:100-120` builds a Biopython `PairwiseAligner` with global/local/placement semantics.
- `platform/api/services/sequence_alignment.py:264-358` detects substitution/insertion/deletion events in pairwise alignments.
- `platform/api/services/sequence_alignment.py:516-530` is the public `align_sequences(...)` entrypoint.

Interpretation:

- This is a real construct editor and pairwise comparator.
- It is not yet a sequencing-validation workbench: there is no read table, no read ledger, no VCF/BCF-style variant artifact, no sequencing-run schema, and no per-read modified-base drilldown tied to a construct record.

### 1.3 NGS/Nanopore: real run inspector with IGV and aggregate QC

Existing frontend foundation:

- `platform/frontend/src/components/NGSToolkit.tsx` launches nanopore workflows, lists runs, inspects stage outputs, shows logs, resolves artifacts, opens IGV, and parses FASTQ QC/methylation artifacts.
- `platform/frontend/src/components/NGSToolkit.tsx:3372-3701` initializes IGV, loads BAM/BAI read tracks, and can load auxiliary tracks for coverage depth, position gradient, GC content, GC z-score, split-read density, soft-clip density, and junction hotspots.
- `platform/frontend/src/components/NGSToolkit.tsx:3628-3650` configures the read track with coverage, soft clips, mismatches, insertion text, display mode, color-by, and group-by controls.
- `platform/frontend/src/components/NGSToolkit.tsx:3645-3647` sets `samplingDepth` and `maxRows`, so high-coverage visualization is intentionally capped/sampled.
- `platform/frontend/src/components/NGSToolkit.tsx:4172-4367` displays FASTQ/multimer QC downloads, read-length histogram, candidate preview, alignment stats, consensus preview, and artifact paths.
- `platform/frontend/src/components/NGSToolkit.tsx:4369-4780` displays modkit methylation downloads, summary tables, motif-targeted plots, reference highlights, coverage filters, and motif calls.

Existing launch/config foundation:

- `platform/frontend/src/components/NanoporeTemplate.tsx` supports POD5/BAM/FASTQ input source selection, reference FASTA selection/paste/create/save, modified-base options, Dorado settings, FASTQ plasmid QC options, minimap2 preset/secondary controls, expected plasmid size, IGV report window/max-site/flank controls, and optional wf-clone assembly controls.
- `platform/api/config/models/nanopore.yaml:4-11` defines the Nanopore Sequencing model as POD5/BAM methylation plus FASTQ plasmid QC.
- `platform/api/config/models/nanopore.yaml:94-104` exposes FASTQ and reference FASTA parameters.
- `platform/api/config/models/nanopore.yaml:180-243` exposes FASTQ QC, minimap2, and IGV report parameters.
- `nextflow.config:974-1038` defines nanopore defaults, including `run_modkit`, `run_fastq_qc`, `expected_plasmid_size`, minimap2 settings, IGV settings, and wf-clone defaults.
- `nextflow.config:1247-1258` defines the `nanopore_methylation` profile.

Existing workflow foundation:

- `ngs.nf` routes nanopore runs into `NANOPORE_METHYLATION`.
- `workflows/nanopore_methylation.nf:38-83` validates exactly one primary input and requires `reference_fasta` for FASTQ analysis.
- `workflows/nanopore_methylation.nf:178-230` runs `FastqAlign` and `FastqPlasmidQC`, then reports FASTQ QC artifacts.
- `workflows/nanopore_methylation.nf:234-255` runs modkit for POD5/BAM paths, with pileup only when a reference exists.
- `modules/dorado.nf:13-63` performs Dorado basecalling and can request modified-base models.
- `modules/dorado.nf:69-120` aligns BAM and preserves MM/ML methylation tags by comment/pipe structure.
- `modules/dorado.nf:226-278` emits `methylation.bed`, `pileup.log`, `modkit_summary.tsv`, and `summary.log`.
- `modules/dorado.nf:282-319` aligns FASTQ to reference using minimap2 and emits `aligned.bam`, `aligned.bam.bai`, `reference.fasta`, and `reference.fasta.fai`.
- `modules/dorado.nf:323-356` declares FASTQ QC outputs including read lengths, summaries, coverage, IGV tracks, report files, and consensus FASTA/logs.
- `modules/dorado.nf:387-505` computes read-length metrics, mapping stats, coverage, consensus, and fallback consensus logic.
- `modules/dorado.nf:508-528` calls `scripts/build_fastq_igv_tracks.py`.
- `modules/dorado.nf:533-613` writes `igv_track_config.json` with BAM and auxiliary tracks.
- `scripts/build_fastq_igv_tracks.py:17-40` defines track-builder CLI outputs.
- `scripts/build_fastq_igv_tracks.py:84-186` streams mapped reads via `samtools view`, counts split/softclip evidence, and reads coverage TSV.
- `scripts/build_fastq_igv_tracks.py:384-471` writes bedGraph/BED/TSV track artifacts.

Interpretation:

- The sequencing side is already more than a placeholder: it has real alignment, consensus, coverage, IGV, and modkit outputs.
- It is still path/artifact oriented rather than data-model oriented.
- It produces aggregate evidence, not a stable per-read/per-base evidence contract that the MolBio editor can consume.

### 1.4 Viewer split

The intended viewer split remains:

- IGV.js should handle read-to-reference inspection, BAM/CRAM pileups, coverage, mismatch review, variant-support evidence, methylation/auxiliary tracks, and consensus review.
- SeqViz should remain the plasmid/construct editor.
- Construct-to-construct compare and MSA should not be forced through IGV.
- Read evidence should be reachable from the MolBio workspace, not only from NGS job pages.
- Circular plasmid handling must account for origin-spanning reads and variants.

This plan keeps that split and adds the missing typed data/report layer between workflows and UI.

### 1.5 Existing tests are thin for sequencing-QC product promises

Current tests relevant to this surface:

- `platform/api/tests/test_nanopore_nextflow.py` verifies the nanopore entrypoint exists and that `build_nextflow_command(...)` routes POD5/BAM parameters into `ngs.nf`/`nanopore_methylation`.
- `platform/api/tests/test_sequence_alignment.py` verifies the pairwise alignment service avoids counting all optimal alignments when Biopython can overflow.
- `platform/frontend/tests/alignmentLabels.test.ts` checks UI naming/copy for alignment labels.
- `platform/frontend/tests/molBioViewerLayout.test.ts` checks MolBio viewer layout defaults.

Missing tests:

- No synthetic FASTQ-to-reference artifact integration test.
- No test for `scripts/build_fastq_igv_tracks.py` outputs.
- No test for FASTQ QC summary schema, consensus behavior, per-base support, variant table, or report bundle.
- No frontend test for NGSToolkit FASTQ QC interpretation or MolBio read-evidence integration.
- No test for modified-base visualization artifacts beyond modkit command routing.

---

## 2. Comparator/product baseline

External Plasmidsaurus whole-plasmid page observed on 2026-04-25 positions its service around:

- full-coverage long-read plasmid sequencing;
- easy insights highlighting mismatches, duplications, and errors;
- basecalls for each position across sequencing reads;
- mutation, insertion, deletion review with amino-acid consequences;
- consensus sequence deliverables in `.fasta` and `.gbk`;
- plasmid map `.html`;
- read-length histogram `.png`;
- virtual gel `.png`;
- trace artifact `.ab1`/`.gbk`;
- coverage plot `.png`/`.gbk`;
- per-base `.txt`/`.tsv`;
- raw reads `.fastq.gz`;
- average coverage in a summary TSV;
- coverage over roughly 20x as a strong-consensus rule of thumb;
- mixture/mixed-peak behavior surfaced in `.ab1` when species are similar.

BioModStack does not need to clone every Plasmidsaurus artifact literally, but any “Plasmidsaurus-class” claim must be backed by equivalent evidence. If BMS does not generate AB1/mixed-peak trace artifacts, the report must say so and provide the alternate evidence source, such as allele-fraction/per-base support tables plus IGV loci.

---

## 3. Gap map: current BMS vs target

### 3.1 Already present and worth preserving

- Construct editing and annotation: SeqViz-backed MolBio Toolkit.
- Pairwise alignment with variant events: `/api/molbio/alignment` and `AlignmentPanel`.
- Nucleotide construct store with features, primers, and per-base `analysis_tracks`.
- Nanopore launch UI for POD5/BAM/FASTQ, reference handling, Dorado/modkit/FASTQ QC parameters.
- FASTQ-to-reference alignment via minimap2.
- BAM/BAI/reference generation for IGV.
- Aggregate FASTQ QC summaries, read lengths, coverage, consensus FASTA, IGV tracks, and optional IGV report HTML.
- Modkit methylation BED and summary outputs for POD5/BAM runs.
- NGSToolkit run inspector, log viewer, IGV modal, methylation report, and FASTQ read-length/multimer charting.

### 3.2 Missing for Geneious/Plasmidsaurus-class read inspection

1. Typed run/artifact contract
   - Current stage reporting is mostly path lists.
   - Need a versioned manifest that names artifact kind, schema, source stage, coordinate system, reference identity, index path, byte size/checksum where feasible, and UI purpose.

2. Per-read ledger
   - Current FASTQ QC has `read_lengths.tsv` and limited candidate previews.
   - Legacy `FastqDimerAnalysis` can emit `dimer_read_ledger.tsv`, but the active nanopore workflow does not call it.
   - Need a general ledger for every read: read id, length, qscore if available, MAPQ, aligned reference/span, strand, soft clips, split/supplementary flags, identity/error burden if computable, variant/support flags, and source FASTQ/BAM.

3. Per-base support table
   - Current coverage TSV is depth-only.
   - Need per-position support: reference base, depth, strand depth, A/C/G/T/N counts, insertion/deletion support, consensus base, allele fractions, low-coverage flag, ambiguity flag, and quality/confidence field if supported.

4. Variant and consequence report
   - Pairwise JSON variants exist for two sequences.
   - Need sequencing-derived substitution/insertion/deletion table from read evidence and consensus, optionally VCF/BCF later.
   - For annotated CDS features, report amino-acid consequence; otherwise explicitly mark `not_evaluated`.

5. Modified-base visibility
   - Current modkit outputs are site/motif aggregate views.
   - Need modified-base tracks suitable for IGV plus per-site/per-read/haplotype-style summaries where MM/ML tags are present.

6. High-coverage scaling
   - Current IGV view uses caps/sampling and several scripts use per-base arrays/bedGraphs.
   - Need explicit high-depth policy: downsampling, binned summaries, indexed tracks, sensible defaults for 500x plasmid data, and a UI that separates “view every read near a locus” from “summarize all reads globally”.

7. Unified MolBio/NGS navigation
   - NGS can inspect reads, but MolBio constructs cannot yet attach/import a sequencing evidence bundle and jump between SeqViz coordinates and IGV evidence.

8. Plasmid report bundle
   - Current FASTQ QC output is useful but not a polished report contract.
   - Need a single report bundle with summary, pass/warn/fail calls, consensus, variants, coverage, read stats, modified-base status, contamination/multimer notes, IGV links, and exportable files.

9. Deliverable parity and truthful exclusions
   - Missing or not proven: consensus `.gbk`, plasmid map HTML, read-length histogram PNG, virtual gel PNG, trace artifact AB1/GBK, coverage PNG/GBK, raw FASTQ handoff/copy, formal `SAMPLE_summary.tsv`, Q60 estimate, mixed-peak representation.
   - These must be planned or explicitly listed as intentionally out of scope.

---

## 4. Target architecture

### 4.1 Role split

- SeqViz/MolBio Toolkit: authoritative construct editor, feature/primer editing, construct compare, selection, and report launch context after promotion; until then, production `/designer` remains standalone and read-QC launch context lives in an experimental surface.
- IGV.js/NGSToolkit: read-pileup inspection, BAM/BAI/reference browsing, auxiliary tracks, modified-base and variant support loci.
- API services: typed artifact discovery, manifest parsing, read-QC summary APIs, report-bundle generation, persistence of provenance.
- Nextflow/scripts: generation of raw evidence artifacts from FASTQ/BAM/POD5 runs.

### 4.2 New typed artifact contract

Create a versioned manifest, for example:

- `qc_manifest.json`
- `artifact_schema_version`: integer
- `job_id`
- `sample_name`
- `reference`: name/path/length/checksum when available
- `consensus`: path/status/method/fallback/length/checksum
- `alignment`: BAM/BAI/reference/FAI paths
- `read_ledger`: path/schema/row_count
- `per_base_support`: path/schema/row_count
- `variants`: path/schema/row_count
- `modified_bases`: modkit summary/BED/track paths/codes
- `igv_tracks`: typed list of coverage/GC/split/softclip/hotspot/modification tracks
- `reports`: HTML/PDF/PNG/GBK/FASTA/log paths
- `interpretation`: pass/warn/fail metrics, thresholds, and notes

This manifest should be generated by the workflow and parsed by the API. It should not depend on frontend heuristics over path names.

### 4.3 Data persistence policy

Do not put high-coverage read evidence into `NucleotideSequence.analysis_tracks`. That field is a small construct record JSON column and should stay for lightweight per-base annotations.

For sequencing QC:

- Store large artifacts as files under the job output directory.
- Store only typed manifest metadata and summary rows in the database if persistence is needed.
- Add a route to resolve/stream manifest-backed artifacts through existing file streaming safeguards.
- Keep raw BAM/FASTQ/large TSVs out of ordinary construct records.

Candidate new API modules:

- `platform/api/schemas/sequence_qc.py`
- `platform/api/services/sequence_qc_manifest.py`
- `platform/api/services/sequence_qc_report.py`
- `platform/api/routers/sequence_qc.py`
- tests under `platform/api/tests/test_sequence_qc_manifest.py`, `test_sequence_qc_report.py`, and expanded `test_nanopore_nextflow.py`

Candidate frontend modules:

- `platform/frontend/src/components/NGSToolkit/SequenceQcSummary.tsx`
- `platform/frontend/src/components/NGSToolkit/SequenceQcVariantTable.tsx`
- `platform/frontend/src/components/NGSToolkit/SequenceQcReadLedger.tsx`
- `platform/frontend/src/components/MolBioToolkit/experimental/MolBioReadQcWorkbench.tsx`
- `platform/frontend/src/components/MolBioToolkit/experimental/ReadEvidencePanel.tsx`
- `platform/frontend/src/components/MolBioToolkit/utils/readEvidenceLinks.ts`
- API helpers in `platform/frontend/src/lib/api.ts`
- optional route registration in `platform/frontend/src/App.tsx` for an experimental path; do not repoint `/designer`.

---

## 5. Phased roadmap

### Phase 0: Make the current artifact surface truthful and typed

**Objective:** Add a typed read-QC artifact manifest around existing outputs before adding new biology.

**Files:**

- Modify: `modules/dorado.nf`
- Modify: `workflows/nanopore_methylation.nf`
- Modify: `platform/api/config/models/nanopore.yaml`
- Create: `platform/api/schemas/sequence_qc.py`
- Create: `platform/api/services/sequence_qc_manifest.py`
- Create: `platform/api/routers/sequence_qc.py`
- Modify: `platform/api/main.py` or router-registration surface, as appropriate
- Test: `platform/api/tests/test_sequence_qc_manifest.py`
- Test: `platform/api/tests/test_nanopore_nextflow.py`

**Implementation notes:**

1. Extend FASTQ QC to write `qc_manifest.json` listing the artifacts it already creates.
2. Extend modkit stages to list `methylation.bed`, `modkit_summary.tsv`, logs, and reference linkage in the same manifest family.
3. Add an API parser that validates manifest schema without reading huge artifacts into memory.
4. Add an endpoint such as `GET /api/sequence-qc/jobs/{job_id}/manifest` or attach this under the existing job artifact model if that is cleaner.
5. Update `nanopore.yaml` outputs so they describe the actual HTML/JSON/FASTA/BAM/index/log artifacts, not only BAM/BED/TSV.

**Acceptance gates:**

- Existing nanopore jobs still launch with no behavior regression.
- For a completed FASTQ QC output directory, the API can return a typed manifest with all existing artifacts named.
- Missing optional artifacts are represented as absent/optional, not as fake paths.
- `reference_copy_fallback` consensus is represented as a failure/low-confidence state and cannot be mistaken for verified consensus.
- Tests cover manifest parsing, missing optional files, malformed manifest, and path normalization.

**Verification commands:**

- `python -m pytest platform/api/tests/test_nanopore_nextflow.py platform/api/tests/test_sequence_qc_manifest.py -q`
- `git diff --check -- modules/dorado.nf workflows/nanopore_methylation.nf platform/api/config/models/nanopore.yaml platform/api/schemas/sequence_qc.py platform/api/services/sequence_qc_manifest.py platform/api/routers/sequence_qc.py platform/api/tests/test_sequence_qc_manifest.py platform/api/tests/test_nanopore_nextflow.py`

### Phase 1: Add per-base support and variant candidate artifacts

**Objective:** Convert FASTQ QC from coverage-only to base-support/variant evidence.

**Files:**

- Modify or split: `scripts/build_fastq_igv_tracks.py`
- Create: `scripts/build_fastq_support_tables.py`
- Modify: `modules/dorado.nf`
- Test: `scripts/test_build_fastq_support_tables.py`
- Test: `platform/api/tests/test_nanopore_fastq_qc_contract.py`

**Outputs:**

- `per_base_support.tsv`
- `variant_candidates.tsv`
- optional later: `variants.vcf`
- manifest entries for each output

**Minimum `per_base_support.tsv` columns:**

- `chrom`
- `position_1based`
- `reference_base`
- `depth`
- `forward_depth`
- `reverse_depth`
- `a_count`
- `c_count`
- `g_count`
- `t_count`
- `n_count`
- `insertion_count`
- `deletion_count`
- `consensus_base`
- `major_allele_fraction`
- `low_coverage`
- `ambiguous`

**Minimum `variant_candidates.tsv` columns:**

- `chrom`
- `start_1based`
- `end_1based`
- `type`
- `reference`
- `alternate`
- `depth`
- `alt_count`
- `alt_fraction`
- `strand_bias_flag`
- `low_coverage_flag`
- `feature_id`
- `feature_name`
- `cds_consequence`
- `aa_consequence`
- `interpretation`

**Acceptance gates:**

- Synthetic reads with a known SNP produce exactly one SNP row.
- Synthetic reads with a known insertion and deletion produce expected indel rows.
- Low/no-coverage regions are flagged and do not get overconfident consensus calls.
- `fastq_consensus.fasta` and `per_base_support.tsv` agree on consensus bases for covered positions.
- No Q60 or high-confidence consensus claim is emitted unless a real quality/confidence calculation exists.

**Verification commands:**

- `python -m pytest scripts/test_build_fastq_support_tables.py platform/api/tests/test_nanopore_fastq_qc_contract.py -q`
- `python -m pytest platform/api/tests/test_nanopore_nextflow.py -q`

### Phase 2: Add a read ledger and high-coverage policy

**Objective:** Make individual reads inspectable and make 500x-style coverage manageable.

**Files:**

- Create: `scripts/build_fastq_read_ledger.py`
- Modify: `modules/dorado.nf`
- Modify: `nextflow.config`
- Modify: `platform/api/config/models/nanopore.yaml`
- Create: `platform/api/services/sequence_qc_read_ledger.py`
- Test: `scripts/test_build_fastq_read_ledger.py`
- Test: `platform/api/tests/test_sequence_qc_read_ledger.py`

**Minimum `read_ledger.tsv` columns:**

- `read_id`
- `source_file`
- `length`
- `qscore`
- `mapped`
- `primary`
- `secondary`
- `supplementary`
- `mapq`
- `strand`
- `chrom`
- `start_1based`
- `end_1based`
- `softclip_left`
- `softclip_right`
- `matches`
- `mismatches`
- `insertions`
- `deletions`
- `identity_pct`
- `variant_support_count`
- `notes`

**High-coverage policy:**

- The global report summarizes all reads.
- IGV defaults to sampled display with explicit UI wording.
- Per-locus drilldown can fetch windows, not entire tables.
- Binned/global tracks use bedGraph initially; bigWig/bigBed can be a follow-up if file sizes become painful.
- Add `max_read_ledger_preview_rows`, `qc_track_window_bp`, and `qc_high_depth_threshold` config knobs if needed.

**Acceptance gates:**

- A synthetic 500x-ish fixture can generate/read summary metrics without UI or API trying to load every read row into memory.
- API pagination/range filtering returns read ledger subsets by locus and flag.
- UI never renders unbounded read rows by default.
- Report makes it clear when IGV is sampled/capped.

**Verification commands:**

- `python -m pytest scripts/test_build_fastq_read_ledger.py platform/api/tests/test_sequence_qc_read_ledger.py -q`

### Phase 3: Surface read-QC evidence through an opt-in experimental UI

**Objective:** Stop treating read evidence as an NGS-only page without destabilizing the standalone MolBio Toolkit. The first MolBio-facing read-evidence surface must be an opt-in experimental tab/route or duplicated workbench shell, not a mutation of the production `/designer` default.

**Files:**

- Modify: `platform/frontend/src/lib/api.ts` only to add non-breaking sequence-QC client helpers.
- Create: `platform/frontend/src/components/NGSToolkit/SequenceQcSummary.tsx`
- Create: `platform/frontend/src/components/NGSToolkit/SequenceQcVariantTable.tsx`
- Create: `platform/frontend/src/components/NGSToolkit/SequenceQcReadLedger.tsx`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx` only to add an opt-in/experimental QC tab; existing launch/run-inspector behavior must remain unchanged.
- Create: `platform/frontend/src/components/MolBioToolkit/experimental/MolBioReadQcWorkbench.tsx` if a duplicated shell is safer than touching `MolBioToolkitV2.tsx`.
- Create: `platform/frontend/src/components/MolBioToolkit/experimental/ReadEvidencePanel.tsx`
- Modify: `platform/frontend/src/App.tsx` only to add an experimental route such as `/designer/read-qc-lab` or another clearly labeled opt-in route.
- Avoid modifying: `platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx`, `SequenceViewer.tsx`, and existing production panels unless the change is strictly additive, feature-gated off by default, and covered by regression tests.
- Create: `platform/frontend/tests/sequenceQcSummary.test.ts`
- Create: `platform/frontend/tests/molBioReadEvidenceExperimental.test.ts`
- Preserve: `platform/frontend/tests/alignmentLabels.test.ts` and `platform/frontend/tests/molBioViewerLayout.test.ts`

**UX requirements:**

- `/designer` remains the existing standalone MolBio viewer/toolkit.
- The harmonized MolBio read-evidence workbench is visibly labeled experimental and opt-in.
- NGSToolkit Run Inspector can expose a QC Summary tab based on the manifest, but existing run launch/log/artifact behavior is unchanged.
- Variant rows link to IGV loci.
- Per-base support can be filtered by low coverage, ambiguity, and variant support.
- Read ledger can be paged/filtered by locus, MAPQ, variant support, split/softclip status.
- The experimental MolBio surface can attach a sequencing job/manifest to the active construct and open the relevant IGV view.
- SeqViz selection and IGV locus navigation should be linked where feasible inside the experimental surface only until promotion.

**Acceptance gates:**

- Existing `/designer` behavior is visually and functionally unchanged unless the user deliberately enters the experimental route/tab.
- Existing MolBio alignment, edit, digest, PCR, primer, feature, assembly, history, search, and RNA-structure panels remain available and pass their current tests.
- Users can start from the experimental construct surface, choose a read-QC job, and jump to evidence at a selected coordinate.
- Users can start from an NGS job, view summary/variants, and open the corresponding construct/reference in the experimental MolBio surface when available.
- No UI path claims missing artifacts are present.
- Large read ledgers are paged or summarized; no full-table browser lockup.
- Promotion from experimental to production requires a separate approval step after regression testing.

**Verification commands:**

- `cd platform/frontend && npm run build`
- `node --test platform/frontend/tests/sequenceQcSummary.test.ts platform/frontend/tests/molBioReadEvidenceExperimental.test.ts platform/frontend/tests/alignmentLabels.test.ts platform/frontend/tests/molBioViewerLayout.test.ts` if these tests are authored as Node-compatible source tests, or use the existing project test invocation once standardized.

### Phase 4: Modified DNA evidence upgrade

**Objective:** Treat modified bases as first-class evidence, not just a modkit summary download.

**Files:**

- Modify: `modules/dorado.nf`
- Modify: `workflows/nanopore_methylation.nf`
- Create: `scripts/build_modbase_tracks.py`
- Create: `scripts/build_modbase_read_summary.py`
- Modify: `platform/api/schemas/sequence_qc.py`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx` or split methylation components from it
- Create: `platform/frontend/src/components/NGSToolkit/ModifiedBaseInspector.tsx`
- Test: `scripts/test_build_modbase_tracks.py`
- Test: `platform/api/tests/test_sequence_qc_modified_bases.py`

**Outputs:**

- modification-specific bedGraph/BED tracks where practical;
- per-site modification summary with coverage and percent modified;
- optional per-read modification summary when MM/ML tags are available;
- manifest entries linking modified-base outputs to the BAM/reference they came from.

**Acceptance gates:**

- POD5/BAM workflows with MM/ML tags expose modkit summary, BED, and visualization tracks.
- FASTQ-only runs clearly mark modified-base analysis as unavailable, not failed.
- UI can filter by modification code, motif, coverage, strand, and percent modified.
- Per-read modified-base drilldown is added only if the generated data supports it; otherwise the UI labels the current view as aggregate/site-level.

**Verification commands:**

- `python -m pytest scripts/test_build_modbase_tracks.py platform/api/tests/test_sequence_qc_modified_bases.py -q`
- `python -m pytest platform/api/tests/test_nanopore_nextflow.py -q`

### Phase 5: Report bundle and deliverable parity

**Objective:** Generate a single QC bundle that can answer “is my construct correct?” without sending the user through scattered downloads.

**Files:**

- Create: `scripts/build_plasmid_qc_report.py`
- Modify: `modules/dorado.nf`
- Create: `platform/api/services/sequence_qc_report.py`
- Modify: `platform/api/config/models/nanopore.yaml`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx`
- Create: `platform/frontend/src/components/NGSToolkit/PlasmidQcReportCard.tsx`
- Test: `scripts/test_build_plasmid_qc_report.py`
- Test: `platform/api/tests/test_sequence_qc_report.py`

**Bundle should include, when available:**

- `SAMPLE_summary.tsv` or BMS-equivalent summary TSV with stable schema.
- `consensus.fasta`.
- `consensus.gbk` if annotations/reference features are available.
- `plasmid_map.html` or an honest “not generated” marker.
- `read_length_histogram.png`.
- `coverage_plot.png`.
- `per_base_support.tsv`.
- `variant_candidates.tsv` and/or `variants.vcf`.
- `read_ledger.tsv` or indexed/paginated equivalent.
- raw FASTQ link/copy policy.
- IGV report HTML and track config.
- modbase report artifacts where applicable.
- `report.html` with pass/warn/fail summary and explicit caveats.

**Truthfulness rules:**

- No AB1 trace artifact is advertised unless actually generated.
- If BMS chooses not to synthesize AB1, report “BMS does not emit AB1; use per-base support/allele fraction and IGV loci instead.”
- No Q60 or Q-score-like consensus claim unless the method is implemented and tested.
- Reference-copy fallback consensus must fail “verified construct” status.
- Any virtual gel must be either real derived output or explicitly out of scope.

**Acceptance gates:**

- A synthetic “perfect plasmid” fixture produces pass status, consensus, coverage, read-length plot, and no variants.
- A synthetic SNP/indel fixture produces warn/fail status and a variant table with correct coordinates.
- A low-coverage fixture produces fail/low-confidence status.
- A fallback-reference fixture fails verified-consensus status.
- Report bundle has a manifest with all generated/missing artifacts and reasons.

**Verification commands:**

- `python -m pytest scripts/test_build_plasmid_qc_report.py platform/api/tests/test_sequence_qc_report.py -q`
- `python -m pytest platform/api/tests/test_nanopore_nextflow.py -q`

### Phase 6: Construct compare, MSA, and dimer/multimer harmonization

**Objective:** Keep read evidence, construct compare, and multi-sequence review separate but interoperable.

**Files:**

- Modify: `platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx`
- Create: `platform/frontend/src/components/MolBioToolkit/panels/ConstructComparePanel.tsx`
- Create: `platform/frontend/src/components/MolBioToolkit/panels/MsaPanel.tsx`
- Modify: `platform/api/routers/molbio_ops.py`
- Create/modify backend compare/MSA services as needed.
- Revisit `modules/dorado.nf` `FastqDimerAnalysis` and decide whether to activate, replace, or retire its legacy dimer ledger outputs.

**Acceptance gates:**

- Pairwise construct compare stays plasmid-aware and circular-origin-aware.
- MSA is not forced through IGV.
- Dimer/multimer evidence feeds the same report/manifest vocabulary as other read-QC evidence.
- Any legacy output activated from `FastqDimerAnalysis` is tested and documented; otherwise keep it out of production claims.

**Verification commands:**

- `python -m pytest platform/api/tests/test_sequence_alignment.py platform/api/tests/test_nanopore_nextflow.py -q`
- `cd platform/frontend && npm run build`
- Run the relevant MolBio frontend source tests after adding construct-compare/MSA tests; keep `platform/frontend/tests/alignmentLabels.test.ts` and `platform/frontend/tests/molBioViewerLayout.test.ts` green.

---

## 6. Verification matrix

### 6.1 Must pass before any “Plasmidsaurus-class” wording

- Per-base support table exists and is tested.
- Variant table exists and is tested on SNP, insertion, deletion, low coverage, and ambiguous support cases.
- Read ledger exists or the docs explicitly say individual read table is not yet available.
- Consensus fallback states are explicit and cannot pass verification.
- Summary TSV includes coverage and pass/warn/fail interpretation.
- Report bundle lists generated and unavailable deliverables truthfully.
- IGV links work from summary/variant rows.
- MolBio Toolkit can open read evidence for an attached construct/job only through an approved experimental or promoted surface.
- The standalone `/designer` MolBio viewer/tools remain non-regressed, with existing alignment/layout tests still green.

### 6.2 Must not claim until separately implemented

- Q60 consensus quality.
- AB1 trace artifact / mixed-peak compatibility.
- Virtual gel.
- Plasmid map HTML parity with a dedicated commercial map renderer.
- Amino-acid consequences unless CDS feature parsing and codon translation are implemented/test-covered.
- Mixture deconvolution beyond allele fractions and visible support.

### 6.3 Baseline regression tests to keep alive

- `platform/api/tests/test_sequence_alignment.py` for Biopython optimal-alignment overflow handling.
- `platform/api/tests/test_nanopore_nextflow.py` for nanopore command routing.
- frontend MolBio layout/alignment label tests.
- New sequence-QC manifest/report/read-ledger tests introduced by this plan.

---

## 7. Suggested immediate first tranche

Do Phase 0 and the smallest part of Phase 1 first, with no production MolBio UI changes:

1. Add `qc_manifest.json` generation for current FASTQ QC artifacts.
2. Add API parser + endpoint for manifest retrieval.
3. Add tests proving the manifest truthfully describes current artifacts.
4. Add `per_base_support.tsv` generation for base counts only, without overclaiming quality or consequences.
5. Add tests with a tiny synthetic BAM/reference fixture.
6. Keep `/designer` and the standalone MolBio tools untouched during this tranche except for regression-test execution.
7. If early UI validation is needed, create an experimental route/tab that duplicates or wraps the necessary MolBio viewer shell instead of modifying the production MolBio workbench.

Do not start with a big UI redesign. The current weakness is not the absence of widgets; it is the absence of a stable, typed evidence contract that both widgets and reports can trust. The first UI work should be opt-in and disposable/promotable, not a direct rewrite of the existing standalone MolBio viewer.

---

## 8. Git/worktree safety notes

The current branch already contains many unrelated modified and untracked files. This plan was written as a new standalone docs file to avoid touching those changes.

When implementing, each phase should be isolated into a dedicated branch or clean worktree. Do not mix this sequencing-QC tranche with Fold-CP/GPU/MSA/runtime changes already present in the working tree.

Safe new file from this planning pass:

- `docs/plans/2026-04-25-molbio-read-qc-harmonization-spec.md`

No existing source file should be edited until a specific phase is approved.
