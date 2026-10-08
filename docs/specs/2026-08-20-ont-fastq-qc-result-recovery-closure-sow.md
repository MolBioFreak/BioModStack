# ONT FASTQ-QC Result Recovery and Browser Closure SOW

**Status:** Controlling implementation and acceptance specification
**Date:** 2026-08-20
**Parent specification:** `docs/specs/2026-08-12-ngs-molbio-global-project-integration-sow.md`
**Target branch:** `test`
**Target environment:** Development
**Production:** Out of scope

## 1. Goal

Restore one ordinary, operator-usable ONT FASTQ-QC path from the existing BFX6NB Project record through a persisted, decision-grade browser result:

`BFX6NB Project and Experiment -> Q2-01 managed FASTQ -> managed eGFP reference -> ont_fastq_qc -> persisted retry3 authority -> result API -> normal browser reopen -> report -> governed downloads and Range -> compact local IGV`

The accepted path must use the existing retry3 scientific result. It must not submit another compute job or change scientific artifact bytes.

## 2. Relationship to the parent specification

This document amends and closes the retained FASTQ-QC result and reopen slice of the parent NGS/MolBio SOW. It does not declare the complete parent SOW finished.

This document replaces informal separation between “backend is kosher” and later UI work. Backend authority, lifecycle publication, browser authorization, scientific presentation, governed byte delivery, and viewer operation are one closure denominator.

When this document and an older retry3 task note disagree, this document controls the remaining closure work. Immutable historical evidence remains historical evidence.

## 3. Authorized work and exclusions

### 3.1 Authorized

- Rewrite and track this bridge specification.
- Repair NGS-only backend, frontend, tests, and deployment wiring required by this specification.
- Commit the isolated candidate.
- Reconcile it with current `origin/test` without force.
- Run repository tests, type checks, builds, static checks, and browser acceptance required here.
- Push the accepted candidate to `test` without force.
- Deploy through the canonical managed Development owner.
- Back up affected Development databases before a write.
- Reconcile retry3 lifecycle projection and persisted stage mirrors through a bounded, audited, idempotent repair path.
- Use retry3 artifacts for API, Range, download, report, and IGV acceptance.

### 3.2 Excluded

- Any fifth or replacement scientific compute job.
- Mutation, regeneration, relabeling, or replacement of retry3 scientific artifact bytes.
- Rewriting retry3 historical runtime, CUDA, Dorado, samtools, scheduler, or retry provenance as if it had been produced by the repaired runtime.
- Production promotion or Production restart.
- Unrelated BioXP, OEM, MD, protein, Gibson, or other worktree changes.
- Weakening job scope, path containment, stale-attempt, cancellation, ownership, checksum, size, manifest, source-FASTQ, reference, or job-mismatch controls.
- Treating the CDN-backed `igv_report.html` as the accepted interactive viewer.
- A new locus-read-list or exact-read-evidence API, digest-bound read identity, alignment-click protocol, or selected-read scientific report. The existing raw-read inspector may remain collapsed and nonblocking, but it is not acceptance authority for this closure.

## 4. Frozen acceptance fixture

### 4.1 Authority chain

| Item | Frozen identity |
|---|---|
| Project | `4af72c1d-27d8-4e14-8f39-4259a80494a0` |
| Global Experiment | `9a10c5a8-b233-4bf3-af14-9c2880525278` |
| Domain Experiment | `916a611b-6879-486f-bf9e-e1b5a796e01c` |
| Domain state revision | `molbio_ngs_state_revision_5922d66c-d4fe-44e8-bdc1-1b81c26449c1` |
| State member receipt | `195b526d-35b3-40e4-b400-e8e4232a98fc` |
| Sample revision | `molbio_ngs_sample_revision_b81cd561-e18d-4fab-9c48-f8aa40f45e19` |
| Reference revision | `molbio_ngs_reference_revision_1f508b7f-15f1-482a-9148-c3b2054ca56d` |
| Workflow | `ont_fastq_qc` |
| Input mode | `fastq` |
| Job | `31f02bd5-830f-4558-aa78-3873c515de68` |
| Post-reconciliation hierarchy authority | `0a48230248b1c2001be89a2a099b1cf13773d709d3f7132840194977b681d9e9` |
| Result root | `/home/dalab/.biomodstack-dev/bms_results/BFX6NB_Q2-01_ont_fastq_qc_acceptance_retry3_20260816_175924` |

### 4.2 Input and reference identities

| Identity layer | SHA-256 |
|---|---|
| Managed Q2-01 FASTQ bytes | `957a1c7fb5a4f10089f52b8b26cee37527176575b99ecc5e81a139c1374d8fff` |
| Original imported eGFP FASTA bytes | `8a14bbbeaa2cf2108bd18fbf7aac7d19bef4f79778f46da1264f0ef6bcee9ab3` |
| Canonical managed/staged FASTA bytes | `0db1dbf0aaeb0dd13d430d60283d2411604149f1c7f7cc55aa0727f45634f26d` |
| Normalized eGFP sequence | `0185e3475f9e04c996d2bd2667f83d8655fb12b1e426bc5b674261ac4b2f3be4` |
| Canonical contig | `eGFP_plasmid`, 5,570 bp, circular, 1-based inclusive |

Each digest has a different role. The implementation must not compare FASTA byte identity with normalized sequence identity.

### 4.3 Expected scientific result

The UI must preserve producer-owned scientific values. It must not derive replacements in the browser.

- Execution: `completed`
- Total reads: 61,708
- Total bases: 315,879,481
- Mapped reads: 61,573
- Coverage fraction: 1.0
- Coverage-envelope minimum: 24,840 base-covering alignment records at position 3516 from `samtools depth -aa` with default filters; deletion bases are excluded from this producer table.
- Decision-check minimum support depth: 49,126 alignment observations at position 5570 from `per_base_support.tsv`; deletion-spanning observations are included in this producer table.
- Consensus identity: 0.9998204667863555
- Expected-reference mapping/unmapped-fraction screen: `pass`; basis `expected_reference_mapping_only`; `organism_identity_claimed: false`
- Coverage check: `pass`
- Read-support check: `review`, reason `MIXED_ALLELES_DETECTED`
- Sequence-identity check: `review`, reason `VARIANT_SUPPORT_AMBIGUOUS`
- Construct status: `review_required`
- Normalized variant: VCF record anchored at 3515, span 3515-3516, `REF=GC`, `ALT=G`; the deleted reference base/affected interval is 3516; support approximately 0.56157
- Resource evidence: retry3 has no accepted producer resource-use receipt. Reconciliation records `historical_unavailable`; it does not infer CPU or GPU execution from installed software, scheduler fields, or artifacts.
- Governed package authority: 36 descriptors, 34 present, 2 unavailable, artifact-set SHA-256 `e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650`.

This result makes no taxonomic contamination, organism-identity, sample-purity, or off-target-absence claim.

“Execution completed” and “scientific review required” must appear as separate states.

## 5. Critical baseline re-evaluation

This table records the candidate state at `f4938ae6` before this closure SOW was executed. It is a root-cause ledger. It is not a claim about later working-tree edits or final acceptance.

| Surface | Baseline state | Closure gap |
|---|---|---|
| Scientific package | Retry3 manifests and artifacts exist and were previously hash-verified | Exact current package/hash closure must be repeated in the final evidence window |
| Result authority service | Implemented locally | Direct unit and adversarial tests are absent |
| Future ONT finalizer | Implemented locally | It accepts only absolute terminal outputs, while retry3 and the workflow persist canonical result-root-relative outputs |
| Stage plan | Candidate assigns transient `job.all_stages` | The deployed `jobs` schema has no `all_stages` column; planned stages must come from the workflow contract or response projection |
| Stage publication | Snapshot CAS implemented locally | Concurrency, cancellation, stale callback, and immutable-terminal tests are required |
| Historical retry3 result | Result projection can derive from manifests and provenance | Persisted `completed_stages` and `stage_outputs` remain incomplete and need an audited repair receipt |
| Generic result ingestion | Canonical FASTQ-QC bypass implemented locally | It needs a direct regression proving no design-result publication occurs |
| FASTQ-only resources | Irrelevant GPU/Dorado settings removed from future launch construction | Direct normalization and launch regressions are required; retry3 historical fields remain immutable audit evidence |
| Frontend parser/query | Implemented locally | Exact parser tests, newest-source ownership, malformed payload, and authorization-recovery tests are absent |
| Result panel | Partial | It displays headline metrics but omits the full decision hierarchy, coverage/read-length visuals, governed downloads, and direct variant-to-viewer navigation |
| Run Inspector hierarchy | Existing configuration and generic stage surfaces precede or duplicate the result | Canonical FASTQ-QC must lead with producer-native decision evidence and collapse technical audit details |
| IGV | Existing full-screen modal | It loads the default IGV genome catalog, permits external locus lookup, auto-requests full screen, and permanently mounts a large raw-read inspector |
| HTML IGV report | Persisted and governed | It loads IGV from jsDelivr and is a download-only artifact for this acceptance |
| Integration | Candidate is dirty and based on `f4938ae6`; fetched `origin/test` is newer | Commit, non-destructive reconciliation, exact-tree retest, review, and guarded push are open |
| Development | Candidate is not deployed | Source, owner, process, listener, database, and browser identities are open |

The candidate is useful recovery work. It is not an accepted backend or product release in its current state.

## 6. Authority and architecture

### 6.1 Canonical authorities

1. The persisted Job supplies job ID, workflow identity, input mode, source input path, reference sequence digest, output directory, status, and historical provenance.
2. `fastq_qc/qc_manifest.json` is the sequence-QC manifest authority.
3. `verification/qc_manifest.json` is the construct-verification authority. It must remain semantically separate from sequence QC.
4. `build_ngs_package_artifacts()` supplies a bounded, cross-bound artifact inventory.
5. `build_alignment_sessions()` supplies the job-scoped genomic viewer contract.
6. `stage_terminal_states` supplies immutable terminal stage receipts. `completed_stages` and `stage_outputs` are durable query mirrors, not independent scientific authority.
7. The browser consumes one strict result projection. It must not search server directories or infer scientific meaning from filenames.

### 6.2 One result path

Canonical `ont_fastq_qc` jobs must bypass generic design ingestion and generic zero-design result semantics. They must use the ONT FASTQ-QC finalizer and result projection only.

### 6.3 One terminal owner

The standard Nextflow terminal publication CAS is the sole transition from active execution to terminal Job state. ONT finalization validates and prepares the terminal mutation. It must not commit a competing terminal state.

Cancellation, awaiting-input, stale-attempt, and operator-gated rows remain authoritative over late worker publication.

