# NGS P4 Dorado implementation audit — 2026-07-21

## Scope

This audit covers the P4 Dorado data plane and its API/UI integration:

- exact Dorado `1.3.1+7c84b01de` runtime and retained-model lock;
- POD5-only chemistry-aware preflight;
- DNA and RNA simplex basecalling;
- DNA duplex basecalling with a confined validated pair file and exact stereo model;
- inline barcode classification with `--kit-name SQK-RBK114-96`;
- `dorado demux --no-classify`;
- one digest-bound, confined BAM resubmission unit per barcode;
- bounded batch sizing and live GPU admission;
- API normalization, authorization, lineage, and frontend controls.

P3 and P4 evidence remain separate. The older direct Dorado CLI evidence is not used as repository-integration acceptance.

## Immutable runtime and model contract

Canonical lock: `config/ngs/dorado_v1.3.1.lock.json`.

- Dorado version: `1.3.1+7c84b01de`
- Source commit: `7c84b01de1e46d4c5b2d5208fc430f27579a6c22`
- Source tree: `19dbc819cb81afab03a37b324e87a012d02bbc77`
- Runtime SIF SHA-256: `2af01c5973eb86736949ea7d29342bb9f24611036906266c35e27c54d2032fad`
- Retained model identities checked: 9 (DNA fast/HAC/SUP, RNA fast/HAC/SUP, DNA stereo, HAC 5mC/5hmC, HAC 6mA)
- Runtime model download: forbidden
- Runtime network use: forbidden
- Mutable aliases and arbitrary model paths: forbidden

The preflight persists only help-output digests, not plaintext runtime capability output. The runtime SIF is rehashed immediately before execution, all selected model trees and POD5/pair/sample-sheet inputs are copied into the task sandbox and reverified, and Apptainer launches the Dorado processes with a separate network namespace and `network none`.

## Fail-closed policy

`scripts/dorado_p4_preflight.py` inventories every POD5 file and binds input file digests, read IDs, sample rate, chemistry metadata, runtime identity, exact model identity, and execution policy into `biomodstack.dorado_preflight.v1`.

It rejects:

- FAST5 and non-POD5 raw-signal inputs;
- empty or mixed invalid inventories;
- unsupported/missing chemistry, flow-cell, sequencing-kit, or sample-rate metadata;
- unsupported molecule/quality/mode selections;
- RNA duplex;
- missing, escaping, malformed, or inventory-inconsistent duplex pair files;
- unsupported barcode kits and escaping/malformed sample sheets;
- modified-base selections incompatible with the exact simplex model;
- unavailable required runtime capabilities;
- model or runtime digest mismatch;
- non-integer/out-of-range batch size or minimum Q score;
- invalid GPU device selections.

`DoradoBasecall` additionally performs live total/free VRAM admission for each visible GPU and validates unaligned BAMs using `samtools quickcheck -u -v`. The API scheduler reserves the same locked 15,360 MiB floor for `model_id=nanopore`.

## Repository-integrated runtime acceptance

The acceptance runs used the repository workflows, repository preflight, retained fixtures, exact retained models, exact SIF, explicit batches, and explicit devices. They did not bypass Nextflow.

