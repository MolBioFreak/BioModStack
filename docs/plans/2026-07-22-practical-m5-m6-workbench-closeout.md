# Practical M5/M6 Structure Workbench Closeout Plan

**Date:** 2026-07-22
**Status:** execution plan
**Target branch:** `test`
**Published implementation baseline:** `061b99e12aac275d950d47118e1172e54106ed49`


## 1. Outcome

Finish the useful BioModStack structure workbench without turning the closeout into a general-purpose visualization platform or a formal-verification project.

The shipped workbench must let an operator:

1. open a real job-owned structure in the existing direct Mol* viewer;
2. save and reopen the useful view state;
3. export PNG, snapshot JSON, visible tabular data, and selected mmCIF with a small provenance manifest;
4. load one supplied CCP4/MRC scalar volume and manipulate its contour, slice, color, and opacity;
5. show/hide one supplied segmentation and apply one supplied registration;
6. play one real GROMACS trajectory from an existing completed MD job;
7. export one short browser-owned WebM from that trajectory;
8. do all of the above without creating another Mol* owner or inventing scientific results in the browser.

This plan deliberately does **not** pursue every theoretical capability and edge case in the frozen M5/M6 specification.

## 2. Non-negotiable boundaries

These remain required because violating them would produce incorrect science, data leakage, or an unstable viewer:

- Keep one BMS-owned direct Mol* 4.5 owner per workbench.
- Keep `StructureSceneController` authoritative for serializable shared state.
- Bind durable resources to job-owned IDs and SHA-256 hashes.
- Never persist signed URLs, credentials, filesystem paths, plugin references, buffers, or WebGL state.
- Do not calculate alignment, segmentation, fitting, electrostatics, or volume correlation in the browser.
- Do not treat independent structures as trajectory frames.
- Keep missing scientific values missing.
- Keep morphs labeled `visual_interpolation_not_physical_trajectory` if morph export is enabled later.
- A refused or failed restore/load must leave the current useful scene intact.
- Unsupported functionality must remain visibly disabled rather than silently falling back.

## 3. Explicitly deferred scope

The following do not block this closeout:

- 100,000-member collection manifests and opaque cursor infrastructure;
- full ensemble ranking/filtering infrastructure;
- two synchronized Mol* canvases and linked cameras;
- more than one simultaneously active scalar volume;
- PDB+DCD trajectory support;
- simultaneous multi-replica playback;
- browser-generated structure correspondence or alignment;
- segmentation authoring or automated fitting;
- PCA/tICA, clustering, kinetics, and free-energy visualization infrastructure;
- MP4/H.264 movie export;
- deterministic movie equivalence across browser engines;
- exhaustive malformed-file and codec matrices;
- exhaustive optional snapshot-binding recovery flows;
- broad M0-M4 regression audits;
- Conformational Mapping Phase 13;
- unrelated lint and test cleanup.

Simple job/design selection already present in Quick Viewer is sufficient for this closeout. A larger collection browser can be authorized later if a real workflow requires it.

## 4. Current baseline

Commit `061b99e12aac275d950d47118e1172e54106ed49` already provides:

- snapshot-v2 contracts and immutable backend records;
- RFC 8785 canonicalization and SHA-256 validation;
- authenticated snapshot and viewer-resource routes;
- PNG, JSON, CSV, mmCIF, and WebM export foundations;
- CCP4/MRC volume parsing and direct Mol* rendering;
- isosurface and orthogonal-slice controls;
- supplied segmentation metadata and rendering;
- supplied registration application;
- browser WebM/VP9 recording foundation;
- one shared viewer/controller/adapter owner;
- focused frontend/backend tests and a passing production build;
- a live development frontend at `http://127.0.0.1:5173/` serving the baseline revision.

Known gaps that matter to this practical closeout:

- the managed API image has not been rebuilt with the M6 routes;
- no explicit primary-database migration creates `viewer_snapshots` on an existing installation;
- snapshot persistence has not been proven across a managed API restart;
- no accepted live volume/segmentation/registration fixture exists;
- no workbench frame stepper is connected to a real completed MD trajectory;
- no real WebM has been produced and checked with `ffprobe`;
- the export buttons have focused coverage but not one complete live acceptance pass.