### 6.4 Normative contract package

The implementation and release package contains these normative contracts:

- `schemas/ngs/ont_fastq_qc_result_v1.schema.json` for the bounded result response;
- `schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json` for the additive retry3 repair receipt;
- `schemas/ngs/ont_alignment_session_v1.schema.json` for alignment-session list and detail responses;
- `schemas/ngs/ont_ngs_error_v1.schema.json` for governed error responses;
- `schemas/ngs/ont_ngs_rotation_success_v1.schema.json` for capability-rotation success;
- `schemas/ngs/ont_ngs_capability_revocation_success_v1.schema.json` for idempotent capability revocation;
- `schemas/ngs/ont_fastq_qc_evidence_bundle_v1.schema.json` for each A1 through A17 evidence body;
- `schemas/ngs/ont_fastq_qc_gate_evidence_body_v1.schema.json` for the closed content of every required evidence file;
- `schemas/ngs/ont_fastq_qc_browser_evidence_manifest_v1.schema.json` for ordered browser evidence;
- `schemas/ngs/ont_fastq_qc_independent_review_receipt_v1.schema.json` for each exact-tree review;
- `schemas/ngs/ont_fastq_qc_deployment_receipt_v1.schema.json` for fencing, quiescence, backup, source, process, listener, database, and migration authority;
- `schemas/ngs/ont_fastq_qc_final_acceptance_receipt_v1.schema.json` for the retained release verdict;
- `platform/api/tests/fixtures/ont_fastq_qc_result_retry3_v1.json` for the shared retry3 result fixture.

`docs/specs/2026-08-20-ont-fastq-qc-result-recovery-spec-package.json` is the seal manifest. Its `records` array is in the exact normative order above, with the review ledger immediately after the SOW. Each row contains exactly path, size, and SHA-256. `package_sha256` is `SHA256(UTF8(RFC8785(manifest_without_package_sha256)))`. Only the top-level `package_sha256` field is omitted. Record order is preserved and participates in the digest. The manifest schema literal `bms.ont-fastq-qc-specification-package.v1` supplies domain separation.

Every timestamp in these contracts is UTC RFC 3339 with a terminal `Z`. A timezone-free timestamp is invalid. Every validator must assert `format` and the UTC lexical form.

JSON Schema owns structural validation. Two closed semantic validators own rules that Draft 2020-12 cannot express:

- `bms.ngs.fastq-qc-result-construction-validator.v1` runs only in the backend while the complete producer rows, manifests, persisted authority, and bounded projection are available. It proves source-row contiguity, full-source extrema, histogram construction, package authority, required-artifact completeness, resource-evidence coherence, and every wire invariant. It emits `coverage.construction_attestation` with exactly `validator`, `source_rows_sha256`, `source_row_count`, `projection_sha256`, and `validated_at`.
- `bms.ngs.fastq-qc-result-wire-validator.v1` runs at backend serialization, the FastAPI response boundary, the TypeScript parser, and exact-fixture gates. It validates the bounded response only. It recomputes `projection_sha256` over RFC 8785 canonical JSON of the complete `coverage` object with `construction_attestation` omitted, requires the attested row count to equal `source_row_count`, validates projected point order and reported extrema within the bounded points, and enforces rules 1 through 20. Only full-source extrema and omitted-row correctness in rule 5 remain construction-only and are accepted through the construction attestation.

The `x-bms-cross-field-invariants` array is synchronized with both validators and does not count as enforcement. An invocation boundary must run the validator whose evidence domain it possesses. No browser validator may claim to inspect omitted source rows.

## 7. Backend requirements

### AUTH-1: Exact job applicability

The ONT result path applies only when persisted authority identifies the canonical nanopore FASTQ-QC workflow and FASTQ input mode. Display counts and filename substrings are not applicability signals.

### AUTH-2: Exact manifest locations

The canonical result endpoint must select `fastq_qc/qc_manifest.json` for sequence QC and `verification/qc_manifest.json` for construct verification. An ambiguous first-match search must not choose a verification manifest as sequence QC.

### AUTH-3: Cross-binding

Before publication, the backend must verify scientific and hierarchy authority:

- authenticated server-derived principal and role through the existing trusted application boundary;
- persisted Project owner or authenticated NGS operator authority;
- exact frozen Project → Global Experiment → Domain Experiment → state revision → member receipt → Job chain;
- exact sample revision and reference revision named by that member receipt and Job;
- exact job ID and workflow ID;
- exact input mode;
- canonical completed analysis state;
- normalized reference digest against Job authority;
- canonical FASTA byte and contig identities where declared;
- source FASTQ digest and size against persisted source input;
- verification inputs against sequence-QC BAM, BAI, stats, reference, and reads;
- every present artifact’s owner scope, route identity, path authority, regular-file type, size, and SHA-256;
- every `owner_scope=result_root` manifest and artifact resolves with no-follow opens below the exact Job result root;
- the sole `owner_scope=managed_input_snapshot` row is `source_reads_fastq`. It resolves only through the exact persisted Job source-input authority, managed-input root, immutable snapshot ID, 341,472,590-byte size, and SHA-256 `957a1c7fb5a4f10089f52b8b26cee37527176575b99ecc5e81a139c1374d8fff`. It is never resolved by joining caller path text to the result root or by accepting a different Job's managed input.

Caller-supplied Project, Experiment, sample, reference, or principal identifiers are selectors only. They cannot establish authority. A Job that is separately readable but foreign to the displayed Project/Experiment chain must be rejected or shown only after all foreign context is cleared.

Capability issuance and rotation must bind an immutable digest of this hierarchy. Every governed result, manifest, session, artifact, report, and read request must validate both the exact Job capability and its hierarchy-binding digest. A stale, foreign, degraded, deleted, or mismatched hierarchy fails closed with a typed denial.

Required negatives cover non-owner, non-operator, cross-Project, cross-Global-Experiment, cross-Domain-Experiment, wrong state revision, wrong member receipt, wrong sample revision, wrong reference revision, guessed Job ID, and rotation against each foreign binding.

### AUTH-4: No-follow file handling

Manifest, table, report, reference, BAM, index, managed source input, and download opens must reject symlinks, traversal, non-regular files, replacement races, and declared/observed mismatches. Tests cover a post-validation replacement race, symlink at every path component, a foreign managed-input snapshot, a cross-Job source-input identity, wrong size/digest, and result-root escape. Result-root and managed-input roots are separate authorities and never fall back to each other.

### AUTH-5: Bounded projection

`GET /api/jobs/{job_id}/ngs-result` must return a versioned exact-key contract bounded to:

- 256 artifact descriptors;
- two alignment sessions;
- 2,048 coverage points;
- 256 KiB encoded JSON.

Coverage reduction must use `minmax_envelope_v1` over one strict, single-contig, ascending 1-based source row per reference position. `bucket_width_rows = max(1, ceil(source_row_count / 1024))`. Each source-order bucket emits its earliest tied minimum and earliest tied maximum, removes a duplicate when both extrema occupy one coordinate, and orders emitted points by coordinate. It adds no synthetic endpoint, does not wrap the circular reference, and rejects repeated coordinates, a second contig, or noncanonical order. The projection must include `maximum_point_count=2048`, source count, bucket width, depth basis, depth unit, earliest global-minimum coordinate/value, tie policy, endpoint policy, and circular policy. The frontend must label this bounded envelope and its `samtools depth -aa` deletion-excluding basis.

The envelope minimum is not interchangeable with construct-verification support depth. For retry3, the envelope owns 24,840 at position 3516. The coverage/read-support decision checks own 49,126 at position 5570 from the separate per-base support table. Both use producer values and explicit units.

The normative wire definition is `schemas/ngs/ont_fastq_qc_result_v1.schema.json`. It uses `additionalProperties: false` at every fixed object layer and freezes required keys, field-specific types and units, enum values, nullability, order rules, cardinality, URL/hash/ID syntax, state-discriminated artifact and session branches, stage output counts, closed decision-check metrics and purposes, closed threshold-profile values, and normalized variant coordinates. The expected-reference screen requires `screen_basis=expected_reference_mapping_only` and `organism_identity_claimed=false`. Schema-valid payloads cannot substitute contamination, taxonomy, purity, or off-target claims.

The construction and wire validators must reject every applicable one of these cross-field failures:

1. artifact count disagreement with the artifact array or state counts;
2. an artifact URL whose Job ID or final opaque route segment differs from its owning object's Job ID or `artifact_id`, or whose `artifact_id` equals the file SHA-256;
3. alignment readiness that disagrees with the session list;
4. non-contiguous histogram bins or a histogram total that differs from its source count;
5. coverage points with a foreign reference, nonascending coordinate, wrong bucket width, wrong projected extrema, wrong reported global minimum, malformed construction attestation, or a projection digest mismatch; the construction validator additionally rejects omitted-source-row extrema or row-order errors;
6. reference name, length, topology, normalized-sequence digest, or FASTA-byte identity disagreement across authority, summary, alignment, verification, coverage, and viewer session data;
7. a variant count that differs from the normalized variant array;
8. a variant record or affected interval that violates `vcf_left_anchored_v1`, the declared kind, or linear 1-based bounds;
9. a stage set or order different from the four canonical stages;
10. encoded compact UTF-8 JSON above 262,144 bytes;
11. any non-finite number;
12. a completed accepted result that contains `missing_required` artifact state;
13. a historical or accepted resource-evidence branch with incoherent receipt fields.
14. any artifact whose source, kind, scientific role, media type, disposition, filename extension, or display order differs from the exact UI-5 row at that array position;
15. any artifact display order that is duplicated, gapped, nonascending, or different from the contiguous 1..artifact-count sequence.
16. any completed stage whose status or output count differs from complete 5/6/8/6 in canonical order;
17. a result session summary that differs from the governed list, lacks a ready primary first, or places anything except one optional dimer-candidates session second;
18. a threshold-profile digest that differs from SHA-256 over UTF-8 canonical JSON using sorted keys, comma/colon separators, and `allow_nan=false` for the exact `values` object, or outer version/calibration/public-accuracy metadata that differs from those values;
19. a PASS verdict unless every check passes, all aggregate and row reason-code arrays are empty, every threshold is satisfied, `automatic_pass_eligible=true`, and `public_accuracy_validated=true`; any review/fail check or nonempty reason array requires REVIEW or FAIL as producer-defined;
20. an artifact denominator other than the exact 36 positional rows, count other than 36/34/2, or an `artifact_set_sha256` that does not recompute through AUTH-8.

Backend construction must pass the canonical complete-source fixture. Backend serialization and frontend parsing must pass the same canonical bounded wire fixture and one adversarial fixture per wire invariant. A construction-only adversarial case is tested only at construction boundaries and must carry no fabricated browser-side proof.

