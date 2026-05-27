# ONT / NGS Standalone Service Literature Review — 2026-05-21

## Purpose

This review grounds the BioModStack NGS/ONT rebuild in recent Oxford Nanopore literature, preprints, and open-source tooling from roughly 2021–2026. The goal is not to preserve the old MVP methylation path. The goal is to define what a mature standalone BMS ONT service family should support: plasmid/construct verification, colony-derived sequencing workflows, FASTQ/BAM/POD5 analysis, modified-base evidence, assembly/consensus, manifest-backed reporting, and runtime-grade validation.

## Executive synthesis for BMS

The last five years of ONT work point to a clear architecture for BMS:

1. Treat ONT as a standalone service family, not as a branch inside `main.nf`.
2. Separate user-facing modes from runtime internals:
   - plasmid/construct verification;
   - colony/fosmid/clone sequencing;
   - FASTQ read-QC against a known reference;
   - BAM/POD5 methylation/modbase analysis;
   - bacterial isolate/clone assembly;
   - optional adaptive-sampling and targeted sequencing later.
3. Make the manifest the product contract, not a convenience file.
4. For plasmids and colonies, prioritize full-construct evidence and per-base support over generic “methylation job” outputs.
5. Do not overclaim methylation/modbase results for FASTQ-only paths.
6. Current ONT tooling is good enough to build a real local service, but the BMS layer must encode limitations, input mode, reference identity, artifact states, and runtime provenance.

## High-priority papers / tools

### 1. SAVEMONEY — barcode-free multiplex plasmid sequencing

- Citation: Uematsu et al., eLife, 2025; preprint 2023.
- Title: “Barcode-free multiplex plasmid sequencing using Bayesian analysis and nanopore sequencing.”
- DOI: `10.7554/eLife.88794`; preprint DOI `10.1101/2023.04.12.536413`.
- Code / resources:
  - `https://github.com/MasaakiU/MultiplexNanopore`
  - `https://pypi.org/project/savemoney/`
  - Google Colab notebook linked from the paper.
- What it shows:
  - A computational strategy, SAVEMONEY / “Simple Algorithm for Very Efficient Multiplexing of Oxford Nanopore Experiments for You,” for mixing plasmids without barcodes and computationally de-mixing reads.
  - Uses a pre-survey step to choose mixtures, then sequence classification, alignment, consensus, and Bayesian analysis using an expected plasmid-construction error prior.
  - The abstract/full text states plasmids differing by as little as two bases can be mixed, and routine multiplexing of six plasmids per 180 reads can maintain high consensus accuracy.
- Why it matters for BMS:
  - Directly relevant to a low-cost plasmid/construct verification service.
  - Suggests BMS should support “expected construct set + pooled reads” as a first-class mode, not only one-sample FASTQ/BAM analysis.
  - Needs a manifest model that can represent per-construct assignment confidence, Bayesian posterior/confidence, ambiguity between similar plasmids, and consensus/per-base evidence.
- Caveat:
  - This is not a general demultiplexer for unknown samples. It assumes expected plasmid sequences and a construction-error prior. BMS should expose this as expected-construct validation, not unknown sample discovery.

### 2. Complete plasmid sequence verification with MinION

- Citation: Brown et al., BMC Bioinformatics, 2023.
- Title: “Complete sequence verification of plasmid DNA using the Oxford Nanopore Technologies’ MinION device.”
- DOI: `10.1186/s12859-023-05226-y`.
- Code:
  - `https://github.com/scottdbrown/minion-plasmid-consensus`
- What it shows:
  - Whole-plasmid sequence verification with MinION as an alternative to primer-walk Sanger.
  - Pseudopairing reads for consensus basecalling reduced reported read error from 5.3% to 0.53% in their workflow.
  - Pileup consensus provides per-base counts and confidence scores.
  - Demonstrated 100% consensus accuracy for pure plasmid samples in their test set and sensitivity to SNPs/indels/subclonal templates.
- Why it matters for BMS:
  - Per-base counts/confidence are exactly the kind of artifact BMS should expose in a construct-validation manifest.
  - BMS should emit both a consensus sequence and the evidence behind it, not only a “pass/fail.”
