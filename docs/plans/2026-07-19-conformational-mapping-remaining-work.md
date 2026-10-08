# Conformational Mapping Remaining-Work Implementation Plan

> **For Hermes:** Use subagent-driven-development to execute this roadmap one approved phase at a time, with scientific-contract review and code-quality review before each phase is accepted.

**Goal:** Complete the BioModStack conformational-mapping product defined by `docs/plans/2026-07-06-conformational-mapping-orchestrator.md`: three production lanes, lossless native artifacts, deterministic normalization, full landscapes and analysis, mutagenesis handoff and matched resampling, transactional persistence/API, authenticated current-run acceptance, and the operator-facing launcher/viewer.

**Architecture:** Preserve the existing contract spine and legacy `confornets_experimental` lane. Complete the canonical `conformational_mapping` workflow through backend-discriminated adapters that emit one common manifest envelope without erasing backend-native semantics. Keep API authorization/staging, Nextflow orchestration, Python finalization/analysis, persistence, and frontend consumption as separate ownership boundaries.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic/JSON Schema 2020-12, Nextflow DSL2, Apptainer, Protenix v2, ConforNets, FrustraMPNN, USAlign, React/TypeScript, Mol*.

---

## 1. Scope snapshot and current verdict

- Repository: `/home/dalab/biomodstack/biomodstack`
- Initial audited branch at `6ba08a0`: `harmonize-test-20260719`
- Final audited branch at plan creation: `fix/bms-runtime-cache-isolation-20260719`
- Audited HEAD after concurrent-drift reconciliation: `42bca7041721de4b06a182ed7fbde743c6821245`
- Worktree at plan creation: clean; this plan was absent.
- Authoritative product specification: `docs/plans/2026-07-06-conformational-mapping-orchestrator.md`
- No CM-relevant source changed in drift from `6d9885b` through `42bca70`; later commits affected release, GPU UI math, binder visibility, and runtime cache ownership.

### Current implementation matrix

| Phase | Existing implementation | Formal state | Remaining conclusion |
|---|---|---:|---|
| 0 | Definitions, vectors, probe and validator scripts exist | No `phase_0_spec_check.json`; no authenticated evidence found in the repository, `/mnt/BioModStack/bms_results`, or external phase-evidence root | Re-run and formally qualify current runtime |
| 1 | Eight schemas, typed contracts, contract tests | `STOP` | Revalidate current bytes, repair any defects, independent review, operator GO |
| 2 | mmCIF normalization, structure map, normalization tests | `STOP` | Expand edge coverage, revalidate, independent review, operator GO |
| 3 | Model/template registration, request builder, routing skeleton | `STOP`; public launch returns HTTP 403 | Build authenticated typed submission boundary after prerequisite GO; retain fail-closed generic route |
| 4 | Canonical ConforNets prep/bind/finalize adapter and focused tests | `STOP` | Add authoritative write-time coordinate emission and current-run proof |
| 5 | No canonical Protenix adapter/finalizer/tests/review | Absent | Implement complete-complex Protenix lane |
| 6 | No import stager/module/security tests/review | Absent | Implement secure import lane |
| 7 | No canonical FrustraMPNN wrapper/finalizer/tests/review | Absent | Implement exact-20 landscapes |
| 8 | No analysis service/CLI/tests/review | Absent | Implement comparison, support, robustness and ranking formulas |
| 9 | No handoff service/tests/review | Absent | Implement prepared Mutagenesis Library handoff |
| 10 | No resampling service/module/tests/review | Absent | Implement matched WT/mutant Protenix resampling |
| 11 | No CM persistence service/router/API tests/review | Absent | Implement transactional persistence and nonvisual API |
| 12 | No current-run evidence matrix/review | Absent | Execute and independently review the release matrix |
| 13 | Legacy ConforNets viewer helpers exist; canonical CM launcher/viewer files do not | Absent | Implement operator launcher, job monitoring and canonical viewer |

### Current hard stops