The route publishes this same normative contract through its FastAPI response model and OpenAPI 200 response. Agent clients must see the closed top-level keys and shared definitions; an untyped object response is not acceptance.

### AUTH-6: No path disclosure

Public descriptors may expose semantic kind, source, state, size, digest, media type, download URL, and absence reason. They must not expose absolute paths, result-root-relative server paths, or stage output paths.

### AUTH-7: HTML report policy

Active HTML reports must be served as attachments. A valid hash does not grant same-origin execution authority. The CDN-backed retry3 report remains a governed download and is not mounted inline.

### AUTH-8: Complete package authority

Fresh terminalization and normal result reopen use `bms.ngs.package-authority.v1`. The algorithm is:

1. Project each governed descriptor to exactly `source`, `kind`, `state`, `sha256`, and `size_bytes`.
2. A `present` row requires lowercase SHA-256 and a nonnegative integer byte size. An unavailable row requires both values to be null. Public URL, media type, Range capability, and unavailable reason do not enter this digest. They remain schema-validated delivery metadata.
3. Treat rows as a semantic multiset. Reject an exact duplicate five-field row. The same `source` and `kind` may occur more than once only when the full five-field rows differ, as with retry3's two sequence-QC logs.
4. Sort rows by the tuple `(UTF8(source), UTF8(kind), UTF8(state), UTF8(sha256 or ""), UTF8(base10(size_bytes) or ""))` in unsigned byte order. The decimal size has no sign or leading zero. Null digest and size use the empty sort key.
5. Form `{"schema":"bms.ngs.package-authority.v1","records":[...]}` and encode it as UTF-8 RFC 8785 canonical JSON. The `schema` field is the digest-domain label.
6. `artifact_set_sha256` is SHA-256 over those exact bytes.

This algorithm produces retry3 digest `e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650`. The denominator is 36 semantic descriptors, with 34 present and 2 unavailable. It is distinct from the 25 terminal stage-output paths.

The persisted authority also binds workflow, input mode, normalized reference digest, source FASTQ digest, both manifest digests, and the three artifact counts. Fresh Jobs carry this authority in specialized `result_integrity`. Retry3 carries the same fields in the additive reconciliation receipt while preserving its historical generic `result_integrity`. Reopen and every governed route fail before disclosure if persisted authority and observed package bytes differ.

For FASTQ mode, `fastq_qc/qc_manifest.json` is the only sequence-QC manifest authority. `qc_manifest.json` at the result root is not a fallback because it can carry another contract.

## 8. Lifecycle requirements

### LIFE-1: Supported terminal output forms

The finalizer must accept only these stage-output forms:

1. an absolute path contained under the exact Job result root; or
2. a canonical relative path of the form `bms_results/<exact-result-root-name>/<stage-owned-suffix>` that resolves to the same root.

It must reject every other relative base, `.` or `..`, root-name mismatch, symlink, missing file, and out-of-root path.

### LIFE-2: Required terminal stages

The canonical stage plan is:

1. `fastq_align`
2. `dimer_qc`
3. `fastq_qc`
4. `construct_verification`

Every required terminal receipt must be `complete`. A failed, missing, contradictory, or duplicated terminal state fails finalization.

Each stage owns one exact ordered suffix list below the Job result root:

| Stage | Required suffixes | Count |
|---|---|---:|
| `fastq_align` | `align/aligned.bam`, `align/aligned.bam.bai`, `align/reference.fasta`, `align/reference.fasta.fai`, `align/fastq_align.log` | 5 |
| `dimer_qc` | `multimer_qc/dimer_breakpoint_call.tsv`, `multimer_qc/dimer_evidence_by_position.tsv`, `multimer_qc/dimer_read_events.tsv`, `multimer_qc/dimer_breakpoint_sequences.tsv`, `multimer_qc/dimer_secondary_anomalies.tsv`, `multimer_qc/dimer_secondary_summary.tsv` | 6 |
| `fastq_qc` | `fastq_qc/read_lengths.tsv`, `fastq_qc/fastq_qc_summary.tsv`, `fastq_qc/fastq_alignment_stats.tsv`, `fastq_qc/fastq_coverage.tsv`, `fastq_qc/per_base_support.tsv`, `fastq_qc/qc_manifest.json`, `fastq_qc/igv_report.html`, `fastq_qc/fastq_consensus.fasta` | 8 |
| `construct_verification` | `verification/qc_manifest.json`, `verification/verification_summary.tsv`, `verification/variants.vcf`, `verification/per_base_metrics.tsv`, `verification/evidence.html`, `verification/topology_evidence.json` | 6 |

The output order, stage ownership, suffix, and cardinality are normative. No path may occur under two stages. Missing, extra, swapped-stage, duplicate, wrong-prefix, or reordered outputs fail completion and reconciliation.

### LIFE-3: Persisted stage mirrors

A successful finalizer must publish the exact canonical stage list to `completed_stages` and the validated output list per stage to `stage_outputs`. It must not assign a non-persisted `all_stages` attribute.

The response layer may derive `all_stages` from the workflow contract. It may derive completed-stage display from validated terminal receipts for historical completed rows. This display fallback must not silently mutate the database.

### LIFE-4: Copy-on-write CAS

Generic stage callbacks must publish fresh JSON values and compare the full original `completed_stages`, `stage_outputs`, and `provenance` snapshots. Conflicts retry within a fixed bound, then return 409.

### LIFE-5: Immutable terminal receipt

A repeated identical terminal stage report is idempotent. A different status or output list for an existing terminal stage returns 409.

### LIFE-6: Operator state wins

Callbacks and finalizers must fail closed after cancellation, failure, awaiting input, stale attempt, queue-owner loss, or any row state outside the exact active execution predicate.

The terminal publisher captures one pre-completion owner snapshot and compares it in the final conditional update. The snapshot includes Job status and queue status, all awaiting/gate fields, pause state, complete `params`, complete `provenance`, `completed_stages`, `stage_outputs`, current-stage mirrors, run-attempt or launch-generation identity, deterministic unit name, queue-owner/lease identity, and systemd InvocationID when those fields exist. The resource receipt, package authority, stage mirrors, and terminal fields publish only when this exact owner snapshot still matches. A zero-row update publishes nothing and reports a stale-owner conflict.

### LIFE-7: Future resource truth

FASTQ-only `ont_fastq_qc` launch normalization must exclude Dorado/basecalling, CUDA visibility, MSA, ANARCII, pinned-GPU, `cpus_per_gpu`, and GPU-sharing settings. Construct verification uses `fastq_qc_cpu`.

This requirement changes future execution only. It does not rewrite retry3 history.

### LIFE-8: Generic ingestion isolation

Canonical FASTQ-QC completion must not call generic design ingestion, create design rows, publish `result_kind: design`, or display `design_count: 0` as scientific output.

### LIFE-9: Producer resource evidence before fresh success

For a fresh `ont_fastq_qc` execution, the transient execution owner must finish a complete `bms.workflow-resource-usage.v1` receipt after the process exits. Nextflow validates and attaches that receipt to Job params before ONT completion preparation. The resource receipt, package authority, lifecycle mirrors, and terminal Job state publish in the same guarded terminal CAS. Missing, incomplete, malformed, or uncommitted resource evidence prevents `completed` publication.

`execution_resources` is a closed state-discriminated result object:

- `historical_unavailable` requires null receipt schema, ID, and digest. It may show persisted scheduler/configuration fields only as ignored historical metadata. It requires `accelerator_applicability=not_applicable`, `dorado_invoked=false`, and an explicit statement that scheduler or software metadata is not execution evidence.
- `accepted` requires `receipt_schema=bms.workflow-resource-usage.v1`, an opaque receipt ID, lowercase receipt SHA-256, exact run-attempt and systemd InvocationID binding, `outcome=completed`, admitted CPU threads, observed cgroup memory/PID peaks, null GPU index/UUID, zero admitted VRAM, no scheduler GPU assignment, `accelerator_applicability=not_applicable`, and `dorado_invoked=false`.

Retry3 uses only `historical_unavailable`. Reconciliation must not construct an accepted receipt.

### LIFE-10: Stage callback CAS

Stage-start and stage-terminal callbacks use snapshot CAS over active Job state and the lifecycle fields they can conflict with. A start callback cannot resurrect a completed or terminal-receipted stage. A lost race reloads current authority and either returns the exact idempotent state or fails with HTTP 409.

### LIFE-11: FASTQ retry normalization

FASTQ submission and resubmission both pass through the canonical `ont_fastq_qc` normalizer before resource estimation. All `dorado_*`, `gpu_*`, MSA, ANARCI, pinned-GPU, CUDA-visible-device, and per-GPU CPU fields are removed. The retry remains CPU-only with VRAM estimate zero.

## 9. Retry3 reconciliation requirements

### RECON-0: One managed entrypoint

The sole repair surface is `scripts/reconcile_ont_fastq_qc_job.py`, executed from the canonical deployed checkout as its managed Development service owner:

```text
python scripts/reconcile_ont_fastq_qc_job.py --job-id <uuid> --dry-run
python scripts/reconcile_ont_fastq_qc_job.py --job-id <uuid> --apply
```

It accepts no database path, result path, lane override, Project ID, actor, stage list, artifact list, or manifest path. Development configuration, database identity, result authority, Project hierarchy, OS actor, and authorization class are server-derived. Unsupported lane, service owner, or database identity fails before mutation. Exit `0` means valid dry-run, applied, or already-identical success; exit `2` means request/authority/integrity rejection; exit `3` means CAS conflict; exit `4` means backup or transaction failure.

### RECON-1: Preconditions

The repair path accepts one explicit Job ID. It requires a canonical completed FASTQ-QC Job, completed queue state, no awaiting input, no active workflow owner/unit, and exact contained result root.

### RECON-2: Full revalidation

Before a database write, it must execute the same manifest, artifact, source, reference, and terminal-stage validation used by future completion.

### RECON-3: Allowed mutation

It may update only:

- `completed_stages`;
- `stage_outputs`;
- a new additive `provenance.alignment_hierarchy_authority_v1` record, only when absent, using the exact server-derived hierarchy authority;
- a new additive `provenance.ont_fastq_qc_reconciliation_v1` audit receipt.

### RECON-4: Preserved history

It must preserve status, queue status, timestamps, params, result directory, scientific manifests, artifact bytes, existing provenance keys, runtime claims, scheduler history, retry history, and error fields.

### RECON-5: Audit receipt

The additive receipt validates against `schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json` and must include:

