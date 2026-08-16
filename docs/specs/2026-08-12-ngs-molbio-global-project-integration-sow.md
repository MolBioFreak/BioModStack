# Statement of Work: Full NGS and Mol Bio Toolkit Integration with Global Project and Experiment Management

**Status:** Implementation-ready scope specification

**Date:** 2026-08-12

**Source baseline:** `test` and `origin/test` at commit `493356b7f063a7d0a6366ccfe88722cca3240104`, tree `0f73fccdda5e6501b75edd43fcba856401502b49`

**Controlling parent specification:** `docs/specs/global-bms-project-experiment-manager.md`

**Implementation authority:** This document specifies work. Source edits, test execution, live runs, deployment, and Production promotion require separate authorization.

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
→ create or import immutable molecular material
→ select samples and managed reference revisions
→ save an exact local scientific-state revision
→ launch ONT/NGS work in that exact context
→ attach terminal evidence
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
- the canonical outer Domain Experiment v2 wire-contract support;
- canonical Project Dataset create/revise/list/exact-revision APIs and reusable UI controls;
- global-owned validation-artifact and bounded attempt-log writers/projections;
- authority-context query isolation;
- one shared managed-workflow resource-admission ledger and enforcement service for the 24-thread CPU and 96-GiB DRAM aggregate limits;
- one shared opaque-keyset pagination and response-limit policy for all new global collections;
- the shared `bms.payload-ownership-manifest.v1`, versioned scanner, and retained `bms.payload-ownership-audit.v1.json` release audit;
- shared operational-health, backup, and export fields introduced by these SOWs.

The Protein In Silico tranche consumes these exact migrations, schemas, routes, services, controls, limits, audit mechanisms, and health fields. It adds protein-specific payload schemas, capability adapters, read projections, viewers, and payload-ownership rows. It cannot create a second Workflow Plan route implementation, launch-context authority, Dataset API, artifact/log writer, query-isolation mechanism, resource-admission ledger, pagination policy, payload scanner, or health model. If implementation order changes, this shared package remains one separately accepted NGS-owned prerequisite and retains the contracts in this document.

The complete package is accepted once through a retained `bms.shared-global-package-acceptance.v1` receipt. The receipt binds the exact source commit/tree; migration numbers and schema attestations; schema IDs and digests; canonical route table and service/component identities; launch-context v2 authority; Dataset UI/API identity; artifact/log writer identity; query-isolation checks; resource-admission policy and ledger identity; pagination/response-limit policy; operational-health, backup, and export field versions; payload-manifest/scanner versions; and the passing payload-ownership audit digest. It records every focused check and acceptance artifact used by the Phase N5 aggregate gate. A missing, partial, stale, or digest-divergent receipt leaves the shared package unaccepted.

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

### 3.1 Inherited global capabilities

These are reusable prerequisites. They do not count as completion of this domain vertical.

| Capability | Current state |
|---|---|
| Project → Global Experiment → Domain Experiment hierarchy | Implemented with immutable revisions and generation-checked lifecycle operations |
| Project tree, relationship map, inspector, and bounded collections | Implemented in `ProjectManager.tsx` and global read models |
| Verified external receipts and Project attachment | Implemented through the global adapter registry and receipt service |
| Launch contexts and canonical return routing | Implemented for typed Job submission |
| Managed global dispatcher and run reconciler | Implemented with single-owner file locking |
| Retry and resubmit APIs | Implemented; direct attempt-to-preparation authority still needs additive closure |
| Global Dataset aggregates and persisted revision members | Implemented; exact domain use and operator flow need completion |
| ELN-lite records | Implemented as append-only research records |
| Worker health | Partial; pending count and age exist, while reconciliation lag, receipt failures, and verified backup/export times remain absent |
| Manual dispatch | Correctly disabled with HTTP `409` |

### 3.2 Existing NGS/MolBio domain implementation

- Stable local Domain state keyed by global Domain Experiment ID.
- Immutable local state revisions and membership graphs.
- Durable samples and immutable sample revisions.
- Managed references, immutable reference revisions, import, archive, and restart reopening.
- Member receipts for molecular revisions, primers, PCR experiment revisions, molecular operations, ONT runs, NGS jobs and manifests, comparison panels, reference revisions, and evidence assessments.
- Evidence attachment and immutable scientific assessments.
- Local audit events and an outbox table with lease, retry, acknowledgement, and conflict fields.
- Native receipt resolvers for expected references, approved comparison panels, ONT runs, NGS jobs, result manifests, and molecular revisions.
- Existing global adapters for molecular revisions and constructs, molecular operations, expected-reference receipts, reference sets, ONT runs, pooled assignment releases, sequence QC, NGS analyses, and alignment sessions.
- A read/reopen domain workspace with Overview, Samples, Molecular Inputs, References, PCR, Instrument Runs, Analyses, Evidence, and History sections.

