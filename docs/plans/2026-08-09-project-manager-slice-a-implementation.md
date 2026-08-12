# Global Project Manager Slice A Implementation Plan

> **For Hermes:** Load `subagent-driven-development`, `test-driven-development`, and the controlling specification before source implementation. Source edits, tests, commits, pushes, and runtime acceptance require Christian's separate authorization.

**Goal:** Deliver the first usable BMS Project Manager for organizing, attaching, inspecting, and reopening current scientific work.

**Architecture:** Extend the current global workspace/experiment store. Add Project-facing routes over the same services, verified cross-store adapters, one bounded Project Manager read model, and the approved Project-tree/relationship-map/inspector frontend. Existing domain stores, launchers, schedulers, result contracts, and viewers remain authoritative.

**Tech stack:** FastAPI, Pydantic, SQLAlchemy async, SQLite migrations, React, TypeScript, React Router, TanStack Query, existing BMS styling and API client.

**Controlling specification:** `docs/specs/global-bms-project-experiment-manager.md`

**Approved UI authority:** `docs/mockups/project-manager/07-map-blocks-runs/index.html`

---

## Scope for this plan

This plan covers Slice A only:

- Projects, Global Experiments, and typed Domain Experiments;
- reversible archive and restore;
- verified attachment of existing records;
- Project tree, relationship map, selected-node inspector, and actual-run rows;
- notes, observations, decisions, and conclusions;
- canonical-source reopening with validated return context;
- one current protein attachment path and one current NGS/MolBio attachment path;
- restart, backup, and export acceptance.

This plan excludes launch-context, dispatcher, reconciler, Workflow Plan execution, broad protein-adapter completion, comparisons, liquid handling, BioXP experiments, and full ELN authoring.

## Hard start gate

The specification worktree is based on `8112bed622740b890e25b69c2bcf29ebebeaf3d6`. Its local `origin/test` reference was at `edf34d9` during final design.

Before implementation:

1. Create a clean implementation branch and worktree from the then-current `test` branch.
2. Record `git rev-parse HEAD` and `git rev-parse HEAD^{tree}`.
3. Reopen every file listed below from that worktree.
4. Reconcile exact fields and callable names before writing source.
5. Keep the specification worktree read-only during implementation.
6. Preserve unrelated changes in all shared worktrees.

Do not copy stale source files from the specification worktree into the implementation worktree.

## File ownership

| Lane | Owned paths | Boundary |
|---|---|---|
| Core persistence/API | `platform/api/experiment_*.py`, `platform/api/routers/projects.py`, compatibility-router changes, migrations | Own hierarchy, revisions, archive/restore, notes, audit, backup/export |
| Adapters/read model | new `platform/api/services/global_experiments/` | Own verified receipts, Project Manager envelope, result-surface projection; reuse current domain services |
| Frontend Project Manager | new `platform/frontend/src/components/projects/`, Project Manager route/navigation, Project API types | Own approved composition and state model |
| Existing-surface integration | bounded edits to current job/result/MolBio/NGS/artifact components | Add only the shared `Add to Project` trigger and return-context display |

Only one worker owns `platform/frontend/src/lib/api.ts` at a time. Only the core lane changes shared experiment models or migrations.

---

## Work package 0: Freeze current schemas and source seams

**Objective:** Turn the final design into exact source-level contracts on the current implementation baseline.

**Create:**

- `docs/specs/schemas/project-v1.schema.json`
- `docs/specs/schemas/global-experiment-v1.schema.json`
- `docs/specs/schemas/domain-experiment-v1.schema.json`
- `docs/specs/schemas/external-entity-receipt-v1.schema.json`
- `docs/specs/schemas/project-manager-read-model-v1.schema.json`
- `docs/specs/schemas/result-surface-v1.schema.json`

**Inspect before writing:**

