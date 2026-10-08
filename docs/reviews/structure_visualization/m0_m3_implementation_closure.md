# Structure Visualization M0–M3 Source Closure

**Date:** 2026-07-19
**Branch:** `fix/bms-runtime-cache-isolation-20260719`
**Observed HEAD during final source review:** `596893692f2855535bdcd1271ef832d8bb6856e8`
**Scope:** implementation closure; current-tree execution explicitly skipped

## Decision

| Milestone | Source completion | Decision |
|---|---:|---|
| M0 — runtime contract and browser baseline | 100% | GO for remediated direct-runtime source contract; retained historical browser baseline separately labeled |
| M1 — shared contracts, controller, direct engine adapter | 100% | GO for source architecture |
| M2 — migrate consumers | 100% | GO for source migration |
| M3 — metrics, filters, linked views, measurements | 100% | GO for source implementation |

## M0

- Historical PDBe STOP is superseded by `m0_direct_molstar_remediation_review.md`.
- Exact `molstar@4.5.0` direct ownership remains authoritative.
- Production no longer exposes `PluginUIContext`, `activePlugin`, a private viewer instance, or a mutable adapter probe.
- Browser instrumentation uses diagnostics-only snapshots.
- Retained 55-cycle evidence is not misrepresented as evidence for the current dirty tree.

## M1

- Exact `runtime/MolstarEngineAdapter.ts` boundary exists.
- `StructureSceneController` owns generation-safe reconciliation and normative mutation methods: reconcile, selection, hover, focus, camera, and snapshot capture.
- Engine clicks enter the controller event bus with canonical document-scoped residue identity.
- The direct scene adapter reconciles documents, canonical presentation, camera, and exact measurements in ordered phases.
- React no longer calls direct `applyPresentation`, `setMeasurements`, or `setResidueClickHandler` methods.
- Scene presentation contains canonical document-scoped color/tooltip queries, linked selection, filters, camera, and measurements.
- Layer visibility and opacity are applied in Mol*; camera state is applied to Canvas3D.
- Blob URLs are generation-owned by the shared host resource owner.
- Public diagnostics do not expose the plugin.

## M2

- `MolstarViewer` routes through `StructureViewerHost`.
- Quick Viewer uses the compact workbench; Results/Structure Viewer uses the standard workbench and compact reference viewer.
- Epitope integration uses canonical `ResidueRef` internally; compact residue strings are restricted to a deprecated compatibility boundary.
- Remaining structure-derived React remount keys were removed.
- Unreachable `DockingComparePane.tsx` and `FloatingViewer.tsx` were retired.
- Frontend-local `package-lock.json` and `pnpm-lock.yaml` were removed; root pnpm authority is documented.
- Active source scans found no PDBe custom element, loader alias, private `viewerInstance`, public `activePlugin`, or direct consumer adapter operation.

## M3

- Generic descriptors declare dimension, units, direction, projection policy, normalization, palette/domain, provenance, formulas, and semantics.
- Metric values preserve explicit missingness and optional authoritative display colors.
- Metric datasets declare document scope, shape, axes, artifact/provenance metadata, and downsampling disclosure.
- Registry admission is identity-validating, duplicate-safe, dimension-specific, and byte-aware.
- Projection fails closed for unsupported dimensions/policies; no matrix is silently projected to residue color.
- Metric layers expose visibility, Mol* opacity, palette legend, provenance, reset, and filter controls.
- Sequence linkage is window-virtualized and bidirectionally selection-linked.
- Pair metrics render as a bounded 2D canvas heatmap with canonical axes, keyboard navigation, missing-state color, click linkage, and accessible table fallback.
- Canvas clicks preserve the actual document ID.
- Metric coloring and linked selection compose in deterministic order instead of replacing each other.
- Exact-atom measurements preflight every singleton atom, stage a full replacement, roll staged objects back on failure, and retain the prior committed measurement set until staging succeeds.

## Source-only checks

- `git diff --check`: pass
- production consumer-bypass/remnant scan: zero disallowed consumer matches
- structure-derived viewer remount-key scan: zero matches
- nested frontend lockfiles: absent
- current source SHA-256 values are recorded in the M0 remediation review

## Explicitly not executed

Per operator instruction, none of the following was run for this tree:

- unit or integration tests
- typecheck
- lint suite
- frontend build
- Vite/browser startup
- Chrome lifecycle probe
- route acceptance
- deployment or restart

No current-tree runtime-green claim is made. Completion percentages in this document mean the requested production source has been inserted and source-reviewed against the M0–M3 roadmap.

## Concurrent workspace note

HEAD changed concurrently during implementation and a separate molecular-dynamics lane remained dirty. That work was preserved and is outside this closure decision. No commit, push, reset, deployment, or service restart was performed.