### 3.3 Blocking gaps

1. `verify_global_domain_binding()` always raises `GlobalAdapterUnavailable`.
2. A Project Manager-created NGS/MolBio shell cannot initialize local state.
3. The global NGS/MolBio payload is an empty schema marker.
4. The frontend forces valid NGS/MolBio contexts to read-only and marks the global adapter unavailable.
5. Reference Create, Import, and Archive controls are disabled.
6. The local outbox has no managed production consumer.
7. The global registry lacks dedicated adapters for several accepted local receipt kinds.
8. Exact ONT reopening does not consistently bind the observed generation.
9. NGS Toolkit submissions do not consume the global launch-context contract end to end.
10. No mounted browser regression proves the full NGS/MolBio lifecycle.
11. React Query placeholder data can retain a prior Project summary while a new authority context loads.
12. Operational health omits required connector and backup/export evidence.
13. Sample revisions and local scientific-state revisions lack first-class global receipt, adapter, Dataset, and lineage closure.
14. The local `ngs_job` receipt exists, while the global registry exposes `ngs_analysis_job`. Their identity and schema mapping is not frozen as one exact adapter contract.
15. `Add to Project` is available on Sequence QC, but it is not a consistent action across all accepted NGS/MolBio native surfaces.
16. The current `ngs_result_manifest` member-receipt resolver accepts `biomodstack.construct_verification.v2` and schemaless legacy artifact versions 1/2, but rejects the schema-present `sequence_qc.manifest.v1` documents emitted by the current manifest builders. The corrected resolver must preserve each raw source schema and artifact-schema version without inventing equivalence.

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

Migration numbers must be rechecked at the implementation baseline. On this SOW baseline, the next global versions are V11 and V12, and the next NGS/MolBio domain version is V4.

### 5.1 Global V11: attempt, launch-context, and Dataset authority

- Add direct immutable `preparation_id` authority to every run attempt.
- Add nullable historical `preparation_id`, `run_attempt_id`, and v2 lifecycle fields to launch-context storage. New `bms.launch-context.v2` rows require one preparation ID. A context becomes `reserved` for exactly one run attempt before managed materialization and becomes `consumed` only after exact canonical Job binding.
- Add nullable `aggregate_heads.dataset_kind` with `CHECK (dataset_kind IS NULL OR aggregate_kind = 'dataset')` and an index on `(workspace_id, parent_id, dataset_kind, lifecycle_state)`. Existing non-Dataset rows receive null. Existing Dataset rows retain their current `description` bytes and receive a kind only when that value already equals one exact enabled registry ID; every other existing Dataset receives null. A null-kind legacy Dataset and every exact historical revision remain readable and exportable. It cannot enter a new v2 preparation or receive a new revision. The owner creates a new typed Dataset from reverified member receipts when continued use is required. Migration never infers kind from names, members, or scientific payloads. Every new Dataset requires a non-null enabled registry ID.
- Backfill existing attempts from their owning workflow run when the relationship resolves uniquely.
- Keep historical `bms.launch-context.v1` rows byte-for-byte readable. They can support verified legacy attachment/reopening. They cannot launch a new prepared Workflow Plan.
- Reject ambiguous or missing attempt backfill authority. Do not guess a preparation for a historical v1 launch context.
- Rebuild the SQLite tables transactionally when needed to enforce foreign keys, v2 state combinations, unique context-to-attempt binding, and the attempt non-null contract.
- Keep attempt IDs, scheduler Job IDs, existing launch-context IDs/receipts, and timestamps unchanged.

Every new planned launch uses a server-issued `bms.launch-context.v2` bound to one exact preparation, normalized-request digest, validation receipt ID/digest, workflow revision, hierarchy, and return URI. Run-group creation atomically reserves each context to its new attempt before any dispatch row becomes visible. The managed worker alone consumes the reserved context when it creates and verifies the canonical Job. Retry and resubmit use fresh contexts when a typed launcher handoff is required; they never reopen or repurpose a consumed context.

Every retry binds the new attempt directly to one immutable preparation. The service revalidates the source attempt's preparation against current one-time and revisioned authority. It reuses that exact preparation ID only when its normalized request, inputs, settings, binding revision, and validation receipt remain valid and unchanged. A stale or changed authority returns `replacement_preparation_required`; the retry remains undispatched until a successor immutable preparation is created. The successor records `supersedes` from new preparation to prior preparation, and the new attempt binds the successor ID. The workflow-run row is never mutated to conceal this choice. Scheduler materialization reads the attempt's preparation.

