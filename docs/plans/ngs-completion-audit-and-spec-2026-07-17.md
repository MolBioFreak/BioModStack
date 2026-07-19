# BioModStack ONT/NGS Completion Audit and Phased Specification

- **Audit date:** 2026-07-17
- **Repository:** `/home/dalab/biomodstack/biomodstack`
- **Audited worktree branch / HEAD:** `test` / `a69711f7e55786f3867e3952b546b3d6b8c48c11`
- **Upstream relation at audit time:** `origin/test...HEAD = 0 behind / 15 ahead`
- **Supersedes as an implementation baseline:** `docs/plans/ngs-workflow-completion-spec-2026-06-25.md`
- **Priority:** automatic plasmid/construct verification through `wf_clone_validation`, ahead of methylation expansion

---

## 1. Executive verdict

**Overall status: FAIL — not release-ready as an automatic plasmid/construct verification system.**

BioModStack now has a broad ONT surface: seven standalone DSL2 workflows, reusable NGS modules, typed API contracts, FASTQ alignment/QC, nested EPI2ME clone validation, a MinKNOW host-agent surface, methylation modules, and frontend result panels. Static/API/frontend tests are largely green, all seven workflow entrypoints resolve under Nextflow preview, and the FASTQ path completed real synthetic executions.

Those facts do **not** establish construct verification. The current implementation can generate an alignment, coverage table, consensus, IGV evidence, multimer evidence, and a review manifest, but it does not convert observed-versus-expected evidence into a scientifically defensible automatic decision. A synthetic exact construct and a synthetic construct carrying a fixed 100%-allele-fraction SNV both completed with the same `review_required` result. No VCF, identity metric, normalized circular comparison, or variant reason was emitted.

### Release-blocking findings

| ID | Finding | Evidence | Severity |
|---|---|---|---|
| P0-01 | Canonical `wf_clone_validation` typed submission is rejected by the real model registry because the registered mode is `clone_validation`. | `platform/api/routers/ont_runs.py`; `platform/api/config/models/nanopore.yaml`; reproduced registry result: `Unknown mode 'wf_clone_validation' for model 'nanopore'`. | P0 |
| P0-02 | There is no validated PASS path for automatic construct verification. | `scripts/build_sequence_qc_manifest.py:117-126`; exact and SNV synthetic runs both returned `review_required`. | P0 |
| P0-03 | Consensus failure can copy expected reference sequence into `fastq_consensus.fasta`, creating a provenance hazard. | `modules/ngs/fastq_plasmid_qc.nf:172-189`. | P0 |
| P0-04 | The runtime SIF has samtools 1.13, so the current path uses the mpileup-majority fallback; it does not produce a normalized indel-aware variant callset. | Synthetic runtime reported `mpileup_majority_consensus`; no `*.vcf*` artifact in either control. | P0 |
| P0-05 | Methylation BAM preparation is blocked for every prepared BAM: `ValidateMappedBam` stages `aligned.bam` and runs `cp aligned.bam aligned.bam`. | `modules/ngs/bam_prepare.nf:69-70`; real Nextflow execution failed with “are the same file.” | P0 |
| P0-06 | Nested EPI2ME integration is unpinned by default and mutates copied upstream code at runtime; “fast” silently becomes HAC. | `modules/ngs/clone_validation.nf:31-59,73-137`. | P0 |
| P0-07 | Methylation report/artifact names do not match emitted files. | Workflow report stage names `modified_sites.tsv`, `modkit_pileup.log`, and `modkit_summary.log`; modules emit `methylation.bed`, `pileup.log`, `modkit_summary.tsv`, and `summary.log`. | P0 |
| P0-08 | API/UI capability declarations overstate actual workflow wiring for duplex, barcoding/demultiplexing, and model choices. | Contract/frontend controls exist; `modules/ngs/dorado_basecall.nf` only invokes `dorado basecaller` and has no duplex/barcode command path. | P0 |
| P0-09 | A mapped BAM is accepted based on mapped-read count without proving that its `@SQ` dictionary matches the supplied expected reference. | `modules/ngs/bam_prepare.nf`; no SN/LN/M5/reference digest compatibility gate. | P0 |
| P0-10 | Local model/runtime provisioning is not release-ready. | Module requires `/weights/dorado`; no Dorado model files were found under `/mnt/BioModStack`; SIF Dorado is 1.3.1 while current first-party release is 2.1.0. | P0 |

**Decision:** do not label any current output “verified,” do not expose a green automatic construct result, and do not commit remediation from this dirty worktree. Implement the phases below in a clean worktree pinned to an explicitly approved base.

---

## 2. Scope and evidence model

### 2.1 Audited product surface

- **Workflow entrypoints**
  - `workflows/ngs/wf_clone_validation.nf`
  - `workflows/ngs/ont_construct_screening.nf`
  - `workflows/ngs/ont_plasmid_qc.nf`
  - `workflows/ngs/ont_fastq_qc.nf`
  - `workflows/ngs/ont_methylation_analysis.nf`
  - `workflows/ngs/ont_basecall_dna.nf`
  - `workflows/ngs/ont_basecall_rna.nf`
- **Core modules**
  - `modules/ngs/dorado_basecall.nf`
  - `modules/ngs/dorado_align.nf`
  - `modules/ngs/bam_prepare.nf`
  - `modules/ngs/fastq_align.nf`
  - `modules/ngs/fastq_plasmid_qc.nf`
  - `modules/ngs/fastq_dimer_qc.nf`
  - `modules/ngs/clone_validation.nf`
  - `modules/ngs/modkit_pileup.nf`
  - `modules/ngs/modkit_summary.nf`
- **Cross-layer contract**
  - `platform/api/services/ont_ngs_contract.py`
  - `platform/api/routers/ont_runs.py`
  - `platform/api/routers/jobs.py`
  - `platform/api/services/nextflow.py`
  - `platform/api/config/models/nanopore.yaml`
  - `platform/api/services/sequence_qc_manifest.py`
  - `platform/api/routers/sequence_qc.py`
  - `platform/api/services/ont_minknow_client.py`
  - `scripts/lib/ont_minknow_host.py`
- **Frontend**
  - `platform/frontend/src/components/NanoporeTemplate.tsx`
  - `platform/frontend/src/components/NGSToolkit.tsx`
  - `platform/frontend/src/components/ngs/OntInstrumentPanel.tsx`
  - `platform/frontend/src/components/ngs/SequenceQcManifestPanel.tsx`
  - `platform/frontend/src/components/ngs/useSequenceQcManifest.ts`
- **Tests and prior specifications**
  - focused API/frontend/Nextflow contract tests;
  - prior NGS architecture and completion documents;
  - official/current documentation and primary literature listed in §15.

### 2.2 Evidence classes

