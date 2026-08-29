# Statement of Work: Full NGS and Mol Bio Toolkit Integration with Global Project and Experiment Management

**Status:** Finalized implementation-controlling child SOW; implementation and Development acceptance remain INCOMPLETE

**Date:** 2026-08-12

**Controlling parent specification:** `docs/specs/global-bms-project-experiment-manager.md`

**Shared-package owner:** This NGS/MolBio tranche solely owns the shared global package listed in parent section 0.6 and this SOW section 2.4. Repository and release records bind the exact document hashes and source baseline without a self-referential commit inside this file.

**Implementation authority:** This document specifies work. Source edits, test execution, live runs, deployment, and Production promotion require separate authorization.

## 0. Current implementation status and phase gap assessment

This ledger records evidence from Development commit `e53e670db3baede36ec69d8257502964ce43d0c3`, tree `d27142e54de0c9102b421c0d63822143ac801b5b`. At assessment time `test`, `origin/test`, canonical Development, and the live API matched that commit. Browser testing used isolated Development records and retained Project-layer fixtures. Production was unchanged. This is historical product evidence. Later specification-only commits do not upgrade it, and PM-02 must resample current remote and live identities before implementation.

**SOW verdict:** INCOMPLETE. The NGS/MolBio Project layer has working hierarchy, a merged Project/Domain workspace, exact state navigation, Dataset/history readers, and exact molecular reopening. Current authority drift blocks new Domain, Dataset, and Plan creation. Required active sample/reference controls, connector convergence, shared-package acceptance, payload-ownership audit, operational records, and the full N6 scenario remain open.

| Phase | Current status | Assessment evidence |
|---|---|---|
| N0 contract/source freeze | **FAILED current-tree gate** | The installed source pin rejects `platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx`. The Project-layer authority denominator is stale relative to installed source. |
| N1 additive migrations and binding | **PARTIAL** | Existing retained local state and binding readers reopen, but a new Project Manager NGS/MolBio Domain could not be created because the source-authority check failed before persistence. |
| N2 connector and convergence | **PARTIAL** | One managed worker owns the lease. Health reports three outbox conflicts and two deferred inbox gaps; therefore convergence and ordered-delivery closure are not accepted. |
| N3 adapters and lineage | **PARTIAL** | Retained sample and reference receipts render in the Project map, but 19 BFX6NB receipts are stale and lack bounded freshness/re-verification authority. Required producer-native `Add to Project` exposure is incomplete across samples, molecular revisions, primers/PCR, references, panels, instrument observations, Jobs, manifests, analyses, alignments, and evidence. Exact negative and restart acceptance for every receipt family is unsealed. |
| N4 active toolkits and launch context | **PARTIAL** | All 11 Domain workspace sections render. One exact pBR322 revision reopens in Mol Bio Toolkit with complete hierarchy and revision query identity. Samples have no create/revise controls. Managed Reference Create/Import/Archive controls remain disabled. Plan creation and preparation are disabled by the source-authority failure. Complete deterministic pagination and response-bound browser closure for each Domain collection is unaccepted. |
| N5 Dataset, ELN, operations, shared package | **PARTIAL/MISSING** | A retained immutable Dataset revision and exact history render. Current Dataset create is disabled by the source-authority failure. A Project decision persists but has no visible tree/map/inspector reopen path. Validation artifacts and bounded attempt logs have zero rows. No shared-package acceptance receipt, payload-ownership audit, or verified current Project export receipt was found. |
| N6 current-tree verification and Development acceptance | **FAILED** | The browser could not complete the required create → bind → state → Dataset → plan → prepare → launch → result → restart path on the assessed tree. Independent exact-tree review is also open. |

### 0.1 Browser-verified critical behavior

- The NGS and Mol Bio routes render one merged NGS/MolBio Project plus Domain Experiment window rather than two sibling management windows.
- The merged window exposes the selected local Project, Domain Experiment, exact local state revision, all Domain section selectors, and the compact optional broader-Project exposure control.
- Mol Bio Toolkit expands to 865 CSS pixels in a 993-pixel browser viewport and is no longer capped at 48 rem.
- Overview, Samples, Molecular Inputs, References, PCR, Instrument Runs, Datasets, Plans & Runs, Analyses, Evidence, and History all route through validated `section` query state without browser alerts, except the authority-blocked Dataset and Plans & Runs mutation surfaces.
- Molecular Inputs renders all four entry modes and the attached exact pBR322 authority. The exact reopen action preserves `workspace_id`, `global_experiment_id`, `domain_experiment_id`, `state_revision_id`, `molbio_sequence_id`, and `molbio_revision_id` and loads Mol Bio Toolkit.
- Restart reopening preserves the browser-created Project, Global Experiment, immutable Project revision, selected URL state, and persisted decision row.

### 0.2 Current release blockers

1. Reconcile and version the installed source pin/runtime authority for the current Mol Bio Toolkit bytes; retain fail-closed verification.
2. Enable and browser-accept owner-authorized sample create/revise and managed-reference create/import/revise/archive flows.
3. Expose and accept receipt-backed `Add to Project` on every required native NGS/MolBio surface.
4. Close current Dataset and Workflow Plan mutation authority after the source pin is repaired; then prove prepare creates no Job and explicit launch creates one idempotent path.
5. Resolve or formally disposition the three outbox conflicts and two deferred generation gaps without regressing heads.
6. Add bounded receipt freshness/re-verification for the stale BFX6NB sample/reference receipts.
7. Close deterministic pagination and response bounds for each Domain collection and its UI accumulation path.
8. Render Project/Experiment ELN records in the tree, map, inspector, or bounded activity surface after append and restart.
9. Produce real global validation-artifact and bounded attempt-log records, current verified backup/export receipts, the retained payload-ownership audit, and one complete shared-package acceptance receipt.
10. Run the full N6 browser scenario and independent exact-tree review on one final deployed commit/tree.

### 0.3 Reopened visual and congruence gate

The merged component hierarchy and `clamp(36rem, calc(100vh - 8rem), 96rem)` rule are historical implementation evidence. They remain **PENDING visual acceptance**. Christian's report that the Project layer consumed about half of the page, duplicated content, and visually separated controls is controlling evidence that the prior automated pass did not close usability.

PM-01 uses the exact state, structure, geometry formulas, desktop viewports, and accessibility evidence in parent section 0.4. It closes technical pre-deployment acceptance only. In this child surface it must:

1. replace the always-visible create form and selected-Project card grid with one compact selected-context band plus a modal or side sheet for create/edit;
2. render local Project, Global Experiment, and Domain Experiment authority once through one hierarchy selector/breadcrumb;
3. keep optional broader-Project links in a collapsed, clearly non-owning association control;
4. render Domain section navigation as one compact row or rail inside the same band rather than a second management window;
5. remove repeated objective, status, revision, identifier, and action content from the default Domain and Toolkit surfaces;
6. keep all PM-03A, PM-03B, PM-04, PM-05, and PM-06 controls available after intentional disclosure and show unavailable controls with their exact reason;
7. give the active Mol Bio or NGS Toolkit the remaining viewport without a parent height cap or nested page-sized scroll region;
8. finish with the parent PM-01 technical packet. Christian's signed visual decision remains exclusively in PM-12 after Development runtime and browser acceptance exist.

### 0.4 NGS/MolBio pending-fix readiness map

The parent packages are bounded implementation tranches. `PM-02` is the first eligible tranche. PM-01 executes only after PM-03B through PM-06 have mounted the final visible control denominator. NGS/MolBio-specific work is assigned as follows:

| Parent package | NGS/MolBio correction ready for implementation |
|---|---|
| PM-01 | Compact and deduplicate `NgsMolBioProjectHub` plus `DomainExperimentWorkspace` under the parent geometry contract after the functional controls are mounted. Retain only pre-deployment technical evidence. |
| PM-02 | Reconcile `source_pin_v1.json`, the successor runtime denominator, capability exposure, and Dataset exposure with the final changed bytes; keep unverified rows unavailable. |
| PM-03B | Implement Project v2 local/global NGS/MolBio hierarchies after PM-03A accepts the shared runtime/service contract and PM-10A accepts the connector foundation. Keep local Projects independent from broader Projects. |
| PM-04 | Mount receipt-backed attachment actions for samples, molecular revisions, primers/PCR, references, panels, ONT observations, Jobs, manifests, QC, analyses, alignments, and evidence. Add literal `manifest_identity` and comparison-panel version consumers. Bind Mol Bio mutations to server-verified opaque Project/Domain context. |
| PM-05 | Mount sample create/revise, evidence attach/assess, managed-reference lifecycle, Dataset create/revise, and Plan/preparation UI using existing typed clients and immutable revision contracts. |
| PM-06 | Implement the parent composite-envelope provenance, maintained count/status projections, and page contract. Replace the integer Plan-revision cursor with an opaque Project/Domain/plan/limit-bound keyset cursor. Add exact-history page accumulation and render Project/Experiment ELN replacement history. |
| PM-07 | Add bounded re-verification and successor-receipt handling for the 19 stale BFX6NB receipts and every accepted adapter family. |
| PM-08A | Prove prepare creates no Job, explicit launch creates one idempotent authority path, and retry/resubmit/cancel preserve immutable preparation and selected Dataset/reference context. |
| PM-09 | Materialize global validation artifacts and bounded attempt logs from real NGS/MolBio terminal execution; expose exact result and evidence reopening. |
| PM-10A | Implement and accept the connector command/inbox/acknowledgement/binding foundation before PM-03B consumes it. |
| PM-10B | After PM-07 through PM-09, resolve three outbox conflicts and two deferred inbox gaps, then prove ordered convergence, operational evidence, and restart recovery without regressing stream heads. |
| PM-11 | Produce current verified backup/restore/export receipts, a retained payload-ownership audit, and one aggregate shared-package acceptance receipt for the immutable pre-deployment candidate. |
| PM-12 | Bind the accepted PM-11 receipt to independent exact-tree review, managed Development deployment, health, restart/reopen, full N6 browser evidence, and Christian's visual acceptance in one final acceptance receipt. |

No package is marked implemented by this readiness map. Implementation, code tests, live scientific runs, and Production promotion require their own authority and evidence.

## 1. Outcome

Deliver one complete NGS/MolBio domain vertical under the existing hierarchy:

```text
Project
→ Global Experiment
→ NGS/MolBio Domain Experiment
→ exact local scientific-state revisions
→ immutable molecular, sample, reference, acquisition, analysis, and evidence authorities
→ global Datasets, lineage, activity, notes, observations, decisions, and conclusions
```

The accepted operator path is:

```text
create NGS/MolBio Domain Experiment
→ initialize a verified local binding
→ create or import immutable molecular material through one shared reference-entry window
→ select an existing exact revision, upload FASTA/GenBank, paste FASTA/raw nucleotides, or retrieve a versioned NCBI accession
→ reuse an existing exact molecular revision when normalized sequence bytes have the same digest while retaining the new NGS import operation and source provenance
→ attach several exact revisions to the owning Experiment and optionally to 0..N Dataset revisions
→ require an explicit primary-reference choice at launch for workflows that consume one reference
→ save an exact local scientific-state revision
→ launch ONT/NGS work in that exact context
→ attach terminal evidence
→ expose exact molecular-viewer reference links on Domain and NGS result surfaces
→ reopen PCR, comparison, QC, alignment, run, and evidence authority
→ restart
→ reopen the complete Project at the same revisions
```

The global layer organizes and verifies the work. Native domain stores keep scientific authority.

## 2. Scope boundary

### 2.1 In scope

- Global-to-local Domain Experiment bootstrap and binding.
- Versioned binding of local state to exact global revisions.
- Managed domain-to-global outbox delivery and idempotent global ingestion.
- Active Mol Bio Toolkit and NGS Toolkit operation in exact Project context.
- Complete verified adapter coverage for the NGS/MolBio receipt vocabulary.
- Global Workflow Plan, preparation, launch-context, run, attempt, retry, resubmit, result, and evidence presentation for supported NGS work.
- Immutable global Datasets with exact, independently retrievable revisions.
- Cross-store lineage and exact native reopening.
- ELN-like notes, observations, decisions, conclusions, and evidence links.
- Bounded Project read models, health, backup, export, restart recovery, and Development browser acceptance.
- Two-tier NGS/MolBio Project ownership: module-local Projects and broader BMS Projects use the same Project and Experiment authority while retaining distinct owning UI surfaces.
- Explicit many-to-many links from one module-local NGS/MolBio Project to multiple broader BMS Projects. Each link selects the local Experiments and Results exposed to that broader Project and never copies native scientific payloads.

### 2.2 Authority boundaries

| Authority | Owner |
|---|---|
| Project, Global Experiment, Domain Experiment heads and revisions | Global `experiments.db` services |
| Local NGS/MolBio state and state revisions | NGS/MolBio domain store |
| Molecular documents, constructs, primers, PCR records, and operations | Mol Bio store and services |
| Core NGS jobs, ONT records, reference sets, assignments, QC, manifests, and alignment sources | Core NGS and instrument services |
| Scientific artifacts and canonical result payloads | Their native stores and governed artifact roots |
| Global membership, receipt verification, lineage, projections, and ELN records | Global Project/Experiment services |
| Hardware and MinKNOW control | Existing NGS/instrument control plane |

The domain connector uses separate sessions for each store. It never writes another store's tables through an ORM model owned by the wrong service.

### 2.2.1 Canonical research containment and result language

- A **Project** is a research container. A Project contains Experiments.
- An **Experiment** is a planned or executed unit of scientific work. It owns references to its real or in-silico input Data, its workflow execution history, and its Results.
- A **Workflow Receipt** records summary and log facts about execution: what ran, when it ran, how it ran, exact settings, software/runtime authority, status, and governed lineage. A Workflow Receipt is execution evidence. It is not the canonical scientific Result payload.
- A **Result** is the data-bearing scientific output that an operator inspects, compares, visualizes, exports, or cites. Canonical Result Data stays in its native store or governed artifact root.
- A **Dataset** is a governed selection of immutable Data or Result references for an Experiment or comparison. Dataset membership never transfers or duplicates canonical payload ownership.

The global Project store holds containment, membership, identifiers, receipts, digests, bounded summaries, and lineage. Native stores retain scientific Data and Result payloads.

### 2.2.2 Two-tier NGS/MolBio Project ownership

Both tiers use the canonical Project → Global Experiment → Domain Experiment hierarchy and parent `bms.project.v2`:

1. `global` Projects are authored from the main Project Manager. They may contain Protein, NGS/MolBio, or later accepted Domain Experiments.
2. `ngs_molbio_local` Projects are authored from the NGS/MolBio layer. They contain only NGS/MolBio Domain Experiments and can operate without a broader BMS Project.

`project_scope` is immutable revision authority. It is server-validated on create and every child mutation. A local Project cannot acquire a Protein child. A global Project cannot be presented as a local owner. Historical Project v1 compatibility follows the parent section 7.1 and never infers local scope from route, name, or children.

A module-local Project is complete without a broader association. It can link to `0..N` global Projects through one stable `bms.ngs-molbio-project-link.v2` aggregate per global/local Project pair, frozen at `docs/specs/schemas/ngs-molbio-project-link-v2.schema.json`. The aggregate has an immutable revision stream. Each current head response contains exactly `schema`, stable `link_id`, immutable `link_revision_id`, `revision_number`, `head_generation`, `state`, both Project IDs with exact revisions/generations/digests, 1..128 selected local Global Experiment references with exact stable ID/revision/generation/digest when active, 0..256 selected Result receipt IDs, nullable `supersedes_link_revision_id`, nullable `legacy_source_link_id`, server-derived `created_by`, `change_summary`, and `created_at`. Experiment stable IDs are unique in one revision. A revoked revision has empty selected arrays.

The server verifies that the source Project is `global`, the target Project is `ngs_molbio_local`, the authenticated principal owns both Projects under the current authorization model, each Experiment is a direct child of the local Project, every selected Experiment has at least one NGS/MolBio Domain child, and every Result receipt is attached to one selected Experiment or its Domain lineage. It derives all persisted revisions, generations, digests, actor, and timestamps. Bytes and mutable ownership never transfer to the global Project.

Create uses `POST /api/projects/{global_project_id}/ngs-molbio-links`, required `Idempotency-Key`, and a closed body containing exactly `local_project_id`, `expected_global_project_generation`, `expected_local_project_generation`, `experiments`, `result_receipt_ids`, and `change_summary`. Its operation slug is `ngs-project-link-create`; canonical target JSON contains server-derived principal ID plus global and local Project IDs. Initial state is `active`, revision number and head generation are `0`, and `supersedes_link_revision_id` is null. A second link for the same global/local pair conflicts.

Successor creation uses `POST /api/projects/{global_project_id}/ngs-molbio-links/{link_id}/revisions`, required `Idempotency-Key`, and a closed body containing exactly `expected_link_head_generation`, `expected_global_project_generation`, `expected_local_project_generation`, `experiments`, `result_receipt_ids`, `state`, and `change_summary`. Its operation slug is `ngs-project-link-revision-create`; canonical target JSON contains server-derived principal ID, global Project ID, and stable link ID. Each request `experiments` item contains exactly `experiment_id` and `expected_experiment_revision_id`; the server derives generation and digest. The successor increments revision number and head generation by one and names the exact prior revision. `state=revoked` requires empty selections. A later owner-authorized active successor can restore selected exposure.

`GET /api/projects/{global_project_id}/ngs-molbio-links?state=&cursor=&limit=` returns bounded current heads. `GET .../links/{link_id}` returns the current head, `GET .../links/{link_id}/revisions?cursor=&limit=` returns immutable history, and `GET .../links/{link_id}/revisions/{link_revision_id}` returns one exact revision. Link history order is exactly `(revision_number DESC, link_revision_id DESC)`. Its authenticated cursor preimage contains `global_project_id`, stable `link_id`, `snapshot_head_generation`, last complete `revision_number` and `link_revision_id`, page `limit`, authorization-context digest, and `bms.ngs-molbio-project-link.v2` contract digest. The first page freezes the current head generation; later pages include only revisions at or below that snapshot. A cursor with another Project, link, snapshot, limit, authority context, or contract digest fails `422`.

Link mutations use the parent section 11.3.1 claim-resolution order: after syntax normalization and owner authorization, an existing same-key/same-request claim replays before current-head checks. Changed content under the same key conflicts. Only a new claim evaluates stale link/Project generation, stale Experiment revision, duplicate Experiment identity, wrong scope, foreign child, foreign Result, invalid state transition, or ownership mismatch, and any failure occurs before writing.

