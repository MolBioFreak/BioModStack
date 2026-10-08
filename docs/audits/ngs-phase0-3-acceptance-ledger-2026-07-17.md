# ONT/NGS Phase 0–3 Acceptance Ledger — 2026-07-17

Authoritative requirements: `docs/plans/ngs-completion-audit-and-spec-2026-07-17.md` (SHA-256 `56611d51acc2f2063a5a6244b36cf7fb140cb1c23fb83829f2b0df79a44f0621`).

## Evidence labels

| Label | Meaning |
|---|---|
| Static | Source/config inspection only |
| Unit/contract | Focused function or cross-layer contract test |
| Parser | Realistic artifact parser/schema test |
| Preview | Nextflow graph/config construction only |
| Synthetic runtime | Executed workflow on controlled synthetic inputs |
| Artifact-semantic | File exists, parses, has correct schema/provenance, and contains scientifically appropriate evidence |
| Production-like | Executed with retained representative ONT data and production-equivalent tools/models |
| Truth-set | Executed against independently characterized constructs/populations |

A lower tier never substitutes for a required higher tier. Exit code 0 alone is not runtime or scientific validation.

## Global invariants

- [x] Expected/reference sequence is never emitted or accepted as observed consensus.
- [x] `workflow_status` and `verification_status` are separate.
- [ ] PASS is impossible unless every mandatory semantic artifact is present, parseable, provenance-linked, and quantitatively within policy.
- [x] Canonical API mode passes the real model registry boundary.
- [ ] Normalized variants or an equivalent auditable representation exist for all automatic verdicts.
- [ ] Circular-origin changes do not change biological verdicts.
- [x] Missing/ambiguous evidence resolves to REVIEW, never PASS.
- [ ] Every runtime claim is bound to exact commands, outputs, and a complete immutable executed source/tool/image identity. The repaired public-data packet recovers exact commands and host-tool versions, but the dirty-worktree source bytes used by the two public runs were not frozen as a complete content-addressed snapshot; those runs cannot support a reproducible software-correctness PASS.

## Phase 0 — Freeze scope and baseline

| Gate | Evidence | Result |
|---|---|---|
| Audited SHA approved and remote-backed | remote ref and local HEAD both `a69711f7e55786f3867e3952b546b3d6b8c48c11` | PASS |
| Clean implementation worktree | 0 porcelain lines before documentation copy | PASS |
| Dirty audit checkout source isolated | implementation source path is a separate git worktree; external launcher/image dependencies are disclosed and excluded from positive runtime evidence | PASS |
| Authoritative report retained byte-for-byte | SHA-256 matches audit copy; baseline carries an explicit model-inventory erratum | PASS |
| Container/model/runtime inventory | host tools, six NGS images, and three model directories in `docs/audits/ngs-phase0-baseline-2026-07-17.md` | PASS |
| Agreed focused baseline tests | API 76/76; frontend 15/15 | PASS |
| Acceptance ledger created | this file | PASS |
| Runtime-smoke diagnostic truthfully classified | launcher does not mount the clean worktree; stopped before workflow execution; no runtime claim | PASS |

**Phase 0 decision:** PASS for Phase 1 source implementation. Automatic scientific PASS remains disabled; runtime gates remain blocked until the clean-worktree launcher and immutable image path are isolated.

## Phase 1 — Truthful submission, BAM handling, and artifact contracts

Status: **implementation and automated gates passed; immutable review protocol active**

- [x] RED captured at the real registry boundary before implementation.
- [x] Canonical workflow IDs map explicitly to internal modes; no prefix stripping; retired `nanopore_methylation` monolith remains absent.
- [x] Non-mocked typed router → registry → validator → job creation test passes.
- [x] Instrument-handoff controls override user extras; validation failures cross the HTTP boundary as 422.
- [x] Requested workflow, canonical workflow, internal mode, and input mode persist.
- [x] Registry validation, module-local allowlists/bounded-integer validation, and shell quoting constrain `dorado_model`, modified-base selectors, `min_qscore`, `dorado_batch_size`, paths, and other shell-bound values even when Nextflow is invoked directly.
- [x] FASTQ reference semantics and generated/declared artifacts agree bidirectionally.
- [x] Reused BAMs receive unique staged/output basenames, quickcheck/index/mapped-read checks, source SHA-256 immutability checks, mapped-contig compatibility, and exact identity via BAM `@SQ M5` or paired server-controlled BAM/reference SHA-256 provenance. Generic HTTP submission rejects both attestation fields; the fallback is bound to the exact validated BAM bytes.
- [x] Runtime self-copy regression covers `aligned.bam`, `prepared.bam`, and arbitrary names.
- [x] Modkit is blocked unless the BAM has mapped reads and meaningful `MM`/`ML` tags coexist on one mapped record; valid, malformed, split-record, and tagged-but-unmapped synthetic controls execute through Nextflow.
- [x] The POD5 methylation branch routes Dorado output through reference alignment before modified-base validation; a real Dorado SIF probe preserved same-record MM/ML tags on the mapped record.
- [x] Real `modkit 0.6.1` pileup and summary run inside the retained Dorado/modkit SIF and produce canonical artifacts.
- [x] Manifest v2 separates expected-sequence, source-file-byte, and observed-consensus digests and provenance.
- [x] Missing observed consensus is `review_required`/incomplete and never falls back to expected reference or scientific PASS.
- [x] Frontend CLI preview uses the executable standalone FASTQ workflow/profile.