| Level | Meaning | What it proves | What it does not prove |
|---|---|---|---|
| E0 | Source presence | Code/control/artifact name exists | Parseability or execution |
| E1 | Static/unit/contract test | A local contract or transformation behaves under test | Nextflow process execution |
| E2 | Nextflow preview/config resolution | DSL2 parses and the intended process graph resolves | Tool/container/artifact success |
| E3 | Synthetic execution | Processes execute on controlled data and emit parseable artifacts | Production ONT chemistry/model/hardware behavior |
| E4 | Production-like execution | Realistic data, pinned models/containers, GPU, artifacts, and semantics validated | Field performance across all instruments/samples |
| E5 | Production acceptance | Truth-set performance, operator recovery, version pinning, and reproducibility meet release gates | Future-version compatibility |

No E0/E1/E2 evidence may be presented as E3/E4/E5.

### 2.3 Worktree qualification

The checkout had **126 Git status entries** before this report was written. NGS-adjacent tracked dirty files included `nextflow.config`, `platform/api/routers/jobs.py`, `platform/api/services/nextflow.py`, `platform/frontend/src/components/NGSToolkit.tsx`, and `platform/frontend/src/components/ngs/OntInstrumentPanel.tsx`, alongside extensive unrelated work. Core NGS workflow/module findings were evaluated from the current worktree; release implementation must be repeated in a clean worktree so committed HEAD, local changes, and remediation are not conflated.

---

## 3. Runtime evidence obtained in this audit

### 3.1 Environment

| Component | Observed |
|---|---|
| Nextflow wrapper | 25.10.1 |
| Docker | 28.5.1 |
| Apptainer | 1.4.1 |
| Host Dorado | 1.1.1+e718a3a26 |
| `apptainer/dorado.sif` Dorado | 1.3.1+7c84b01de |
| SIF minimap2 | 2.24-r1122 |
| SIF samtools | 1.13 |
| SIF modkit | 0.6.1 |
| SIF bcftools | absent |
| Visible GPUs | RTX 5090, RTX 5060 Ti, two RTX 3090 |
| Dorado model directory | required by module; no model files found under `/mnt/BioModStack` |

The installed SIF is a useful execution base for alignment/QC, but its tool versions and missing `bcftools` are not an acceptable final verification toolchain.

### 3.2 Test results

| Probe | Result | Evidence class |
|---|---:|---|
| Focused API/contract tests excluding contaminated smoke fixture | 89/89 passed | E1 |
| Frontend ONT/manifest contract tests | 15/15 passed | E1 |
| Nextflow preview/config resolution | 7/7 entrypoints passed | E2 |
| FASTQ exact-reference synthetic execution | all four processes completed; BAM/BAI valid; 20 records | E3 |
| FASTQ fixed-SNV synthetic execution | all four processes completed; BAM/BAI valid; 20 records | E3 |
| Methylation full synthetic execution | failed in `BamValidateMappedBam` self-copy | E3 negative |
| MM/ML validator module on untagged BAM | correctly failed with explicit MM/ML error | E3 negative |
| POD5/simplex GPU basecalling | not run; model provisioning absent | none |
| Duplex basecalling | not run | none |
| Barcode/demultiplex run | not run | none |
| Real MinKNOW hardware run/handoff | not run | none |
| Production plasmid truth set | not run | none |

### 3.3 Exact-versus-SNV control

A deterministic 3,000-bp reference and 20 full-length reads were used. In the mutant control every read carried one `T→A` substitution at position 1500.

| Metric | Exact | Mutant |
|---|---:|---:|
| Aligned BAM records | 20 | 20 |
| Consensus length | 3,000 | 3,000 |
| Consensus/reference differences (external audit comparison) | 0 | 1 |
| Per-base support at position 1500 | `T`, depth 20, fraction 1.0 | `A`, depth 20, fraction 1.0 |
| Manifest consensus method | `mpileup_majority_consensus` | `mpileup_majority_consensus` |
| Manifest construct status | `review_required` | `review_required` |
| Identity field | absent | absent |
| VCF/BCF artifact | absent | absent |

**Interpretation:** current evidence generation can expose the mutation indirectly, but the product does not classify it. This is the decisive evidence that automatic verification is unfinished.

---

## 4. Current feature-gap matrix

Legend: **PASS** = implemented and supported by the stated evidence; **PARTIAL** = source exists but semantics/runtime/gates are incomplete; **FAIL** = required behavior is absent or contradicted; **UNPROVEN** = no adequate runtime evidence.

