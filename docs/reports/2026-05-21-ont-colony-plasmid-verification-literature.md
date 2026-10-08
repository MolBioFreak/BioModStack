# ONT-based colony sequencing for plasmid / construct verification

Date: 2026-05-21
Scope: literature and practical protocols specifically relevant to sequencing from colonies or colony-derived material with Oxford Nanopore sequencing for plasmid / synthetic construct verification.

## Bottom line

Confirmed follow-up: the user-confirmed **cash-prize / fast-biology ideas** source is **Bryan Duoto's Colony-to-Sequence protocol**, not the protocols.io-only trail. The actionable artifact is **Colony-to-Sequence: Benchtop nanopore plasmid confirmation from colonies without overnight minipreps**, available at https://bryanduoto.bio/docs/colony-to-sequence-protocol.pdf. It is the most direct match for **same-day colony-to-sequence plasmid validation using homebrew SPRI / magnetic beads and Oxford Nanopore sequencing**.

The earlier protocol.io hits remain useful but are now secondary context: **Sarah Fletcher 2024, Sequencing Bacterial Isolates from Contaminated Food Samples with ONT** is still the strongest exact protocols.io direct-colony ONT hit, and **Ryan Teague 2024, Genomic Assembly of Plasmid DNA from Bacterial Cultures** is still the strongest plasmid-specific protocols.io colony/culture hit. They should not be treated as the confirmed remembered source.

The earlier DuBA.flow/arXiv and Chuzel/phiXXer hits remain valid technical anchors, but they are distinct from the confirmed Duoto/Niko Fast Biology Bounties artifact.

There is direct literature support for three colony-origin ONT validation patterns:

1. **Colony -> crude lysate -> vector-specific phi29 amplification -> barcoded ONT -> de novo insert assembly**
   - Strongest direct preprint/article anchor: Chuzel et al. 2024 / phiXXer.
   - Best for large-insert fosmid/cosmid clone sequencing directly from colonies.
   - Not a generic small-plasmid validation workflow unless adapted with appropriate vector-specific primers and circular-template assumptions.

2. **Colony/culture PCR -> barcoded amplicon ONT -> reference-based construct validation**
   - Strongest direct peer-reviewed anchors: Currin et al. 2019 and DuBA.flow 2024.
   - Strongest protocol/preprint-style anchor: Ramírez Rojas, Brinkmann & Schindler arXiv:2401.14191, which explicitly frames DuBA.flow from a single colony to final report.
   - Best for validating designed inserts/regions across many colonies.
   - Not automatically whole-plasmid unless primer scheme spans whole plasmid or target region.

3. **Colony -> crude colony prep or overnight culture/miniprep -> ONT whole-plasmid/whole-genome-style sequencing**
   - Primary practical protocol anchor: Duoto 2026 Colony-to-Sequence direct-colony micro-alkaline lysis + dual-SPRI plasmid enrichment + ONT rapid barcoding workflow.
   - Secondary protocol anchors: Fletcher 2024 protocols.io direct-colony Rapid PCR Barcoding workflow, Teague 2024 protocols.io plasmid assembly workflow, ONT Whole genome colony PCR extraction protocol, and Poochon colony whole-plasmid service page.
   - Whole-plasmid analysis anchors: OnRamp 2023, Brown et al. 2023, Circuit-seq 2022, SAVEMONEY/eLife 2025.
   - The whole-plasmid papers usually start from purified plasmid DNA, not a raw colony, but they define the downstream evidence contract BMS should use once colony-derived plasmid material has been sequenced.

## Direct colony-origin literature

### Chuzel et al. 2024 — direct colony fosmid/cosmid sequencing preprint / phiXXer

- Title: High-throughput nanopore DNA sequencing of large insert fosmid clones directly from bacterial colonies
- Preprint DOI: 10.1101/2024.02.05.578990
- Published journal/year: Applied and Environmental Microbiology, 2024
- Journal DOI: 10.1128/aem.00243-24
- PMID: 38767355
- PMCID: PMC11218629
- Code: https://github.com/aWormGuy/phiXXer
- Zenodo: https://zenodo.org/doi/10.5281/zenodo.10912937
- URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC11218629/

What it shows:
- This is the most literal direct colony ONT clone-sequencing anchor found: **rapid and high-throughput fosmid sequencing directly from E. coli colonies without liquid culturing or fosmid purification**.
- Sample prep uses crude colony lysate, vector/backbone-specific phi29-XT amplification, debranching of phi29 products, ONT native barcoding, pooled long-read sequencing, and de novo assembly/vector trimming.
- Demonstrated **96 fosmids in one ONT run** and then a larger validation set of **1,436 human gut microbiome fosmids in 15 runs**.
- Validated large-insert clone inserts around the fosmid/cosmid scale: approximately **30–40 kb** expected inserts.