### 5.2 Global V12: domain connector command and inbox

Add only the records needed for this fixed connector:

- durable global-to-domain connector commands;
- idempotent domain-event inbox rows;
- canonical acknowledgements and conflicts;
- lease owner/token/expiry and retry state;
- exact Project, Global Experiment, Domain Experiment, and binding revision references.

Add a server-owned combined hierarchy binding-receipt writer over existing aggregate revisions and `domain_adapter_receipts`. No new generic external-receipt registry is created.

Use existing `domain_adapter_receipts`, external receipts, resources, audit, and lineage tables for accepted authority. Do not create a second global receipt system.

### 5.3 NGS/MolBio V4: binding revisions and ordered outbox authority

- Convert the current binding row into append-only binding revisions.
- Add a current binding-revision pointer to the stable local Domain state.
- Add `binding_revision_id` to each local state revision.
- Add combined global binding receipt ID, canonical JSON, and digest fields. Retain all legacy Project/Global receipt columns as read-only evidence.
- Add `needs_reverification` as the migration state for a legacy binding that has no server-issued combined receipt.
- Persist `event_stream`, `stream_generation`, and nullable `source_generation` as first-class fields. Enforce one unique `(global_domain_experiment_id, binding_revision_id, event_stream, stream_generation)` tuple.
- For every V1-V3 outbox row, preserve the row ID, event type, payload bytes/digest, status, lease/evidence fields, and timestamps. Assign it to the migrated legacy binding revision. Verify that `payload_sha256` matches the unchanged canonical payload, parse the exact event schema, and derive `event_stream` and `source_generation` from the frozen map below. Within each resulting stream, assign `stream_generation = row_number() over (ORDER BY created_at ASC, id ASC)`, starting at 1. The current initialization event therefore receives stream generation `1` and source generation `0`.
- Because the pinned V1-V3 source has no sanctioned dispatcher, every migratable legacy outbox row must have `status=pending`, null lease/evidence/conflict fields, `retry_count=0`, `next_retry_at=null`, and `last_error=null`. Any other combination blocks V4 migration with a typed `untrusted_legacy_delivery_state` attestation error. The migration never fabricates a global inbox cursor or converts local status into acknowledgement authority.
- Rebuild the outbox table transactionally so the new ordering fields are non-null where required and covered by immutability triggers. Generate the migration mapping twice in a rolled-back dry run and require byte-identical event ID → stream/revision/sequence output before commit.
- Add first-class `sample_revision` and `ngs_molbio_state_revision` member-receipt kinds.
- Enforce legal lease/status/evidence combinations.
- Preserve every V1-V3 SQL body and checksum.
- Preserve all current IDs, member receipts, state revisions, samples, references, evidence, audit events, and outbox events.

Every pre-V4 local state revision and outbox row for a Domain receives that Domain's deterministic legacy binding revision ID. The stable Domain state's current binding pointer also starts at that ID. This backfill is non-null and exact. The later server-verified successor binding keeps `supersedes` from new binding revision to legacy binding revision, becomes current only after the combined hierarchy receipt is stored, and does not rewrite historical state or event rows.

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

A missing field, wrong schema, unknown event type, non-unique record, ownership mismatch, or digest mismatch blocks V4 migration with a typed attestation error. The migration does not infer identity from timestamps or join an audit row by similarity. V4 and later event types must register one stream-key and source-generation derivation before emission. Runtime code allocates `stream_generation` transactionally from the stream cursor; callers cannot submit it.

Fresh and upgraded databases must attest to equivalent final constraints. Backup and restore must include the new rows and verify foreign keys, trigger bodies, and schema manifests.

## 6. Global and domain APIs

### 6.1 Extend existing canonical Project APIs

- Domain creation writes outer `bms.domain-experiment.v2` with `domain_contract_version="2"`. The NGS payload is `bms.ngs-molbio-experiment.v2` with the exact closed fields `experiment_mode`, `scientific_objective`, `planned_capability_ids`, `grouping_intent`, `acceptance_criteria`, and `evidence_plan`. Modes and capability IDs come from the server registry. Grouping, criterion, and evidence wrappers use the strict contracts below.
- Existing outer/domain v1 revisions remain readable byte-for-byte. A v1 NGS marker is never rewritten or silently interpreted as v2 intent. The first edit creates a v2 successor and requires the operator to supply every v2 field.
- Domain creation returns connector provisioning state.
- Domain patching creates a new immutable global revision and a successor binding command.
- Archive and restore keep local scientific history readable. New domain mutations require an active acknowledged binding.

The persisted outer `bms.domain-experiment.v2` object has exactly these fields and types:

