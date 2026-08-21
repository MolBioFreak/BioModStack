# Global BMS Project and Experiment Manager Specification

**Status:** Finalized implementation-controlling specification; product implementation and Development acceptance remain INCOMPLETE
**Scope owner:** Global BMS manager and Protein In Silico domain owner. The NGS/MolBio SOW owns the shared global package named in section 0.6.
**Target product:** BioModStack
**Document type:** Product, architecture, persistence, API, UI, migration, and delivery specification

The tracked parent specification, the tracked NGS/MolBio child SOW, and their versioned schema/runtime records form one controlling package. Repository and release records bind the exact document hashes and source tree. This document does not embed a self-referential commit identity.

## 0. Current implementation status and gap assessment

This section records the latest retained implementation evidence. It does not narrow the controlling requirements or transfer acceptance from one domain vertical to another.

**Assessment baseline:** Development `test`, `origin/test`, canonical source, and live API at commit `e53e670db3baede36ec69d8257502964ce43d0c3`, tree `d27142e54de0c9102b421c0d63822143ac801b5b`, with a clean canonical checkout. The managed Development API, frontend, and workflow adapter owned ports `18002`, `18082`, and `18001`. Production was not changed. This is historical product evidence. Later specification-only commits do not upgrade it, and PM-02 must resample current remote and live identities before implementation.

**Program verdict:** INCOMPLETE. The implemented organizer provides substantial Slice A hierarchy and presentation behavior. Slice A acceptance, Slice B execution closure, and the complete Definition of Done remain open.

### 0.1 Browser-verified PASS at the assessment baseline

- The Projects index rendered create, search, lifecycle filter, archive filter, recent ordering, direct reopen, active-experiment counts, and unresolved-failure counts.
- A new Development Project was created through the browser, reopened at its exact path, edited to immutable revision 2, and retained the server-derived owner and generation.
- A Global Experiment was created through the browser. Tree, relationship-map selection, inspector selection, validated `focus`/`selected` URL state, browser back, and browser forward stayed synchronized.
- Global Experiment archive and restore completed through explicit UI confirmations. Archival remained non-destructive and the restored identity was unchanged.
- A Project-level decision was appended through the browser and persisted as an immutable research record.
- After a managed API/frontend restart, the Project, Global Experiment, Project revision, exact selection URL, and decision row reopened from the Development stores.
- The retained BFX6NB Project rendered the Project tree, relationship map, selected-node inspector, bounded workflows, external receipt nodes, three run rows, attempt provenance, and accessible relationship list.
- An exact molecular reference link reopened Mol Bio Toolkit with the same Project, Global Experiment, Domain Experiment, local state revision, molecular sequence ID, and immutable molecular revision ID.

### 0.2 PARTIAL or MISSING gates

- **Slice A complete operator path: MISSING.** Protein In Silico Domain Experiment creation is explicitly closed in the Project Manager until accepted capabilities and producer-native receipt selectors are advertised. The required two-domain Slice A scenario cannot run.
- **Required scientific metadata controls: PARTIAL.** The current create/edit dialog exposes Project name/objective and Global Experiment name/objective/scientific question. It does not expose the complete controlling Project and Global Experiment metadata sets, including contributors, tags, dates, hypothesis, priority, success criteria, review summary, and conclusion.
- **NGS/MolBio Domain creation: FAILED.** The browser returned `installed source authority digest mismatch: platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx`. No Domain Experiment was created.
- **ELN-lite visibility: PARTIAL.** Browser append persisted the Project decision, but no Project-level Notes or Decisions virtual folder, map node, activity item, or inspector section rendered it. The record was available only through the API.
- **Receipt freshness and canonical reopening: PARTIAL.** The retained BFX6NB map rendered 19 sample/reference receipts as `Stale` because persisted verification had no bounded freshness or re-verification receipt.
- **Run lifecycle and reconciliation: PARTIAL.** Three BFX6NB Workflow Runs rendered `Dispatch Pending` with one attempt each and no binding, runtime, or terminal authority.
- **Operational closure: MISSING.** The live global store had zero validation-artifact rows, zero bounded attempt-log rows, no retained shared-global-package acceptance receipt, no retained payload-ownership audit, and no verified Project export receipt. The latest verified backup/restore receipt was from 2026-08-14 and bound to an older source revision.
- **Worker/connector convergence: PARTIAL.** One worker owned the Development lease, but health reported `failure_count=1`, three NGS/MolBio outbox conflicts, and two deferred inbox generation gaps.
- **Complete native attachment coverage: PARTIAL.** Generic Project attachment and exact adapter infrastructure exists, but the required producer-native `Add to Project` action is not exposed and accepted across every scientific surface in the controlling denominator.
- **Complete Slice B and all-capability Protein coverage: MISSING.** Current browser evidence cannot establish planning, preparation, typed-launcher handoff, dispatch, terminal reconciliation, retry/resubmit/clone semantics, or every installed Protein/MD capability through one accepted Project path.
- **Independent exact-tree review and complete Development acceptance: MISSING** for the assessed tree.

### 0.3 Gate result

The weakest required gate controls the verdict. Current hierarchy and presentation work is implemented and useful, but the Global BMS Project and Experiment Manager is not complete under sections 18 through 21. The next implementation tranche must first restore exact installed-source authority, then close Protein Domain creation, required metadata controls, native attachment exposure, Project-level ELN rendering, receipt re-verification, dispatcher/connector convergence, operational receipts, and the retained full browser scenarios.

### 0.4 Operator-reported presentation gaps

The 2026-08-20 structural merge and Toolkit-height change are historical implementation evidence. Their visual acceptance is **REOPENED / PENDING** because Christian reported that the Project layer consumed about half of the usable page, duplicated content, and presented incongruent Project/Domain controls above Mol Bio Toolkit. DOM nesting, component counts, and a larger CSS height do not close this gate.

PM-01 applies to the selected-context default state of `/designer` and `/ngs`. It does not replace the three-region `/projects/{project_id}` Research Map composition in section 12.1.

The selected-context default state must satisfy all of these conditions:

- render one compact management band before the active Toolkit. It contains one hierarchy breadcrumb/selector, one normalized state/readiness summary, and one primary action group;
- show local NGS/MolBio Project authority once. Optional broader-Project associations use a separate labeled disclosure and never appear as a second owning Project;
- move Project/Experiment creation and edit forms into one modal or side sheet. Keep association management, provenance, history, and advanced controls in collapsed disclosures. Opening those transient surfaces does not redefine default-state geometry;
- omit repeated objective, owner, status, selected revision, identifier, and action blocks from the Domain workspace and Toolkit when the management band already shows them. Detail/history views may repeat a value only with an explicit detail label;
- expose one compact Domain section navigator. It cannot render as another page header or card-sized management window;
- preserve every owner-authorized function in the controlling SOW. Compaction cannot hide an unavailable control as though it were complete;
- keep Toolkit content usable through one Toolkit-owned scrolling region. The page shell and management band cannot impose a second fixed-height scrolling region, clip controls, or create a blank page-sized area;
- keep headings, terminology, selected IDs, disabled-state reasons, and deep-link/return context congruent across `/designer` and `/ngs`.

The measurement frame is exact. `content_top` is the lower edge of the persistent application header. `content_bottom` is the viewport bottom. `content_height = content_bottom - content_top`. `management_height` is the distance from `content_top` to the active Toolkit's top border. `toolkit_visible_height = max(0, min(toolkit_bottom, content_bottom) - max(toolkit_top, content_top))`. With one valid selected Project/Global/Domain context, all secondary disclosures closed, browser zoom 100%, and no modal open:

- `management_height / content_height <= 0.35`;
- `toolkit_visible_height / content_height >= 0.60`;
- the active Toolkit begins inside the first viewport.

These thresholds apply at 1366×768, 1920×1080, and 2560×1440. Tablet and phone layouts follow section 12.16 and remain functionally complete without the desktop ratios. PM-01 technical acceptance includes exact-route captures for both `/designer` and `/ngs`, computed rectangles and ratios, stable semantic control counts, keyboard focus traversal, and first-use plus selected-context readback. It contains no Development-runtime, PM-12 browser, or operator-decision receipt. Christian's later signed `bms.operator-visual-acceptance.v4` receipt is the closing subjective usability gate inside PM-12.

### 0.5 Pending-fix packages

Each package remains pending until its acceptance evidence exists. `Behavior owner` identifies the accountable product-contract owner. Shared-file ownership follows section 0.6. A domain extension owner may add rows or adapters through an accepted shared contract and cannot fork it.

| Package | Behavior owner | Prerequisites | Pending correction and acceptance boundary |
|---|---|---|---|
| PM-01 UI implementation and technical viewport acceptance | NGS/MolBio frontend owner | PM-03A, PM-03B, PM-04, PM-05, and the operator controls from PM-06 mounted | Implement section 0.4 after the final control denominator is visible. Accept pre-deployment geometry, semantic duplicate-control inventory, deep-link/reload behavior, and accessibility checks. Christian's signed decision is excluded and remains a PM-12 gate. |
| PM-02 source and exposure authority | NGS/MolBio shared-package owner | None | Reconcile every installed source pin and runtime denominator. Capability and Dataset exposure derives from accepted runtime evidence. Accept with exact source/tree seal, negative drift behavior, truthful unavailable state, and matching API/UI exposure. |
| PM-03A global hierarchy and Protein authoring | Global/Protein owner | PM-02 | Add Project v2, complete Project/Global Experiment metadata controls, and enable Protein Domain creation only through accepted capability and producer-native receipt authorities. Accept create/edit/revision/archive/restore with exact typed readback. |
| PM-03B NGS/MolBio hierarchy authoring | NGS/MolBio owner | PM-02, accepted Project v2 runtime/service contract from PM-03A, and accepted PM-10A connector foundation | Complete local/global NGS/MolBio authoring, Domain v4 creation, provisioning, and optional broader-Project links. Accept both Project scopes and cross-scope negative cases. |
| PM-04 native attachment and context | NGS/MolBio shared-package owner for global attachment; each domain owner for native triggers | PM-02; accepted PM-03A hierarchy identity | Expose receipt-backed `Add to Project`, literal deep-link consumers, and server-verified opaque context. Accept the producer/consumer matrix, cross-Project denials, exact reopen/return, and zero copied scientific payloads. |
| PM-05 active NGS/MolBio authoring | NGS/MolBio owner | PM-03B and PM-04 | Mount owner-authorized sample, managed-reference, evidence, Dataset, Plan, and preparation controls. Accept the complete browser mutation/revision/reopen path with generation and authorization failures visible. |
| PM-06 shared read model and ELN integrity | NGS/MolBio shared-package owner | PM-02 and PM-03A; final acceptance uses PM-04 and PM-05 records | Implement the canonical composite route in section 12.9, transactionally maintained count/status projections, opaque authority-bound cursors, complete response provenance, persisted adapter versions, ELN replacement/reopen, and actual replica relations. Accept bounded multi-page API/UI closure and exact digest closure. |
| PM-07 receipt and lineage reconciliation | NGS/MolBio shared-package owner; domain adapters remain domain-owned | PM-02 and PM-04 | Add bounded freshness, re-verification, immutable successor receipts, and exact Domain-scoped lineage. Accept fresh/stale/missing/tampered/reverified cases for every accepted family across restart. |
| PM-08A shared plan and execution control | NGS/MolBio shared-package owner | PM-02, PM-03B, PM-04, PM-05, PM-06, PM-07 | Complete Plan, preparation, launch, retry, resubmit, clone, fenced cancel, comparison, and result-control wrappers. Accept NGS/MolBio N5 execution semantics without a second scheduler or launch authority. |
| PM-08B Protein and MD capability closure | Global/Protein owner | Accepted PM-08A shared package and PM-03A | Add Protein/MD adapters only. Accept one Project-governed native chain for each installed in-scope capability and approved compatible comparison. |
| PM-09 artifacts, logs, and result surfaces | NGS/MolBio shared-package owner for writers; domain owners publish typed inputs | PM-08A; Protein rows also require PM-08B | Activate the global artifact/blob and bounded log writers, then expose terminal artifacts and canonical result surfaces. Accept persisted rows, digest verification, bounded display, and exact native reopening. |
| PM-10A connector foundation | NGS/MolBio shared-package owner | PM-02 | Implement the durable global-to-domain command, inbox, acknowledgement, lease, retry, conflict, and binding-status foundation. Accept initialize/reverify idempotency, ordered delivery, duplicate suppression, restart recovery, and one managed owner before PM-03B consumes it. |
| PM-10B worker convergence and operations | NGS/MolBio shared-package owner | PM-10A and accepted PM-07 through PM-09 | Resolve conflicts and deferred gaps, complete worker recovery/reconciliation, and expose migration, lag, adapter, verification, backup, and export health. Accept restart recovery, zero unresolved conflict/gap, verified backup/restore/export, and no orphan execution. |
| PM-11 retained shared-package evidence | Release integrator resolving owner-issued evidence | Exact accepted `bms.project-manager.package-acceptance.v3` receipts for PM-01, PM-02, PM-03A, PM-03B, PM-04, PM-05, PM-06, PM-07, PM-08A, PM-08B, PM-09, PM-10A, and PM-10B | Resolve the protected package-owner registry and owner-authenticated coverage bodies. Produce payload-ownership audit, `bms.shared-global-package-acceptance.v5`, five typed migration attestations, quiescence, and v3 backup/restoration/export receipts for one immutable candidate commit/tree. PM-11 contains no Development deployment, final review, browser run, or operator decision. |
| PM-12 final acceptance | Release integrator; Christian owns final visual decision | Accepted PM-11 v4 receipt | Run three-authority exact-tree review, managed Development deployment, typed health, Slice A, full Slice B, N6 browser acceptance, exact retained-version restart/reopen, and Christian visual acceptance without workarounds. Publish one `bms.project-manager.final-acceptance.v4` that resolves every package-contained typed receipt against one source commit/tree and deployed build. Production promotion remains a separate decision. |