| Capability | Current implementation evidence | Requirement / best practice | Status | Residual delta / risk |
|---|---|---|---|---|
| Canonical typed submission | Canonical IDs/aliases exist in `ont_ngs_contract.py`. | Canonical ID must map deterministically to a registered model mode through the real validator. | **FAIL** | `wf_clone_validation` is rejected; mocked tests hide it. |
| Standalone DSL2 composition | Seven entrypoints directly include required modules; all previewed. | Direct DSL2 composition; no deleted monolith wrappers. | **PASS (E2)** | Tool/runtime coverage remains incomplete. |
| FASTQ ingestion | `FastqAlign`, QC, multimer, manifest; two E3 runs. | Validate FASTQ, align with declared preset, retain provenance. | **PARTIAL** | No read validation report; preset not selected from read accuracy; no automatic verdict. |
| POD5 ingestion | DNA/RNA/basecall and reference workflows accept POD5 paths. | Validate POD5 structure, chemistry, sample rate, run IDs, and model compatibility. | **PARTIAL/E2** | No real model/GPU run; model directory absent. |
| FAST5 ingestion | No conversion module or typed FAST5 mode. | Dorado consumes POD5; FAST5 must be converted with official POD5 tooling or explicitly rejected before job creation. | **FAIL** | Requested legacy input silently unsupported. |
| BAM ingestion | `PrepareBamForAnalysis` sorts/indexes or aligns unmapped input. | Validate BAM integrity, sort order, mapping, tags, and reference dictionary/digest. | **FAIL** | Self-copy collision; mapped BAM/reference compatibility is not proven. |
| Dorado simplex DNA | `dorado basecaller` process and DNA workflow exist. | Pinned Dorado/model, chemistry-compatible selection, GPU/model preflight, BAM provenance. | **PARTIAL/E2** | No E3 basecall; local runtime behind current release. |
| Dorado simplex RNA | RNA workflow exists. | RNA-compatible model, direct-RNA/RNA002/RNA004 constraints, splice-aware downstream alignment where applicable. | **PARTIAL/E2** | Model choice and RNA alignment semantics unproven. |
| Duplex basecalling | API/UI declare duplex as a basecalling mode. | Execute `dorado duplex`, expose pairing method/counts, distinguish duplex reads, validate model/chemistry. | **FAIL** | No duplex command or pair-ID path in NGS module. |
| fast/hac/sup | Selector exposed; Dorado can auto-resolve chemistry when models are provisioned. | Allowed choices must be molecule/chemistry/release compatible and recorded as exact resolved model IDs. | **PARTIAL** | No resolved model manifest; clone wrapper silently maps fast→HAC and pins old v5.0.0 overrides. |
| Model provisioning | `DORADO_MODELS_DIR=/weights/dorado` preflight exists. | Offline/pinned model inventory, digest, compatibility lookup, deterministic mounts, explicit download/admin workflow. | **FAIL** | Directory/model inventory unavailable; no runtime proof. |
| Modified-base basecalling | `--modified-bases` wiring and MM/ML validator exist. | Compatible simplex/mod model, BAM output preserving MM/ML/MN, aligned-coordinate validity, thresholds recorded. | **PARTIAL** | Full workflow blocked by BAM bug; no tagged E3 run. |
| Modkit pileup/summary | Modules emit bedMethyl and summary. | Validate MM/ML, reference compatibility, chosen threshold/filter policy, parse bedMethyl semantics. | **PARTIAL** | Runtime blocked; local modkit 0.6.1 versus current 0.6.4; artifacts mismatch report contract. |
| Barcoding/classification | API/UI expose kit and multiplexing controls. | Validate kit/chemistry, classify, demultiplex, sample-sheet map, prevent barcode bleed, record unclassified/ambiguous reads. | **FAIL** | No `dorado barcoder`, `demux`, or basecaller kit wiring in module. |
| Multiplex sample isolation | Product declaration exists. | Per-barcode run units and contamination metrics; no pooled consensus masquerading as one clone. | **FAIL** | No demonstrated per-barcode data plane. |
| Adapter/barcode trimming | Dorado trim flag exists. | Record trimming policy; do not mix incompatible trimmed/untrimmed coordinate/tag semantics. | **PARTIAL** | No cross-field validation with barcodes/mod tags/alignment. |
| MinKNOW status/start/stop | Host agent implements connection, health, find/start/stop, output polling, and handoff paths. | Safety-gated start/stop, protocol/product-code validation, output provenance, real-device acceptance. | **PARTIAL/E1** | No hardware E4 run; API package/version coupling and recovery still unproven. |
| FASTQ DNA alignment | minimap2 `-a`, configurable preset, secondary suppression, sort/index. | Use `map-ont` for noisy reads, `lr:hq` for accurate Q20+/SUP where validated; record command/version. | **PARTIAL/E3** | Default remains `map-ont`; no accuracy-driven policy; no `cs`/`MD` evidence contract. |
| RNA alignment | RNA workflow can basecall but does not prove splice-aware alignment. | `splice`/`splice:hq` for genomic RNA alignment; `map-ont`/`lr:hq` only for non-spliced targets as justified. | **FAIL/UNPROVEN** | Molecule/reference type is not used to select semantics. |
| Existing BAM realignment | Mapped-count test avoids unnecessary re-alignment. | Compare `@SQ` SN/LN/M5 and expected-reference digest; preserve/recreate MM/ML correctly. | **FAIL** | A mapped-to-wrong-reference BAM can be accepted. |
| Consensus provenance | Manifest records method; copied-reference case is marked fail. | Observed consensus must be observed-only; absence is represented as unavailable, never filled with expected sequence. | **FAIL** | Expected sequence is still written to an observed-consensus filename. |
| SNV/indel calls | Per-base table and consensus exist. | Emit normalized VCF/BCF with SNV, insertion, deletion, genotype/VAF/depth/filter. | **FAIL** | No callset; majority fallback is not indel-complete. |
| Identity and reference coverage | Coverage tables exist. | Circular-normalized alignment metrics: identity, aligned bases, reference/assembly coverage, gaps, length delta. | **FAIL** | Identity absent; coverage does not drive verdict. |
| Circular topology | Multimer/dimer module includes doubled-reference and breakpoint logic; upstream clone workflow circularizes. | Normalize rotation/orientation, prove one complete circular monomer, report unresolved topology. | **PARTIAL** | Results are not integrated into construct verdict. |
| Structural variants | Dimer/breakpoint evidence exists. | Detect large insertions/deletions/rearrangements, split alignment, duplicated backbone/insert, whole-plasmid multimers. | **PARTIAL** | No unified SV artifact or decision policy. |
| Mixed clone / contamination | Mapping/unmapped counts and upstream evidence exist. | Report allele fractions, secondary contigs, unmapped/clipped reads, non-target mapping, barcode bleed. | **FAIL** | No mixed-population or contamination verdict. |
| Automatic PASS/FAIL/REVIEW | Manifest can emit fail for copied reference, otherwise review. | Machine-readable, reason-coded decision with versioned thresholds and evidence sufficiency. | **FAIL** | No validated PASS; exact and SNV controls indistinguishable. |
| EPI2ME clone validation | Nested v1.8.4-compatible workflow can assemble/polish/align/call; report/status are exposed. | Pin revision/images/models; consume BAM-stats/BCF/HTML into BioModStack-native contract. | **PARTIAL** | Default unpinned; runtime source patches; `sample_status.txt` lacks expected identity/coverage columns. |
| Artifact contract | Workflow kinds exist in API and result UI. | Declared paths must exist, parse, and have correct scientific semantics. | **FAIL** | Methylation names mismatch; clone/manifest semantics not fully normalized. |
| Frontend reporting | Manifest, IGV, methylation, instrument panels exist. | Never show green from missing/stale/fallback evidence; show thresholds, reason codes, versions, variants, provenance. | **PARTIAL** | UI cannot display information the backend does not produce. |
| Reproducibility | Logs and some versions exist. | Pin workflow commit, containers, exact Dorado/model IDs/digests, commands, reference/input digests, schema. | **FAIL** | Nested workflow/runtime patching and model drift prevent replay. |
| Production runtime | FASTQ E3 evidence obtained. | POD5/BAM/duplex/barcode/modbase/MinKNOW truth-set E4/E5 matrix. | **FAIL** | Most advertised paths remain E0-E2. |

---

## 5. Official-tool requirements that constrain implementation

### 5.1 Dorado

- Current Dorado documentation/release baseline at audit time is **2.1.0**; current model listings include DNA R10.4.1 E8.2 400-bps v5.2.0 fast/HAC/SUP families and RNA004 SUP v5.2.0.
- Dorado simplex can select a model by exact path/ID or automatically from chemistry plus a model-complexity selector. BioModStack must record the **resolved exact model ID**, not just `sup`.
- Duplex is a distinct command. It accepts POD5 input and may use automatic pairing or explicit read-pair IDs. A UI `duplex=true` flag is not implementation evidence.
- Modified-base calls are carried in SAM/BAM `MM` and `ML` tags; model compatibility must be validated. FASTQ cannot preserve that evidence.
- Barcode classification and demultiplexing require a declared kit and explicit output grouping; unclassified/ambiguous reads must remain visible.
- FAST5 is not the supported Dorado signal input. Use official POD5 conversion and preserve conversion provenance.

### 5.2 minimap2

- `map-ont` is intended for noisy ONT genomic reads.
- `lr:hq` is intended for accurate long reads, including Q20+ ONT reads; `asm5` is appropriate for highly similar assembly-to-reference alignment.
- `splice`/`splice:hq` are required for spliced RNA-to-genome alignment.
- Preset, minimap2 version, secondary/supplementary policy, and emitted tags must be stored in run provenance.