**Phase 1 decision:** READY FOR COMMIT only when two fresh independent PASS reviews match the final staged object. Because recording those verdicts in this file would change the object they reviewed, final immutable candidate identifiers and verdict/session evidence are carried in the scoped commit trailers; this ledger records the preceding exact-candidate cycle.

### Phase 1 evidence

| Gate | Command / evidence | Result |
|---|---|---:|
| Initial RED | project venv `pytest -q platform/api/tests/test_ont_ngs_phase1_acceptance.py` before implementation | 4 failed |
| Recovered intermediate acceptance | project venv `pytest -q platform/api/tests/test_ont_ngs_phase1_acceptance.py` | 12 passed in 18.61s |
| Post-review adversarial RED | retired-selector, public BAM-attestation, exact-BAM binding, direct Dorado numeric injection, POD5 alignment topology, and tagged-but-unmapped controls | reviewer FAIL reproduced; focused regressions failed before fixes |
| Review-cleanup RED -> GREEN | duplicate Nanopore registry mode IDs and undocumented `run_multimer_qc` workflow alias | **2 failed** before cleanup; **2 passed** after one authoritative definition per mode and alias removal |
| Final focused acceptance + synthetic runtime controls | project venv for `test_ont_ngs_phase1_acceptance.py`, `test_bam_reference_digest_runtime.py`, `test_ont_ngs_runtime_controls.py`, `test_ngs_fastq_runtime_smoke.py`, `test_nanopore_nextflow.py`, and `test_nextflow_entrypoint_registry.py` with real Nextflow/samtools/SIF paths | **51 passed in 60.21s; 0 skipped** |
| Complete Phase 1 Python/Nextflow matrix | `pytest -q platform/api/tests scripts/test_build_sequence_qc_manifest.py -k 'ont or nanopore or ngs or nextflow or sequence_qc or workflow_cache_hygiene'` with the project venv and real tool paths | **320 passed, 589 deselected, 0 skipped** in 67.00s; 9 unrelated pre-existing Boltz-CP invalid-escape deprecation warnings |
| Registry/product compatibility | registry file plus explicit uniqueness and retired-monolith product contracts | included in the 51-test focused gate; exactly seven unique canonical modes; retired compatibility entrypoint rejects `nanopore_methylation`; undocumented `run_multimer_qc` is absent |
| Full API regression diagnostic | project venv `pytest -q platform/api/tests` | **893 passed, 2 skipped, 7 failed** in 119.51s; failures are unchanged/out-of-scope BioXP network/state, install-profile, core-runtime service, and GPU-orchestrator tests |
| Frontend NGS contract | `npx --no-install tsx --test tests/nanoporeTemplateContract.test.ts` from `platform/frontend` | **12 passed, 0 failed** |
| Full frontend contract diagnostic | `npx --no-install tsx --test tests/*Contract.test.ts` | **60 passed, 1 failed**; unrelated unchanged BioXP top-bar regex contract |
| Nextflow profiles and Dorado direct boundary | `/usr/local/bin/nextflow config -profile <profile>,apptainer` for all seven canonical ONT/NGS profiles under `set -e`; focused valid-model preview and malicious direct-parameter probes | **7/7 profiles parsed**; valid preview PASS; direct `min_qscore` and `dorado_batch_size` injection rejected |
| Real Dorado alignment/tag-preservation probe | deterministic synthetic unaligned BAM with paired MM/ML tags; Dorado `aligner` from retained SIF; host samtools structural/mapped/tag checks | **mapped_records=1; mapped_same_record_mm_ml=1** |
| Frontend production-build diagnostic | `npm run build` from `platform/frontend` | TypeScript passed; Vite transformed 1036 modules then failed on unchanged unresolved `@teselagen/sequence-utils` import from `packages/bio-parsers/src/anyToJson.js` |
| Source hygiene | `git diff --check`, `git diff --cached --check HEAD`, and project-venv `compileall -q platform/api scripts` | PASS before final restaging |
| Pre-final immutable review cycle | HEAD `9e2935ab2746a46503cbbf6c74908a297a3e25b1`; 30 staged files; tree `c7ab52198881c802c6f00c9b3254d3f6dc52fcc6`; cached-diff SHA-256 `f4f5d686e79459b65b0b38ad7cb4a64ddecb06737e715c1a7f0d6df8f21b36fb`; stable patch ID `1ec1f31043fb55b63dab9ee140e4ad3a4e17d0b5` | DeepSeek V4 Pro session `20260718_043539_e4e7c9`: PASS, no blockers; DeepSeek V4 Flash session `20260718_043540_4a80d3`: PASS, no blockers. This ledger-only evidence update invalidates those verdicts for commit authorization. |
| Discarded final-review cycle | tree `f252d63094436ecea096788c4f45efa8265301d7`; cached-diff SHA-256 `e5a50345ed78423f2dae4dc21c1f05ba83a53bedb99ce444a7db7eea4fa60542`; stable patch ID `48b7172930ea14039c2d88b4cb5f57538e23fb6b` | DeepSeek V4 Pro session `20260718_045344_7b0d72`: PASS; DeepSeek V4 Flash session `20260718_045345_98a09e`: PASS with duplicate-mode MEDIUM and hidden-alias LOW. Parent rejected acceptance, added RED/GREEN contracts, consolidated the registry, removed the alias, and invalidated both verdicts. Final exact-candidate verdicts are recorded in commit trailers. |