### 0.6 Execution order and contract precedence

`PM-02` is the first eligible execution package. After PM-02 passes, PM-03A, PM-04 shared foundations, PM-06 foundations, PM-07 foundations, and PM-10A may proceed only with non-overlapping file ownership. PM-03B follows the accepted Project v2 runtime/service contract from PM-03A and the accepted PM-10A connector foundation. PM-05 then mounts the complete NGS/MolBio control denominator. PM-06 and PM-07 must reach acceptance before PM-08A starts. PM-01 runs after those controls exist and closes only its technical pre-deployment receipt. PM-08A precedes PM-08B. PM-09 follows the execution writers. PM-10B starts only after PM-07 through PM-09 are accepted and closes convergence plus operations. PM-11 resolves all thirteen pre-deployment package receipts. PM-12 alone performs deployment, browser acceptance, and Christian's signed visual decision.

The NGS/MolBio child SOW is authoritative for shared migrations, nested Plan/Dataset/launch APIs, connector state, pagination policy, artifact/log writers, resource admission, payload ownership, and their shared-package receipt. This parent is authoritative for the hierarchy, global product composition, Protein payloads/adapters, Slice A, and whole-program acceptance. A conflict uses the more specific child contract only inside its assigned shared-package boundary. All other conflicts use this parent. Runtime source evidence can reveal a gap; it cannot silently override either contract.

Behavior ownership does not grant overlapping edits to shared files. The NGS/MolBio shared-package owner is the sole editor of global migration ledgers/files, shared schema/runtime registries, common Plan/Dataset/launch services, the composite read-model implementation, artifact/log writers, shared worker/connector code, resource admission, payload-audit code, and shared health/backup/export projections. A parent behavior owner supplies its closed migration and registry requirements to that file owner and reviews the result inside the owning PM gate. The exact path/symbol assignment is recorded before each package starts. Two active packages cannot edit one path or symbol.

## 1. Purpose

BioModStack requires one durable project and experiment manager above its scientific modules. This manager must organize related computational research, molecular records, sequencing work, and later instrument activities without replacing the systems that own those records.

The manager must support research that crosses module boundaries. One project can contain multiple global experiments. A global experiment can contain several typed domain experiments. Each domain experiment preserves its own scientific vocabulary and canonical data authority.

The initial domain coverage is:

1. Protein In Silico experiments, owned by this delivery lane.
2. NGS / MolBio experiments, owned by a separate delivery lane.

Liquid-handler experiments are a future adopter. This specification reserves a generic adapter boundary for them. It does not define liquid-handler workflow semantics.

## 2. Controlling hierarchy

The canonical hierarchy is:

```text
Project
└── Global Experiment
    ├── Protein In Silico Experiment
    │   ├── Workflow Plans
    │   ├── Prepared Executions
    │   ├── Computational Runs
    │   ├── Analyses and Comparisons
    │   └── Results and Evidence
    │
    └── NGS / MolBio Experiment
        ├── Molecular Records
        ├── Workflow Plans
        ├── Sequencing and Analysis Runs
        └── Results and Evidence
```

A Project is the durable research container. A Global Experiment is one coherent study, question, or experimental tranche inside a Project. A Domain Experiment is the typed scientific implementation of part of that study.

A Global Experiment can contain multiple Domain Experiments of the same type. This supports repeated protein campaigns, related controls, independent NGS experiments, and staged follow-up work.

## 3. Product goals

The implementation must provide:

- durable Projects that can be created, reopened, searched, revised, completed, and archived;
- durable Global Experiments inside Projects;
- typed Domain Experiments inside Global Experiments;
- a complete Protein In Silico experiment layer;
- a stable contract for the separate NGS / MolBio experiment layer;
- immutable scientific configuration revisions;
- explicit draft, preparation, launch, runtime, review, and completion states;
- verified links to records owned by other BMS databases and services;
- cross-workflow lineage and result grouping;
- project-level and experiment-level notes, evidence, and conclusions;
- direct reopening of the canonical domain UI for each linked record;
- one canonical main Project Manager for `global` Projects, plus domain-owned authoring surfaces for explicitly scoped local Projects that use the same Project authority and contracts;
- typed UI and API parity for all manager operations;
- recovery-safe dispatch and reconciliation;
- export and backup support for global metadata;
- clear evidence when a linked domain record is missing, stale, or inconsistent.

## 4. Non-goals

This tranche excludes:

- a replacement for canonical BMS job, result, MD, CM, MolBio, NGS, ONT, or BioXP stores;
- copying domain payloads into `experiments.db`;
- a universal scientific schema that flattens all domains;
- liquid-handler workflow implementation;
- BioXP protocol or movement integration;
- autonomous experiment selection;
- autonomous scientific conclusions;
- an internal LLM campaign planner;
- a rewrite of existing dedicated launchers;
- a rewrite of Results Viewer;
- a new scheduler;
- direct shell commands inside saved workflow definitions;
- cross-database foreign keys;
- destructive migration of current global workspace data;
- enterprise multi-tenant access control.

## 5. Existing foundation

The current global backend already provides a strong substrate in:

- `platform/api/experiment_database.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_models.py`
- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- `platform/api/routers/experiment_workspaces.py`
- `platform/api/services/conformational_mapping/global_adapter.py`

It already supports:

- a dedicated `experiments.db` store;
- workspace, experiment, workflow, and dataset aggregates;
- stable resources and mutable aggregate heads;
- immutable revisions and dependency edges;
- workflow drafts;
- workflow preparations;
- validation receipts;
- run groups, workflow runs, and attempts;
- idempotency claims;
- dispatch outbox rows;
- runtime and terminal receipts;
- lineage edges and audit events;
- external entity receipts;
- content-addressed artifact and log schemas;
- backup, export, and analytics operations.

At the original design baseline, the global UI was absent and Conformational Mapping was the only substantive scientific adapter. Section 0 records the later implementation state. Existing hierarchy, UI, adapter, worker, and schema code is reusable evidence only until its package gate passes. Artifact, log, sync, and dispatch facilities remain incomplete where section 0 identifies missing writers, reconciliation, or retained evidence.

## 6. Authority model

### 6.1 Global manager authority

The global manager owns:

- Project identity and metadata;
- Global Experiment identity and metadata;
- Domain Experiment membership and global lifecycle context;
- workflow-plan membership;
- preparation and grouped-launch intent;
- global lineage among domain-owned records;
- global evidence references;
- project and experiment notes;
- review and conclusion records;
- cross-domain organization;
- archive state;
- adapter registration;
- dispatch intent and reconciliation history.

### 6.2 Domain authority

Domain systems retain authority for their scientific records.

| Domain | Canonical authority |
|---|---|
| Generic computational execution | Core `Job` and scheduler/runtime records in `biomodstack.db` |
| Protein designs and result rows | Existing job, design, result, and artifact authorities |
| Conformational Mapping | `ConformationalMappingRequest`, CM child jobs, manifests, and result contracts |
| Molecular dynamics | `MdRun`, MD child stages, artifacts, and analysis rows |
| FrustraMPNN | Immutable FrustraMPNN result, landscape, comparison, and guidance authorities |
| Molecular records | `molbio.db` documents, revisions, operations, primers, constructs, and PCR records |
| ONT acquisition | `OntInstrumentRun`, events, preflight, and terminal artifact manifest |
| NGS analysis | Canonical NGS jobs, artifacts, manifests, comparison panels, and receipts |
| BioXP | Robot-owned capability and command receipts, with BMS as a typed relay |

The global store must never become a second authority for these records.

### 6.3 Receipt rule

Every cross-store link must use a verified immutable receipt. The receipt must identify:

```json
{
  "schema": "bms.global.external-entity-receipt.v1",
  "store_id": "core|molbio|ont|bioxp|other-approved-store",
  "entity_kind": "canonical typed kind",
  "entity_id": "opaque canonical ID",
  "entity_revision_id": "immutable revision ID when supported",
  "content_digest": "sha256 hex digest",
  "contract_digest": "sha256 of normalized contract when content digest is unavailable",
  "source_build_revision": "source/runtime revision when applicable",
  "verified_at": "UTC timestamp",
  "verifier_id": "server-owned adapter identity",
  "reopen_route": {"template_id": "bms.route.<registered>", "path": "/same-origin/path", "query": {}},
  "metadata": {"schema_id": "bms.<registered>.v1", "content_sha256": "sha256 hex digest", "canonical_size_bytes": 2, "payload": {}}
}
```

The complete receipt validates `docs/specs/schemas/external-entity-receipt-v1.schema.json`. Route templates and query keys are registered per adapter. `query` is a closed key-to-value map, so one key cannot occur twice. The server sorts query keys by RFC 8785 canonical member order before route digesting and URI construction. The runtime validator rejects schemes, authorities, protocol-relative paths, dot segments, encoded separators, backslashes, fragments, control characters, unknown templates/keys, routes outside the current same-origin BMS deployment prefix, and any noncanonical or duplicate query representation before receipt issuance. Metadata validates its registered schema, digest, and canonical byte cap, then applies the versioned payload-ownership manifest; scientific payload bytes are forbidden.

The server-owned adapter must query the canonical source before issuing a receipt. The generic HTTP caller cannot self-assert record existence, route, metadata, or digest correctness.

A missing source record, digest mismatch, or unsupported entity kind must fail closed.

Each receipt must contain `content_digest`, `contract_digest`, or both. The adapter must state which bytes or normalized contract produced each digest.

## 7. Terminology and object contracts

### 7.1 Project

A Project is the operator-facing form of the existing global `workspace` aggregate. New Project creation uses `bms.project.v2`, frozen at `docs/specs/schemas/project-v2.schema.json`. Project scope is immutable authority and cannot remain an unvalidated extension inside `bms.project.v1`.

Required Project v2 revision fields:

```json
{
  "schema": "bms.project.v2",
  "project_scope": "global|ngs_molbio_local",
  "name": "string",
  "description": "string",
  "research_objective": "string",
  "owner": "non-empty server-derived principal ID",
  "contributors": ["string"],
  "tags": ["string"],
  "status": "draft|active|on_hold|completed|archived",
  "start_date": "date or null",
  "target_end_date": "date or null",
  "external_references": [
    {
      "kind": "doi|url|accession|ticket|other",
      "value": "string",
      "label": "string"
    }
  ],
  "created_by": "non-empty server-derived principal ID",
  "change_summary": "string",
  "needs_metadata_review": false
}
```

All nested schemas are closed. `project_scope`, `owner`, and `created_by` are immutable after create. A Project-owner transfer requires a later explicit authorization contract and is outside this tranche. Create requests omit `owner`, `created_by`, and `needs_metadata_review`; the authenticated server derives them. Patch requests carry `expected_head_generation`, omit immutable fields, and replace complete arrays when supplied.

Historical conforming `bms.project.v1` revisions remain byte-for-byte readable. A v1 Project without `project_scope` is treated as `global` for read/filter compatibility only. Current historical v1 bytes that already contain `project_scope` remain readable as `legacy_scoped_v1`; they are not declared schema-conforming. The sole first-mutation path is `POST /api/projects/{project_id}/upgrade`. Its closed body supplies a complete v2 successor and the server applies the exact scope rule above. Missing v2 metadata is supplied explicitly as a bounded value, empty array, or nullable value; the server never infers it from names, routes, children, links, or UI origin. It derives `needs_metadata_review=true` when the v1 bytes lacked any v2 metadata field. When the v1 head is archived, the requested non-archived status must equal the immediate non-archived predecessor. Ordinary patch, archive, and restore return `409 project_contract_upgrade_required` for a v1 head. No new v1 Project is created after PM-03A.

Project edits create immutable revisions and advance one generation-checked head. Archival changes lifecycle state and retains all children.

### 7.2 Global Experiment

A Global Experiment is one study or experimental tranche inside a Project. New creation uses `bms.global-experiment.v2`, frozen at `docs/specs/schemas/global-experiment-v2.schema.json`.

Required Global Experiment revision fields:

```json
{
  "schema": "bms.global-experiment.v2",
  "name": "string",
  "objective": "string",
  "scientific_question": "string",
  "hypothesis": "string or null",
  "description": "string",
  "status": "draft|planned|active|analysis|review|completed|blocked|archived",
  "priority": "low|normal|high|critical",
  "tags": ["string"],
  "shared_source_receipt_ids": ["receipt ID"],
  "shared_dataset_ids": ["dataset ID"],
  "comparison_plan": "string or null",
  "success_criteria": ["string"],
  "review_summary": "string or null",
  "conclusion": "string or null",
  "created_by": "non-empty server-derived principal ID",
  "change_summary": "string",
  "needs_metadata_review": false
}
```

The hypothesis field can be empty for exploratory work. Success criteria must be explicit before the experiment enters `active` state.

Create requests omit `created_by` and `needs_metadata_review`; the server derives them. Patch requests carry `expected_head_generation`, omit `created_by`, and replace complete arrays when supplied. Completion requires non-empty `review_summary` and `conclusion` as enforced by the frozen schema.

Historical `bms.global-experiment.v1` revisions remain byte-for-byte readable. The sole first-mutation path is `POST /api/projects/{project_id}/experiments/{experiment_id}/upgrade`. Its closed body supplies the complete v2 fields. Missing historical hypothesis, criteria, review, or conclusion is represented explicitly by the request's nullable value or bounded empty array, while the server derives `needs_metadata_review=true`; migration never invents scientific metadata. When the v1 head is archived, the requested non-archived status must equal the immediate non-archived predecessor. Ordinary patch, archive, and restore return `409 global_experiment_contract_upgrade_required` for a v1 head. No new v1 Global Experiment is created after PM-03A.

Domain Experiment membership comes from each child aggregate's immutable parent relationship. It is not duplicated inside the Global Experiment revision payload. `shared_dataset_ids` are organizational Dataset aggregate references for navigation and review. They are never resolved as scientific inputs. Workflow Plans and preparations pin exact Dataset revision IDs.