Historical v1 lineage-edge links remain byte-for-byte read-only. Their first owner-authorized change creates one v2 aggregate with a deterministic non-null `legacy_source_link_id`; it never rewrites the edge and cannot create two v2 aggregates for one legacy pair. One local Experiment or Result can support several global Projects while retaining one owning Project, one canonical identity, and one native payload authority. A linked Experiment appears as `linked_local_project` relationship content in the global Project read model and is never reparented into the global hierarchy. Advancing either Project head or any selected Experiment head never rewrites a link. The read model compares every stored Project and Experiment revision/digest with current heads and derives association reconciliation as `current` or `stale`; continued exposure after relevant hierarchy change requires an explicit v2 successor.

### 2.2.3 Shared MolBio and NGS reference library

The molecular sequence viewer and Mol Bio store own one shared catalogue for DNA and RNA reference sequences from operator imports, molecular design outputs, external accessions, and other governed sources. Each saved sequence has immutable revisions. An NGS/MolBio Experiment can attach `0..N` exact sequence revisions to its local scientific-state revision through server-issued molecular revision receipts.

An attachment records Experiment membership and the intended molecular role. It does not copy sequence authority into the Project store or the NGS domain store. MolBio reopens and edits the native sequence through a successor revision. NGS selects only an exact revision attached to the active Experiment state, then receives transient runtime FASTA through a receipt-bound handoff. Several references can remain attached for construct verification, controls, comparisons, and alternative expected sequences. A single NGS launch still identifies its exact expected reference and any separately approved comparison panel.

The former Domain-managed FASTA catalogue remains available only to reopen historical jobs that already reference it. New authoring uses the shared molecular catalogue. Importing or designing a new reference returns the operator to the molecular viewer, where the sequence becomes available for explicit Experiment attachment.

The integration preserves current molecular and sequencing policy:

- Mol Bio assembly starts from pre-overlapped fragments. DNA Weaver plans purchases and pydna validates assembly. DnaCauldron and Gibsembler are not introduced.
- RFDpoly remains the nucleotide-design authority. Its outputs enter the domain through immutable molecular revisions and receipts.
- The SnapGene-parity Project data plane remains a Mol Bio concern. NGS consumes immutable handoffs and never mutates construct authority from observed reads.
- BLOW5 is the target normalized raw-signal authority. POD5 remains at the ONT edge until replay proof supports a later cutover. New FAST5 authority is unsupported.
- Barcode kit BC114 remains non-blocking. The Project layer reports native compatibility evidence and does not invent a blanket rejection.

### 2.3 Out of scope

- A second sequence editor, NGS result viewer, alignment viewer, or instrument-control UI inside Project Manager.
- Copying FASTA, BAM, BLOW5/POD5, alignment, manifest, or report payloads into `experiments.db`.
- Automatic attachment by name, path, timestamp, or inferred similarity.
- Liquid-handler and BioXP integration.
- Rich ELN pages, signatures, witness flows, inventory, or generalized document blocks.
- Production promotion.

### 2.4 Shared global work-package ownership

This NGS/MolBio tranche is the single implementation owner for the shared global closeout required by both domain verticals:

- additive direct attempt-to-preparation and prepared launch-context v2 authority;
- canonical nested Project Workflow Plan, preparation, launch-context, run-group, retry, resubmit, cancellation, result-surface, and comparison wrappers over existing services;
- the canonical outer Domain Experiment v3 wire-contract support;
- canonical Project Dataset create/revise/list/exact-revision APIs and reusable UI controls;
- global-owned validation-artifact and bounded attempt-log writers/projections;
- authority-context query isolation;
- one shared managed-workflow resource-admission ledger and enforcement service for the 24-thread CPU and 96-GiB DRAM aggregate limits;
- one shared opaque-keyset pagination and response-limit policy for all new global collections;
- the shared `bms.payload-ownership-manifest.v1`, versioned scanner, and retained `bms.payload-ownership-audit.v1.json` release audit;
- shared operational-health, backup, and export fields introduced by these SOWs.

The Protein In Silico tranche consumes these exact migrations, schemas, routes, services, controls, limits, audit mechanisms, and health fields. It adds protein-specific payload schemas, capability adapters, read projections, viewers, and payload-ownership rows. It cannot create a second Workflow Plan route implementation, launch-context authority, Dataset API, artifact/log writer, query-isolation mechanism, resource-admission ledger, pagination policy, payload scanner, or health model. If implementation order changes, this shared package remains one separately accepted NGS-owned prerequisite and retains the contracts in this document.

PM-11 is the sole publication point for retained `bms.shared-global-package-acceptance.v5`. Historical shared-package v1 and failed-candidate v2/v3 schemas remain byte-for-byte readable and cannot accept this package. The v4 body validates `docs/specs/schemas/shared-global-package-acceptance-v5.schema.json`. Its ordered prerequisite array contains exactly one `bms.project-manager.package-acceptance.v3` pointer for each of PM-01, PM-02, PM-03A, PM-03B, PM-04, PM-05, PM-06, PM-07, PM-08A, PM-08B, PM-09, PM-10A, and PM-10B.

`bms.project-manager.package-owner-registry.v2` fixes each package's owner principal, owner service, protected public-key object, exact native-owner principal set, coverage order, one package-specific evidence-body schema, one `bms.project-manager.package-evidence.v2` receipt per coverage item, seven resolver checks, and release-integrator write denial. The 13 package-specific evidence-body schemas close every coverage branch. Each body requires the exact coverage-contract ID, a retrievable denominator, typed producer receipts, positive and negative observation artifacts, resolver trace, all fixed native-owner attestations, and a self-digest. The v2 package receipt repeats the registry-selected owner, evidence-body schema identity/digest, and positional coverage rows. `sha256-rfc8785-ordered-complete-package-evidence-bodies-v2` hashes UTF-8 RFC 8785 canonical JSON for the array of complete resolved package-evidence bodies in the package's registry order, with no fields omitted. The resolver verifies schema digest, every evidence self-digest, all native-owner signatures, multiplicity, candidate source, and trace before computing that manifest. A foreign owner, missing or repeated coverage row, generic body, wrong multiplicity, changed order, or release-integrator signature fails.

PM-11 also resolves five positional `bms.project-manager.migration-attestation.v3` bodies for `global-experiments`, `molbio-domain`, `molbio-ngs-domain`, `core-ngs`, and `biomodstack-native`. The native row must cover launch fences, fence immutability triggers, canonical Jobs, native submission receipts, and cancellation tombstones. It resolves typed quiescence, payload-ownership audit, backup v3, restoration v3, and export v3 bodies. The quiescence body proves zero active Jobs, connector leases, and open launch fences during its named observation fence. PM-11 contains no Development runtime, final review, health, browser run, managed Development restart, or visual decision.

The v4 receipt embeds the two ordered specification-document rows and the complete ordered schema manifest from section 18. `schema_manifest_sha256` is SHA-256 of UTF-8 RFC 8785 canonical JSON for that exact array. `outer_package_sha256` uses `sha256-path-nul-raw-digest-v1`: for every package row in order, hash the UTF-8 path, one NUL byte, and the raw 32-byte SHA-256 file digest. Generated files, implementation files, receipt instances, and files outside the listed denominator are excluded. The resolver reads every path from the immutable candidate tree and verifies ordinal, path, `$id`, raw digest, unique external-reference target, schema count, and package count.

Every active operational digest uses `bms.project-manager.operational-evidence.v2`. A raw artifact digest is SHA-256 of all bytes returned by a confined no-follow read of its exact storage identity; the body records media type and byte count. A canonical JSON artifact rejects duplicate keys, parses exact UTF-8 JSON, omits only its declared field list, serializes RFC 8785 UTF-8 bytes, and records raw and canonical sizes and digests. A receipt self-digest hashes RFC 8785 canonical JSON with only its named self-digest omitted. A writer assertion signs SHA-256 of RFC 8785 canonical JSON containing principal, issuer, registry/key identity, and the complete asserted body. An HTTP observation hashes the resolved URL, authenticated authorization context, selected lowercase response headers, and complete bounded response body through the algorithms named in its closed object.

Derived PM-12 digests have fixed preimages. A component build hashes its component/source rows, raw build and executable artifacts, canonical command line, canonical environment, process ID/start/CWD, and managed unit. `deployed_build_sha256` hashes the three complete component-build rows in API, frontend, workflow-adapter order. Listener and database-set digests hash their complete positional rows. Health response digests come from complete typed HTTP observations. Pre/post restart sets are canonical JSON artifacts containing the complete ordered process rows. Review digests are self-digests of complete immutable review bodies. Screenshot digests cover raw image bytes; geometry digests cover complete canonical geometry receipts. `viewport_evidence_sha256` hashes the complete three-row viewport array in 1366×768, 1920×1080, and 2560×1440 order. No bare 64-hex field is acceptance evidence without its package-defined preimage and retrievable bytes.

PM-12 runs only after PM-11 v4 is accepted. It produces `bms.project-manager.final-acceptance.v4`, whose seven typed pointers resolve shared-package v4, Development-runtime v2, exact-tree-review v2, health-acceptance v2, restart/reopen v2, browser-acceptance v2, and operator-visual v3 bodies. The exact-tree review resolves three distinct reviewer principals from `bms.project-manager.reviewer-registry.v2`, with one fixed role each for parent/child, schema truth table, and adversarial cross-package review. Every review starts and ends on the final receipt's `outer_package_sha256`, has zero BLOCKER and MAJOR findings, and carries a reviewer-authenticated immutable body. The release integrator cannot occupy a reviewer role. The final receipt is issued only through the protected `bms-pm12-finalizer-v1` writer assertion after every nested digest, source/build identity, reviewer authority, and Christian authorization resolves.

Christian's decision uses owner-enrollment v1, trust-registry v2, issued-challenge v1, consumed-challenge v1, sign-counter v1, authorization v2, and visual-acceptance v3. Owner enrollment records the credential ID plus retrievable protected COSE public-key bytes and binds relying-party ID and allowed origins. Registry credentials are object keys, so credential IDs are unique. The owner-enrollment service alone can create, retire, or replace a key. The authentication service durably issues a one-use challenge that binds audience, nonce, expiry, Christian principal, candidate commit/tree, deployed build, Development-runtime receipt, browser receipt, complete viewport-evidence digest, decision, relying party, origin, registry generation, and credential. It atomically changes the exact challenge revision from `issued` to `consumed`, verifies the raw client-data, authenticator-data, and signature artifacts against the active COSE key, and performs one compare-and-advance write through `bms.operator-sign-counter.v2`. Authorization fails on reuse, expiry, origin/RP mismatch, registry drift, key mismatch, signature failure, absent user presence/verification, or counter conflict. The PM-12 resolver traverses and reconstructs every nested body. Production promotion remains outside these receipts.

### 2.5 Payload ownership and duplication audit

The release stores a versioned `bms.payload-ownership-manifest.v1`. It assigns each canonical payload class to one active authority:

| Payload class | Active authority |
|---|---|
| Molecular sequence/document, construct, primer, PCR, and operation payloads | Mol Bio store and its governed artifacts |
| Local scientific-state, sample, binding, and evidence-assessment payloads | NGS/MolBio domain store |
| Raw signal, reads, alignments, NGS result manifests, QC reports, and instrument observations | Core NGS/instrument store and governed artifact roots |
| Project membership, receipt identity, digests, bounded labels/metadata, lineage, and ELN text | Global `experiments.db` |

`experiments.db` may store stable IDs, schema/version, digest, byte size, media type, lifecycle, semantic role, bounded display metadata, canonical routes, and ELN text. It cannot store canonical sequence/read/alignment/signal/report bytes, base64 encodings, complete domain manifests, or a JSON copy of a domain scientific payload.

Release acceptance emits `bms.payload-ownership-audit.v1.json`. For every canonical payload it records payload class, stable native identity, owner store/root, SHA-256, byte size, all active locations, permitted preservation copies, and pass/fail reason. The audit enumerates database BLOB/TEXT/JSON fields and governed artifact manifests, then fails when canonical bytes or a complete canonical payload appears in more than one active authority. Scheduler staging and sandbox copies are permitted only as marked transient runtime files outside authority roots and must be absent when the no-active-job release audit runs. Backups and exports are permitted non-serving preservation copies only when they remain outside active authority roots, bind the source digest, and appear separately in the audit. The retained audit, manifest, and scanner version enter the release packet.

## 3. Starting-state ledger

This ledger describes source evidence at the 2026-08-20 assessment baseline. `Source present` does not mean accepted. Section 0 and the PM package gates control current status.

### 3.1 Inherited global capabilities

These are reusable prerequisites. They do not count as completion of this domain vertical.

| Capability | Source evidence and open gate |
|---|---|
| Project → Global Experiment → Domain Experiment hierarchy | Source present with immutable revisions and generation-checked lifecycle; Project v2 scope and final two-tier acceptance remain PM-03A/PM-03B work. |
| Project tree, relationship map, inspector, and bounded collections | Source present; PM-06 provenance, cursor, ELN, linked-local-Project, and complete-page acceptance remain open. |
| Verified external receipts and Project attachment | Source present; native trigger denominator and exact-family acceptance remain PM-04/PM-07 work. |
| Launch contexts and canonical return routing | Source present for typed Job submission; prepared v2 handoff and full NGS path remain PM-08A work. |
| Managed global dispatcher and run reconciler | One source implementation exists; conflicts, deferred gaps, terminal reconciliation, and restart acceptance remain PM-10B work. |
| Retry and resubmit APIs | Source present; direct attempt-to-preparation and stale-authority successor semantics remain PM-08A work. |
| Global Dataset aggregates and revision members | Read source present; Project v2 wrappers, active UI, exact paging, and accepted idempotency remain PM-05/PM-06 work. |
| ELN-lite records | Append source present; Project tree/map/inspector/activity visibility and replacement history remain PM-06 work. |
| Worker health | Partial; complete connector lag, verification, resource, backup, and export evidence remains PM-10B work. |
| Manual dispatch | Correctly unavailable with HTTP `409`; the managed worker stays the sole owner. |

### 3.2 Existing NGS/MolBio source

The assessed source contains stable local Domain state, immutable state revisions, membership graphs, samples, managed references, evidence, audit/outbox records, member-receipt resolvers, several global adapters, and a read/reopen Domain workspace. It also contains substantial Project hierarchy, Dataset/history, binding, and exact molecular-reopen code. These are implementation inputs. They do not satisfy N1 through N6 until the package gates prove migrations, authority, active controls, connector convergence, exact negative cases, restart behavior, and retained evidence on one candidate tree.

### 3.3 Current blocking gaps

1. The installed source pin/runtime denominator rejects current Mol Bio Toolkit bytes. PM-02 must restore fail-closed source authority before any dependent mutation package.
2. Current new Project source writes `project_scope` inside a v1 payload whose frozen schema does not declare it. PM-03A must add parent Project v2, preserve historical bytes, and migrate only through deterministic successor rules.
3. Active sample, managed-reference, evidence, Dataset, Plan, and preparation controls remain incomplete. PM-05 owns their operator path after PM-03B/PM-04.
4. Receipt-backed `Add to Project` and literal deep-link consumers remain incomplete across the accepted native denominator. The manifest resolver must accept each listed raw schema/version exactly without inventing equivalence. PM-04 owns this closure.
5. Composite-envelope provenance, ELN visibility, query isolation, deterministic accumulation, and opaque Plan-revision pagination remain incomplete. PM-06 owns one canonical read contract.
6. Several receipt families lack exact accepted adapters, historical reopen negatives, or bounded stale-to-successor re-verification. PM-07 owns closure without current-head substitution.
7. NGS Toolkit submission has not passed the complete Plan → preparation → launch-context → native Job → terminal Result path. PM-08A owns the shared execution contract.
8. Three outbox conflicts, two deferred inbox gaps, worker failure evidence, and incomplete operational health block convergence. PM-10B owns their durable resolution or explicit terminal disposition after PM-10A supplies the connector foundation.
9. Validation artifacts, bounded attempt logs, current backup/restore/export receipts, payload-ownership audit, and shared-package receipt are absent for the final candidate. PM-09/PM-11 own those records.
10. PM-01 technical UI acceptance, PM-12 signed visual acceptance, and the full N6 exact-tree Development scenario remain open. Automated DOM structure or service readiness cannot close the operator gate.

### 3.4 Canonical NGS/MolBio capability denominator

Add one server-owned NGS/MolBio capability inventory. The baseline minimum Project-plannable workflow IDs are:

```text
ngs.ont.basecall_dna
ngs.ont.basecall_rna
ngs.ont.plasmid_qc
ngs.ont.construct_screening
ngs.ont.methylation_analysis
ngs.ont.fastq_qc
ngs.ont.pooled_reference_assignment
ngs.ont.clone_validation
molbio.oligo_design.rfdpoly
```

These map explicitly to the current canonical workflow authorities `ont_basecall_dna`, `ont_basecall_rna`, `ont_plasmid_qc`, `ont_construct_screening`, `ont_methylation_analysis`, `ont_fastq_qc`, `ont_pooled_reference_assignment`, `wf_clone_validation`, and `oligo_design`. Aliases cannot become separate capabilities.

The baseline minimum Project-context domain-operation IDs are:

```text
molbio.sequence.import_revision
molbio.sequence.edit_revision
molbio.sequence.annotation
molbio.sequence.alignment
molbio.sequence.rna_structure_analysis
molbio.primer.design_qc
molbio.pcr
molbio.restriction_digest
molbio.mutagenesis
molbio.assembly.ligation
molbio.assembly.gibson
molbio.assembly.golden_gate
```

The Gibson capability preserves engine roles. DNA Weaver provides purchase planning. pydna provides validation/simulation. Neither silently substitutes for the other. Samples, managed references, comparison panels, instrument observations, receipts, and evidence assessments are typed authorities rather than executable capabilities.

Each inventory record contains canonical ID/version, label, scientific role, allowed Domain modes, workflow/operation mapping, parameter or operation schema, preparation/materializer when scheduled, result/receipt contracts, canonical source and viewer destinations, accepted source roles, exposure state, readiness authority, and inventory digest. Exposure is `accepted`, `experimental`, `internal`, `historical`, or `disabled`.

Phase N0 compares this inventory with every mounted Mol Bio Toolkit panel, NGS Toolkit launcher, canonical ONT workflow, API mutation route, scheduler entrypoint, receipt resolver, and result surface. A visible or server-advertised family cannot remain unclassified. Accepted scheduled workflows pass the full plan-to-result and ten-gate parameter contract. Accepted deterministic domain operations pass strict request/effective-value, receipt, lineage, `Add to Project`, and exact-reopen closure. Incomplete entries remain hidden or historical/read-only.