- schema/version;
- target Job ID;
- server-derived actor and authorization class;
- Project, Global Experiment, Domain Experiment, state revision, member receipt, sample revision, and reference revision identities;
- canonical Development lane and path-opaque database identity;
- verified backup identity, byte size, SHA-256, and integrity result;
- software commit/tree;
- normalized repair-request digest;
- preimage digests for `completed_stages`, `stage_outputs`, and the complete original `provenance` object;
- postimage digests for the new completed-stage list, new stage-output map, and receipt-free provenance;
- a protected-row preimage digest and receipt digest;
- sequence and verification manifest digests;
- exact reconciled stage list;
- artifact count and package identity summary;
- workflow/input/reference/source identity and `resource_evidence_status=historical_unavailable`;
- application timestamp;
- explicit `scientific_artifacts_modified: false`.

All authority digests use this helper:

`D(label, value) = SHA256(UTF8("bms.ont-fastq-qc-reconciliation.v1\0" + label + "\0") || RFC8785(value))`.

The normalized request is exactly `{"schema":"bms.ont-fastq-qc-reconciliation-request.v1","job_id":"<uuid>","operation":"apply"}`. The receipt stores `D("normalized-request", request)`.

The database identity preimage is exactly `{"schema":"bms.sqlite-database-identity.v1","lane":"development","logical_name":"biomodstack","service_unit":"biomodstack-api.service","resolved_path_sha256":"<sha256 of UTF-8 canonical absolute path>","device":<st_dev>,"inode":<st_ino>}`. Only `D("database-identity", value)` is persisted.

The result-root identity preimage is exactly `{"schema":"bms.result-root-identity.v1","job_id":"<uuid>","basename":"<single result-root basename>","resolved_path_sha256":"<sha256 of UTF-8 canonical absolute path>","device":<st_dev>,"inode":<st_ino>}`. The directory is opened with no-follow semantics and the stat values come from that open handle. Store `D("result-root-identity", value)`.

The protected-row preimage is exactly the RFC 8785 object `{"schema":"bms.ont-fastq-qc-protected-row.v1","job_id":...,"status":...,"queue_status":...,"awaiting_input":...,"paused":...,"completed_at":...,"params":...,"provenance":...,"completed_stages":...,"stage_outputs":...,"output_dir":...,"error_message":...}` using JSON values read by one `BEGIN IMMEDIATE` transaction. Store `D("protected-row-preimage", value)`.

The online backup uses SQLite's native backup API from a source connection opened read-only before `BEGIN IMMEDIATE`. Record the source tuple `{"schema":"bms.sqlite-backup-source-preimage.v1","database_identity_sha256":...,"source_size_bytes":...,"source_sha256":...,"page_size":...,"page_count":...,"schema_version":...,"data_version":...,"integrity_check":"ok","foreign_key_violations":0}`. `source_sha256` is over the exact source database file after WAL checkpoint authority proves no active writer and before backup starts. Store `D("backup-source-preimage", value)`. The backup receipt records a separate `integrity_check="ok"`, `foreign_key_violations=0`, exact byte size and SHA-256. `quick_check` is not an accepted substitute.

After backup and before mutation, the apply transaction re-reads the source file identity, page count, schema version, protected row, and database identity. It requires exact equality with the backed-up source preimage and CAS inputs. The transaction commits the four permitted fields only. Replay recomputes all digest domains from current bytes, verifies the backup object remains resolvable by its immutable `backup_id`, and requires the stored backup SHA-256 and integrity results to match that object.

Build the receipt-free provenance postimage by preserving every original key and value. Add `alignment_hierarchy_authority_v1` only when the key was absent. If the key exists, require exact canonical equality with the server-derived record. Store `D("receipt-free-provenance-postimage", receipt_free_provenance)` as `receipt_free_provenance_postimage_sha256`. The final provenance is this receipt-free object plus exactly one `ont_fastq_qc_reconciliation_v1` key. The receipt does not contain a digest of the final provenance object. Verification removes the receipt key, requires exact equality with the receipt-free postimage, and validates the receipt independently.

`receipt_sha256` is `D("receipt", receipt_without_receipt_sha256)`. No other field is omitted. The verified online-backup fields must be present before this digest is calculated. The timestamp is generated once as UTC RFC 3339 with `Z` and is retained on replay.

### RECON-6: CAS and idempotence

The write must use one atomic transaction and compare the exact row preimage, all three mutable-field preimages, hierarchy binding, backup-bound preimage, database identity, and terminal Job state. A lost race returns exit `3` without a partial write. A repeated invocation validates the closed receipt and hierarchy record, recomputes every digest, and returns byte-identical unchanged success while preserving the original timestamp and digest.

### RECON-7: Backup

The `--apply` entrypoint must create its own SQLite online backup of every affected Development database before mutation, run full `PRAGMA integrity_check` and `PRAGMA foreign_key_check` separately on source and backup, record both results, file size, and SHA-256, and keep the backup outside the live database path. It must bind the backup preimage and identity to the transaction CAS and additive audit receipt. Backup mismatch, concurrent change after backup, wrong lane/database, authorization failure, and partial failure must leave the live row unchanged.

## 10. Browser authorization and artifact delivery

### WEB-1: Job-scoped capability

Manifest, result, alignment-session, report, artifact, and read access remains job-scoped, hierarchy-bound, principal-authorized, and same-origin. A capability for retry3 must not authorize another Job or survive a Project/member-binding mismatch.

### WEB-2: Fresh-page recovery

A normally authenticated browser page with no prior capability cookie must recover through `POST /api/jobs/{job_id}/alignment-access/rotate`. The request has no body and uses same-origin credentials. Its 200 body validates against `schemas/ngs/ont_ngs_rotation_success_v1.schema.json` and contains the exact Job ID, `rotated=true`, `scheme=opaque_job_capability_v1`, a positive rotation count, and UTC expiry.

The capability is delivered only as `Set-Cookie: __Host-bms-ngs-<first16(SHA256(UTF8(job_id)))>=<opaque>; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=<1..1800>`. It has no Domain attribute. Local HTTP Development uses the same host-only name without the `__Host-` prefix and is accepted only on loopback. The opaque value never appears in JSON, URL, log, or browser-readable storage. Every protected fetch uses `credentials: same-origin`. A successful rotation replaces the exact Job cookie.

Job switch and page disposal call bodyless `DELETE /api/jobs/{job_id}/alignment-access` with same-origin credentials. It is idempotent for an absent cookie. A 200 body validates against `schemas/ngs/ont_ngs_capability_revocation_success_v1.schema.json` and contains `schema=bms.ngs.capability-revocation-success.v1`, exact Job ID, `revoked=true`, and the scheme. The response expires the exact cookie with its original attributes and `Max-Age=0`. It derives principal and exact Job scope on the server; a foreign principal or hierarchy returns the typed 403, and package/rotation authority conflict returns typed 409. OpenAPI maps 200/403/404/409. Client state discards the old Job generation regardless of an idempotent successful body. Rotation derives the principal, role, frozen Project/member hierarchy, and complete package authority on the server. The client may perform one protected-request retry after one shared rotation for concurrent callers.

Revocation evaluates the first matching row only:

| Priority | Principal | Cookie | Hierarchy | Package | CAS | Result | Expire |
|---:|---|---|---|---|---|---|---|
| 1 | foreign/unauthorized | any | any | any | any | 403 `NGS_PRINCIPAL_DENIED` | no |
| 2 | authorized | absent | valid | valid | n/a | 200 revoked | yes |
| 3 | authorized | malformed, stale, or wrong Job | any | any | n/a | 403 `NGS_CAPABILITY_DENIED` | yes |
| 4 | authorized | valid owned | drift | any | n/a | 403 `NGS_HIERARCHY_DENIED` | yes |
| 5 | authorized | valid owned | valid | drift | n/a | 409 `NGS_PACKAGE_INTEGRITY_CONFLICT` | yes |
| 6 | authorized | valid owned | valid | valid | won | 200 revoked | yes |
| 7 | authorized | valid owned | valid | valid | lost, reload absent/revoked | 200 revoked | yes |
| 8 | authorized | valid owned | valid | valid | lost, reload newer active cookie | 409 `NGS_CAPABILITY_ROTATION_CONFLICT` | no |

Exact capability-denial responses and rotation conflicts must be distinguished from network, server, parser, and scientific-integrity failures.

### WEB-3: Page lifetime

Recovery is bounded to page lifetime and exact Job. Job switches must not reuse stale source state, errors, results, viewer sessions, or pending asynchronous commits.

### WEB-4: Byte responses

Every governed artifact `GET` and `HEAD` uses strong ETag `"sha256:<lowercase-sha256>"`, `Accept-Ranges: bytes`, exact `Content-Type`, and `Content-Disposition`. BAM, BAI, FASTA, FAI, and local viewer tracks use `inline; filename="<ASCII kind>-<first12sha>.<closed-extension>"`. HTML, logs, manifests, tables, VCF, FASTQ, and every other download use `attachment` with the same filename rule. CR, LF, quote, slash, backslash, and non-ASCII input never enter the filename.

- A full GET returns 200, exact `Content-Length`, ETag, Accept-Ranges, Content-Type, and Content-Disposition. HEAD returns the same headers and no body.
- Only one `bytes=start-end`, `bytes=start-`, or `bytes=-suffixLength` range is accepted. Numbers are unsigned base-10 without sign. Multipart, whitespace, empty, reversed, overflow, and zero suffix ranges return 400 `NGS_RANGE_INVALID`.
- For a nonempty object, an end beyond size normalizes to `size-1`; a suffix length beyond size normalizes to the full object. A start equal to or above size is 416. A zero-byte object accepts no satisfiable range and returns 416 for any syntactically valid Range.
- A satisfiable range returns 206 with exact fragment bytes, `Content-Range: bytes <start>-<end>/<size>`, fragment Content-Length, and the same ETag, Accept-Ranges, Content-Type, and Content-Disposition.
- An unsatisfiable range returns 416 `NGS_RANGE_UNSATISFIABLE`, `Content-Range: bytes */<size>`, and the same ETag and Accept-Ranges.
- `If-None-Match` equal to the strong ETag on a non-Range GET or HEAD returns 304 with ETag, Accept-Ranges, Content-Type, and Content-Disposition and no Content-Length or body.
- A missing `If-Range` honors a valid Range. A present `If-Range` equal to the strong ETag also honors Range. Only a present unequal value ignores Range and returns the full 200 representation. Date validators are unsupported and are treated as unequal.
- `If-None-Match` is evaluated before Range and returns 304 on a match. A malformed Range is evaluated before a present unequal `If-Range` and returns 400. HEAD never returns 206 or a body; it ignores Range and returns the same 200 or 304 metadata as the corresponding full GET.
- Error responses never carry artifact bytes. Digest, size, inode, or regular-file drift returns 409 `NGS_ARTIFACT_INTEGRITY_CONFLICT` before response commitment.