Completion requires a terminal review revision. The review can state inconclusive, failed, or negative results. Completion does not imply scientific success.

### 7.3 Domain Experiment

A Domain Experiment is a typed child of one Global Experiment. New Domain creation and the explicit first-mutation upgrade of a historical v1 or v2 Domain use the closed outer `bms.domain-experiment.v4` contract defined exactly in the NGS/MolBio child SOW section 6.1 and shared by both domain kinds.

The common v3 fields are `schema`, immutable `domain_kind`, immutable `domain_contract_version="3"`, `name`, `objective`, `status`, `tags`, exact `source_receipt_ids`, exact `dataset_revision_ids`, server-derived `created_by`, `change_summary`, one complete registered `domain_payload`, `domain_payload_canonical_size_bytes`, and `canonical_size_bytes`. Stable Dataset IDs and current Dataset heads are invalid scientific inputs. The domain payload must validate against the immutable schema and digest registered for the exact domain kind and version.

The NGS/MolBio child SOW owns the outer v3 schema implementation. The Protein owner supplies only the registered `bms.protein-in-silico-experiment.v3` payload schema and adapters. Neither owner can fork the outer contract. Historical v1 and v2 revisions remain byte-for-byte readable and cannot launch new planned work. Their first mutation must use the complete-v3 upgrade route and cannot rewrite historical bytes.

Workflow, activity, and result membership comes from child parent relationships and typed lineage edges. These dynamic memberships are not duplicated inside Domain Experiment revisions.

A Domain Experiment belongs to exactly one Global Experiment. A domain record can be referenced by several Domain Experiments when each relationship has an explicit lineage role. The global manager does not change the domain record's canonical ownership.

### 7.4 Workflow Plan

A Workflow Plan is the existing global workflow aggregate with these clarified rules:

- It belongs to exactly one Domain Experiment for all newly created plans.
- It can contain several typed nodes.
- Each node names a registered server-owned adapter.
- Saved revisions contain declarative scientific and scheduling parameters.
- Saved revisions cannot contain commands, scripts, executable paths, or arbitrary imports.
- Saving creates no job.
- Preparing creates no job.
- Launching one or more accepted preparations creates run and attempt records before scheduler materialization.
- Any edit after launch creates a new immutable revision.

### 7.5 Dataset

A Dataset is a stable global grouping of immutable members. It can contain:

- external entity receipt IDs;
- domain revision IDs represented by receipts;
- selected design IDs;
- structure or sequence identities;
- job artifact receipts;
- saved review selections;
- comparison cohorts.

The dataset stores membership and member digests. Canonical bytes remain in their owning store or artifact location.

### 7.6 Activity

An Activity is a global projection of work that occurred in a domain system. It can represent:

- a BMS job;
- an MD run;
- a CM request;
- a FrustraMPNN analysis;
- an ONT run;
- an NGS analysis job;
- a MolBio operation;
- a future instrument run.

Activities are linked by verified receipts. The global projection can show normalized state, timestamps, and navigation. The domain system remains the lifecycle authority.

### 7.7 Note, observation, and conclusion

Project notes and experiment notes are append-only records. Editing a note creates a replacement record linked to the original. The original remains visible in audit history.

An Observation records what was seen. A Conclusion records an operator-authored interpretation. Automated analysis can publish Evidence. It cannot publish an operator conclusion.

Required common fields are frozen at `docs/specs/schemas/research-record-v2.schema.json`:

```json
{
  "schema": "bms.research-record.v2",
  "resource_id": "server-issued opaque ID",
  "project_id": "server-derived owning Project ID",
  "record_kind": "note|observation|conclusion|decision",
  "subject_resource_id": "server-derived opaque ID",
  "subject_revision_id": "exact immutable subject revision ID",
  "subject_revision_sha256": "sha256 of exact subject revision",
  "body": "non-empty string",
  "author": "server-derived principal ID",
  "source_receipt_ids": ["receipt ID"],
  "supersedes_record_id": "opaque ID or null",
  "created_at": "server-derived UTC timestamp",
  "content_sha256": "sha256 of canonical record JSON with this field omitted"
}
```

Historical `bms.research-record.v1` rows remain byte-for-byte readable. New appends use v2. A v1 edit is a v2 successor, never a rewrite. The server verifies every source receipt, exact subject revision/digest, record kind, owning Project, and replacement relation. It computes `content_sha256` from RFC 8785 canonical JSON for the complete record with `content_sha256` omitted. A successor names a record under the same owning Project and exact subject; cross-subject, self, missing, or cyclic replacement fails.

## 8. Protein In Silico experiment contract

### 8.1 Purpose

The Protein In Silico experiment layer organizes protein-AI and molecular-simulation work without replacing the typed launchers, scheduler, scientific stores, or result viewers.

### 8.2 Required domain payload

The exact payload is the tracked `schemas/ngs_molbio/protein-in-silico-experiment-v3.schema.json` package and its registered references. Its required top-level fields are:

```json
{
  "schema": "bms.protein-in-silico-experiment.v3",
  "experiment_mode": "exploration|design|redesign|prediction|validation|comparison|simulation|analysis",
  "scientific_objective": "string",
  "targets": ["bms.protein-target.v3"],
  "design_constraints": [],
  "planned_capability_ids": ["registered capability ID"],
  "comparison_groups": ["bms.protein-comparison-group.v1"],
  "validation_capability_ids": ["registered validation capability ID"],
  "acceptance_criteria": ["bms.scientific-criterion.v2 with schema_sha256"],
  "evidence_plan": ["bms.evidence-requirement.v2 with schema_sha256"]
}
```

The payload and every referenced schema are closed. It has 1..64 targets with unique `target_id`. Each target contains exactly the fields frozen by `bms.protein-target.v3`: bounded receipt or Dataset-member authority, one `bms.protein-entity-map-reference.v1`, and the expected content digest. The map reference stores only its verified receipt identity/digest, canonical content digest/size, counts, and at most 32 sequence-free display entities. Full sequences and residue mappings remain in the receipt's native store or governed digest-bound artifact. Capability arrays use unique registered IDs. Validation capability count is at most 32.

Outer `dataset_revision_ids` is the one authoritative ordered Dataset-revision list for both domain kinds. Protein payloads contain no second top-level Dataset list. Every `dataset_member_refs[].dataset_revision_id` under every Protein target must occur exactly once in the outer list. Create, patch, exact read, preparation, backup, restoration, export, and replay reject a missing, duplicate, foreign, reordered-without-successor, or unresolved Dataset revision before using the payload.

The current `bms.protein-constraint.v1` registry state is `closed_empty`, and the payload schema therefore requires `design_constraints` to be empty. PM-03A cannot expose authorable design constraints until an additive registered payload schema and successor Protein payload version are accepted. Comparison groups include the registered compatibility contract required by `bms.protein-comparison-group.v1`. Semantic validation rejects unresolved targets, duplicate semantic members, incompatible comparison authority, unregistered capability IDs, or criteria/evidence payloads whose registered schema digest does not match.

Historical `bms.protein-in-silico-experiment.v1` and v2 payloads and their referenced target/map schemas remain byte-for-byte read-only. The first mutation uses the complete outer-v3 Domain upgrade route with a complete Protein v3 payload. Persistence reconstructs RFC 8785 canonical payload and complete revision bytes. It rejects a payload over 786,432 bytes or a complete persisted revision over 917,504 bytes before commit. The server derives and verifies both size fields on write, exact read, backup, restoration, export, and preparation; callers cannot supply or override either field.

Exact workflow parameters remain in immutable Workflow Plan revisions. The Domain Experiment stores scientific intent, grouping, and references.

### 8.3 Protein capability families

The manager must support every installed and globally registered protein capability through typed adapters. Initial families include:

- protein structure prediction;
- complex structure prediction;
- de novo protein design;
- binder design;
- antibody and nanobody design;
- constrained and local redesign;
- protein CAD and shape-constrained design;
- conformational mapping and ensemble generation;
- molecular dynamics;
- mutation and variant exploration;
- sequence design through the approved canonical producer;
- structure and sequence validation;
- FrustraMPNN analysis, comparison, guidance, and reanalysis;
- cross-run statistical analysis;
- structure, ensemble, trajectory, and metric visualization.

The registry must use canonical capability IDs. User-authored IDs are invalid.

Ordinary protein jobs can use one shared core-job adapter when their canonical request and result contracts are fully represented by the core job authority. Workflows with additional authorities require specialized adapters. CM and MD require specialized adapters. FrustraMPNN requires result-aware receipt issuance.

### 8.4 Protein adapter responsibilities

Each protein adapter must:

1. Validate the saved domain payload.
2. Resolve all source receipt IDs.
3. Normalize launcher state through the same server contract used by the dedicated launcher.
4. Produce a deterministic normalized request digest.
5. Run canonical admission checks.
6. Create a preparation receipt without creating work.
7. Materialize work only through the supported BMS scheduling API.
8. Return canonical job and domain record IDs.
9. Publish verified activity and result receipts.
10. Provide a stable reopen route.
11. Project domain lifecycle into global normalized state.
12. Preserve domain failure details.

An adapter cannot substitute a different model, runtime, algorithm, or result.

### 8.5 MD integration

MD is a first-class Protein In Silico activity. The global layer must reference:

- the immutable MD request and request digest;
- the authoritative `MdRun` identity;
- scheduler job identity;
- topology and starting-structure receipts;
- selected force-field, solvent, boundary, ensemble, duration, timestep, and restraint settings from the canonical request;
- runtime and GPU identity;
- trajectory, checkpoint, topology, energy, and analysis artifacts;
- derived analysis records;
- terminal state and failure receipt;
- structure and trajectory viewer routes.

The global layer must preserve GROMACS as the MD authority. It cannot infer completed analysis from file presence alone.

### 8.6 CM integration

The existing CM global adapter remains the reference implementation. It must be moved under a Protein In Silico Domain Experiment without changing its canonical request authority.

The integration must preserve:

- source receipts;
- CM request identity;
- generator and backend identity;
- expected cardinality;
- child dependency order;
- ensemble artifacts;
- FrustraMPNN attachments;
- comparison activities;
- retries and partial failures;
- direct navigation to CM launch and result surfaces.

## 9. NGS / MolBio integration contract

The separate NGS / MolBio worker owns its domain schema and adapters. Its implementation must satisfy the following global boundary:

- create or revise a Domain Experiment with `domain_kind=ngs_molbio`;
- validate all molecular and sequencing references against their canonical stores;
- publish verified receipts for immutable molecular revisions, PCR experiment revisions, NGS jobs, ONT run records, comparison panels, and terminal manifests;
- provide stable reopen routes;
- publish normalized lifecycle projections;
- preserve source and result digests;
- preserve sample, construct, and expected-reference identity;
- consume global Project and Global Experiment IDs as context;
- avoid storing global Project or Global Experiment authority in `molbio.db`;
- avoid direct writes into `experiments.db` outside the supported global service/API;
- use idempotent handoffs;
- fail closed on stale or unverified cross-store references.

The global manager may render generic NGS / MolBio cards and activity rows. Domain-specific editors remain owned by that worker.

## 10. Persistence changes

### 10.1 Aggregate kinds

Extend the global aggregate-kind constraint to support:

```text
workspace          internal storage kind for Project
experiment         Global Experiment
domain_experiment  typed domain child
workflow           Workflow Plan
dataset            global immutable grouping
```

The operator-facing API and UI use `Project`. Existing `workspace` IDs remain stable.

### 10.2 New tables

Add only the tables needed for first-class global records:

#### `research_records`

- `resource_id`
- `workspace_id`
- `subject_resource_id`
- `schema_id`
- `subject_revision_id`
- `subject_revision_sha256`
- `record_kind`
- `body`
- `author`
- `source_receipt_ids_json`
- `supersedes_record_id`
- `created_at`
- `content_sha256`

New v2 rows require all four immutable authority fields. `content_sha256` is SHA-256 of RFC 8785 canonical record JSON with that field omitted. Historical v1 rows receive `schema_id=bms.research-record.v1`; unavailable historical subject revision/digest or canonical v2 content digest remains null and cannot be invented. A constraint requires non-null exact subject and content authority when `schema_id=bms.research-record.v2`. Backup, restore, export, exact reads, and integrity checks preserve and verify these columns.

#### `domain_adapter_receipts`

- `resource_id`
- `workspace_id`
- `domain_experiment_id`
- `adapter_id`
- `adapter_version`
- `operation_kind`
- `normalized_request_sha256`
- `receipt_json`
- `created_at`

Existing `external_entity_receipts`, `lineage_edges`, `revisions`, and audit events remain authoritative for cross-store linkage and revision history.

### 10.3 Existing dormant tables

Activate existing artifact and log tables through explicit writers. Avoid adding parallel artifact tables.

The global artifact writer must accept only:

- content-addressed data already verified by a trusted adapter;
- canonical media type;
- logical role;
- owning run or activity;
- content digest;
- byte size when known;
- canonical reopen or download route.

The log writer must ingest bounded chunks from an authoritative runtime stream. It must preserve attempt identity and sequence order.

### 10.4 Lifecycle states

Lifecycle transitions must be generation-checked and audited.

The current revision's `status` is the durable desired lifecycle state. `aggregate_heads.lifecycle_state` is an atomically updated projection of that status. A transaction that would leave them inconsistent must fail.

Parent state is a projection. Child state remains authoritative within its domain.

A Global Experiment can be `completed` when child Domain Experiments include failed, blocked, or inconclusive outcomes. The completion revision must explain the disposition.

Archiving a parent does not archive or cancel canonical domain runs.

### 10.5 Single-writer behavior

The first implementation retains SQLite and one global writer. A real dispatcher/reconciler worker must own:

- pending outbox dispatch;
- stale dispatch lease recovery;
- external lifecycle reconciliation;
- terminal receipt ingestion;
- retry-safe publication;
- health and lag metrics.