### 5.3 modkit / SAM tags

- `modkit pileup` consumes aligned modBAM with valid `MM`/`ML` tags and emits bedMethyl-style counts.
- Default probability filtering is data-dependent; a reproducible workflow must record either explicit thresholds or the calculated thresholds and filtering summary.
- Alignment/re-alignment must preserve modification tags and valid read-coordinate semantics; missing or malformed tags are a hard input error, not “zero methylation.”

### 5.4 MinKNOW

- MinKNOW API 6.10.1 is a gRPC/protobuf client surface for connection management, protocol discovery/start/stop, acquisition state, basecalling/barcoding options, and output paths.
- BioModStack’s host-agent boundary is appropriate because MinKNOW and device access live outside the core API container.
- Release evidence requires a real instrument or approved simulator: protocol selection, safety confirmations, run ID, output discovery, stable-file handoff, interruption/recovery, and no duplicate workflow submission.

### 5.5 EPI2ME `wf-clone-validation`

Audited upstream release: **v1.8.4**, commit `b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42`.

Useful upstream behavior:

- length/quality filtering and downsampling;
- three subsampled assemblies;
- Flye/miniasm and Trycycler reconciliation;
- circular contig handling and Medaka polishing;
- full-reference alignment with `asm5`;
- BCF variant artifacts and BAM statistics;
- report-level expected coverage/identity checks;
- optional insert/host references and linearization efficiency.

Integration limitation:

- Upstream writes `sample_status.txt` before adding expected-assembly/insert coverage and identity columns. Those results are present in report-stage data/HTML and supporting artifacts, not a complete normalized machine-readable verdict. BioModStack must normalize BAM stats, assembly/reference alignments, and BCF files into its own schema.

---

## 6. Plasmid-verification literature translated into product requirements

| Evidence | What it contributes | BioModStack requirement |
|---|---|---|
| Brown et al. 2023, BMC Bioinformatics | Reference mapping, consensus, coverage/quality filters, unmapped-read contamination analysis, explicit plasmid reports. | Whole-reference alignment, zero-coverage detection, quality/depth metrics, contamination evidence, explicit decision. |
| OnRamp 2023, Genome Research | Rotated/doubled reference visualization, mpileup consensus, full-length alignment, mismatch/indel display, automated per-sample reports. | Circular-origin normalization, variant tables plus human-readable alignment/IGV evidence. |
| Trycycler 2021, Genome Biology | Reconciliation of multiple long-read assemblies, circularization, rotation to consistent start, polishing; long-read assemblies can disagree. | Assembly provenance, topology normalization, ambiguity→REVIEW, never trust one assembler blindly. |
| Emiliani et al. 2022, ACS Synthetic Biology | Multiplexed long-read assembly/annotation can detect single-base changes, structural changes, and contamination; concatemeric repeats can improve consensus. | SNV/indel/SV and contamination detection are all first-class, not just coverage. |
| Currin et al. 2019, Synthetic Biology | High-multiplex construct verification used minimap2 mapping, bcftools calls, coverage/identity thresholds, read-count gates, and strand-bias review. | VCF/BCF, VAF/depth/strand support, configurable sample acceptance profiles. |
| Biofoundry-scale 2024 / DuBA.flow 2024 | Scalable plasmid QC needs sample-wise assembly, full-length comparison, reporting, and contamination/multiplex controls; simple reference mapping may miss unexpected material. | Pair a fast reference screen with assembly/topology/contamination analysis for final PASS. |
| Uematsu & Baskin 2025, eLife | Barcode-free multiplexing is possible with Bayesian assignment, but assignment uncertainty is intrinsic evidence. | Conventional barcodes remain the default; any barcode-free mode must expose assignment probability/uncertainty and route ambiguous assignments to REVIEW. |
| Vaisbourd et al. 2025, ACS Synthetic Biology | Whole-plasmid multimers occur and must be distinguished from other structural states. | Report monomer/dimer/trimer/concatemer evidence separately from sequence correctness. |
| Schimke & Vollmers 2026, PLOS ONE | R2C2/Chopper can recover complete plasmids but observed complete-sequence evidence and variant interpretation remain distinct. | Preserve complete observed sequence and reason-coded variant evidence; no lossy summary-only verdict. |

### 6.1 Scientific rule

A **PASS** means: a provenance-valid observed assembly/consensus, normalized for circular origin/orientation, fully covers the expected construct, has no disallowed high-confidence sequence or structural differences, has sufficient independent read support, and has no unresolved mixed/contamination/topology warning.

“Reads mapped,” “consensus generated,” “report exists,” or “coverage looks high” are not PASS conditions.

---

## 7. Target verification contract

### 7.1 Separate execution state from scientific state

```json
{
  "schema_version": "2.0.0",
  "workflow_status": "completed",
  "verification_status": "pass",
  "reason_codes": [],
  "threshold_profile": "plasmid-strict-v1",
  "reference": {
    "path": "reference/reference.fasta",
    "sha256": "...",
    "length_bp": 7000,
    "topology": "circular"
  },
  "observed_sequence": {
    "path": "verification/observed_consensus.fasta",
    "sha256": "...",
    "method": "wf-clone-validation+medaka",
    "source": "observed_reads",
    "tool_versions": {}
  },
  "alignment": {
    "preset": "asm5",
    "identity": 1.0,
    "reference_coverage": 1.0,
    "observed_coverage": 1.0,
    "orientation": "+",
    "rotation_offset": 0,
    "uncovered_reference_bases": 0,
    "uncovered_observed_bases": 0
  },
  "read_support": {
    "mapped_fraction": 0.99,
    "median_depth": 100,
    "minimum_depth": 25,
    "low_depth_bases": 0,
    "ambiguous_bases": 0
  },
  "variants": {
    "high_confidence_count": 0,
    "mixed_allele_count": 0,
    "vcf": "verification/variants.normalized.vcf.gz"
  },
  "topology": {
    "expected": "circular_monomer",
    "observed": "circular_monomer",
    "structural_variant_count": 0,
    "multimer_fraction": 0.0
  },
  "contamination": {
    "unmapped_fraction": 0.01,
    "secondary_contig_count": 0,
    "barcode_ambiguity_fraction": 0.0
  },
  "artifacts": []
}
```

### 7.2 Provenance invariants

1. `observed_sequence.source` may only be `observed_reads`, never `expected_reference`.
2. If no observed consensus/assembly can be generated, omit the sequence and set `verification_status=review` or `fail` with `OBSERVED_SEQUENCE_UNAVAILABLE`.
3. Store SHA-256 digests for input reads/BAM/POD5 manifest, expected reference, observed sequence, models, containers, and normalized callset.
4. Store exact commands, tool versions, model IDs, workflow commit, threshold profile, reference topology, and circular normalization method.
5. An artifact is “present” only after existence, nonzero/allowed-empty state, parser validation, and semantic validation.

### 7.3 Proposed strict plasmid profile v1