### WEB-5: Generic-route isolation

Generic browse, download, static, upload, compatibility, and parent-listing routes must not expose or overwrite governed result trees. Dedicated governed routes must win over overlapping dynamic routes.

### WEB-6: Typed failure contract

All governed result, session, artifact, Range, read, and rotation failures use `schemas/ngs/ont_ngs_error_v1.schema.json`. The response contains only `schema=bms.ngs.error.v1`, a closed machine code, a bounded public message, the target Job ID, a resource enum, and `retryable`. It contains no path, exception text, SQL, token, cookie, principal, or secret.

| HTTP | Code | Required use |
|---:|---|---|
| 403 | `NGS_CAPABILITY_DENIED` | Missing, invalid, or wrong-Job capability |
| 403 | `NGS_HIERARCHY_DENIED` | Frozen hierarchy or capability-binding mismatch |
| 403 | `NGS_PRINCIPAL_DENIED` | Authenticated principal lacks Project/operator authority |
| 403 | `NGS_ROTATION_ORIGIN_DENIED` | Rotation is outside same-origin local Development policy |
| 404 | `NGS_RESOURCE_NOT_FOUND` | Governed Job, session, artifact, or read identity is absent |
| 409 | `NGS_AUTHORITY_CONFLICT` | Canonical workflow/input/reference authority conflicts |
| 409 | `NGS_PACKAGE_INTEGRITY_CONFLICT` | Current package differs from persisted authority |
| 409 | `NGS_CAPABILITY_ROTATION_CONFLICT` | Rotation CAS loses to another writer |
| 409 | `NGS_ROTATION_INELIGIBLE` | Target is not a completed eligible nanopore Job |
| 409 | `NGS_ARTIFACT_INTEGRITY_CONFLICT` | Snapshot size or digest differs before serving |
| 409 | `NGS_READ_SCAN_TRUNCATED` | Bounded exact-read scan cannot prove absence |
| 400 | `NGS_RANGE_INVALID` | Range syntax is malformed or multipart |
| 416 | `NGS_RANGE_UNSATISFIABLE` | Range is outside the immutable object; return `Content-Range: bytes */<size>` |

`retryable` is fixed by code: only `NGS_CAPABILITY_DENIED` and `NGS_CAPABILITY_ROTATION_CONFLICT` are true. Every other v1 code is false.

OpenAPI uses components `OntFastqQcResultV1`, `OntAlignmentSessionListV1`, `OntAlignmentSessionDetailV1`, `OntNgsRotationSuccessV1`, `OntNgsCapabilityRevocationSuccessV1`, `OntNgsErrorV1`, and `BinaryArtifactResponse`. The result route maps 200/403/404/409. Session list and detail map 200/403/404/409. Rotation and revocation each map 200/403/404/409. Reads map 200/400/403/404/409. Artifact GET/HEAD maps 200/206/304/400/403/404/409/416. Every non-2xx JSON response references `OntNgsErrorV1`; 206 and 416 publish exact `Content-Range` headers. Contract tests compare these exact component IDs and status maps for every governed route.

## 11. Scientific result UI

### UI-1: Result-first hierarchy

The first result viewport for canonical FASTQ-QC must show:

- run name and exact Job ID;
- workflow and input mode;
- execution state;
- scientific verdict `REVIEW REQUIRED`;
- producer reason codes;
- `Total reads` as an integer count and `Mapped reads` as count over total;
- `Total bases` in bp;
- `Reference coverage` as percent bases with at least one base-covering alignment record, displaying retry3 `100.00%` from source fraction `1.0`;
- `Decision minimum support depth` as alignment observations from `per_base_support.tsv`, displaying 49,126 and stating that deletion-spanning observations participate;
- `Coverage-envelope minimum` as base-covering alignment records from `samtools depth -aa`, displaying 24,840 at position 3516 and stating that deletion bases are excluded;
- `Consensus identity` as a percent, displaying retry3 `99.9820%` from source fraction `0.9998204667863555`;
- a clear action to open the local genomic viewer.

Configuration, raw params, server paths, technical manifests, and historical runtime details belong in collapsed audit sections.

### UI-2: Decision checks

Render the producer-owned expected-reference mapping screen, coverage, read-support, sequence-identity, and topology checks with status, purpose, decisive metrics, units, and reason codes. The expected-reference screen must visibly state `expected_reference_mapping_only` and `organism_identity_claimed: false`; a PASS must not be described as taxonomic contamination exclusion, organism identity, sample purity, or absence of off-target sequence. Coverage/read-support depth is labeled as alignment observations. Zero-valued diagnostics may remain collapsed when the check passes.

### UI-3: Scientific visuals

Render two bounded, responsive visuals from backend-owned data:

1. **Read-length distribution.** Purpose: show read-length shape against the 5,570 bp reference length. Axes: read length in base pairs and read count. This is a server-derived visualization over the producer’s 61,708 per-read rows. `fixed_width_v1` always emits 50 half-open bins starting at zero; `width = max(1, ceil((max_length + 1) / 50))`; assignment is `min(floor(length / width), 49)`; bin counts must sum exactly to `source_row_count`. The browser must not rebin. A 5,570 bp reference marker is a labeled derived overlay. Retry3’s producer `expected_plasmid_size=7000` must be displayed beside `reference_length=5570`; any 7,000-based copy-number/dimer/trimer metrics are labeled historical or omitted from the decision interpretation.
2. **Coverage across the construct.** Purpose: identify low aligned-base coverage and the context of review loci. Axes: 1-based eGFP coordinate and base-covering alignment records. Label the series as `minmax_envelope_v1` and `samtools_depth_aa_default_filters_excludes_deletions_v1`. Show its global minimum of 24,840 at 3516. Show the separate decision-check minimum support depth of 49,126 nearby with its per-base-support basis. Do not hardcode or display a support-minimum coordinate unless a future versioned wire field owns it. Do not imply that the values are interchangeable or that either is statistical uncertainty.

No chart may fabricate confidence intervals, thresholds, or scientific status.

### UI-4: Variant table

Display normalized variants with distinct VCF record start, end, REF, ALT, kind, affected interval kind/start/end, support, depth, and producer `support_status`. Retry3 has no producer-owned row-level reason. The table must not assign aggregate verification reason codes to an individual variant. Show aggregate `MIXED_ALLELES_DETECTED` and `VARIANT_SUPPORT_AMBIGUOUS` in the decision-check area. `SNV` and `MNV` affect their record reference bases. A left-anchored `DEL` affects the suffix of REF after the retained ALT prefix. A left-anchored `INS` is a `between_bases` interval after the retained REF span. `COMPLEX` affects its record reference span. Every record and affected interval must stay within the linearized 1..reference-length coordinate space; a circular event ID may group records but cannot authorize a wrapped interval. For retry3, position 3515 is the retained left anchor and base 3516 is the deleted affected interval. A row action must open IGV at a bounded range containing the affected interval without labeling the anchor as the deleted base.

### UI-5: Governed downloads

Group descriptors in ascending `display_order`. Each descriptor carries closed `scientific_role`, `display_order`, and `content_disposition` fields. Unavailable descriptors remain in their assigned position and show their reason without a link. The authoritative retry3 projection is:

| Order | Source | Kind | Scientific role | Media type | Disposition |
|---:|---|---|---|---|---|
| 1 | `sequence_qc` | `sequence_qc_manifest` | `authority` | `application/json` | `attachment` |
| 2 | `sequence_qc` | `reference` | `reference` | `application/octet-stream` | `inline` |
| 3 | `sequence_qc` | `modified_bases` | `optional_evidence` | `null` | `none` |
| 4 | `sequence_qc` | `reference_index` | `reference` | `application/octet-stream` | `inline` |
| 5 | `sequence_qc` | `summary` | `qc_metrics` | `text/tab-separated-values` | `attachment` |
| 6 | `sequence_qc` | `read_lengths` | `qc_metrics` | `text/tab-separated-values` | `attachment` |
| 7 | `sequence_qc` | `alignment_stats` | `qc_metrics` | `text/tab-separated-values` | `attachment` |
| 8 | `sequence_qc` | `coverage` | `qc_metrics` | `text/tab-separated-values` | `attachment` |
| 9 | `sequence_qc` | `per_base_support` | `qc_metrics` | `text/tab-separated-values` | `attachment` |
| 10 | `sequence_qc` | `consensus` | `consensus` | `application/octet-stream` | `attachment` |
| 11 | `sequence_qc` | `consensus_index` | `consensus` | `application/octet-stream` | `attachment` |
| 12 | `sequence_qc` | `consensus_log` | `audit_log` | `application/octet-stream` | `attachment` |
| 13 | `sequence_qc` | `alignment_bam` | `alignment` | `application/octet-stream` | `inline` |
| 14 | `sequence_qc` | `alignment_bai` | `alignment` | `application/octet-stream` | `inline` |
| 15 | `sequence_qc` | `igv_coverage_depth` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 16 | `sequence_qc` | `igv_position_gradient` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 17 | `sequence_qc` | `igv_gc_content` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 18 | `sequence_qc` | `igv_gc_zscore` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 19 | `sequence_qc` | `igv_split_read_density` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 20 | `sequence_qc` | `igv_softclip_density` | `viewer_auxiliary` | `application/octet-stream` | `inline` |
| 21 | `sequence_qc` | `igv_junction_hotspots` | `viewer_auxiliary` | `application/vnd.realvnc.bed` | `inline` |
| 22 | `sequence_qc` | `igv_report_sites_bed` | `viewer_auxiliary` | `application/vnd.realvnc.bed` | `inline` |
| 23 | `sequence_qc` | `igv_report_sites_tsv` | `viewer_auxiliary` | `text/tab-separated-values` | `inline` |
| 24 | `sequence_qc` | `igv_track_config` | `viewer_auxiliary` | `application/json` | `inline` |
| 25 | `sequence_qc` | `igv_report` | `report` | `text/html` | `attachment` |
| 26 | `sequence_qc` | `log` | `audit_log` | `application/octet-stream` | `attachment` |
| 27 | `sequence_qc` | `log` | `audit_log` | `application/octet-stream` | `attachment` |
| 28 | `construct_verification` | `construct_verification_manifest` | `authority` | `application/json` | `attachment` |
| 29 | `construct_verification` | `verification_summary` | `verification` | `text/tab-separated-values` | `attachment` |
| 30 | `construct_verification` | `normalized_variants` | `verification` | `text/x-vcf` | `attachment` |
| 31 | `construct_verification` | `per_base_metrics` | `verification` | `text/tab-separated-values` | `attachment` |
| 32 | `construct_verification` | `human_evidence_report` | `report` | `text/html` | `attachment` |
| 33 | `construct_verification` | `observed_consensus` | `consensus` | `application/octet-stream` | `attachment` |
| 34 | `construct_verification_input` | `source_read_provenance` | `source_input` | `application/json` | `attachment` |
| 35 | `construct_verification_input` | `source_reads_fastq` | `source_input` | `application/octet-stream` | `attachment` |
| 36 | `input_mode` | `signal_data` | `optional_evidence` | `null` | `none` |