- Caveat:
  - Uses a full flow cell per plasmid in the described high-stringency procedure; this is high confidence but not necessarily cost-optimized for routine colony screening. SAVEMONEY/R2C2-like strategies address the multiplexing side.

### 3. Direct-from-colony high-throughput fosmid sequencing

- Citation: Applied and Environmental Microbiology, 2024.
- Title: “High-throughput nanopore DNA sequencing of large insert fosmid clones directly from bacterial colonies.”
- DOI: `10.1128/aem.00243-24`; preprint DOI `10.1101/2024.02.05.578990`.
- Code:
  - `https://github.com/aWormGuy/phiXXer`
  - Zenodo DOI linked in paper: `10.5281/zenodo.10912937`.
- What it shows:
  - Sequencing of large-insert fosmids directly from E. coli colonies without liquid culture or fosmid purification.
  - Uses crude lysate + phi29 polymerase amplification, ONT sequencing, and a bioinformatics pipeline called phiXXer for de novo assembly and vector trimming.
  - Demonstrates accurate sequencing of 96 fosmids in a single run for ~30–40 kb inserts.
- Why it matters for BMS:
  - This is the strongest direct “colony NGS with nanopore” anchor found in this pass.
  - BMS should support a “colony-derived clone sequencing” service mode distinct from normal purified-plasmid mode.
  - The manifest must represent colony/clone source, amplification method, vector-trimmed insert sequence, assembly status, read depth, vector contamination/trim stats, and failed/ambiguous clones.
- Caveat:
  - Fosmid/cosmid large-insert workflows are not identical to routine small plasmid miniprep workflows. BMS should not blindly generalize 96-fosmid performance to every construct class without validation fixtures.

### 4. Nanopore identification of bacterial colonies

- Citation: Environmental Science: Atmospheres, 2024.
- Title: “Application of nanopore sequencing for accurate identification of bioaerosol-derived bacterial colonies.”
- DOI: `10.1039/d3ea00175j`; preprint DOI `10.1101/2023.01.03.522650`.
- What it shows:
  - Full-length 16S nanopore sequencing can outperform/clarify noisy Sanger cases for bacterial colony identification, including mixed/multi-species colony situations.
- Why it matters for BMS:
  - Colony-origin ONT services should not be limited to plasmid/fosmid constructs. There is also a bacterial colony ID / isolate QC lane.
  - BMS could eventually support “colony identity from long 16S/amplicon reads” with explicit taxonomic assignment confidence.
- Caveat:
  - This is colony identification, not construct validation. It belongs to an adjacent isolate/colony-ID service mode.

### 5. R2C2/Chopper complete plasmid sequencing

- Citation: PLOS One, 2026.
- Title: “Sequencing complete plasmids on Oxford Nanopore Technologies sequencers using R2C2 and Chopper.”
- DOI: `10.1371/journal.pone.0345168`.
- What it shows:
  - Adapts ONT R2C2 sequencing for rapid, low-cost complete plasmid sequencing, individually or pooled.
  - Develops Chopper to produce full-length plasmid sequences.
- Why it matters for BMS:
  - Another direct plasmid-complete-sequencing strategy, complementary to SAVEMONEY and pileup/pseudopairing approaches.
  - Worth deeper follow-up before deciding whether BMS should support R2C2/Chopper-like mode, SAVEMONEY-like expected-set demux, or both.
- Caveat:
  - Needs protocol/runtime review before integration. Treat as a candidate mode, not immediate acceptance.

### 6. Autocycler / consensus bacterial assembly

- Citation: Wick et al., Bioinformatics, 2025.
- Title: “Autocycler: long-read consensus assembly for bacterial genomes.”
- DOI: `10.1093/bioinformatics/btaf474`.
- Code:
  - `https://github.com/rrwick/Autocycler`
- What it shows:
  - Automated consensus assembly from multiple alternative long-read assemblies of bacterial genomes.
  - Extends the Trycycler idea toward more scalable automated bacterial assembly.
- Why it matters for BMS:
  - If BMS adds bacterial isolate or clone assembly, a modern service should not just run one assembler and publish a contig.
  - Assembly service manifests should include assembler(s), polishing/consensus strategy, circularity, plasmid reconstruction, contamination checks, and structural ambiguity.
- Caveat:
  - More relevant to isolate/fosmid/bacterial genome assembly than to simple plasmid verification.