```text
schema: const bms.domain-experiment.v2
domain_kind: protein_in_silico | ngs_molbio
domain_contract_version: const "2"
name: string, 1..255 characters
objective: string, max 8192 characters
status: draft | planned | active | analysis | review | completed | blocked | archived
tags: array of unique strings, max 64 items, each 1..64 characters
source_receipt_ids: array of unique receipt resource IDs, max 256
dataset_revision_ids: array of unique exact Dataset revision resource IDs, max 128
created_by: non-empty server-derived actor ID, max 255 characters
change_summary: string, 1..1024 characters
domain_payload: exactly one registered domain v2 payload
```

All wrapper and nested schemas use `additionalProperties: false`. Stable Dataset IDs and current Dataset heads are invalid in `dataset_revision_ids`. The server verifies that every receipt and Dataset revision belongs to the same Project/Global/Domain authority allowed by the request.

The create request carries every persisted field except `created_by`; actor identity is derived from authenticated authority. The outer schema/kind/version become immutable after create. Patch requests carry `expected_head_generation` plus only mutable outer fields. Any `domain_payload` change supplies one complete replacement payload, and any Dataset-list change supplies the complete ordered exact-revision list. Partial nested merge is forbidden. Responses return the complete persisted v2 object and immutable revision identity. Caller-supplied actor IDs, generations, digests, receipt bodies, and canonical relationship fields are rejected.

The NGS payload contract is:

```text
schema: const bms.ngs-molbio-experiment.v2
experiment_mode: molecular_design | assembly_validation | pcr_validation | sequencing | quality_control | alignment | comparison | analysis
scientific_objective: string, max 8192 characters
planned_capability_ids: array of unique registered IDs, max 64
grouping_intent: array of bms.ngs-molbio.group.v1, max 128
acceptance_criteria: array of bms.scientific-criterion.v1, max 128
evidence_plan: array of bms.evidence-requirement.v1, max 128
```

`bms.ngs-molbio.group.v1` contains exactly `group_id`, `label`, and `members`. `group_id` is a 1..128-character stable ID unique in the payload. `label` is a 1..255-character display string. `members` has 1..256 unique entries. Each member contains exactly `member_kind`, `resource_id`, `role`, and `ordinal`; `member_kind` is `receipt` or `dataset_revision`; `resource_id` is a 1..255-character exact immutable resource ID; `role` is `input`, `sample`, `reference`, `target`, `panel`, `control`, or `comparison`; and `ordinal` is an integer from 0 through 65535 unique within the group.

`bms.scientific-criterion.v1` contains exactly `criterion_id`, `schema_id`, `subject_role`, and `payload`. `bms.evidence-requirement.v1` contains exactly `requirement_id`, `schema_id`, `subject_role`, `required`, and `payload`. IDs and roles are 1..128 characters. `subject_role` is `input`, `sample`, `reference`, `target`, `panel`, `control`, `result`, `comparison`, `evidence`, or `other`. `required` is boolean. `payload` is an object whose serialized canonical JSON is at most 64 KiB. Each `schema_id` resolves to a server-registered immutable closed JSON Schema; its payload rejects unknown fields. The registry stores schema bytes and SHA-256, and persisted intent binds both ID and digest. Draft intent may use empty capability/criteria/evidence arrays. A transition to `planned` or `active` requires at least one capability, criterion, and evidence requirement.

### 6.2 Connector routes

These route names are final:

```text
GET  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/binding
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/initialize
POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/binding/reverify
```

`GET .../binding` has no request body. It returns exactly `schema`, Project/Global/Domain stable and revision IDs, `binding_revision_id`, `global_receipt_id`, `global_receipt_sha256`, `connector_command_id`, `command_state`, `acknowledgement_id`, `acknowledgement_sha256`, `local_state_id`, `provisioning_state`, `head_generation`, `created_at`, and `updated_at`. `schema` is `bms.ngs-molbio.binding-status.v1`; `command_state` is `pending`, `leased`, `applied`, `duplicate`, `retryable`, or `conflicted`; and `provisioning_state` is `provisioning`, `ready`, or `degraded`. Pending values are null. The route returns one exact current binding revision and never substitutes a newer Project or Domain head.

`POST .../initialize` accepts exactly `expected_domain_revision_id`. `POST .../binding/reverify` accepts exactly `expected_domain_revision_id` and `expected_binding_revision_id`. Both require one `Idempotency-Key` HTTP header of 1..255 visible ASCII characters; a body idempotency field is invalid. `initialize` creates or replays the connector command for the named immutable Domain revision. `reverify` re-resolves the full hierarchy and appends a successor binding revision only when the named binding is current; it cannot rewrite the old binding or its local state revisions.

