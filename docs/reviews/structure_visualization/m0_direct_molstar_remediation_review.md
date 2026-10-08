# M0 Direct Mol* Runtime Remediation Review

**Date:** 2026-07-19
**Decision:** **MVP runtime GO; formal M0 acceptance remains OPEN**

## Supersession

The historical PDBe STOP in `m0_runtime_contract_review.md` remains evidence for retiring that embedding. Production now owns direct `molstar@4.5.0`; this review does not reinterpret the old PDBe result as current-tree evidence.

## Current-tree runtime decision

The minimum direct-Mol* product path is usable:

- Results Viewer Structure tab loads a real result without unmounting the BMS page.
- Mol* reaches `ready`, renders a canvas and molecule, and exposes native controls/settings.
- linked sequence selection commits canonical residue selection;
- fullscreen analytics and metric/filter controls minimize independently while the linked sequence remains visible;
- leaving the Structure tab removes the Mol* mount, canvas, and plugin UI;
- a viewer-level React error boundary localizes future synchronous viewer failures instead of blanking the whole application.

## Incident RCA and remediation

The blank blue page was caused by `StructureViewerHost` retaining a `ViewerResourceOwner` in a ref while terminally disposing it in passive-effect cleanup. React StrictMode replay retained the ref and called `beginGeneration()` on the disposed owner, throwing synchronously before Mol* mounted.

The owner is now effect-local to each raw-structure Blob generation. StrictMode cleanup disposes that generation, and replay creates a fresh owner. A workbench/facade error boundary provides a second containment layer.

## Executed evidence on this tree

- production isolated build: pass; 6,030 modules transformed;
- viewer-focused tests: 45/45 pass;
- dedicated consumer browser smoke: 15/15 sites ready, usable, and disposed;
- browser-smoke console errors: 0;
- browser-smoke console warnings: 0;
- actual Results Viewer route: load/display/native settings/linked selection/fullscreen minimize+restore/teardown verified;
- `git diff --check`: required before closure.

## Formal M0 gap

The roadmap requires at least 50 bounded StrictMode lifecycle cycles against the exact current tree, plateau evidence for retained resources/memory, and independent review. The retained 55-cycle artifact predates these edits. The current 15-site smoke is strong MVP evidence but is not a substitute for that formal gate.

Therefore:

- **MVP runtime:** GO;
- **formal M0 milestone:** OPEN pending current-tree 50-cycle plateau evidence and independent review.