The result schema closes every enum and requires unique contiguous `display_order` values 1 through 36. Repeated `kind=log` rows remain distinct by order and digest. Frontend grouping preserves first role occurrence and ascending display order within each role. Present artifacts show kind, size, abbreviated digest, and source. The package-authority digest excludes these presentation fields as defined by AUTH-8.

The exact filename-extension map is: `json` for both manifests, track config, and source-read provenance; `fasta` for reference, consensus, and observed consensus; `fai` for reference and consensus indexes; `tsv` for summary, read lengths, alignment stats, coverage, per-base support, report-sites TSV, verification summary, and per-base metrics; `log` for consensus log and both log rows; `bam`; `bai`; `bedgraph` for coverage depth, position gradient, GC content, GC z-score, split-read density, and soft-clip density; `bed` for junction hotspots and report-sites BED; `html` for both reports; `vcf` for normalized variants; and `fastq.gz` for source reads. Modified bases and signal data are unavailable and use null extension.

Artifact state is discriminated. `present` requires nonnull lowercase SHA-256, nonnegative size, exact governed URL, the table media type, Range capability, and `unavailable_reason=null`. Every unavailable state requires null digest, size, URL, media type, `content_disposition=none`, and a bounded producer reason. A completed accepted result cannot contain `missing_required`.

### UI-6: Progressive disclosure

Sequence-QC manifest details, construct-verification manifest details, alignment-session receipts, stage receipts, technical provenance, logs, and historical resource fields remain available under labeled disclosures.

### UI-7: No duplicate false states

Canonical FASTQ-QC must bypass the legacy multimer empty state and generic design count. It must not display both a valid result and `No FASTQ QC outputs available`, `design_count: 0`, or all-stages-complete inference from Job status alone.

### UI-8: Strict runtime parser

The TypeScript parser must reject unknown or missing versioned fields, malformed hashes/IDs/URLs, non-finite numbers, incoherent present/unavailable artifact states, bad stage sets, invalid histogram/coverage ordering, and foreign Job identity.

It must preserve producer array order and scientific values.

### UI-9: Async source ownership

Every result, authorization, viewer, and error commit must bind to the current Job generation and mounted state. A late response for Job A must not change Job B’s visible data.

### UI-10: Failure quality

Integrity, authorization, parser, network, and viewer errors must be visible and specific. Valid scientific summaries and downloads must remain visible if the optional WebGL viewer fails.

## 12. Compact local IGV

### VIEW-1: Bundled library

The accepted viewer uses the frontend-bundled IGV.js package. It must set `loadDefaultGenomes: false` and `search: false`.

### VIEW-2: Zero external dependencies

Opening retry3 and navigating a range must issue no request to `igv.org`, `raw.githubusercontent.com`, `cdn.jsdelivr.net`, or any other external genome, locus, script, style, worker, or data service.

### VIEW-3: Exact custom reference

The viewer must load the governed retry3 FASTA/FAI and bind contig `eGFP_plasmid`, length 5,570, circular topology, canonical FASTA digest, FAI digest, and normalized sequence digest through `bms.ngs.alignment-session.v1`.

### VIEW-4: Exact alignment

The retry3 primary session ID is `de4e4d2b2062fbf21ddfee65`. It is computed as the first 24 lowercase hexadecimal characters of `SHA256(UTF8(job_id + "\0" + mode + "\0" + join("\0", artifact_id for roles sorted by unsigned UTF-8 role name)))`. The exact sorted denominator is `alignment=0fe950758c4b3f1bb04700d4f80a831bae6c4fb2c0903569cb73f7657671bdad`, `alignment_index=a9cf4ab96491b5ac4b1f627080aa29e65700dc2f15717d8a789cff7c48aa052e`, `coverage_depth=91967113f82d5f787815e6f4ee3cec159951fe7fc56537f0cf25f67850e97f07`, `gc_content=2a2366e9b569a770abf8db8853441de703545ea9fb892c81a05edfbf32be07b5`, `gc_zscore=84a81dbc968d29430610ac79aa9a99e246cb4a8c404e47b75e1a518110f4f1b8`, `junction_hotspots=d3154cac432f8a6a38b9742c58b9677545101f52004ed837f0636182b9c78a20`, `position_gradient=c7263246bc9a3ae4f3e3ac09c5061b6424ed27afb0f9cac272ed5386b8350137`, `reference=22a112b444bf00c5971df02f7428df41cb12da19915235baa20a7c764f852f4c`, `reference_index=b0f82c25c8d1315c1d74e6cd96b99b067ab2f1a56673a410644d96ea998ebd79`, `report=299233faddbc71977de67f3c64892fd1fd36f903ba80b1b5fce5debd4253adba`, `soft_clip_density=18c9e0ea329446b651fecdd8bbfb26a6661186ac69956482a5b7f4f3f14a3268`, `split_read_density=39d7e17323f51f9969d6c8bbcdf69bfa0aa231bb69d8125e709da35c1513ad0f`, and `track_config=fae8378b10c283ca281531b72a79924cc32308f69e633a919fb7ca5631d65eab`. The result summary, list envelope, detail envelope, route parameter, and `reads_url` query use this same value.

The primary session loads the governed BAM/BAI pair. For every Job, `alignment_pair_sha256` is recomputed from that session's own BAM/BAI file digests with `SHA256(UTF8("bms.ngs.alignment-pair.v1\0") || RFC8785({"alignment_sha256":...,"alignment_index_sha256":...}))`. Retry3's value is `cd290f7f431b9f1cae9040ce12a7e1a300c637bedc5a469d43cc66cce9c6b58e`. Per-file artifact IDs remain route identities and do not equal file SHA-256 values. The alignment artifact ID is `0fe950758c4b3f1bb04700d4f80a831bae6c4fb2c0903569cb73f7657671bdad`; the alignment-index artifact ID is `a9cf4ab96491b5ac4b1f627080aa29e65700dc2f15717d8a789cff7c48aa052e`. The value `299233faddbc71977de67f3c64892fd1fd36f903ba80b1b5fce5debd4253adba` identifies the download-only HTML report artifact and has no alignment authority.

The session also binds exact BAM and BAI sizes, both manifest digests, the package digest, and exact reads URL.

`GET /api/jobs/{job_id}/alignment-sessions` and `GET /api/jobs/{job_id}/alignment-sessions/{session_id}` publish the closed list and detail branches from `schemas/ngs/ont_alignment_session_v1.schema.json`. One ready primary session is required. A ready branch has `unavailable_reason=null` and complete reference, index, alignment-pair, manifest, package, and URL authority. An unavailable branch has a bounded nonempty reason and null viewer resource fields. The list contains one primary session and at most one `dimer_candidates` session in canonical order. The exact Job ID appears in every governed URL. Session identity, artifact route identity, file digests, sizes, role keys, and `reads_url?session_id=<exact-session>` must agree.

### VIEW-5: Compact default

Opening IGV must create a bounded modal or pane that leaves application context visible. It must not request browser fullscreen automatically. Fullscreen may be an explicit operator action.

### VIEW-6: Inspector control

The existing raw-read inspector is collapsed by default, opens only after explicit operator action, and must not permanently cover the alignment canvas. Its current behavior is informational and nonblocking. New exact-read identity, locus-list, and detail protocols remain outside this closure.

### VIEW-7: Range control

Provide a visible Range control that accepts exact `contig:start-end` coordinates. It must validate the canonical contig and 1-based inclusive bounds locally from backend authority, reject invalid input without web lookup, and navigate the loaded browser.

### VIEW-8: Required navigation proof

Acceptance must navigate to `eGFP_plasmid:3400-3600`, then to the VCF record anchored at 3515 with affected base 3516. The displayed locus must retain exact contig case and coordinates.

### VIEW-9: Range-backed bulk loading

IGV must use governed byte Range requests for BAM and reference access. It must not download the complete 305,396,924-byte BAM during initial open. Initial cumulative BAM response bytes must stay below 64 MiB.

### VIEW-10: Track readiness

The alignment track and required reference must reach a visible ready state. `Loading tracks…` is terminally unacceptable. Reference FASTA/FAI or primary BAM/BAI failure makes the session unavailable. Optional auxiliary tracks are loaded independently and may fail with a specific message without removing the required ready alignment track.

### VIEW-11: Download-only HTML report

`igv_report.html` may be downloaded with attachment policy and exact digest. Its external script dependency prevents it from satisfying VIEW-1 through VIEW-10.

## 13. Required tests

Tests must be added before implementation fixes for each uncovered behavior.

### 13.1 Backend focused tests

- exact ONT applicability and FASTQ-only resource normalization;
- absolute and canonical relative stage-output acceptance;
- traversal, wrong root, symlink, missing output, and failed-stage rejection;
- stage callback copy-on-write, identical replay, contradictory replay, concurrent publication, cancellation, awaiting-input, and stale-owner loss;
- future finalizer manifest/source/reference/package validation and one terminal owner;
- full-package authority equality and mismatch across fresh completion, retry3 receipt, and reopen;
- canonical FASTQ manifest selection that rejects the result-root fallback;
- fresh producer-resource receipt before-success publication and retry3 historical-unavailable handling;
- CPU-only FASTQ resubmit normalization before VRAM estimation;
- canonical FASTQ-QC generic-ingestion bypass;
- exact Project/Global/Domain/state/member/sample/reference/Job hierarchy positive binding and every foreign-binding denial;
- principal/role authorization, guessed Job ID, stale hierarchy capability, and capability-rotation negatives;
- retry3-compatible result projection with incomplete historical stage mirrors;
- normative JSON Schema validation against the shared valid fixture and adversarial invariant fixtures;
- construction-validator parity for complete producer rows and wire-validator parity for every bounded `bms.ngs.fastq-qc-result-wire-validator.v1` rule;
- OpenAPI response-model equality with the normative closed result contract;
- closed alignment-session, governed-error, reconciliation-receipt, and final-acceptance-receipt schema tests;
- exact sequence versus verification manifest selection;
- malformed, foreign, oversized, duplicate, non-finite, and digest-drift result rejection;
- bounded response size, coverage envelope, artifact count, and session count;
- exact envelope depth basis, global-minimum preservation, bucket width, ties, endpoint policy, and deletion-context distinction from per-base support depth;
- closed decision-check purpose/metric/unit contracts, expected-reference-screen overclaim rejection, and closed threshold-profile values;
- kind-specific SNV/MNV/INS/DEL/COMPLEX interval normalization, retry3’s 3515 anchor/3516 affected base, and cross-origin rejection;
- reconciliation dry run, apply, conflict, idempotence, preservation, and additive receipt;
- reconciliation digest-domain, self-digest omission, receipt-free provenance, timestamp, database-identity, hierarchy, and malformed-replay negatives;
- reconciliation wrong-owner, wrong-lane/database, backup mismatch, concurrent change, partial failure, and exit-code behavior;
- full, Range, conditional, attachment, cross-job, generic-route, and replacement-race delivery.

