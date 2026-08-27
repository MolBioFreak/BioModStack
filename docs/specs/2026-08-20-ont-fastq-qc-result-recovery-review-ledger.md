# ONT FASTQ-QC result recovery review ledger

## Scope

This ledger consolidates the independent SOW and candidate reviews from these batches:

- `deleg_1aafbf5c`
- `deleg_c0a5d4df`
- `deleg_9b41e380`
- `deleg_23034cb9`
- `deleg_e07ab1c0`
- `deleg_159cf663`
- `deleg_35961ece`
- `deleg_ae15e449`
- `deleg_69595f14`
- `deleg_fc350aeb`
- `deleg_2832c37c`
- `deleg_634fa0f4`
- `deleg_4d2e4ee9`

The reviewed SOW revisions included SHA-256 values `8144eeebc7151d6e69d4f0c01948a67188c6b403a7b426f11be1ad299972f310`, `87f374043d739c4a009bcedc34f56b10ab60617ca376e3e1d4a2f13626a7ef44`, `77601ac574effd07347c09c28678fa0e54fb8fec0d46ad599ebc04172d576ab4`, and `0f86dadf5cd4161cf6c0aab7e388ea56120b79f086593dd21c9f2fc44db8ea4a`. Each prior verdict became obsolete when the SOW or its normative package changed. A PASS for an older hash does not approve the current package.

Status meanings:

- **FIXED**: the current specification or source contains the required correction. Any remaining verification is named explicitly.
- **OBSOLETE**: the exact reviewed claim applies only to a replaced byte revision or is contradicted by the current bytes.
- **OPEN**: implementation, verification, integration, deployment, reconciliation, or live acceptance remains incomplete.

## Hash-bound review verdicts

| ID | Finding or verdict | Status | Current disposition |
|---|---|---|---|
| H-01 | Backend and browser reviews of SOW `8144…` requested changes | OBSOLETE | The reviewed SOW bytes were replaced. Every underlying invariant appears below. |
| H-02 | Browser review of SOW `87f…` returned PASS | OBSOLETE | Backend review still blocked that revision, and later SOW edits invalidate the browser PASS. |
| H-03 | Backend and browser reviews of SOW `776…` requested changes | OBSOLETE | The reviewed SOW bytes were replaced. Every underlying invariant appears below. |
| H-04 | Exact-hash reviews of SOW `0f86…`, schema `a09f…`, and fixture `f860…` requested changes | OBSOLETE | All three normative artifacts changed. No prior reviewer approves the current package. |
| H-05 | Direct integration from `f4938ae6` into `origin/test` was safe | OBSOLETE | Independent integration audits proved the old-base overlay can silently discard upstream behavior. Narrow replay remains required. |
| H-06 | Backend and browser reviews in `deleg_35961ece` requested changes | OBSOLETE | Their exact package bytes changed. Every current-byte finding is resolved by S-19 through S-27 and requires a new exact-hash review. |
| H-07 | Backend and browser reviews of package `3733a00d…` requested changes | OBSOLETE | The reviewed bytes changed. HASH-01/WEB-01/RESULT-01/SESSION-01/AUDIT-01/AUTH-01 and F-01 through F-07 are resolved by S-28 through S-36. |
| H-08 | Backend and browser reviews of package `93fa1ee1…` requested changes | OBSOLETE | The reviewed bytes changed. LIFECYCLE-01/SESSION-01/SCIENCE-01/RESULT-01 and EVIDENCE/BROWSER/RANGE/REVOCATION findings are resolved by S-37 through S-45. |
| H-09 | Backend and browser reviews of package `924b1e1b…` requested changes | OBSOLETE | The reviewed bytes changed. RESULT-VALIDATOR/AUDIT-EVIDENCE and F-01 through F-04 are resolved by S-46 through S-50. |
| H-10 | Backend and browser reviews of package `16dd6997…` requested changes | OBSOLETE | The reviewed bytes changed. A3 literal, A4 trace, A5 rotation-denial, and A6 header/digest findings are resolved by S-51 through S-54. |
| H-11 | Backend and browser reviews of package `84e392e0…` requested changes | OBSOLETE | The reviewed bytes changed. Audit-evidence, revocation, containment, A3 identity, and A6 identity findings are resolved by S-55 through S-60. |
| H-12 | Backend and browser reviews of package `c771b198…` requested changes | OBSOLETE | The reviewed bytes changed. Opaque-route, A2, revocation-acceptance, browser-route, and A7 retained-evidence findings are resolved by S-61 through S-65. Operator prohibited further review. |

