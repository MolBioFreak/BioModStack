# BioModStack NGS Phase 2 implementation plan

**Date:** 2026-07-18
**Branch:** `recovery/ngs-phase2-20260718`
**Base:** Phase 1 commit `3c8e72002a2cef8b1963c3f1ce1431e16fdf1dcb`

## Objective

Deliver a fail-closed, circular-aware construct-verification layer on top of the Phase 1 ONT/NGS runtime without weakening Phase 1 input, provenance, BAM/reference, shell-safety, or scientific-status controls. Phase 2 must expose machine-readable and human-readable evidence through the API/frontend and must never turn missing or fallback consensus into a passing expected-reference copy.

## Non-negotiable evidence contract

- Expected reference and independently observed consensus are distinct typed inputs.
- A missing, fallback-derived, malformed, or digest-mismatched observed sequence yields `REVIEW`, never `PASS`.
- Verdict is exactly `PASS`, `FAIL`, or `REVIEW`, with stable reason codes and threshold provenance.
- Circular origin and reverse-complement representation do not change the biological verdict.
- Variant, per-base support, topology, contamination, and provenance checks are represented separately.
- The verifier emits no observed FASTA when no observed consensus exists.
- Required artifacts are schema-validated and path-safe; absent optional artifacts carry explicit state/reason.
- Primary raw/alignment viewing uses the sample alignment BAM/reference, never the dimer-candidate subset by default.

## Canonical Phase 2 files

- `config/ngs/construct_verify_profiles.json` — versioned threshold profiles.
- `schemas/ngs/construct_verification_manifest.schema.json` — machine schema.
- `scripts/verify_construct.py` — deterministic verifier and artifact writer.
- `modules/ngs/construct_verify.nf` — Nextflow boundary.
- `platform/api/tests/test_construct_verification_phase2.py` — adversarial truth fixtures.
- `platform/frontend/tests/ngsAlignmentViewerContract.test.ts` — primary-vs-dimer viewer contract.

The ledger names above are canonical. The older plan aliases `verify_plasmid_construct.py` and `plasmid_verify.nf` are retired in favor of one implementation, not duplicated wrappers.

## RED/GREEN slices

1. **Contract bootstrap**
   - RED: required script/schema/profile/module absent.
   - GREEN: add parseable placeholders with no claimed scientific functionality.

2. **Observed-evidence guard and manifest policy**
   - RED: exact independent consensus can pass only with all evidence; missing/fallback/digest mismatch must review.
   - GREEN: strict FASTA/state/profile parsing, SHA-256 binding, reason-code precedence, deterministic manifest writer.

3. **Circular normalization and variant truth**
   - RED fixtures: exact rotation, reverse-complement rotation, supported SNV, supported insertion/deletion, origin-spanning event, wrong reference.
   - GREEN: circular-aware orientation/rotation normalization and deterministic VCF/variant ledger.

4. **Support, mixture, coverage, contamination, topology**
   - RED fixtures: low depth, strand imbalance, mixed alleles, unmapped/off-target excess, unresolved topology, contradictory multimer evidence.
   - GREEN: evidence-specific checks with explicit `pass/fail/review/not_evaluated`; no aggregate-pass shortcut.

5. **Artifacts and schema**
   - RED: schema rejection for malformed verdict/check/provenance/artifact state and path escape.
   - GREEN: v2 manifest, summary TSV, normalized VCF, per-base metrics, evidence HTML, observed FASTA only when real.

6. **Nextflow integration**
   - Always emit a typed verification-input directory from FASTQ QC so missing consensus still invokes verification.
   - Add `ConstructVerify` after alignment/QC in the standalone FASTQ/plasmid workflows.
   - Publish `verification/` as a first-class stage and keep Phase 3 `wf-clone-validation` out of this phase.

7. **API and frontend**
   - Prefer `verification/qc_manifest.json` over legacy `fastq_qc/qc_manifest.json` for new jobs while retaining old-run compatibility.
   - Render verdict, reason codes, threshold profile, variants, support, topology, contamination, provenance, and explicit uncertainty.
   - Correct IGV artifact selection so the default alignment viewer uses `align/aligned.bam` plus its matching reference/index; dimer evidence remains a separately labeled optional view.

8. **Public-data validation**
   - Pin accessions, downloads, checksums, truth references/publications, tool/container versions, commands, thresholds, and resource usage.
   - Report software correctness, technical concordance, and biological accuracy separately.
   - Do not use the expected reference as a substitute for missing observed consensus.

9. **Verification and review**
   - Run focused Phase 2 tests first, then Phase 1 matrix, Nextflow profile parse/runtime, frontend contracts/build, Python compile/whitespace/secret scans.
   - Update the acceptance ledger with exact commands/results and unrelated diagnostics.
   - Freeze one immutable Phase 2 patch, obtain independent security/scientific and adversarial fixture reviews, then make one scoped commit.

## Out of scope

- Phase 3 pinned upstream `wf-clone-validation` parity/integration.
- Claiming organism-level contamination identification from an expected-reference-only mapping screen.
- Claiming physical circularity when only sequence consistency is available; unresolved topology is `REVIEW`.
- Reusing dimer-candidate alignments as the default raw-read evidence surface.
