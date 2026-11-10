# wf-clone candidate (review required)

Base: `51d72788bbe90f3cbbefd8be13ed75dc3a52cadd`. This task worktree does not change canonical source, live runtime selectors, services, SQL Jobs, or the shared build ledger.

## Selection and source

Keep current branch scientific settings and deterministic wf-clone source `a575ddbc5513759a962cf41f8f64d7b246662bdc` (upstream v1.8.4 `b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42`), unchanged compatibility patch and all five Medaka archive identities. No source/framework-contract upgrade is proposed.

The original five branch edition SIFs were unavailable in the audited local stores and configured HF content-addressed image endpoints (all five authenticated HEADs returned 404). The existing provision owner supplies no alternate exact-byte release source; the origin GitHub release inventory has only an Android APK release, not these SIFs. Original installed clone caches contain the upstream hashes, not branch edition hashes.

The user-approved alternative is the source ONT host's independently compiled, same-source/tool edition, previously qualified on 35 real-read native tasks. Source SIFs were freshly hashed at `<source-home>/.local/state/bns-provision/clone-build/`. This candidate changes only the five measured SIF hashes and task-owned receiving cache location; it never relabels upstream bytes as edition bytes. The existing provisioner/validator remain the admission owners.

## Required dual framework

Retain managed embedded Nextflow 25.10.1 build 10547 for the outer app. Install the original thin launcher at `/usr/local/bin/nextflow` for the existing clone contract 25.10.0 build 10289. Its SHA-256 is `2a398d1dbf3a7258218ae8991429369ac4fdd86cb99b8c6c8f6c922202d9d524`. Restore the exact 25.10.0 JAR at the compiler's actual default adapter home `~/.biomodstack-dev/nextflow/framework/25.10.0/nextflow-25.10.0-one.jar`, SHA-256 `2189a0147496fa2ce32230cfbe4972c2017c0e1795d4bc9fe8ffbceb2a0ee3f2`.

Native offline probes confirmed both identities. The embedded launcher still returns 25.10.1 with NXF_VER=25.10.0, so it cannot replace the clone thin launcher. Do not globally set BMS_NEXTFLOW_EXECUTABLE to that embedded binary. No unsupported single-engine source upgrade is proposed, and no binding source defect was found: the current compiler already projects the managed clone home correctly.

## Native annotation is a separate contract

API pLannotate 2.0.0 was absent. A fresh environment was installed from current `docker/plannotate-conda-linux-64.lock` (SHA-256 `4a71bb86c72c7fb53da4974ce3fbb9034786c30676c37b847158bffbe3a56bae`): 214/214 package URLs and SHA-256 records match, with repository-pinned micromamba SHA-256 `c04571cfb0750e5432d530a3068b8fcd232ebed3133358e056e59a90b9852b00`. Only the existing database data payload was reused; all 20 manifest files verify, and its manifest matches the source ONT host's payload (`1167ae6d730591d1b311e78f86f25ed6f913b0fb5b05d9e52123ffd6569d7eb4`).

The existing BNS native wrapper is installed at the task prefix's `annotation/bin/plannotate`; environment prefix is `annotation/micromamba/envs/plannotate`. Real full-database annotation of the explicit 10,488-base fixture preserved sequence and produced 23 GenBank features, 23 CSV records and a nonempty HTML map. Actual versions: pLannotate 2.0.0, BLAST 2.17.0, DIAMOND 2.2.4, Python 3.12.13. This does not replace the workflow pLannotate image, whose version is independently qualified.

## Qualification and activation boundary

Target receiving and native execution qualification are complete: all five receiving SIF hashes equal the freshly measured source-host edition, both Flye/Canu selected-closure validators pass, all five Medaka archive hashes and native model-resolution controls pass, and the existing provisioning owner admits the exact cache. The actual generated task export prelude validates with Nextflow 25.10.0 build 10289; substituting the embedded outer launcher fails with the expected NEXTFLOW_VERSION_MISMATCH (25.10.1 build 10547). Actual nested and outer logs independently confirm those respective engine identities.

The retained-real-read PhiX run completed 36/36 native tasks with zero failed, ignored, cached, or retried tasks. It produced nonempty final FASTA/FASTQ, annotated GenBank/BED, BAM/index, BCF/index, alignment/assembly statistics and a 2,517,762-byte report. Final native file hashes and sample_status were read back. Receipts, binaries, generated exports and fixtures stay outside Git at `~/.local/share/biomodstack/ont-clone-candidate-20261108/`; see `qualification-summary.json`, `native-final-readback.json` and `receiving-progress.json`.

The bounded run retained compiler scientific defaults (HAC6, min_quality 9, assembly coverage 60, Flye nano-hq, current thresholds). Its explicit fixture target is NCBI NC_001422.1, 5,386 bases, with approx_size=5386; 149 genuine retained reads come unchanged from locked upstream `test_data/test/barcode02/test2.fastq` (SHA-256 `1710063c60cfef8b0c09f6f2c5471af0c510b11e38ea839138096e4ffd2e92f5`). The authoritative FASTA was retrieved from NCBI efetch; its bytes hash to `caa155c83967cc080f4f188f14f1f28c18d77448e87c3d00d3579ed348f4de2a`. CPUQuota=400% and a four-CPU/eight-GB task allocation bounded the standalone run. Unknown read-group provenance remains unknown for all 149 reads; HAC6 is an explicit native override, not invented instrument/model provenance.

**Scientific scope limitation:** native assembly completed successfully but emitted one 16,237-base sequence, not a reference-sized 5,386-base construct. The native reference comparison reports six SNP and eight indel records. This is native runtime/workflow execution qualification, not biological construct correctness or basecaller-model suitability acceptance. No scientific setting, threshold or source was changed to hide this result. The earlier 10,488-base low-read fixture exited zero through its insufficient-read branch without assembly (15 completed scheduling tasks); that outcome is retained separately in `initial-default-fixture-qualification.json` and `clone-native/`. The covered PhiX fixture was selected instead of lowering coverage or duplicating reads.

Actual workflow image tools are Flye 2.9.4-b1799, minimap2 2.28-r1209, samtools 1.19.2, Canu master +27 changes (r10515, `2a41e933b8de6ed66723d75d36dd071b46d2c347`), Medaka 2.2.2, and workflow pLannotate 1.2.2. These are the current workflow-specific image contracts; bundled samtools 1.19.2 does not replace Dorado's separately selected samtools 1.25, and workflow pLannotate 1.2.2 does not replace the separately installed API pLannotate 2.0.0.

The image file hash differs from the upstream reference (upstream 28d79c... vs branch f1a8b4...); validation records the hash found and no longer refuses on it. Activation requires parent review of this measured lock and the separately staged `candidate-selectors.env`; no live selector has been changed. `suggested-build-ledger-delta.json` is a proposal for the parent-owned shared ledger, not an edit to it. Keep recovery artifacts until parent decides consolidation.

A supplemental pytest attempt could not collect because the managed API environment has no pytest; no packages were installed into that live environment. Native owner and tool qualification are the acceptance evidence, not a fabricated unit-test pass.