Key protocol details recovered from full text:
- 96 clones spotted from glycerol stocks onto LB agar + 12.5 µg/ml chloramphenicol + 1x CopyControl inducing solution and grown overnight at 37 °C to form arrayed colonies.
- Each colony scraped/resuspended into the corresponding well of a 96-well PCR plate containing **5 µl 60 mM KOH, 2 mM EDTA pH 8.0**.
- Cell lysis: **60 °C for 10 min**.
- Neutralization: add **5 µl 75 mM Tris-HCl pH 7**.
- Amplification master mix includes phi29-XT reaction buffer, dNTPs, phi29-XT, and two pCC1 backbone primers with phosphorothioate bonds near the 3′ ends.
- Amplification: add **10 µl master mix** to each neutralized lysate; incubate **42 °C for 2–8 h**, then **65 °C for 10 min**. The paper found 8 h optimal among tested conditions.
- Debranching: **12 µl** amplified product + T7 endonuclease I, 37 °C for 2 h.
- ONT prep: FFPE/end repair, **Native Barcoding Kit 96 V14** with NB01–96, pooled 96 samples, AMPure cleanup, native adaptor ligation.
- Sequencing: GridION, R10.4.1 chemistry, super-accuracy basecalling, 260 bps accuracy mode, **48–72 h**.
- Assembly: phiXXer trims pCC1 vector sequence from reads, discards fragments <150 nt, assembles with Canu, selects the highest length/coverage contig, then trims residual vector from the contig. QC maps reads with minimap2/SAMtools and flags likely E. coli contamination.

Performance / validation details:
- For 96 T. kodakarensis genomic fosmids: >2.7 million reads; N50 ~11.5 kb; average/median reads per barcode 21,414 / 18,944.
- phiXXer assembled insert contigs for **89/96** fosmids.
- **80/89** assembled fosmids perfectly matched Illumina/plexWell reference data by BLASTN criteria (>99% coverage, 100% identity).
- Overall described as approximately **93% assembly rate**, with 92% of assemblies perfectly matching the orthogonal reference in repeated analyses.
- For the HGM library: **1,331/1,436** fosmids assembled; median insert contig size 32.3 kb; median depth 310x.

BMS relevance:
- This deserves a separate BMS mode, not just another generic plasmid QC path: `ont_colony_large_insert_clone_assembly` or `ont_colony_phi29_vector_rca_assembly`.
- Required manifest fields should include vector backbone, vector primer sequences, colony plate/well, induction condition, lysis chemistry, phi29/RCA settings, debranching status, barcode ID, ONT kit/chemistry, vector-trimming reference, assembler parameters, and contamination/reference checks.
- This is the best direct precedent for colony-to-de-novo clone insert sequence reconstruction in BMS, especially fosmid/cosmid/BAC-like workflows.

Caveat:
- It is not a generic small-plasmid verification method as written. It is built around fosmid/cosmid large-insert clones, pCC1-style vector-specific primers, circular template amplification, and phi29-specific artifact handling.
- BMS should not collapse it into `ont_colony_amplicon_construct_qc` or simple miniprep-like `whole_plasmid_qc`; it has different prep artifacts: RCA concatemers/chimeras, vector-primer specificity, debranching, vector trimming, and host gDNA contamination.

### Currin et al. 2019 — highly multiplexed colony/culture PCR ONT validation

- Title: Highly multiplexed, fast and accurate nanopore sequencing for verification of synthetic DNA constructs and sequence libraries
- Journal/year: Synthetic Biology, 2019
- DOI: 10.1093/synbio/ysz025
- PMCID: PMC7445882
- URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC7445882/

What it shows:
- Directly frames nanopore sequencing for verifying synthetic DNA constructs.
- Reports processing **576 constructs / six 96-well plates in one workflow in 72 h from E. coli colonies to analyzed data**.
- Method uses automated colony picking into LB + antibiotic, overnight culture, dilution, PCR template generation, sample barcoding PCR, pooling, ligation library prep, MinION sequencing, and custom analysis.
- Reported claim from abstract: statistical analysis of strand bias permits accurate sequence analysis with single-base resolution despite raw nanopore error.

