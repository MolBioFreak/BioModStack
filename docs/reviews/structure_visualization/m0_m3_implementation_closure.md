# Structure Visualization M0–M3 Current-Tree Reassessment

**Date:** 2026-07-19
**Scope:** direct Mol* integration/refactor and current-tree execution evidence

## Decision

The previous source-only document claimed M0–M3 were each 100% complete. That claim is withdrawn. Source presence is not milestone acceptance.

| Milestone | Honest status | Decision |
|---|---|---|
| M0 — runtime contract and measured browser baseline | MVP path verified; formal current-tree 50-cycle plateau and independent review missing | **MVP GO / milestone OPEN** |
| M1 — contracts, controller, owner, direct adapter | Core architecture runs and focused generation/teardown tests pass; full roadmap acceptance matrix is not re-executed | **Substantially implemented / acceptance OPEN** |
| M2 — migrate existing consumers | 15-site browser inventory passes; 12 route-reachable sites and 3 retained orphan fixtures are explicit | **Focused migration GO / formal gate OPEN** |
| M3 — metrics, filters, linked views, measurements | Residue pLDDT plus persisted structure scalars and sequence linkage work; pair-matrix/measurement acceptance remains artifact- and workflow-incomplete | **PARTIAL — not milestone complete** |

## What is now genuinely working

- direct owned `molstar@4.5.0` runtime;
- shared host/workbench boundary for generic and epitope consumers;
- StrictMode-safe raw-structure Blob ownership;
- localized viewer error boundary instead of whole-page blanking;
- real Results Viewer structure load and visible molecular render;
- native Mol* toolbar/settings/control panels;
- pLDDT coloring and linked sequence selection;
- one shared metric workbench exposing every available metric layer rather than a pLDDT-only selector;
- on the verified DRT4 route: pLDDT, pTM, complex ipLDDT, complex ipDE, radius of gyration, residue count, helix %, sheet %, and coil %;
- structure-scalar values display their persisted value and provenance without fake residue coloring or opacity controls;
- the shared workbench owns minimize/restore in normal and fullscreen modes;
- sequence-only fullscreen state with the workbench minimized while pLDDT is active;
- legacy top metric/quick-view/legend controls and the duplicate analytics sidebar/fullscreen panel are disabled;
- removal of legacy result-table sort/contact/distance/H3 controls from the Structure surface;
- removal of global table pagination chrome from the Structure surface;
- deterministic actual-route teardown;
- 15/15 consumer browser smoke with zero errors/warnings;
- isolated production build and 54 viewer-focused tests passing.

The verified DRT4 producer declared no aligned-error artifact and its persisted contact-map analysis is missing. PAE, contact distance, and ipSAE are therefore not invented as selectable layers for that result.

## Known whole-tree gates

The most recent canonical frontend run was 416/417 with one unrelated concurrent BioXP failure: `bioxpInterlinkStatus.test.ts` imports `isBioXpCommandAvailable`, which the concurrently edited module does not export. This viewer work did not modify that BioXP lane.

Canonical lint also remains red outside the viewer scope with five errors in `InfraLiveTelemetry.tsx`, `ModelDocumentationLinks.tsx`, `QualitySettingsPanel.tsx`, `StructurePredictionTemplate.tsx`, and `dashboard/reorchestrateStructureSettings.ts`. No Mol* or structure-viewer file produced a lint error.

A formal M2 whole-frontend GO requires both canonical gates to return green after their owners resolve those lanes.

## Why M3 is not complete

The roadmap acceptance is broader than the current MVP. Remaining evidence/work includes:

- prove entity-type, repeated-instance, neighborhood, and missingness filters change authoritative 3D presentation rather than only UI state;
- exercise real PAE/contact pair matrices and exact matrix → sequence → 3D → matrix round trips on route data;
- expose and live-test exact-atom distance/angle/dihedral measurement UX;
- verify metric provenance, units, direction, missingness, and tooltips for every admitted production metric;
- test insertion codes, auth/label namespace differences, repeated operators, multiple models, altlocs, and non-protein components in browser flows;
- prove keyboard, touch, and screen-reader behavior on essential controls;
- execute admission/performance tests for large annotations and matrices.

## Ordered next steps

1. **Close M0/M1 evidence, not features.** Automate the current browser smoke as a package command, run at least 50 StrictMode replacement cycles on the exact tree, record plugins/canvases/listeners/Blob URLs/detached nodes/memory, run A→B→A and overlay reorder/removal, then obtain independent review.
2. **Close M2 inventory and whole-app gates.** Decide whether G1–G3 orphan fixtures remain compatibility fixtures or are retired, run lint, clear the unrelated BioXP test failure with its owner, then rerun canonical tests/build/browser route acceptance.
3. **Finish M3 linked-data correctness.** Start with one real pLDDT route and one real PAE route; make filter semantics authoritative, prove bidirectional exact selection, then wire measurement controls. Do not add new visual dimensions before these gates pass.
4. **Consolidate remaining legacy analytics.** Move any still-useful `StructureViewerPane` analytics into shared workbench extensions and delete duplicate presentation paths; keep workflow-specific configuration declarative.
5. **Stop before M4.** Complex/interface/interaction work does not begin until M0–M3 acceptance evidence is complete.

## Release boundary

No commit, push, deployment, service restart, or package upgrade is included in this reassessment.