- `platform/api/experiment_models.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- `platform/api/routers/experiment_workspaces.py`
- `platform/api/services/result_contracts.py`
- `platform/api/services/rfd3_local_redesign.py`
- `platform/api/services/conformational_mapping/global_adapter.py`
- current MolBio/NGS receipt and sequence-QC services
- `platform/frontend/src/lib/api.ts`

**Acceptance:**

- schemas match the controlling hierarchy and approved composite envelope;
- all unknown fields fail closed;
- current domain identities and digests map without copying payloads;
- result-surface routing derives from current registries;
- no duplicate source-of-truth service is proposed.

## Work package 1: Add hierarchy persistence and migration

**Objective:** Represent Domain Experiments and ELN-lite records while preserving existing workspace and experiment identities.

**Modify:**

- `platform/api/experiment_models.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_operations.py`

**Required behavior:**

- keep existing `workspace` resources as operator-facing Projects;
- keep existing `experiment` resources as Global Experiments;
- add a typed `domain_experiment` aggregate kind;
- add append-only note, observation, decision, and conclusion records with replacement links;
- use existing revision, lineage, receipt, audit, artifact, and log tables where they already fit;
- archive and restore through lifecycle transitions and head-generation checks;
- include new records in backup and deterministic Project export;
- avoid membership arrays that duplicate parent relationships or lineage.

**Focused checks after test authorization:**

- migrate a legacy workspace and experiment without changing IDs;
- create two Domain Experiments of the same type under one Global Experiment;
- reject cross-Project parenting;
- preserve active domain runs when a parent is archived;
- restore an archived experiment with an audit event;
- verify backup/export object coverage.

## Work package 2: Add canonical Project services and routes

**Objective:** Expose Project terminology through one service path without forking compatibility behavior.

**Modify:**

- `platform/api/experiment_services.py`
- `platform/api/routers/experiment_workspaces.py`
- `platform/api/main.py`

**Create:**

- `platform/api/routers/projects.py`

**Required routes:**

- Project CRUD, archive, restore, revisions, and activity;
- Global Experiment CRUD, archive, restore, revisions, and activity;
- Domain Experiment CRUD, archive, restore, activities, and attach;
- bounded Project summary with validated `focus_id` and `selected_node_key`;
- bounded runs, results, lineage, notes, and activity pagination.

**Required behavior:**

- strict request models with unknown-field rejection;
- mutations require expected head generation where applicable;
- `/api/projects` and `/api/experiment-workspaces` call the same services;
- typed error codes distinguish missing, foreign, stale generation, invalid transition, and unsupported operation;
- removal uses archive and cannot cancel a canonical domain run.

## Work package 3: Add verified attachment adapters

**Objective:** Attach existing scientific records through source-owned verification.

**Create:**

- `platform/api/services/global_experiments/__init__.py`
- `platform/api/services/global_experiments/adapters.py`
- `platform/api/services/global_experiments/receipts.py`
- focused adapter modules for the representative Slice A protein and NGS/MolBio paths.

**Required behavior:**

- register adapters in source only;
- search returns bounded read-only projections;
- receipt issuance re-reads canonical identity, revision, availability, contract, and digest;
- attachment declares one of `references`, `uses_input`, `produced`, `validated_by`, or another approved lineage mode;
- idempotent reattachment returns the existing semantic edge and receipt;
- stale, missing, unavailable, unsupported, or digest-mismatched records fail visibly;
- no path, job name, timestamp, or label infers membership.

**Representative first adapters:**

- one current core/RFD3 protein job or result through current result contracts;
- one current MolBio/NGS receipt or immutable reference/QC record through its domain service.

## Work package 4: Build the bounded Project Manager read model

**Objective:** Serve the complete Project tree level, focused relationship map, selected-node inspector, and first run page without broad joins or scans.

**Create:**

- `platform/api/services/global_experiments/read_models.py`
- `platform/api/services/global_experiments/result_surfaces.py`

**Required behavior:**

- return complete Project → Global Experiment → Domain Experiment tree nodes plus virtual folders;
- return compact sibling Global Experiment nodes and one expanded focused experiment;
- bound map nodes, edges, runs, results, notes, and activity;
- keep collapsed count nodes when a map response truncates;
- include receipt IDs, digest-set hash, adapter versions, reconciliation state, warnings, and server-issued actions;
- derive result surfaces from current result and domain registries;
- return actual run/replica rows with canonical and normalized states;
- avoid reading large artifacts in page handlers;
- suppress actions unsupported by the canonical authority.

## Work package 5: Add frontend API types and Project Manager route

**Objective:** Establish the typed frontend boundary and top-level navigation.

**Modify:**

- `platform/frontend/src/App.tsx`
- `platform/frontend/src/components/Layout.tsx`
- `platform/frontend/src/lib/api.ts`

**Create:**

- `platform/frontend/src/components/projects/ProjectManagerPage.tsx`
- `platform/frontend/src/components/projects/projectManagerTypes.ts`
- `platform/frontend/src/components/projects/projectManagerState.ts`

**Required behavior:**

- add the top-level `Project Manager` tab;
- lazy-load `/projects` and `/projects/:projectId`;
- validate `focus` and `selected` against server response;
- use TanStack Query keys scoped by Project, focus, and selection;
- abort or ignore stale selection responses;
- retain exact URL state across browser back and forward;
- keep authoritative identity out of browser storage.

## Work package 6: Implement the approved three-pane composition

**Objective:** Build the approved Research Map base with borrowed block and run treatments.

**Create under `platform/frontend/src/components/projects/`:**

- `ProjectTree.tsx`
- `RelationshipMap.tsx`
- `GlobalExperimentNode.tsx`
- `DomainExperimentBlock.tsx`
- `ProjectInspector.tsx`
- `RunInspector.tsx`
- `RunRows.tsx`
- `ProjectManagerEmptyState.tsx`
- `ProjectManagerErrorState.tsx`

**Required behavior:**

- keep tree, map, and inspector together on desktop;
- show all Global Experiments compactly and expand one focus;
- segment focused Protein In Silico and NGS/MolBio work in boxes;
- show actual runs and MD replicas as compact inspector rows;
- synchronize tree selection, map emphasis, inspector content, and URL;
- provide text equivalents for map edges;
- support keyboard selection, visible focus, and non-color state labels;
- use collapsible rails and responsive inspector/tree drawers at smaller widths;
- preserve the approved mockup's information hierarchy without copying its illustrative data.

## Work package 7: Add ELN-lite and experiment management actions

**Objective:** Complete daily Project organization without adding a document system.

**Create under the Project frontend:**

- Project, Global Experiment, and Domain Experiment create/edit forms;
- archive/restore confirmations;
- short note, observation, decision, and conclusion forms;
- bounded activity display.

**Required behavior:**

- show current revision and generation;
- require expected generation for revisions and archive/restore;
- explain that remove means reversible archive;
- never imply archival cancels a run;
- append replacements instead of overwriting prior records;
- surface conflict errors with a reload-current-revision action.

## Work package 8: Implement one reusable Add-to interaction

**Objective:** Use one typed attachment flow in the Project Manager and participating BMS surfaces.

**Create:**

- `platform/frontend/src/components/projects/AddToProjectDialog.tsx`
- `platform/frontend/src/components/projects/AddToProjectTrigger.tsx`

**Modify only after the shared dialog is stable:**

- representative job/result surface;
- representative MolBio/NGS surface;
- artifact/download surface if it exposes canonical artifact identity.

**Required behavior:**

- select Project, Global Experiment, Domain Experiment, operation mode, lineage role, and optional note;
- distinguish reference, immutable input binding, generated output/evidence, and clone/import revision;
- display canonical source identity, revision, digest, and availability before confirmation;
- state whether source bytes remain in the original store;
- return and select the newly attached node after success;
- preserve typed failures without creating partial visibility.

## Work package 9: Canonical reopening and return context

**Objective:** Open existing launchers and viewers without creating a universal viewer.

**Modify:**

- Project Manager result and inspector actions;
- bounded existing viewer/launcher breadcrumb surfaces as required.

**Required behavior:**

- use only server-issued same-origin routes;
- preserve Project, focus, and selected-node return context;
- resolve and validate the binding receipt before showing Project breadcrumbs;
- route MD, CM, FrustraMPNN, ordinary protein, NGS, MolBio, and artifact records to their current surfaces;
- show unsupported and unavailable results explicitly.

## Work package 10: Slice A acceptance and handoff

**Objective:** Prove one real organizer path at an exact Development build identity.

**Future verification commands:** Determine exact selectors after Phase 0 source reconciliation. Run them only after Christian authorizes tests.

**Required evidence:**

- focused backend contract and migration checks;
- focused frontend API/state/component checks;
- one frontend build or type-check;
- the real Slice A operator path from section 19, phase 4;
- restart persistence;
- backup and export verification;
- deployed commit, tree, process, listener, database, adapter registry, and writer identity.

**Slice A handoff condition:** The operator can create and reopen a Project, traverse its scientific hierarchy, attach representative current protein and NGS/MolBio records, inspect lineage and actual runs, open canonical sources, record a decision, archive/restore an experiment, and recover the same state after restart.

---

## Implementation start order

Start with work package 0. After schema freeze, packages 1 and 3 can proceed in parallel because they own separate files. Package 2 follows the hierarchy services. Package 4 consumes packages 1 and 3. Package 5 can scaffold route and types after the read-model schema freezes. Packages 6 and 7 then proceed in the Project component directory. Package 8 starts after canonical attachment requests stabilize. Package 9 follows result-surface descriptors. Package 10 is the authorized acceptance gate.

Do not start Slice B dispatcher or launch-context work before Slice A reaches its acceptance gate.