Key protocol details recovered from full text:
- Transformant E. coli colonies picked into 1 ml LB + antibiotic and incubated overnight at 30 °C / 950 rpm.
- Cultures diluted 1:400 in dilute PBS to generate PCR templates.
- PCR: CloneAmp HiFi; 5 µl enzyme premix + 2.5 µl primer mix + 2.5 µl diluted template.
- Cycling: 95 °C 180 s; 35 cycles of 98 °C 20 s, 64 °C 15 s, 72 °C 210 s; final 72 °C 210 s.
- Amplicons pooled/purified; 1–1.5 µg DNA prepared with ONT 1D amplicon/cDNA by ligation kit SQK-LSK109 on R9.4.1 MinION.

BMS relevance:
- Direct support for a high-throughput colony-derived construct verification mode.
- This is an **amplicon/region-validation service**, not inherently whole-plasmid de novo verification.
- Good candidate service name: `ont_colony_amplicon_construct_qc`.

Caveat:
- Requires target-specific primers and careful amplicon design. It is not primer-free whole-plasmid sequencing.

### DuBA.flow 2024 — direct colony PCR amplicon sequencing for construct validation

- Peer-reviewed title: DuBA.flow—A Low-Cost, Long-Read Amplicon Sequencing Workflow for the Validation of Synthetic DNA Constructs
- Journal/year: ACS Synthetic Biology, 2024
- DOI: 10.1021/acssynbio.3c00522
- PMCID: PMC10877597
- Code: https://github.com/RGSchindler/DuBA.flow
- URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC10877597/
- Protocol/preprint title: Validation of Golden Gate assemblies using highly multiplexed Nanopore amplicon sequencing
- Protocol/preprint venue/year: arXiv, 2024
- arXiv: 2401.14191
- arXiv URL: https://arxiv.org/abs/2401.14191
- arXiv PDF: https://arxiv.org/pdf/2401.14191
- Authors on arXiv protocol: Adán A. Ramírez Rojas, Cedric K. Brinkmann, Daniel Schindler

What it shows:
- Directly states that construct validation can be performed by **barcoded colony PCR amplicon sequencing**.
- The arXiv protocol explicitly describes DuBA.flow as a start-to-finish workflow providing all steps **from a single colony to the final easy-to-interpret sequencing report**.
- Uses a two-step dual-barcode amplicon approach with reusable barcode primers and target-specific first-step primers.
- Shows amplicons can be generated directly from **E. coli colonies** for dual-barcoded ONT sequencing.
- The arXiv protocol says the colony protocol has been tested for **E. coli and S. cerevisiae colonies**.
- Demonstrates very high multiplexing: **1536 amplicons on a single Flongle**, estimated **0.10 € per sample** in their automation-assisted proof-of-concept.

Key protocol details / claims:
- Initial target-specific PCR adds general/M13-like sequences.
- Barcode primers are added in a second PCR; 96 forward x 96 reverse barcodes produce >9000 possible combinations.
- For E. coli colony candidates in the arXiv protocol: transfer cell material to 50 µl ddH2O, briefly vortex/resuspend, use **1 µl** as PCR template, and preserve remaining cell material for downstream culture/cryopreservation.
- For S. cerevisiae candidates: suspend in 50 µl ddH2O; mix 25 µl suspension with 25 µl 40 mM NaOH; lyse **95 °C for 15 min**; use **1 µl** as PCR template.
- First PCR example: 10 µl reaction, Q5-style high-fidelity polymerase, 10 cycles of 98 °C / 66 °C / 72 °C.
- Dilute first PCR **100-fold** before barcode PCR.
- Barcode PCR example: 10 µl reaction, 25 cycles, then pool all PCRs and purify.
- Library prep in arXiv protocol uses ONT Ligation Sequencing Kit series; their example references SQK-LSK109 and Flongle/MinION operation.
- The Dockerized DuBA.flow analysis emits a comprehensive summary report plus per-sample reports, including interactive HTML files.
- Barcoded sequences are pooled, purified, prepared with ONT ligation sequencing kit, and sequenced on Flongle.
- Direct colony PCR conditions must be optimized for each new primer pair to avoid nonspecific amplicons.
- Article reports no observed differences between amplicons generated from E. coli cells and purified plasmids.
- Workflow can deliver data the next day after overnight sequencing; transformation-to-data can be <72 h.
- In an automation example, approximately 65% of amplicons validated a specific construct, approximately 10% suggested potentially non-individual colonies, and approximately 25% yielded no amplicon.

BMS relevance:
- This is the most directly BMS-useful colony sequencing protocol for many colony candidates.
- BMS manifest should support colony_id/well_id, primer pair, expected amplicon/reference, barcode pair, amplicon yield/pass-fail, read count, coverage, variant candidates, and ambiguous/mixed-colony flag.