Both mutation routes return `202` with exactly the binding-status object above when processing is pending or retryable, and `200` with that same object for an exact replay or an already applied command. The response always identifies the one command selected by the normalized request. Reusing an idempotency key with a different canonical request returns `409 idempotency_conflict`; a stale Domain or binding revision returns `409 stale_revision`; an acknowledged exact replay returns the prior identity without another command or receipt. Unknown Project/Global/Domain/binding authority returns `404`. Missing or malformed fields, an unknown field, or an invalid header returns `422`. An unavailable sole connector owner returns `503 connector_unavailable`. Digest divergence, foreign hierarchy, archived authority, an ambiguous native target, or a durable connector conflict returns typed `409` and leaves the prior current binding unchanged. The browser cannot submit actor identity, command IDs, receipt bodies, acknowledgements, digests, generations, or lifecycle state.

### 6.3 Preserve and complete domain APIs

Keep the current `/api/molbio-ngs` sample, reference, state, member-receipt, evidence, and history routes. Change their authority source from the unavailable stub to the real binding adapter.

Freeze this route-level authorization matrix:

| Mutation class | Required authority | Additional gate |
|---|---|---|
| Domain initialize and binding reverify | persisted Project owner | exact hierarchy, expected Domain revision, idempotency key |
| Domain v2 create/patch, governance/status change, archive, and restore | persisted Project owner | expected head generation and current binding command where applicable |
| Local state, sample, managed-reference, evidence attachment/assessment, and domain-member-receipt writes | persisted Project owner | current acknowledged binding; exact Domain ownership; idempotency and generation checks where the native contract supports them |
| Project Dataset create/revise/archive/restore and Project attachment | persisted Project owner | exact Project → Global Experiment → Domain Experiment hierarchy; expected head generation for revise/archive/restore; required idempotency key for create/revise |
| Workflow Plan create/revise/prepare, launch-context issue, launch, retry, resubmit, and cancellation | persisted Project owner | active acknowledged binding; immutable preparation and launch authority; managed scheduler ownership |
| Canonical Mol Bio Toolkit mutations that save revisions or operations into this Project | persisted Project owner resolved from opaque Project context | exact source revisions; server-derived Project/Domain attribution; active acknowledged binding |
| Project-scoped NGS Toolkit submission and result/evidence attachment | persisted Project owner resolved from opaque launch context | canonical Job service and exact terminal receipt |
| System-wide panel seeding, backup, export verification, connector administration, and health operations that mutate global state | authenticated operator or admin | dedicated system-operation policy; no Project owner substitution |
| Manual connector dispatch and reconciliation | unavailable | authenticated callers still receive HTTP `409`; managed worker is sole owner |

Every mutation derives the effective actor and roles at the authenticated server boundary. Request bodies cannot supply actor, owner, role, attribution, acknowledgement, canonical digest, or relationship authority. The audit record stores the server-derived principal, effective authorization class, Project ID when applicable, binding revision when required, request identity, and result identity. Missing, stale, foreign, or ambiguous authorization fails closed before any native or global write. Read-only preview or validation routes stay non-authoritative and cannot persist Project state.

Add exact detail/list operations only where a receipt kind lacks an independently retrievable native authority. Current stable routes remain compatible.

All new and completed collections use opaque keyset cursors. Default limit is 50 and maximum limit is 100. Ordering is `(created_at DESC, stable_id DESC)` unless a scientific order is part of the immutable contract. Dataset members use `(ordinal ASC)` because ordinal is unique within one immutable revision. The opaque member cursor binds `revision_id`, last ordinal, page limit, and the authority-context digest; a cursor from another revision, Project, Domain, or profile is invalid. Each page returns `items`, `next_cursor`, `has_more`, and `total` only when the count is available without an unbounded scan. Exact Dataset revisions with more than 100 members expose member pages; the revision envelope never embeds an unbounded membership array. Invalid, foreign, or stale cursors fail with `422`.

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

- `dataset-create:` plus the lowercase SHA-256 of canonical JSON containing `project_id` and `domain_id`;
- `dataset-revision-create:` plus the lowercase SHA-256 of canonical JSON containing `dataset_id`;
- `dataset-archive:` plus the lowercase SHA-256 of canonical JSON containing `dataset_id`;
- `dataset-restore:` plus the lowercase SHA-256 of canonical JSON containing `dataset_id`.

The claim key is `(scope, Idempotency-Key)`.

