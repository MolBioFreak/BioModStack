# DNA and plasmid analysis

- **Plasmid analysis → Plasmid read QC** retains reference-backed QC/consensus and optionally runs modkit in tandem. Assembly/clone validation remains a separate choice.
- **DNA analysis** retains the `ont_basecall_dna` API identity. It accepts POD5, BAM, or FASTQ, with optional shared reference QC and modification analysis. QC defaults off; opting into plasmid/dimer QC uses the same controls and algorithms as the dedicated plasmid workflow, not a new generic consensus implementation.
- **Generate methylation tables** uses the shared modkit validation, pileup and summary modules. It does not replace the original input or tagged alignment BAM. The modification model, threshold (including zero), QC switch and basecalling device remain explicit settings.

## Input formats

| Input | Supported path | Modification analysis |
| --- | --- | --- |
| POD5 | Direct Dorado basecalling; reference alignment when requested | Select a supported DNA HAC simplex modification model, then optionally enable modkit |
| BAM | Existing reads are sorted/indexed; optional reference realignment | Existing meaningful paired MM/ML tags are retained; modkit consumes them, not new signal calls |
| FASTQ | Existing basecalled reads are aligned to a reference; optional QC | FASTQ has bases/qualities, not MM/ML probabilities. It cannot reconstruct modifications |
| BLOW5 | Existing raw-signal import/validation/indexing, then **Restore POD5** | Basecall the restored POD5; not a direct Dorado input in this integration |
| SLOW5 | Existing raw-signal import converts losslessly to indexed BLOW5 with slow5tools, then **Restore POD5** | Same restored-POD5 path; not direct FASTQ/BAM conversion |

Mapped BAM preparation preserves tags. For unaligned BAM, select reference realignment. A reference is needed for FASTQ alignment and modkit per-site output. Barcoded POD5 retains the existing demultiplexing path and per-barcode downstream submission; it is not an aggregate tandem-modkit workflow. Existing raw-retention policy is unchanged; these analysis changes do not delete raw signal.

## Compute location and device

Compute location selects the controller or a registered worker. Basecalling device selects CPU or CUDA **on that target**. Worker choices do not probe controller GPUs or inherit its CPU installation default. Explicit choices survive location changes and saved-job hydration. CPU submissions omit GPU pins; remote GPU assignment stays with the worker scheduler. Hardware observations are informational, not new refusal gates.

## Verification scope

Focused tests exercise typed settings/digests, mounted form-to-prepared-request transport, native Nextflow graph compilation, and actual tagged-BAM modkit pileup/summary through both standalone workflows. Tiny tagged BAM fixtures are controls, not biological validation. This change does not claim native GPU basecalling, rented-worker execution, or new native BLOW5/SLOW5 conversion qualification.