Caveat:
- Again, this is **amplicon construct validation**, not necessarily whole plasmid unless the chosen amplicon spans the intended plasmid/insert.

## Colony-to-ONT practical protocol anchors

### Duoto 2026 — confirmed Colony-to-Sequence direct-colony plasmid validation protocol

- Title: Colony-to-Sequence: Benchtop nanopore plasmid confirmation from colonies without overnight minipreps
- Author: Bryan Duoto
- Protocol PDF: https://bryanduoto.bio/docs/colony-to-sequence-protocol.pdf
- Prepared: 2025-06-07
- Revised / edited: 2026-03-10
- Source status: user-confirmed primary protocol PDF for the cash-prize / fast-biology colony-to-sequence target; protocol details below are grounded in the PDF text.

What it shows:
- This is the confirmed user-remembered **Fast Biology Bounties** artifact and the strongest directly actionable BMS protocol for same-day colony-to-sequence plasmid validation.
- The workflow explicitly validates plasmid constructs **directly from bacterial colonies without overnight minipreps**.
- The core prep is micro-alkaline lysis followed by a two-stage homebrew SPRI cleanup: low-ratio depletion of large DNA/debris followed by higher-ratio plasmid DNA capture.
- The downstream sequencing model is one ONT rapid barcode per colony/well, pooled sequencing, and barcode-specific clone validation with EPI2ME `wf-clone-validation`.

Key protocol details recovered from the PDF:
- Plate setup: add **15 µl nuclease-free water or TE** to each target well; pick **one isolated colony per well**; include at least a blank, known-correct positive control, and mixed-colony stress control.
- Micro-alkaline lysis: add **10 µl NaOH/SDS lysis mix**, incubate **90 s to 2 min**, add **10 µl potassium acetate neutralization mix**, rest **1-2 min**, then clarify by **3 min at ~3,000-4,000 x g** or passive settling.
- Critical upstream failure mode: genomic DNA shearing. The protocol repeatedly emphasizes gentle handling and no vortexing after lysis/neutralization.
- Dual-SPRI plasmid enrichment: transfer **20 µl clarified supernatant** to a fresh plate. Pilot low-ratio depletion conditions are **0.25x, 0.30x, or 0.35x** bead mix; capture conditions bring the retained supernatant to **0.90x or 1.00x** total bead ratio.
- Elution: elute captured plasmid DNA in **10-20 µl** water or low-salt buffer.
- Yield checkpoint: ONT plasmid sequencing protocol input is **50 ng high-molecular-weight plasmid DNA per sample**; wells below this threshold can be excluded or routed to an optional phi29 rolling-circle amplification rescue branch.
- ONT library: **SQK-RBK114.24** or **SQK-RBK114.96**, one barcode per colony-derived sample, pooled, cleaned, adapter-added, and loaded on **FLO-MIN114 R10.4.1**.
- Analysis: MinKNOW/Dorado live basecalling and demultiplexing, then EPI2ME `wf-clone-validation` per barcode. Optional inputs include expected plasmid size, full plasmid reference FASTA, insert reference FASTA, primer sequences, and host reference.
- Stopping criterion: not a fixed runtime promise; stop once each barcode reaches a clone-validation coverage target. The protocol proposes about **180x total mapped bases per barcode** as a practical heuristic derived from `wf-clone-validation`'s assembly coverage behavior.
- Pilot design: start with **24 colonies**, not 96. Include 3-5 kb high-copy, 7-10 kb moderate, and larger/lower-yield stress constructs before scaling.
- Day-1 targets: best-condition plasmid-enriched read fraction **>70%**, successful barcode calls **>=18/24**, high agreement with reference method, and total turnaround **<=3 h** for 24-plex.
- Unit economics: at full 96-plex utilization, approximate steady-state consumable cost is **$13-14 per colony**, with smaller-plex runs costing more per sample.

BMS relevance:
- This should be treated as the primary confirmed implementation anchor for `ont_colony_to_sequence_plasmid_validation` and a direct-colony prep route under `ont_colony_whole_plasmid_qc`.
- Required manifest fields should include colony plate/well, host strain and EndA status, expected plasmid/reference/insert metadata, micro-alkaline lysis condition, neutralization condition, low-ratio bead condition, final capture bead ratio, elution volume, DNA mass checkpoint, RCA rescue flag, ONT kit/chemistry, flow cell, barcode ID, MinKNOW/Dorado demux output, `wf-clone-validation` result paths, pass/fail state, insert/orientation/junction calls, assembly-size band, structural discrepancy flags, host-read fraction, and mixed-colony/cross-contamination warning.
- BMS product wording should distinguish this from ONT whole-genome colony PCR and from miniprep-based whole-plasmid sequencing: the defining sample-prep feature is **direct colony -> micro-alkaline lysis -> dual-SPRI plasmid enrichment -> rapid barcoded ONT**.