## 4. Binding and cross-store consistency contract

### 4.1 Server-issued binding receipt

Add one server-owned NGS/MolBio binding adapter with this frozen ID:

```text
bms.ngs-molbio.domain-binding.adapter.v1
```

It resolves the exact global hierarchy and issues one canonical `bms.ngs-molbio.global-binding-receipt.v1` with:

- stable Project ID, current immutable Project revision ID, generation, digest, and reopen destination;
- stable Global Experiment ID, current immutable revision ID, generation, digest, and reopen destination;
- stable NGS/MolBio Domain Experiment ID, immutable revision ID, generation, digest, lifecycle state, and reopen destination;
- `domain_kind=ngs_molbio` and contract version;
- adapter ID/version and verification time;
- one global binding receipt resource ID, canonical receipt digest, and acknowledgement.

The global service persists this combined hierarchy receipt as a Domain-owned `domain_adapter_receipts` resource. It does not create caller-supplied external receipts for global aggregates. The local binding stores the exact combined receipt ID, canonical JSON, and digest.

The caller supplies only stable IDs, the expected Domain Experiment revision ID, and an idempotency key. The server derives all digests, parent identities, lifecycle state, and receipt bodies.

The verifier fails closed when a parent is missing, archived, foreign, stale, unavailable, or digest-divergent. A Domain Experiment that resolves to more than one native target also fails.

### 4.2 Versioned binding decision

Bindings are append-only and versioned.

A local scientific-state revision pins one `binding_revision_id`. A later global Domain Experiment revision creates a successor binding revision. Historical local state keeps its prior binding. Global edits never overwrite old binding authority.

The current single immutable row in `molbio_ngs_global_bindings` becomes the first binding revision during migration. Its ID is UUIDv5 under `uuid.NAMESPACE_URL` from the exact UTF-8 seed `bms:molbio-ngs:legacy-binding-v1:{global_domain_experiment_id}:{global_domain_experiment_revision_id}:{global_domain_experiment_revision_digest}:{project_digest}:{global_experiment_digest}`. Existing Project and Global Experiment receipt fields remain immutable legacy evidence. Migration never invents a valid combined receipt. A migrated binding stays `needs_reverification` until the server re-resolves the hierarchy and appends a verified combined binding receipt. No stable Domain Experiment ID changes.

### 4.3 Bootstrap saga

The global Domain Experiment create or successor-revision transaction performs these operations in this order:

1. write the immutable global Domain Experiment revision;
2. resolve and verify the exact Project → Global Experiment → Domain Experiment hierarchy;
3. persist the canonical combined hierarchy receipt in `domain_adapter_receipts`;
4. persist a durable connector command that references that existing receipt resource ID, canonical body digest, Domain Experiment revision ID, and revision digest.

The receipt and command commit atomically. A local binding can never be asked to reference a receipt that is still prospective.

The managed connector then:

1. loads and re-verifies the persisted global receipt and command;
2. appends the local binding revision idempotently using that existing receipt ID, canonical body, and digest;
3. returns one canonical local acknowledgement under the same command ID;
4. records that acknowledgement on the global command and marks the command applied, duplicate, retryable, or conflicted.

Every step is idempotent under one server-issued command ID. Recovery explicitly reconciles `global-receipt-and-command-only`, `local-binding-present/acknowledgement-missing`, and `acknowledgement-recorded/command-not-finalized` states. A legacy or damaged local-binding-only state can recover only when its referenced global receipt exists and matches byte-for-byte; otherwise it becomes a durable conflict. Recovery never invents, replaces, or silently advances receipt or binding authority.

Project Manager shows `provisioning`, `ready`, or `degraded`. A retry action reuses the same command identity and exact revision. It does not create another Domain Experiment.

### 4.4 Domain-to-global outbox

Use the existing local outbox as a real delivery state machine. The existing `GlobalExperimentWorker` owns one separately leased NGS/MolBio connector lane. A second connector process or worker implementation is forbidden. The connector provides:

- exclusive lease claims;
- token-fenced acknowledgement and failure updates;
- stale-lease recovery;
- bounded retry with next-attempt time;
- durable semantic conflicts;
- restart recovery;
- idempotent global ingestion keyed by event ID and payload digest;
- one canonical acknowledgement body.

The envelope contains:

```text
schema
source_store_id
event_id
event_type
global_domain_experiment_id
binding_revision_id
state_revision_id | null
event_stream
stream_generation
source_generation | null
payload
payload_sha256
occurred_at
```

Ordering is scoped to `(global_domain_experiment_id, binding_revision_id, event_stream)`. `event_stream` is one of `binding`, `state`, `sample:{sample_id}`, `reference:{reference_id}`, `member:{entity_kind}:{entity_id}`, or `evidence:{assessment_id}`. `stream_generation` is the connector sequence. It starts at 1 and increases by exactly 1 for every emitted event in that stream. `source_generation` preserves the native sample/reference/state/instrument generation when one exists. It is never used as the connector cursor.

The global inbox stores `last_applied_stream_generation` and the accepted payload digest for each stream. Delivery behavior is fixed:

- `stream_generation = last + 1` applies transactionally and advances the cursor;
- `stream_generation <= last` with the same event/digest is an acknowledged replay;
- `stream_generation <= last` with different identity or digest is a durable conflict;
- `stream_generation > last + 1` is `deferred_gap` and cannot update a head, receipt, lineage, or read projection;
- an event for a superseded binding revision can fill its own historical stream, but it cannot change current-binding projections;
- revalidation never rewrites a stream cursor. It emits a successor binding revision and new stream scope;
- resolving a semantic conflict requires a new valid native revision/event. Blind replay cannot clear it.

After a missing generation arrives, the managed worker drains consecutive deferred events in generation order under the same token-fenced lease. No event can regress a projected head.

Required event families are binding acknowledgement/health, state revision publication, member receipt publication, sample revision publication, reference revision publication/archive, evidence attachment, and evidence assessment publication.

Manual dispatch and manual reconcile endpoints remain disabled with HTTP `409`.

## 5. Additive persistence work

Observed next versions at the assessment baseline were global V11/V12 and NGS/MolBio V4. Those numbers are historical evidence, not reservations. Immediately before the first migration edit, the NGS/MolBio shared-package owner reads the complete current ledgers and assigns the next unused monotonic numbers to every approved logical migration in dependency order. The mapping from logical ID to numeric version is recorded in the package receipt before parallel implementation. No worker may reuse a number from this document or edit a shared migration file concurrently.

The complete logical migration denominator is:

1. `PARENT-GLOBAL-HIERARCHY-V2` for Project-scope authority, v2 hierarchy schema registration, server-derived ownership constraints, and any additive indexes/columns required for scope-filtered heads;
2. `NGS-GLOBAL-CONNECTOR` for the PM-10A command/inbox/acknowledgement foundation;
3. `NGS-DOMAIN-BINDING` for PM-03B local binding after PM-10A acceptance;
4. `NGS-GLOBAL-PROJECT-LINK-V2` for PM-03B immutable exact-revision local/global association resources after hierarchy and binding authority;
5. `NGS-GLOBAL-DATASET-KIND` for PM-05 Dataset-kind persistence and legacy null-kind rules;
6. `PARENT-GLOBAL-READ-PROJECTIONS` after the counted hierarchy, link, record, Dataset, Plan, run, and result resources exist, for transactionally maintained count/status authority;
7. `NGS-GLOBAL-ATTEMPT-LAUNCH-CONTEXT` only after PM-05, PM-06, and PM-07 acceptance, for PM-08A direct attempt-to-preparation and typed-handoff v2 lifecycle authority.

The shared migration owner may combine adjacent logical migrations in one numeric migration only when their PM prerequisites are identical and the resulting rollback/attestation unit remains exact. It records that decision before edits. It may not reorder dependencies, mutate a prior migration, or hide one logical denominator row. Subsection numbering below is thematic; the ordered denominator above controls execution.

### 5.1 `NGS-GLOBAL-ATTEMPT-LAUNCH-CONTEXT`: attempt and launch-context authority

- Add direct immutable `preparation_id` authority to every run attempt.
- Add nullable historical `preparation_id`, `run_attempt_id`, `launch_fence_epoch`, `launch_fence_token_sha256`, and v2 lifecycle fields to launch-context storage. New `bms.launch-context.v2` rows validate `docs/specs/schemas/launch-context-v2.schema.json`, require one preparation ID plus exact hierarchy revisions, and follow its state-dependent nullability. A typed-handoff context becomes `reserved` for exactly one run attempt and its launch fence before launcher submission. It becomes `consumed` only after exact canonical Job and binding-receipt authority exist.

- Backfill existing attempts from their owning workflow run when the relationship resolves uniquely.
- Keep historical `bms.launch-context.v1` rows byte-for-byte readable. They can support verified legacy attachment/reopening. They cannot launch a new prepared Workflow Plan.
- Reject ambiguous or missing attempt backfill authority. Do not guess a preparation for a historical v1 launch context.
- Rebuild the SQLite tables transactionally when needed to enforce foreign keys, v2 state combinations, unique context-to-attempt binding, monotonically increasing attempt-local fence epochs, and the attempt non-null contract. Every managed or typed native Job authority adds one serialized launch-fence row keyed by attempt and optional context, with exact epoch/token digest, state `open|job_committed|cancelled_before_commit`, and nullable canonical Job/submission-receipt identity.
- Keep attempt IDs, scheduler Job IDs, existing launch-context IDs/receipts, and timestamps unchanged.

Every new typed-launcher handoff uses a server-issued `bms.launch-context.v2` that validates `docs/specs/schemas/launch-context-v2.schema.json` and binds one exact preparation, normalized-request digest, validation receipt ID/digest, Workflow Plan revision, hierarchy revisions/context, source receipt, canonical return URI, issuer, and lifecycle timestamps. Its stable authority field is `workflow_plan_id`; v1 `workflow_id` remains a historical compatibility field only. Issue creates `state=issued` with null fence fields. Run-group creation allocates a monotonically increasing attempt-local `launch_fence_epoch` and a cryptographically random token, stores only its SHA-256 in durable global rows, advances the context to `reserved`, and binds exactly one new attempt. The server opens the matching native fence row and verifies its acknowledgement before launch intent becomes visible. The raw token remains server-to-server authority and never enters a browser or receipt. The global store advances `reserved → claimed` only after that acknowledgement and before launcher submission.

Typed submission is one idempotent cross-store saga keyed by `(run_attempt_id, launch_context_id, launch_fence_epoch)`. The native launcher transaction locks the matching fence row and verifies the supplied token against `launch_fence_token_sha256`. When the row is `open`, that same native transaction either creates exactly one canonical Job plus one immutable native submission receipt and advances the fence to `job_committed`, or processes a serialized cancellation command and advances it to `cancelled_before_commit`. The submission receipt contains attempt/context, fence epoch/token digest, canonical Job ID, normalized native request digest, and source authority. A `cancelled_before_commit` tombstone permanently rejects Job creation. A `job_committed` replay returns the same Job and receipt after verifying every digest. A stale epoch/token, changed request, conflicting Job, or conflicting receipt becomes a durable conflict and cannot mutate the fence.

Cancellation first commits global state `cancelling`, blocks retry/comparison/new dispatch, revokes global launch admission, and sends the exact epoch/token cancellation command. It does not project terminal `cancelled` or release resources yet. The native transaction serializes that command against first Job commit. If cancellation wins, its verified tombstone acknowledgement permits global terminal cancellation with no Job. If Job creation wins, the acknowledgement names that exact Job; the reconciler binds it, invokes its canonical cancellation lifecycle, and waits for terminal acknowledgement before projecting `cancelled`. The global reconciler alone writes the binding/cancellation receipts and advances a successful context to `consumed`. Crash replay uses the native fence state and cannot reverse either winner. Managed materialization uses the same fence protocol with a null context ID before scheduler submission. No other transition, release, expiry reset, or context reuse is valid. Expiry is derived from `expires_at` and does not mutate retained authority bytes. Retry and resubmit require fresh attempts, epochs, tokens, and typed contexts where applicable.

Every retry binds the new attempt directly to one immutable preparation. The service revalidates the source attempt's preparation against current one-time and revisioned authority. It reuses that exact preparation ID only when its normalized request, inputs, settings, binding revision, and validation receipt remain valid and unchanged. A stale or changed authority returns `replacement_preparation_required`; the retry remains undispatched until a successor immutable preparation is created. The successor records `supersedes` from new preparation to prior preparation, and the new attempt binds the successor ID. The workflow-run row is never mutated to conceal this choice. Scheduler materialization reads the attempt's preparation.

### 5.1.1 `NGS-GLOBAL-DATASET-KIND`: Dataset kind authority

Add nullable `aggregate_heads.dataset_kind` with `CHECK (dataset_kind IS NULL OR aggregate_kind = 'dataset')` and an index on `(workspace_id, parent_id, dataset_kind, lifecycle_state)`. Existing non-Dataset rows receive null. Existing Dataset rows retain their current `description` bytes and receive a kind only when that value already equals one exact enabled registry ID; every other existing Dataset receives null. A null-kind legacy Dataset and every exact historical revision remain readable and exportable. It cannot enter a new v2 preparation or receive a new revision. The owner creates a new typed Dataset from reverified member receipts when continued use is required. Migration never infers kind from names, members, or scientific payloads. Every new Dataset requires a non-null enabled registry ID.

### 5.2 `NGS-GLOBAL-CONNECTOR`: domain connector command and inbox

Add only the records needed for this fixed connector:

- durable global-to-domain connector commands;
- idempotent domain-event inbox rows;
- canonical acknowledgements and conflicts;
- lease owner/token/expiry and retry state;
- exact Project, Global Experiment, Domain Experiment, and binding revision references.

Add a server-owned combined hierarchy binding-receipt writer over existing aggregate revisions and `domain_adapter_receipts`. No new generic external-receipt registry is created.

Use existing `domain_adapter_receipts`, external receipts, resources, audit, and lineage tables for accepted authority. Do not create a second global receipt system.

### 5.3 `NGS-DOMAIN-BINDING`: binding revisions and ordered outbox authority

- Convert the current binding row into append-only binding revisions.
- Add a current binding-revision pointer to the stable local Domain state.
- Add `binding_revision_id` to each local state revision.
- Add combined global binding receipt ID, canonical JSON, and digest fields. Retain all legacy Project/Global receipt columns as read-only evidence.
- Add `needs_reverification` as the migration state for a legacy binding that has no server-issued combined receipt.
- Persist `event_stream`, `stream_generation`, and nullable `source_generation` as first-class fields. Enforce one unique `(global_domain_experiment_id, binding_revision_id, event_stream, stream_generation)` tuple.
- For every V1-V3 outbox row, preserve the row ID, event type, payload bytes/digest, status, lease/evidence fields, and timestamps. Assign it to the migrated legacy binding revision. Verify that `payload_sha256` matches the unchanged canonical payload, parse the exact event schema, and derive `event_stream` and `source_generation` from the frozen map below. Within each resulting stream, assign `stream_generation = row_number() over (ORDER BY created_at ASC, id ASC)`, starting at 1. The current initialization event therefore receives stream generation `1` and source generation `0`.
- Because the pinned V1-V3 source has no sanctioned dispatcher, every migratable legacy outbox row must have `status=pending`, null lease/evidence/conflict fields, `retry_count=0`, `next_retry_at=null`, and `last_error=null`. Any other combination blocks the `NGS-DOMAIN-BINDING` migration with a typed `untrusted_legacy_delivery_state` attestation error. The migration never fabricates a global inbox cursor or converts local status into acknowledgement authority.
- Rebuild the outbox table transactionally so the new ordering fields are non-null where required and covered by immutability triggers. Generate the migration mapping twice in a rolled-back dry run and require byte-identical event ID → stream/revision/sequence output before commit.
- Add first-class `sample_revision` and `ngs_molbio_state_revision` member-receipt kinds.
- Enforce legal lease/status/evidence combinations.
- Preserve every V1-V3 SQL body and checksum.
- Preserve all current IDs, member receipts, state revisions, samples, references, evidence, audit events, and outbox events.

Every pre-`NGS-DOMAIN-BINDING` local state revision and outbox row for a Domain receives that Domain's deterministic legacy binding revision ID. The stable Domain state's current binding pointer also starts at that ID. This backfill is non-null and exact. The later server-verified successor binding keeps `supersedes` from new binding revision to legacy binding revision, becomes current only after the combined hierarchy receipt is stored, and does not rewrite historical state or event rows.

The V1-V3 event-to-stream map is exact:

| Legacy event type | Migrated `event_stream` | Migrated `source_generation` |
|---|---|---|
| `molbio_ngs.domain_state.initialized` | `binding` | integer `0` |
| `molbio_ngs.domain_state.revision_saved` | `state` | payload `state_revision_number` |
| `molbio_ngs.sample.created` | `sample:{payload.sample_id}` | exact sample revision's `revision_number`, verified by `payload.sample_revision_id` and digest |
| `molbio_ngs.sample.revision_saved` | `sample:{payload.sample_id}` | exact sample revision's `revision_number`, verified by `payload.sample_revision_id` and digest |
| `molbio_ngs.reference.created` | `reference:{payload.reference_id}` | exact reference revision's `revision_number`, verified by `payload.reference_revision_id` and digest |
| `molbio_ngs.reference.revision_saved` | `reference:{payload.reference_id}` | exact reference revision's `revision_number`, verified by `payload.reference_revision_id` and digest |
| `molbio_ngs.reference.archived` | `reference:{payload.reference_id}` | payload `head_generation` |
| `molbio_ngs.instrument_run_evidence.attached` | `member:ont_instrument_run:{payload.run_id}` | payload `observed_generation` |
| `molbio_ngs.evidence.assessed` | `evidence:{payload.evidence_id}` | null |

Per-event attestation is fixed:

- initialization payload hierarchy IDs and digests must equal the migrated binding row;
- state publication must resolve `state_revision_id` in the same Domain and match `state_revision_number`, payload digest, and membership digest;
- sample events must resolve the stated sample/revision pair in the same Domain and match the revision payload digest;
- reference create/revision events must resolve the stated reference/revision pair in the same Domain and match the canonical FASTA digest;
- reference archive must resolve the same-Domain reference, match `head_generation`, and match the persisted archive timestamp;
- instrument evidence must resolve `payload.receipt_id` to an `ont_instrument_run` member receipt whose entity ID is `payload.run_id`, revision is `payload.observed_generation`, and content digest is `payload.observation_sha256`;
- evidence assessment must resolve the same-Domain assessment and match `payload.wrapper_sha256` and `payload.scientific_assessment`.

