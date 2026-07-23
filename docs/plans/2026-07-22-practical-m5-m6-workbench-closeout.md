# Structure Workbench Closeout

**Goal:** Finish the six practical workbench capabilities for BioModStack’s small professional team.

**Scope:** One direct BMS-owned Mol* lifecycle, real job-owned artifacts, focused tests, one live acceptance path. The work is not done until all six stages below pass.

## Cross-cutting rendering-default contract

Initial molecular representation is determined by serializable result/scene data, never by route, job ID, result label, or component-specific branching.

1. **General default:** every non-MD structure scene starts as Mol* `polymer-cartoon`.
2. **MD default:** an MD scene without an explicit rendering preference starts as Mol* `auto`, allowing the loaded trajectory/topology data to select the appropriate representation.
3. **Explicit result override:** a result contract may declare `renderingProfile` as exactly `auto`, `atomic-detail`, or `polymer-cartoon`; it takes precedence for every result class.
4. The profile is part of the authoritative, serializable `StructureSceneState`, validated at construction, preserved in snapshots, and passed once through the direct adapter.
5. User-selected manual representation changes remain available; they are not erased by the initial default.
6. Do not add page-specific, route-specific, workflow-specific, or job-specific representation exceptions.

Acceptance: contract tests must cover non-MD cartoon default, MD automatic default, an explicit override, and snapshot preservation; a mounted Mol* test must confirm the selected initial preset reaches the direct owner.

## Current audited state

Audit target: `test` at `a2eebd7b63a081cd92ef493778202bd561c78032`.

| Stage | Status | Reality |
|---|---|---|
| 1. Snapshot persistence | Partial | Save/restart/restore works, but the deployed database has a table created by SQLAlchemy rather than recorded migration versions 9–11. |
| 2. Useful exports | Partial | Snapshot JSON, PNG, selected mmCIF, CSV/JSON paths, and manifests exist. A populated CSV/JSON export has not yet been live-proven. |
| 3. Volumes and segmentation | Partial | One unrelated EMD-5778 scalar map was loaded live. No matching fixture job, label map, registration, or complete operator controls exist. |
| 4. GRO+XTC playback | Missing | A small test fixture exists, but no materialized MD job/frame map and the direct adapter deliberately refuses playback. |
| 5. Accepted WebM | Missing | The browser encoder exists but has no reachable authoritative frame stepper or VP9/ffprobe acceptance proof. |
| 6. Integrated release | Missing | It follows the preceding stages. |

## 1. Deploy snapshot persistence correctly

### Required change

The API currently starts through Uvicorn and `Base.metadata.create_all()`. It does not run the numbered migration runner. The live `schema_migrations` ledger stops at version 8 even though the `viewer_snapshots` table exists.

Files:
- `platform/api/migrations/runner.py`
- `platform/api/migrations/add_viewer_snapshots.py`
- `platform/api/tests/test_viewer_snapshot_migration.py`
- `docker/api.Dockerfile` or the managed API startup entrypoint
- `compose.core-runtime.yml` only if the entrypoint needs a compose change

### Acceptance

1. Fix `run_all(db_path=...)` so every migration receives the supplied database path.
2. Run migrations before Uvicorn against the mounted application database.
3. Verify `schema_migrations` records versions through 11.
4. Prove existing snapshots remain readable.
5. Save a snapshot, recreate the API, and restore it from the browser.

## 2. Finish useful exports

The implementation already derives export rows from real metric layers. Do not invent CSV content.

Files likely involved:
- `platform/frontend/src/structureViewer/StructureViewerHost.tsx`
- `platform/frontend/src/structureViewer/extensions/m6/M6WorkbenchPanel.tsx`
- `platform/frontend/tests/structureViewerM6Contracts.test.ts`

### Acceptance

1. Select one real result that exposes non-empty metric layers.
2. Download populated CSV and JSON plus their manifests.
3. Verify Snapshot JSON, PNG, selected mmCIF, CSV, and JSON output names, MIME types, and SHA-256 manifest fields.
4. Add one narrow integration/component test for the non-empty table path if it is not already covered.

## 3. Prove volumes, segmentation, and registration

The existing scalar map proof is not a complete fixture: its manifest explicitly says it is unregistered to the unrelated 1UBQ structure. It cannot satisfy this stage.