## 5. Delivery rules

- Work from the canonical clean `test` checkout.
- Fetch and rebase normally if `origin/test` advances; never force-push.
- Preserve unrelated shared-branch work.
- Use one writer and avoid worktree sprawl.
- Keep each phase independently usable and publishable.
- Do not add generalized abstractions before a real fixture or workflow requires them.
- Add only focused tests that prove the phase's operator-visible result or a critical scientific/safety boundary.
- Every phase ends with a coherent commit and exact local/remote ref verification.

## 6. Phase 1 — Put snapshot persistence on the managed API

### Objective

Make the already-implemented viewer routes and snapshot model operational on the actual managed API.

### Code changes

1. Add `platform/api/migrations/add_viewer_snapshots.py`.
2. Add migration version 11 to `platform/api/migrations/runner.py`.
3. The migration must create `viewer_snapshots` with the model's current columns and indexes:
   - `id` primary key;
   - `job_id` foreign key/index;
   - `label`;
   - `created_by`;
   - `schema_version`;
   - `snapshot_sha256` index;
   - `snapshot_json`;
   - `created_at`.
4. Make the migration idempotent for a clean installation and an installation where ORM startup already created the table.
5. Add one migration test that starts with the pre-M6 schema, runs the migration twice, and verifies the resulting table and indexes.
6. Retain the existing router and service implementation unless a real managed-runtime failure identifies a defect.

### Verification

Run:

```bash
cd platform/api
uv sync --group dev --frozen
uv run --frozen --group dev python -m pytest \
  -o addopts='' -q \
  tests/test_viewer_resources.py \
  tests/test_viewer_snapshot_migration.py
uv run --frozen python -m compileall -q \
  database.py main.py migrations/add_viewer_snapshots.py \
  migrations/runner.py routers/viewer_resources.py \
  services/viewer_resource_contracts.py services/viewer_resources.py
```

### Managed deployment

Use the repository-owned deployment proof path:

```bash
python3 scripts/bms_api_image_proof.py preflight --pretty
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml \
  up -d --no-deps --force-recreate bms-api
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml ps bms-api
```

Then verify:

- `/api/health` succeeds;
- the API container/image carries the accepted source revision;
- snapshot list/create/get/delete routes no longer return 404;
- existing jobs and unrelated API routes remain available;
- API logs contain no migration or import errors.

### Live acceptance

On a disposable real job:

1. open a structure;
2. save a snapshot;
3. fetch it back from the API;
4. restart only the managed API container;
5. reopen the snapshot;
6. verify the same structure, camera, representations, selection, and measurements;
7. deliberately alter one required hash in a test request and verify refusal without scene replacement.

### Done when

One real snapshot survives a managed API restart and a bad required hash cannot replace the current scene.

### Commit

```text
feat(viewer): deploy immutable workbench snapshots
```

## 7. Phase 2 — Close the useful export paths

### Objective

Prove the exports operators will actually use. Do not build a generalized export service.

### Code review and targeted fixes

Inspect and adjust only as needed:

