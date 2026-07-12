# BioModStack Beta Hardening Remediation Plan

> **For Hermes:** Execute with test-driven development, phase-scoped commits, independent spec review, independent code-quality review, and explicit revision/rollback gates.

**Goal:** Repair the confirmed BioModStack data-plane, ingestion/state, browser/Electron, workflow/GPU, and deployment-safety breakpoints; promote the verified tree through `test` to `main`; then rebuild and verify both dev and production runtimes.

**Architecture:** Preserve current dev/prod separation (`8002`/`5173` versus `8000`/`18080`). Make list/read paths bounded and side-effect free, make job completion transactional with ingestion, centralize client polling and resource ownership, make workflow/scheduler configuration persistent and deterministic, and add containment/provenance so a beta defect cannot exhaust the host or obscure the deployed revision.

**Runtime constraints:** Keep both runtimes offline during implementation. Do not launch workflows. Production promotion and runtime restarts occur only after all offline gates are green. Preserve credentials exclusively in `~/.biomodstack/env.sh` and never commit values.

**Commit policy:** One reviewed commit per phase or smaller coherent sub-phase. Every boundary requires: RED test evidence, GREEN targeted tests, diff/security scan, independent spec review, independent quality review, and rollback notes. Existing pre-hardening work is reconciled and committed separately before remediation starts.

---

## Phase 0: Baseline and Existing Work Reconciliation

### Scope

- Preserve the dirty checkout and local-ahead commits.
- Classify and finish pre-existing changes for dev/prod API-port separation, datetime compatibility, nucleotide ordering, and Protenix v2 defaults.
- Exclude generated `.ngs-tools` libraries/binaries and `wf_test*` work directories from commits.
- Verify commit identity is `MolBioFreak <teaguechristian@gmail.com>`.

### Acceptance gate

- Existing targeted tests are green or every remaining failure is explicitly classified.
- Each pre-existing logical change is committed separately.
- `git diff --check` passes.
- No generated workflow or binary artifacts are staged.

### Rollback

- Backup branch and external binary patch identify the pre-hardening state.

---

## Phase 1: Bounded Data Plane and Read-Side Safety

### Required behavior

1. `/api/jobs?summary=true` uses an explicit SQL projection and does not hydrate heavyweight `Job` JSON fields.
2. Job and design list parameters enforce positive offsets/limits and conservative hard caps.
3. Full job detail remains available only through the single-job endpoint.
4. Job-detail GET is side-effect free; analysis triggering requires an explicit mutation endpoint.
5. Add a durable index for `designs.job_id` through the repository's schema-initialization path and test the resulting schema/query contract.
6. Frontend list consumers request bounded summaries; no default full-500 job fetches remain.
7. NGS full-job five-second polling is removed or converted to a small summary/status query.

### Likely files

- `platform/api/routers/jobs.py`
- `platform/api/routers/designs.py`
- `platform/api/database.py`
- `platform/api/tests/test_jobs_list_summary.py`
- new/updated list-boundary and schema tests
- `platform/frontend/src/api/client.ts`
- `ReferenceSelector.tsx`, `BatchComparePane.tsx`, `DesignBrowser.tsx`, `JobBrowser.tsx`, `NGSToolkit.tsx`, `LigandSelector.tsx`, `ResultsViewer.tsx`, `Dashboard.tsx`, `QuickViewer.tsx`
- frontend contract tests

### Acceptance gate

- A regression test proves summary queries do not select heavyweight columns.
- API rejects zero, negative, and above-cap limits.
- The database has an index covering `designs(job_id)`.
- Source tests prove all list consumers use bounded summary calls.
- Reading a job never starts analysis.
- Targeted API/frontend tests, typecheck, and diff review pass.

### Rollback

- Revert the phase commit; the schema addition is additive and safe to retain if needed.

---

## Phase 2: Transactional Result Ingestion and Job-State Truth

### Required behavior

1. Remove duplicate `artifact_class`/`artifact_schema_version` constructor arguments.
2. A job is not finalized as `completed` until required result ingestion succeeds.
3. Ingestion failure produces an explicit partial/failed state with actionable error context.
4. Reconciliation uses the same ordering and state-transition contract as normal completion.
5. State transitions update `status`, `queue_status`, timestamps, awaiting fields, and error fields consistently.
6. Provide a dry-run-first consistency auditor/repair command for orphan children, contradictory status/queue states, completed-without-completion-time, and unusable awaiting-input records.
7. No production database mutation occurs during implementation; repair is exercised against fixtures/copies only.

### Likely files

- `platform/api/services/result_ingester.py`
- `platform/api/services/nextflow.py`
- `platform/api/services/gpu_orchestrator.py`
- `platform/api/database.py`
- focused ingestion, reconciliation, and state-machine tests
- a repository-owned audit/repair script and tests if no existing command is suitable

### Acceptance gate

- Regression test reproduces and eliminates the duplicate-keyword failure.
- Ingestion failure cannot leave a newly completed job.
- Normal completion and reconciliation share equivalent terminal-state semantics.
- Repair tool defaults to dry-run, is idempotent, and reports exact planned changes.
- Targeted and broader API tests pass.

### Rollback

- Revert the phase commit; do not run repair against production until after deployment backup and reviewed dry-run.

---

## Phase 3: Browser and Electron Resource Ownership

### Required behavior

1. Fix the Antibody de novo effect so state it mutates is not part of a self-sustaining dependency cycle.
2. Every object URL has one clear owner and balanced revocation on replacement/unmount.
3. Mol* viewers/overlays, timers, listeners, workers, and subscriptions are explicitly disposed.
4. Bulk Results Viewer loading and client-side sorting are bounded and paginated.
5. Polling is centralized/deduplicated, backs off when unavailable, and pauses when hidden/offline where appropriate.
6. React Query has explicit large-query garbage-collection/refetch policy.
7. Electron keeps persistent renderer-failure diagnostics containing timestamp, reason, exit code, runtime channel, URL, and bounded memory context.
8. Runtime-channel switching waits for channel-specific readiness and does not report stale context.