### 7. Adaptive sampling / Readfish / UNCALLED ecosystem

- Citations / tools:
  - Readfish PromethION scale-up: Genome Research, 2025, DOI `10.1101/gr.279329.124`.
  - Adaptive sampling benchmark: Genome Biology, 2025, DOI `10.1186/s13059-025-03729-w`.
  - UNCALLED GitHub: `https://github.com/skovaka/UNCALLED`.
  - Readfish GitHub: `https://github.com/LooseLab/readfish`.
- What it shows:
  - ONT can select/reject molecules in real time to enrich targets or deplete unwanted reads.
  - Tooling is active but application-dependent, and benchmarking emphasizes that optimal enrichment depends on task and tool choice.
- Why it matters for BMS:
  - Not Phase 1 for plasmid/colony QC, but very relevant for future targeted ONT services.
  - BMS architecture should not preclude real-time runs, barcode-aware adaptive sampling, or live run control.
- Caveat:
  - Requires live sequencer integration and low-latency run control. Do not fake this with post-hoc filtering.

### 8. ONT native tooling layer: Dorado, Bonito, Remora, modkit, medaka

- Current open-source/tooling anchors checked via GitHub API:
  - Dorado: `https://github.com/nanoporetech/dorado` — ONT basecaller.
  - Bonito: `https://github.com/nanoporetech/bonito` — PyTorch basecaller.
  - Remora: `https://github.com/nanoporetech/remora` — modified-base calling separated from basecalling.
  - modkit: `https://github.com/nanoporetech/modkit` — modified-base analysis utilities.
  - medaka: `https://github.com/nanoporetech/medaka` — ONT sequence correction/consensus.
- Why it matters for BMS:
  - These are runtime components, not product semantics.
  - BMS should wrap them behind explicit services and manifests:
    - basecalling provenance;
    - model/version;
    - flowcell/chemistry assumptions;
    - modbase model availability;
    - FASTQ/BAM/POD5 input state;
    - per-artifact quality and limitations.
- Caveat:
  - ONT tool/model version churn is real. BMS must pin runtime images/models and report versions in manifests.

## Proposed BMS service families suggested by literature

### A. ONT construct/plasmid verification service

Inputs:
- expected plasmid/construct sequence(s);
- FASTQ/BAM/POD5 or raw run folder;
- optional pooled sample design for SAVEMONEY-style demux;
- optional known barcode/sample sheet.

Outputs:
- consensus FASTA/GBK if safe;
- per-base support table;
- SNP/indel candidate table;
- pass/warn/fail construct verdict;
- ambiguity table for similar constructs;
- raw read alignment BAM/BAI;
- IGV/report artifacts;
- manifest with expected sequence identity, coordinate system, limitations.

Key literature anchors:
- SAVEMONEY, eLife 2025.
- MinION plasmid consensus, BMC Bioinformatics 2023.
- R2C2/Chopper plasmid sequencing, PLOS One 2026.

### B. Colony-derived clone/fosmid service

Inputs:
- colony/clone identifiers;
- vector/backbone reference;
- expected insert size or expected construct where available;
- reads from colony-derived phi29/RCA or other prep.

Outputs:
- vector-trimmed insert sequence;
- assembly contigs;
- circularity/insert-boundary evidence;
- clone pass/fail/ambiguous state;
- failed clone reason;
- per-clone read depth and barcode/demux stats.

Key literature anchors:
- Direct-from-colony fosmid sequencing, AEM 2024.
- phiXXer pipeline.

### C. Colony/isolate identity service

Inputs:
- colony amplicon reads, e.g. 16S or targeted loci;
- optional expected organism/genus/species.

Outputs:
- taxonomic assignment with confidence;
- mixed colony flag;
- ambiguous/multi-species state;
- read support table.

Key literature anchor:
- Bioaerosol-derived bacterial colony ID, Environmental Science: Atmospheres 2024.

### D. ONT methylation/modbase service

Inputs:
- POD5 or modbase-capable BAM;
- reference;
- basecaller/modbase model metadata.

Outputs:
- modkit pileup/summary;
- bedMethyl/modBAM artifacts;
- motif summaries where relevant;
- modified-base unavailable/not-applicable states for FASTQ-only runs.