## Specification and normative-contract findings

| ID | Consolidated finding | Status | Current evidence or remaining gate |
|---|---|---|---|
| S-01 | Frozen hierarchy omitted the exact state revision, member receipt, and hierarchy digest | FIXED | SOW §4.1 freezes all three identities and binds them through authorization, reconciliation, and acceptance. Exact-hash review remains open. |
| S-02 | Package denominator conflicted between 25 and 36 | FIXED | SOW AUTH-8 and A1 define 36 semantic descriptors, with 34 present and 2 unavailable. A2 separately defines 25 terminal stage-output paths. |
| S-03 | `artifact_set_sha256` lacked a reproducible byte domain | FIXED | AUTH-8 defines the five-field projection, unavailable representation, duplicate rule, byte-sort tuple, RFC 8785 document, domain label, and SHA-256 procedure. It reproduces retry3 digest `e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650`. Focused tests pass. |
| S-04 | Reconciliation provenance and receipt digests were circular | FIXED | RECON-5 defines domain-separated RFC 8785 digests, a receipt-free provenance postimage, and a self-digest that omits only `receipt_sha256`. The hierarchy record is an explicit permitted additive key. Implementation remains open. |
| S-05 | Reconciliation receipt lacked a closed schema and complete authority fields | FIXED | `schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json` closes hierarchy, lane/database, backup, source tree, pre/postimage, package, and no-compute/no-mutation fields. Service and CLI wiring remain open. |
| S-06 | Stage completion was count-only and did not freeze roles or exact outputs | FIXED | LIFE-2 freezes all 25 retry3 terminal paths in producer order, per-stage cardinality 5/6/8/6, path prefix, required roles, and negative cases. Implementation parity remains open. |
| S-07 | Lifecycle timestamps were timezone-free or validator-dependent | FIXED | All normative timestamp contracts require UTC RFC 3339 with terminal `Z`; the fixture uses `Z`; format-aware schema tests pass. |
| S-08 | JSON Schema annotations were treated as executable cross-field validation | FIXED | AUTH-5 now separates the complete-source construction validator from the bounded wire validator and binds them through a projection attestation. Browser validation never claims omitted-row proof. Implementation parity remains open. |
| S-09 | Resource evidence could mix historical metadata with accepted execution proof | FIXED | LIFE-9 and the result schema define `historical_unavailable` and `accepted` branches. Retry3 cannot carry an accepted receipt. Focused backend projection/schema tests pass. Full result and frontend verification remain open. |
| S-10 | Browser rotation and governed failures lacked closed routes, statuses, and codes | FIXED | WEB-2, WEB-6, and `ont_ngs_rotation_success_v1.schema.json` freeze cookie transport, expiry, replacement, retryability, OpenAPI component IDs, and route status maps. Router/OpenAPI implementation remains open. |
| S-11 | Alignment-session viewer authority was incomplete and availability states were incoherent | FIXED | VIEW-4 freezes retry3 session `de4e4d2b2062fbf21ddfee65`, the BAM/BAI pair digest, opaque route IDs, file digests, and identifies `299233…` as the HTML-report route ID. The alignment schema models pair identity separately. |
| S-12 | Unavailable package artifacts could omit a producer reason | FIXED | Result-schema state branches require a nonempty reason for unavailable states and null reason for present states. Shared adversarial schema tests pass. Backend/frontend parity remains open. |
| S-13 | UI-4 required a per-row variant reason absent from producer authority | FIXED | UI-4 displays producer `support_status`, prohibits invented row-level reasons, and keeps aggregate reason codes in the decision-check surface. |
| S-14 | First-viewport labels could mix fraction/percent and two depth bases | FIXED | UI-1 freezes labels, scales, bases, and retry3 values for coverage, consensus identity, decision support depth, and coverage-envelope depth. Mounted and browser proof remain open. |
| S-15 | Download grouping and unavailable roles were underspecified | FIXED | UI-5 and the result schema freeze every retry3 descriptor's source, kind, role, media type, disposition, and display order. The fixture carries those fields. Frontend implementation remains open. |
| S-16 | Retry3’s mapping screen could be presented as taxonomic contamination exclusion | FIXED | §4.3 and UI-2 define `expected_reference_mapping_only`, `organism_identity_claimed=false`, and prohibited claims. |
| S-17 | VCF coordinates could mislabel the deleted biological base | FIXED | §4.3 and UI-4 separate VCF anchor 3515 from affected/deleted base 3516. |
| S-18 | Final release drift lacked one closed acceptance receipt | FIXED | REL-11 and the final schema embed typed A1-A17 bundles, ordered browser evidence, exactly one PASS per review scope, and a typed deployment receipt. The named verifier enforces one revision and recomputes nested and final digests. Emission remains open. |
| S-19 | Coverage construction rules were impossible to re-prove from bounded browser points | FIXED | AUTH-5 defines separate construction and wire validator IDs plus a source-row/projection attestation. Schema annotations include required-artifact and resource coherence. |
| S-20 | Result and alignment contracts used incompatible session IDs | FIXED | Both schemas require 24 lowercase hex and the retry3 fixture uses deterministic session `de4e4d2b2062fbf21ddfee65`. |
| S-21 | Reconciliation digests and SQLite checks were not independently reconstructable | FIXED | RECON-5 through RECON-7 now freeze every digest label/preimage, no-follow result-root identity, source snapshot fields, full integrity checks, backup method, transaction timing, and replay comparison. |
| S-22 | Final evidence pointers were opaque bare hashes | FIXED | Package-contained evidence, browser, review, and deployment schemas are normative and embedded in the final receipt. |
| S-23 | Release fencing and runtime identities lacked reproducible preimages | FIXED | REL-11 freezes preimages for fence, quiescence, backups, processes, listeners, databases, migrations, and frontend build. |
| S-24 | Governed non-2xx and rotation OpenAPI contracts were open | FIXED | WEB-6 freezes exact component IDs and per-route status maps. |
| S-25 | `299233…` was assigned to the wrong alignment identity | FIXED | VIEW-4 identifies it as the HTML report route ID and adds a distinct alignment-pair digest plus per-file route and byte identities. |
| S-26 | Artifact grouping and delivery metadata were inferential | FIXED | UI-5 contains the complete 36-row role/media/disposition/order table and the schema closes its fields. |
| S-27 | Range, ETag, conditional, and capability-cookie behavior was underdefined | FIXED | WEB-2 and WEB-4 freeze cookie attributes, credentials, strong ETag syntax, accepted ranges, If-None-Match, If-Range, 200/206/304/400/409/416 headers, and attachment naming. |
| S-28 | Specification package self-digest was inferential | FIXED | §6.4 freezes record order, omitted field, RFC 8785 bytes, UTF-8 encoding, SHA-256, and schema domain. |
| S-29 | Missing `If-Range` incorrectly disabled Range | FIXED | WEB-4 now honors Range when `If-Range` is absent or matches and ignores it only when a present validator differs. |
| S-30 | UI-5 mapping and contiguous order were annotations only | FIXED | AUTH-5 rules 14 and 15 make the complete row mapping and contiguous order mandatory at construction and wire boundaries. |
| S-31 | Alignment list allowed dimer-only, duplicate, reversed, or unavailable-only primary lists | FIXED | The alignment schema uses positional branches: one ready primary first and one optional dimer-candidates second. |
| S-32 | Retry3 session ID denominator was not reproducible | FIXED | VIEW-4 freezes the exact sorted role-to-artifact-ID denominator for `de4e4d2b2062fbf21ddfee65`. |
| S-33 | Generic alignment schema hard-coded retry3 pair digest | FIXED | The generic schema accepts a per-session digest and requires semantic recomputation. VIEW-4 pins retry3 only. |
| S-34 | HttpOnly capability had no governed revocation path | FIXED | WEB-2 and the revocation-success schema freeze DELETE, idempotence, expiry header, typed failures, and OpenAPI mapping. |
| S-35 | Download filename extension was open | FIXED | UI-5 and the fixture/schema freeze a kind-specific extension for every present row and null for unavailable rows. |
| S-36 | A1-A17 bundles and browser screenshots admitted missing or duplicate evidence | FIXED | The evidence schema fixes one assertion and exact required filenames per gate; the browser schema fixes five unique positional views. |
| S-37 | Stage summaries allowed missing states and wrong output counts | FIXED | Result-schema positional stages require complete 5/6/8/6 and AUTH-5 rule 16 enforces wire parity. |
| S-38 | Result session summaries allowed dimer-only or reversed lists | FIXED | Result and alignment schemas require ready primary first and optional dimer second; list/detail equality is normative. |
| S-39 | False PASS and stale threshold-profile digests were accepted | FIXED | AUTH-5 rules 18 and 19 define producer profile hashing, duplicate metadata equality, and verdict/check/reason coherence. The fixture restores exact producer values. |
| S-40 | Empty or truncated artifact arrays passed | FIXED | Result schema has 36 positional descriptors; AUTH-5 rule 20 requires counts 36/34/2 and package-digest recomputation. |
| S-41 | Evidence files had no closed content schema | FIXED | `ont_fastq_qc_gate_evidence_body_v1.schema.json` closes every required body and bundle files name their exact content schema. |
| S-42 | Screenshot digests did not resolve bytes or capture context | FIXED | Browser rows bind filename, size, media type, dimensions, route, origin, viewport, timestamp, build, and digest. |
| S-43 | Range boundary and conditional precedence remained open | FIXED | WEB-4 closes end/suffix normalization, empty objects, HEAD, If-None-Match, If-Range, malformed precedence, and exact A7 vectors with fragment digests. |
| S-44 | Revocation outcomes conflicted for absent, stale, foreign, and drifted state | FIXED | WEB-2 contains a complete result/expiry precedence matrix and CAS reload rule. |
| S-45 | Primary list and detail could disagree on readiness | FIXED | Unavailable sessions are auxiliary dimer-only; the semantic validator requires byte-equal list/detail session authority. |
| S-46 | Wire validator denominator contradicted later rules | FIXED | §6.4 now requires wire enforcement of rules 1 through 20, with only omitted-source portions of rule 5 construction-only. |
| S-47 | A3/A5/A13/A15 evidence remained self-attesting summaries | FIXED | Gate bodies embed the governed response or exact case/event/step rows and bind retained trace bytes. |
| S-48 | A8-A11 screenshots were not cross-bound to browser rows | FIXED | Final verifier and schema annotation require exact A8→1, A9→2, A10→5, and A11→3/4 digest equality. |
| S-49 | A7 vector count and matrix digest were open | FIXED | A7 fixes three vectors and the RFC 8785 matrix digest `1d0fc326…`. |
| S-50 | Revocation overlap precedence remained ambiguous | FIXED | WEB-2 uses a first-match eight-row Cartesian precedence table for principal, cookie, hierarchy, package, and CAS state. |
| S-51 | A3 named the wrong result schema literal | FIXED | A3 now requires `bms.ngs.fastq-qc-result.v1` and recomputes embedded response bytes. |
| S-52 | A4 retained counts without a recovery trace | FIXED | A4 binds one immutable typed trace and seven ordered denial/rotation/retry/result/session/artifact/revocation events. |
| S-53 | A5 omitted rotation denials for foreign bindings | FIXED | A5 fixes 26 rows: two positive reads, 12 read denials, and 12 matching rotation denials. |
| S-54 | A6 allowed incoherent download identity and omitted headers | FIXED | A6 fixes retry3 BAM identity, bytes, digest, ETag, media type, disposition, package, and equality. |
| S-55 | A1/A2/A12/A17 retained self-attesting summaries | FIXED | Gate bodies now embed exact or closed typed source evidence with canonical filename, schema, size, digest, and final-verifier recomputation. |
| S-56 | Absent-cookie revocation bypassed hierarchy/package drift | FIXED | The 200 absent-cookie row requires valid hierarchy and package authority. First-match order is authoritative. |
| S-57 | Managed source FASTQ conflicted with result-root containment | FIXED | Artifact `owner_scope` separates no-follow result-root files from the exact persisted managed-input snapshot; race/symlink/foreign/cross-Job negatives are required. |
| S-58 | A3 Job and response could differ from retry3/final/browser authority | FIXED | Every gate/bundle/browser/final Job is the retry3 constant and A3 response is exact canonical fixture content. |
| S-59 | A6 used file digest as route identity | FIXED | A6 and the result descriptor use opaque BAM artifact ID `0fe950…`; the file digest remains `c14a54…`. |
| S-60 | Result artifact URLs did not uniformly expose distinct route identity | FIXED | Every present descriptor has an exact opaque `artifact_id`, URL final-segment equality, and digest inequality; the 13 viewer roles use VIEW-4 IDs. |
| S-61 | Stale semantic text still required digest-valued artifact URLs | FIXED | AUTH-5 and the schema require opaque `artifact_id` URL identity and digest inequality only. |
| S-62 | A2 lacked retained DB/API lifecycle readbacks | FIXED | A2 embeds exact canonical DB/API four-stage, 25-output readbacks and requires reconciliation equality. |
| S-63 | Revocation precedence was not in acceptance evidence | FIXED | A4 fixes all eight positional precedence outcomes including expiry and CAS reload. |
| S-64 | Browser screenshots admitted arbitrary routes | FIXED | All five rows and A15 bind the exact ordinary retry3 hierarchy route. |
| S-65 | A7 boundary evidence used summary booleans | FIXED | A7 now retains nine exact request/response rows and digest `8183cd3e…`. |