### 13.2 Frontend tests

- strict result parser positive fixture and one RED fixture per closed invariant;
- strict alignment-session and typed-error parsers with exact-key, state-branch, URL, ID, digest, size, manifest, and Job/session binding negatives;
- mounted result-first panel with `REVIEW REQUIRED`, exact metrics, checks, variant, plots, and downloads;
- no legacy empty/design state for canonical FASTQ-QC;
- one shared authorization rotation and one protected retry;
- rotation conflict and non-auth failure behavior;
- Job A to Job B newest-source ownership with transport that ignores abort;
- IGV config has `loadDefaultGenomes: false` and `search: false`;
- compact modal does not request fullscreen on open;
- raw-read inspector starts collapsed;
- valid and invalid Range control behavior;
- variant action emits exact bounded locus;
- viewer failure leaves scientific result and downloads visible.

Required mounted files include `tests/vitest/ontFastqQcResultPanel.test.tsx` and `tests/vitest/ngsResultRoutingMounted.test.tsx`. Every new mounted file must be registered in `vitest.md.config.ts` or the closed allowlist must be replaced by an intentional governed glob. Required Node files include `tests/ontFastqQcResult.test.ts`, `tests/ngsAlignmentSession.test.ts`, and `tests/ngsAlignmentViewer.test.ts`.

### 13.3 Verification matrix

Run on the exact reconciled candidate:

- focused API NGS/result/lifecycle/artifact tests;
- normal collectors for every changed backend test module;
- frontend NGS Node tests;
- mounted routing/component tests;
- frontend production TypeScript build;
- frontend production Vite build;
- Nextflow workflow/config parse checks for changed workflow/resource files;
- `git diff --check` and frozen-lockfile verification;
- exact candidate-versus-current-parent differential for inherited failures.

Collection evidence must name every required file above, report a nonzero test count for each owning command, and show the specific RED-to-GREEN behavior names. A package-level zero exit does not count if an expected file or test was not collected.

Unrelated baseline failures must be reported separately and may not hide a candidate-only failure.

## 14. Integration and Development release

### REL-1: Candidate seal

Stage only specification-owned paths. Record branch, commit, tree, status, changed-path manifest, and lockfile hashes.

### REL-2: Current parent

Fetch `origin/test` immediately before reconciliation. Merge it into the sealed feature branch without force or unrelated source loss.

### REL-3: Conflict resolution

Resolve overlapping files by preserving both current upstream behavior and this specification. Regenerate dependency locks only from merged manifests when a lock conflict exists.

### REL-4: Exact-tree review

Run independent backend/security and frontend/product reviews against the exact post-merge tree. Every accepted finding must name that tree. Fixes invalidate earlier exact-tree PASS results.

### REL-5: Deployment fence and pre-push safety

Before any push to `test`, pause the canonical Development sync owner through `scripts/biomodstack_dev_sync.py --pause-deploy` and verify a durable paused receipt. Preserve a pre-existing pause as pre-existing operator state.

While the fence is active, require:

- verified online backups for affected Development databases;
- zero running Jobs;
- zero actively queued Jobs;
- zero live workflow transient units;
- preserved paused and awaiting-input rows;
- no Production action.

A failure before push restores the prior sync state only when this operation created the pause and `origin/test` remains at the previously accepted revision. A failure after push keeps automatic deployment fenced, records the exact failure and target revision, and requires repair or an explicit supported rollback before resume. The fence may not be left without a named owner and receipt.

### REL-6: Guarded push

Fetch again after final review. Require the current `origin/test` to equal the recorded merge parent. Push feature and `HEAD:refs/heads/test` without force while the deployment fence is active. Fetch and require local HEAD, `origin/test`, and `git ls-remote` to match. Prove the pushed revision has not deployed yet.

### REL-7: Canonical deployment

Resume only through the supported managed Development sync owner. Deploy the exact fenced revision and prove its source/process/listener/database identity. Do not launch an alternate API or frontend from the feature worktree.

### REL-8: Runtime identity

Prove canonical checkout SHA/tree, managed service names, process PIDs/start times/owners, API and frontend listeners, environment lane, Development database paths, and migration versions.

### REL-9: Retry3 repair

After deployed source identity is proven, run reconciliation dry-run, inspect exact proposed JSON changes, apply once, and read back the row through both SQLite and live API.

### REL-10: Drift rule

Any source, remote, service, database, artifact, or browser-build drift during acceptance invalidates affected evidence. Re-establish the exact state before continuing.

### REL-11: Final acceptance receipt

After A1 through A17 pass, emit one `bms.ont-fastq-qc-final-acceptance.v1` receipt that validates against `schemas/ngs/ont_fastq_qc_final_acceptance_receipt_v1.schema.json`. It binds:

- exact hashes for the SOW, review ledger, result schema, reconciliation schema, alignment-session schema, error schema, rotation-success schema, capability-revocation schema, evidence-bundle schema, gate-evidence-body schema, browser-evidence schema, independent-review schema, deployment-receipt schema, final-receipt schema, and retry3 fixture;
- accepted commit/tree, remote `test`, canonical Development checkout, served frontend build, API process, listeners, database identity, and migration identity;
- exact independent review identities and PASS verdict digests;
- hierarchy, reconciliation receipt, package authority, source FASTQ, reference, and both manifest digests;
- one ordered PASS row for each A1 through A17 with an immutable evidence-bundle ID and SHA-256;
- browser screenshots, console/network audit, Range evidence, and normal-reopen evidence digests;
- `no_fifth_compute_job=true`, `scientific_artifacts_modified=false`, and `production_action=false`.

Every A1 through A17 body is embedded as a `bms.ont-fastq-qc-evidence-bundle.v1` object in canonical gate order. `bundle_sha256` is SHA-256 over UTF-8 RFC 8785 of the complete bundle with only `bundle_sha256` omitted. Files are resolved from the immutable release evidence directory by exact bundle ID, filename, size, and digest. The ordered browser manifest and all three exact-scope independent review receipts are embedded and self-digested by the same omit-only-own-digest rule.

The gate-specific assertion and required-file denominator is: A1 `A1_PACKAGE_AUTHORITY_CLOSED` with `package_manifest`, `artifact_inventory`; A2 `A2_LIFECYCLE_MIRRORS_CLOSED` with `lifecycle_db`, `reconciliation_receipt`; A3 `A3_RESULT_ENDPOINT_CLOSED` with `result_response`; A4 `A4_BROWSER_RECOVERY_CLOSED` with `rotation_trace`; A5 `A5_HIERARCHY_AUTHORIZATION_CLOSED` with `authorization_matrix`; A6 `A6_FULL_DOWNLOAD_CLOSED` with `full_download`; A7 `A7_RANGE_CLOSED` with `range_matrix`; A8 `A8_FIRST_VIEWPORT_CLOSED` with `first_viewport`; A9 `A9_DECISION_REPORT_CLOSED` with `decision_report`; A10 `A10_LOCAL_VIEWER_OPEN` with `igv_open`; A11 `A11_RANGE_NAVIGATION_CLOSED` with `navigation`; A12 `A12_BULK_DATA_BEHAVIOR_CLOSED` with `transfer_audit`; A13 `A13_FAILURE_ISOLATION_CLOSED` with `failure_isolation`; A14 `A14_CONSOLE_NETWORK_CLOSED` with `console_network`; A15 `A15_NORMAL_REOPEN_CLOSED` with `normal_reopen`; A16 `A16_RUNTIME_RELEASE_CLOSED` with `runtime_identity`; and A17 `A17_ISOLATION_CLOSED` with `isolation_audit`. The bundle verifier requires each named file exactly once and no unlisted file. It validates each file's typed content against the corresponding A-row evidence requirements.

`ont_fastq_qc_gate_evidence_body_v1.schema.json` is the content schema for those JSON files. Each body retains the exact source response or trace filename and digest plus gate-specific rows. A5 contains the owner/operator positives and every named foreign-binding denial. A8 retains every required label. A9 retains all five check IDs, both plot IDs, variant anchor/affected-base semantics, downloads, and disclosures. A11 retains separate range and variant screenshots. A13 retains the failure-injection trace and three visibility observations. A14 retains uncaught, integrity-denial, hanging-request, external-request, and disclosure counts. A15 retains the ten ordered ordinary-navigation/reopen/foreign-Job steps and the foreign outcome. Summary booleans or counts without these retained rows are invalid.

A1 embeds the exact 36-row scientific package manifest and artifact inventory with canonical byte sizes and SHA-256 values. A2 embeds the complete reconciliation receipt and binds its canonical size/digest. A3 embeds the complete frozen `bms.ngs.fastq-qc-result.v1` retry3 fixture. Its body digest and byte count are recomputed over UTF-8 RFC 8785 bytes. The outer A3 Job, embedded response Job, all evidence-bundle Jobs, browser Job, and final scientific Job equal `31f02bd5-830f-4558-aa78-3873c515de68`. A4 retains `rotation_trace.json`, its size, SHA-256, `bms.ont-fastq-qc-rotation-trace.v1`, and seven exact events in order: initial capability denial, one successful rotation, one protected result retry, sessions list load, primary-session load, artifact HEAD load, and capability revocation. A5 retains exactly 26 rows: owner/operator read success, 12 named read denials, and the same 12 rotation denials. Counts are 2 positive and 24 negative. A6 fixes the retry3 `alignment_bam` download to opaque artifact ID `0fe950758c4b3f1bb04700d4f80a831bae6c4fb2c0903569cb73f7657671bdad`, Job `31f02bd5-830f-4558-aa78-3873c515de68`, 305,396,924 bytes, file SHA-256 `c14a54c6152b72789a6932a8b2e70adc35188835d5df1970042af10b54183971`, strong ETag `"sha256:c14a54c6152b72789a6932a8b2e70adc35188835d5df1970042af10b54183971"`, `Accept-Ranges: bytes`, `application/octet-stream`, and `inline; filename="alignment_bam-c14a54c6152b.bam"`. Route ID, file digest, body, ETag, Content-Length, and package authority must agree with their distinct roles.