Runtime identities: Nextflow `25.10.0` build `10289`; host `samtools 1.23.1`; host `minimap2 2.31-r1302`; Node `v20.20.2`; npm `10.8.2`; `/mnt/BioModStack/apptainer/dorado.sif` SHA-256 `2af01c5973eb86736949ea7d29342bb9f24611036906266c35e27c54d2032fad`; container `modkit 0.6.1`.

The synthetic runtime gates prove read-derived consensus provenance, refusal of expected-reference consensus substitution, exact generated-versus-declared artifact names, source-preserving BAM/BAI handling, mapped-contig and reference-identity rejection, same-record modified-base-tag validation, and real Modkit pileup/summary artifact production. The real SIF alignment probe additionally proves Dorado alignment preserves paired MM/ML tags on a mapped record. These gates do **not** prove a full POD5→Dorado basecalling run, retained production sample behavior, truth-set accuracy, or automatic scientific verification. Manifest interpretation remains `review_required`; Phase 1 cannot emit automatic scientific PASS.

Unrelated repository diagnostics remain outside this scoped commit: seven unchanged API tests fail on BioXP network/state, install-profile, core-runtime service, or GPU-orchestrator assumptions; the unchanged BioXP top-bar contract expects an obsolete gating expression; and the frontend workspace cannot resolve `@teselagen/sequence-utils` from `packages/bio-parsers/src/anyToJson.js` during Vite bundling.

## Phase 2 — Automatic construct verification

Status: **implementation and automated gates reported; public-data reproducibility is partial; exact-candidate independent review is not recorded**

- [x] Dedicated `scripts/verify_construct.py` exists with unit and CLI tests.
- [x] `modules/ngs/construct_verify.nf` is directly included by all four Phase 2 FASTQ/reference workflow entrypoints.
- [x] Machine-readable outputs include verdict, reason codes, metrics, normalized variants, structure/topology, contamination, artifact digests, and provenance.
- [x] Strict fail-closed policy is encoded in versioned experimental profiles and Draft 2020-12 schema `biomodstack.construct_verification.v2`.
- [x] Deliberate SNV, indel, deletion, insertion, low coverage, low identity, mixed 50:50, contamination, dimer/multimer, origin-spanning indel, reverse-complement rotation, repeat/homopolymer normalization, and malformed-evidence controls pass their expected verdicts.
- [x] Exact controls can satisfy the scientific checks only with independent observed sequence plus corroborating read support; the production `plasmid_strict_v1` profile remains uncalibrated and therefore cannot emit automatic `PASS`.
- [x] Copied-reference, absent, malformed, mismatched, corrupted, fallback, and untrusted evidence can never PASS.
- [x] API and frontend expose verdict, reasons, quantitative metrics, provenance, and expected-vs-observed distinction.
- [x] Alignment viewing is job-scoped and manifest-bound, with opaque per-job creator capabilities, cross-job denial, primary/dimer kind-and-mode isolation, canonical-path and regular-file checks, exact BAM/index/reference binding, bounded reads, HTTP ranges, ETags, and cache reuse for large artifacts.
- [x] Public-data reporting separates software correctness, technical concordance, and biological accuracy without promoting experimental thresholds.