A missing field, wrong schema, unknown event type, non-unique record, ownership mismatch, or digest mismatch blocks the `NGS-DOMAIN-BINDING` migration with a typed attestation error. The migration does not infer identity from timestamps or join an audit row by similarity. Successor event types must register one stream-key and source-generation derivation before emission. Runtime code allocates `stream_generation` transactionally from the stream cursor; callers cannot submit it.

### 5.4 `PARENT-GLOBAL-HIERARCHY-V2`: hierarchy authority

Add nullable `project_scope` to the global aggregate head, index it with aggregate kind and lifecycle, and enforce that Project/workspace heads have `global|ngs_molbio_local` while every other aggregate kind has null. Backfill each Project from its exact current revision bytes: a valid historical v1 extension value is preserved; a v1 payload with no scope becomes `global`; invalid, conflicting, or unreadable values block migration. Verify all existing children against the derived scope. A local Project with a non-NGS/MolBio child is a typed migration blocker and is never silently reclassified. Historical revision bytes remain unchanged.

Register the exact Project v2, Global Experiment v2, Research Record v2, hierarchy-revision v1, and parent read-model/continuation v2 specification hashes through the runtime registry in their owning packages. Add nullable `schema_id`, `subject_revision_id`, `subject_revision_sha256`, and `content_sha256` columns to research records when absent. Backfill historical rows as `bms.research-record.v1` without changing their content fields and leave unavailable exact subject/digest authority null. A constraint requires all four authority fields for `schema_id=bms.research-record.v2`; every new append writes them from the verified exact subject revision and canonical record bytes in one transaction. Exact reads, backup, restore, and export revalidate stored v2 content and subject digests. Head/revision writes require payload schema and `project_scope` to agree with the head row. Fresh and upgraded stores enforce identical checks, indexes, and trigger definitions.

### 5.5 `NGS-GLOBAL-PROJECT-LINK-V2`: exact association history

Add one stable head table, one immutable revision table, and normalized ordered Experiment and Result-reference tables. The head table stores stable `link_id`, immutable global/local Project pair, current revision ID, head generation, timestamps, and unique nullable legacy v1 source ID. Enforce one head per Project pair. The revision table stores every field in `bms.ngs-molbio-project-link.v2`, canonical JSON, payload SHA-256, and unique `(link_id, revision_number)`. Each noninitial revision names the exact prior current revision, and one revision can have at most one successor. Ordered Experiment rows store ordinal plus exact Experiment ID/revision/generation/digest and enforce unique Experiment ID per revision. Ordered Result rows store ordinal plus exact receipt ID and enforce uniqueness per revision. No-update/no-delete triggers protect revisions and members.

Initial create inserts revision/head generation `0` atomically. Successor creation uses one conditional head update from the expected generation to `+1`; its revision number and reported head generation equal the new generation. Schema conditionals reject a non-active or predecessor-bearing revision `0` and require a predecessor for every later revision. The service additionally enforces `revision_number == head_generation` and exact predecessor equality with the locked prior head; JSON Schema alone cannot compare those sibling values or resolve history. State, member cardinality, exact Project/Experiment authority, Result lineage, owner, idempotency claim, revision rows, member rows, head CAS, audit row, and read-projection delta commit in one transaction. Reconstructing canonical JSON from normalized rows must reproduce the stored digest on write, exact read, backup, and restore.

A historical v1 lineage edge is never rewritten. The first authorized change derives one deterministic legacy source identity and creates one v2 head with `legacy_source_link_id`; the unique constraint prevents a second conversion. Ambiguous pair ownership, forked edge history, or contradictory v1 bytes blocks conversion.

### 5.6 `PARENT-GLOBAL-READ-PROJECTIONS`: bounded count/status authority

Add a transactionally maintained Project projection keyed by Project ID with projection generation, every exact count/status field required by `bms.project-manager.read-model.v2`, canonical projection JSON, source high-watermarks, content SHA-256, and update time. Every global-store transaction that changes hierarchy, links, research records, Datasets, Plans, attempts, Results, receipts, or reconciliation state updates the affected projection in the same transaction. Domain-local events affect it only after the accepted connector event and global receipt commit. No browser or read route updates the projection.

Migration builds each projection from an exact read snapshot through bounded keyset batches, records the snapshot high-watermarks, and recomputes it independently before commit. Count/status disagreement, missing authority, unsupported legacy state, or digest mismatch blocks migration. The composite read route never scans an unbounded source to repair a missing projection and returns typed `503 read_model_unavailable` on absent or invalid authority. Fresh and upgraded databases must produce equivalent final projection bytes for the same authoritative rows.

Fresh and upgraded databases must attest to equivalent final constraints. Backup and restore must include the new rows and verify foreign keys, trigger bodies, schema manifests, immutable revision bytes, ordered members, and projection digests.

## 6. Global and domain APIs

### 6.1 Extend existing canonical Project APIs

- New Project creation uses parent `bms.project.v2`. New Global Experiment creation uses parent `bms.global-experiment.v2`. Project `project_scope` is required and immutable. `owner`, `created_by`, and metadata-review state are server-derived. Historical v1 handling follows parent sections 7.1 and 7.2.
- The main Project Manager accepts only `global` Projects as hierarchy roots. The NGS/MolBio Project layer accepts both scopes but can author Protein-free local Projects only when scope is `ngs_molbio_local`.
- Domain creation writes outer `bms.domain-experiment.v4` with `domain_contract_version="3"`. The NGS payload remains `bms.ngs-molbio-experiment.v2` with the exact closed fields `experiment_mode`, `scientific_objective`, `planned_capability_ids`, `grouping_intent`, `acceptance_criteria`, and `evidence_plan`. Modes and capability IDs come from the server registry. Grouping, criterion, and evidence wrappers use the strict contracts below.
- Existing outer/domain v1 and v2 revisions remain readable byte-for-byte. A historical marker is never rewritten or silently interpreted as v3 intent. Its first mutation uses the parent `domain-experiment-upgrade` route with every v3 field and one complete registered payload. Ordinary patch, archive, and restore reject a v1/v2 head with `409 domain_contract_upgrade_required`.
- Domain creation returns connector provisioning state.
- Domain patching creates a new immutable global revision and a successor binding command.
- Archive and restore copy only v3 source payloads and keep local scientific history readable. A historical archived head can become active only through the closed complete-v3 upgrade request whose lifecycle status matches its immediate non-archived predecessor. New domain mutations require an active acknowledged binding.

The persisted outer `bms.domain-experiment.v4` object has exactly these fields and types:

```text
schema: const bms.domain-experiment.v4
domain_kind: protein_in_silico | ngs_molbio
domain_contract_version: const "3"
name: string, 1..255 characters
objective: string, max 8192 characters
status: draft | planned | active | analysis | review | completed | blocked | archived
tags: array of unique strings, max 64 items, each 1..64 characters
source_receipt_ids: array of unique receipt resource IDs, max 256
dataset_revision_ids: array of unique exact Dataset revision resource IDs, max 128
created_by: non-empty server-derived actor ID, max 255 characters
change_summary: string, 1..1024 characters
domain_payload: exactly one registered closed payload for domain_kind
domain_payload_canonical_size_bytes: server-derived integer, 2..786432
canonical_size_bytes: server-derived integer, 2..917504
```

The server derives `domain_payload_canonical_size_bytes` from the UTF-8 byte length of RFC 8785 canonical `domain_payload`. It derives `canonical_size_bytes` from the UTF-8 byte length of RFC 8785 canonical complete persisted revision JSON with only `canonical_size_bytes` omitted. Create and patch requests omit both fields. Persistence rejects either exceeded limit before commit and rechecks both values on exact read, preparation, backup, restoration, and export. The 917,504-byte persisted cap leaves a fail-closed envelope budget under the 1 MiB response limit.

All wrapper and nested schemas use `additionalProperties: false`. Stable Dataset IDs and current Dataset heads are invalid in `dataset_revision_ids`. The server verifies that every receipt and Dataset revision belongs to the same Project/Global/Domain authority allowed by the request.

The create request contains exactly `schema`, `domain_kind`, `domain_contract_version`, `name`, `objective`, non-archived `status`, `tags`, `source_receipt_ids`, `dataset_revision_ids`, `change_summary`, and one complete `domain_payload`. It omits server-derived `created_by`, `domain_payload_canonical_size_bytes`, and `canonical_size_bytes`. Actor identity and both byte counts derive from authenticated server authority. The outer schema/kind/version become immutable after create. Patch requests carry `expected_head_generation` plus only mutable outer fields and also omit all three server-derived fields. Any `domain_payload` change supplies one complete replacement payload, and any Dataset-list change supplies the complete ordered exact-revision list. Partial nested merge is forbidden. Responses return the complete persisted `bms.domain-experiment.v4` object, both size attestations, and immutable revision identity. Caller-supplied actor IDs, size attestations, generations, digests, receipt bodies, and canonical relationship fields are rejected.

Outer `dataset_revision_ids` is the sole ordered Dataset authority for NGS/MolBio and Protein Domains. Protein v3 has no second top-level Dataset list. Every Protein target `dataset_member_refs[].dataset_revision_id` must resolve exactly once inside the outer list. Create, patch, exact read, preparation, backup, restoration, export, and replay enforce that containment and ordering before accepting the revision.

The NGS payload contract is:

```text
schema: const bms.ngs-molbio-experiment.v2
experiment_mode: molecular_design | assembly_validation | pcr_validation | sequencing | quality_control | alignment | comparison | analysis
scientific_objective: string, max 8192 characters
planned_capability_ids: array of unique registered IDs, max 64
grouping_intent: array of bms.ngs-molbio.group.v1, max 128
acceptance_criteria: array of bms.scientific-criterion.v2, max 128
evidence_plan: array of bms.evidence-requirement.v2, max 128
```

`bms.ngs-molbio.group.v1` contains exactly `group_id`, `label`, and `members`. `group_id` is a 1..128-character stable ID unique in the payload. `label` is a 1..255-character display string. `members` has 1..256 unique entries. Each member contains exactly `member_kind`, `resource_id`, `role`, and `ordinal`; `member_kind` is `receipt` or `dataset_revision`; `resource_id` is a 1..255-character exact immutable resource ID; `role` is `input`, `sample`, `reference`, `target`, `panel`, `control`, or `comparison`; and `ordinal` is an integer from 0 through 65535 unique within the group.

`bms.scientific-criterion.v2` contains exactly `criterion_id`, `schema_id`, `schema_sha256`, `subject_role`, and `payload`. `bms.evidence-requirement.v2` contains exactly `requirement_id`, `schema_id`, `schema_sha256`, `subject_role`, `required`, and `payload`. Historical v1 wrappers remain read-only. IDs and roles are 1..128 characters. `subject_role` is `input`, `sample`, `reference`, `target`, `panel`, `control`, `result`, `comparison`, `evidence`, or `other`. `required` is boolean. `payload` is an object whose serialized RFC 8785 canonical JSON is at most 64 KiB. Each `(schema_id, schema_sha256)` resolves to one package-bound immutable closed JSON Schema; the digest is SHA-256 of its exact file bytes, and its payload rejects unknown fields. Create, patch, exact read, migration, backup, restore, preparation, and replay verify both values before accepting the Domain revision. Draft intent may use empty capability/criteria/evidence arrays. A transition to `planned` or `active` requires at least one capability, criterion, and evidence requirement.

The `x-bms-unique-by` and `x-bms-unique-ordinal` keywords are documentation only under Draft 2020-12. Acceptance therefore requires the package-bound `bms.project-manager.semantic-validator.v2` contract and the exact implementation artifact named by it. The validator enforces, in order: unique NGS `group_id`; unique NGS member tuple `(member_kind, resource_id, role)` per group; unique NGS member ordinal per group; unique criterion ID; unique evidence-requirement ID; unique Protein comparison member tuple `(target_id, role)` per comparison group; and unique Protein comparison member ordinal per comparison group. It runs after JSON Schema validation at create, Domain upgrade, patch, exact read, migration, backup, restoration, export, preparation, and replay. A duplicate returns `semantic_uniqueness_violation`; no normalized value is persisted or returned. PM-02 binds the exact validator and duplicate-fixture manifest, and PM-11 rejects any candidate whose implementation artifact or adversarial fixture digest differs.

### 6.2 Connector routes

These route names are final:

```text
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/binding
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/initialize
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/binding/reverify
```

`GET .../binding` has no request body. It returns exactly `schema`, Project/Global/Domain stable and revision IDs, `binding_revision_id`, `global_receipt_id`, `global_receipt_sha256`, `connector_command_id`, `normalized_request_sha256`, `command_state`, `acknowledgement_id`, `acknowledgement_sha256`, `local_state_id`, `provisioning_state`, `head_generation`, `created_at`, and `updated_at`. `schema` is `bms.ngs-molbio.binding-status.v1`; `command_state` is `pending`, `leased`, `applied`, `duplicate`, `retryable`, or `conflicted`; and `provisioning_state` is `provisioning`, `ready`, or `degraded`. Pending command values are null. The route returns one exact current binding revision and never substitutes a newer Project or Domain head.

`POST .../initialize` accepts exactly `expected_domain_revision_id`. `POST .../binding/reverify` accepts exactly `expected_domain_revision_id` and `expected_binding_revision_id`. Both require one `Idempotency-Key` HTTP header of 1..255 visible ASCII characters; a body idempotency field is invalid. `initialize` creates or replays the connector command for the named immutable Domain revision. `reverify` re-resolves the full hierarchy and appends a successor binding revision only when the named binding is current; it cannot rewrite the old binding or its local state revisions.

Both mutation routes return `202` with exactly the binding-status object above when processing is pending or retryable, and `200` with that same object for an exact replay or an already applied command. The response always identifies the one command selected by the normalized request. Reusing an idempotency key with a different canonical request returns `409 idempotency_conflict`; a stale Domain or binding revision returns `409 stale_revision`; an acknowledged exact replay returns the prior identity without another command or receipt. Unknown Project/Global/Domain/binding authority returns `404`. Missing or malformed fields, an unknown field, or an invalid header returns `422`. An unavailable sole connector owner returns `503 connector_unavailable`. Digest divergence, foreign hierarchy, archived authority, an ambiguous native target, or a durable connector conflict returns typed `409` and leaves the prior current binding unchanged. The browser cannot submit actor identity, command IDs, receipt bodies, acknowledgements, digests, generations, or lifecycle state.

Initialize uses operation slug `domain-binding-initialize`. Reverify uses `domain-binding-reverify`. For both, `canonical_target_json` contains exactly server-derived `principal_id`, `project_id`, `global_experiment_id`, and `domain_id`; it is serialized as UTF-8 RFC 8785 JSON and its lowercase SHA-256 forms the parent-compatible claim scope `{operation_slug}:{target_sha256}`. The complete initialize request preimage is RFC 8785 JSON containing exactly `operation_slug`, path object `{project_id,global_experiment_id,domain_id}`, and body object `{expected_domain_revision_id}`. The reverify preimage uses the same path and body `{expected_domain_revision_id,expected_binding_revision_id}`. SHA-256 of those exact UTF-8 bytes is `normalized_request_sha256`. After strict syntax normalization and owner authorization, each route resolves `(scope, Idempotency-Key)` before current-head, lifecycle, binding, connector-owner, or native-target validation. Same key and digest returns the stored byte-identical response and original status. A changed digest conflicts. A new claim proceeds and commits atomically with its connector command; a uniqueness race reloads the winner and applies the same rule.

### 6.3 Preserve and complete domain APIs

Keep the current `/api/molbio-ngs` sample, reference, state, member-receipt, evidence, and history routes. Change their authority source from the unavailable stub to the real binding adapter.

Freeze this route-level authorization matrix:

| Mutation class | Required authority | Additional gate |
|---|---|---|
| Domain initialize and binding reverify | persisted Project owner | exact hierarchy, expected Domain revision, idempotency key |
| Domain v4 create/patch, governance/status change, archive, and restore | persisted Project owner | expected head generation, server-derived canonical size attestations, and current binding command where applicable |
| Local state, sample, managed-reference, evidence attachment/assessment, and domain-member-receipt writes | persisted Project owner | current acknowledged binding; exact Domain ownership; idempotency and generation checks where the native contract supports them |
| Project Dataset create/revise/archive/restore and Project attachment | persisted Project owner | exact Project → Global Experiment → Domain Experiment hierarchy; expected head generation for revise/archive/restore; required idempotency key for every mutation |
| Workflow Plan create/revise/prepare, launch-context issue, launch, retry, resubmit, and cancellation | persisted Project owner | active acknowledged binding; immutable preparation and launch authority; managed scheduler ownership |
| Canonical Mol Bio Toolkit mutations that save revisions or operations into this Project | persisted Project owner resolved from opaque Project context | exact source revisions; server-derived Project/Domain attribution; active acknowledged binding |
| Project-scoped NGS Toolkit submission and result/evidence attachment | persisted Project owner resolved from opaque launch context | canonical Job service and exact terminal receipt |
| System-wide panel seeding, backup, export verification, connector administration, and health operations that mutate global state | authenticated operator or admin | dedicated system-operation policy; no Project owner substitution |
| Manual connector dispatch and reconciliation | unavailable | authenticated callers still receive HTTP `409`; managed worker is sole owner |

Every mutation derives the effective actor and roles at the authenticated server boundary. Request bodies cannot supply actor, owner, role, attribution, acknowledgement, canonical digest, or relationship authority. The audit record stores the server-derived principal, effective authorization class, Project ID when applicable, binding revision when required, request identity, and result identity. Missing, stale, foreign, or ambiguous authorization fails closed before any native or global write. Read-only preview or validation routes stay non-authoritative and cannot persist Project state.

Add exact detail/list operations only where a receipt kind lacks an independently retrievable native authority. Current stable routes remain compatible.

All new and completed collections use opaque keyset cursors. Default limit is 50 and maximum limit is 100. Ordering is `(created_at DESC, stable_id DESC)` unless a scientific order is part of the immutable contract. Project-link history uses section 2.2.2 `(revision_number DESC, link_revision_id DESC)` and its snapshot-bound cursor. Dataset members use `(ordinal ASC)` because ordinal is unique within one immutable revision. The opaque member cursor binds `revision_id`, last ordinal, page limit, and the authority-context digest; a cursor from another revision, Project, Domain, or profile is invalid. Each page returns exactly `items`, `next_cursor`, `has_more`, and `total`. `total` is a non-negative integer when the count is available without an unbounded scan and `null` otherwise. Exact Dataset revisions with more than 100 members expose member pages; the revision envelope never embeds an unbounded membership array. Invalid, foreign, or stale cursors fail with `422`.