1. `platform/api/routers/jobs.py:4952-4960` rejects canonical CM creation with HTTP 403.
2. `workflows/conformational_mapping.nf:10-34,64-87` explicitly hard-fails Protenix and import branches.
3. `platform/api/config/templates/conformational_mapping.yaml:7-20` advertises validation-only behavior and exposes no user controls.
4. The pinned ConforNets code does not emit run/saved-step/ConforNet/sample identities at the moment each coordinate file is written.
5. `docs/reviews/conformational_mapping/phase_1_spec_check.json` through `phase_4_spec_check.json` all record `STOP`; Phase 0 has no review file.
6. Canonical Phase 5-13 source/test/review files are absent.
7. Legacy ingester gate currently has one real failure: `test_confornets_ingests_native_confidence_evaluation_diversity_and_landscape_metrics` reaches SQLAlchemy `MissingGreenlet` through `routers/designs.py:_collect_plotly_metrics` after ingestion. The projected `load_only(ANALYTICS_LOAD_ONLY_COLUMNS)` query omits `review_profile_id`, then metric authorization accesses that unloaded field. Current result: 4 passed, 1 failed.
8. Production result contracts contain only legacy `confornets_monomer_v1`; the five required canonical contracts are absent.
9. Canonical `model_id=conformational_mapping` is not recognized by `_is_confornets_job()`, and current idempotency relies on name/sample/frame heuristics instead of stable candidate identity.
10. Invalid or missing legacy JSON can degrade to `None` and fall back to globbed conformer files; there is no authoritative manifest hash/cardinality gate in production ingestion.
11. Legacy UI rendering and candidate ordering can depend on artifact strings, metric shape, frame metadata, or names rather than an approved canonical result contract and API order.
12. No authenticated current-run Phase 0 or Phase 12 evidence was found.

## 2. Non-negotiable completion boundary

The suite is not complete until all of the following are true:

- Protenix v2, ConforNets, and secure import each produce validated backend-native trees and canonical manifests.
- ConforNets coordinates come directly from write-time loop identities; no path-name reconstruction.
- Protenix retains complete complexes, repeated copies, modifications, covalent bonds, preprocessing declarations, confidence and full-data sidecars.
- Every analyzed protein residue maps from authoritative mmCIF identity to normalized PDB identity and back.
- FrustraMPNN emits exactly 20 explicit substitution slots per scoreable residue, with missingness and provenance.
- Analysis implements the specification's matched-coordinate, hierarchical-support, robustness and ranking formulas exactly.
- Mutagenesis handoff and WT/mutant resampling are independently transactional and idempotent.
- Persistence rejects hash-invalid, partial, unknown or misclassified contracts atomically.
- Users can configure, submit, monitor, cancel/retry when allowed, inspect failures, compare candidates, inspect overlays, and download native/canonical outputs.
- A current authenticated release matrix passes on the installed runtime and records exact image/checkpoint/tool/resource identities.
- Every Phase 0-13 review has current hashes, independent reviewers and GO.

## 3. Critical path and execution order

```text
Gate restoration (0-4)
  -> canonical Protenix (5)
  -> secure import (6)
  -> full landscapes (7)
  -> analysis/ranking (8)
  -> prepared handoff (9)
  -> matched resampling (10)
  -> persistence + typed API (11)
  -> authenticated current-run matrix (12)
  -> operator launcher/viewer (13)
```

No downstream phase may use a controlled fixture as evidence that an upstream runtime has passed. Development can be prepared on isolated branches, but acceptance remains sequential because each phase consumes the previous phase's frozen hashes and contracts.

---

## 4. Work package A — Restore a trustworthy Phase 0-4 foundation

### A1. Repair the legacy ingestion baseline

**Objective:** Remove the SQLAlchemy async-session failure so the required legacy no-regression gate produces a trustworthy result.

**Files:**
- Modify: `platform/api/routers/designs.py`
- Modify only if the defect belongs there: `platform/api/services/result_ingester.py`
- Test: `platform/api/tests/test_confornets_result_ingester.py`

**Required tests:**
- Reproduce the current `MissingGreenlet` failure before the fix.
- Add `review_profile_id` to the explicit analytics projection, or materialize the complete authorization decision inside the awaited query path; prove no deferred ORM load occurs.
- Ensure `_collect_plotly_metrics` does not trigger expired/lazy ORM I/O outside the awaited query path.
- Reingestion enriches rows without duplicates.
- Historical `conformer` spelling remains stored unchanged.
- Canonical reads normalize only in memory.

**Gate:** All five ConforNets ingester tests pass in the repository's route-free pytest namespace; no existing result-contract test regresses.

### A2. Re-run Phase 0 runtime truth