Caveat:
- This is still a pilot protocol / starting condition, not a universal validated production standard. Host strain, organism, plasmid size/copy number, and bead-ratio behavior should be treated as variables to validate before production scaling.
- Exact bead ratios should be represented as tunable DoE parameters in BMS rather than hard-coded constants.
- Downstream plasmid validation still needs BMS to preserve read evidence, consensus/reference comparison, per-base support, structural discrepancy flags, and truthful low-confidence / mixed-colony states.

### Fletcher 2024 protocols.io — direct colony PCR ONT workflow with plasmid-enrichment branch

- Title: Sequencing Bacterial Isolates from Contaminated Food Samples with ONT
- Protocol venue/year: protocols.io, 2024
- Published / created: 2024-04-23
- DOI: 10.17504/protocols.io.81wgbz7zogpk/v1
- Author / creator: Sarah Fletcher
- Canonical URL: https://www.protocols.io/view/sequencing-bacterial-isolates-from-contaminated-fo-81wgbz7zogpk/v1

What it shows:
- This is the best independent protocols.io hit for the user-requested **2022-2024 colony Nanopore NGS** memory.
- It describes a direct colony-origin workflow: obtain bacteria from a contaminated food sample, perform **colony PCR-based DNA extraction**, prepare a Rapid PCR Barcoding library, sequence on ONT MinION/GridION, and analyze with EPI2ME Labs.
- The workflow is not a vendor-only ONT page, even though it uses ONT kits/protocol logic.
- It is framed as bacterial isolate sequencing, not synthetic-construct validation, but it contains a plasmid-specific enrichment branch.

Key protocol details:
- Overview figure text: obtain bacterial culture, perform **colony PCR-based DNA extraction**, prepare DNA library with the **Rapid Barcoding Kit from ONT**, load into **ONT MinION flow cells**, sequence using the **GridION**, and analyze using Nextflow `wf-metagenomics` and WIMP / EPI2ME Labs.
- Direct colony step: obtain **1 colony** from the culture plate with sterile toothpick/needle/loop and swirl in **50 µl 10 mM Tris-HCl pH 8.0 for 10 s**.
- Supports multiple colonies: repeat for individual colonies / bacterial samples **up to 24**.
- Plasmid-enrichment branch: if interested in plasmid DNA, transfer the **50 µl cell suspension** to a 0.2 ml PCR tube and incubate **95 °C for 5 min**.
- The protocol notes that heating the colony suspension enriches observed **plasmid reads** in downstream sequencing relative to non-heat-treated libraries.
- Add **1 µl thermolabile Proteinase K**, incubate **37 °C for 15 min**, then **55 °C for 10 min**.
- Library prep: use **3 µl treated cell suspension** as Rapid PCR Barcoding Kit template; recommended PCR cycles are **25 cycles** without heat treatment and **30 cycles** with heat-treated cells.
- Barcoding / pooling: barcode reactions 1-24, quantify, pool equimolarly to **200-400 fmol** / approximately **400-800 ng**, AMPure cleanup, add rapid adapter.
- Sequencing: load into MinION flow cell, insert into GridION, run MinKNOW with the Rapid PCR Barcoding Kit selected.
- Analysis: EPI2ME Labs `wf-metagenomics` and WIMP, with barcode sample sheet and optional AMR data collection.

BMS relevance:
- Treat this as the best **independent protocols.io** direct-colony ONT prep anchor found in the earlier search, but secondary to the now-confirmed Duoto Colony-to-Sequence target for plasmid-validation implementation.
- Suggested BMS sample-prep preset: `ont_colony_rapid_pcr_barcoding_ngs`.
- Manifest fields should include colony source plate/well, colony suspension buffer, heat plasmid-enrichment flag, Proteinase K flag, Rapid PCR Barcoding kit/version, PCR cycle count, barcode ID, MinION/GridION run metadata, sample sheet, downstream workflow, and whether the analysis target is genome/isolate ID, plasmid-read enrichment, AMR, or construct/plasmid validation.

Caveat:
- This is not by itself a full whole-plasmid validation pipeline. It is a direct colony-to-ONT NGS prep and bacterial isolate analysis protocol with optional plasmid-read enrichment.
- It should be used in BMS as sample provenance / extraction evidence, then paired with a BMS downstream plasmid consensus / read-to-reference contract if the target is plasmid QC.
- Public X/Twitter search was blocked by login/challenge surfaces during this scan, so no exact tweet/status URL was verified.

