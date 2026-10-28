# BNS DNA and plasmid analysis

The dedicated Plasmid analysis → Plasmid read QC path and general DNA analysis
share the reference-analysis implementation, model settings, optional QC controls,
modkit stages and result artifacts. DNA analysis retains the canonical
`ont_basecall_dna` API identity for saved jobs. DNA reference QC defaults off;
plasmid QC defaults on. Modification calling and methylation tables default off
in both. Selecting an analysis does not imply a modification model.

## Inputs

- **POD5:** Dorado basecalls raw signal. Tandem modkit requires a supported
  modification model, reference alignment, and the resulting MM/ML-tagged BAM.
  The installed modified-base choices require DNA HAC simplex.
- **BAM:** reuse mapped reads, or explicitly realign against the selected reference.
  Modkit consumes the original modification probabilities preserved through BAM
  preparation/alignment; converting BAM to FASTQ for optional QC is a separate
  branch and is never the input to modkit.
- **FASTQ:** supported for reference-backed analysis/QC. It does not contain BAM
  MM/ML probabilities and cannot be used for modkit. This is not raw signal and
  is not sent back through Dorado basecalling.
- **BLOW5/SLOW5:** supported by the separate raw-signal import/conversion subsystem,
  not as direct primary inputs to these Dorado workflows. Imported SLOW5 becomes
  indexed BLOW5. Use Restore POD5 on a ready BLOW5 representation before
  basecalling. This release does not claim direct Dorado BLOW5/SLOW5 input.
- Barcoded basecalling remains separate from downstream per-barcode analysis.

Optional circular-reference/dimer and construct-specific QC is opt-in for general
DNA. Those results retain their plasmid/construct interpretation; they are not
claims of general genomic variant analysis.

## Execution target

Choose local or a remote execution target, then CPU or CUDA for basecalling.
The device selector is scoped to that target. A controller's CPU installation
must not select CPU implicitly for a remote worker. Explicit saved/operator
choices are preserved, including CPU on a worker. The scheduler owns physical
GPU assignment; local GPU pins are not sent to a remote worker or a CPU launch.
Remote device choices are intent, **not a worker hardware probe or guarantee**.
Native execution reports unavailable hardware. Instrument control stays local.

## Evidence scope

Focused tests cover typed contract/digest discovery, setting normalization,
mounted prepare/submit transport, and real Nextflow graphs. Native CPU tests run
both standalone workflows on a synthetic MM/ML-tagged BAM through installed
samtools and containerized modkit and check produced BED/summary outputs.
Synthetic controls qualify transport/tool execution, not biological accuracy.
This does not establish new POD5 inference, local CUDA, remote GPU, or raw-format
round-trip qualification. Pushing the edition branch does not deploy services.

## Upstream format references

- [Dorado 2.1 README](https://github.com/nanoporetech/dorado/blob/release-v2.1/README.md): POD5 basecalling; HTS read alignment.
- [modkit](https://github.com/nanoporetech/modkit): modified bases stored in BAM MM/ML tags.
- [blue-crab](https://github.com/Psy-Fer/blue-crab): POD5 ↔ SLOW5/BLOW5 conversion; conversion support is not native Dorado input support.