These are release defaults, not universal biological truths; they must be calibrated against the truth set in Phase 7.

#### PASS

All conditions required:

- workflow completed and all required artifacts passed semantic validation;
- observed consensus/assembly provenance is valid;
- expected and observed topology normalize to one complete circular monomer;
- reference coverage = 100%; observed assembly coverage = 100%;
- no uncovered reference base and no unexplained extra observed base;
- no high-confidence disallowed SNV, insertion, deletion, or structural variant;
- no ambiguous consensus base;
- minimum depth ≥ 20 and median depth ≥ 30, unless an approved assay-specific profile overrides it;
- mapped fraction ≥ 0.90;
- no mixed allele with VAF in `[0.10, 0.90)` at depth ≥ 20;
- no unresolved secondary contig/contamination or barcode-bleed flag;
- no unresolved multimer/topology flag;
- both-strand support present at each called variant candidate unless the library design makes strand balance inapplicable and the profile records that exception.

#### FAIL

Any definitive contradiction:

- high-confidence disallowed variant with VAF ≥ 0.90;
- structural difference, incorrect construct length, missing/extra segment, wrong insert/backbone, or topology mismatch;
- expected reference copied or otherwise used as observed consensus;
- sample/reference identity mismatch;
- definitive contamination/wrong-barcode result above the approved threshold.

#### REVIEW

Insufficient or ambiguous evidence:

- low/zero coverage or unresolved consensus bases;
- mixed allele VAF 0.10–0.90;
- strand-biased or systematic-context candidate;
- assembly disagreement, unresolved circularization/rotation, multiple contigs, or high clipped/unmapped fraction;
- model/reference/tag incompatibility;
- missing nonfatal artifact or uncalibrated threshold profile.

Reason codes are mandatory and stable; free-text notes are supplementary.

---

## 8. Target workflow architecture

```text
FAST5 ──POD5 convert+validate──┐
POD5 ──inspect chemistry───────┼─> Dorado simplex/duplex ─> BAM(+MM/ML, RG/barcode)
BAM ──integrity/ref/tag gate───┤
FASTQ ──read validation────────┘
                                  │
                        barcode classify/demux
                                  │
          ┌───────────────────────┴────────────────────────┐
          │                                                │
 read-to-reference fast screen                  de novo/reconciled assembly
 minimap2 map-ont/lr:hq/splice                  wf-clone/Trycycler/Medaka
          │                                                │
 BAM, depth, VAF, clipped/unmapped               circular observed assembly
          └───────────────────────┬────────────────────────┘
                                  │
                  circular-normalized asm5 comparison
                                  │
           normalized VCF + identity/coverage/SV/topology
                                  │
          contamination + mixed clone + multimer evidence
                                  │
             schema-v2 PASS / FAIL / REVIEW manifest
                                  │
                    API validation + frontend display
```

The fast screen may short-circuit obvious FAIL/REVIEW cases, but final PASS requires the observed-sequence/topology path.

---

## 9. Artifact contract to enforce

### 9.1 Canonical verification artifacts

| Path | Required for PASS | Semantic validation |
|---|---:|---|
| `verification/qc_manifest.json` | yes | schema v2; internally consistent; no unknown reason codes |
| `verification/observed_consensus.fasta` | yes | nonempty; source=observed; digest matches manifest |
| `verification/observed_consensus.fasta.fai` | yes | length/name match FASTA |
| `verification/reference.normalized.fasta` | yes | digest linked to submitted expected reference |
| `verification/assembly_to_reference.paf` | yes | parseable; one normalized primary full-length comparison |
| `verification/variants.normalized.vcf.gz` + `.tbi` | yes | parseable; reference contig/digest match; normalized alleles |
| `verification/alignment_metrics.json` | yes | identity, ref/assembly coverage, gaps, rotation/orientation |
| `verification/read_support.tsv.gz` | yes | every reference position represented or explicit gap |
| `verification/topology.json` | yes | monomer/circular/SV/multimer evidence and method |
| `verification/contamination.json` | yes | mapped/unmapped/clipped/secondary/barcode evidence |
| `verification/tool_provenance.json` | yes | tools, versions, models, images, workflow revision, commands |
| `verification/report.html` | yes | human-readable rendering of the same machine contract |

### 9.2 Methylation canonicalization

Use actual module outputs as the starting contract:

- `methylation/modified_base_input.bam`
- `methylation/modified_base_input.bam.bai`
- `methylation/modified_base_tag_check.log`
- `methylation/methylation.bed`
- `methylation/pileup.log`
- `methylation/modkit_summary.tsv`
- `methylation/summary.log`

Remove undeclared/non-emitted legacy names such as `modified_sites.tsv`, `modkit_pileup.log`, and `modkit_summary.log`, or deliberately rename the module outputs in one atomic contract change. Tests must validate path, parser, and semantics.

---

## 10. Phased implementation specification

Each phase is a separately reviewable commit/PR. A failed binary gate blocks the next phase. No “mostly green” gate counts.

### Phase 0 — clean implementation baseline

**Goal:** isolate remediation from the current dirty checkout.

**Actions**

1. Create a new worktree from an explicitly approved remote commit.
2. Record base commit, branch, container digests, model inventory, and expected dirty-state = clean.
3. Copy this report only after approval; do not merge unrelated current changes.

**Gate P0**

- `git status --short` is empty before changes.
- base commit equals the approved remote SHA.
- baseline focused tests execute and results are attached.

**Commit:** documentation/baseline only.

### Phase 1 — truthful submission, BAM handling, and artifact contracts

**Files**

- `platform/api/routers/ont_runs.py`
- `platform/api/services/ont_ngs_contract.py`
- `platform/api/config/models/nanopore.yaml`
- `platform/api/services/nextflow.py`
- `modules/ngs/bam_prepare.nf`
- `workflows/ngs/ont_methylation_analysis.nf`
- `modules/ngs/modkit_pileup.nf`
- `modules/ngs/modkit_summary.nf`
- `modules/ngs/fastq_plasmid_qc.nf`
- `scripts/build_sequence_qc_manifest.py`
- existing API/Nextflow tests plus new cross-layer tests

**Changes**

1. Replace ad hoc prefix stripping with one explicit canonical workflow-ID→registered-mode map.
2. Exercise the real model registry in submission tests; do not mock past validation.
3. Fix BAM output staging by using distinct staged/output basenames or `samtools view/sort -o validated.bam`; never copy onto itself.
4. Validate BAM quickcheck, sort order, index, mapped count, `@SQ` SN/LN/M5, and expected-reference SHA-256. Realign only under an explicit policy.
5. Remove the reference-copy consensus fallback. Emit `observed_consensus.state=unavailable` and fail/review reason instead.
6. Canonicalize methylation artifact paths and update API product declarations/tests atomically.
7. Separate `workflow_status` from `verification_status` in manifest v2 scaffolding.

**Tests**