## Candidate implementation findings

| ID | Consolidated finding | Status | Current evidence or next gate |
|---|---|---|---|
| I-01 | Terminal Nextflow publication did not guard the complete active owner/lifecycle snapshot | OPEN | SOW LIFE-6 is closed. Current source added parameter preimage protection but still needs exact owner, InvocationID, provenance, stage mirror, and stale-attempt CAS review plus adversarial tests. |
| I-02 | Reconciliation service receipt omitted hierarchy, database/lane, source tree, pre/postimage, and self-digest authority | OPEN | Closed schema and SOW now exist. `ont_ngs_reconciliation.py` and its tests still use the old receipt. |
| I-03 | Reconciliation CLI lacked source-tree/database identity binding, hierarchy sessions, owner quiescence, and exit 0/2/3/4 mapping | OPEN | `reconcile_ont_fastq_qc_job.py` remains to be repaired and tested. No apply is authorized before deployment. |
| I-04 | Governed routes could expose files after package drift because only `/ngs-result` enforced persisted package authority | OPEN | Current `require_alignment_job` prebuilds and caches the validated result for canonical jobs, which addresses the disclosure order in source. Full route matrix and drift negatives remain required. |
| I-05 | Pydantic/OpenAPI response validation accepted arbitrary dictionaries | OPEN | Current root model invokes the strict result contract, but OpenAPI component equality and adversarial runtime tests remain required. |
| I-06 | Frontend result parser was weaker than the closed wire schema | OPEN | It still needs field-specific or generated Draft 2020-12 validation and complete semantic-validator parity tests. |
| I-07 | Alignment-session API and frontend do not yet implement the new closed viewer contract | OPEN | Add ready/unavailable envelopes, manifest/package bindings, required local FASTA/FAI and BAM/BAI authority, parser tests, and mounted behavior. |
| I-08 | Typed governed errors were not implemented across result/session/artifact/Range/read/rotation routes | OPEN | Implement the shared error response and OpenAPI error components without path or secret disclosure. |
| I-09 | FASTQ resource projection did not distinguish historical metadata from accepted producer evidence | FIXED | Backend helper and result schema now implement both branches. Focused GREEN evidence: 3 resource/result-schema tests and 16 supporting schema/package tests. Full matrix remains open. |
| I-10 | Viewer generations could survive an exact Job switch, and optional-track failures could suppress the primary viewer | OPEN | Reset every asynchronous viewer generation on Job identity change. Keep primary BAM readiness independent from optional tracks. Add mounted regressions. |
| I-11 | Result hierarchy and first viewport did not expose all required labels, bases, and decision fields | OPEN | Update mounted UI, progressive disclosure, role-grouped downloads, and browser acceptance. |
| I-12 | HTML/IGV report could execute external CDN code or use unsafe inline policy | FIXED | Current source makes HTML download-only, removes the jsDelivr CSP allowance, and requires local same-origin IGV. Full route/browser proof remains open. |
| I-13 | Coverage accepted gaps or non-1-based rows | FIXED | Current source requires `position == row_index + 1`, one reference, nonnegative depth, and full reference length. Focused and full tests remain open after later edits. |
| I-14 | Conflicting workflow identity fields could pass through first-truthy classification | FIXED | Current completion/adapter/hierarchy/result paths reject contradictory canonical identity fields. Full regression matrix remains open. |
| I-15 | Artifact MIME could come from unsafe filename guessing | FIXED | Governed descriptor semantics now select media type, including `text/x-vcf`. Route and OpenAPI proof remain open. |
| I-16 | Frontend concurrent late-403 recovery could exhaust a one-shot retry | FIXED | Current client shares rotation/invalidation and provides result-only recovery. Mounted cookie/rotation/browser proof remains open. |
| I-17 | Current test evidence predates final schema, resource, MIME, coverage, classification, and response changes | OPEN | Rerun exact backend/frontend/typecheck/build gates after the last source edit and after integration. |
| I-18 | Candidate wire validator still requires artifact URL identity to equal file SHA-256 | OPEN | The sealed specification now requires distinct opaque `artifact_id` route identity and `owner_scope`. Repair the validator/projection/routes in S3. The stale 11-case runtime matrix is excluded from the S2 package seal and remains failing until that repair. |