Static `single_writer: true` output is insufficient. The runtime must publish current worker identity, lease state, last successful sweep, pending count, oldest pending age, and failure count.

### 10.6 Data-plane separation

The product uses four explicit data planes:

| Plane | Responsibility | Authority |
|---|---|---|
| Global control plane | Projects, Global Experiments, Domain Experiments, workflow plans, preparations, global lineage, notes, review, and adapter receipts | `experiments.db` |
| Domain scientific plane | Jobs, designs, CM requests, MD runs, FrustraMPNN records, molecular revisions, ONT runs, NGS records, and later instrument records | Existing domain-owned stores |
| Artifact plane | Structures, trajectories, sequence files, manifests, plots, logs, and downloads | Canonical artifact owner plus immutable manifest and digest |
| Presentation read model | Bounded Project, Experiment, activity, result, evidence, and health projections for the UI | Recomputable projection assembled from the three authority planes |

The presentation read model is never a scientific authority. It must identify the exact authority records and digests used to assemble each response.

The global API must not perform unbounded scans of `jobs`, designs, artifact directories, MolBio revisions, or NGS history during a page request. It starts from global parent relationships and verified receipt IDs. Each registered adapter resolves only the bounded entities named by those receipts.

### 10.7 Cross-store consistency

Cross-store operations use an outbox and verified-receipt saga. Atomicity ends at each database boundary.

Typed-launcher Job creation follows the child SOW's unique `(run_attempt_id, launch_context_id, launch_fence_epoch)` protocol. The global store claims/reserves first. The native store then creates or reuses one canonical Job and immutable submission receipt in one transaction after verifying the exact fence token. A global reconciliation transaction verifies that receipt before writing the global binding receipt and consuming the context. Retry performs lookup-before-create at the native boundary. A crash between stores leaves `binding_pending` or `reconciliation_pending`; recovery finalizes the same Job/receipt, while any tuple, token, request, or digest conflict becomes durable `digest_mismatch` or conflict evidence and blocks another creation. No specification may describe the two store commits as one transaction.

The global saga stores these source states while it converges:

```text
binding_pending
bound
reconciliation_pending
stale
source_unavailable
digest_mismatch
```

`bound` requires a canonical source lookup and matching digest. Temporary source unavailability does not delete the prior valid receipt. The UI shows the last verified time and current reconciliation state.

`digest_mismatch` blocks scientific rendering from that receipt. It retains the historical receipt and creates an audit event.

The Project Manager uses one normalized reconciliation enum. `bound` maps to `current`; `binding_pending` and `reconciliation_pending` map to `pending`; `stale`, `source_unavailable`, and `digest_mismatch` retain their names. `unresolved_reconciliation` counts every normalized non-`current` row. The source state remains in the registered typed detail payload, while count/status projections use only the normalized enum.

## 11. API contract

### 11.1 Canonical project routes

```text
GET    /api/projects?project_scope=&status=&include_archived=&cursor=&limit=
POST   /api/projects
GET    /api/projects/{project_id}
PATCH  /api/projects/{project_id}
POST   /api/projects/{project_id}/upgrade
POST   /api/projects/{project_id}/archive
POST   /api/projects/{project_id}/restore
GET    /api/projects/{project_id}/revisions?cursor=&limit=
GET    /api/projects/{project_id}/revisions/{revision_id}
GET    /api/projects/{project_id}/activity?cursor=&limit=
GET    /api/projects/{project_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/records
```

### 11.2 Global experiment routes

```text
GET    /api/projects/{project_id}/experiments?status=&include_archived=&cursor=&limit=
POST   /api/projects/{project_id}/experiments
GET    /api/projects/{project_id}/experiments/{experiment_id}
PATCH  /api/projects/{project_id}/experiments/{experiment_id}
POST   /api/projects/{project_id}/experiments/{experiment_id}/upgrade
POST   /api/projects/{project_id}/experiments/{experiment_id}/archive
POST   /api/projects/{project_id}/experiments/{experiment_id}/restore
GET    /api/projects/{project_id}/experiments/{experiment_id}/revisions?cursor=&limit=
GET    /api/projects/{project_id}/experiments/{experiment_id}/revisions/{revision_id}
GET    /api/projects/{project_id}/experiments/{experiment_id}/activity?cursor=&limit=
GET    /api/projects/{project_id}/experiments/{experiment_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/experiments/{experiment_id}/records
```

### 11.3 Domain experiment routes

```text
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains?domain_kind=&status=&include_archived=&cursor=&limit=
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
PATCH  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/upgrade
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/archive
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/restore
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/revisions?cursor=&limit=
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/revisions/{revision_id}
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity?cursor=&limit=
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/attach
```

### 11.3.1 Hierarchy mutation contract

Every hierarchy mutation is owner-authorized, uses a closed request, and derives actor, parentage, revision identity, digests, and timestamps on the server. Caller-supplied `owner`, `created_by`, canonical parent fields, generations outside the named CAS field, revision IDs, digests, receipt bodies, or audit identity are invalid.

Project create accepts `schema`, `project_scope`, `name`, `description`, `research_objective`, `contributors`, `tags`, non-archived `status`, dates, external references, and `change_summary`; it omits server-derived `owner`, `created_by`, and `needs_metadata_review`. Global Experiment create accepts `schema`, all scientific and display fields of `bms.global-experiment.v2`, and `change_summary`; it omits server-derived `created_by` and `needs_metadata_review`. Domain create accepts exactly `schema=bms.domain-experiment.v4`, `domain_kind`, `domain_contract_version="3"`, `name`, `objective`, non-archived `status`, `tags`, `source_receipt_ids`, `dataset_revision_ids`, `change_summary`, and one complete registered `domain_payload`. It omits server-derived `created_by`, `domain_payload_canonical_size_bytes`, and `canonical_size_bytes`. Patch requests contain `expected_head_generation` and only mutable fields; they also omit those three server-derived fields. Array fields replace the complete array. Domain payload replacement supplies one complete registered payload. Scope, owner, creator, schema kind, domain kind, and domain contract version are immutable.

Create, patch, aggregate upgrade, archive, and restore require one `Idempotency-Key` header of 1..255 visible ASCII characters; a body key is invalid. The server hashes operation, path hierarchy, expected generation when present, and canonical request. The claim scope is exactly `{operation_slug}:{sha256(canonical_target_json)}`. `canonical_target_json` always contains the server-derived authenticated principal ID and then: `{project_scope}` for Project create; `{project_id}` for Project mutation, Project upgrade, or Global Experiment create; `{project_id,global_experiment_id}` for Global Experiment mutation, Global Experiment upgrade, or Domain create; `{project_id,global_experiment_id,domain_id}` for Domain mutation, upgrade, or attachment; `{project_id,subject_resource_id,record_kind}` for a Research Record append; or `{adapter_id,entity_id,operation}` for standalone receipt issuance. Operation slugs are exactly `project-create|project-patch|project-upgrade|project-archive|project-restore|global-experiment-create|global-experiment-patch|global-experiment-upgrade|global-experiment-archive|global-experiment-restore|domain-experiment-create|domain-experiment-patch|domain-experiment-upgrade|domain-experiment-archive|domain-experiment-restore|research-record-append|adapter-receipt-issue|domain-attachment-create`. Child Dataset, Project-link, connector, Plan, and run mutations use their child-defined operation slugs with this same construction. The complete scope remains under 128 ASCII characters.

After syntax normalization and owner authorization, every mutation in sections 11.3.1 and 11.3.2 resolves an existing idempotency claim before current-head, lifecycle, native-source, receipt-availability, or reconciliation checks. Same-key/same-hash replay returns the original status and byte-identical response even after accepted authority advances. Same-key/different-hash returns `409 idempotency_conflict`. Only a new claim proceeds to current authority validation. A new key with a stale generation returns `409 stale_generation`. Each mutation and idempotency claim commits in one transaction.

Archive and restore bodies contain exactly `expected_head_generation` and `change_summary`. `archived` cannot be set through an ordinary create or patch. The generic algorithms operate only when the copied source payload already uses the current contract for its aggregate kind: Project v2, Global Experiment v2, or outer Domain v4. Archive copies the complete non-archived current payload, changes only `status=archived` and request `change_summary`, then writes one successor and head projection. Restore requires a current-contract archived head and immediate current-contract non-archived predecessor. It copies that predecessor's complete payload, retains its exact non-archived status and immutable fields, replaces only `change_summary`, then writes one successor.

`POST /api/projects/{project_id}/upgrade` accepts exactly `expected_head_generation`, `schema=bms.project.v2`, the deterministically derived `project_scope`, complete `name`, `description`, `research_objective`, `contributors`, `tags`, non-archived `status`, nullable dates, `external_references`, and `change_summary`. It omits `owner`, `created_by`, and `needs_metadata_review`. `POST /api/projects/{project_id}/experiments/{experiment_id}/upgrade` accepts exactly `expected_head_generation`, `schema=bms.global-experiment.v2`, complete `name`, `objective`, `scientific_question`, nullable `hypothesis`, `description`, non-archived `status`, `priority`, `tags`, `shared_source_receipt_ids`, `shared_dataset_ids`, nullable `comparison_plan`, `success_criteria`, nullable `review_summary`, nullable `conclusion`, and `change_summary`. It omits `created_by` and `needs_metadata_review`. Each server locks the named v1 head, checks the immediate predecessor for an archived head, derives actor and review state, validates the complete v2 object, writes one immutable successor, advances the head once, and leaves every v1 byte unchanged. The corresponding operation slug and stable aggregate target govern replay. Changed request bytes conflict; a stale generation fails before a write.

`POST /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/upgrade` is the sole first-mutation path for a historical Domain v1/v2 head. It requires `Idempotency-Key` and a closed body containing exactly `expected_head_generation`, `schema=bms.domain-experiment.v4`, the existing immutable `domain_kind`, `domain_contract_version="3"`, complete `name`, `objective`, non-archived `status`, `tags`, `source_receipt_ids`, `dataset_revision_ids`, `change_summary`, and one complete registered `domain_payload`. It omits actor, both canonical-size fields, digests, and parent identity. The server verifies the historical head and supplied authority, derives the authenticated upgrading principal and both size attestations, and writes one v3 successor without modifying the historical bytes. When the historical head is archived, the supplied non-archived status must equal its immediate non-archived predecessor's lifecycle status. A missing or archived predecessor fails.

Archive or restore against a Domain source that is v1/v2 returns `409 domain_contract_upgrade_required` and performs no write. Archive of archived state, restore of active state, missing predecessor, archived predecessor, or a lifecycle-upgrade mismatch returns `409 invalid_lifecycle_transition`. The upgrade operation slug is `domain-experiment-upgrade`; its idempotency target is the same stable Domain tuple used by other Domain mutations. Neither lifecycle operation cancels or mutates canonical domain work.

A successful hierarchy mutation response returns stable aggregate ID, exact current revision ID and number, new head generation, lifecycle state, complete persisted payload, normalized request SHA-256, and created/updated timestamps. A Domain response contains the complete persisted `bms.domain-experiment.v4` object, including both server-derived canonical byte-size attestations. Every revision list item and exact-revision response validates `docs/specs/schemas/hierarchy-revision-v1.schema.json`. The exact route resolves the requested `revision_id` directly, verifies stored payload bytes against `payload_sha256` and `payload_schema_id`, and never consults or substitutes the current head. Local Project scope rejects non-NGS/MolBio Domain children. The main Project Manager rejects a local Project as an owning root while still showing governed links.

Research-record append requests require `Idempotency-Key` and contain exactly `record_kind`, `body`, `source_receipt_ids`, nullable `supersedes_record_id`, `expected_project_head_generation`, and `expected_subject_revision_id`; the path supplies the stable subject. The server derives resource ID, author, subject authority, and creation time. It verifies Project ownership, active subject lifecycle, exact current subject revision, every source receipt, and the replacement chain before writing the v2 record, audit row, idempotency claim, and count/activity projection in one transaction. Same-key/same-request replay returns the original record. A replacement names an existing record under the same subject and creates a new append-only record. Record list pages use the opaque page contract in section 12.9.

Every hierarchy, revision, activity, and record list returns exactly `items`, `next_cursor`, `has_more`, and `total`, with the same nullable-total and cursor failure rules as section 12.9. Hierarchy heads use `(updated_at DESC, stable_id DESC)`. Revisions use `(revision_number DESC, revision_id DESC)`. Activity and records use `(created_at DESC, stable_id DESC)`. Default limit is 50 and maximum limit is 100. Direct list routes and the composite Project Manager route call the same projection services and registered item schemas. A direct route cannot become a second read authority or use a different filter, state, or ordering contract.

### 11.3.2 Receipt issuance and attachment contract

Adapter entity search returns the common bounded page with registered items containing exact `adapter_id`, `adapter_version`, `entity_id`, `entity_revision_or_generation`, `content_sha256`, `entity_kind`, label, allowed receipt operation, readiness, and canonical destination. The browser cannot construct or alter an entity identity.

Standalone receipt issuance at `POST /api/domain-adapters/{adapter_id}/entities/{entity_id}/receipt` is a non-attaching verification operation. It requires `Idempotency-Key` and a closed body containing exactly `operation`, `expected_entity_revision_or_generation`, and `expected_content_sha256`. The server re-resolves the source, verifies every field, and returns one immutable external-entity receipt. It does not consume one-time launch authority, create membership, write lineage, or advance a Project head.