- `platform/frontend/src/structureViewer/extensions/m6/M6WorkbenchPanel.tsx`
- `platform/frontend/src/structureViewer/contracts/m6Reproducibility.ts`
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`
- `platform/frontend/src/structureViewer/StructureViewerHost.tsx`

Required behavior:

- Snapshot JSON contains the current BMS scene and durable bindings.
- PNG is captured from the existing Mol* canvas.
- CSV/JSON export the rows actually supplied by the active workbench panel.
- Selected mmCIF exports the exact active selection/components rather than silently broadening to every loaded structure.
- Every output downloads with one small manifest containing:
  - job ID;
  - snapshot ID;
  - export kind;
  - source artifact IDs and hashes;
  - engine/adapter version;
  - output filename, MIME type, byte length, and SHA-256;
  - morph warning when applicable.
- Object URLs are released after download.

### Focused tests

Keep one frontend export test file. Cover only:

- deterministic snapshot JSON hash;
- CSV missing values remain empty, not zero;
- selected mmCIF does not broaden an exact selection;
- manifest output hash matches downloaded bytes;
- paths, signed URLs, and authorization fields are absent.

### Live acceptance

From one real structure scene, download and inspect:

1. snapshot JSON and manifest;
2. PNG and manifest;
3. populated CSV and JSON tables and manifests;
4. selected mmCIF and manifest.

Recompute each output hash once. Open the PNG and mmCIF. Confirm that CSV/JSON contain the current useful table rows.

### Done when

All four export classes download, open, and can be tied back to the exact source job and artifact hashes.

### Commit

```text
feat(viewer): finish governed workbench exports
```

## 8. Phase 3 — Add one accepted volume/segmentation fixture lane

### Objective

Prove the existing volume, segmentation, and registration implementation with one compact real runtime path.

### Fixture package

Add a small, redistributable package under:

```text
platform/api/tests/fixtures/viewer_workbench/
```

It contains:

- one valid single-channel CCP4/MRC scalar map;
- one small integer-label CCP4/MRC segmentation map on the same grid;
- one structure used for visual registration proof;
- one `viewer/volumes.json` manifest;
- one supplied registration transform;
- a README recording source, creation method, hashes, dimensions, units, and expected visible result.

The fixture may be a deliberately small deterministic scientific fixture. It does not need to cover every volume format or axis convention.

### Narrow producer helper

Add a small backend helper that writes the governed `viewer/volumes.json` manifest for a job output directory from already-produced local artifacts. It must:

- copy or reference only files contained by the job output;
- calculate SHA-256 and byte length;
- record dimensions, axis order, transform, units, and provenance supplied by the producer;
- never infer registration from the structure;
- never expose the host path in API output.

This helper is for workflow adapters and fixture materialization. Do not add a public arbitrary-path upload API in this tranche.

Suggested path:

```text
platform/api/services/viewer_volume_manifest.py
```

### UI/runtime work

Use the existing M6 panel and controller. Make only operator-facing corrections found during fixture use:

- allow one active scalar or segmentation volume at a time;
- load and remove;
- absolute contour;
- X/Y/Z slice;
- color and opacity;
- show/hide supplied segment IDs;
- apply the supplied registration.

Do not add hierarchy editing, fitting, correlation, multi-volume compositing, or oblique slicing.

### Focused tests

Add only:

- manifest helper output and hash verification;
- valid scalar fixture accepted;
- bad artifact hash refused;
- supplied registration applied without browser calculation;
- segmentation IDs show/hide correctly;
- replace/remove clears the prior Mol* volume object.

### Live acceptance

Create one disposable fixture job and verify:

1. structure opens;
2. scalar map opens in the same Mol* owner;
3. contour, slice, color, and opacity update;
4. scalar map can be removed and loaded again;
5. segmentation opens in the supplied registered position;
6. two segment IDs can be toggled;
7. snapshot save/reopen restores the active volume presentation;
8. no fresh browser-console errors appear.

### Done when

A real job-owned fixture proves scalar volume, segmentation, registration, snapshot round-trip, and replacement in the live workbench.

### Commit

```text
feat(viewer): prove supplied volume and segmentation lane
```

## 9. Phase 4 — Connect one real GRO+XTC trajectory

### Objective

Play one existing completed GROMACS trajectory in the current Mol* viewer. Do not build a general trajectory platform.

### Supported lane

Only:

- topology: GRO;
- coordinates: XTC;
- artifacts obtained from the existing authenticated MD routes;
- one active replica at a time.

### Backend work

Reuse:

- `GET /api/jobs/{job_id}/md/summary`
- `GET /api/jobs/{job_id}/md/artifacts`
- `GET /api/jobs/{job_id}/md/artifacts/{artifact_id}/content`

Add only the metadata needed by the frontend if it is not already present:

- topology artifact ID/hash;
- trajectory artifact ID/hash;
- atom count/order identity;
- frame count;
- replica;
- source frame and supplied time/step mapping.

Do not duplicate trajectory bytes under viewer-resource routes.

### Frontend work

Add:

```text
platform/frontend/src/structureViewer/extensions/trajectory/TrajectoryControls.tsx
platform/frontend/src/structureViewer/runtime/MdTrajectoryFrameStepper.ts
```

Extend the existing controller/adapter only where required for:

- load;
- frame select;
- play/pause;
- loop;
- unload;
- changing jobs/replicas;
- cancellation.

Controls:

- frame slider;
- play/pause;
- loop checkbox;
- source frame/time display;
- replica selector only when multiple replicas really exist.

No unrelated structure list may become a trajectory.

### Focused tests

Use one completed MD fixture or preserved disposable job. Cover:

- correct GRO/XTC artifact pairing;
- hash and atom-order mismatch refusal;
- source frame/time displayed correctly;
- moving the slider changes the displayed frame;
- play/pause/loop;
- changing job or replica unloads the old trajectory;
- a rapid frame change ends at the latest requested frame.

Do not build an exhaustive malformed-XTC suite.

### Live acceptance

Use one real completed GROMACS job:

1. open trajectory;
2. seek beginning/middle/end;
3. play and pause;
4. loop once;
5. switch away and back;
6. verify no duplicate Mol* canvas and no console errors.

### Done when

One real completed GRO+XTC job plays correctly through the existing viewer and exposes an authoritative frame stepper to movie export.

### Commit

```text
feat(viewer): play authoritative gromacs trajectories
```

## 10. Phase 5 — Produce one accepted WebM

### Objective

Use the existing browser-owned encoder to produce a practical trajectory movie.

### Scope

- WebM/VP9 only;
- existing Mol* canvas only;
- 30 fps default;
- maximum 1920×1080;
- maximum 60 seconds;
- trajectory source from Phase 4;
- no MP4;
- no cross-browser determinism claim.

### Work

1. Pass `MdTrajectoryFrameStepper` into `M6WorkbenchPanel.movieFrameStepper`.
2. Enable `VITE_BMS_WEBM_VP9_CAPABILITY_PROVEN=true` only in the deployment where the real export passes.
3. Preserve:
   - source artifact bindings;
   - source frame range;
   - frame count;
   - fps and codec;
   - output hash;
   - manifest hash.
4. Keep cancellation and recorder/track/blob cleanup.
5. Keep morph export disabled unless a real exact-mapped morph source is separately available. Morph does not block trajectory WebM closeout.

### Focused tests

Cover only:

- unsupported codec/deployment stays disabled;
- request over active limits refuses before recording;
- cancellation returns no completed output;
- successful output contains expected frame count/range and hashes.

### Live acceptance

1. Export a 5-10 second movie from the real Phase 4 trajectory.
2. Cancel one export midway and confirm no completed file is reported.
3. Export again successfully.
4. Run:

```bash
ffprobe -v error -show_format -show_streams exported.webm
```

5. Confirm VP9, expected dimensions, plausible duration, and a playable file.
6. Recompute the output SHA-256 and compare it with the manifest.

### Done when

One real trajectory WebM plays, passes `ffprobe`, has matching source/output provenance, and cancellation leaves no false completed result.

### Commit

```text
feat(viewer): export trajectory webm from molstar
```

## 11. Phase 6 — Final integrated acceptance and release

### Objective

Prove the practical operator workflow on the exact candidate and publish it.

### Final automated gates

```bash
cd platform/frontend
pnpm install --frozen-lockfile
pnpm exec tsc -p tsconfig.app.json --noEmit --pretty false
pnpm exec tsx --test \
  tests/structureViewerM6Contracts.test.ts \
  tests/structureViewerSemantics.test.ts \
  tests/structureViewerSnapshotExport.test.ts \
  tests/structureViewerVolumes.test.ts \
  tests/structureViewerTrajectory.test.ts