**Objective:** Replace stale/historical assumptions with authenticated facts from the installed runtime.

**Files:**
- Verify/update: `docs/specs/conformational_mapping/cm_contract_definitions_v1.md`
- Verify/update: `docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json`
- Verify/update: `scripts/probes/conformational_mapping/probe_phase0_runtime.py`
- Verify/update: `scripts/probes/conformational_mapping/validate_phase0_vectors.py`
- Create: `docs/reviews/conformational_mapping/phase_0_spec_check.json`
- Evidence outside repo: `/home/dalab/biomodstack-phase-evidence/conformational_mapping/phase_0/<UTC>/`
- Runtime outputs outside repo: `/mnt/BioModStack/bms_results/conformational_mapping_phase0/<run_id>/`

**Required facts:**
- Exact hashes/versions for Protenix SIF/checkpoint, ConforNets SIF/OpenFold checkpoint/state, FrustraMPNN SIF/checkpoint, USAlign and Nextflow.
- Protenix entity support and exact output tree for protein, DNA, RNA, ligand, ion, modifications, covalent bonds and repeated copies.
- Seed/sample/default-parameter/MSA/template behavior and local resource use.
- ConforNets task-specific dimensions and exact output-write behavior.
- FrustraMPNN exact row semantics, checkpoint and threshold behavior.

**Gate:** Every Phase 0 vector is authenticated or the supported scope is narrowed explicitly; independent runtime/scientific/security review; operator GO.

### A3. Re-certify Phases 1 and 2 against current bytes

**Objective:** Turn technically useful schema/normalization code into reviewed prerequisites.

**Files:**
- Existing Phase 1/2 implementation and tests.
- Update review records only after exact current-byte review.

**Additional Phase 2 coverage:**
- Multiple models with explicit selection.
- Alternate-location occupancy ties.
- Insertion codes and author numbering collisions.
- Multi-character chain IDs and PDB chain-space exhaustion.
- Repeated identical entities with distinct instance identities.
- Waters, ligands, ions and non-polymers present but excluded from protein scoring without loss from the authoritative complex.
- Modified and unknown residues.
- Malformed/short mmCIF loops and duplicate atom IDs.
- Round-trip mapping and deterministic byte output.

**Gate:** Phase 1 and Phase 2 review records contain current HEAD/tree hashes, complete commands, independent approvals and GO.

### A4. Complete the Phase 3 typed control plane

**Objective:** Preserve the generic endpoint's 403 while providing a principal-aware typed CM submission API backed by server-owned artifact identities.

**Files:**
- Modify: `platform/api/services/conformational_mapping/request_builder.py`
- Create initially in Phase 11 ownership or amend the phase boundary explicitly: `platform/api/routers/conformational_mapping.py`
- Modify: `platform/api/main.py` for router registration
- Modify: `platform/api/services/nextflow.py`
- Test: `platform/api/tests/test_conformational_mapping_routing.py`
- Create: `platform/api/tests/test_conformational_mapping_submission.py`

**Required behavior:**
- Pure validation before DB row, output root or scheduler event.
- Server-owned principal/source fields.
- Registered artifact IDs, never arbitrary runtime paths.
- Atomic job/request/coordinate-plan materialization.
- One canonical request authority; hidden/inactive frontend fields cannot leak defaults.
- Submit/status/cancel/retry permissions defined by typed job state.

**Gate:** Negative requests prove zero DB/scheduler/output mutations; positive controlled submission persists one request and one coordinate plan with exact hashes.

### A5. Instrument ConforNets at coordinate write time

**Objective:** Resolve the current Phase 4 hard stop without altering legacy behavior.

**Required design:**
- Build a canonical-only pinned image or patch layer; do not change `confornets_experimental` runtime bytes.
- Patch the pinned upstream write loop to emit an append-only coordinate sidecar containing target, task, test case, reference/null, run, saved step, ConforNet index, sample index, relative path, bytes and SHA-256.
- Bind each sidecar row to request/runtime/container/checkpoint identities.
- Use atomic sidecar publication and fail if a coordinate file exists without exactly one matching row.
- Never recover missing dimensions from sorted paths.