- canonical `wf_clone_validation` submission reaches `create_job` after real registry validation;
- every alias resolves to one canonical workflow and registered mode;
- BAM named `aligned.bam` and arbitrary BAM names both succeed;
- wrong-reference BAM is rejected or explicitly realigned;
- missing consensus never creates a FASTA containing expected reference;
- every declared methylation artifact is emitted and parser-valid on a tagged synthetic BAM.

**Gate P1**

- canonical clone submission validation returns no error;
- copied-reference test proves no observed FASTA exists;
- self-copy regression test passes in actual Nextflow execution;
- artifact contract test reports zero missing/unexpected paths;
- all focused tests green.

**Commit:** `ngs: make submission and artifact contracts truthful`.

### Phase 2 — automatic plasmid/construct verification engine (highest priority)

**Add**

- `modules/ngs/plasmid_verify.nf`
- `scripts/verify_plasmid_construct.py`
- `scripts/normalize_circular_reference.py` or equivalent tested library
- `platform/api/schemas/sequence_qc_manifest_v2.json`
- `platform/api/tests/test_plasmid_verifier.py`
- `platform/api/tests/test_ngs_plasmid_runtime_smoke.py`
- truth fixtures under a dedicated writable test-data path

**Modify**

- `workflows/ngs/wf_clone_validation.nf`
- `workflows/ngs/ont_construct_screening.nf`
- `workflows/ngs/ont_plasmid_qc.nf`
- `workflows/ngs/ont_fastq_qc.nf`
- `modules/ngs/fastq_plasmid_qc.nf`
- `modules/ngs/fastq_dimer_qc.nf`
- `modules/ngs/clone_validation.nf`
- `scripts/build_sequence_qc_manifest.py`
- API manifest loader/router and frontend manifest panel

**Dependencies**

- pinned current minimap2;
- current samtools/bcftools or an equivalently validated long-read variant/consensus toolchain;
- bgzip/tabix;
- pinned EPI2ME workflow/images when assembly path is used;
- JSON Schema validator;
- explicit circular-normalization implementation.

**Changes**

1. Treat expected reference and observed sequence as distinct typed inputs.
2. Produce an observed assembly/consensus from reads only.
3. Normalize circular origin and orientation; retain original sequence and transform metadata.
4. Compare observed assembly to expected with `minimap2 -x asm5` or a validated equivalent.
5. Emit normalized SNV/indel VCF and separate SV/topology evidence.
6. Compute identity, reference coverage, observed coverage, gaps, length delta, depth, VAF, strand support, clipped/unmapped fraction, secondary contigs, and multimer evidence.
7. Apply the versioned PASS/FAIL/REVIEW policy in §7.3.
8. Consume EPI2ME BAM-stats/BCF/assembly artifacts; do not infer PASS from `sample_status.txt` alone.
9. Render HTML/IGV from the same machine-readable contract.

**Mandatory synthetic truth cases**

| Fixture | Expected result |
|---|---|
| exact circular construct, rotated origin | PASS |
| exact reverse-complement construct | PASS with normalized orientation |
| one fixed SNV | FAIL with SNV reason and VCF record |
| one 1-bp insertion | FAIL with insertion reason |
| one 1-bp deletion | FAIL with deletion reason |
| large deletion | FAIL with SV reason |
| wrong insert/backbone | FAIL |
| 20% mixed allele | REVIEW with mixed-clone reason |
| low-depth interval | REVIEW |
| no observed consensus | REVIEW/FAIL, never copied reference |
| contaminant secondary contig | REVIEW or FAIL per calibrated threshold |
| whole-plasmid dimer/concatemer | REVIEW with topology reason |
| circular-origin-spanning indel | correct normalized call, not duplicated/lost |

**Gate P2**

- all truth fixtures produce exact expected status and reason codes;
- exact and fixed-SNV controls no longer share the same verdict;
- VCF, metrics, topology, contamination, and provenance artifacts pass parsers;
- no PASS is possible if any required artifact is missing/stale/unparseable;
- independent review approves the scientific threshold profile.

**Commit:** `ngs: add circular-aware automatic construct verification`.

### Phase 3 — pin and normalize `wf-clone-validation`

**Files**

- `modules/ngs/clone_validation.nf`
- `workflows/ngs/wf_clone_validation.nf`
- `nextflow.config`
- container/image lock files
- new adapter/parser tests

**Changes**

1. Pin upstream release/commit and image digests; initial audited target is v1.8.4 / `b3bf4ee...` unless a later revision is separately qualified.
2. Remove runtime source rewriting. Carry compatibility/Flye changes as a reviewed patch, fork, or upstream contribution.
3. Remove silent fast→HAC substitution; reject unsupported combinations or record an explicit approved fallback.
4. Pin exact Dorado override model IDs and prove compatibility with upstream schema.
5. Parse observed assembly, BAM stats, BCF, coverage, identity, insert/host metrics, and report metadata into manifest v2.
6. Keep upstream HTML as supporting evidence, not the authoritative BioModStack verdict.

**Gate P3**

- offline execution uses only pinned assets;
- rerun from same inputs produces identical contract-level artifacts/digests except declared nondeterministic fields;
- no runtime patch command exists;
- upstream version/model/image provenance appears in manifest;
- fixture outputs normalize to the same P2 verdict contract.

**Commit:** `ngs: pin and adapt wf-clone-validation outputs`.

### Phase 4 — input, model, duplex, and barcode completion

**Add/modify**

- `modules/ngs/pod5_prepare.nf`
- `modules/ngs/dorado_basecall.nf`
- `modules/ngs/dorado_demux.nf` (new)
- `workflows/ngs/ont_basecall_dna.nf`
- `workflows/ngs/ont_basecall_rna.nf`
- typed API contract/model YAML/frontend controls
- model inventory/preflight service and tests

**Changes**

1. Add explicit `fast5` mode: convert via official POD5 tooling; validate source/output record counts and preserve conversion manifest/digests.
2. Inspect POD5 chemistry/sample rate/run IDs before model selection.
3. Replace free-form/overbroad model controls with an inventory generated from the installed Dorado release and exact downloaded model IDs.
4. Implement true simplex and duplex command paths; optionally consume explicit pair-ID files and emit pairing metrics.
5. Implement barcode classification/demultiplexing with kit validation, sample-sheet mapping, unclassified/ambiguous counts, and separate per-barcode workflow units.
6. Enforce compatibility matrix: DNA/RNA, chemistry, speed, duplex, modified bases, barcode kit, Dorado version.
7. Mount a deterministic models directory; record model file digests; disable surprise runtime downloads in production.
8. Record exact resolved model ID in every BAM/manifest.

**Gate P4**

- real/sanitized POD5 simplex DNA and RNA E3 runs;
- duplex fixture/run proves duplex reads and pairing metrics;
- multiplex fixture proves barcode isolation and intentional cross-barcode read detection;
- unsupported combinations are rejected before Nextflow launch;
- cold/offline run succeeds from pinned model inventory;
- model/GPU/mount preflight fails closed with actionable error.