- RNA004 simplex, SUP, 4,000 Hz, `min_qscore=0`: **1 BAM record**.
- DNA R10.4.1/E8.2 duplex, HAC, 5,000 Hz, validated retained pair file: **2 BAM records; 2/2 `dx:i:1`**.
- DNA barcode simplex, FAST, 5,000 Hz, `SQK-RBK114-96`, `min_qscore=10`: **4 basecall records; 4 demux records**.
- Demux units: **barcode01=2, barcode04=1, unclassified=1**.
- Classification provenance: `dorado_basecaller_inline`.
- Demultiplexing command: `dorado demux --no-classify`.
- Per-barcode outputs are consolidated to exactly one BAM under `demux/units/<unit_id>.bam` and each manifest entry carries `bam_path`, `bam_sha256`, and `read_count`.
- The API resolver successfully loaded all three real generated units while enforcing source-root confinement and SHA-256 equality.
- DNA modified-base acceptance, HAC 5mC/5hmC, `min_qscore=0`: **7 BAM records; 7/7 carry `MM` tags**.
- A confined, kit-bound sample sheet was snapshotted, digest-verified, and bound to the exact observed POD5 `(experiment_name, flow_cell_id, position_id)` tuple that Dorado 1.3.1 consumes. The parser now mirrors the pinned alias grammar, requires one experiment per sheet, rejects reserved/canonical-looking aliases and out-of-kit barcode IDs, and does not accept selectors assembled from unrelated runs. A final rewritten acceptance POD5 carrying `experiment_name=BMS_P4_SAMPLE` produced real `BC01`/`BC04` aliases in the basecall BAM and canonicalized outputs `barcode01=3`, `barcode04=2`, `unclassified=2`; aliases remain presentation-only while canonical `barcodeNN` unit identities are authoritative. POD5 without a nonempty Dorado experiment name fails closed rather than confusing the protocol-run UUID with Dorado's sample-sheet experiment selector.
- DNA `trim_adapters=false` acceptance completed with **3 BAM records**; the retained BAM `@PG` command contains `--no-trim`. RNA rejects this impossible choice because Dorado RNA always trims adapters.
- Authorized `barcode01` resubmission was exercised through `ont_plasmid_qc`: the unaligned unit was copied into a task-local immutable snapshot, that snapshot was authenticated before use and reverified after use, Dorado consumed only `source.snapshot.bam`, and the run completed with **2 input, 2 output, 0 unmapped** plus full plasmid-QC/construct-verification outputs.
- Barcode-unit listing/resubmission now additionally requires the source job's normalized `barcode_kit=SQK-RBK114-96`; a completed but unbarcoded DNA source cannot become a barcode-product authorization domain merely because a manifest appears in its result directory.
- A successful active `dorado_demux` stage now persists an immutable server-side terminal-product anchor over the aggregate demux manifest, per-barcode-unit manifest, preflight, and runtime provenance. Listing/resubmission requires the recorded stage product and compares the live aggregate manifest against that anchor; a coherently regenerated replacement BAM plus replacement manifests is rejected.
- DNA/RNA stage reporting and canonical artifact contracts now expose preflight/runtime provenance, and DNA additionally exposes the aggregate demux manifest and barcode-unit collection.
- Frontend consensus assembly now selects `wf_clone_validation` directly, requires a reference FASTA, and is unavailable for RNA rather than silently attaching unused assembly parameters to a basecall-only workflow.
- Barcode selection disables and clears whole-run consensus assembly, clone hydration derives quality from either normalized quality or an exact retained model identifier, and the Q-score control exposes the supported `0..30` range.
- A subsequent comprehensive review found one remaining generic-BAM snapshot race plus a reference-digest enforcement gap. `PrepareBamForAnalysis` now creates and authenticates a task-local regular-file BAM snapshot before any `samtools` operation. `DoradoAlign` now snapshots the reference before alignment, validates the API-provided normalized `reference_sequence_sha256`, consumes and publishes only that snapshot, and verifies its raw bytes stayed unchanged. Repository-integrated positive acceptance consumed two reads with matching before/after BAM and reference identities; an all-zero reference digest failed closed with the expected authorization error.
- The corresponding wrong-digest runtime run failed closed with `EXPECTED_DIGEST_REJECTION=PASS`.
- A later exact-source review found that Python RFC-4180 parsing still accepted sheets that pinned Dorado 1.3.1 interprets literally or rejects. Sample-sheet parsing now uses literal comma/cardinality semantics and rejects quotes, surrounding whitespace, duplicate headers, blank/ragged rows, and mixed or bare-CR line endings before applying the pinned header, tuple, alias, and kit constraints. Explicit adversarial regressions cover leading whitespace, quoted fields, duplicate headers, extra/trailing columns, reserved/canonical-looking aliases, tuple mismatch, and out-of-kit barcodes.
- That review also found that terminal-product anchoring previously checked too little cross-product identity. Anchor construction now validates the accepted lock, resolved model, basecalling mode, runtime SIF, calls BAM, demux source, total read count, aggregate unit catalog, every unit manifest, and every unit BAM identity before persisting a compact identity summary and immutable product digests. Coherent lock/model/runtime/calls/count/catalog/manifest/BAM contradiction probes all fail closed; the strengthened anchor accepted the retained real alias run.
- Workflow stage callbacks now require a random launch-scoped Bearer credential. Only its SHA-256 digest is persisted; plaintext is supplied to the launched workflow only through its process environment. The terminal demux transition uses an atomic digest-conditioned update and revokes the credential in the same commit, so replay fails. The reporter refuses to send without a credential, and a live Nextflow probe captured the expected Bearer header at a local test endpoint. The host workflow-adapter launch route is additionally loopback-only.
- Final adversarial review reproduced a mixed-EOL mismatch: an LF header plus CRLF data row was accepted by qualification but aborted pinned Dorado. Preflight now reads raw bytes, accepts uniform LF or uniform CRLF, and rejects either mixed ordering before field parsing; the exact retained-input CLI probe reports `MIXED_EOL_PREFLIGHT_REJECTED=PASS`.
- Stage reporting is now fail-closed end to end. The reporter exits nonzero for missing credentials, HTTP failures, and exceptions, and every NGS workflow propagates a nonzero reporter result as a workflow error. Direct reporter and live Nextflow unavailable-API probes both exited nonzero.
- All POD5 routes publish `dorado_preflight.json` and `dorado_runtime_provenance.json` with the basecall stage. Demux canonicalization additionally recognizes canonical or sample-sheet-alias BAM filename stems and maps aliases back to physical `barcodeNN` identities; retained real alias-directory output remains canonical.
- Per-unit aggregate and individual manifests now cross-bind both the authoritative source-calls digest and preflight digest. The API validates those identities against the terminal runtime/preflight products and rejects coherently rehashed false-provenance packets.
- Terminal `dorado_demux` reporting moved from the process-output subscription to `workflow.onComplete`, after `publishDir` completion. A direct Nextflow publication probe observed both terminal products before the completion callback (`true,true`), eliminating the valid-run HTTP 409 race while preserving fail-closed reporting.
- Runtime GPU negative acceptance passed for an invalid logical device, insufficient total VRAM, and insufficient free VRAM; each failed before Dorado execution. A final valid post-check DNA run on one visible GPU exited zero.