**Files to create/modify after an allowlist amendment:**
- Create: `containers/confornets-canonical/ConforNets.def`
- Create: `containers/confornets-canonical/patches/emit_coordinate_ledger.patch`
- Create: `scripts/validate_confornets_coordinate_emission.py`
- Modify: `modules/conformational_mapping_confornets.nf`
- Modify: `scripts/bind_confornets_output_ledger.py`
- Modify: `scripts/finalize_confornets_conformational_mapping.py`
- Test: `platform/api/tests/test_conformational_mapping_confornets.py`
- Review: `docs/reviews/conformational_mapping/phase_4_spec_check.json`

**Gate:** Current installed canonical image runs diversity and reference-guided cases; expected and observed coordinate sets match exactly; native bytes remain unchanged; legacy lane hashes/tests are unchanged; independent reviewers and operator GO.

---

## 5. Work package B — Complete all three producer lanes

### B1. Phase 5: Protenix complete-complex ensemble

**Files:**
- Modify narrowly: `modules/protenix.nf`
- Create: `modules/conformational_mapping_protenix.nf`
- Create: `scripts/finalize_protenix_conformational_mapping.py`
- Modify: `workflows/conformational_mapping.nf`
- Create: `platform/api/tests/test_conformational_mapping_protenix.py`
- Create fixtures under: `platform/api/tests/fixtures/conformational_mapping/protenix/`
- Create review: `docs/reviews/conformational_mapping/phase_5_spec_check.json`

**Implementation:**
- Convert the complete-complex snapshot without dropping entities, fields, copies, modifications or bonds.
- Reject malformed/non-positive counts rather than coercing them to one, and require one ordered runtime/output identity per declared copy.
- Preserve ordered IDs and source→runtime→output identity.
- Carry target/seed/sample coordinates in channels.
- Retain the native hierarchy; remove basename-flattening from the canonical lane.
- Bind CIF, confidence, full-data, preprocessing/MSA/template declarations, logs and runtime metadata.
- Enforce exact cardinality, composition audit and immutable resume key.

**Gate:** Positive and negative complete-complex matrix passes; current-run small protein and mixed-complex jobs produce complete manifests with measured resources.

### B2. Phase 6: Secure external import

**Files:**
- Create: `platform/api/services/conformational_mapping/import_stager.py`
- Create: `modules/conformational_mapping_import.nf`
- Modify: `platform/api/routers/conformational_mapping.py`
- Modify: `workflows/conformational_mapping.nf`
- Create: `platform/api/tests/test_conformational_mapping_import_security.py`
- Create review: `docs/reviews/conformational_mapping/phase_6_spec_check.json`

**Implementation:**
- Accept authenticated upload handles or registered artifact IDs only.
- Descriptor-safe open/copy, containment checks, no-follow behavior, limits and content inspection.
- Revalidate source identity immediately before scheduling and rehash after copy.
- Emit immutable receipt and deterministic import coordinates.
- Reject traversal, encoding tricks, globs, metacharacters, symlink races, retargeting, collisions and duplicate content before scheduling.

**Gate:** Every attack case proves zero scheduling/output/DB mutation; positive imports preserve request order, hashes and provenance.

---

## 6. Work package C — Complete the scientific analysis plane

### C1. Phase 7: Full FrustraMPNN landscapes

**Files:**
- Create: `modules/conformational_mapping_frustrampnn.nf`
- Create: `scripts/finalize_frustrampnn_landscape.py`
- Modify: `workflows/conformational_mapping.nf`
- Create: `platform/api/tests/test_conformational_mapping_frustrampnn.py`
- Create review: `docs/reviews/conformational_mapping/phase_7_spec_check.json`

**Implementation:**
- Reuse `FrustrampnnQC`; do not duplicate it.
- Feed the Phase 2 normalized PDB and structure map, not the inline generic converter as identity authority.
- Validate raw rows and exact 20-slot amino-acid coverage per scoreable mapped residue.
- Preserve continuous scores, raw CSV, selected checkpoint hash, threshold policy and explicit missingness.
- Keep excluded partner/context semantics explicit.

**Gate:** Exact-20 and mapping tests pass; one current runtime scoring row validates against raw output and authoritative source identity.

### C2. Phase 8: Comparison, support and ranking

**Files:**
- Create: `platform/api/services/conformational_mapping/analysis.py`
- Create: `scripts/analyze_conformational_mapping.py`
- Create: `platform/api/tests/test_conformational_mapping_analysis.py`
- Create review: `docs/reviews/conformational_mapping/phase_8_spec_check.json`