### Teague 2024 protocols.io — plasmid assembly from bacterial colony/culture input

- Title: Genomic Assembly of Plasmid DNA from Bacterial Cultures
- Protocol venue/year: protocols.io, 2024
- Published: 2024-10-04
- DOI: none listed on protocol record during this scan
- Author / creator: Ryan Teague
- Canonical URL: https://www.protocols.io/view/genomic-assembly-of-plasmid-dna-from-bacterial-cul-8epv5r4z5g1b/v1

What it shows:
- This is the strongest **guy-authored, plasmid-specific protocols.io** hit in the 2022-2024 colony/plasmid/Nanopore search.
- It is colony-origin and plasmid-specific, but not raw colony direct: the protocol grows a bacterial colony/culture, extracts plasmid DNA, purifies it, prepares an ONT ligation library, and runs MinION/GridION sequencing.
- It was developed for the North Carolina State University BIT 495 Portable Genome Sequencing course.

Key protocol details:
- Description says the purpose is to complete genomic assemblies of **plasmid DNA from bacterial cultures** using long DNA reads to reduce multi-contig assemblies.
- Workflow summary: a bacterial colony is grown; plasmids are extracted; DNA is purified with a slight QIAprep Spin Miniprep variation; library is made using the **Ligation Sequencing Kit** with a plasmid-custom protocol; sample is loaded into a **MinION flow cell** and read out with MinION/GridION; analysis is performed with BV-BRC.
- Materials explicitly list **overnight culture of bacteria transformed with your plasmid**, QIAprep Spin Miniprep kit, **Ligation Sequencing Kit V14**, MinION Flow Cell R10.4.1, and GridION Sequencing Device Mk1.
- Prep target: adjust to **1 µg plasmid DNA** for repair/end-prep, then use ligation adapter, Salt-T4 DNA ligase, AMPure cleanup, and final library mixture.
- Sequencing section is intentionally high-level: load the library into MinION flow cell / GridION; analysis section points to BV-BRC.

BMS relevance:
- Suggested BMS mode/preset: `ont_colony_culture_plasmid_assembly` or `ont_miniprep_plasmid_assembly`.
- This is useful for the product shape “colony-selected plasmid -> full plasmid assembly,” but BMS should model the intermediate overnight culture/miniprep explicitly rather than labeling it direct colony sequencing.
- Manifest fields should include colony_id, culture condition, plasmid extraction method, purification method, input mass, ONT kit/chemistry, flow cell, GridION/MinION run metadata, assembler/tool, and expected plasmid/reference if known.

Caveat:
- As written, the protocol’s sequencing and analysis sections are brief; it is a wet-lab prep/procedure anchor, not a complete validated caller like OnRamp/Circuit-seq/SAVEMONEY.
- No exact X/Twitter post was verified for this protocol during this scan.

### Oxford Nanopore Technologies — Whole genome colony PCR extraction method

- Current title: Whole genome colony PCR
- Earlier title from ONT change log: PCR amplification of gram-negative bacterial DNA direct from a colony
- Type: ONT first-party extraction/library-prep protocol
- URL: https://nanoporetech.com/document/extraction-method/colony-pcr-dna
- First relevant dated version recovered from page change log: v2, 2021-04-21
- Later change log: v3, 2024-04-02 removed Exonuclease I reference for V14 Rapid PCR Barcoding Kit
- Last observed page metadata during this scan: last updated 2026-05-07

What it shows:
- This is the direct colony-to-ONT workup that was easy to underweight in the first pass: ONT provides a protocol to extract and prepare **genomic and plasmid DNA from a bacterial colony**, verified with gram-negative E. coli.
- A colony is picked from a plate, treated with Proteinase K, and prepared for sequencing with the Rapid PCR Barcoding Kit; ONT says sequencing performance was assessed on GridION.
- Explicitly includes a plasmid-enrichment branch.

Key protocol details:
- Pick one colony into 50 µl 10 mM Tris-HCl pH 8.0 and swirl until turbid.
- For plasmid DNA enrichment: incubate the 50 µl suspension at 95 °C for 5 min.
- Add 1 µl thermolabile Proteinase K; incubate 37 °C 15 min, then 55 °C 10 min.
- Prepare library with ONT Rapid PCR Barcoding Kit using 3 µl treated suspension as template.
- Recommended PCR cycles: 25 cycles without heat treatment; 30 cycles with heat-treated cells.
- Page notes heating enriched observed plasmid reads downstream versus non-heat-treated libraries.
- Reported post-PCR yield: 30–60 ng/µl.

