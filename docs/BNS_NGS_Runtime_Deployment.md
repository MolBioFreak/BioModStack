# BNS NGS / MolBio runtime deployment

The NGS/ONT/MolBio edition is published on `bns-plasmid-development-toolkit-test`.
The full BMS reference for this runtime audit is upstream `test` commit
`8348ec0b01c36ab212a2f75b9209951e627104b5`. The native NGS workflows, signal
workbench, model locks, scientific controls and shared persistence remain the
BMS implementations. Their scientific readiness and hardware admission gates
remain authoritative. Rechecking upstream `test` at
`366cdc0dd8f703dce0cb2508938f61bcc67a6dbe` on 2026-10-26 found no newer
changes in the audited NGS runtime paths.

## Runtime inventory

| Component | Pinned version / identity | Existing application use |
| --- | --- | --- |
| Dorado | 2.1.2+8b8fc5d | DNA/RNA basecalling, move tables, modified bases |
| Dorado models | Nine bundles, aggregate hashes in the Dorado lock | DNA/RNA fast, HAC, SUP; stereo; 5mC/5hmC and 6mA |
| samtools | 1.24 in Dorado runtime | Mapping products, Bayesian consensus and QC |
| modkit | 0.6.4 | Modified-base summaries / methylation analysis |
| igv-reports | 1.16.3, bundled IGV.js 3.5.2 | Standalone alignment / variant reports |
| wf-clone-validation | v1.8.4, BMS compatibility commit a575ddbc5513759a962cf41f8f64d7b246662bdc | Clone assembly and validation |
| Flye, Canu, wf-common | Exact images in the clone-validation lock | Assembly and native EPI2ME workflow stages |
| Medaka | 2.2.2; all five model archives checked by SHA-256 | Consensus polishing |
| pLannotate | 2.0.0 in the API annotation environment; locked workflow image | Sequence annotation / clone reports |
| blue-crab | 0.5.0 | POD5 ↔ SLOW5/BLOW5 conversion |
| SLOW5tools / pyslow5 | 1.4.0 / 1.4.0 | Validation, indexing, routing, waveform lookup |
| Squigualiser | 0.7.0, commit 5a2404f1f43bc3227a85475c59b2b77970078b2e | Read/reference/pileup views, reform, realign, calibration |
| Squigulator | 0.5.0, commit c5f0c619a28b9532388877096acb7568c34b9c4b | Bounded ideal trace production and comparison |
| ViennaRNA, Biopython, pydna | Locked API environment | RNA structure, molecular operations, PCR, primers, digests and alignment |
| MinKNOW | Host installation and existing host bridge | Mk1D management and instrument acquisition |

Project data records accept FASTQ, POD5, SLOW5, BLOW5, BAM/CRAM, FASTA and
analysis records. **NGS → Raw signal tools** exposes source import and the
existing raw-signal ledger. BLOW5 imports require their matching adjacent index.
Registration preserves unknown fidelity until native validation succeeds. It
never asserts scientific acceptance. SLOW5 can be retained as native source;
waveform and Squigualiser workbench consumers use validated indexed BLOW5.
The alignment result's **Read & Signal Workbench** retains the existing complete
mapping, calibration, rendering and ideal-comparison controls.

DNA basecalling has one task card: strand mode, barcoding and per-read
modification calling are configured once below it. Methylation analysis is a
separate task for reference-based modification tables, including existing tagged
BAM input. Molecule type follows the selected task; optional basecalling
references open on request. Model details and expert settings remain available
without duplicating task cards. Older duplex, barcode and BAM-QC selections
reopen under their parent task with saved parameters retained. Modification tags
alone do not change a saved DNA basecalling job into an analysis workflow.

## Installation identities

The four OCI policies under `platform/api/config/ont_signal_workbench` name
measured local immutable image IDs. They are distinct from the previously built
upstream images. The two NGS locks record measured SIF bytes for this edition's
builds. Tool source revisions, model hashes and scientific settings were retained.
`config/ngs/bns_runtime_builds_v1.json` records the upstream and edition build
identities. A rebuilt SIF must never be labelled as the old SIF: build timestamps
and packaging metadata can change its bytes.

Build signal images using the four `scripts/build_ont_*_runtime.sh` scripts.
They report measured candidates; they do not automatically select a new image.
Squigualiser definitions use the signed Debian snapshot of 2026-08-10 to retain
the released package pins after packages leave the live repository indexes.
Qualify the candidate before updating its policy. Scientific jobs remain
network-disabled and use `--pull=never`.