After strict syntax normalization, server-derived path identity, and owner authorization, the service computes `request_sha256` over the complete canonical request, including path IDs, body, and operation kind. `normalized_request_sha256` is this same digest. It then resolves the claim before current-head, lifecycle, receipt-availability, or native-authority validation. An existing same-key/same-hash claim returns the byte-identical stored `response_json` and original status semantics with the same Dataset or revision ID, even when the first successful mutation advanced the head. Create and revision-create replay as HTTP `201`; archive and restore replay as HTTP `200`. The fixed per-operation success status requires no new status column in `idempotency_claims`. An existing same-key/different-hash claim returns `409 idempotency_conflict`. When no claim exists, the service verifies the current hierarchy, lifecycle, generation, receipts, metadata, and native authority, then records the claim through existing global `idempotency_claims` in the same database transaction as the Dataset mutation. A uniqueness race reloads the winning claim and applies the same replay/conflict rule. Commit failure leaves neither mutation nor claim. A concurrent duplicate cannot create a second Dataset or revision or repeat a lifecycle transition. A stale `expected_head_generation` on a new key returns `409 stale_generation`. Archive of archived state or restore of active state returns `409 invalid_lifecycle_transition`. Missing or malformed keys, unknown fields, invalid member ordering, duplicate members, invalid metadata, and unsupported roles return typed `422` before any Dataset write.

Successful Dataset create returns HTTP `201` with exactly `schema=bms.dataset-head.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, `dataset_kind`, `head_generation=0`, `lifecycle_state=active`, `normalized_request_sha256`, and `created_at`. Global V11 adds nullable `aggregate_heads.dataset_kind` under the exact migration and legacy rules in section 5.1; Dataset creation writes the registry ID there and never stores it only in `description`. The existing `aggregate_created` audit event for a Dataset records exactly `dataset_kind`, `change_summary`, `normalized_request_sha256`, and the server-derived actor ID in addition to stable resource identity. Successful revision create returns HTTP `201` with exactly `schema=bms.dataset-revision.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, immutable `revision_id`, `revision_number`, new `head_generation`, `member_count`, `revision_sha256`, `normalized_request_sha256`, and `created_at`. Its immutable revision payload records `change_summary`. Successful archive or restore returns HTTP `200` with exactly `schema=bms.dataset-head.v1`, `project_id`, `global_experiment_id`, `domain_id`, `dataset_id`, `dataset_kind`, the new `head_generation`, resulting `lifecycle_state`, `normalized_request_sha256`, `created_at`, and `updated_at`. The same transaction records `change_summary` and server-derived actor in the lifecycle audit event. Every route verifies the full Project → Global Experiment → Domain Experiment → Dataset hierarchy and owner authority.

The exact-revision route returns revision metadata and at most the first 100 members. `members` is omitted when the revision has more than 100 members; the response then includes the member-page URI. The member route uses the frozen Dataset ordering and page contract. Historical revision retrieval never resolves the current head.

Compatibility workspace routes call the same services.

### 6.5 Canonical Domain Workflow Plan, launch, and result routes

This NGS/MolBio SOW is the single contract and implementation owner for these shared wrappers. Both NGS/MolBio and Protein In Silico consume them with domain-specific capability adapters. Use:

```text
D = /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
```