`POST D/attach`, where `D` is the canonical Domain path, requires `Idempotency-Key` and a closed body containing exactly `adapter_id`, `entity_id`, `operation`, `role`, nullable `note`, `expected_project_head_generation`, `expected_global_experiment_revision_id`, `expected_domain_revision_id`, and nullable `expected_domain_context_id`. For NGS/MolBio, `expected_domain_context_id` is the exact current binding revision. For Protein, it is null unless its registered adapter contract names another immutable context. The service re-resolves and verifies the native entity, exact Global Experiment revision, exact Domain revision/context, Project owner, Project scope, adapter operation, and role. It then writes or reuses the deterministic external receipt, attachment receipt, lineage edge, idempotency claim, audit row, and new Project generation in one global-store transaction. No browser-supplied receipt body, digest, route, actor, or source metadata is accepted.

A successful attachment validates `docs/specs/schemas/attachment-receipt-v1.schema.json` and returns the closed `bms.global.attachment-receipt.v1` authority. It includes its stable attachment receipt ID, exact external receipt and lineage-edge IDs, the external receipt's nullable `content_digest` and `contract_digest` as `external_content_digest` and `external_contract_digest`, hierarchy revisions/context, adapter/native identity, operation/role/note, normalized request digest, new Project generation, and server actor/time. `receipt_sha256` is SHA-256 of RFC 8785 canonical JSON for the complete attachment receipt with only `receipt_sha256` omitted. The server verifies that at least one copied external digest is non-null and equals the exact external receipt before computing the attachment digest. Same-key/same-request replay returns the original response. Same-key/different-request conflicts. A new key with stale Project generation, changed Global Experiment revision, changed Domain revision/context, foreign entity, unsupported role, or digest mismatch fails before any write. Native `Add to Project` triggers call this contract; they cannot implement another attachment path.

### 11.4 Workflow routes

Existing workflow, draft, revision, preparation, run-group, retry, and reconcile operations remain. Canonical new routes nest them under a Domain Experiment.

Every mutation must use strict request models. Revision-changing requests require `expected_head_generation`. Launch requests require an idempotency key.

### 11.5 Search and picker routes

```text
GET /api/project-search?query=&project_scope=&cursor=&limit=
GET /api/projects/{project_id}/experiment-search?query=&cursor=&limit=
GET /api/domain-adapters?domain_kind=&cursor=&limit=
GET /api/domain-adapters/{adapter_id}/entities/search?query=&cursor=&limit=
POST /api/domain-adapters/{adapter_id}/entities/{entity_id}/receipt
```

Search results are read-only projections. Attaching an entity requires a newly verified receipt.

The two search paths are deliberately outside the corresponding `{project_id}` and `{experiment_id}` detail shapes. No opaque identifier is interpreted as a search literal. Acceptance registers the search and detail routes in both possible relative orders in an isolated router and proves that both search routes and representative detail reads resolve to their intended handlers. The deployed route inventory must show the same non-colliding templates.

### 11.6 Compatibility routes

The existing `/api/experiment-workspaces` routes remain during migration. They must call the same services as `/api/projects` and return deprecation metadata. They cannot maintain a separate write path.

Removal of compatibility routes requires a later explicit release decision and proof that current clients have migrated.

## 12. Frontend specification

### 12.1 Navigation

Add one top-level `Project Manager` item to `platform/frontend/src/components/Layout.tsx`.

The approved UI direction is:

- `docs/mockups/project-manager/07-map-blocks-runs/index.html`

This mockup is the implementation authority for spatial composition. It preserves the Research Map layout with a persistent Project tree, central relationship map, and selected-node inspector. Global Experiments and Domain Experiments use boxed map regions. Selected workflows and runs expose actual run, replica, state, lineage, and canonical-source details in the inspector.

The Experiment Canvas, Research Map, and Lab Notebook remain design references for the approved hybrid. The earlier Split Workbench, Compact Registry, and tree-only hybrid directions were rejected. Rejected mockups cannot be used as implementation authority.

#### 12.1.1 Approved composition contract

The Research Map is the conceptual and spatial base. It cannot be reduced to a Project selector or tree beside a different dashboard.

| Region | Required behavior | Source concept |
|---|---|---|
| Project tree | Persistent traversal across Projects, Global Experiments, Domain Experiments, and expandable `Plans`, `Runs`, `Results`, and `Datasets` folders | Research Map |
| Relationship map | Primary center surface showing the selected Project, compact Global Experiments, one expanded focused Global Experiment, boxed Domain Experiments, typed evidence/result nodes, and lineage edges | Research Map plus Experiment Canvas segmentation |
| Selected-node inspector | Type-specific detail for the current Project, experiment, workflow, run, result, dataset, molecular record, note, or decision | Research Map plus Lab Notebook detail treatment |
| Actual runs | Compact rows with canonical identity, state, stage, target, replica/batch relationship, elapsed time or progress, outputs, warning/failure condition, and allowed actions | Lab Notebook |
| Global and Domain Experiment grouping | Bounded boxes inside the map. Boxes group related records without becoming a card grid or a second Project tree | Experiment Canvas |

The three regions remain visible together on supported desktop widths. Selecting an item in any region synchronizes the tree selection, map focus, inspector content, and URL. The relationship map remains the primary work surface. A table or overview dashboard cannot replace it.

The Project tree has two node classes:

- persisted nodes for Project, Global Experiment, and Domain Experiment identity;
- deterministic virtual folders for Plans, Runs, Results, Datasets, Notes, Decisions, and Activity.

Virtual folders are navigation projections. They are not persisted aggregates and do not own membership. Expanding one requests a bounded page from the relevant authority.

The map follows these density rules:

- the selected Project is the root;
- all non-archived Global Experiments appear as compact nodes or boxes;
- only the focused Global Experiment expands its Domain Experiment boxes and first bounded page of attached records;
- edges identify their lineage mode through a server-issued type and accessible label;
- large result, run, and activity sets remain collapsed behind count-bearing nodes until selected;
- map layout is deterministic for the same response and can be recomputed by the browser without changing scientific meaning;
- pan, zoom, fit-to-focus, and keyboard focus are presentation operations only.

The inspector is authoritative only as a presentation of server-owned data. A selection change replaces its title, summary, actions, relationship, scientific context, and type-specific sections together. Stale responses from the previous selection cannot update any inspector region.

#### 12.1.2 Deep-link and selection contract

The selected Project remains in the path. Map and inspector selection use validated query state:

```text
/projects/{project_id}?focus={global_experiment_id}&selected={typed_node_key}
```

`focus` must identify the Project itself or one of its Global Experiments. `selected` must resolve through the Project read model. Unknown, archived-without-permission, or foreign selection values produce a visible unavailable state and preserve the Project page. Browser back and forward restore the exact validated focus and selection.

The initial selection order is:

1. validated URL selection;
2. focused Global Experiment;
3. most recently active non-archived Global Experiment;
4. Project root.

Browser storage cannot define Project membership, selected scientific authority, or return context.

Routes:

```text
/projects
/projects/:projectId
/projects/:projectId/experiments/:experimentId
/projects/:projectId/experiments/:experimentId/domains/:domainId
```

### 12.2 Projects page

The Projects page must provide:

- create Project;
- search by name, objective, owner, and tag;
- status filter;
- recent activity ordering;
- active experiment counts;
- unresolved failure or blocked-state indicators;
- archive filter;
- direct reopen.

### 12.3 Project page

Header fields:

- Project name;
- objective;
- read-only authenticated owner identity and editable contributors;
- status;
- tags;
- dates;
- immutable revision indicator;
- Edit and Archive controls.

Primary page sections:

1. Overview.
2. Global Experiments.
3. Shared Assets and Datasets.
4. Notes and Decisions.
5. Activity and Audit.

These are tree folders, map groupings, and inspector sections inside the approved composition. They are not replacement dashboard tabs.

Use the existing horizontal space. Avoid narrow stacked forms when selectors, tables, and compact cards are clearer.

### 12.4 Global Experiment page

The experiment header contains immutable record-keeping information before workflow controls:

- name;
- scientific question;
- objective;
- hypothesis when applicable;
- success criteria;
- status;
- priority;
- tags;
- current revision;
- comments and change summary.

Primary sections:

1. Overview.
2. Domain Experiments.
3. Workflow Plans.
4. Runs.
5. Results and Evidence.
6. Notes, Observations, and Conclusions.
7. Lineage and Audit.

These sections remain in the Project tree, focused map, and selected-node inspector. A separate tabbed experiment dashboard is not the primary layout.

### 12.5 Protein In Silico page

The Protein In Silico Domain Experiment page must expose:

- targets and roles;
- immutable source references;
- scientific objective;
- planned capability lanes;
- workflows grouped by purpose;
- prepared and runnable workflow revisions;
- active and historical runs;
- results and validation evidence;
- comparison groups;
- direct reopen links to canonical launchers and viewers.

Configuration controls should use mature selectors, sliders, check boxes, sequence selectors, structure selectors, and domain-specific launchers. The global page must not recreate model configuration forms.

### 12.6 Launch context

Launching from a Domain Experiment creates a short-lived, server-owned `bms.launch-context.v2` receipt under the child SOW lifecycle and closed schema at `docs/specs/schemas/launch-context-v2.schema.json`. The browser receives an opaque `launch_context_id`. The dedicated launcher resolves that ID through the API. Its browser-visible allowlisted projection contains only:

```json
{
  "project_id": "opaque ID",
  "global_experiment_id": "opaque ID",
  "domain_experiment_id": "opaque ID",
  "workflow_plan_id": "opaque ID",
  "workflow_revision_id": "opaque ID",
  "return_uri": "typed internal route"
}
```

The dedicated launcher loads the context, preserves it during save and submit, and displays the destination Project and Experiment.

The browser cannot author or alter Project, Global Experiment, Domain Experiment, Workflow, or Workflow Revision identity inside this context. Reservation adds the server-owned launch-fence epoch and token digest required by the child SOW. These fields never enter browser authority. The browser authorization expires; the immutable context row and its lifecycle evidence remain retained. Mutation or launch use of an expired, consumed, mismatched, revoked, or unknown context fails closed. A separate owner-authorized read-only audit resolver can return retained historical projection without making it launchable.

A job created from this context must publish a verified binding receipt before the UI claims it belongs to the experiment.

### 12.7 Attach existing records

Operators can attach existing jobs, MD runs, CM requests, FrustraMPNN results, structures, datasets, and later domain records.

The picker must:

- query a typed adapter;
- show canonical identity and provenance;
- show whether a record is already attached;
- require a lineage role;
- issue a verified receipt at attachment time;
- fail visibly on stale records or digest mismatch.

### 12.8 Existing surfaces

Existing Dashboard, Job Launcher, Data Viewer, Mol Bio Toolkit, NGS Toolkit, Stats Toolkit, and BioXP pages remain available.

Job and result pages should add Project and Experiment breadcrumbs when a valid receipt exists. Unassigned legacy jobs remain usable and can later be attached.

Browser-local clone and draft mechanisms should stop carrying authoritative experiment membership. Server-owned context replaces that role.

### 12.9 Presentation read models

The main Project Manager consumes one bounded composite envelope. The canonical initial and pagination route is:

```text
GET /api/projects/{project_id}/summary
  ?focus_id=&selected_node_key=
  &tree_parent_node_key=&tree_cursor=&map_cursor=
  &run_cursor=&result_cursor=&lineage_cursor=
  &note_cursor=&decision_cursor=&dataset_cursor=&activity_cursor=
  &tree_limit=&map_limit=&run_limit=&result_limit=&lineage_limit=
  &note_limit=&decision_limit=&dataset_limit=&activity_limit=
```

This route is the sole Project Manager composition contract. Separate Project map, Global Experiment summary, Domain summary, and duplicate collection-summary routes are not required and cannot become a second response authority. Domain-specific detail, artifact, log, Plan, Dataset, and result-surface routes remain separate where their contracts require exact native data.

Every cursor is opaque and binds the Project ID, subject generation, source-digest set, focus/selection scope, collection kind, branch parent where applicable, sort direction, last complete sort tuple, requested limit, authorization context, read-model contract digest, and immutable snapshot token. A cursor from another Project, branch, focus, selection, collection, profile, contract, subject generation, or source snapshot is invalid. Defaults are `tree_limit=100`, `map_limit=50`, and every other limit `25`; each maximum is `100`. Every page uses exactly `items` or the collection-specific node/edge array, `next_cursor`, `has_more`, and `total`. `has_more=true` requires a nonempty page and non-null next cursor. `has_more=false` requires a null next cursor. `total` is fixed for the cursor chain and is a non-negative integer when available without an unbounded scan or `null` otherwise. The server continues only against the bound snapshot. If that snapshot cannot be retained after any authority change, it returns `422 stale_cursor` instead of silently skipping, duplicating, or mixing items. Invalid, foreign, altered, stale, repeated, or non-advancing cursors return `422`. A response above the child SOW limits fails visibly rather than rendering partial success.

Every summary envelope includes the following common fields. This block is schematic; quoted type labels explain the contract and do not represent accepted instance bytes:

```json
{
  "schema": "bms.project-manager.read-model.v2",
  "subject_id": "opaque ID",
  "subject_generation": 1,
  "assembled_at": "UTC timestamp",
  "source_receipt_ids": ["receipt ID"],
  "source_authorities": [
    {
      "authority_kind": "project_revision|global_experiment_revision|domain_experiment_revision|receipt|dataset_revision|research_record|run|other_registered_kind",
      "stable_id": "opaque ID",
      "revision_or_generation": "opaque exact value",
      "content_sha256": "sha256 hex digest",
      "adapter_id": "string or null",
      "adapter_version": "string or null"
    }
  ],
  "source_digest_set_sha256": "sha256 hex digest",
  "adapter_versions": [{"adapter_id": "string", "version": "string"}],
  "reconciliation": {
    "state": "current|pending|stale|source_unavailable|digest_mismatch",
    "last_verified_at": "UTC timestamp or null",
    "reason": "string or null"
  },
  "counts": {
    "global_experiments": 0,
    "domain_experiments": 0,
    "linked_local_projects": 0,
    "workflow_plans": 0,
    "runs": 0,
    "results": 0,
    "dataset_revisions": 0,
    "notes": 0,
    "decisions": 0,
    "attached_entities": 0,
    "unresolved_reconciliation": 0
  },
  "status_summary": {
    "projects": {"draft": 0, "active": 0, "on_hold": 0, "completed": 0, "archived": 0},
    "global_experiments": {"draft": 0, "planned": 0, "active": 0, "analysis": 0, "review": 0, "completed": 0, "blocked": 0, "archived": 0},
    "domain_experiments": {"draft": 0, "planned": 0, "active": 0, "analysis": 0, "review": 0, "completed": 0, "blocked": 0, "archived": 0},
    "project_links": {"active": 0, "revoked": 0},
    "datasets": {"active": 0, "archived": 0},
    "workflow_plans": {"active_unpublished": 0, "active_published": 0, "archived": 0},
    "runs": {"planned": 0, "prepared": 0, "queued": 0, "running": 0, "paused": 0, "cancelling": 0, "completed": 0, "failed": 0, "cancelled": 0, "blocked": 0, "unknown": 0},
    "reconciliation": {"current": 0, "pending": 0, "stale": 0, "source_unavailable": 0, "digest_mismatch": 0}
  },
  "pagination": {
    "results": {"items": [], "next_cursor": null, "has_more": false, "total": 0},
    "lineage": {"items": [], "next_cursor": null, "has_more": false, "total": 0},
    "notes": {"items": [], "next_cursor": null, "has_more": false, "total": 0},
    "decisions": {"items": [], "next_cursor": null, "has_more": false, "total": 0},
    "datasets": {"items": [], "next_cursor": null, "has_more": false, "total": 0},
    "activity": {"items": [], "next_cursor": null, "has_more": false, "total": 0}
  }
}
```

`source_authorities` contains every authority whose fields appear anywhere in that response, including hierarchy revisions, selected details, page items, persisted adapter receipts, and bounded projection receipts for counts or status summaries. A bounded projection receipt uses `authority_kind=bounded_projection`, the registered query-contract ID as `stable_id`, a server-issued read-snapshot/high-watermark token as `revision_or_generation`, and SHA-256 of canonical JSON containing normalized query parameters, result, and ordered source high-watermarks. It contains no authority that was not used. Entries are unique and sorted by the canonical UTF-8 tuple `(authority_kind, stable_id, revision_or_generation, content_sha256, adapter_id-or-empty, adapter_version-or-empty)`. `source_digest_set_sha256` is SHA-256 of canonical JSON for that ordered array, with nullable adapter fields retained as JSON null. `source_receipt_ids` is the sorted receipt-ID subset for compatibility. `adapter_versions` has one row per used adapter ID; a contradictory version fails the envelope. A missing digest, omitted used authority, digest mismatch, or contradictory duplicate fails the envelope. Pagination responses bind only authorities represented or used in that exact response; they never imply digest coverage for unloaded pages.

Every polymorphic page item, canonical identity, edge metadata object, selection detail, and canonical surface uses the closed `typedEnvelope` wrapper in the v2 schema. `schema` names a registered immutable payload schema, `source_authority_keys` contains SHA-256 of canonical JSON for each referenced `source_authorities` row, and `payload` validates against the named schema and digest. List items also require `stable_id`. Unknown schemas, missing authority keys, unused authority references, or payload validation failure reject the response. Top-level counts, state summaries, and node counts have the exact fields in the v2 schema; every state or count key is present with zero when unused. Their source is a transactionally maintained, migration-attested projection. The summary route never computes them through an unbounded scan. Missing, stale, or integrity-invalid projection authority returns `503 read_model_unavailable` with no partial envelope.

Count semantics are fixed. `global_experiments` and `domain_experiments` count all retained heads, including archived heads. `linked_local_projects` counts distinct local Projects whose current link head is active. `workflow_plans` counts all retained Plan heads. `runs` counts canonical Workflow Run heads rather than attempts. `results` counts produced Result receipts and excludes input/reference attachments. `dataset_revisions` counts immutable Dataset revisions. `notes` and `decisions` count immutable Research Record rows, including superseded history. `attached_entities` counts accepted attachment receipts. `unresolved_reconciliation` equals the sum of `pending`, `stale`, `source_unavailable`, and `digest_mismatch` reconciliation rows. Project, Global Experiment, Domain Experiment, link, Dataset, Plan, and run status maps partition their corresponding retained heads exactly. `active_unpublished` means an active Plan head with no immutable published revision; `active_published` means an active head with at least one. The single Project status map has exactly one value of `1` and all others `0`.

#### 12.9.1 Project Manager composite envelope

The initial request omits every cursor and returns `bms.project-manager.read-model.v2`. A continuation request sends exactly one collection cursor, the matching collection limit, and the same `focus_id` and `selected_node_key`; every other cursor and limit is absent. A tree continuation also supplies the exact original `tree_parent_node_key`. The response validates `docs/specs/schemas/project-manager-continuation-v2.schema.json`, names exactly one `collection`, and returns only that collection's typed items plus its cursor fields and complete page provenance. Initial and continued run items both validate `docs/specs/schemas/project-manager-run-v2.schema.json`; no continuation wrapper can substitute another run payload schema. `stream_item_count` counts canonical collection-stream records in that response. It equals `items.length` for every non-map collection. For a map continuation, repeated focus/endpoint support nodes may increase `items.length` but do not increase `stream_item_count`. The service verifies this relationship. It never emits empty placeholder pages for non-advanced collections. Advancing one collection cannot reset or advance another.

The tree is lazy and branch-scoped. When `tree_parent_node_key` is omitted, the server uses the Project node as parent. `tree.nodes` contains only that parent's direct children, sorted by server-owned node-type order, case-folded label, subject ID, and node key. The Project object is the root and is not duplicated in `tree.nodes`. Expanding another hierarchy node or virtual folder supplies its exact node key. A continuation repeats the same parent and limit. The browser merges pages by exact `node_key`, rejects a changed node body under the same key, and retains each branch cursor independently.

The map cursor traverses one deterministic union stream of canonical node and edge records ordered by record-kind order and stable record key. One page selects at most `map_limit` records. `stream_record_count` is the exact number of selected union-stream records and is positive for a nonterminal page. The page also includes the focus node and every endpoint node required to render selected edges, even when those support nodes repeat from another page. Support nodes do not consume the record limit or `stream_record_count`. Every edge has both endpoint definitions in the same page. The browser deduplicates support nodes by exact `node_key`, rejects contradictory duplicates, and never drops an edge silently. `map.total` counts union-stream records, not repeated support nodes.

The complete initial response validates `docs/specs/schemas/project-manager-read-model-v2.schema.json`; every continued response validates `docs/specs/schemas/project-manager-continuation-v2.schema.json`. Historical v1 responses remain compatibility reads and cannot satisfy PM-06 acceptance. The following block is schematic; quoted union labels and `<...>` values explain the fields and are not accepted instance bytes. The v2 initial response adds one bounded Project Manager payload:

```json
{
  "project": {
    "id": "opaque ID",
    "project_scope": "global|ngs_molbio_local",
    "name": "string",
    "objective": "string",
    "lifecycle_state": "string",
    "head_generation": 1,
    "current_revision_id": "opaque ID",
    "updated_at": "UTC timestamp"
  },
  "tree": {
    "parent_node_key": "typed stable key",
    "nodes": [
      {
        "node_key": "typed stable key",
        "node_type": "project|global_experiment|domain_experiment|linked_local_project|virtual_folder",
        "subject_id": "opaque ID or null",
        "parent_node_key": "typed stable key or null",
        "label": "string",
        "lifecycle_state": "string or null",
        "counts": {"children": 0, "plans": 0, "runs": 0, "results": 0, "datasets": 0, "records": 0, "unresolved": 0},
        "has_children": true,
        "allowed_actions": []
      }
    ],
    "next_cursor": "opaque or null",
    "has_more": false,
    "total": 1
  },
  "map": {
    "focus_node_key": "typed stable key",
    "nodes": [],
    "edges": [],
    "stream_record_count": 0,
    "next_cursor": null,
    "has_more": false,
    "total": null
  },
  "selection": {
    "node_key": "typed stable key",
    "node_type": "typed node kind",
    "title": "string",
    "subtitle": "string or null",
    "canonical_identity": {"schema": "bms.<registered-schema>", "source_authority_keys": ["<sha256>"], "payload": {}},
    "summary": {"schema": "bms.<registered-schema>", "source_authority_keys": ["<sha256>"], "payload": {}},
    "relationship": {"schema": "bms.<registered-schema>", "source_authority_keys": ["<sha256>"], "payload": {}},
    "scientific_context": {"schema": "bms.<registered-schema>", "source_authority_keys": ["<sha256>"], "payload": {}},
    "reconciliation": {"state": "current", "last_verified_at": null, "reason": null},
    "available_actions": [],
    "canonical_surface": null
  },
  "runs": {
    "items": [],
    "next_cursor": null,
    "has_more": false,
    "total": null
  },
  "warnings": [],
  "allowed_actions": []
}
```

The tree vocabulary stops at Project, Global Experiment, Domain Experiment, linked-local-Project, and virtual-folder level. Each branch page is bounded and can be accumulated to closure. It never embeds every run, result, Dataset member, research record, or activity. Lower-level node types appear in bounded map, selection, or page data when requested. A map page selects at most 100 canonical union-stream records and may repeat up to 201 required focus/endpoint support nodes under section 12.9.1. A continued response keeps count-bearing collapsed nodes and returns an opaque cursor or narrower-focus action.

Node keys use `{node_type}:{stable_id}`. Virtual folders use `virtual_folder:{owning_subject_id}:{folder_kind}` where `folder_kind` is one of `plans|runs|results|datasets|notes|observations|decisions|conclusions|activity|lineage`. A `linked_local_project` is a governed cross-Project relationship, never a hierarchy child. Research-record nodes resolve the exact append-only record and its replacement chain.

Each map node includes `node_key`, `node_type`, label, normalized state, canonical source identity when applicable, count summary, reconciliation state, and allowed actions. Each edge includes source and target node keys, lineage mode, stable edge key, and an accessible relationship label.

Each run item includes:

```json
{
  "run_id": "global run identity",
  "native_binding_state": "unmaterialized|binding_pending|bound|conflicted",
  "canonical_job_id": "domain job/run identity or null",
  "workflow_type": "typed workflow family",
  "target_label": "operator label",
  "canonical_state": "source state",
  "normalized_state": "global state",
  "stage": "source stage or null",
  "progress": {"kind": "fraction|elapsed|indeterminate", "value": null, "unit": null},
  "started_at": "UTC timestamp or null",
  "elapsed_seconds": 0,
  "replica_index": null,
  "batch_or_run_group_id": "opaque ID or null",
  "output_count": 0,
  "condition": {"severity": "none|warning|failure", "code": null, "message": null},
  "receipt_id": "verified binding receipt ID or null",
  "adapter_id": "registered adapter ID",
  "available_actions": [],
  "canonical_surface": null
}
```

`unmaterialized` has no canonical Job or binding receipt. `binding_pending` means native materialization or typed submission has begun but verified global binding is incomplete. `bound` requires both canonical Job ID and verified binding receipt. `conflicted` retains the attempt and conflict evidence while both authority fields remain null in this projection. The service never fabricates either ID from planned intent. Fraction progress uses `unit=ratio` and a value in `[0,1]`. Elapsed progress uses a non-negative integer with `unit=seconds`; it must equal `elapsed_seconds`. Indeterminate progress requires both `value` and `unit` to be null. The read-model builder rejects every other combination.

Run actions such as `open_results`, `view_lineage`, `retry`, `resubmit`, or `clone` are server-issued. The Project Manager cannot infer an action from state labels. Retry and resubmit route through the canonical domain operation and publish new lineage. Clone uses the child SOW's canonical operation, creates fresh immutable Plan intent, and never mutates the selected run. Cancellation first projects `cancelling` and revokes the attempt's launch-fence token. The native fence transaction serializes that revocation against the first canonical Job commit. If revocation wins, it writes a native cancellation tombstone and no Job can commit. If Job creation wins, global cancellation reconciles that exact Job and invokes its canonical lifecycle. The global attempt becomes terminal `cancelled` and releases resources only after a verified native pre-commit tombstone acknowledgement or the winning canonical Job's terminal cancellation acknowledgement. Cancellation blocks retry and comparison immediately. Repeated or stale-token messages are idempotent replays or conflicts under the child contract.

The first envelope is size-bounded. Large design tables, mutation landscapes, trajectory frames, sequence reads, log chunks, and lineage graphs use paginated or artifact-specific endpoints.

The browser may calculate display layout, counts already authorized by the response contract, and deterministic identity grouping. It cannot calculate scientific metrics, acceptance labels, score deltas, statistical conclusions, missing values, or cross-run comparability.

### 12.10 Result index and canonical viewer routing

The Global Experiment `Results and Evidence` section is a result index and review surface. It is not a universal scientific viewer.

Each result receipt resolves to a server-issued surface descriptor:

```json
{
  "schema": "bms.result-surface.v1",
  "receipt_id": "receipt ID",
  "entity_kind": "typed canonical kind",
  "entity_id": "canonical entity ID",
  "contract_id": "canonical result contract ID",
  "content_digest": "sha256 hex digest",
  "surface_kind": "protein_design|molecular_dynamics|conformational_mapping|frustrampnn|ngs|molbio|artifact|unsupported",
  "route": {"template_id": "bms.route.<registered>", "path": "/same-origin/path", "query": {}},
  "readiness": "running|partial|ready|failed|blocked|unsupported",
  "native_summary": {"schema_id": "bms.<registered-summary>.v1", "content_sha256": "sha256 hex digest", "canonical_size_bytes": 2, "payload": {}},
  "scientific_acceptance": {
    "state": "passed|failed|review|unavailable|not_applicable",
    "reason": "string or null"
  },
  "provenance": {"schema_id": "bms.<registered-provenance>.v1", "content_sha256": "sha256 hex digest", "canonical_size_bytes": 2, "payload": {}},
  "comparison": {"state": "available|not_applicable|incompatible|unavailable", "reason": "adapter reason or null", "authority": null},
  "available_actions": ["open|download|compare|attach_evidence"]
}
```