## Integration and release findings

| ID | Consolidated finding | Status | Current evidence or next gate |
|---|---|---|---|
| R-01 | Old-base worktree can be merged wholesale | OBSOLETE | Candidate began at `f4938ae6`; reviewed `origin/test` was 73 commits ahead. Eight overlapping paths can silently discard upstream behavior despite textually clean merges. |
| R-02 | A narrow replay onto exact current `origin/test` is required | OPEN | Re-fetch the remote immediately before integration. Carry add-only/non-overlap files, manually port specification-owned overlap hunks, omit superseded frontend paths, preserve target-native workbench behavior, and union Vitest registrations. |
| R-03 | Candidate is sealed, committed, pushed, and deployed | OPEN | Candidate remains dirty, uncommitted, unpushed, undeployed, and unreconciled. |
| R-04 | Retry3 lifecycle mirrors are reconciled | OPEN | Dry-run only. Apply requires canonical Development deployment, owner gate, online backup, exact receipt, CAS, and idempotent replay. No scientific artifact mutation is allowed. |
| R-05 | Ordinary browser reopen, report, Range, downloads, and local IGV are accepted | OPEN | No current live acceptance exists. Previously deployed browser behavior failed. A3 through A15 remain open. |
| R-06 | No fifth compute job and no Production action remain binding constraints | OPEN | No fifth job or Production action has occurred. Final A17 and acceptance-receipt evidence are still required.