**Commit:** `ngs: complete Dorado model duplex and barcode data plane`.

### Phase 5 — RNA alignment and methylation completion

**Files**

- `modules/ngs/dorado_align.nf`
- `modules/ngs/bam_prepare.nf`
- `modules/ngs/modkit_pileup.nf`
- `modules/ngs/modkit_summary.nf`
- `workflows/ngs/ont_methylation_analysis.nf`
- DNA/RNA workflows and contract tests

**Changes**

1. Select `map-ont`, `lr:hq`, `splice`, `splice:hq`, or `asm5` from molecule/read/reference semantics, not one global default.
2. Validate and preserve MM/ML/MN tags through alignment; hard fail malformed tags.
3. Pin modkit and record probability filtering/threshold calculations.
4. Parse bedMethyl and summary outputs; distinguish “no calls” from missing/invalid tags.
5. Add strand/motif aggregation only where scientifically defined.
6. Keep methylation status separate from construct sequence verification.

**Gate P5**

- tagged synthetic BAM produces expected bedMethyl counts;
- untagged BAM fails at the MM/ML gate;
- wrong-reference modBAM is rejected or safely realigned with tags preserved;
- RNA-to-genome fixture proves splice-aware CIGARs;
- all canonical methylation artifacts exist and parse;
- no methylation result changes construct PASS unless an explicit assay profile says so.

**Commit:** `ngs: finish RNA and modified-base analysis contracts`.

### Phase 6 — MinKNOW handoff and run-state integrity

**Files**

- `scripts/lib/ont_minknow_host.py`
- host-agent client/runtime files
- `platform/api/services/ont_minknow_client.py`
- ONT run router/state models
- `OntInstrumentPanel.tsx`
- tests for handoff/recovery

**Changes**

1. Pin `minknow_api` package/protobuf compatibility to deployed MinKNOW.
2. Preserve explicit operator confirmations and hardware checks.
3. Persist MinKNOW protocol/run/acquisition IDs and output roots.
4. Mark files stable only after close/size-stability criteria; submit each run/barcode exactly once.
5. Recover cleanly across API/host-agent restart without duplicate starts/submissions.
6. Distinguish connection, protocol, acquisition, basecalling, file-handoff, and downstream workflow states.

**Gate P6**

- approved real-device or official simulator start/status/stop test;
- interruption/restart recovery test;
- stable-file handoff and exactly-once downstream submission;
- output provenance ties MinKNOW run to POD5/BAM/FASTQ and final manifest;
- no unsafe automatic protocol start/stop.

**Commit:** `ngs: harden MinKNOW run and file handoff state`.

### Phase 7 — frontend/reporting and production acceptance

**Files**

- `SequenceQcManifestPanel.tsx`
- `useSequenceQcManifest.ts`
- `NanoporeTemplate.tsx`
- NGS results/run pages
- API artifact normalizer and frontend tests
- operator documentation

**Changes**

1. Display PASS/FAIL/REVIEW with reason codes, threshold profile, evidence sufficiency, tool/model/workflow versions, input/reference/observed digests, and artifact validation state.
2. Show variant table/VCF, identity/coverage, low-depth regions, contamination/mixed-clone metrics, circular/topology/multimer status, and IGV/report links.
3. Never infer green from workflow completion, HTTP 200, report existence, or missing manifest fields.
4. Require explicit selected API/frontend health endpoints to be HTTP 200; no fallback endpoint.
5. Produce operator runbook for model install, MinKNOW handoff, failed verdict review, and replay.

**Production truth set**

At minimum:

- ≥20 exact expected plasmids across representative sizes/GC/homopolymers;
- engineered SNVs, 1-bp and larger indels, wrong inserts/backbones, deletions/rearrangements;
- mixed-clone titration at 5%, 10%, 20%, 50%;
- contamination and barcode-bleed mixtures;
- monomer/dimer/concatemer samples;
- simplex and duplex where supported;
- at least two applicable Dorado model qualities;
- real instrument/POD5 and pre-basecalled BAM/FASTQ paths.

**Gate P7 / release gate**

- 100% detection of seeded fixed SNV/indel/SV/wrong-construct failures in the truth set;
- 0 false PASS on any ambiguous/mixed/low-evidence fixture;
- exact expected plasmids meet the approved false-REVIEW/false-FAIL bound;
- all E4 runs reproducible from pinned assets;
- API artifact paths and UI status agree for every workflow;
- independent scientific and release approval recorded before merge/deployment.

**Commit:** `ngs: release verified ONT construct workflow and operator UI`.

---

## 11. Required test matrix

| Layer | Required checks |
|---|---|
| Pure Python | schema validation, circular normalization, identity/coverage, VCF normalization, verdict reason codes, artifact digests |
| Source contracts | direct DSL2 includes, no legacy monolith, no copied-reference fallback, pinned clone revision/images |
| API | canonical/alias mapping through real registry, parameter compatibility rejection, artifact resolver semantics, no mocked validator bypass |
| Frontend | no green on missing/stale evidence, exact reason/threshold display, variant/topology/contamination panels |
| Nextflow preview | all seven entrypoints and all profiles |
| Synthetic E3 | FASTQ, BAM, POD5, MM/ML, barcode, duplex, circular/SNV/indel/SV/mixed/contamination/topology fixtures |
| Production-like E4 | pinned GPU/model/container runtime, realistic read depth/error, MinKNOW handoff |
| Release E5 | full truth set, recovery/replay, operator runbook, independent approval |

Every runtime test must retain `.nextflow.log`, process command/log, versions, input digests, output manifest, artifact parser results, and exit code.

---

## 12. Dependencies and version policy

| Dependency | Current observed | Target policy |
|---|---|---|
| Nextflow | 25.10.1 wrapper | Pin tested Nextflow version; no runtime source patching |
| Dorado | host 1.1.1; SIF 1.3.1; current release 2.1.0 | Build/pin qualified container; exact digest and model compatibility matrix |
| Dorado models | none verified under required mount | Offline installed exact IDs + SHA-256; admin preflight |
| minimap2 | 2.24 in SIF; current 2.31 | Upgrade/pin and qualify `map-ont`, `lr:hq`, `splice(:hq)`, `asm5` |
| samtools | 1.13 | Upgrade/pin current supported version with required consensus/index behavior |
| bcftools | absent | Add/pin if used; otherwise document validated alternative |
| modkit | 0.6.1 in SIF; current 0.6.4 | Upgrade/pin; record filter thresholds |
| EPI2ME clone workflow | default unpinned; audited v1.8.4 | Pin commit/tag and all images; normalize outputs |
| MinKNOW API | upstream 6.10.1 | Pin package matching deployed MinKNOW; real-device qualification |
| POD5 tooling | not wired | Pin official converter/inspector for FAST5 support |

No production job may install/download a model, workflow, or container implicitly. Provisioning is an explicit admin operation with digest verification.

---

## 13. Risk register