Key tooling anchors:
- Dorado, Remora, modkit.
- Modbed visualization paper, Cell Genomics 2023, DOI `10.1016/j.xgen.2023.100455`.
- Dogme Nextflow modification reprocessing pipeline, Bioinformatics 2026, DOI `10.1093/bioinformatics/btag066`.

### E. Bacterial/clone assembly service

Inputs:
- FASTQ/BAM reads;
- optional reference/vector;
- optional isolate metadata.

Outputs:
- assembly FASTA;
- assembly graph/logs;
- circular contig calls;
- plasmid reconstruction status;
- consensus/polishing metrics;
- quality and contamination flags.

Key anchors:
- Flye, Raven, Trycycler, Autocycler ecosystem.
- Autocycler, Bioinformatics 2025.

## Architecture implications for BioModStack

1. `ngs.nf` should be a dispatcher, not the product.
   - It should route to explicit workflow/model services like `ont_plasmid_verify`, `ont_colony_fosmid`, `ont_fastq_construct_qc`, `ont_modbase`, `ont_isolate_assembly`.

2. The model registry should stop mixing runtime internals and scientific inputs.
   - Separate: sample/input mode, expected construct/reference, analysis mode, runtime assets, advanced options.

3. The API should expose NGS-specific service semantics.
   - A generic “launch model” endpoint is not enough for polished ONT services.
   - Add mode-specific validation/preflight before Nextflow.

4. The manifest needs subtypes.
   - `sequence_qc.manifest.v1` can be the envelope.
   - Add sub-contracts:
     - `ont_plasmid_verify.v1`
     - `ont_colony_clone.v1`
     - `ont_modbase.v1`
     - `ont_assembly.v1`
     - `ont_colony_id.v1`

5. UI should be mode-specific but contract-backed.
   - The NGS Toolkit should not attempt to infer meaning from stage-output paths.
   - It should render from manifest view models with explicit unavailable/missing/legacy states.

6. Runtime provenance must be first-class.
   - ONT chemistry/model/tool churn means every output must record Dorado/Bonito/Remora/modkit/medaka versions, model names, reference hash, and container/runtime image.

## Immediate BMS implementation recommendation

Do not start with adaptive sampling or general bacterial WGS.

Start with two concrete, testable services:

1. `ont_fastq_construct_qc`
   - FASTQ + expected construct/reference.
   - Outputs: BAM/BAI, per-base support, consensus, variant candidates, manifest, IGV/report.
   - This is closest to current BMS code and can validate the manifest-first contract.

2. `ont_plasmid_pool_verify`
   - Expected construct set + pooled FASTQ/BAM.
   - Implement SAVEMONEY-inspired demux/assignment as a later subphase after baseline construct QC is stable.
   - Outputs per-construct assignment confidence, consensus/evidence, ambiguity state.

Then add:

3. `ont_colony_clone_qc`
   - Colony/clone IDs + vector/reference expectations.
   - Initially support colony-derived FASTQ from an external prep; later add phi29/RCA-specific metadata and phiXXer-style assembly/vector trimming.

4. `ont_modbase_methylation`
   - POD5/BAM-only path.
   - Keep separate from FASTQ construct QC to avoid false methylation expectations.

## Caveats / follow-up needed

- This was a targeted scan, not a final systematic review.
- Need deeper full-text review of R2C2/Chopper before deciding whether it belongs in BMS.
- Need direct testing of SAVEMONEY/MultiplexNanopore on synthetic BMS construct fixtures before adopting its method.
- Need evaluate whether phiXXer is directly useful for BMS or whether its ideas should inform a BMS-native colony clone workflow.
- Need license/runtime checks before vendoring or containerizing any external OSS tooling.
- Need define sample prep metadata separately from computational service support; BMS can analyze colony-derived data before it fully automates colony wet-lab preparation.

## Shortlist for deeper review next

1. SAVEMONEY / MultiplexNanopore source code and output formats.
2. R2C2 + Chopper plasmid workflow.
3. phiXXer pipeline and vector-trimming assumptions.
4. Current BMS `scripts/build_sequence_qc_manifest.py` versus a plasmid/colony manifest subtype.
5. Dorado/modkit/Remora version/model pinning strategy.
6. Existing BMS NGS UI against the service-family split above.