A2 also embeds exact canonical DB and API lifecycle readbacks. Each contains the four complete stage rows and the exact 25 ordered output suffixes. The verifier requires DB, API, and reconciliation receipt equality. A12 embeds the closed transfer/network trace. The verifier hashes its retained canonical bytes, validates every completed request row, recomputes initial BAM transfer bytes, rejects a full BAM transfer or hanging request, and requires viewer readiness. A17 embeds pre/post four-Job inventories, pre/post 25-file retry3 artifact snapshots, a zero-action Production audit, and pre/post unrelated-worktree manifests. The verifier requires equal pre/post Job IDs, artifact rows, and unrelated-path manifests before accepting no fifth compute, no scientific mutation, no Production action, and no unrelated worktree change.

The browser manifest has exactly five screenshot rows in order: 1 `first_viewport`, 2 `decision_report`, 3 `range_3400_3600`, 4 `variant_3515_3516`, and 5 `compact_igv`. Every row and A15 bind the same ordinary route `/projects/4af72c1d-27d8-4e14-8f39-4259a80494a0/experiments/9a10c5a8-b233-4bf3-af14-9c2880525278/domains/916a611b-6879-486f-bf9e-e1b5a796e01c`. Duplicate order or view, gaps, omissions, extra rows, diagnostic routes, and quick-viewer routes are invalid.

Each screenshot row binds immutable filename, byte size, `image/png`, SHA-256, pixel dimensions, exact route, loopback Development origin, CSS viewport, device scale, UTC capture time, and accepted frontend-build SHA-256. The verifier resolves the filename only below the immutable evidence root and hashes the bytes before accepting view semantics.

The A7 range matrix includes these exact retry3 vectors: BAM `bytes=0-1023` returns 206, `Content-Range: bytes 0-1023/305396924`, 1,024 bytes, fragment SHA-256 `3f5cec6120ee6e15883ff1b40dbf4f959a2e864c6c20c41cb85b075519c97f6d`; FASTA `bytes=0-255` returns 206, `Content-Range: bytes 0-255/5654`, 256 bytes, fragment SHA-256 `746ab93c063e8a9f7780352e555bbdb169d5075af264e55a5fb01a241300296e`; VCF `bytes=0-127` returns 206, `Content-Range: bytes 0-127/423`, 128 bytes, fragment SHA-256 `414536db288b87df3b508e2a407ae0a5bb14ca4194458c470a6218b005704c71`. It also records one end-beyond-size normalization, one suffix-beyond-size normalization, 416 at start=size, invalid multipart 400, absent/matching/mismatching If-Range, If-None-Match precedence, and HEAD behavior.

The A7 body fixes `vector_count=3`. Its nine positional boundary rows retain exact method, request headers, status, typed code, Content-Range, Content-Length, ETag, disposition, and body presence for end normalization, suffix normalization, 416, malformed multipart 400, absent/matching/mismatching If-Range, If-None-Match 304, and HEAD. `matrix_sha256` is `8183cd3e271f670cb354771fc20024a9bd2e9e705169e266b4aadcfcf68938cf`, computed as SHA-256 over UTF-8 RFC 8785 of `{"schema":"bms.ont-fastq-qc-range-matrix.v1","vectors":<exact-three-vectors>,"boundary_rows":<exact-nine-rows>}`.

The deployment receipt embeds owner-issued typed identities. Each nested identity stores the schema ID, source handle, canonical preimage digest, and native receipt digest. Canonical extraction preimages are:

- fence: pause owner, prior pause state, pause receipt ID, creation time, target commit/tree, and resume disposition;
- quiescence: one transaction timestamp plus exact running, queued, transient-unit, paused, and awaiting-input row identities and counts;
- backup: database logical name, device/inode, online-backup ID, source/backup integrity results, exact size, and SHA-256;
- process: systemd unit, InvocationID, PID, UID, start monotonic time, executable SHA-256, source commit/tree, and lane;
- listener: protocol, numeric address, port, owning PID, socket inode, and service unit;
- database: the RECON-5 database-identity preimage plus migration identity;
- migration: ordered migration IDs and SHA-256 values as RFC 8785 array;
- frontend: build manifest with sorted relative asset path, size, SHA-256, source commit, and source tree.

Every nested `canonical_preimage_sha256` is SHA-256 over UTF-8 RFC 8785 of that exact typed preimage. Native receipt bytes remain immutable evidence files and their digest is `receipt_sha256`.

`bms.ont-fastq-qc-final-acceptance-verifier.v1` first validates every embedded object. It recomputes all contract hashes, every evidence-file digest, each nested self-digest, and each gate pointer. It then requires one commit/tree across source, remote `test`, canonical checkout, deployment, browser manifest, all evidence bundles, and all three review receipts. It requires exactly one PASS review in canonical order for `backend_security_scientific`, `frontend_browser_viewer`, and `integration_minimality`. It verifies all A1 through A17 assertions against their typed bodies. It verifies the final `receipt_sha256` last as SHA-256 over UTF-8 RFC 8785 of the complete final receipt with only `receipt_sha256` omitted. The receipt is emitted only after live acceptance and cannot be a prerequisite for itself.

The final verifier also requires A8 screenshot digest = browser row 1, A9 = row 2, A10 = row 5, and A11 range/variant screenshot digests = rows 3/4 respectively. It resolves every referenced response, matrix, trace, and screenshot below the immutable evidence root and verifies filename, size, schema, and SHA-256 before evaluating its gate.

The A4 verifier requires all eight revocation precedence rows in order, including status, typed code, expiry behavior, and CAS reload outcome. A successful revocation event alone cannot satisfy WEB-2.

## 15. Live acceptance ledger

Every row is required unless marked otherwise.

| ID | Pass condition | Required evidence |
|---|---|---|
| A1 | Retry3 package authority closes | Exact 36-descriptor denominator, 34 present, 2 unavailable, sizes, SHA-256, exact manifest digests, source/reference cross-binding, and artifact-set SHA-256 `e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650` |
| A2 | Lifecycle mirrors close | Four canonical completed stages with 25 terminal output descriptors total in DB/API, per-stage counts 5/6/8/6, outputs match immutable terminal receipts, reconciliation receipt valid, resource evidence explicitly `historical_unavailable` |
| A3 | Result endpoint closes | Fresh governed request returns exact bounded result for retry3 with no raw paths or generic design fields |
| A4 | Browser recovery closes | Fresh page with no cookie rotates once, retries once, and loads result/sessions/artifacts |
| A5 | Hierarchy authorization closes | Owner/operator positive path passes; non-owner, non-operator, guessed Job, cross-Project/Global/Domain/state/member/sample/reference, stale-binding, rotation, and second-Job governed reads all fail closed |
| A6 | Full download closes | Exact artifact bytes, size, digest ETag, media type, and attachment policy match authority |
| A7 | Range closes | BAM, FASTA, and one small artifact return exact 206 fragments and Content-Range; invalid Range fails correctly |
| A8 | First viewport closes | Result-first hierarchy visibly shows execution complete plus scientific review, exact metrics, and reason codes |
| A9 | Decision report closes | Checks, two purpose-labeled plots, variant table, downloads, and progressive audit disclosures render from retry3 |
| A10 | Local viewer opens | Bundled compact IGV loads exact reference and alignment without external requests or automatic fullscreen |
| A11 | Range navigation closes | `eGFP_plasmid:3400-3600` and the 3515-3516 VCF record context both display with exact case and affected-base semantics |
| A12 | Bulk-data behavior closes | No full BAM transfer; initial BAM Range bodies total under 64 MiB; viewer reaches ready state |
| A13 | Failure isolation closes | Optional viewer failure does not suppress valid report or downloads |
| A14 | Console/network closes | No uncaught errors, integrity denials, hanging requests, external genomic calls, or secret/path disclosure |
| A15 | Normal reopen closes | Start at frozen BFX6NB Project, enter the frozen Global/Domain Experiment and state revision through ordinary controls, open retry3 from its exact member receipt, close page state, repeat, and recover the same result; substituting a foreign readable Job clears context or fails closed |
| A16 | Runtime release closes | Remote, canonical checkout, managed processes, listeners, database, and served frontend/API all bind to one accepted revision |
| A17 | Isolation closes | No fifth compute Job, no retry3 artifact mutation, no Production action, no unrelated worktree change |

## 16. Implementation order

1. Add this specification and exact RED tests for backend lifecycle and result authority.
2. Repair terminal-output resolution, persisted stage mirrors, finalizer tests, and generic-ingestion/resource isolation.
3. Add the bounded, audited retry3 reconciliation service and command with dry-run/apply tests.
4. Add strict parser/query tests and repair current frontend contract defects.
5. Rebuild the canonical FASTQ-QC result hierarchy, checks, plots, variant actions, and governed downloads.
6. Make IGV local-only and compact; add the governed Range control and keep the existing read inspector collapsed and nonblocking.
7. Run the pre-merge focused matrix and seal the feature candidate.
8. Merge current `origin/test`, resolve exact overlaps, and rerun the complete matrix.
9. Obtain exact-tree reviews, apply required fixes, and rerun affected gates.
10. Fence the managed Development sync owner, back up state, prove pre-push safety, and perform the guarded non-force push while deployment remains paused.
11. Prove the pushed revision is still fenced, then resume the managed owner and prove runtime identity.
12. Reconcile retry3 through the deployed supported path.
13. Run live API, Range, fresh-browser, normal-reopen, report, and viewer acceptance.
14. Repair any specification-owned live defect and repeat every affected row.

## 17. Definition of done

This specification is complete only when A1 through A17 pass against one exact deployed Development revision and the retry3 fixture.

Source implementation, local tests, a push, healthy services, persisted files, or one successful API request cannot independently satisfy completion.

A final completion claim must include:

- accepted commit and tree;
- remote `test` identity;
- managed Development source/process/listener/database identity;
- retry3 reconciliation receipt;
- exact artifact and Range evidence;
- fresh-browser and normal-reopen evidence;
- screenshots of the first result viewport, decision visuals, exact Range, and compact loaded IGV;
- browser console/network audit;
- explicit proof of no fifth compute job and no scientific artifact mutation.
- the validated final acceptance receipt and its SHA-256.