| Risk | Consequence | Mitigation |
|---|---|---|
| Long-read systematic errors/homopolymers | False variant or false exact call | polished assembly, per-base confidence, strand/context review, truth-set calibration |
| Reference-biased consensus | Expected sequence can hide real insertions/rearrangements | independent observed assembly, normalized callset, assembly coverage, SV/topology checks |
| Circular origin artifacts | duplicated/lost calls at sequence boundary | rotate/double reference, normalize variants back to canonical coordinates, boundary fixtures |
| Mixed clone below consensus threshold | Consensus looks exact despite minority clone | VAF/depth model and explicit mixed-population REVIEW policy |
| Contamination/unexpected plasmid | High target coverage hides other DNA | unmapped/clipped/secondary assembly and taxonomic/reference screening where approved |
| Barcode bleed | Reads assigned to wrong clone | per-kit classification, ambiguity metrics, per-barcode isolation and controls |
| Model/runtime drift | Same input produces different result | pinned images/models/workflow commits and digests |
| Upstream schema change | Parser silently misses evidence | adapter version tests and fail-closed schema validation |
| Dirty-worktree integration | unrelated changes merged or audit behavior misattributed | clean worktree, scoped commits, diff gate |
| UI optimism | Operator sees green without evidence | backend-authoritative status, strict schema, no fallback rendering |

---

## 14. Explicit non-goals until P2 is green

- Do not prioritize methylation UX over construct verification.
- Do not call a QC manifest “verified” when it says `review_required`.
- Do not treat EPI2ME HTML/report existence as a PASS.
- Do not add more UI switches for unsupported model/duplex/barcode combinations.
- Do not revive deleted monolithic workflow wrappers.
- Do not merge from the current dirty checkout.

---

## 15. Sources

All web sources were checked on 2026-07-17. Repository/commit references are preferred where available.

### Official technology documentation

1. Oxford Nanopore, **Dorado documentation — Simplex basecalling**: <https://software-docs.nanoporetech.com/dorado/latest/basecaller/simplex/>
2. Oxford Nanopore, **Dorado documentation — Duplex basecalling**: <https://software-docs.nanoporetech.com/dorado/latest/basecaller/duplex/>
3. Oxford Nanopore, **Dorado documentation — Modified basecalling**: <https://software-docs.nanoporetech.com/dorado/latest/basecaller/mods/>
4. Oxford Nanopore, **Dorado documentation — Barcoding**: <https://software-docs.nanoporetech.com/dorado/latest/barcoding/barcoding/>
5. Oxford Nanopore, **Dorado model list**: <https://software-docs.nanoporetech.com/dorado/latest/models/list/>
6. Oxford Nanopore, **Dorado model downloader**: <https://software-docs.nanoporetech.com/dorado/latest/models/downloader/>
7. Oxford Nanopore, **Dorado releases** (2.1.0 dated 2026-07-13 at audit time): <https://github.com/nanoporetech/dorado/releases>
8. Oxford Nanopore, **POD5 file-format tooling / FAST5 conversion**: <https://github.com/nanoporetech/pod5-file-format>
9. Oxford Nanopore, **Plasmid sequencing from DNA using SQK-RBK114.24/.96**: <https://nanoporetech.com/document/rapid-sequencing-v14-plasmid-sequencing-sqk-rbk114-96>
10. Li, H., **minimap2 README v2.31**: <https://github.com/lh3/minimap2/blob/v2.31/README.md>
11. Oxford Nanopore, **modkit documentation**: <https://nanoporetech.github.io/modkit/>
12. GA4GH/samtools, **SAM/BAM tags and VCF specifications**: <https://github.com/samtools/hts-specs>
13. Oxford Nanopore, **MinKNOW API 6.10.1**, commit `f8ca84ff1b1f23676cd78e7171d20993e51e225a`: <https://github.com/nanoporetech/minknow_api>
14. Oxford Nanopore EPI2ME, **wf-clone-validation documentation**: <https://epi2me.nanoporetech.com/epi2me-docs/workflows/wf-clone-validation/>
15. EPI2ME Labs, **wf-clone-validation v1.8.4**, commit `b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42`: <https://github.com/epi2me-labs/wf-clone-validation/tree/v1.8.4>

### Primary plasmid/construct-verification literature

16. Brown et al. (2023), **Complete sequence verification of plasmid DNA using the Oxford Nanopore Technologies' MinION device**, BMC Bioinformatics. DOI: <https://doi.org/10.1186/s12859-023-05226-y>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC10039527/>
17. Mumm et al. (2023), **Multiplexed long-read plasmid validation and analysis using OnRamp**, Genome Research. DOI: <https://doi.org/10.1101/gr.277369.122>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC10317119/>
18. Wick et al. (2021), **Trycycler: consensus long-read assemblies for bacterial genomes**, Genome Biology. DOI: <https://doi.org/10.1186/s13059-021-02483-z>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC8442456/>
19. Emiliani et al. (2022), **Multiplexed Assembly and Annotation of Synthetic Biology Constructs Using Long-Read Nanopore Sequencing**, ACS Synthetic Biology. DOI: <https://doi.org/10.1021/acssynbio.2c00126>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC9295152/>
20. Currin et al. (2019), **Highly multiplexed, fast and accurate nanopore sequencing for verification of synthetic DNA constructs and sequence libraries**, Synthetic Biology. DOI: <https://doi.org/10.1093/synbio/ysz025>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC7445882/>
21. Vegh et al. (2024), **Biofoundry-Scale DNA Assembly Validation Using Cost-Effective High-Throughput Long-Read Sequencing**, ACS Synthetic Biology. DOI: <https://doi.org/10.1021/acssynbio.3c00589>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC10877595/>
22. Ramirez Rojas et al. (2024), **DuBA.flow - A Low-Cost, Long-Read Amplicon Sequencing Workflow for the Validation of Synthetic DNA Constructs**, ACS Synthetic Biology. DOI: <https://doi.org/10.1021/acssynbio.3c00522>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC10877597/>
23. Uematsu and Baskin (2025), **Barcode-free multiplex plasmid sequencing using Bayesian analysis and nanopore sequencing**, eLife. DOI: <https://doi.org/10.7554/eLife.88794>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC12029211/>
24. Vaisbourd et al. (2025), **Preventing Multimer Formation in Commonly Used Synthetic Biology Plasmids**, ACS Synthetic Biology. DOI: <https://doi.org/10.1021/acssynbio.4c00508>
25. Schimke and Vollmers (2026), **Sequencing complete plasmids on Oxford Nanopore Technologies sequencers using R2C2 and Chopper**, PLOS ONE. DOI: <https://doi.org/10.1371/journal.pone.0345168>; full text: <https://pmc.ncbi.nlm.nih.gov/articles/PMC13068223/>

---

## 16. Final acceptance statement

BioModStack may claim **automatic plasmid/construct verification** only after Phase 2 and Phase 3 gates are green and the Phase 7 truth-set release gate is independently approved. Until then, the honest product label is:

> **ONT sequence-QC and construct-review evidence generation — automatic verification not yet validated.**