The complete descriptor validates `docs/specs/schemas/result-surface-v1.schema.json`. The server derives `surface_kind`, route, contract, readiness, summaries, provenance, comparison projection, and actions. Each route passes the same registered internal-route validator as external receipts. Each typed payload validates its registered schema, digest, canonical byte cap, and payload-ownership class. `unsupported` surface and readiness values occur together, require a null route, `comparison.state=not_applicable`, null reason/authority, and no actions. Every supported viewer kind carries a registered route and `open`. An artifact surface carries its governed route and `download`. A null route is valid only for `unsupported`.

Comparison is one closed projection. `available` requires `readiness=ready`, a non-null route, a verified compatibility-adapter authority, null reason, and `compare`. `incompatible` or `unavailable` requires a non-empty adapter-derived reason, null authority, and no `compare`. `not_applicable` requires null reason/authority and no `compare`. The browser renders the exact state and reason and never infers comparability. The registered result builder enforces the same bidirectional matrix before issuance. A summary or provenance envelope may contain bounded display/provenance facts; it cannot embed canonical scientific arrays, sequences, reads, alignments, structures, trajectories, reports, manifests, or encoded file bytes. The browser cannot infer fields from `model_id`, file extension, output path, or job name.

Canonical routing for existing protein surfaces is:

| Result kind | Canonical surface |
|---|---|
| Ordinary protein design or structure result | Existing `ResultsViewer` design/job surface |
| Molecular dynamics | Existing `MDResultsPane` with lifecycle, artifacts, analysis, trajectory, and structure playback |
| Conformational Mapping | Existing `ConformationalMappingViewer` keyed by canonical CM request identity |
| FrustraMPNN | Existing `FrustraMpnnResultsViewer` keyed by job and invocation authority |
| NGS | NGS Toolkit result surface owned by the NGS / MolBio worker |
| Molecular record or operation | Mol Bio Toolkit record/history surface owned by the NGS / MolBio worker |
| Download-only artifact | Governed same-origin artifact route |

The global result card shows producer-native status, decisive scientific quantities, source identity, digest, and provenance before generic metadata. Execution completion and scientific acceptance remain separate fields.

Opening a result preserves a return route to the exact Project, Global Experiment, and Domain Experiment. The canonical viewer displays the same context as breadcrumbs after it resolves a valid global binding receipt.

### 12.11 Result and evidence behavior

The result index supports:

- status and result-kind filters;
- target, workflow, dataset, and run grouping;
- explicit partial, failed, blocked, and unavailable records;
- direct canonical viewer opening;
- immutable artifact download;
- evidence attachment;
- creation of approved cross-run datasets;
- operator observations and conclusions;
- provenance and lineage inspection.

Job-scoped saved review/filter sets remain owned by the existing Results Viewer. A global Dataset can reference a saved review set through a verified receipt. It does not copy the design IDs or filter state into a second mutable authority.

Cross-run comparison appears only when a registered comparison adapter proves compatible source contracts, identities, units, and analysis versions. The global UI must show `not comparable` with the adapter's reason when that proof fails.

### 12.12 Viewer integrity

Every scientific viewer opened from the global manager must:

- validate the versioned backend result contract at runtime;
- bind compact summaries and paginated rows to the canonical artifact digest;
- preserve producer order and values;
- reject absent, duplicate, foreign, stale, or contradictory authority;
- keep missing values explicit;
- keep execution state separate from scientific acceptance;
- prevent stale asynchronous responses from replacing a newly selected result;
- use bounded first responses and paginated large data;
- display a visible fail-closed error when authority validation fails;
- preserve direct governed downloads;
- retain Project and Experiment return context without making that context a scientific authority.

### 12.13 Primary operator flows

#### Create and plan

```text
Projects
→ create or open Project
→ create Global Experiment
→ enter immutable record information
→ add Protein In Silico Domain Experiment
→ add source records or datasets
→ add or reopen a typed workflow
→ save revision
→ prepare
→ launch when explicitly selected
```

#### Monitor and review

```text
Global Experiment
→ Runs
→ inspect normalized and canonical state
→ open one activity
→ open canonical result viewer
→ return to experiment
→ attach evidence or save a global dataset
→ record observation
→ record conclusion or next decision
```

#### Attach historical work

```text
Domain Experiment
→ Attach existing
→ choose typed source
→ search canonical source through adapter
→ select record and lineage role
→ verify receipt
→ show activity or result in the global read model
```

### 12.14 Approved interaction behavior

| Operator action | Required result |
|---|---|
| Select Project | Load its complete bounded hierarchy, root map, and Project inspector |
| Select Global Experiment | Focus and expand its map box, preserve sibling Global Experiments as compact nodes, and load its inspector |
| Select Domain Experiment | Keep the containing Global Experiment expanded, emphasize its Domain box, and load Domain details |
| Select workflow or run | Emphasize its map row or node and load type-specific inspector content; an MD run shows its actual replica rows |
| Select result, evidence, dataset, or molecular record | Load canonical identity, receipt, digest, reconciliation state, lineage role, and server-issued surface action |
| Expand a tree virtual folder | Load the first bounded page without changing persisted hierarchy |
| Add existing | Open one shared attachment interaction with Project, Global Experiment, Domain Experiment, relationship mode, canonical source, and optional note |
| Remove experiment | Archive the selected Global or Domain Experiment after generation validation; do not delete records or cancel canonical runs |
| Reopen canonical source | Navigate to the server-issued BMS route and preserve a validated return link to the exact Project selection |

`Add existing` distinguishes these explicit operations:

- attach a membership or reference receipt;
- bind an immutable input to a new Workflow Plan revision;
- link a generated output or evidence record;
- clone or import canonical material into a new experiment revision.

The UI cannot collapse these operations into one ambiguous `copy` action. The confirmation text states whether bytes remain in the source store, whether a new immutable revision is created, and which lineage edge will be written.

Removal means archival from active Project navigation. Archival retains identity, children, receipts, notes, lineage, and audit. It does not delete or cancel a domain run. Archived experiments remain discoverable through the archive filter and can be restored through a generation-checked audited action.

### 12.15 ELN-lite record behavior

The Project Manager supports short purpose, hypothesis, success-criteria, note, observation, decision, and conclusion records. Notes remain append-only with replacement links as defined in section 7.7. The inspector and bounded activity feed present these records in context.

The first release excludes rich notebook pages, free-position document blocks, arbitrary file embedding, templates, signatures, witness workflows, inventory, and generalized document authoring. Existing specialized editors remain the correct surface for sequences, constructs, analyses, and scientific results.

### 12.16 Responsive, loading, and failure states

At desktop width, the tree uses a bounded resizable rail, the map consumes remaining width, and the inspector uses a bounded resizable rail. The operator can collapse either rail. Fit-to-focus restores the map after resizing.

At tablet width, the tree becomes a persistent collapsible rail and the inspector becomes a drawer. At phone width, the tree is a full-height navigation drawer, the map remains pannable, and the inspector opens as a full-width sheet. All hierarchy, attachment, notes, run inspection, and canonical reopening actions remain available.

Loading keeps the last validated Project context visible and marks the changing region busy. Selection failures remain inside the inspector when the Project is still valid. Project-level failures replace the work surface with a visible error and retry action. Digest mismatch, stale receipt, unavailable source, blocked adapter, and unsupported surface have distinct labels and cannot render as empty success states.

Tree rows, map nodes, and run rows use focusable controls with visible focus. Map edges have text equivalents in the inspector and lineage view. Color cannot be the only carrier of state, domain type, warning, or selection.

## 13. Normalized lifecycle projection

Domain adapters map canonical states into:

```text
planned
prepared
queued
running
paused
cancelling
completed
failed
cancelled
blocked
unknown
```

The projection stores:

- canonical source state;
- normalized state;
- source generation or timestamp;
- observed time;
- adapter identity;
- reason when mapping is `blocked` or `unknown`.

A projection cannot overwrite the canonical state.

`cancelling` is nonterminal. It begins when global launch admission is revoked and remains until the native launch fence returns a verified pre-commit cancellation tombstone or the already committed canonical Job returns a terminal cancellation acknowledgement. Only then may the projection advance to `cancelled` and release resources.

The UI must display both normalized and canonical states when they differ materially.

## 14. Lineage rules

Supported edge modes include:

```text
owns
contains
derived_from
uses_input
produced
validated_by
compared_with
retried_from
resubmitted_from
supersedes
references
supports_conclusion
```

Each edge must include a stable edge key and typed metadata. Duplicate semantic edges must be idempotent.

Clone uses the existing `derived_from` mode from the fresh Plan draft resource to the exact immutable source Workflow Plan revision. Its edge key is `cloned-plan-intent`, and the child closed clone receipt binds the edge ID, mode, and both endpoints. It does not use an undefined `cloned_from` mode.

Lineage must support these representative paths:

```text
sequence/structure source
→ workflow revision
→ preparation
→ run group
→ workflow run
→ attempt
→ core job or typed domain run
→ artifacts and results
→ analysis or comparison
→ observation
→ conclusion
```

Cross-domain paths use receipt resources as the linked nodes.

## 15. Migration and compatibility

### 15.1 Existing workspaces

Every existing `workspace` remains byte-for-byte identified by its current ID and becomes an operator-facing Project.

No migration invents owner, hypothesis, success criteria, or scientific conclusions. Missing fields remain empty and are marked `needs_metadata_review`.

### 15.2 Existing experiments

Existing `experiment` aggregates remain Global Experiments. Existing names and questions populate the first migrated `bms.global-experiment.v1` revision where deterministic mapping is possible.

### 15.3 Existing workflows

Existing workflows already parented to an experiment can be classified through their registered adapter.

For a CM workflow:

- create one Protein In Silico Domain Experiment under the existing Global Experiment;
- bind the workflow to that Domain Experiment;
- preserve every existing resource and revision ID;
- record the migration in audit and lineage tables.

A workflow parented directly to a workspace remains in `needs_domain_assignment`. The UI requires explicit operator assignment before new launches. Migration must not guess a Global Experiment or domain.

### 15.4 Existing runs and receipts

Existing run groups, runs, attempts, and external receipts retain their identities. Domain binding is added through lineage and typed adapter receipts.

### 15.5 Legacy jobs

Legacy jobs remain unassigned. Operators can attach them through verified pickers. No automatic project assignment based on job name, batch name, path, or creation time is permitted.

## 16. Failure behavior

The manager must fail closed for:

- unknown adapter ID;
- unsupported adapter version;
- missing canonical domain entity;
- digest mismatch;
- stale head generation;
- invalid lifecycle transition;
- launch without an accepted preparation;
- launch without an idempotency key;
- cross-project parent mismatch;
- Domain Experiment attached to more than one Global Experiment;
- workflow attached to more than one Domain Experiment;
- result substitution;
- scheduler identity conflict;
- malformed terminal receipt;
- dispatcher ownership ambiguity.

Failures must return typed error codes and actionable messages. They must create audit events when the mutation reached a durable transaction boundary.

## 17. Security and integrity

- Saved workflow definitions remain declarative.
- Arbitrary commands, paths, scripts, imports, and executable references remain forbidden.
- External receipts use SHA-256 digests and server-owned verification.
- File paths from domain stores are never trusted without canonical-root validation.
- API models reject unknown fields.
- Cross-project resource references are rejected.
- Archive operations are reversible through an audited unarchive operation.
- Destructive delete is absent from the operator API.
- Adapter registration occurs in source and cannot be performed through HTTP.
- Runtime and terminal receipts remain immutable.
- Backup and restoration validate the child-owned `bms.project-manager.backup-receipt.v4`, `bms.project-manager.restoration-receipt.v4`, and `bms.project-manager.isolated-restoration-reopen.v2` contracts. They cover the exact stores `global-experiments`, `molbio-domain`, `molbio-ngs-domain`, `core-ngs`, and `biomodstack-native`; the exact roots `global-project-artifacts`, `molbio-governed-artifacts`, `ngs-governed-results`, and `bms-native-results`; every accepted `project-manager`, `ngs-molbio`, `protein`, `conformational-mapping`, `molecular-dynamics`, `frustrampnn`, `structure-prediction`, and `trajectory` authority family; and every retained historical schema/version in the sealed denominator. Coverage includes coherent SQLite snapshots, source commit/tree, high-watermarks, schema/trigger/migration digests, retrievable deterministic v2 file manifests, bounded object counts, isolated empty restore roots, connector reconciliation, and isolated restart. Source expectations and isolated observations use distinct authenticated verifier principals and key objects.
- Project export validates child-owned `bms.project-manager.export-receipt.v4`. It binds the same five-store, four-root, and eight-family denominator to one exact Project revision, immutable archive and v2 manifest identities, reproducible typed-row digests, object/file counts, candidate commit/tree, and a verifier-proved non-serving destination disjoint from every active store and root.

## 18. Operational requirements

The manager must expose health for:

- global database path and schema attestation;
- migration status;
- writer identity;
- dispatcher lease;
- pending outbox rows;
- oldest pending age;
- reconciliation lag;
- adapter registry and versions;
- failed receipt verification count;
- last successful backup;
- last successful export verification.

Development and Production keep separate `experiments.db` stores. Project IDs cannot be assumed portable between environments without verified export/import.

### 18.1 Current-source reconciliation gate

Before the first source edit for any PM package, fetch `origin/test`, require a clean isolated worktree, and record the exact implementation baseline commit, tree, branch policy, changed-path denominator, and package owner. Reopen every source seam used by that package. A historical assessment SHA, specification-design SHA, or prior runtime record is evidence only and cannot become field-level implementation authority.