| Operation | Method and path | Strict request | Success identity | Concurrency and idempotency |
|---|---|---|---|---|
| Attach verified native authority | `POST D/attach` | existing closed attachment request: `adapter_id`, `entity_id`, `operation`, `role`, optional `note`, `expected_head_generation` | `bms.global.attachment-receipt.v1`, external receipt ID, lineage-edge ID, new Project generation | normalized verified request is idempotent; stale Project generation is `409` |
| List/get plans | `GET D/plans?cursor=&limit=` and `GET D/plans/{plan_id}` | bounded query only | stable plan IDs, heads, current revision, and draft generation | no mutation |
| Create plan | `POST D/plans` | exactly `name`, registered `capability_id`, `expected_domain_revision_id` | `plan_id`, server-derived family/adapter, draft ID/generation `0`, Domain revision | required `Idempotency-Key`; changed request on same key is `409` |
| Replace draft | `PUT D/plans/{plan_id}/draft` | exactly `expected_draft_generation`, complete closed workflow payload | draft ID, new generation, payload digest | generation CAS |
| Publish revision | `POST D/plans/{plan_id}/revisions` | exactly `expected_head_generation`, `expected_draft_generation`, `change_summary` | immutable revision ID/number and payload/dependency digests | both generations match |
| List/get exact revisions | `GET D/plans/{plan_id}/revisions?cursor=&limit=` and `GET D/plans/{plan_id}/revisions/{revision_id}` | bounded query only | exact immutable revision | no head substitution |
| Prepare revision | `POST D/plans/{plan_id}/revisions/{revision_id}/preparations` | exactly ordered `input_dataset_revision_ids` | preparation ID, normalized-request digest, validation ID/receipt digest, status, expected cardinality | required `Idempotency-Key`; creates no Job |
| Get preparation | `GET D/preparations/{preparation_id}` | none | immutable normalized request, requested/effective settings, source authorities, safe scheduler summary, validation | commands/paths omitted |
| Issue prepared handoff | `POST D/preparations/{preparation_id}/launch-contexts` | exactly `return_uri` | `bms.launch-context.v2` bound to preparation, request digest, validation receipt, hierarchy, state, expiry | required `Idempotency-Key`; only adapters declared `typed_launcher_handoff` can use it |
| Launch run group | `POST D/run-groups` | ordered `preparation_launches` using the adapter-mode rule below | `bms.run-group.v1`, group/request/state/generation and ordered run/attempt/preparation plus context or dispatch identities | required `Idempotency-Key`; all bindings commit before launch intent becomes visible |
| Get run group | `GET D/run-groups/{run_group_id}` | none | exact runs, attempts, preparations, handoff contexts, canonical Jobs/runs, receipts, state | no mutation |
| Retry | `POST D/run-groups/{run_group_id}/retry` | `expected_run_group_generation`, ordered `replacements` with run ID, preparation ID, and fresh context only for handoff adapters | fresh attempts with `retried_from` and updated generation | required `Idempotency-Key`; reuse/successor preparation rule applies |
| Resubmit | `POST D/run-groups/{run_group_id}/resubmit` | `expected_run_group_generation`, ordered `preparation_launches` | new group with `resubmitted_from` | required `Idempotency-Key`; source terminal |
| Cancel | `POST D/run-groups/{run_group_id}/cancel` | `expected_run_group_generation`, bounded `reason` | cancellation receipt and updated group/runs/attempts | required `Idempotency-Key`; releases reservations and blocks retry/comparison |
| Reopen exact result | `GET D/results/{receipt_id}/surface` | none | validated `bms.result-surface.v1` | exact receipt only |
| Create comparison | `POST D/comparisons` | `compatibility_adapter_id`, ordered members with receipt/role/ordinal, `expected_project_head_generation` | `bms.global.comparison-receipt.v1`, native comparison identity, global receipt/digest/edges/surface/new generation | required `Idempotency-Key`; compatibility proves before creation/attachment |

`preparation_launches` has 1..128 unique preparation IDs. The registry assigns exactly one mode to each adapter: `managed_materialization` or `typed_launcher_handoff`. A managed item omits `launch_context_id` and creates one managed dispatch intent. A handoff item supplies one v2 context bound to the same preparation; the transaction reserves it to the new attempt and returns the canonical launcher URI without creating a scheduler-materialization outbox row. The dedicated launcher submits the canonical Job/request under that attempt and consumes the context. No context can cross an attempt, retry, resubmit, run, Domain, or Project.

The draft is one complete closed `bms.workflow.<family>.v1` payload. The capability inventory derives and fixes family, adapter, allowed model/mode, launch mode, and result contracts. The shared wrapper accepts only the registered existing workflow fields: schema, family, contract version, adapter, nodes, edges, parameters, scheduler, stage/backend where applicable, source receipt IDs, expected cardinality, and dependencies. Capability schemas close `parameters` and `scheduler.params`. Commands, paths, scripts, imports, arbitrary model IDs, actor identity, digests, receipt bodies, canonical Job IDs, effective settings, and caller-authored lineage fail before persistence.

New planned handoffs use `bms.launch-context.v2` from section 5.1 with preparation ID, normalized-request digest, validation receipt ID/digest, and nullable reserved attempt ID. Historical v1 stays readable and cannot launch a prepared plan. Existing dedicated launchers remain native submission authorities and must match every immutable preparation field before canonical submission. Managed materializers read the attempt's direct preparation authority.

All wrappers use `additionalProperties: false`, persisted Project-owner authorization, full hierarchy verification, and bounded responses. Unknown/foreign authority is `404`; stale generation, idempotency conflict, expired/consumed context, digest mismatch, or incompatibility is `409`; malformed/unsupported input is `422`; unavailable single managed owner is `503`. Manual dispatch and reconciliation stay HTTP `409`. `/api/experiment-workspaces` compatibility routes call these services and do not define a second contract.

### 6.6 Resource-admission authority

