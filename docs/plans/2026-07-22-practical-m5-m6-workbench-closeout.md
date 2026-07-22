# Practical Structure Workbench Closeout

**Goal:** Make the existing direct Mol* workbench useful and trustworthy for the small BioModStack team.

**Approach:** Keep one BMS-owned Mol* canvas and one controller. Persist only the information needed to reopen a view. Use real supplied structure, map, label, and MD artifacts. Do not build enterprise-scale tenancy, a compute cluster, or browser-side scientific analysis.

## Product boundary

This is a small-team local deployment behind the existing trusted local proxy. It needs a narrow safety boundary, not enterprise identity machinery:

- The local proxy identifies the trusted local team boundary; ordinary browser code never receives its proxy secret.
- Saved state records artifact IDs and SHA-256 hashes, never local paths or signed URLs.
- A restore refuses changed or missing source data rather than silently loading something else.
- The browser renders supplied data. It does not invent density, segmentation, registration, RMSD, PCA, clustering, or dynamics.
- One direct Mol* plugin remains the only viewer owner.

That is enough to keep a small-team workflow reproducible and scientifically honest.

## Already working

Published `test` revision: `64002396c5961c34dfa4166253867793ec949f91`.

- Snapshot persistence is deployed. A real 1UBQ view was saved, the API was restarted, and the snapshot restored.
- PNG, selected mmCIF, snapshot JSON, and export-manifest downloads work on a real structure.
- CSV/JSON table export is available when a real visible metric table is present; it stays disabled when there are no rows rather than exporting fake data.
- A real EMDB EMD-5778 CCP4 density map has been loaded through the governed artifact route, hash-checked, rendered, and snapshotted.
- The managed API and the Vite development proxy run the published revision.

## Remaining work

### 1. Dedicated structure/map fixture

The current density proof uses a real map and makes no false registration claim. Replace the temporary proof fixture with one dedicated completed fixture job that contains a matching structure and map.

Files:
- `platform/api/services/viewer_resources.py`
- `platform/api/tests/test_viewer_resources.py`
- a small fixture materializer under `scripts/` or `platform/api/scripts/`

Acceptance:
1. Fixture job owns its own `viewer/volumes.json`, structure, and map.
2. Opening that job shows one structure and one density map.
3. Contour, slice, opacity, and remove/reload work.
4. A snapshot restores the same map presentation after API restart.

### 2. Supplied segmentation and supplied registration

Only add this after obtaining a matching label map and a known supplied transform. Do not create either in the browser.

Files already provide the seam:
- `platform/frontend/src/structureViewer/contracts/spatialVolumes.ts`
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`
- `platform/api/services/viewer_resources.py`

Acceptance:
1. Load one integer label map with two or three meaningful labels.
2. Toggle a label and verify its supplied color/name.
3. Apply one supplied transform from the fixture manifest.
4. Snapshot and restore the result.

If no matching supplied label/transform exists, leave those controls unavailable. Do not manufacture a scientific relationship merely to demonstrate UI.

### 3. GRO + XTC playback

Do this only when one BMS MD result actually has a topology, XTC, exact atom-order identity, and a frame/time map.

Use the Mol* 4.5 substrate already installed:
- GRO topology to model
- XTC coordinates
- `TrajectoryFromModelAndCoordinates`
- one active replica and source-frame selection

Files:
- `platform/api/routers/md_results.py`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/structureViewer/contracts/mdTrajectory.ts`
- `platform/frontend/src/structureViewer/runtime/MolstarEngineAdapter.ts`
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`

Acceptance:
1. Select one real replica.
2. Seek known frame 0, middle, and final frame.
3. Play and pause without replacing the Mol* owner.
4. Show source frame and time from the supplied frame map.

No multi-replica dashboard, inferred frame timing, or independent-model playback is needed.

### 4. WebM after playback works

Use the existing canvas and current bounded encoder only after the real trajectory lane exists.

Acceptance:
1. Capture a short 5-10 second trajectory clip.
2. Cancel one capture without leaving a recorder or track running.
3. Check the output with `ffprobe`.
4. Keep morph output visually labeled; never call it physical dynamics.

## Everyday acceptance checklist

Before calling a viewer change done, use one real job and confirm:

1. Structure opens in one Mol* canvas with native controls visible.
2. Console has no new errors.
3. Save and restore works across API restart when changed.
4. PNG and selected mmCIF download.
5. The real map loads, changes display, removes, and restores from snapshot.
6. Focused frontend/backend checks and one production build pass.

That is the complete standard. Do not add load testing, tenant abstractions, broad capability matrices, generalized import workflows, or speculative edge-case suites unless a real user workflow breaks.