The Project Manager composite envelope follows parent section 12.9. Its `source_authorities` and `source_digest_set_sha256` cover every hierarchy revision, selection authority, receipt, record, run, and page item used in that exact response. A page cursor and a response digest are different contracts: the cursor proves continuation scope, while the digest proves the authorities represented in the returned page.

One JSON response is capped at 1 MiB before compression. Bounded summaries remain below 256 KiB. A result that would exceed the cap returns a typed `response_too_large` error or a governed artifact/download descriptor. These bounds apply to samples, references, receipts, Datasets, lineage, activity, runs, results, evidence, audit, and history collections. Development acceptance verifies the same limits through the live reverse proxy.

### 6.4 Dataset APIs

Expose these canonical Project API wrappers over the existing Dataset services:

```text
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/revisions
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/revisions
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/revisions/{revision_id}
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/revisions/{revision_id}/members
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/archive
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/restore
```

Dataset create accepts exactly `name`, `dataset_kind`, and `change_summary`. `name` is 1..255 Unicode scalar values after trimming, `dataset_kind` is one exact server-registry ID from section 8 or an incorporated domain SOW, and `change_summary` is 1..1024 Unicode scalar values. The raw UTF-8 body and its canonical JSON form are each limited to 64 KiB. Revision create accepts exactly `expected_head_generation`, `change_summary`, and an ordered `members` array. `expected_head_generation` is a non-negative integer, `change_summary` uses the same 1..1024 bound, and `members` contains 0..10,000 entries. The raw UTF-8 revision body and canonical normalized request are each limited to 1 MiB. An oversized raw or canonical request returns `413 dataset_request_too_large` before persistence.

Each member contains exactly `receipt_id`, `role`, `ordinal`, `media_type`, and `metadata`. `receipt_id` is 1..128 characters, `role` is one adapter-registered value of at most 128 ASCII characters, `ordinal` is a non-negative integer equal to the member's zero-based array position, and `media_type` is null or a normalized value of at most 128 ASCII characters that must equal receipt-derived authority when that authority declares one. Ordered `(receipt_id, role)` pairs are unique. The server resolves native identity, exact revision/generation, content digest, canonical member JSON, byte size, media type, and reopen route from the verified receipt. Caller-supplied native digests, byte sizes, paths, routes, schema identities, canonical member bodies, or relationship authority are rejected.

`metadata` is a closed display-only object with `additionalProperties: false`. It permits only optional `display_label` of 1..255 Unicode scalar values, `group_label` of 1..128, `condition_label` of 1..128, and `tags`, which contains at most 16 unique strings of 1..64 scalar values. Its canonical JSON is at most 2 KiB. Nested objects, binary values, numeric arrays, arbitrary keys, IDs, digests, paths, URIs, receipt bodies, encoded/base64 data, and sequence/read/alignment/signal/report/manifest/structure payloads are invalid. The runtime validator applies the same versioned payload-class rules used by `bms.payload-ownership-manifest.v1` before writing a member. A metadata value that is classified as a canonical scientific payload returns `422 dataset_metadata_payload_forbidden`.

Dataset create, revision create, archive, and restore each require one `Idempotency-Key` HTTP header of 1..255 visible ASCII characters. A body idempotency field is invalid. Archive and restore bodies accept exactly `expected_head_generation` and `change_summary`, under the bounds above. The existing 128-character `idempotency_claims.scope` value is exactly one of:

- `dataset-create:` plus the lowercase SHA-256 of canonical JSON containing server-derived `principal_id`, `project_id`, and `domain_id`;
- `dataset-revision-create:` plus the lowercase SHA-256 of canonical JSON containing server-derived `principal_id` and `dataset_id`;
- `dataset-archive:` plus the lowercase SHA-256 of canonical JSON containing server-derived `principal_id` and `dataset_id`;
- `dataset-restore:` plus the lowercase SHA-256 of canonical JSON containing server-derived `principal_id` and `dataset_id`.

The claim key is `(scope, Idempotency-Key)`.

After strict syntax normalization, server-derived path identity, and owner authorization, the service computes `request_sha256` over the complete canonical request, including path IDs, body, and operation kind. `normalized_request_sha256` is this same digest. It then resolves the claim before current-head, lifecycle, receipt-availability, or native-authority validation. An existing same-key/same-hash claim returns the byte-identical stored `response_json` and original status semantics with the same Dataset or revision ID, even when the first successful mutation advanced the head. Create and revision-create replay as HTTP `201`; archive and restore replay as HTTP `200`. The fixed per-operation success status requires no new status column in `idempotency_claims`. An existing same-key/different-hash claim returns `409 idempotency_conflict`. When no claim exists, the service verifies the current hierarchy, lifecycle, generation, receipts, metadata, and native authority, then records the claim through existing global `idempotency_claims` in the same database transaction as the Dataset mutation. A uniqueness race reloads the winning claim and applies the same replay/conflict rule. Commit failure leaves neither mutation nor claim. A concurrent duplicate cannot create a second Dataset or revision or repeat a lifecycle transition. A stale `expected_head_generation` on a new key returns `409 stale_generation`. Archive of archived state or restore of active state returns `409 invalid_lifecycle_transition`. Missing or malformed keys, unknown fields, invalid member ordering, duplicate members, invalid metadata, and unsupported roles return typed `422` before any Dataset write.