BMS relevance:
- This is the strongest first-party practical protocol for **colony -> ONT reads with plasmid enrichment** and should be treated as the canonical direct colony prep preset.
- BMS should expose this separately from miniprep-based whole-plasmid sequencing: `sample_prep=ont_whole_genome_colony_pcr`, with flags for `heat_plasmid_enrichment=true`, `proteinase_k=true`, kit version, PCR cycles, and colony source plate/well.
- BMS can represent this as a sample-prep provenance preset, not as proof of complete plasmid validation by itself.

Caveat:
- The ONT page is an extraction/library-prep protocol, not a full validated plasmid-variant caller. BMS still needs downstream read-to-reference/consensus evidence.
- The current page is post-2024/current-site content, but the relevant colony protocol lineage is explicitly dated by ONT's own change log to 2021-04-21 under the older title.

### Poochon Scientific — colony whole-plasmid nanopore service

- Page: Whole Plasmid Sequencing
- URL: https://www.poochonscientific.com/services/nanopore-sequencing/whole-plasmid-sequencing/

What it shows:
- Commercial service explicitly offers **Nanopore Whole Plasmid Sequencing of Colony Samples**.
- Accepts colony suspension, liquid culture, glycerol stock, or agar plate for plasmids 2–30 kb.
- States service includes colony incubation, plasmid MiniPrep, long-read ONT sequencing, sequence in TXT/FASTA, and annotated plasmid map.
- Turnaround shown as 48 h; no sequencing primer required.

BMS relevance:
- Confirms the product shape users expect: colony input -> whole-plasmid sequence/map deliverable.
- Useful competitive/service anchor, but not a peer-reviewed protocol.

Caveat:
- It is a vendor service page; underlying analysis pipeline and error model are not disclosed.

## Whole-plasmid ONT analysis literature useful after colony-derived plasmid prep

### Mumm et al. 2023 — OnRamp

- Title: Multiplexed long-read plasmid validation and analysis using OnRamp
- Journal/year: Genome Research, 2023
- DOI: 10.1101/gr.277369.122
- PMCID: PMC10317119
- Web app: https://onramp.boylelab.org/

What it shows:
- Reference-based full-plasmid validation using pooled plasmid ONT reads.
- Provides custom wet-lab protocols plus a web app that generates reference-consensus alignments, quality scores, and read-level views.
- Uses barcoding-free pooled plasmid preparation; two prep routes: ONT rapid/transposase-based and restriction digest + ligation-based.
- Designed for medium-throughput routine plasmid validation and easier interpretation by bench scientists.

BMS relevance:
- Downstream evidence model for `ont_colony_whole_plasmid_qc` after colony/miniprep or ONT colony-prep protocol.
- Manifest should include expected reference(s), consensus, quality score, read-level evidence/IGV, mutation/indel calls, homopolymer/end-coverage caveats.

Caveat:
- Main protocols start from purified/equimolar pooled plasmid DNA, not raw colony input.

### Brown et al. 2023 — complete plasmid sequence verification

- Title: Complete sequence verification of plasmid DNA using the Oxford Nanopore Technologies’ MinION device
- Journal/year: BMC Bioinformatics, 2023
- DOI: 10.1186/s12859-023-05226-y
- Code: https://github.com/scottdbrown/minion-plasmid-consensus

What it shows:
- Cost-effective MinION plasmid sequencing and consensus generation.
- Abstract reports pseudopairing reduces read error from 5.3% to 0.53% and pileup consensus provides per-base counts/confidence scores.
- Demonstrates 100% consensus accuracy for pure plasmid samples in their test cases and sensitivity to indels/SNVs.

BMS relevance:
- Good per-base support / consensus confidence model for whole-plasmid colony-derived samples after miniprep/colony-prep.

Caveat:
- Uses pure plasmid samples and a full flow cell per plasmid in their procedure, so not directly high-throughput colony screening.

### Emiliani et al. 2022 — Circuit-seq

- Title: Multiplexed Assembly and Annotation of Synthetic Biology Constructs Using Long-Read Nanopore Sequencing
- Journal/year: ACS Synthetic Biology, 2022
- DOI: 10.1021/acssynbio.2c00126
- PMCID: PMC9295152

What it shows:
- High-throughput plasmid sequencing using DNA transposition and ONT.
- Builds full-length contiguous plasmid maps without prior knowledge of the underlying sequence.
- Can estimate plasmid contamination levels and characterize methylation marks.

BMS relevance:
- Useful when colony-derived plasmid material needs de novo/unknown construct reconstruction rather than only reference validation.