Use the repository's Dorado and Medaka Apptainer definitions and the exact
clone-validation image URIs. `scripts/provision_wf_clone_runtime.py
--image-source-dir <released-image-directory>` installs matching released SIF
bytes without overwriting a conflicting artifact. Build outputs, model weights,
databases, recorded input data and runtime logs stay outside Git.

Alignment sessions, read pages and FASTQ-QC result views run samtools from the
same Dorado SIF named by `config/ngs/dorado_v2.1.2.lock.json`. Publish the
edition SIF into the shared image store and select it for the API lane:

```sh
python scripts/publish_runtime_images.py --store-root "$BMS_RUNTIME_IMAGE_STORE" \
  --lane development --manifest <manifest.json>
# manifest: {"BMS_NGS_RUNTIME_SIF": {"source": "<edition dorado.sif>",
#            "sha256": "<dorado.sif_sha256 from the lock>"}}
```

An upstream BMS Dorado object in the store does not satisfy this lock. Without
the edition object every alignment session reports `pinned NGS samtools runtime
is unavailable or does not match the Dorado lock`; the rest of the result stays
readable.

## Sequencing runs on the Mk1D

BMS manages runs; MinKNOW carries them out. Install the bridge with
`config/bns-native-installation/install-minknow-bridge.py`, then in **NGS → ONT
instrument control**:

1. Pick the Mk1D position and the sequencing kit used for the library. BMS asks
   MinKNOW for the matching sequencing protocol and its default (HAC) basecalling
   model for the inserted flow cell.
2. Set the run length and press **Start sequencing**. BMS records the run before
   it calls MinKNOW, then saves MinKNOW's run id.
3. BMS polls MinKNOW every 10 seconds. When the run completes, or is stopped
   with **Stop run**, BMS seals the FASTQ/POD5 files from MinKNOW's run folder,
   registers the POD5 for raw-signal work and enables plasmid-QC handoff.

If BMS loses contact with MinKNOW during a start, the run is marked `unknown`
and is never started a second time. Polling picks it up again once MinKNOW
responds. Hardware checks and power cycling are done from MinKNOW itself.

Configure `BMS_ONT_CONTAINER_RUNTIME=podman` and `BMS_CONTAINER_RUNTIME=podman`
in the supported host environment for rootless Podman. Managed units preserve
both selectors. Rootless worker commands keep the host UID so their output
mounts and descriptor broker sockets remain accessible. Image admission still
requires the exact approved hash. Docker remains supported.

## Acceptance boundaries

The ONT-platform receiving checks exercised the actual pinned tools: conversion
and indexed lookup with exact ADC equality, the production descriptor broker and
partitioning path, native Squigualiser plotting, bounded Squigulator production,
and the offline two-track comparison renderer. All nine Dorado model aggregates
and all five Medaka archive hashes matched their locks. CPU native basecalling
checks covered DNA, RNA and modified bases. These checks qualify those receiving
paths; they do not replace every workflow's governed live acceptance.

Managed-service acceptance also exercised native upload and external source
registration, POD5 conversion by the running worker, external indexed BLOW5
validation, and waveform retrieval of all 2,552 samples in the recorded-read
fixture. External source provenance remains distinct from MinKNOW acquisition
receipts. The descriptor broker supports long deployment paths without changing
the persistent staging root or relaxing file confinement.

A saved MolBio revision issued the immutable reference receipt for a synthetic
12-read FASTQ job through the native prepare/preview/submit API. The managed CPU
workflow completed alignment, dimer analysis, Bayesian consensus, IGV reporting
and construct verification. Its observed consensus matched all 2,746 expected
bases. The scientific verdict remained **REVIEW**, correctly retaining depth,
strand-balance and topology limitations of this synthetic fixture. Workflow
completion is not biological acceptance. Mounted UI tests, TypeScript checking
and production compilation passed; these checks are not browser click-through
or physical instrument acceptance.

BMS's governed Dorado execution currently requires its CUDA device and VRAM
policy. A native CPU Dorado smoke check does not prove that BMS submits CPU
basecalling jobs. A host without a compatible local NVIDIA device can acquire
signal through MinKNOW and perform supported asynchronous analysis of existing
reads. GPU, recorded-data and instrument-dependent acceptance must remain
explicit wherever it has not run. Instrument acquisition is never started by
the installation checks.