### Phase 2 evidence

| Gate | Command / evidence | Result |
|---|---|---:|
| Pre-hardening focused Phase 1 + Phase 2 regression (superseded by the remediation cycle below) | project venv `pytest -q` over the eight Phase 1 files plus construct-verification, topology, manifest, security, and alignment-session suites; real Nextflow/samtools paths; isolated `NXF_HOME` | **119 passed in 71.87s; 0 skipped** on the discarded pre-review candidate |
| Hardened verifier synthetic runtime | real Nextflow `25.10.0` `ConstructVerify` replay under `/tmp/biomodstack-phase2-runtime/attempt6` with retained-source FASTQ, BAM/BAI, alignment-stat, support, and topology inputs | exit `0`; production profile returned `REVIEW`; retained source-read/BAM and alignment-stat recomputation passed, while the old synthetic support table and ordinary full-length linear topology fixture were correctly rejected as insufficient rather than converted to `PASS` |
| Four standalone workflow previews | explicit FASTQ/reference fixtures for `ont_fastq_qc.nf`, `ont_plasmid_qc.nf`, `ont_construct_screening.nf`, and `wf_clone_validation.nf` under `/tmp/biomodstack-phase2-previews-final` | **4/4 exit 0** |
| Real `ont_fastq_qc` runtime | `/tmp/biomodstack-phase2-workflow-fastq-attempt2` | exit `0`; verification artifacts emitted; tiny 48-bp read fixture correctly did not receive a scientific PASS |
| Repeated plasmid-QC runtime branches | final real runs under `/tmp/biomodstack-phase2-runtime-{ont-plasmid,construct,clone}-final` | **3/3 exit 0** and emitted verification artifacts; deliberately too-short 48-bp FASTQ control returned `FAIL` in all three with zero coverage/unmapped fraction 1.0, proving no workflow path converted runtime completion into scientific PASS |
| Public SupHAC acquisition and execution | Zenodo archival record `6554346` (Dryad DOI `10.5061/dryad.zgmsbccd0`), distinguished from Figshare article `16627654`; exact direct URLs, retrieval-process UTCs, registry file IDs, MD5/SHA-256 values, workbook rows, subset scripts/manifests, run commands, host-tool versions, and resource limits are recorded in `docs/evidence/ngs-phase2-public-data-report-2026-07-18.{md,json}` | original two host-local runs exited `0`; unfiltered BC115=`REVIEW`; reference-conditioned BC114=`FAIL`. Final replay `/tmp/biomodstack-phase2-public/BC114-final9-run` also exited `0`, validated support-table/BAM recomputation and observed-consensus binding, and preserved `FAIL` for the exact-allele-supported `A→AT` (**246/264; 93.1818%**) while retaining insufficient topology explicitly. The superseded 94.3182% figure counted all insertion alleles/lengths at the anchor. No false scientific PASS; reproducibility remains partial. |
| Public claim separation | same evidence report | software correctness **PARTIAL / NOT QUALIFIED AS A REPRODUCIBLE PASS**; BC114 agreement **99.8478% but reference-conditioned**; biological accuracy **NOT QUALIFIED**; thresholds remain experimental with `public_accuracy_validated=false` |
| Alignment-session service/router | `tests/test_ngs_alignment_sessions.py`; `tests/test_ont_ngs_capability_lifecycle.py`; real 20-read BAM page/detail probe; production-shaped primary/dimer manifests; final BC114 workflow output probe | **19 alignment tests plus capability-lifecycle integration passed**; persisted `results/<job-name>_<timestamp>` output roots resolve through the authorized job record while remaining confined to the server results root; source-job capability is required before ONT resubmit/resume, ONT resume rejects all caller or awaiting-payload parameter overrides so runtime/workflow/output/source identity cannot be replaced, and each successor receives a fresh job-scoped capability; direct generic ONT creation is rejected; typed creation persists the digest in the initial job commit and capability issuance failure occurs before creation; exact BAM/reference binding, bounded reads, dimer conflict rejection, and cross-job denial are verified |
| Browser acceptance | real browser against job-scoped FastAPI harness using real workflow BAM/BAI/reference; production capability dependency regression | **PASS**: range `206`/32 bytes, invalid range `416`, bounded read limit `5`; creator capability accepted and a different job capability denied `403` |
| Frontend Phase 2 contracts | `tsx --test` for alignment-session, alignment-viewer, and sequence-QC contracts | **25 passed**; stale-request guards, explicit list/detail truncation visibility, non-fabricated FASTQ export, timeout-safe generation-bound IGV cleanup, delayed-track suppression, session-only tracks/report/config, current-locus filtering, provenance/support rendering, and bound locus navigation covered |
| Frontend typecheck, changed-file lint, and production bundle | `pnpm exec tsc -b`; focused ESLint; `pnpm run build` | all **PASS** after harmonization onto `test`; Vite transformed 4,623 modules and built production assets in 29.00s |
| Full frontend diagnostic | `pnpm test` (`tsx --test tests/*.test.ts`) | **316 passed, 1 unrelated BioXP top-bar source-regex failure**; failing file and source are unchanged by Phase 2 |
| Full API diagnostic | project venv `pytest -q` | **922 passed, 2 skipped, 30 unrelated failures** across install profile, BioXP live/state assumptions, core-runtime/workflow-adapter, system runtime, missing unrelated Nextflow files, and legacy source-message expectations; Phase 2 scoped matrix is green |
| Source hygiene | `git -c diff.renames=false diff --check`; project-venv `compileall`; JSON/schema/profile/report parsing; final focused API and frontend contracts | **PASS** on the harmonized `test` candidate; combined ONT/Phase 1/Phase 2/runtime matrix **271 passed** after exact circular alignment, origin-safe exact-allele/length BAM insertion support across circular/RC representations, verifier-compatible canonical profile digest binding, centralized canonical-job runtime-root rejection, source-authorized and capability-safe resubmit/resume, atomic initial capability-digest persistence, persisted-output session discovery, and authoritative dimer-mode conflict rejection; the selected frontend harmonization matrix **78 passed** (including the 25 Phase 2 contracts); TypeScript, focused ESLint, and production build passed |