If `origin/test` moves before review or publication, freeze the implemented package, inspect the incoming commits for authority or path overlap, port or reconcile without rewriting shared history, and rerun the exact package review against the resulting tree. Runtime source records are regenerated after the final source bytes and before managed Development activation.

The reconciled implementation must reuse these current seams:

- `platform/api/services/result_contracts.py` remains the producer/result-contract registry used to derive Project Manager result-surface descriptors;
- `platform/api/services/conformational_mapping/global_adapter.py` remains the reference for preallocated typed global scheduler materialization;
- `platform/api/services/rfd3_local_redesign.py` and `rfd3_local_redesign_v1` remain the RFD3 local redesign request and result authority;
- existing MolBio/NGS receipts, immutable reference sets, pooled assignment records, and sequence-QC manifests remain domain authority;
- `platform/frontend/src/lib/api.ts` remains the shared frontend API client and typed contract surface;
- existing specialized viewers and launchers remain canonical.

No parallel result registry, RFD3 contract, CM materializer, NGS reference store, or frontend transport client may be created for this feature. Any exact field, endpoint, uniqueness, or lifecycle assertion in an implementation plan must be rechecked against the reconciled source before code is written.

### 18.2 Lightweight delivery boundary

The product ships through two bounded delivery slices. This ordering provides an operator-visible organizer quickly while preserving the complete architecture.

Slices define product acceptance. PM packages in sections 0.5 and 0.6 define the current execution order and sole-owner boundaries. The numbered phases below are requirement groupings, not permission to create a parallel implementation path.

#### Slice A: Organize and reopen

Slice A is the first usable Project Manager release. It includes:

- create, edit, archive, restore, and reopen Projects;
- create, edit, archive, restore, and reopen Global Experiments and Domain Experiments;
- the approved Project tree, relationship map, and inspector composition;
- boxed Protein In Silico and NGS/MolBio Domain Experiment regions;
- bounded run, result, dataset, note, decision, activity, warning, and lineage projections;
- `Add existing` from the Project Manager and participating existing surfaces;
- verified reference, generated-output, and evidence attachment;
- actual run and replica display;
- canonical launcher/viewer reopening and validated return context;
- short notes, observations, decisions, and conclusions;
- compatibility access to existing workspaces and experiments.

Slice A does not add a new launch path. Existing launchers and schedulers remain fully usable. Work created outside the manager can be attached immediately through verified receipts.

Slice A acceptance requires one complete operator path: create Project → create Global Experiment → create both Domain Experiment types → attach one current protein job or result and one current NGS/MolBio record → inspect tree/map/inspector lineage → open the canonical source → record a decision → restart and reopen the same Project.

#### Slice B: Plan and execute in context

Slice B adds:

- immutable Workflow Plan revisions under Protein In Silico Domain Experiments;
- server-owned launch-context receipts for existing typed launchers;
- saved intent, prepare, explicit launch, run-group, attempt, retry, and reconciliation presentation;
- managed dispatcher and reconciler ownership;
- current protein capability adapters in the order defined in phase 6;
- output auto-linkage through terminal receipts.

Slice B cannot weaken or replace Slice A attachment and reopening behavior.

#### Deferred increments

These remain outside Slice A and Slice B unless separately authorized:

- full ELN document authoring;
- generalized plugin or user-authored adapter registration;
- cross-run comparison without an explicit compatibility adapter;
- liquid-handler and BioXP Domain Experiment implementations;
- enterprise access-control, witness, signature, or compliance workflows;
- automatic attachment based on names, paths, timestamps, or inferred scientific similarity.

## 19. Delivery phases

Implementation requires separate authorization. Code tests and live acceptance require explicit approval before execution.

### Phase 0: Reconcile source and freeze shared contracts

**Objective:** Start from the current `test` source, freeze schemas, and assign non-overlapping file ownership.

Required work:

- create the implementation branch or worktree from the then-current `test` branch;
- record baseline commit and tree;
- reopen every source seam named in section 18.1;
- verify the frozen Project v2, Global Experiment v2, Domain Experiment v3, Project-link v2, Research Record v2, Project Manager read-model v2, attachment-receipt, and result-surface schemas; add only versioned successors for any later correction;
- register the accepted schema bytes in the runtime registry during their owning PM package rather than treating prose or a file path as installed authority;
- reconcile the NGS/MolBio receipt shape with its current worker-owned contracts;
- define backend, frontend, adapter, and migration ownership.

Likely files:

- `docs/specs/global-bms-project-experiment-manager.md`
- new schemas under `docs/specs/schemas/`
- a separate implementation plan under `docs/plans/`

Acceptance gate:

- no exact contract statement relies on the older specification worktree;
- hierarchy and authority match this specification;
- shared schemas are frozen before parallel source edits;
- each worker has a non-overlapping file boundary.

### Phase 1: Project hierarchy and canonical API

**Objective:** Extend the existing global store and expose Project, Global Experiment, and Domain Experiment operations through one service path.

Likely files:

- `platform/api/experiment_models.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- `platform/api/routers/experiment_workspaces.py`
- new `platform/api/routers/projects.py`
- `platform/api/main.py`

Acceptance gate:

- stable hierarchy and immutable revisions;
- idempotent create plus generation-checked edit, archive, and restore;
- Global and Domain Experiment removal uses archival without canonical-run cancellation;
- compatibility routes call the same services;
- backup and export include every new record.

### Phase 2: Slice A receipt, read-model, and canonical-routing plane

**Objective:** Provide enough verified cross-store data for the first usable organizer without introducing launch orchestration.

Likely files:

- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- new `platform/api/services/global_experiments/adapters.py`
- new `platform/api/services/global_experiments/receipts.py`
- new `platform/api/services/global_experiments/read_models.py`
- new `platform/api/services/global_experiments/result_surfaces.py`
- new focused adapters for current core jobs/results, MD, CM, RFD3, FrustraMPNN, and one current NGS/MolBio receipt path.

Acceptance gate:

- generic callers cannot self-assert receipts;
- attachment is idempotent and verifies canonical identity and digest;
- stale, unavailable, unsupported, and mismatched sources fail visibly;
- the Project Manager composite envelope is bounded and digest-bound;
- result routing derives from the current result-contract and domain registries;
- at least one current protein record and one current NGS/MolBio record can be attached and reopened.

### Phase 3: Slice A Project Manager frontend

**Objective:** Implement the approved tree, map, blocks, inspector, and ELN-lite interaction model.

Likely files:

- `platform/frontend/src/App.tsx`
- `platform/frontend/src/components/Layout.tsx`
- new `platform/frontend/src/components/projects/`
- `platform/frontend/src/lib/api.ts`
- participating launcher, job, result, dataset, MolBio, NGS, and artifact surfaces for the shared `Add to Project` trigger.

Acceptance gate:

- the Research Map remains the base composition;
- the Project tree, relationship map, and inspector synchronize through validated URL state;
- Global and Domain Experiments use boxed map regions;
- MD and other actual runs use compact rows with type-specific inspector detail;
- create, edit, archive, restore, attach, reopen, note, decision, and lineage paths are present;
- canonical viewers preserve a validated Project return path;
- desktop and mobile behavior follows section 12.16;
- browser storage owns no Project identity or membership.

### Phase 4: Slice A Development acceptance

**Objective:** Prove the first usable organizer through one real Development path.

Required scenario:

1. Create a Project and Global Experiment.
2. Create Protein In Silico and NGS/MolBio Domain Experiments.
3. Attach one current protein job or result.
4. Attach one current NGS/MolBio record.
5. Inspect both through tree, map, and inspector.
6. Open each canonical source and return to the exact Project context.
7. Record an observation and decision.
8. Archive and restore one experiment.
9. Restart the service and reopen the complete Project.
10. Verify backup and export coverage.

Slice A is usable after this gate. Existing launchers remain the execution entrypoints.

### Phase 5: Slice B launch context, dispatcher, and reconciliation

**Objective:** Consume the NGS/MolBio-owned PM-08A shared Plan/launch package, then add only parent-owned Protein integration without replacing typed launchers or the scheduler.

Shared files named below remain owned by the PM-08A executor until the shared-package receipt passes. The Protein executor can add domain adapters and native launcher integration only after that gate.

Likely shared or consumer files:

- `platform/api/experiment_models.py`
- `platform/api/experiment_services.py`
- `platform/api/services/global_experiments/launch_contexts.py`
- `platform/api/services/global_experiments/worker.py`
- `platform/api/main.py`
- typed launcher integration points.

Acceptance gate:

- immutable Workflow Plan revisions can be saved and prepared without launching;
- opaque server-owned launch context survives launcher save and submit;
- one visible writer owns dispatch;
- stale leases recover safely;
- restart does not duplicate jobs;
- terminal receipts auto-link canonical outputs;
- retry, resubmit, and clone preserve distinct lineage semantics.

This phase cannot create a second launch-context service, dispatcher, reconciler, nested Plan route, Dataset route, artifact/log writer, pagination policy, resource-admission ledger, or health model.

### Phase 6: Slice B Protein In Silico adapter completion

**Objective:** Execute PM-08B after the accepted PM-08A shared package. Bind installed Protein and MD capabilities through current typed authorities without changing shared wrappers.

Implementation order:

1. Ordinary core protein jobs and result receipts.
2. RFD3 local redesign.
3. Structure prediction and design results.
4. Conformational Mapping.
5. Molecular Dynamics.
6. FrustraMPNN result, comparison, and guidance attachments.
7. Cross-run datasets and explicitly compatible comparisons.

Acceptance gate:

- each enabled capability can be planned, saved, prepared, launched, reopened, and reviewed through a Protein In Silico Domain Experiment;
- typed settings retain UI/API parity;
- every result links to canonical data and artifacts;
- existing specialized launchers and viewers remain authoritative;
- no model, algorithm, runtime, or result fallback occurs;
- comparison remains unavailable until a registered adapter proves compatibility.

### Phase 7: NGS/MolBio contract completion

**Objective:** Consume the separate worker's complete domain layer through the frozen receipt and result contract.

Acceptance gate:

- molecular revisions, constructs, operations, ONT runs, assignments, QC manifests, and NGS analyses can be attached through verified receipts;
- domain-specific editing remains in Mol Bio Toolkit and NGS Toolkit;
- cross-domain lineage is visible in the same Project map;
- immutable NGS handoffs preserve their current authority and revision topology.

### Phase 8: Complete Development acceptance

**Objective:** Prove Slice B and the complete first-version contract.

Required scenario:

1. Reopen the accepted Slice A Project.
2. Save and revise a Workflow Plan without launching.
3. Prepare and inspect validation.
4. Launch through the canonical typed launcher and supported scheduler.
5. Observe run-group, attempt, domain lifecycle, and terminal receipt reconciliation.
6. Reopen canonical results.
7. Attach analysis evidence and record a conclusion.
8. Restart and prove durable state, dispatcher ownership, backup, and export.

Production promotion remains a separate authorization gate.

## 20. Verification requirements for implementation

When Christian separately authorizes tests, the implementation must include:

- migration tests from the current experiment schema;
- immutable-revision and generation-conflict tests;
- lifecycle transition tests;
- cross-project isolation tests;
- receipt verification and digest-mismatch tests;
- idempotent attachment tests;
- dispatch crash-window and stale-lease tests;
- duplicate scheduler materialization tests;
- reconciliation tests;
- backup, restore, export, and verification tests;
- frontend API-contract tests;
- frontend create, reopen, revise, archive, and attach flows;
- bounded summary-envelope and pagination tests;
- result-surface routing tests for ordinary protein results, MD, CM, FrustraMPNN, NGS, MolBio, download-only artifacts, and unsupported records;
- stale receipt, source-unavailable, and digest-mismatch rendering tests;
- stale asynchronous viewer-response rejection tests;
- presentation-envelope size tests through the deployed reverse-proxy path;
- real Development scheduler acceptance;
- browser-visible lineage and result reopening;
- current SHA, tree, process, listener, database, and worker ownership proof.

Mocked adapters can verify contract behavior. They cannot satisfy live scientific acceptance.

## 21. Definition of done

The Global BMS Project and Experiment Manager is complete when:

- Projects are first-class operator-visible records;
- the approved Project tree, relationship map, boxed experiment regions, and selected-node inspector are the primary Project Manager composition;
- tree, map, inspector, and validated URL selection stay synchronized;
- Global and Domain Experiment removal is reversible archival that retains lineage and never cancels canonical runs;
- Global Experiments are durable Project children;
- typed Domain Experiments are durable Global Experiment children;
- the Protein In Silico layer covers all installed in-scope protein and MD capabilities;
- the separate NGS / MolBio layer integrates through the same global contract;
- saved workflow revisions can be prepared and launched later;
- launch, retry, and reconciliation preserve immutable lineage;
- domain records stay authoritative in their owning stores;
- every cross-store link is verified and digest-bound;
- global artifacts, logs, activities, notes, observations, and conclusions are visible;
- Project and Experiment pages use bounded read models whose source receipts and digest set are visible;
- the global result index opens canonical specialized viewers without duplicating their scientific logic;
- viewer responses stay bound to canonical result contracts and artifact digests;
- Project and Experiment state survives restart and can be reopened from the UI;
- the `/designer` and `/ngs` selected-context default state passes section 0.4 at all three desktop viewports, has no duplicate authority controls, and resolves a signed `bms.operator-visual-acceptance.v4` receipt for `christian-release-owner-v1` through owner enrollment, the protected COSE trust registry, one-use challenge consumption, and atomic sign-counter authority;
- existing CM global records migrate without identity loss or invented metadata;
- unassigned legacy jobs remain available and can be attached safely;
- dedicated launchers and viewers remain the scientific configuration and result authorities;
- Development acceptance passes on the deployed source revision;
- backup and export verification pass;
- no unresolved authority conflict can change the operator-visible record.

## 22. Final architectural rule

The global manager organizes research and records why activities belong together. Domain systems determine what each scientific record means. Verified receipts connect the two authority layers.