**Implementation:**
- Exact matched-coordinate pairing and invariant comparison.
- Hierarchical aggregation by backend outer/inner strata.
- Explicit expected/valid support and unmatched reasons.
- Substitution, context, redistribution, sign consistency, clash-free fraction and rank-stability formulas exactly as specified.
- Deterministic total status and ranking order with reconstructable components.
- No unsupported energetic or biological-benefit interpretation.

**Gate:** Independent hand calculations reconstruct every output number, status and rank; mutation/order/missingness adversaries fail.

---

## 7. Work package D — Complete mutation handoff and matched resampling

### D1. Phase 9: Prepared Mutagenesis Library handoff

**Files:**
- Create: `platform/api/services/conformational_mapping/mutagenesis_handoff.py`
- Modify narrowly: `platform/api/routers/jobs.py`
- Create: `platform/api/tests/test_conformational_mapping_handoff.py`
- Create review: `docs/reviews/conformational_mapping/phase_9_spec_check.json`

**Gate:** Author identity maps to sequence identity; WT/source hashes validate; retries are idempotent; injected failures leave no partial registration; scheduler launch count remains zero.

### D2. Phase 10: Matched WT/mutant Protenix resampling

**Files:**
- Create: `platform/api/services/conformational_mapping/resampling.py`
- Create: `modules/conformational_mapping_resampling.nf`
- Modify: `workflows/conformational_mapping.nf`
- Modify narrowly: `platform/api/routers/conformational_mapping.py`
- Create: `platform/api/tests/test_conformational_mapping_resampling.py`
- Create review: `docs/reviews/conformational_mapping/phase_10_spec_check.json`

**Implementation:**
- Materialize WT and mutant from complete-complex snapshots.
- Change only declared protein residues and approved changed-entity features.
- Preserve all other entities, copies, bonds, order and feature bytes.
- Support and prove all three feature modes with pinned tool/database/settings hashes.
- Pair outputs only by exact runtime coordinates and invariants.
- Make registration/launch atomic and idempotent.

**Gate:** Byte-level unchanged-entity proof, exact substitution proof, matched cardinality and explicit unmatched rows; current-run WT/mutant job deferred to Phase 12.

---

## 8. Work package E — Persistence, API and lifecycle

### E1. Phase 11 persistence and result contracts

**Files:**
- Modify: `platform/api/services/result_contracts.py`
- Modify: `platform/api/services/result_ingester.py`
- Create: `platform/api/services/conformational_mapping/persistence.py`
- Create: `platform/api/routers/conformational_mapping.py`
- Modify: `platform/api/main.py`
- Modify additively if required: `platform/api/database.py`, `platform/api/run_migrations.py`
- Modify narrowly: `platform/api/routers/designs.py`
- Create: `platform/api/tests/test_conformational_mapping_persistence.py`
- Create: `platform/api/tests/test_conformational_mapping_api.py`
- Create review: `docs/reviews/conformational_mapping/phase_11_spec_check.json`

**Required result contracts:**
- `conformational_mapping_protenix_v1`
- `conformational_mapping_confornets_v1`
- `conformational_mapping_import_v1`
- `conformational_mapping_analysis_v1`
- `conformational_mapping_resampling_v1`

**Production resolution and ingestion corrections:**
- Move exact alias policy into the production resolver: accept only `monomer_conformation` and historical `conformer`, normalize in memory, and never rewrite historical stored spelling merely to canonicalize it.
- Recognize canonical `model_id=conformational_mapping` only through its approved backend-discriminated result contract; do not classify it through substring or metric-shape heuristics.
- Use stable request/candidate IDs and manifest hashes for idempotency; names, frame indices, and sample-index guesses are not authority.
- Reject missing, malformed, hash-invalid, cardinality-invalid, partial, extra, duplicate, shared, or unreferenced manifests atomically. Do not fall back to recursive structure globs for canonical jobs.
- Persist ensemble, native-manifest, structure-map, landscape, analysis, handoff, resampling, lineage, support, and missingness records rather than only generic artifact columns and aggregate frustration summaries.
- Replace whole-CSV landscape reads with paginated/range-backed canonical endpoints.