Successful Dataset create returns HTTP `201` with exactly `schema=bms.dataset-head.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, `dataset_kind`, `head_generation=0`, `lifecycle_state=active`, `normalized_request_sha256`, and `created_at`. `NGS-GLOBAL-DATASET-KIND` adds nullable `aggregate_heads.dataset_kind` under the exact migration and legacy rules in section 5.1.1; Dataset creation writes the registry ID there and never stores it only in `description`. The existing `aggregate_created` audit event for a Dataset records exactly `dataset_kind`, `change_summary`, `normalized_request_sha256`, and the server-derived actor ID in addition to stable resource identity. Successful revision create returns HTTP `201` with exactly `schema=bms.dataset-revision.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, immutable `revision_id`, `revision_number`, new `head_generation`, `member_count`, `revision_sha256`, `normalized_request_sha256`, and `created_at`. Its immutable revision payload records `change_summary`. Successful archive or restore returns HTTP `200` with exactly `schema=bms.dataset-head.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, `dataset_kind`, the new `head_generation`, resulting `lifecycle_state`, `normalized_request_sha256`, `created_at`, and `updated_at`. The same transaction records `change_summary` and server-derived actor in the lifecycle audit event. Every route verifies the full Project → Global Experiment → Domain Experiment → Dataset hierarchy and owner authority.

The exact-revision route returns revision metadata plus `members_page` in the common page envelope, with default and maximum limit `100`. Continuation uses the member route and its opaque Dataset/revision/ordering/limit-bound cursor. The route never conditionally replaces members with a URI or embeds an unbounded array. Historical revision retrieval never resolves the current head.

Compatibility workspace routes call the same services.

### 6.5 Canonical Domain Workflow Plan, launch, and result routes

This NGS/MolBio SOW is the single contract and implementation owner for these shared wrappers. Both NGS/MolBio and Protein In Silico consume them with domain-specific capability adapters. Use:

```text
D = /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
```

| Operation | Method and path | Strict request | Success identity | Concurrency and idempotency |
|---|---|---|---|---|
| Attach verified native authority | `POST D/attach` | parent section 11.3.2 closed request: `adapter_id`, `entity_id`, `operation`, `role`, nullable `note`, `expected_project_head_generation`, `expected_global_experiment_revision_id`, `expected_domain_revision_id`, exact `expected_domain_context_id=binding_revision_id` | `bms.global.attachment-receipt.v1`, external receipt ID, lineage-edge ID, exact Global Experiment revision, exact Domain revision/context, normalized request digest, new Project generation | required `Idempotency-Key`; server re-verifies native authority; stale Project, Domain, or binding is `409` |
| List/get plans | `GET D/plans?cursor=&limit=` and `GET D/plans/{plan_id}` | bounded query only | stable plan IDs, heads, current revision, and draft generation | no mutation |
| Create plan | `POST D/plans` | exactly `name`, registered `capability_id`, `expected_domain_revision_id` | `plan_id`, server-derived family/adapter, draft ID/generation `0`, Domain revision | required `Idempotency-Key`; changed request on same key is `409` |
| Replace draft | `PUT D/plans/{plan_id}/draft` | exactly `expected_draft_generation`, complete closed workflow payload | draft ID, new generation, payload digest | required `Idempotency-Key`; generation CAS |
| Publish revision | `POST D/plans/{plan_id}/revisions` | exactly `expected_head_generation`, `expected_draft_generation`, `change_summary` | immutable revision ID/number and payload/dependency digests | required `Idempotency-Key`; both generations match |
| List/get exact revisions | `GET D/plans/{plan_id}/revisions?cursor=&limit=` and `GET D/plans/{plan_id}/revisions/{revision_id}` | bounded query only | exact immutable revision | no head substitution |
| Prepare revision | `POST D/plans/{plan_id}/revisions/{revision_id}/preparations` | exactly ordered `input_dataset_revision_ids` | preparation ID, normalized-request digest, validation ID/receipt digest, status, expected cardinality | required `Idempotency-Key`; creates no Job |
| Get preparation | `GET D/preparations/{preparation_id}` | none | immutable normalized request, requested/effective settings, source authorities, safe scheduler summary, validation | commands/paths omitted |
| Issue prepared handoff | `POST D/preparations/{preparation_id}/launch-contexts` | exactly `return_uri` | `bms.launch-context.v2` bound to preparation, request digest, validation receipt, hierarchy, state, expiry | required `Idempotency-Key`; only adapters declared `typed_launcher_handoff` can use it |
| Launch run group | `POST D/run-groups` | ordered `preparation_launches` using the adapter-mode rule below | `bms.run-group.v1`, group/request/state/generation and ordered run/attempt/preparation plus context or dispatch identities | required `Idempotency-Key`; all bindings commit before launch intent becomes visible |
| Get run group | `GET D/run-groups/{run_group_id}` | none | exact runs, attempts, preparations, handoff contexts, canonical Jobs/runs, receipts, state | no mutation |
| Retry | `POST D/run-groups/{run_group_id}/retry` | `expected_run_group_generation`, ordered `replacements` with run ID, preparation ID, and fresh context only for handoff adapters | fresh attempts with `retried_from` and updated generation | required `Idempotency-Key`; reuse/successor preparation rule applies |
| Resubmit | `POST D/run-groups/{run_group_id}/resubmit` | `expected_run_group_generation`, ordered `preparation_launches` | new group with `resubmitted_from` | required `Idempotency-Key`; source terminal |
| Clone run intent | `POST D/run-groups/{run_group_id}/clone` | `bms.run-clone-request.v1`: exactly `expected_run_group_generation`, `source_run_id`, `source_attempt_id`, `name`, `change_summary`, `expected_domain_revision_id` | `bms.run-clone-receipt.v1` with fresh Plan/draft identity, normalized request digest, and exact `derived_from` lineage | required `Idempotency-Key`; creates no preparation, context, dispatch, or Job |
| Cancel | `POST D/run-groups/{run_group_id}/cancel` | exactly `expected_run_group_generation` and required normalized `reason` | cancellation receipt and updated group/runs/attempts | required `Idempotency-Key`; enters fenced `cancelling`, retains context audit bytes, and blocks retry/comparison |
| Reopen exact result | `GET D/results/{receipt_id}/surface` | none | validated `bms.result-surface.v1` | exact receipt only |
| Create comparison | `POST D/comparisons` | `compatibility_adapter_id`, ordered members with receipt/role/ordinal, `expected_project_head_generation` | `bms.global.comparison-receipt.v1`, native comparison identity, global receipt/digest/edges/surface/new generation | required `Idempotency-Key`; compatibility proves before creation/attachment |

Every mutation row in this table has a closed request. The `Strict request` column is the complete field denominator; nested items are closed by their registered schemas. Every mutation requires one `Idempotency-Key` header of 1..255 visible ASCII characters. The operation slugs are exactly `plan-create`, `plan-draft-replace`, `plan-revision-create`, `workflow-preparation-create`, `launch-context-issue`, `run-group-create`, `run-group-retry`, `run-group-resubmit`, `run-clone`, `run-group-cancel`, and `comparison-create`; attachment uses the parent slug. Canonical target JSON contains the server-derived principal ID and exact Domain ID for Plan, run-group, or comparison creation; Plan ID for draft replacement or publication; Workflow revision ID for preparation; preparation ID for context issue; run-group, source-run, and source-attempt IDs for clone; and run-group ID for retry, resubmit, or cancel. The service computes the normalized request digest from operation, exact path IDs, and canonical body. After syntax normalization and owner authorization, it resolves an existing claim before current generation, lifecycle, source, preparation, resource-admission, or compatibility checks. Same-key/same-digest replay returns the original status and byte-identical response after authority advances. Changed content conflicts. Only a new claim proceeds, and claim plus mutation commit atomically.

Clone validates `docs/specs/schemas/run-clone-request-v1.schema.json`. It verifies that the exact `source_attempt_id` belongs to `source_run_id` and the route run group at `expected_run_group_generation`, and that it resolves to exactly one immutable preparation, Plan revision, Domain revision, requested-settings digest, effective-settings digest, and capability-contract digest. It copies the complete closed source Plan revision payload and requested settings into a fresh editable draft under a new Plan aggregate. No server selection of a latest, successful, or current attempt is permitted.

The response validates `docs/specs/schemas/run-clone-receipt-v1.schema.json`. It binds every source identity, source capability/requested/effective-settings digest, new Plan/draft identity, copied payload digest, normalized request digest, and server actor/time. Clone writes exactly one lineage edge using the existing `derived_from` mode: `lineage_source_resource_id` is the new draft resource ID, `lineage_target_resource_id` is the exact source immutable Workflow Plan revision resource ID, and `lineage_edge_key=cloned-plan-intent`. The service verifies those sibling equalities and stores the returned `lineage_edge_id` in the same transaction as the draft, receipt, audit row, and idempotency claim. `receipt_sha256` is SHA-256 of RFC 8785 canonical receipt JSON with only `receipt_sha256` omitted. The draft contains no Job, attempt, context, resource assignment, terminal state, or browser-authored authority. Until edited, publishing and preparing it under the same capability-contract digest must reproduce the source requested settings and normalized effective-settings digest; changed registry authority fails closed and requires explicit revision work.

Cancellation `reason` is a required UTF-8 string normalized to Unicode NFC after trimming leading and trailing Unicode whitespace. The normalized value must contain 1..1024 Unicode scalar values; whitespace-only input, control characters other than horizontal space, or changed normalized bytes fail `422`. The canonical body and receipt contain only that normalized value.

A submitted `return_uri` is a navigation hint, never authority. It must match one registered same-origin relative-route template for the exact Project/Global/Domain hierarchy, begin with one `/`, contain no scheme, authority, backslash, control character, dot segment, encoded path separator, or fragment, and use only the template's allowlisted query keys. The server reconstructs and stores the canonical URI from verified IDs. A mismatch returns `422`; no redirect or launch-context row is created.

Plan-head listing uses `(created_at DESC, plan_id DESC)`. Exact Plan revisions use scientific order `(revision_number DESC, revision_id DESC)`. Their cursor is an opaque authenticated encoding of `project_id`, `experiment_id`, `domain_id`, `plan_id`, collection kind, last revision number, last revision ID, page limit, and authority-context digest. A bare integer revision cursor is invalid. The list/get operations return the common `items`, `next_cursor`, `has_more`, and `total` page envelope without substituting a current head for an exact revision; `total` is null when a bounded count is unavailable.

`preparation_launches` has 1..128 unique preparation IDs. The registry assigns exactly one mode to each adapter: `managed_materialization` or `typed_launcher_handoff`. A managed item omits `launch_context_id`; a handoff item supplies one v2 context bound to the same preparation. Both allocate a fresh attempt fence under section 5.1. No scheduler or launcher intent becomes visible until the matching native `open` fence acknowledgement is verified. The dedicated launcher or managed dispatcher must present the exact epoch/token to the native serialization transaction. Cancellation and submission race only there. A `cancelled_before_commit` tombstone suppresses submission permanently. A winning `job_committed` row forces reconciliation and canonical Job cancellation before terminal global projection. No context or fence can cross an attempt, retry, resubmit, run, Domain, or Project.

The draft is one complete closed `bms.workflow.<family>.v1` payload. The capability inventory derives and fixes family, adapter, allowed model/mode, launch mode, and result contracts. The shared wrapper accepts only the registered existing workflow fields: schema, family, contract version, adapter, nodes, edges, parameters, scheduler, stage/backend where applicable, source receipt IDs, expected cardinality, and dependencies. Capability schemas close `parameters` and `scheduler.params`. Commands, paths, scripts, imports, arbitrary model IDs, actor identity, digests, receipt bodies, canonical Job IDs, effective settings, and caller-authored lineage fail before persistence.

New planned handoffs use the closed `bms.launch-context.v2` schema from section 5.1 with exact hierarchy revisions/context, Workflow Plan/revision, preparation ID, normalized-request digest, validation receipt ID/digest, source receipt, reconstructed return URI, issuer, lifecycle timestamps, and nullable state-dependent attempt/Job/binding fields. Historical v1 stays readable and cannot launch a prepared plan. Existing dedicated launchers remain native submission authorities and must match every immutable preparation field before canonical submission. Managed materializers read the attempt's direct preparation authority.

All wrappers use `additionalProperties: false`, persisted Project-owner authorization, full hierarchy verification, and bounded responses. Unknown/foreign authority is `404`; stale generation, idempotency conflict, expired/consumed context, digest mismatch, or incompatibility is `409`; malformed/unsupported input is `422`; unavailable single managed owner is `503`. Manual dispatch and reconciliation stay HTTP `409`. `/api/experiment-workspaces` compatibility routes call these services and do not define a second contract.

### 6.6 Resource-admission authority

The NGS-owned shared managed-workflow admission service owns one durable allocation ledger across all active managed BMS workflow children on the deployment. It atomically reserves effective CPU threads and DRAM before dispatch or typed-launcher submission. `pending`, `dispatching`, `queued`, `running`, and `cancelling` allocations count. Release occurs exactly once only after a verified native `cancelled_before_commit` acknowledgement, a winning canonical Job's terminal cancellation acknowledgement, or another canonical terminal result. A retry cannot overlap a live predecessor reservation. A launch that would exceed 24 threads or 96 GiB DRAM fails as `resource_admission_denied`; it is not queued and its request is not reduced. The ledger records policy source/version, owner/lease, Project/Domain/plan/preparation/attempt/fence identities, effective request, state, timestamps, release reason, and recovery evidence. Health reports aggregate reserved versus actual usage. Phase N0 reuses exact existing enforcement if found; otherwise this ledger and gate are new shared work.

### 6.7 Backup and isolated restoration authority

PM-10B produces one `bms.project-manager.backup-receipt.v4` that validates `docs/specs/schemas/project-manager-backup-receipt-v4.schema.json`. Failed-candidate v1/v2 receipts stay inert. The exact ordered store denominator is `global-experiments`, `molbio-domain`, `molbio-ngs-domain`, `core-ngs`, and `biomodstack-native`, where the last store is the managed `biomodstack.db` authority. The exact ordered root denominator is `global-project-artifacts`, `molbio-governed-artifacts`, `ngs-governed-results`, and `bms-native-results`, where the last root is managed `bms_results`. The receipt also has one ordered coverage row for each `project-manager`, `ngs-molbio`, `protein`, `conformational-mapping`, `molecular-dynamics`, `frustrampnn`, `structure-prediction`, and `trajectory` family.

The owner uses SQLite online-backup snapshots after a resolved `bms.project-manager.quiescence.v1` fence proves zero active Jobs, connector leases, and open launch fences. `runtime_implementation_sha256` is SHA-256 of the raw bytes at exact candidate-tree path `platform/api/config/ngs_molbio_runtime/runtime_implementation_v1.json`. Every database snapshot digest and archive digest is SHA-256 of raw artifact bytes. The runtime record supplies each store's exact database identity, migration-ledger table, ordered primary-key columns, and attested object-count table set; each store row binds that registry entry by `store_binding_sha256`.

`sqlite-schema-rfc8785-v1` executes a read transaction over the restored or snapshotted database, reads `type`, `name`, `tbl_name`, and exact nullable `sql` from `sqlite_schema` for non-`sqlite_%` tables, indexes, triggers, and views, orders by UTF-8 bytes of `type`, `name`, `tbl_name`, then nullable `sql`, encodes the ordered array as RFC 8785 JSON, and hashes those bytes. `sqlite-trigger-set-rfc8785-v1` uses the same procedure restricted to triggers. `sqlite-attested-table-typed-rows-v1` reads every column from the exact runtime-attested ledger table, orders by its attested primary-key columns under SQLite binary collation, and represents each cell as `{t:"null"}`, `{t:"integer",v:"<base-10>"}`, `{t:"real",v:"<16-lowercase-hex IEEE-754 binary64 big-endian bytes>"}`, `{t:"text",v:"<exact UTF-8 string>"}`, or `{t:"blob",v:"<unpadded base64url raw bytes>"}`. Each row object contains table name, ordered column names, typed values, and typed primary-key values. The digest frame is unsigned 64-bit big-endian canonical-row byte length followed by RFC 8785 row bytes for every row. Missing/duplicate keys, non-finite reals, invalid UTF-8, query drift, or a changed runtime binding fails. `sqlite-count-by-attested-table-v1` records exact `COUNT(*)` for every bound table in UTF-8 table-name order.

Every root uses retrievable `bms.project-manager.file-manifest.v2` bytes validated by `docs/specs/schemas/project-manager-file-manifest-v2.schema.json`. File keys are bounded non-empty Unicode strings. The schema rejects absolute paths, empty path components, dot/dot-dot segments, trailing separators, backslashes, NUL, C0 controls, and DEL. The semantic verifier rejects non-NFC, path escape, symlinks, link count other than one, and non-regular files after no-follow confined open. It sorts NFC path UTF-8 bytes ascending. For each file it hashes path bytes, NUL, unsigned 64-bit big-endian size, and raw 32-byte file SHA-256 into `ordered_files_sha256`. It verifies count and total size. Manifest `content_sha256` is SHA-256 of UTF-8 RFC 8785 canonical JSON with only itself omitted.

Before restoration, `bms.project-manager.historical-authority-denominator.v2` scans every retained schema/version through runtime-attested keyset queries over the coherent snapshot set. It records an object-keyed schema-version map and an object-keyed case map. The mandatory baseline contains Project v1, Global Experiment v1, Domain v1/v2, Protein payload v1/v2, and Research Record v1. Every additional observed historical schema/version is added; zero-case rows are invalid. Each case binds schema/version, authority family, resource kind, store, stable and exact revision IDs, raw persisted-byte digest, registered route template and complete parameters, response-size bound, and expected complete canonical source-snapshot response digest. The extractor signs the denominator before the restore verifier receives any destination identity.

`sha256-rfc8785-backup-denominator-v3` hashes RFC 8785 JSON for an object containing exactly the complete ordered `stores`, `artifact_roots`, `authority_family_coverage`, resolved historical-authority denominator body, quiescence pointer, cross-store high-watermarks, snapshot timestamps, runtime-record identity, and source commit/tree. No field is omitted. The `bms-backup-snapshot-verifier-v1` principal resolves source snapshots and expectations and signs the backup body through the package operational-evidence contract. WAL files or live database copies are never coherent snapshots. The receipt self-digest omits only itself.

PM-10B restores that backup into separately named empty stores and roots that are not mounted by Development or Production. The result validates `docs/specs/schemas/project-manager-restoration-receipt-v4.schema.json`. Each store row names the source snapshot identity/digest from the resolved backup and one verifier-computed restored raw-file digest, size, schema, triggers, ledger, integrity, foreign keys, and counts. Each root row names the source manifest identity/digest and a newly reconstructed restored v2 manifest. A retrievable destination-identity artifact proves the isolated storage identity. The `bms-isolated-restoration-verifier-v1` principal is distinct from `bms-backup-snapshot-verifier-v1`; their protected key-object digests must differ, and the PM-11 resolver rejects one principal or key on both sides.

The isolated drill reconciles the exact four high-watermark integers with zero conflicts or gaps. `high_watermarks_sha256` is SHA-256 of their complete RFC 8785 canonical object. Isolated process sets and restart command validate `bms.project-manager.isolated-restoration-reopen.v2`; they contain no Development deployment or PM-12 restart authority. After isolated restart, the distinct restore verifier invokes every denominator case's exact route and parameters and records a complete HTTP observation. PM-11 requires object-key equality between denominator cases and observations, exact schema/version/raw-persisted identity, route equality, and expected/observed canonical response digest and size equality. Current-head substitution, route drift, missing or extra keys, response over 1 MiB, writer overlap, or a Development build field fails. The restoration v3 body points to this isolated receipt and uses the same self-digest omission rule. `bms.project-manager.restart-reopen-acceptance.v3` remains exclusively downstream in PM-12.

### 6.8 Verified whole-Project export authority

PM-10B produces one `bms.project-manager.export-receipt.v4` that validates `docs/specs/schemas/project-manager-export-receipt-v4.schema.json`. Failed-candidate v1/v2 receipts stay inert. The export binds one exact Project revision/generation/digest and candidate commit/tree. Its five ordered store rows, four ordered root rows, and eight ordered family rows use the backup denominator; an empty family reports zero. It identifies one immutable raw-byte-hashed archive, its byte size, and a `bms.project-manager.file-manifest.v2` body with `root_id=project-export`.

Each store export materializes selected rows as the same typed-cell row objects defined above, adding authority family, resource kind, stable ID, and exact revision ID. It sorts by UTF-8 bytes of that authority tuple and then table plus typed primary key. `sha256-length-prefixed-sqlite-typed-rows-v1` hashes each unsigned 64-bit big-endian canonical-row length and RFC 8785 row bytes. Each root row resolves a v2 selected-files manifest, so `ordered_files_sha256`, count, and size are reconstructed rather than accepted as loose values. The verifier retrieves the archive and every manifest by confined no-follow reads, proves Project revision or governed-lineage ownership, and reopens every exported authority.

The v3 destination is an exact storage identity classified `governed-offline-archive`. `bms-export-isolation-verifier-v1` records a canonical destination-identity artifact and nine positional comparisons against all five active stores and four roots. Every comparison requires disjoint path ancestry, unequal device/inode identity, absence from Development and Production mounts, and zero serving listeners. Finalized storage is read-only and immutable. The export fails when the destination is under, above, equal to, mounted with, or served from an active authority. The receipt has no `verified` boolean; its self-digest omits only itself. PM-11 accepts only a typed v3 export pointer whose body, destination evidence, archive, manifests, and source identity all reconstruct.

## 7. Adapter closure

Each adapter must search, verify, issue a digest-bound receipt, map lifecycle, provide an exact native route, emit typed upstream lineage, and support Project attachment.

| Native family | Current global coverage | Required SOW result |
|---|---|---|
| Local scientific-state revision | Missing | Add domain-state revision adapter with binding, payload, and membership-graph digests |
| Sample revision | Missing | Add dedicated adapter with stable sample ID, exact revision, payload digest, and Domain ownership |
| Molecular revision | Direct native adapter present; local `molecular_revision` member-receipt contract is not accepted exactly | Keep direct adapter; add exact member-receipt adapter and prove the sequence/revision identity crosswalk |
| Construct revision | Direct native adapter present | Keep; preserve construct classification and operation lineage. A local `molecular_revision` receipt does not become a construct receipt without native classification proof |
| Primer revision | Missing | Add dedicated adapter |
| PCR experiment revision | Missing | Add dedicated adapter |
| Molecular operation | Direct native adapter present; local `molecular_operation` member-receipt digest contract differs | Keep direct adapter; add exact member-receipt adapter and verify ordered input/output digest authority |
| Expected-reference receipt | Present as a separate one-time native authority | Keep one-time handoff and consumer identity; do not relabel it as a domain external-member receipt |
| NGS reference revision | Missing | Add dedicated adapter with exact FASTA/artifact digest |
| Approved comparison panel | Missing | Add dedicated adapter using panel ID, version, and snapshot digest |
| ONT instrument run | Direct current-head adapter present; local observed-generation receipt is not accepted exactly | Keep direct adapter; add exact observed-generation member-receipt adapter and terminal-manifest verification |
| NGS Job receipt | Local `ngs_job` receipt present; global `ngs_analysis_job` is a separate terminal-analysis projection | Add dedicated `bms.ngs.job-reference.adapter.v1` for entity kind `ngs_job`; verify `bms.core.ngs-job-launch.v1`, `model_id=nanopore`, canonical workflow ID, exact launch digest, and `/ngs?job_id=` reopen identity |
| NGS result manifest | Missing; direct sequence-QC adapter uses Job identity and its own manifest contract | Add dedicated manifest-receipt adapter with exact manifest identity, schema, bytes, and owner Job verification |
| Reference set | Present as direct core authority | Keep complete member authority |
| Pooled assignment release | Present as direct core authority | Keep assignment, release, child, and reference-set lineage |
| Sequence QC | Present as direct core authority | Keep strict manifest and workflow-owner checks; it cannot consume a local `ngs_result_manifest` receipt by implication |
| Workflow-aware NGS analysis | Present as direct core authority | Keep; reject wrong workflow or manifest owner |
| Alignment source/session | Present as direct core authority | Re-resolve viewer session from canonical job/reference/QC identity |
| NGS evidence assessment | Missing | Add dedicated immutable assessment adapter |

The missing adapter IDs are frozen as follows:

| Entity kind | Adapter ID |
|---|---|
| `ngs_molbio_state_revision` | `bms.ngs-molbio.state-revision.adapter.v1` |
| `sample_revision` | `bms.ngs-molbio.sample-revision.adapter.v1` |
| `primer_revision` | `bms.molbio.primer-revision.adapter.v1` |
| `pcr_experiment_revision` | `bms.molbio.pcr-experiment-revision.adapter.v1` |
| `ngs_reference_revision` | `bms.ngs.reference-revision.adapter.v1` |
| `ngs_comparison_panel` | `bms.ngs.comparison-panel.adapter.v1` |
| `ngs_job` | `bms.ngs.job-reference.adapter.v1` |
| `ngs_result_manifest` | `bms.ngs.result-manifest.adapter.v1` |
| `ngs_evidence_assessment` | `bms.ngs-molbio.evidence-assessment.adapter.v1` |

The existing `bms.ngs.analysis-reference.adapter.v1` remains the terminal workflow-aware `ngs_analysis_job` adapter. It cannot issue launch-authority `ngs_job` receipts. Generic adapters cannot stand in for any listed native receipt family.

### 7.1 Normative accepted-receipt crosswalk

The local external-member envelope is `bms.molbio-ngs.external-member-receipt.v1`. Each row below defines the exact native contract that a dedicated global adapter must accept. `Native key` is the complete lookup tuple, even when the local envelope stores one component in `entity_id` and the rest in the typed reopen selector. `Digest` names the bytes or canonical object hashed with SHA-256. Allowed state-member roles are exact.

| Local `entity_kind` | Source schema | Native key and revision/generation | Digest | Allowed local roles | Global adapter ID/version | Exact reopen parameters | Baseline acceptance |
|---|---|---|---|---|---|---|---|
| `ngs_molbio_state_revision` | `bms.molbio-ngs.domain-state-revision.v1` | `(global_domain_experiment_id, state_revision_id)`; revision number and binding revision ID | canonical state payload plus ordered membership-graph authority, bound separately in receipt metadata | Dataset/result authority only; not a state member of itself | `bms.ngs-molbio.state-revision.adapter.v1` | `global_domain_experiment_id`, `state_revision_id` | Missing |
| `sample_revision` | `bms.molbio-ngs.sample-revision.v1` | `(global_domain_experiment_id, sample_id, sample_revision_id)`; sample revision number | canonical sample payload bytes | sample authority referenced through `sample_revision_id`; no current baseline member-receipt role | `bms.ngs-molbio.sample-revision.adapter.v1` | `global_domain_experiment_id`, `sample_id`, `sample_revision_id` | Missing |
| `molecular_revision` | `bms.molbio.molecular-revision.v1` | `(sequence_id, revision_id)`; molecular revision number | canonical normalized nucleotide sequence bytes | `molecular_expected_construct`, `molecular_input_fragment`, `molecular_assembly_product`, `molecular_pcr_template`, `molecular_pcr_product` | `bms.molbio.member-molecular-revision.adapter.v1` | `sequence_id`, `revision_id` | Missing; current `bms.molbio.revision-reference.adapter.v1` uses `molbio_revision` plus a composite entity ID |
| `primer_revision` | `bms.molbio.primer-revision.v1` | `(primer_id, revision_id)`; primer revision number | canonical normalized primer-sequence bytes | `molecular_primer_forward`, `molecular_primer_reverse` | `bms.molbio.primer-revision.adapter.v1` | `primer_id`, `revision_id` | Missing |
| `pcr_experiment_revision` | `bms.molbio.pcr-experiment-revision.v1` | `(experiment_id, revision_id)`; PCR revision number | canonical JSON of the complete immutable PCR revision row | `molecular_pcr_experiment` | `bms.molbio.pcr-experiment-revision.adapter.v1` | `experiment_id`, `revision_id` | Missing |
| `molecular_operation` | `bms.molbio.molecular-operation.v1` | `operation_id`; revision marker `event` | canonical JSON containing complete operation row plus ordered input and output rows | `molecular_operation` | `bms.molbio.member-operation.adapter.v1` | `operation_id` | Missing; current `bms.molbio.operation-reference.adapter.v1` uses `molbio_operation` and a different normalized digest contract |
| `ngs_reference_revision` | `bms.molbio-ngs.reference-revision.v1` | `(global_domain_experiment_id, reference_id, reference_revision_id)`; reference revision number | canonical FASTA bytes/content authority under the native reference contract | `ngs_reference` | `bms.ngs.reference-revision.adapter.v1` | `global_domain_experiment_id`, `reference_id`, `reference_revision_id` | Missing; the current registry's `revision_id` reopen alias is incompatible and must be removed before acceptance |
| `ngs_comparison_panel` | `bms.ngs.approved-comparison-panel.v1` | `(panel_id, panel_version)` | immutable panel snapshot/manifest bytes identified by `snapshot_sha256` | `ngs_comparison_panel` | `bms.ngs.comparison-panel.adapter.v1` | `panel_id`, `panel_version` | Missing |
| `ont_instrument_run` | `bms.ont.instrument-run-observation.v1` | `(run_id, observed_generation)` | canonical observation object for that exact event, including terminal manifest digest only when owned by that generation | `ngs_instrument_run` | `bms.ngs.ont-observation.adapter.v1` | `run_id`, `observed_generation` | Missing; current `bms.ngs.ont-run-reference.adapter.v1` resolves current run state |
| `ngs_job` | `bms.core.ngs-job-launch.v1` | `job_id`; revision marker `launch` | canonical launch object with Job identity, workflow/model/mode, inputs, parent/source identities, and created time | `ngs_analysis_job` | `bms.ngs.job-reference.adapter.v1` | `job_id` | Missing |
| `ngs_result_manifest` | `biomodstack.construct_verification.v2`; canonical `sequence_qc.manifest.v1` with `artifact_schema_version` 1 or 2; or a schemaless legacy artifact version 1/2 normalized explicitly to `bms.sequence-qc.manifest.v1` or `.v2` | `(job_id, manifest_identity)`; revision marker `result-manifest` | exact manifest file bytes | `ngs_analysis_result_manifest` | `bms.ngs.result-manifest.adapter.v1` | `job_id`, `manifest_identity` | Missing; the current member-receipt resolver rejects schema-present canonical sequence-QC manifests, while current sequence-QC and analysis adapters remain direct Job projections |
| `ngs_evidence_assessment` | `bms.molbio-ngs.ngs-evidence-receipt.v1` | `(global_domain_experiment_id, evidence_id)`; immutable revision `1` | canonical evidence wrapper bytes identified by `wrapper_sha256` | `ngs_verification_assessment` | `bms.ngs-molbio.evidence-assessment.adapter.v1` | `global_domain_experiment_id`, `evidence_id` | Missing |

The one-time `bms.molbio.ngs-receipt.v2` expected-reference authority, direct `bms.ngs.reference-set-reference.adapter.v1`, direct `bms.ngs.pooled-assignment-release.adapter.v1`, direct sequence-QC/analysis/alignment adapters, and construct-classification adapter remain separate native contracts. They do not silently accept a local envelope with a different `entity_kind`, key, revision field, digest, or schema.

A family changes from `Missing` to `Present` only after the registered adapter accepts the exact row above and an integration check proves `resolve local receipt → verify adapter → attach → persist global receipt → restart services → reopen the exact historical authority`. The test must reject wrong stable ID, wrong revision/generation, wrong Domain owner, wrong schema, changed bytes, changed digest, incomplete key, and a current-head substitution. Any implementation change to a row requires a versioned adapter or schema successor and a matching SOW/release-matrix update.

## 8. Immutable Dataset contract

A Dataset is a stable global aggregate with append-only revisions. The canonical membership lives in `dataset_revision_members`.

Each member stores:

- global receipt ID;
- native stable identity;
- exact native revision or generation;
- member content digest;
- semantic role and ordinal;
- media type and bounded metadata when applicable;
- canonical member JSON and its SHA-256 digest.

The Dataset does not copy molecular or sequencing payloads.

Required NGS/MolBio Dataset registry rows are:

| Exact `dataset_kind` ID | Meaning |
|---|---|
| `ngs_molbio.molecular_construct_cohort.v1` | Molecular input and construct cohort |
| `ngs_molbio.sample_cohort.v1` | Sample cohort |
| `ngs_molbio.reference_comparison_panel_cohort.v1` | Expected-reference or comparison-panel cohort |
| `ngs_molbio.acquisition_run_input_cohort.v1` | Acquisition/run input cohort |
| `ngs_molbio.qc_analysis_result_cohort.v1` | QC and analysis result cohort |
| `ngs_molbio.saved_review_comparison_cohort.v1` | Saved review or comparison cohort |

The registry owns the exact ID, display label, allowed Domain kinds, allowed member receipt kinds and roles, minimum and maximum member count, and compatibility rules. Every row in this SOW and the incorporated Protein table uses minimum `0` and maximum `10,000`; each member role and receipt kind must also be accepted by the exact adapter or compatibility contract named by the Domain capability. No registry row can broaden an adapter's accepted receipt contract. An unknown, disabled, wrong-Domain, wrong-role, or wrong-receipt-kind request returns `422 unsupported_dataset_kind` or `422 unsupported_dataset_member`. A Dataset kind is immutable after creation. New kinds require an additive versioned registry row; they cannot reuse an existing ID with changed member semantics.

A Workflow Plan pins Dataset revision IDs. Resolving a current Dataset head during preparation is forbidden. Reopening an older Dataset revision retrieves its persisted member rows without consulting the current head.

## 9. Lineage rules

Direction is fixed as follows:

| Edge | Stored direction |
|---|---|
| `derived_from` | derived receipt or result → immediate immutable source receipt |
| `compared_with` | comparison authority → each compared immutable member |
| `uses_input` | preparation or activity → Dataset revision or source receipt |
| `produced` | run attempt or activity → output/result receipt |
| `validated_by` | scientific subject → evidence or validation receipt |
| `references` | local state revision → immutable member receipt |
| `retried_from` | new attempt → immediate prior attempt |
| `resubmitted_from` | new run group → source run group |

Every `compared_with` edge includes `role=reference|target|panel|control`, ordinal, compatibility contract ID, and source digest. The global UI may render it as a comparison relationship. Storage remains directed from the comparison record.

Clone does not add a second lineage vocabulary. It uses `derived_from` from the fresh Plan draft resource to the exact immutable source Workflow Plan revision, with `edge_key=cloned-plan-intent` and the edge identity bound into `bms.run-clone-receipt.v1`.

The required visible chain is:

```text
molecular/construct revision
→ expected-reference or managed-reference revision
→ local scientific-state revision
→ Dataset revision or preparation
→ ONT acquisition / NGS Job
→ terminal manifest
→ QC / alignment / comparison
→ evidence assessment
→ observation
→ conclusion
```

The connector creates a cross-receipt edge only when the related native identity resolves to exactly one attached receipt with the expected digest.

## 10. Workflow Plan and launch integration

- Register explicit NGS workflow adapters for each supported top-level NGS workflow family.
- Keep MolBio editor operations as domain operations unless they already use a scheduler-owned Job.
- Keep RFDpoly/oligo design as a typed molecular-design workflow. Its accepted outputs become immutable molecular revisions before downstream NGS use.
- Preparation revalidates binding revision, local state revision, member receipts, Dataset revisions, and one-time handoffs.
- Prepare creates no Job.
- Launch requires explicit operator action and one idempotency key.
- For `typed_launcher_handoff`, Project Manager issues one opaque v2 launch context, NGS Toolkit redeems it, restores exact hierarchy/binding/local-state/return authority, and submits through the crash-recoverable native-receipt saga in section 5.1.
- For `managed_materialization`, the request omits `launch_context_id`; the run attempt binds the immutable preparation directly and creates exactly one managed dispatch intent. NGS Toolkit performs no context redemption.
- Both modes share one explicit-launch idempotency claim and one canonical run/attempt lineage contract. A capability has exactly one registered mode.
- A typed native submission receipt and the later verified global binding receipt preserve context identity; a managed canonical Job preserves direct preparation/attempt identity.
- Terminal reconciliation publishes verified result receipts and Project lineage.
- Retry creates a fresh attempt bound to an immutable preparation. It revalidates one-time and revisioned preparation authority before dispatch.
- Cancellation stops retries and comparisons through the existing Job lifecycle authority.
- Expose workflow GPU selection through the same typed UI/API when the selected NGS workflow uses a GPU. The scheduler validates the allowed selected device set and records the actual GPU UUID/index in the runtime receipt.
The scheduler validates selected devices against live capability. The NGS-owned shared admission service enforces the 24-thread aggregate CPU and 96-GiB aggregate DRAM limits under section 6.6's active-allocation and fail-closed rules. This aggregate ledger is new work unless Phase N0 identifies an exact existing authority. The runtime receipt stores the actual GPU UUID/index and active CPU/DRAM allocation evidence.

Instrument control remains domain-owned. A Project launch context never authorizes a MinKNOW or device action by itself.

### 10.1 Scientific-setting operator and agent parity

Every accepted NGS/MolBio workflow and every model-backed child stage follows `docs/Model_Configuration_Operator_Control_and_Agent_Parity.md`. Phase N0 inventories the exact installed capability and parameter surface for ONT basecalling, demultiplexing, QC, reference assignment, alignment, variant/methylation analysis, PCR/assembly analysis, RFDpoly/oligo design, and each other advertised top-level family. A capability is incomplete while an output-affecting setting remains hidden, raw-JSON-only, browser-only, agent-only, or silently fixed in workflow code.

One versioned server-owned parameter schema per capability/model defines the stable key, model-native mapping, type, default, bounds or enum, units, precision, scientific meaning, applicability, incompatibilities, reproducibility effect, UI control, persisted representation, and supported model/runtime range. Unknown fields and unsupported combinations fail before preparation. Internal paths, credentials, container construction, artifact roots, and scheduler-selected physical devices remain server-owned.

For every accepted capability, the release matrix proves all ten gates:

| Gate | Required evidence |
|---|---|
| Installed inventory | Pinned capability/parameter inventory and source/runtime digest |
| Global schema | Closed versioned schema with model-native compiler mapping |
| Browser controls | Suitable typed control for every relevant setting, including effective defaults, bounds, units, and help |
| Agent parity | The same schema supports discovery, validation, submission, and readback |
| Persistence | Requested and normalized effective settings survive save, reopen, retry, resubmit, clone, and authorized replay |
| Execution | Scheduler materialization consumes the validated effective settings without omission or fallback |
| Receipt | Preparation and terminal execution receipt record schema version, effective configuration, digest, model/runtime identity, and actual resource identity |
| Global result experience | Native outputs use the required bounded data, visualization, statistics, capture, persistence, and viewer mechanisms |
| Workflow reuse | Every consuming workflow uses the same configuration compiler and result authority |
| Live agreement | One approved owner-path receipt proves typed request to native execution/result agreement for the exact released revision |

Deterministic Mol Bio operations that do not invoke a scientific model still use one strict versioned operation schema, persist explicit defaults and effective values, and reject browser-owned canonical identities. Model-backed child stages satisfy the full ten-gate matrix. A retry follows the preparation rule in section 5.1; clone and resubmit preserve the exact requested settings unless an operator creates a new immutable plan revision.

## 11. Frontend deliverables

### 11.1 Project Manager

- Replace the empty NGS/MolBio marker form with typed scientific intent controls sourced from a server-owned capability registry.
- Show provisioning, acknowledged, stale, unavailable, and degraded binding states.
- Add `Open NGS/MolBio workspace`, retry verification, exact Dataset, run, result, evidence, and lineage actions.
- Display producer-native status, exact revision/generation, digest, and reconciliation state.

### 11.2 NGS/MolBio Domain workspace

Activate owner-authorized controls for:

- initialize/reverify binding;
- create and revise samples;
- create, import, revise, archive, and reopen managed references;
- select exact molecular, primer, PCR, operation, run, analysis, comparison, and evidence receipts;
- save a new local state revision;
- create/revise global Datasets from selected receipts;
- launch or reopen NGS work in exact context;
- attach evidence and create assessments.

Every accepted native Mol Bio or NGS detail surface gets the same receipt-backed `Add to Project` action. This includes samples, molecular revisions, primers, PCR revisions, managed references, comparison panels, ONT observations, NGS Jobs, result manifests, QC, analyses, alignment sessions, and evidence assessments. The action issues or re-verifies the native receipt server-side. It never constructs receipt identity in the browser.

Specialized sequence editing stays in Mol Bio Toolkit. NGS launch and result work stays in NGS Toolkit. The domain workspace coordinates them through server-issued destinations.

### 11.3 Exact deep links

Every emitted query key must have a literal destination consumer. Required identity includes the full hierarchy plus the native selector:

- sequence/construct ID + revision ID;
- primer ID + revision ID;
- PCR experiment ID + revision ID;
- sample ID + sample revision ID;
- managed reference ID + reference revision ID;
- ONT run ID + observed generation;
- Job ID + terminal manifest identity;
- comparison panel ID + version;
- evidence assessment ID + wrapper digest where required.

Hierarchy changes clear or revalidate all child selectors.

### 11.4 Query isolation

React Query placeholder data must not cross deployment profile, Project, Global Experiment, Domain Experiment, binding revision, or local state revision contexts.

- Remove `keepPreviousData` from authority-bearing context changes, or key and clear data before a new context renders.
- Reset accumulated map, run, result, Dataset, lineage, and activity pages when any authority scope changes.
- Disable mutations until the new context has validated.
- Reject late responses from prior contexts.

## 12. ELN-like behavior

The global inspector and activity feed expose:

- Project and Global Experiment purpose, hypothesis, success criteria, and status;
- Domain scientific objective and planned capabilities;
- append-only notes and observations;
- evidence-linked decisions and conclusions;
- superseding records without rewriting history;
- exact receipt and Dataset revision links.

Automated analysis publishes evidence. Only an operator-authored record can be a conclusion.

## 13. Failure and security behavior

- Project mutations require Project owner authority.
- Global operational endpoints require operator authority.
- Domain mutations require owner authority plus an acknowledged current binding.
- Unknown fields fail schema validation.
- Missing or ambiguous native targets fail closed.
- Caller-supplied paths, digests, receipt bodies, and acknowledgements are rejected.
- Stale generations return typed conflict responses.
- A digest mismatch never degrades to a current-head lookup.
- Connector semantic conflicts persist and do not enter blind retry loops.
- Read/reopen of historical accepted state remains available when current binding health is degraded.
- Manual dispatch and reconciliation remain HTTP `409`.

## 14. Delivery phases

### Phase N0: Re-pin and freeze connector contracts

**Work:** Re-pin `test`; inventory concurrent drift; freeze Domain v4, binding, event/ordering, acknowledgement, adapter, capability, parameter, criterion/evidence, pagination, payload-ownership, and deep-link schemas.

**Gate:** Every field maps to an existing native or global authority. File ownership is non-overlapping. Every advertised capability has one complete parameter inventory and ten-gate parity ledger.

### Phase N1: Parent hierarchy and connector migrations

**Work:** Allocate and add only `PARENT-GLOBAL-HIERARCHY-V2` and `NGS-GLOBAL-CONNECTOR`; implement server-issued hierarchy receipts and the PM-10A persistence foundation. Later logical migrations remain unallocated work items until their prerequisite package is eligible.

**Gate:** Fresh and upgraded stores match for these two logical migrations, old checksums remain unchanged, and the connector persistence foundation is ready for N2 without claiming Domain binding.

### Phase N2: Managed connector foundation

**Work:** Implement PM-10A global command processing, local outbox leasing, global inbox ingestion, acknowledgement, duplicate suppression, typed conflict recording, bounded retry/recovery, and foundation health.

**Gate:** Deterministic fixtures prove restart does not duplicate state or receipts, stale leases cannot acknowledge reclaimed work, and initialize/reverify reach one exact binding status. Reversed delivery, generation gaps, stale revisions, duplicate digests, and conflicts follow section 4.4 without regressing a projected head. This gate accepts the connector mechanism for PM-03B use. It does not claim final current-tree convergence.

### Phase N3: Domain binding, Project links, adapters, and lineage

**Work:** After N2 accepts PM-10A, allocate and add `NGS-DOMAIN-BINDING` and `NGS-GLOBAL-PROJECT-LINK-V2`; implement PM-03B binding/bootstrap/link behavior. Then add every missing adapter, exact native resolver, result surface, and typed cross-receipt lineage edge.

**Gate:** Binding/bootstrap/link acceptance passes on the accepted connector foundation. The registered adapter set then equals the accepted receipt-family set. Wrong owner, revision, generation, or bytes fail before attachment.

### Phase N4: Active toolkit, read-model, and lineage prerequisites

**Work:** Allocate and add `NGS-GLOBAL-DATASET-KIND` for PM-05. Enable Domain mutation controls and wire Project context through Mol Bio Toolkit and NGS Toolkit. Complete exact Dataset revision flows, activity and ELN records, PM-06 query isolation, pagination/response limits, maintained projections, and read-model provenance. Allocate and add `PARENT-GLOBAL-READ-PROJECTIONS` only after every counted resource exists. Complete and accept PM-07 receipt freshness, exact reopen, successor receipt, and lineage reconciliation. This phase cannot allocate `NGS-GLOBAL-ATTEMPT-LAUNCH-CONTEXT`, start PM-08A, create launch intent, or expose retry/resubmit/clone/cancel.

**Gate:** PM-05, PM-06, and PM-07 are separately accepted. Old Dataset and local-state revisions reopen independently. One mounted operator flow crosses Project Manager and both toolkits through authoring, bounded multi-page reads, literal exact receipt reopening, and return without browser-owned identity. Every accepted receipt family passes fresh/stale/missing/tampered/reverified restart cases. No PM-08A consumer starts before this gate.

### Phase N5: Fenced execution, artifacts, and operations

**Work:** Only after N4 accepts PM-05, PM-06, and PM-07, allocate and add `NGS-GLOBAL-ATTEMPT-LAUNCH-CONTEXT` and implement PM-08A Plan, preparation, fenced launch, retry, resubmit, clone, cancellation, comparison, result reopen, and resource admission. After PM-08A passes, add and accept PM-09 production writers/read projections for global-owned validation artifacts, bounded attempt-log records, and result surfaces. Only after PM-07, PM-08A, and PM-09 are accepted may PM-10B execute against the current worker/outbox/inbox state; resolve every conflict and deferred gap or record an accepted terminal disposition without regressing stream heads. Complete health, backup, export, the recursively closed schema/payload manifest, scanner, and retained release audit. Emit exact component attestations for PM-11. Do not publish the aggregate shared-package receipt in N5. Domain scientific artifacts and full logs remain native and attach through verified receipts.

**Gate:** The fenced cancel-before-submit, submit-before-cancel, crash-replay, stale-token, and resource-release cases pass for typed and managed modes. Clone creates only fresh immutable intent and exact lineage. PM-09 writer/result-surface evidence is accepted before PM-10B starts. Current connector conflicts and deferred generation gaps are zero, with restart proof and no orphan execution. Health includes connector lag, verification failures, `cancelling` attempts, resource reservations versus actual use, and verified backup/export times. The exact migration/schema/route/service/UI/health/audit identities, recursive schema closure, and retained payload audit pass. N5 ends with complete component attestations and no final aggregate receipt.

### Phase N6: Current-tree verification and Development acceptance

**Work:** After all thirteen owner-authenticated PM-01 through PM-10B package receipts pass, PM-11 executes the v4 package validator, reconstructs the 98-row recursive schema manifest and 100-file outer package, verifies the protected package-owner registry, package-specific evidence bodies, all five migration v2 attestations, typed quiescence, payload ownership, backup v3, restoration v3, isolated restoration/reopen, and export v3 bodies, and publishes `bms.shared-global-package-acceptance.v5`. PM-12 then resolves the three-authority exact-tree review, Development-runtime v2, typed health, retained-version restart/reopen, fixed Slice A/Slice B/N6 browser evidence, and Christian's signed visual v3 gate. It publishes `bms.project-manager.final-acceptance.v4` only after all bodies bind the same source commit/tree and deployed build. Historical and failed-candidate versions cannot satisfy either phase.

**Gate:** PM-11 resolves to one immutable pre-deployment candidate. The later PM-12 final receipt resolves that same candidate, its accepted PM-11 receipt, the exact deployed build, and every final acceptance artifact without a backward dependency.

## 15. Verification scope

Test execution requires separate approval. Once authorized, use a bounded suite.

### 15.1 Backend contract checks

- Binding success, wrong parent, archived parent, stale revision, unavailable authority, and digest mismatch.
- Typed and managed launch fences cover cancel-before-submit, cancel-after-check-before-native-commit, submit-before-cancel, cancellation crash replay, stale epoch/token, duplicate commands, canonical Job cancellation, and release only after verified acknowledgement.
- Clone rejects foreign/stale run or attempt authority, requires an exact source attempt, copies the exact closed Plan revision and requested settings into one fresh draft, records its exact `derived_from` edge and effective-settings digest, and creates no preparation, context, dispatch, or Job.
- Bootstrap idempotency and versioned successor binding.
- Outbox claim, stale lease reclaim, token fencing, retry, duplicate, semantic conflict, reversed delivery, stream-generation gap deferral/drain, superseded-binding delivery, and no head regression.
- Deterministic V1-V3 outbox migration preserves every row byte/ID and twice produces the same binding/stream/stream-generation map; unknown legacy event types fail migration.
- One test for each event schema.
- Exact adapter registry set and verifier negatives for each family.
- Multiple revisions of one stable native entity.
- Dataset kind registry enforcement; exact kind persistence and audit; legacy null-kind migration/read-only behavior; canonical membership; digest binding; exact historical retrieval; current-head independence; 0/10,000/10,001-member boundaries; 2-KiB metadata and 64-KiB/1-MiB request boundaries; metadata-payload rejection; same-key replay after head advancement; changed-request conflict; concurrent duplicate suppression; and crash/restart replay through the existing idempotency claim.
- Launch context scope, preparation revalidation, direct attempt preparation binding, unchanged-preparation reuse, required-successor preparation, retry, resubmit, cancellation, and terminal receipt linkage.
- One field-by-field parameter-parity matrix per accepted capability: installed inventory, closed schema/compiler mapping, requested/effective persistence, retry/resubmit/clone/replay preservation, scheduler consumption, receipt configuration/digest, global result experience, and workflow reuse.
- Project non-owner and global non-operator denial on every new mutation/operation class.
- Cross-Project, cross-Global, cross-Domain, foreign-binding, and foreign-Dataset-member injection denial.
- Unknown-field rejection for every new Domain, connector, Dataset, plan, criterion/evidence, and operation request schema.
- Caller-supplied path, digest, receipt body, acknowledgement, owner ID, generation, and canonical relationship rejection.
- Degraded current binding permits historical accepted reads and rejects new domain mutations.
- Cursor stability, deterministic order, foreign/stale cursor rejection, over-100-member Dataset paging, 1 MiB response failure, and 256 KiB summary bounds.
- Payload-ownership scanner detects a seeded duplicate and emits the required passing retained audit after removal.
- Backup and isolated restoration cover all five stores, all four governed roots, and all eight authority families with coherent SQLite snapshots, exact high-watermarks, schema/trigger/migration attestations, reproducible digest/object-count agreement, connector reconciliation, isolated restart, and all eight historical reopen kinds. Export, schema attestation, and restart reopening remain separate checks.

### 15.2 Mounted frontend checks

- Create an NGS/MolBio Domain Experiment and observe automatic provisioning.
- Recover a failed acknowledgement through the retry action.
- Create/import/archive/reopen a managed reference.
- Save and reopen an exact local state revision.
- Launch from exact Project context and return from the native result.
- Switch Project/Global/Domain contexts while stale responses are pending.
- Against real Development backend stores, load Project A, switch to Project B while B is loading, and prove no Project A summary, action, receipt, Dataset, lineage row, result, or mutation remains visible or actionable. A late Project A response is cancelled or discarded and cannot repopulate B. Mock-only or fixture-only evidence cannot satisfy this gate.
- Reopen every accepted receipt family through its literal consumer.
- Render binding, digest, reconciliation, evidence, Dataset, and lineage states.
- Exercise typed controls and agent readback for every relevant setting in each accepted capability matrix.
- Show owner/operator denials, degraded-binding read-only behavior, over-cap collection handling, and `response_too_large` without rendering empty success.

### 15.3 Static and build checks

Run only the affected Python checks, frontend type/build checks, migration attestation, `git diff --check`, and focused security/static checks required by the changed paths. Broad unrelated suites are outside this SOW.

## 16. Development live-acceptance scenario

Use one real retained or newly approved molecular/NGS dataset. A new physical sequencing run is outside normal acceptance unless separately approved.

If BFX6NB is selected, its accepted input authority is BAM, BAI, and the exact reference only. No BFX6NB FASTQ is asserted or synthesized. BFX6NB provenance cannot reuse DRT4 identities or receipts. The acceptance packet records the exact chosen files, sizes, digests, reference revision, and native receipt identities before launch.

1. Prove Development source SHA, tree, listener owner, database paths, and single dispatcher owner.
2. Create a Project and Global Experiment.
3. Create an NGS/MolBio Domain Experiment and observe an acknowledged local binding.
4. Create or import one immutable molecular revision.
5. Create a sample and managed reference revision.
6. Save a local state revision with exact member receipts.
7. Create a global Dataset revision from selected members.
8. Save and prepare one supported NGS Workflow Plan without creating a Job.
9. Launch through NGS Toolkit with the opaque Project context.
10. Observe the canonical Job, run group, attempt, native state, and terminal receipt.
11. Reopen QC, alignment, comparison, and evidence at exact identities.
12. Record an observation and conclusion with source receipts.
13. Restart API and frontend services.
14. Reopen the same Project, local state revision, Dataset revision, run, result, and ELN records.
15. Verify connector lag, failed-verification count, backup receipt, and export verification time.
16. Run the versioned payload-ownership scanner over `experiments.db`, the NGS/MolBio domain store, core NGS tables, and governed artifact roots. Retain the passing manifest/audit that proves each canonical payload has one active authority and that global rows contain only permitted bounded projections, receipt identity, metadata, and digests.
17. Capture browser evidence from the exact deployed build.

## 17. Definition of done

The SOW is complete only when all statements are true:

- A Project Manager-created NGS/MolBio Domain Experiment reaches acknowledged local state without a test-only adapter.
- Global and local historical revisions remain independently retrievable.
- Local scientific-state and sample revisions have exact global receipt, Dataset, lineage, and native reopen behavior.
- Every accepted local receipt kind has exact global search, verification, attachment, result routing, lineage, and native reopen behavior.
- Active Mol Bio and NGS operator actions consume the selected Project and local-state authority.
- Preparation creates no Job. Explicit launch creates one idempotent run path.
- Retry creates a fresh attempt bound directly to an immutable, revalidated preparation.
- Unchanged valid authority may reuse the exact preparation on retry. Changed or stale authority requires an immutable successor preparation linked by `supersedes`.
- Global Datasets persist exact ordered membership and reopen exact revisions.
- `derived_from` and `compared_with` edges follow the direction rules in this SOW.
- Missing, stale, ambiguous, foreign, or digest-divergent authority fails visibly.
- Placeholder data never crosses authority contexts.
- Connector delivery converges after restart and prevents stale-token finalization.
- Ordered connector delivery handles gaps, reversed arrival, duplicates, conflicts, and successor bindings without head regression.
- Every accepted NGS/MolBio model-backed capability passes all ten parameter-parity gates. Deterministic domain operations retain strict requested/effective operation schemas.
- The security negative matrix proves owner/operator authorization, hierarchy isolation, foreign authority denial, strict unknown-field rejection, and caller-authority rejection.
- All collections obey the frozen page, cursor, summary, and 1 MiB response limits through the deployed reverse proxy.
- GPU-capable plans honor typed workflow GPU selection. CPU and DRAM admission honor the 24-thread and 96-GiB aggregate limits.
- Global-owned validation artifacts and bounded attempt logs have real writers and Project read projections. Native scientific payloads remain in their owning stores.
- The retained `bms.payload-ownership-audit.v1.json` proves zero canonical scientific-payload duplication across active authorities and classifies permitted transient/preservation copies.
- Project export, the closed five-store/four-root/eight-family backup and isolated restoration drill, health, and restart reopening include all accepted authorities and pass their exact receipt schemas.
- Focused current-tree checks pass after final source edits.
- Independent exact-tree review finds no release-blocking specification or scientific-integrity defect.
- `test` is pushed, Development runs that exact commit with one dispatcher owner, and browser acceptance passes.
- The selected-context `/designer` and `/ngs` surfaces pass parent section 0.4 at all three desktop viewports and receive Christian's visual acceptance.
- Production remains unchanged until separately authorized.

## 18. Likely implementation files

### Frozen specification schemas

- `docs/specs/schemas/project-v2.schema.json`
- `docs/specs/schemas/global-experiment-v2.schema.json`
- `docs/specs/schemas/hierarchy-revision-v1.schema.json`
- `docs/specs/schemas/attachment-receipt-v1.schema.json`
- `docs/specs/schemas/external-entity-receipt-v1.schema.json`
- `docs/specs/schemas/launch-context-v2.schema.json`
- `docs/specs/schemas/ngs-molbio-project-link-v2.schema.json`
- `docs/specs/schemas/project-manager-read-model-v2.schema.json`
- `docs/specs/schemas/project-manager-run-v2.schema.json`
- `docs/specs/schemas/project-manager-continuation-v2.schema.json`
- `docs/specs/schemas/research-record-v2.schema.json`
- `docs/specs/schemas/result-surface-v1.schema.json`
- `docs/specs/schemas/run-clone-request-v1.schema.json`
- `docs/specs/schemas/run-clone-receipt-v1.schema.json`
- `docs/specs/schemas/project-manager-file-manifest-v1.schema.json`
- `docs/specs/schemas/project-manager-backup-receipt-v1.schema.json`
- `docs/specs/schemas/project-manager-restoration-receipt-v1.schema.json`
- `docs/specs/schemas/project-manager-export-receipt-v1.schema.json`
- `docs/specs/schemas/operator-visual-acceptance-v1.schema.json`
- `docs/specs/schemas/shared-global-package-acceptance-v2.schema.json`
- `docs/specs/schemas/project-manager-final-acceptance-v1.schema.json`
- `docs/specs/schemas/project-manager-file-manifest-v2.schema.json`
- `docs/specs/schemas/project-manager-quiescence-v1.schema.json`
- `docs/specs/schemas/project-manager-migration-attestation-v1.schema.json`
- `docs/specs/schemas/project-manager-package-acceptance-v1.schema.json`
- `docs/specs/schemas/project-manager-backup-receipt-v2.schema.json`
- `docs/specs/schemas/project-manager-restoration-receipt-v2.schema.json`
- `docs/specs/schemas/project-manager-export-receipt-v2.schema.json`
- `docs/specs/schemas/project-manager-development-runtime-v1.schema.json`
- `docs/specs/schemas/project-manager-exact-tree-review-v1.schema.json`
- `docs/specs/schemas/project-manager-health-acceptance-v1.schema.json`
- `docs/specs/schemas/project-manager-restart-reopen-acceptance-v1.schema.json`
- `docs/specs/schemas/project-manager-browser-acceptance-v1.schema.json`
- `docs/specs/schemas/operator-trust-registry-v1.schema.json`
- `docs/specs/schemas/operator-decision-authorization-v1.schema.json`
- `docs/specs/schemas/operator-visual-acceptance-v2.schema.json`
- `docs/specs/schemas/shared-global-package-acceptance-v3.schema.json`
- `docs/specs/schemas/project-manager-final-acceptance-v2.schema.json`
- `docs/specs/schemas/project-manager-operational-evidence-v2.schema.json`
- `docs/specs/schemas/project-manager-package-owner-registry-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-01-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-02-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-03a-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-03b-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-04-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-05-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-06-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-07-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-08a-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-08b-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-09-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-10a-v2.schema.json`
- `docs/specs/schemas/project-manager-package-evidence-body-pm-10b-v2.schema.json`
- `docs/specs/schemas/project-manager-package-acceptance-v3.schema.json`
- `docs/specs/schemas/project-manager-migration-attestation-v3.schema.json`
- `docs/specs/schemas/project-manager-semantic-validator-v2.schema.json`
- `docs/specs/schemas/project-manager-historical-authority-denominator-v2.schema.json`
- `docs/specs/schemas/project-manager-backup-receipt-v4.schema.json`
- `docs/specs/schemas/project-manager-isolated-restoration-reopen-v2.schema.json`
- `docs/specs/schemas/project-manager-restoration-receipt-v4.schema.json`
- `docs/specs/schemas/project-manager-export-receipt-v4.schema.json`
- `docs/specs/schemas/project-manager-development-runtime-v3.schema.json`
- `docs/specs/schemas/project-manager-reviewer-registry-v2.schema.json`
- `docs/specs/schemas/project-manager-exact-tree-review-v3.schema.json`
- `docs/specs/schemas/project-manager-capability-execution-denominator-v2.schema.json`
- `docs/specs/schemas/project-manager-execution-control-acceptance-v2.schema.json`
- `docs/specs/schemas/project-manager-health-acceptance-v3.schema.json`
- `docs/specs/schemas/project-manager-restart-reopen-acceptance-v3.schema.json`
- `docs/specs/schemas/project-manager-browser-acceptance-v3.schema.json`
- `docs/specs/schemas/operator-owner-enrollment-v2.schema.json`
- `docs/specs/schemas/operator-trust-registry-v3.schema.json`
- `docs/specs/schemas/operator-challenge-issued-v2.schema.json`
- `docs/specs/schemas/operator-challenge-consumed-v2.schema.json`
- `docs/specs/schemas/operator-sign-counter-v2.schema.json`
- `docs/specs/schemas/operator-decision-authorization-v3.schema.json`
- `docs/specs/schemas/operator-visual-acceptance-v4.schema.json`
- `docs/specs/schemas/shared-global-package-acceptance-v5.schema.json`
- `docs/specs/schemas/project-manager-final-acceptance-v4.schema.json`
- `schemas/ngs_molbio/domain-experiment-v4.schema.json`
- `schemas/ngs_molbio/ngs-molbio-experiment-v2.schema.json`
- `schemas/ngs_molbio/protein-in-silico-experiment-v3.schema.json`
- `schemas/ngs_molbio/evidence-requirement-v2.schema.json`
- `schemas/ngs_molbio/evidence-requirement-native-receipt-v1.schema.json`
- `schemas/ngs_molbio/evidence-requirement-operator-observation-v1.schema.json`
- `schemas/ngs_molbio/evidence-requirement-result-artifact-v1.schema.json`
- `schemas/ngs_molbio/ngs-molbio-group-v1.schema.json`
- `schemas/ngs_molbio/ngs-molbio-group-member-v1.schema.json`
- `schemas/ngs_molbio/protein-comparison-group-v1.schema.json`
- `schemas/ngs_molbio/protein-comparison-member-v1.schema.json`
- `schemas/ngs_molbio/protein-constraint-v1.schema.json`
- `schemas/ngs_molbio/protein-dataset-member-ref-v1.schema.json`
- `schemas/ngs_molbio/protein-entity-map-reference-v1.schema.json`
- `schemas/ngs_molbio/protein-target-v3.schema.json`
- `schemas/ngs_molbio/scientific-criterion-v2.schema.json`
- `schemas/ngs_molbio/scientific-criterion-artifact-presence-v1.schema.json`
- `schemas/ngs_molbio/scientific-criterion-manual-review-v1.schema.json`
- `schemas/ngs_molbio/scientific-criterion-metric-threshold-v1.schema.json`

The first 79 files are closed specification and acceptance contracts. Every previously reviewed lower version remains immutable historical or failed-candidate authority. Only package acceptance v2, migration attestation v2, backup/restoration/export v3, Development/review/health/restart/browser v2, trust registry v2, authorization v2, visual v3, shared-package v4, and final-acceptance v3 can satisfy the active gates. The following 19 files are the complete recursive runtime-validation closure rooted at Domain v4, NGS/MolBio payload v2, and Protein payload v3. Historical Domain v1/v2, Protein payload v1/v2, Protein target v2/entity-map v1, criterion/evidence v1, and shared-package acceptance v1 authorities remain byte-for-byte readable outside this new-write closure. Runtime authority changes only through PM-02 and its sole owner.

The exact specification review, package manifest, PM-11 receipt, and PM-12 final seal bind all 98 listed schema files in this order. Every manifest row contains zero-based ordinal, path, exact `$id`, raw-file SHA-256, and sorted unique non-fragment external `$ref` targets. The resolver validates every external reference in every row and requires each target `$id` to resolve to exactly one listed file with the recorded digest. The three Domain payload roots must additionally reach exactly the 19 runtime-validation rows. Missing, duplicate, unresolved, unlisted, reordered, unreachable-runtime, or digest-divergent targets fail the manifest. The PM-11 receipt binds the RFC 8785 canonical schema-manifest SHA-256 and the 100-file outer package SHA-256 using section 2.4's framing. Omitting one row or changing one file invalidates the package hash, review, PM-11 receipt, and PM-12 receipt.

### Global backend

- `platform/api/experiment_models.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- `platform/api/routers/projects.py`
- `platform/api/routers/project_manager.py`
- `platform/api/routers/experiment_workspaces.py`
- `platform/api/services/global_experiments/adapters.py`
- `platform/api/services/global_experiments/receipts.py`
- `platform/api/services/global_experiments/read_models.py`
- `platform/api/services/global_experiments/result_surfaces.py`
- `platform/api/services/global_experiments/worker.py`
- `platform/api/main.py`