### Likely files

- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
- `ResultsViewer.tsx` and high-pressure job consumers
- `platform/frontend/src/main.tsx`
- resource-owning viewer components and frontend tests
- `platform/desktop-electron/src/windowDiagnostics.ts`
- `runtimeChannels.ts`, `appWindow.ts`, `main.ts`, and Electron tests

### Acceptance gate

- Regression test proves one antibody PDB input creates at most one current blob URL and does not loop.
- Cleanup tests cover replacement and unmount.
- No 50,000-row browser path remains.
- Polling contracts are bounded and visibility/offline aware.
- Frontend generated tests, typecheck, lint, production build, and Electron tests pass.

### Rollback

- Revert the phase commit; API hard caps from Phase 1 remain protective.

---

## Phase 4: Workflow, NGS, and GPU Scheduling Correctness

### Required behavior

1. Resolve all current Nextflow lint errors in exposed workflow entrypoints.
2. Harmonize NGS registry names, root compatibility entrypoint, workflow files, modules, and tests.
3. Preserve runtime compatibility while replacing reserved `_` closure identifiers and deprecated constructs where necessary.
4. Move GPU scheduler configuration/reservation/cooldown state to a persistent configured state directory shared by the scheduling authority; do not bake mutable state into images.
5. Remove mutable GPU state from image build context and source tracking where appropriate.
6. Add conservative nonzero VRAM safety margins and model-specific reservations based on observed PPIFlow/ESMFold peaks.
7. Prevent unsafe parallel admission of known heavy models.
8. Bound retained Nextflow process logs and make cancellation/process-group cleanup deterministic.

### Likely files

- `ngs.nf`, `workflows/ngs/*.nf`, registry/config/tests
- `workflows/boltzgen_design.nf` if strict lint compatibility requires adjustment
- `platform/api/services/gpu_config.py`
- `platform/api/services/gpu_orchestrator.py`
- GPU router/adapter paths, Compose/environment state mounts, `.dockerignore`
- `platform/api/services/nextflow.py`
- scheduler, workflow, and lint regression tests

### Acceptance gate

- Whole-tree Nextflow lint reports zero errors for supported/exposed entrypoints.
- NGS registry and every advertised entrypoint resolve to an existing workflow.
- Scheduler state persists outside the code/image layer and is identical across control surfaces.
- Tests prove heavy-model admission respects safety margin and concurrency constraints.
- Nextflow log retention is bounded.

### Rollback

- Revert code/config commits and restore the backed-up scheduler policy file; do not discard persistent job data.

---

## Phase 5: Containment, Provenance, Test Isolation, and Release Hygiene

### Required behavior

1. Add explicit memory/task/PID boundaries to systemd and Compose with conservative values and documented overrides.
2. Health/readiness distinguishes process liveness, DB readiness, adapter readiness, workflow-launch permission, and frontend readiness.
3. Embed Git SHA/build identity into API health/version output, frontend build metadata, Electron diagnostics, and image labels.
4. Tests cannot invoke real user-systemd, Docker, or production state without an explicit opt-in integration marker/guard.
5. Generated build/test/runtime artifacts use the invoking user and writable isolated paths.
6. Fix stale source-inspection tests and make the full validation pipeline reproducible.
7. Deployment scripts rebuild images explicitly, render/install current units, and support rollback to the previously tagged images/revision.

### Likely files

- `compose.core-runtime.yml`
- `biomodstack_services.py`, unit rendering/templates, launch scripts
- API health/router and version metadata
- frontend Vite build metadata
- Electron diagnostics/package metadata
- test fixtures/conftest and deployment tests
- Dockerfiles/build scripts/documentation

### Acceptance gate

- Unit/Compose contract tests prove limits and revision metadata are present.
- Full API suite is green with no real service activation.
- Frontend tests/typecheck/lint/build and Electron tests are green.
- Full Nextflow lint and targeted previews are green.
- A post-suite check proves no BMS listeners, containers, or services were activated.
- Independent integration review approves the complete diff.

### Rollback

- Preserve prior image tags and unit files before deployment. Revert to previous Git revision/images if readiness or resource checks fail.

---

## Phase 6: Integration and GitHub Promotion Gate

1. Verify clean worktree, no uncaptured stashes/worktrees, correct author, and all phase commits present on `test`.
2. Fetch and merge `origin/main` into `test` non-destructively if needed; resolve and rerun integration gates.
3. Push `test`; verify `HEAD == origin/test == git ls-remote`.
4. Inspect current-HEAD GitHub checks. Fix current failures before promotion.
5. Push the same verified tree to `main` without force; verify `origin/main...origin/test` is `0 0`.
6. Record final commit, tree hash, checks, and image revision.

## Phase 7: Deployment Gate

1. Create a read-only database backup and record prior image IDs/unit state.
2. Rebuild API, web, host-agent/workflow components using repository scripts and no build cache where provenance requires it.
3. Install/render current systemd units and daemon-reload.
4. Bring up production core runtime and adapter through documented controls; verify containers, health, DB, adapter, launch guard, web, revision, resource limits, and logs.
5. Bring up dev API/frontend simultaneously on `8002`/`5173`; verify it does not collide with production `8000`/`18080`.
6. Run bounded smoke checks only—never use `/api/jobs` as health.
7. Stop and roll back on memory growth, task/FD growth, readiness disagreement, repeated orchestration errors, or revision mismatch.
8. Leave both runtimes in the explicitly requested operational state and report exact service/container/revision truth.