pnpm run build

cd ../api
uv sync --group dev --frozen
uv run --frozen --group dev python -m pytest \
  -o addopts='' -q \
  tests/test_viewer_resources.py \
  tests/test_viewer_snapshot_migration.py \
  tests/test_viewer_volume_manifest.py
```

If the exact test filenames differ during implementation, keep the same narrow coverage rather than creating a large new suite.

### Final live checklist

On the managed API and canonical frontend revision:

- [ ] open one real structure;
- [ ] exactly one Mol* canvas/plugin owner exists;
- [ ] native Mol* controls are present;
- [ ] save and restore one snapshot across API restart;
- [ ] PNG exports and opens;
- [ ] CSV/JSON contain real current rows;
- [ ] selected mmCIF contains only the selected structure/components;
- [ ] scalar CCP4/MRC loads and its contour/slice can be changed;
- [ ] segmentation IDs show/hide in the supplied registered position;
- [ ] volume state survives snapshot restore;
- [ ] one GRO+XTC trajectory seeks, plays, pauses, and loops;
- [ ] one trajectory WebM passes `ffprobe` and hash verification;
- [ ] switching jobs removes prior trajectory/volume state;
- [ ] no fresh browser-console errors;
- [ ] API and frontend report the accepted commit revision.

### Final Git procedure

Before staging each commit and before final release:

```bash
unset GIT_INDEX_FILE
git status --short --untracked-files=all
git diff --name-status
git diff --stat
git diff --cached --name-only
git diff --check
```

Exclude:

- `platform/mobile-cordova/www/`;
- `platform/api/.venv/`;
- generated exports and fixture-job output;
- unrelated BioXP/external-import/shared-branch changes.

Push normally to `test`, then verify:

```bash
git fetch origin test
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/test)"
git show --check --oneline HEAD
```

Rebuild/recreate only services changed by the accepted commit and verify their exact runtime revision.

### Done when

Every final live-checklist item passes on the published remote `test` revision. This is the practical M5/M6 workbench DONE point.

## 12. Commit and dependency sequence

| Order | Tranche | Depends on | Blocking result |
|---:|---|---|---|
| 1 | Managed snapshot migration/API deployment | current M6 baseline | snapshots survive restart |
| 2 | Useful static exports | tranche 1 for snapshot context | PNG/JSON/CSV/mmCIF accepted |
| 3 | Scalar volume + segmentation fixture lane | tranche 1 | real governed spatial data accepted |
| 4 | GRO+XTC trajectory | existing MD artifact routes | real authoritative frame stepper |
| 5 | WebM export | tranche 4 | one ffprobe-accepted movie |
| 6 | Integrated acceptance/release | tranches 1-5 | published practical DONE revision |

The phases are sequential where dependencies exist. Static exports and fixture preparation may proceed in parallel after Phase 1, but only one writer integrates to `test`.

## 13. Stop conditions

Stop and perform root-cause analysis before further edits if any of these occur:

- an operation creates a second Mol* plugin owner;
- a snapshot contains a path, signed URL, token, or runtime object;
- a failed restore clears or mutates the previous useful scene;
- a browser path calculates scientific alignment, registration, segmentation, electrostatics, or correlation;
- an independent structure list is converted into trajectory frames;
- selected-mmCIF export broadens identity;
- a migration risks deleting or rewriting existing job/scientific records;
- the managed runtime revision cannot be tied to the published commit;
- a high-severity review finding remains unresolved.

Do not stop the release for unrelated lint, broad legacy test failures, hypothetical formats, or deferred capabilities listed in section 3.

## 14. Final deliverables

The closeout produces:

1. an idempotent viewer-snapshot migration;
2. managed API routes deployed and revision-sealed;
3. persistent snapshot save/reopen;
4. accepted PNG, JSON, CSV, and selected-mmCIF exports;
5. a compact governed scalar/segmentation/registration fixture package;
6. one job-output manifest producer helper;
7. one real GRO+XTC trajectory player;
8. one accepted WebM/VP9 trajectory export;
9. focused tests and production build evidence;
10. a live acceptance record tied to the exact remote `test` commit.

Anything outside these ten deliverables is a separate future authorization, not a blocker for practical closeout.