The public runs are intentionally not relabeled as biological or broad software-validation success. BC115 retained mixed/ambiguous evidence and returned `REVIEW`; its alignment statistics also mixed read and alignment-record counts, making contamination evidence unavailable. BC114 reached 99.8478% Sanger identity but retained one strongly supported insertion and returned `FAIL`; because the Sanger record selected the 283 reads, this is descriptive reference-conditioned agreement only. A 2026-07-19 exact-current verifier replay and independent primary-BAM audit confirmed the exact inserted `T` in 246/264 reads, split across 141 forward and 105 reverse reads with median inserted-base Q30. That closes an allele-counting or strand-only software artifact as the explanation, but no independent chromatogram/truth artifact exists to adjudicate ONT error versus reference error versus sample biology. The full HAC/SupHAC confusion matrix, immutable executed-source snapshot, independent biological insertion adjudication, per-process resource trace, and long-plasmid stress benchmark remain unavailable or scheduled work.

FAST5 is explicitly unsupported by the current production runtime rather than pending for lack of a fixture. A checksummed EPI2ME `wf-basecalling` 1.5.7 FAST5 fixture was found and converted successfully with official `pod5` 0.3.35 (1,116 reads), but the production Dorado 1.3.1 lane correctly rejected its 4,000 Hz signal against available 5,000 Hz models. Canonical launch and handoff contracts are therefore POD5-only and fail closed; a separately pinned Dorado 0.9.6 + v4.1.0 legacy lane would be required before any 4 kHz FAST5 rebasecall acceptance claim.

The post-`3d67db4` exact remote lineage was rerun on clean commit `8ac5d61967f0d6c5f5252230b94570623aa2aabf`: the 22-module NGS/API matrix passed **303/303**, the frontend suite passed **389/389**, TypeScript passed, and the production build passed. Full provenance and discrepancy dispositions are recorded in `docs/audits/ngs-discrepancy-closure-2026-07-19.md`.