**Lifecycle/API behavior:**
- Typed submit, status, progress, cancellation, retry eligibility, failure receipts and result retrieval.
- Durable state across API restart.
- Transactional idempotent ingestion.
- Hash/cardinality/contract validation before visibility.
- Lineage queries and paged/range landscape access.
- Native/canonical artifact download through content-addressed IDs.
- Exact legacy alias behavior; no historical row rewrite.
- Translate canonical resume into an immutable validated request/resume descriptor. The generic `resume_job_id`/`resume_work_dir` parameter injection cannot be forwarded to a command builder that accepts only `cm_request_path`.

**Gate:** Failure injection at every persistence boundary leaves no partial state; retry/restart tests pass; large matrices remain paged; unknown contracts receive no viewer/analyzer capability.

---

## 9. Work package F — Current-run release qualification

### F1. Phase 12 authenticated release matrix

**Files:**
- Create: `docs/reviews/conformational_mapping/phase_12_spec_check.json`
- Create approved summaries under: `docs/reviews/conformational_mapping/phase_12_e2e_manifest/`
- Store all raw runtime evidence under: `/mnt/BioModStack/bms_results/conformational_mapping_phase12/<run_id>/`

**Required current-run rows:**
- ConforNets diversity
- ConforNets reference-guided
- Protenix protein ensemble
- Protenix complete-complex ensemble
- Secure import positive and pre-schedule rejection
- Exact-20 FrustraMPNN landscape
- Hierarchical analysis/ranking
- Handoff idempotency
- Matched WT/mutant resampling
- Persistence/API roundtrip and restart recovery
- Legacy ConforNets non-regression
- Cancel, timeout, backend failure, partial-output rejection and retry eligibility

**Gate:** Every scheduled row has authenticated request/status captures, terminal success, exact manifests/hashes/cardinality/composition, runtime/container/checkpoint/resource records and API roundtrip. Every rejection row proves no job/workflow/result/output allocation. Independent runtime, scientific, security, API and release reviews all say GO.

---

## 10. Work package G — Complete operator product

### G1. Typed launcher and job operations

**Files:**
- Create: `platform/frontend/src/components/conformationalMapping/ConformationalMappingLauncher.tsx`
- Create: `platform/frontend/src/components/conformationalMapping/conformationalMappingApi.ts`
- Modify: `platform/frontend/src/components/JobSubmission.tsx`
- Modify: `platform/frontend/src/components/workflowModelInventory.ts`
- Create: `platform/frontend/tests/conformationalMappingLauncher.test.ts`

**Required UI:**
- Explicit backend selection and backend-specific fields.
- Complete-complex target editor or registered snapshot selection.
- ConforNets task/reference controls within supported limits.
- Import handle selection with no raw server paths.
- Seed/sample/resource estimate and storage warning.
- Validation errors before submission.
- Status/progress/logs, cancel/retry rules and failure receipt display.
- Canonical launch must receive dedicated handling; the existing launcher special case for `confornets_experimental` is not sufficient.

### G2. Canonical ensemble/analysis viewer

**Files:**
- Create: `platform/frontend/src/components/conformationalMapping/ConformationalMappingViewer.tsx`
- Create: `platform/frontend/src/components/conformationalMapping/conformationalMappingSemantics.ts`
- Modify: `platform/frontend/src/components/StructureViewerPane.tsx`
- Modify only through public adapter APIs: `platform/frontend/src/components/MolstarViewerImpl.tsx`
- Create: `platform/frontend/tests/conformationalMappingSemantics.test.ts`
- Extend: `platform/frontend/tests/structureViewerSemantics.test.ts`
- Create review: `docs/reviews/conformational_mapping/phase_13_spec_check.json`

**Required UI:**
- Deterministic candidate selection/order and multi-structure overlay.
- Backend coordinate, provenance, support and missingness display.
- Residue mapping and frustration overlays keyed only by API identity.
- Analysis/ranking tables with reconstructable components and warnings.
- Native/canonical artifact downloads.
- Fail closed on unknown contracts; no browser-side scientific calculations.
- Render only approved Phase 11 contracts. Artifact groups, model/provenance strings, nested metric shape, and filename patterns are insufficient authority.
- Preserve API candidate identity and order; never reconstruct order from frame metadata or names.
- Replace legacy “Frame” wording with backend-neutral candidate/sample identity from the canonical contract.
- Add the canonical conformational-mapping lens to `ResultsViewer`; legacy structure-viewer controls alone are not the product surface.