Caveat:
- Not a direct raw-colony protocol; purified plasmid prep remains upstream.

### Uematsu & Baskin 2025 — SAVEMONEY

- Title: Barcode-free multiplex plasmid sequencing using Bayesian analysis and nanopore sequencing
- Journal/year: eLife, 2025
- DOI: 10.7554/eLife.88794
- Preprint DOI: 10.1101/2023.04.12.536413
- Code: https://github.com/MasaakiU/MultiplexNanopore
- PyPI: https://pypi.org/project/savemoney/

What it shows:
- Expected-sequence, barcode-free pooled plasmid verification with Bayesian read assignment/consensus.
- Relevant to pooled colonies/minipreps if expected construct sequences are known.

BMS relevance:
- Candidate later mode: `ont_colony_pool_plasmid_verify` or `ont_plasmid_pool_verify`.

Caveat:
- Depends on expected construct sequences; not generic unknown demultiplexing.

## Recommended BMS split

### 1. `ont_colony_large_insert_clone_assembly`

Best-supported direct colony-to-de-novo clone-sequencing mode from Chuzel/phiXXer.

Inputs:
- FASTQ/POD5/basecalled reads, ideally demuxed per barcode
- plate/well/colony metadata
- vector backbone FASTA
- vector-specific phi29/RCA primer metadata
- expected vector family, e.g. pCC1FOS/fosmid/cosmid/BAC-like
- prep metadata: lysis chemistry, neutralization, phi29 polymerase, RCA time, debranching enzyme, ONT kit/barcode set

Outputs:
- demuxed reads per colony/well
- vector-trimmed reads
- assembled insert contig FASTA
- residual-vector report
- host-gDNA contamination report
- coverage/depth metrics
- assembly pass/fail/ambiguous state
- optional taxonomic annotation for metagenomic inserts
- `qc_manifest.json`

### 2. `ont_colony_amplicon_construct_qc`

Best supported colony-specific mode for designed amplicon/construct-region validation.

Inputs:
- FASTQ or POD5/basecalled FASTQ
- plate/well/colony metadata
- expected amplicon or plasmid/reference FASTA/GenBank
- primer pair metadata
- barcode pair metadata

Outputs:
- demuxed reads per colony/well
- BAM/BAI to expected reference
- per-base support TSV
- consensus FASTA
- variant/indel candidates
- mixed/non-individual colony flag
- no-amplicon/low-read/no-specific-band state
- HTML/IGV view
- `qc_manifest.json`

### 3. `ont_colony_to_sequence_plasmid_validation` / `ont_colony_whole_plasmid_qc`

Confirmed primary direct-colony implementation anchor: **Duoto 2026 Colony-to-Sequence**.

Colony input, but sample prep may be:
- direct Duoto **Colony-to-Sequence** route: one colony -> 15 µl water/TE suspension -> micro-alkaline lysis -> potassium acetate neutralization -> gentle clarification -> dual-SPRI low-ratio depletion / high-ratio plasmid capture -> ONT rapid barcoding -> `wf-clone-validation`;
- direct ONT **Whole genome colony PCR** route: one colony -> 50 µl Tris suspension -> optional 95 °C plasmid-enrichment heat -> thermolabile Proteinase K -> Rapid PCR Barcoding using 3 µl treated colony suspension;
- colony -> miniprep -> ONT whole-plasmid sequencing; or
- vendor/service-style colony incubation + miniprep + ONT.

Outputs:
- full-plasmid consensus
- read-to-reference evidence
- per-base support/confidence
- structural/multimer/chimera warnings where possible
- annotated map if implemented
- explicit sample-prep provenance

### 4. `ont_colony_pool_plasmid_verify`

Later, for pooled expected constructs using OnRamp/SAVEMONEY-like semantics.

Outputs need assignment confidence and ambiguity, not just per-sample pass/fail.

## Important caveats for product wording

- Do not claim Chuzel/phiXXer as generic small-plasmid validation without adapting and validating the vector-specific phi29/RCA design; it is a direct colony fosmid/cosmid/large-insert clone workflow.
- Do not call colony PCR amplicon workflows “whole plasmid verification” unless the amplicon/reference actually covers the whole plasmid.
- Do not claim Plasmidsaurus/Poochon parity unless BMS emits comparable whole-plasmid consensus + map + per-base support/read evidence.
- For colony-origin inputs, preserve sample-prep provenance because raw colony PCR, overnight culture PCR, colony-miniprep, and purified plasmid workflows have different failure modes.
- Mixed colony / non-individual colony is a first-class biological state, not just a sequencing failure.