The NGS-owned shared managed-workflow admission service owns one durable allocation ledger across all active managed BMS workflow children on the deployment. It atomically reserves effective CPU threads and DRAM before dispatch or typed-launcher submission. `pending`, `dispatching`, `queued`, and `running` allocations count. Terminal or atomically cancelled allocations release once. A retry cannot overlap a live predecessor reservation. A launch that would exceed 24 threads or 96 GiB DRAM fails as `resource_admission_denied`; it is not queued and its request is not reduced. The ledger records policy source/version, owner/lease, Project/Domain/plan/preparation/attempt IDs, effective request, state, timestamps, release reason, and recovery evidence. Health reports aggregate reserved versus actual usage. Phase N0 reuses exact existing enforcement if found; otherwise this ledger and gate are new shared work.

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
| `ngs_reference_revision` | `bms.molbio-ngs.reference-revision.v1` | `(global_domain_experiment_id, reference_id, reference_revision_id)`; reference revision number | canonical FASTA bytes/content authority under the native reference contract | `ngs_reference` | `bms.ngs.reference-revision.adapter.v1` | `global_domain_experiment_id`, `reference_id`, `revision_id` | Missing |
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
- Project Manager issues one opaque launch context.
- NGS Toolkit redeems the context and restores exact Project, Global Experiment, Domain Experiment, binding revision, local state revision, and return URI.
- Canonical NGS submission persists that context in server-owned Job provenance.
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

**Work:** Re-pin `test`; inventory concurrent drift; freeze Domain v2, binding, event/ordering, acknowledgement, adapter, capability, parameter, criterion/evidence, pagination, payload-ownership, and deep-link schemas.

**Gate:** Every field maps to an existing native or global authority. File ownership is non-overlapping. Every advertised capability has one complete parameter inventory and ten-gate parity ledger.

### Phase N1: Additive migrations and binding adapter

**Work:** Add global V11/V12 and domain V4 equivalents; implement server-issued hierarchy receipts and versioned local bindings.

**Gate:** Fresh and upgraded stores match; old checksums remain unchanged; bootstrap replay and successor binding behavior are deterministic.

### Phase N2: Managed connector and convergence

**Work:** Implement global command processing, local outbox leasing, global inbox ingestion, acknowledgement, conflict, recovery, and health.

**Gate:** Restart does not duplicate state or receipts. Stale leases cannot acknowledge reclaimed work. A verified stale source can converge to current after revalidation. Reversed delivery, generation gaps, stale revisions, duplicate digests, and conflict recovery follow section 4.4 and cannot regress a projected head.

### Phase N3: Adapter and lineage closure

**Work:** Add every missing adapter, exact native resolver, result surface, and typed cross-receipt lineage edge.

**Gate:** The registered adapter set equals the accepted receipt-family set. Wrong owner, revision, generation, or bytes fail before attachment.

### Phase N4: Active toolkit and launch-context integration

**Work:** Enable domain mutation controls; wire Project context through Mol Bio Toolkit and NGS Toolkit; support plan, prepare, explicit launch, retry, resubmit, result reopen, and return.

**Gate:** One mounted operator flow crosses Project Manager, both toolkits, canonical Job authority, and exact result reopening without browser-owned identity.

### Phase N5: Datasets, ELN, shared-package acceptance, read models, and operations

**Work:** Complete exact Dataset revision flows, bounded projections, activity, ELN records, query isolation, pagination/response limits, resource admission, health, backup, and export. Add production writers/read projections for global-owned validation artifacts and bounded attempt-log records. Complete the versioned payload manifest, scanner, and retained release audit. Aggregate the accepted outputs from N1 through N5 into the single NGS-owned shared-package receipt defined in section 2.4. Domain scientific artifacts and full logs remain native and attach through verified receipts.

**Gate:** Old Dataset and local-state revisions reopen independently. Health includes connector lag, verification failures, resource reservations versus actual use, and verified backup/export times. The exact migration/schema/route/service/UI/health/audit identities all verify, the retained payload audit passes, and one complete `bms.shared-global-package-acceptance.v1` receipt is published. A partial receipt cannot pass this gate.

### Phase N6: Current-tree verification and Development acceptance

**Work:** Run the approved focused checks, exact-tree review, Development deployment, health checks, and browser scenario.

**Gate:** Definition of done passes at one exact commit/tree and deployed build identity.

## 15. Verification scope

Test execution requires separate approval. Once authorized, use a bounded suite.

### 15.1 Backend contract checks

- Binding success, wrong parent, archived parent, stale revision, unavailable authority, and digest mismatch.
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
- Backup, restore, export, schema attestation, and restart reopening.

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
- Project export, store backups, health, and restart reopening include all new records.
- Focused current-tree checks pass after final source edits.
- Independent exact-tree review finds no release-blocking specification or scientific-integrity defect.
- `test` is pushed, Development runs that exact commit with one dispatcher owner, and browser acceptance passes.
- Production remains unchanged until separately authorized.

## 18. Likely implementation files

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
