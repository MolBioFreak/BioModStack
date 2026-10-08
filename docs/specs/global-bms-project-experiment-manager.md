# Global BMS Project and Experiment Manager Specification

**Status:** Approved implementation-controlling design
**Scope owner:** Global BMS manager and Protein In Silico experiment layer
**Specification worktree baseline:** `8112bed622740b890e25b69c2bcf29ebebeaf3d6`
**Specification baseline tree:** `32bad99d697aa221a2b75e0e824bd86bfdd3d23a`
**Latest local `origin/test` observed during final design:** `edf34d9`
**Target product:** BioModStack
**Document type:** Product, architecture, persistence, API, UI, migration, and delivery specification

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
- one global UI for project and experiment management;
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

The existing global UI is absent. The only substantive scientific adapter is Conformational Mapping. Several artifact, log, sync, and dispatch facilities remain schema-only or API-triggered.

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
  "reopen_uri": "typed internal BMS route",
  "metadata": {}
}
```

The server-owned adapter must query the canonical source before issuing a receipt. The generic HTTP caller cannot self-assert record existence or digest correctness.

A missing source record, digest mismatch, or unsupported entity kind must fail closed.

Each receipt must contain `content_digest`, `contract_digest`, or both. The adapter must state which bytes or normalized contract produced each digest.

## 7. Terminology and object contracts

### 7.1 Project

A Project is the operator-facing form of the existing global `workspace` aggregate.

Required Project revision fields:

```json
{
  "schema": "bms.project.v1",
  "name": "string",
  "description": "string",
  "research_objective": "string",
  "owner": "string or null",
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
  "created_by": "string or null",
  "change_summary": "string"
}
```

Project edits create immutable revisions and advance one generation-checked head. Archival changes lifecycle state and retains all children.

### 7.2 Global Experiment

A Global Experiment is one study or experimental tranche inside a Project.

Required Global Experiment revision fields:

```json
{
  "schema": "bms.global-experiment.v1",
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
  "created_by": "string or null",
  "change_summary": "string"
}
```

The hypothesis field can be empty for exploratory work. Success criteria must be explicit before the experiment enters `active` state.

Domain Experiment membership comes from each child aggregate's immutable parent relationship. It is not duplicated inside the Global Experiment revision payload.

Completion requires a terminal review revision. The review can state inconclusive, failed, or negative results. Completion does not imply scientific success.

### 7.3 Domain Experiment

A Domain Experiment is a typed child of one Global Experiment.

Common fields:

```json
{
  "schema": "bms.domain-experiment.v1",
  "domain_kind": "protein_in_silico|ngs_molbio",
  "domain_contract_version": "string",
  "name": "string",
  "objective": "string",
  "status": "draft|planned|active|analysis|review|completed|blocked|archived",
  "tags": ["string"],
  "source_receipt_ids": ["receipt ID"],
  "dataset_ids": ["dataset ID"],
  "created_by": "string or null",
  "change_summary": "string",
  "domain_payload": {}
}
```

The `domain_payload` must validate against a server-owned schema registered for `domain_kind` and `domain_contract_version`.

Workflow, activity, and result membership comes from child parent relationships and typed lineage edges. These dynamic memberships are not duplicated inside Domain Experiment revisions.

A Domain Experiment belongs to exactly one Global Experiment. A domain record can be referenced by several Domain Experiments when each relationship has an explicit lineage role. The global manager does not change the domain record’s canonical ownership.

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

Required common fields:

```json
{
  "schema": "bms.research-record.v1",
  "record_kind": "note|observation|conclusion|decision",
  "subject_resource_id": "opaque ID",
  "body": "string",
  "author": "string or null",
  "source_receipt_ids": ["receipt ID"],
  "supersedes_record_id": "opaque ID or null",
  "created_at": "UTC timestamp"
}
```

## 8. Protein In Silico experiment contract

### 8.1 Purpose

The Protein In Silico experiment layer organizes protein-AI and molecular-simulation work without replacing the typed launchers, scheduler, scientific stores, or result viewers.

### 8.2 Required domain payload

```json
{
  "schema": "bms.protein-in-silico-experiment.v1",
  "experiment_mode": "exploration|design|redesign|prediction|validation|comparison|simulation|analysis",
  "targets": [
    {
      "target_id": "operator-visible stable identity",
      "label": "string",
      "entity_receipt_ids": ["receipt ID"],
      "role": "target|binder|partner|template|reference|control|other"
    }
  ],
  "scientific_objective": "string",
  "design_constraints": ["typed constraint object"],
  "planned_capabilities": ["registered capability ID"],
  "comparison_groups": ["typed grouping object"],
  "validation_strategy": ["registered validation capability ID"]
}
```

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
- `record_kind`
- `body`
- `author`
- `source_receipt_ids_json`
- `supersedes_record_id`
- `created_at`

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

The global manager must expose these states while a saga converges:

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

## 11. API contract

### 11.1 Canonical project routes

```text
GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}
PATCH  /api/projects/{project_id}
POST   /api/projects/{project_id}/archive
POST   /api/projects/{project_id}/restore
GET    /api/projects/{project_id}/revisions
GET    /api/projects/{project_id}/activity
GET    /api/projects/{project_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/records
```

### 11.2 Global experiment routes

```text
GET    /api/projects/{project_id}/experiments
POST   /api/projects/{project_id}/experiments
GET    /api/projects/{project_id}/experiments/{experiment_id}
PATCH  /api/projects/{project_id}/experiments/{experiment_id}
POST   /api/projects/{project_id}/experiments/{experiment_id}/archive
POST   /api/projects/{project_id}/experiments/{experiment_id}/restore
GET    /api/projects/{project_id}/experiments/{experiment_id}/revisions
GET    /api/projects/{project_id}/experiments/{experiment_id}/activity
GET    /api/projects/{project_id}/experiments/{experiment_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/experiments/{experiment_id}/records
```

### 11.3 Domain experiment routes

```text
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
PATCH  /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/archive
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/restore
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity
GET    /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records?kind=&cursor=&limit=
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records
POST   /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/attach
```

### 11.4 Workflow routes

Existing workflow, draft, revision, preparation, run-group, retry, and reconcile operations remain. Canonical new routes nest them under a Domain Experiment.

Every mutation must use strict request models. Revision-changing requests require `expected_head_generation`. Launch requests require an idempotency key.

### 11.5 Search and picker routes

```text
GET /api/projects/search
GET /api/projects/{project_id}/experiments/search
GET /api/domain-adapters
GET /api/domain-adapters/{adapter_id}/entities/search
POST /api/domain-adapters/{adapter_id}/entities/{entity_id}/receipt
```

Search results are read-only projections. Attaching an entity requires a newly verified receipt.

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
- owner and contributors;
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

Launching from a Domain Experiment creates a short-lived, server-owned launch-context receipt. The browser receives an opaque `launch_context_id`. The dedicated launcher resolves that ID through the API and receives:

```json
{
  "project_id": "opaque ID",
  "global_experiment_id": "opaque ID",
  "domain_experiment_id": "opaque ID",
  "workflow_id": "opaque ID or null",
  "workflow_revision_id": "opaque ID or null",
  "return_uri": "typed internal route"
}
```

The dedicated launcher loads the context, preserves it during save and submit, and displays the destination Project and Experiment.

The browser cannot author or alter Project, Global Experiment, Domain Experiment, Workflow, or Workflow Revision identity inside this context. Expired, consumed, mismatched, or unknown launch-context IDs fail closed.

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

Each global page consumes one bounded summary envelope before it requests optional tables or scientific details.

Required endpoints:

```text
GET /api/projects/{project_id}/summary
GET /api/projects/{project_id}/map?focus_id=&cursor=&limit=
GET /api/projects/{project_id}/experiments/{experiment_id}/summary
GET /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/summary
GET /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity?cursor=&limit=
GET /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/results?cursor=&limit=
GET /api/projects/{project_id}/experiments/{experiment_id}/lineage?cursor=&limit=
```

Every summary envelope includes:

```json
{
  "schema": "bms.global-read-model.v1",
  "subject_id": "opaque ID",
  "subject_generation": 1,
  "assembled_at": "UTC timestamp",
  "source_receipt_ids": ["receipt ID"],
  "source_digest_set_sha256": "sha256 hex digest",
  "adapter_versions": [{"adapter_id": "string", "version": "string"}],
  "reconciliation": {
    "state": "current|pending|stale|source_unavailable|digest_mismatch",
    "last_verified_at": "UTC timestamp or null",
    "reason": "string or null"
  },
  "counts": {},
  "status_summary": {},
  "recent_activity": [],
  "result_previews": [],
  "pagination": {}
}
```

#### 12.9.1 Project Manager composite envelope

The initial Project Manager request uses the existing Project summary route with optional validated focus and selection parameters:

```text
GET /api/projects/{project_id}/summary?focus_id=&selected_node_key=
```

The response extends `bms.global-read-model.v1` with one bounded Project Manager payload:

```json
{
  "project": {
    "id": "opaque ID",
    "name": "string",
    "objective": "string",
    "lifecycle_state": "string",
    "head_generation": 1,
    "current_revision_id": "opaque ID",
    "updated_at": "UTC timestamp"
  },
  "tree": {
    "nodes": [
      {
        "node_key": "typed stable key",
        "node_type": "project|global_experiment|domain_experiment|virtual_folder",
        "subject_id": "opaque ID or null",
        "parent_node_key": "typed stable key or null",
        "label": "string",
        "lifecycle_state": "string or null",
        "counts": {},
        "has_children": true,
        "allowed_actions": []
      }
    ]
  },
  "map": {
    "focus_node_key": "typed stable key",
    "nodes": [],
    "edges": [],
    "truncated": false,
    "next_cursor": null
  },
  "selection": {
    "node_key": "typed stable key",
    "node_type": "typed node kind",
    "title": "string",
    "subtitle": "string or null",
    "canonical_identity": {},
    "summary": {},
    "relationship": {},
    "scientific_context": {},
    "reconciliation": {},
    "available_actions": [],
    "canonical_surface": null
  },
  "runs": {
    "items": [],
    "next_cursor": null
  },
  "warnings": [],
  "allowed_actions": []
}
```

The tree response is complete only through Domain Experiment and virtual-folder level. It never embeds every run, result, dataset member, note, or activity. Map nodes and edges are bounded. The implementation must declare and enforce a maximum map-node count. A truncated response keeps count-bearing collapsed nodes and returns a cursor or narrower-focus action.

Each map node includes `node_key`, `node_type`, label, normalized state, canonical source identity when applicable, count summary, reconciliation state, and allowed actions. Each edge includes source and target node keys, lineage mode, stable edge key, and an accessible relationship label.

Each run item includes:

```json
{
  "run_id": "global run identity",
  "canonical_job_id": "domain job/run identity",
  "workflow_type": "typed workflow family",
  "target_label": "operator label",
  "canonical_state": "source state",
  "normalized_state": "global state",
  "stage": "source stage or null",
  "progress": {"kind": "fraction|elapsed|indeterminate", "value": null},
  "started_at": "UTC timestamp or null",
  "elapsed_seconds": 0,
  "replica_index": null,
  "batch_or_run_group_id": "opaque ID or null",
  "output_count": 0,
  "condition": {"severity": "none|warning|failure", "code": null, "message": null},
  "receipt_id": "verified receipt ID",
  "adapter_id": "registered adapter ID",
  "available_actions": [],
  "canonical_surface": null
}
```

Run actions such as `open_results`, `view_lineage`, `retry`, `resubmit`, or `clone` are server-issued. The Project Manager cannot infer an action from state labels. Retry and resubmit route through the canonical domain operation and publish new lineage. Clone creates new immutable intent and never mutates the selected run.

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
  "route": "typed same-origin BMS route or null",
  "readiness": "running|partial|ready|failed|blocked|unsupported",
  "native_summary": {},
  "scientific_acceptance": {
    "state": "passed|failed|review|unavailable|not_applicable",
    "reason": "string or null"
  },
  "provenance": {},
  "available_actions": ["open|download|compare|attach_evidence"]
}
```

The server derives `surface_kind`, route, contract, readiness, and actions. The browser cannot infer them from `model_id`, file extension, output path, or job name.

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
retries
supersedes
references
supports_conclusion
```

Each edge must include a stable edge key and typed metadata. Duplicate semantic edges must be idempotent.

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
- Backup and export manifests contain schema version, source revision, database digest, and object counts.

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

This specification worktree started at commit `8112bed622740b890e25b69c2bcf29ebebeaf3d6`. At the final design pass, the local `origin/test` reference was nine commits ahead at `edf34d9`. Those commits changed current RFD3 local redesign, Conformational Mapping, result-contract, NGS/MolBio receipt, sequence-QC, workflow-adapter, and frontend API surfaces.

The design remains valid, but implementation cannot use the older worktree as field-level source authority. Before the first source edit, the implementation branch must be created from or reconciled with the then-current `test` branch. The executor must reopen the affected files and record the exact implementation baseline SHA and tree.

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
- write Project, Global Experiment, Domain Experiment, attachment receipt, read-model, and result-surface schemas;
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
- generation-checked create, edit, archive, and restore;
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

**Objective:** Add saved intent and execution context without replacing typed launchers or the scheduler.

Likely files:

- `platform/api/experiment_models.py`
- `platform/api/experiment_services.py`
- new `platform/api/services/global_experiments/launch_contexts.py`
- new `platform/api/services/global_experiments/dispatcher.py`
- new `platform/api/services/global_experiments/reconciler.py`
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

### Phase 6: Slice B Protein In Silico adapter completion

**Objective:** Bind installed protein and MD capabilities through current typed authorities.

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
- existing CM global records migrate without identity loss or invented metadata;
- unassigned legacy jobs remain available and can be attached safely;
- dedicated launchers and viewers remain the scientific configuration and result authorities;
- Development acceptance passes on the deployed source revision;
- backup and export verification pass;
- no unresolved authority conflict can change the operator-visible record.

## 22. Final architectural rule

The global manager organizes research and records why activities belong together. Domain systems determine what each scientific record means. Verified receipts connect the two authority layers.