Public ONT submission paths exclude protected job-result roots, even where the configured data root is their ancestor. Only the capability-authorized barcode route opts the exact manifest-bound BAM into result-path use. ONT job names reject traversal/path separators before they can influence result directories.

## Verification matrix before integration

- Root P3/P4 focused tests: **37 passed**.
- Final focused P3/P4 API/security/alignment matrix: **209 passed**.
- Complete API suite in an isolated network namespace: **1,714 passed, 3 skipped, 13 unrelated baseline failures**. The failures are confined to Proteinbase/PPIFlow/ConforNets metadata, retired-workflow inventory, and BioXP ownership/route-guard assertions; no NGS, ONT, Dorado, barcode, scheduler, or alignment test failed.
- No-network focused API matrix: **178 passed**.
- Frontend full contract matrix: **393 passed**; TypeScript, the production build, and ESLint over every changed P4 frontend source/test file passed. Repository-wide ESLint remains non-green on five unrelated pre-existing unused-variable errors in `DynamicForm.tsx`, `QualitySettingsPanel.tsx`, `StructurePredictionTemplate.tsx`, and `reorchestrateStructureSettings.ts`.
- TypeScript project build: passed.
- Frontend production build: passed.
- Seven NGS workflow configurations: parsed successfully with Nextflow 25.10.0.
- JSON/YAML/Python compile and rename-disabled diff checks: passed.
- Changed-file secret scan: no non-test secret material found.
- Recursive retained-model verification: 9/9 exact identities passed.

Independent reviews found no critical issues but identified high-severity gaps in result-BAM authorization, forced realignment, job-name confinement, execution-time BAM identity, scheduler reservation, trim semantics, runtime isolation, sample-sheet alias/tuple semantics, mutable barcode-result authority, missing canonical P4 products, and clone/control compatibility. Each finding was remediated and covered by static, API, adversarial, negative-runtime, or repository-integrated runtime evidence. A fresh exact-tree review remains the final pre-commit gate.

The feature branch was 44 commits behind `origin/test` immediately before integration. The final post-merge matrix, commit/tree identities, and remote seal are recorded below after reconciliation.

## Final integration seal

Pending final reconciliation, post-merge verification, and remote identity seal.