**Gate:** Frontend tests/lint/isolated build pass; browser consumes only Phase 11 APIs; API hashes and displayed identities match; independent frontend/scientific review and operator GO.

---

## 11. Reconciled audit evidence

- Independent scientific/data-plane audit validated all 53 Phase 0 vectors across nine fixture files; every runtime status remains unmeasured.
- An exact-commit archive ran 148 focused Phase 1-4 tests: 148 passed. This establishes reusable source behavior only.
- Independent API/UI audit ran 124 schema/normalization/ConforNets tests: 124 passed.
- Current frontend suite: 389 passed, covering legacy controls but not canonical Phase 13 acceptance.
- API/result/legacy-ingestion gate: 33 passed, 1 failed. The failure and exact projection cause are recorded in Work Package A1.
- All required Phase 11 and Phase 13 acceptance IDs are absent.
- The three audits modified no repository files and agreed that the earliest release gate is Phase 0.

## 12. Phase-wide verification requirements

Every implementation phase must run:

```bash
PYTHONPATH=platform/api python -m pytest -q <phase tests> <all prior CM tests>
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_confornets_experimental.py \
  platform/api/tests/test_experimental_nextflow_entrypoint.py \
  platform/api/tests/test_confornets_result_ingester.py \
  platform/api/tests/test_result_contracts.py \
  platform/api/tests/test_nextflow_entrypoint_registry.py
uv run --project platform/api --group dev --frozen ruff check <changed Python paths>
uv lock --project platform/api --check
/home/dalab/.local/lib/nextflow/25.10.1/nextflow config workflows/conformational_mapping.nf -flat
pnpm --dir platform/frontend test
pnpm --dir platform/frontend lint
BMS_FRONTEND_BUILD_OUT_DIR=/tmp/cm_frontend_dist pnpm --dir platform/frontend build:isolated
git diff --check
```

Runtime phases additionally require exact command, image/checkpoint/tool hashes, environment, GPU allocation, wall time, peak RAM/VRAM/storage, artifact tree, request/manifest hashes and terminal state.

## 13. Review and commit discipline

- One exclusive writer for each phase worktree.
- Capture exact phase-start HEAD, status, allowlist and file hashes outside the repo.
- Write RED tests before production changes.
- No broad restore/reset/clean/add operations.
- No commit until exact-tree runtime and review evidence is inspected.
- One scoped commit per independently reviewable tranche.
- Fetch and reconcile the latest `origin/test` before each push; never force-push.
- A focused pass cannot override a failed broad or legacy gate.
- Review must name the exact candidate tree and patch hash.

## 14. Recommended delivery tranches

| Tranche | Deliverable | Approximate review units |
|---|---|---:|
| 1 | Baseline repair, Phase 0 truth, Phase 1-3 recertification | 3-4 |
| 2 | Canonical ConforNets coordinate-emitting runtime and Phase 4 GO | 2-3 |
| 3 | Protenix complete-complex lane | 2-3 |
| 4 | Secure import lane | 1-2 |
| 5 | FrustraMPNN landscape | 1-2 |
| 6 | Analysis/ranking | 2-3 |
| 7 | Handoff and resampling | 3-4 |
| 8 | Persistence, typed API and lifecycle | 3-4 |
| 9 | Current-run release matrix | 1 evidence tranche |
| 10 | Operator launcher and canonical viewer | 3-4 |

Expected total: approximately **21-30 independently reviewable units**, not one monolithic change.

## 15. Immediate next tranche

Do not start Phase 5 or UI work first. The next implementation tranche is:

1. Repair and prove the legacy ingester `MissingGreenlet` defect.
2. Re-run Phase 0 against current installed assets and create the missing review/evidence record.
3. Re-certify current Phase 1 and Phase 2 bytes.
4. Amend the Phase 4 allowlist for a canonical-only instrumented ConforNets image.
5. Implement write-time coordinate emission and obtain Phase 4 current-run GO.
6. Only then start the Protenix complete-complex adapter.

## 16. Definition of done

The requested suite is done only when Phases 0-12 have current independent GO records, all three producer lanes and all downstream analysis/handoff/resampling/persistence capabilities pass the authenticated current-run matrix, and Phase 13 supplies an operator-usable launcher/viewer without inventing or weakening backend semantics. Until then, status is reported by the first unresolved gate, never by file count or focused-test count.