An immutable review of discarded tree `255f6b0fdb017685336fab5bd027bc0b10857f25` returned scientific and frontend `FAIL` findings: uncalibrated automatic PASS, insufficient evidence binding/non-finite handling/topology semantics, incomplete public reproducibility, absent production dimer manifests, non-exact BAM/reference identity, stale UI request ownership, fabricated FASTQ quality, and legacy unbound IGV tracks. Those findings triggered the current remediation cycle. That tree is not eligible for commit; fresh review must bind to the corrected final tree.

**Phase 2 decision:** PENDING EXACT-CANDIDATE REVIEW; NOT FINAL-ACCEPTED. Automated and runtime evidence above may be submitted for review, but the public-data runs demonstrate only recorded fail-closed execution, not a reproducible software-correctness PASS or biological accuracy. Commit remains prohibited until a frozen candidate receives independent security/runtime, scientific, and frontend approvals; no such final verdict is claimed in this ledger.

## Phase 3 — Pinned `wf-clone-validation` production integration

- [x] Upstream release is pinned to v1.8.4 commit `b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42` and tree `9cc0a24beee74eccdb07765b755fa64e04bd8141`; patched commit/tree are independently bound.
- [x] Five exact container files and their live SHA-256 values, Nextflow 25.10.0 build 10289, and exact HAC v5.0.0 model identity/store are immutable and recorded.
- [x] Runtime has no `nextflow pull`, source copy/mutation, patch/sed/rewrite, hidden model download, fast-to-HAC substitution, or assembly-tool fallback.
- [x] Adapter schema v1 validates and hashes required/optional outputs and rejects missing, ambiguous, escaped/symlinked, malformed, or contradictory evidence.
- [x] Upstream final FASTA plus the authoritative original analysis BAM/reference feed the existing Phase-2 verifier; upstream BAM/status/exit zero cannot create PASS.
- [x] Focused P3 fixtures cover exact runtime, mismatches, adapter happy path, missing/duplicate/malformed/contradictory output, and execution-versus-verdict separation; existing Phase-2 truth fixtures remain the scientific semantic suite.
- [x] Existing production-like offline run completed all tasks for `sample02` (3,037 bp) from the exact locked assets, and its output passes the new adapter.
- [x] API/artifact and frontend surfaces expose adapter/runtime provenance plus the separate canonical construct-verification artifact; mutable source/revision/path/profile controls are removed and rejected.

**Phase 3 decision:** IMPLEMENTED LOCAL CANDIDATE; independent review and a fresh full run of the rewritten outer wrapper remain pending. No biological PASS or release-final acceptance is claimed. Exact evidence: `docs/audits/ngs-p3-wf-clone-validation-implementation-2026-07-19.md`.

## Commit and review ledger

| Scope | RED evidence | GREEN evidence | Independent review | Commit | Remote |
|---|---|---|---|---|---|
| Phase 0 documentation | baseline contracts passed; launcher/image isolation limitations and model-inventory erratum recorded | 76 API + 15 frontend; six image digests and three model-directory digests reproduced | APPROVE — fresh independent review, no blockers | this scoped commit | base remote-backed |
| Phase 1 | adversarial RED covered retired selector, public/unbound BAM provenance, direct numeric injection, unaligned POD5 Modkit, tagged-but-unmapped BAM, duplicate registry modes, and hidden alias | 51 focused; 320 complete matrix; 12/12 frontend; 7/7 profiles; real Dorado tag preservation; real Modkit 0.6.1 | pre-final/discarded cycles recorded above; final exact-candidate PASS sessions in commit trailers | this scoped commit | not pushed |
| Phase 2 | adversarial RED covered malformed/untrusted evidence, wrong reference, corruption, support/topology/contamination contradictions, non-finite values, inconsistent BAM/stat/support evidence, false linear circularity, sidecar mixing, equal-length wrong references, generic-submit runtime-root injection, stale requests, path escape, symlinks, cross-job artifact IDs, ranges, and repeat/homopolymer orientation normalization | 192 latest combined API; 16 focused frontend; lint/typecheck/production build; hardened synthetic and public BC114 replays; primary production session `ready=true`; browser range/read/cross-job gate | discarded tree review failed and was remediated; **fresh corrected-tree review pending** | pending approvals | not pushed |
| Phase 3 | `/tmp/codex-p3-red.txt`: discriminating lock/validator/adapter/runtime/API contracts failed before implementation | focused P3 contracts, related API/NGS matrix, frontend contracts/typecheck, Python/JSON/YAML/Nextflow/static hygiene, live lock validation, official-output adapter replay, and provisioning identity reproduction recorded in `/tmp/codex-p3-summary.txt` | pending | not committed | not pushed |