Files:
- `platform/api/services/viewer_resources.py`
- `platform/api/tests/test_viewer_resources.py`
- `platform/frontend/src/structureViewer/contracts/spatialVolumes.ts`
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`
- `platform/frontend/src/structureViewer/extensions/m6/M6WorkbenchPanel.tsx`
- one small fixture materializer under `platform/api/scripts/` or `scripts/`

### Required fixture

One completed job with:

- a matching structure and valid scalar CCP4/MRC map;
- one supplied integer-label map with two or three meaningful labels;
- one supplied 4×4 registration transform for that exact structure/map pair;
- exact artifact hashes, dimensions, axis order, transforms, units, and provenance in `viewer/volumes.json`.

No browser-generated labels, transform, density, or scientific relationship.

### Required UI completion

Expose only the controls asked for:

- contour value;
- X/Y/Z slice selection;
- color;
- opacity;
- show/hide;
- individual supplied-label show/hide;
- remove/reload.

### Acceptance

1. Load scalar map and exercise contour, slice, color, opacity, show/hide, remove, and reload.
2. Load labels, toggle at least two supplied labels, and show their supplied names/colors.
3. Apply the supplied registration.
4. Save, restart API, restore, and verify the same volume/label/presentation state.

## 4. Connect one real GRO+XTC trajectory

A bounded GRO+XTC fixture exists under `platform/api/tests/fixtures/bms_md_analysis/gromacs_1u19_format_smoke/`, but it has no browser playback frame map and is not materialized as a completed MD job.

Files:
- `platform/api/services/md/results.py`
- `platform/api/routers/md_results.py`
- `platform/api/schemas/` for the frame-map artifact contract
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/components/MDResultsPane.tsx`
- `platform/frontend/src/structureViewer/contracts/mdTrajectory.ts`
- `platform/frontend/src/structureViewer/contracts/sceneState.ts`
- `platform/frontend/src/structureViewer/runtime/MolstarEngineAdapter.ts`
- `platform/frontend/src/structureViewer/runtime/MolstarDirectSceneEngineAdapter.ts`
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`

### Required work

1. Materialize one completed MD fixture job with GRO, XTC, a representative structure, exact hashes, and one frame map: display frame → source frame/time/step.
2. Expose the frame map and artifacts through the existing job-owned MD route.
3. Build the Mol* GRO topology + XTC coordinates path in the existing direct adapter.
4. Replace the current explicit unsupported stubs with source-frame seek and play/pause/loop for one active replica.
5. Wire the MD results viewer controls to the controller.

### Acceptance

1. Seek frame 0, a middle frame, and the final frame.
2. Display the exact supplied source-frame and time values.
3. Play, pause, and loop one active replica.
4. Keep one direct Mol* owner. No multi-replica player.

## 5. Produce one accepted WebM

Files:
- `platform/frontend/src/structureViewer/runtime/browserMovieExport.ts`
- `platform/frontend/src/structureViewer/extensions/m6/M6WorkbenchPanel.tsx`
- `platform/frontend/src/structureViewer/StructureViewerHost.tsx`
- `platform/frontend/src/components/MDResultsPane.tsx`

### Required work

1. Build the WebM frame stepper from the real trajectory in stage 4.
2. Pass `jobId` and that stepper through the MD viewer to the M6 panel.
3. Run a real browser capture from the existing Mol* canvas.
4. Validate the downloaded 5–10 second VP9 WebM with `ffprobe` and SHA-256.
5. Exercise cancellation and verify the recorder/tracks are cleaned up.
6. Only then set `VITE_BMS_WEBM_VP9_CAPABILITY_PROVEN=true` for the accepted runtime.

## 6. Integrated acceptance and release

Run only the focused checks needed for these changes:

- snapshot/resource/migration backend tests;
- MD result/fixture tests;
- frontend TypeScript and focused structure/MD tests;
- isolated production frontend build;
- one browser checklist covering stages 1–5.

Then:

1. Inspect the exact diff and protect unrelated work.
2. Commit coherent tranches normally.
3. Push the accepted commit to `test` without rewriting history.
4. Rebuild/restart the API and frontend from that same commit.
5. Verify API and frontend diagnostics report that exact revision.

## Explicitly excluded

- giant collection pagination;
- linked dual-viewer comparisons;
- PDB+DCD playback;
- multi-replica playback;
- multi-volume compositing;
- browser scientific calculations;
- complex segmentation authoring;
- MP4;
- exhaustive malformed-file or codec matrices;
- broad M0–M4 audits;
- speculative lifecycle or edge-case suites;
- Conformational Mapping Phase 13.

## Definition of done

All six stages have real evidence. In particular, the accepted build can save/reopen a view, export meaningful artifacts, restore a matching density/label/registration scene, play a real GRO+XTC source, and produce one verified WebM from that source.