### Domain backend

- `platform/api/molbio_ngs_models.py`
- `platform/api/molbio_ngs_migrations.py`
- `platform/api/molbio_ngs_services.py`
- `platform/api/routers/molbio_ngs_experiments.py`
- `platform/api/services/molbio_ngs_member_receipts.py`
- `platform/api/services/molbio_ngs_evidence.py`
- `platform/api/services/ngs_comparison_panels.py`
- native ONT, reference-set, assignment, QC, and alignment services as required by exact adapter closure

### Frontend

- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/pages/ProjectManager.tsx`
- `platform/frontend/src/components/project-manager/ManagerDialog.tsx`
- `platform/frontend/src/components/experiments/GlobalExperimentContext.tsx`
- `platform/frontend/src/components/molbio-ngs/DomainExperimentWorkspace.tsx`
- `platform/frontend/src/components/MolBioToolkit/`
- `platform/frontend/src/components/NGSToolkit.tsx`
- `platform/frontend/src/components/NanoporeTemplate.tsx`
- `platform/frontend/src/components/project-manager/ProjectReturnBanner.tsx`

### Focused tests

- `platform/api/tests/test_molbio_ngs_experiment_management.py`
- `platform/api/tests/test_molbio_ngs_restart_reopen.py`
- `platform/api/tests/test_project_manager_domain_adapters.py`
- `platform/api/tests/test_project_manager_hierarchy.py`
- `platform/api/tests/test_project_manager_read_models.py`
- `platform/api/tests/test_experiment_operations.py`
- existing NGS receipt, ONT run, reference-set, QC, alignment, and migration test owners
- `platform/frontend/tests/vitest/projectManagerPage.test.tsx`
- new mounted NGS/MolBio workspace tests added to the existing Vitest configuration

## 19. Release boundary

Completion produces a Development candidate on `test`. The release packet records commit, tree, migration ledgers, database paths, service PIDs/owners, listener map, worker lease owner, health response, backup/export receipts, focused test output, and browser evidence.

Production promotion is a separate SOW or explicit operator action.
