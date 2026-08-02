# BioModStack Structure Visualization Platform Roadmap

**Date:** 2026-07-18
**Status:** Draft plan; no source implementation, package upgrade, deployment, or Conformational Mapping Phase 13 authorization is implied.
**Contract:** `docs/specs/structure_visualization/structure_visualization_contract_v1.md`
**Repository baseline:** branch `test`, HEAD `188d69c2e266f82d14d4ac0778dc17785c2dfead` at plan creation.

## 1. Outcome

Build one BioModStack structure-visualization platform that every workflow composes instead of creating another Mol*/PDBe wrapper. The platform must support a large and growing set of AI/ML metrics, linked filtering, complexes and interfaces, conformational ensembles, coordinate trajectories, comparisons, measurements, interactions, volumes, and exports while preserving scientific identity and provenance.

The first objective is **harmonization and runtime correctness**, not feature quantity. New science-facing features begin only after the shared host/controller owns every existing viewer lifecycle.

## 2. Recovered baseline

The current implementation has useful pieces but no complete platform boundary:

- `platform/frontend/src/lib/molstar-loader.ts` centralizes package loading and custom-element registration.
- `platform/frontend/src/components/MolstarViewer.tsx` owns most generic loading, overlay, touch, coloring, and selection behavior.
- `platform/frontend/src/components/EpitopeMolstarViewer.tsx` independently duplicates loading, blob, event, readiness, and selection behavior.
- Fifteen direct wrapper render sites (12 generic and 3 epitope) use the generic or epitope wrappers across result review, quick/floating viewers, structure prediction, docking, mutagenesis, antibody, local redesign, and oligo workflows.
- Parent components sometimes force runtime recreation with React keys when design, display mode, or overlays change.
- Production Vite deliberately resolves `pdbe-molstar-stable` 3.3.0 after a newer-runtime Chromium renderer crash.
- Current dirty work includes partial lifecycle and residue-metric hardening, but does not migrate the epitope wrapper or prove real StrictMode/WebGL teardown.
- The metric adapter carries better residue semantics, but the pinned 3.3 query path cannot faithfully consume every emitted identity field.
- The current frontend test script compiles TypeScript tests and uses `node --test`; no browser automation dependency is installed.

Relevant pre-existing dirty paths at plan creation include:

- `platform/frontend/src/components/MolstarViewer.tsx`;
- `platform/frontend/src/components/QuickViewer.tsx`;
- `platform/frontend/src/components/StructureViewerPane.tsx`;
- `platform/frontend/src/components/molstarLifecycle.ts`;
- `platform/frontend/src/lib/molstar-metrics.ts`;
- `platform/frontend/tests/molstarMetrics.test.ts`.

These paths must be preserved and reconciled deliberately. This plan does not claim ownership of unrelated dirty files.

## 3. Capability-parity target

The target is not to clone another viewer. It is to combine proven interaction patterns behind BMS contracts.

| Capability | Reference pattern | BMS baseline | Priority |
|---|---|---|---|
| Representations and typed component visibility | Mol*, NGL | Engine available; workflow controls inconsistent | P1 |
| Rich selections, named sets, boolean/range/neighborhood filters | Mol*, NGL, VMD, iCn3D | Fragmented; legacy concatenated epitope keys remain | P1 |
| Linked sequence/annotation/3D selection | Mol*, iCn3D | Engine UI exists; no BMS-wide contract | P1 |
| Metric catalog, legends, thresholds, missingness, provenance | BMS requirement | Dirty residue-scalar start only | P1 |
| Pair-matrix view linked to 3D, including PAE/contact probability | ChimeraX, iCn3D | Missing; matrices correctly remain non-residue-colorable | P1 |
| Measurements: label, distance, angle, dihedral, planes/orientation | Mol* | Engine available; not harmonized | P1 |
| Multiple structures, superposition, RMSD, matched mapping | Mol*, ChimeraX, VMD | Static overlays exist; no shared comparison contract | P2 |
| Side-by-side synchronized/independent comparison | iCn3D | Missing | P2 |
| Complex component filters: protein/DNA/RNA/ligand/glycan/ion/water | Mol*, NGL | Partly available in engine; not exposed consistently | P2 |
| Contacts, hydrogen bonds, clashes, neighborhoods | Mol*, NGL, ChimeraX | Missing as shared BMS extensions | P2 |
| Interface inventory, buried SASA, interface network and residues | ChimeraX | Missing | P2 |
| Assembly, symmetry, repeated-instance inspection | Mol*, ChimeraX | Engine available; identity/control gaps | P2 |
| Candidate/ensemble browser with ranking and provenance | BMS Conformational Mapping | Ad hoc overlay controls only | P2 |
| Real coordinate-set/trajectory playback | Mol*, NGL, VMD, ChimeraX | No governed runtime path | P2 |
| Morph interpolation clearly labeled as visualization | ChimeraX | Missing | P3 |
| Difference layers, per-state displacement, RMSF, clustering, PCA/tICA | VMD/analysis tools plus BMS backend | Missing; must be backend-derived | P3 |
| Density, electrostatic, segmentation, and other spatial volumes | Mol*, ChimeraX, iCn3D | Engine capability not integrated | P3 |
| Saved scenes, reproducible snapshots, figure/export metadata | Mol*, iCn3D | Not standardized | P2 |
| Large-assembly/ensemble admission and performance controls | All mature tools | No unified policy | P1 |

## 4. Product shape

BMS will have one `StructureWorkbench` with four compositions:

1. **Compact:** embedded structure, status, essential controls, linked selection.
2. **Standard:** component filters, representations, metrics, sequence, measurements, annotations.
3. **Comparison:** side-by-side or overlaid structures with explicit reference, mapping, transforms, and camera-link controls.
4. **Analysis:** standard/comparison plus pair matrices, interface network, state/conformer browser, charts, volume controls, provenance, and export.

Domain configurations enable extensions and choose defaults; they do not fork the runtime.

## 5. Phase gates

### M0 — Approve contract, preserve baseline, and prove the actual runtime

**Goal:** establish what production 3.3.0 can and cannot do before consolidating source.

**Create:**

- `platform/frontend/src/structureViewer/contracts/viewerCapabilities.ts`;
- `platform/frontend/src/structureViewer/runtime/molstarStable33Capabilities.ts`;
- `platform/frontend/tests/molstarRuntimeContract.test.ts`;
- `platform/frontend/browser-tests/molstar-runtime-probe.html`;
- `platform/frontend/browser-tests/molstarRuntimeProbe.ts`;
- `docs/reviews/structure_visualization/m0_runtime_contract_review.md`.

**Modify only if approved:**

- `platform/frontend/package.json` and lockfile to add one browser-test runner, preferably Playwright, after dependency review;
- `platform/frontend/vite.config.ts` only if an isolated test entrypoint needs explicit configuration.

**Work:**

1. Snapshot branch, HEAD, complete porcelain status, and hashes of every focused dirty path.
2. Resolve and record the production import, alias, installed package, wrapper types, and implementation commit/version.
3. Build a capability matrix for:
   - wrapper-level load completion and errors;
   - disconnect and disposal;
   - label/auth residue identity;
   - insertion code, model, altloc, operator/assembly instance, repeated entity instance;
   - selection, coloring, overlays, overlay removal, measurements, trajectories, assemblies/symmetry, volumes, snapshots, and event provenance.
4. Add compile-time and source-contract tests against 3.3.0.
5. Run a real Chromium harness under React StrictMode with at least 50 bounded lifecycle cycles.
6. Measure live plugins, canvases/WebGL contexts, events/listeners, timers, blobs, detached nodes, console errors, and renderer memory.
7. Compare two future options without changing production:
   - keep PDBe Molstar as the adapter boundary;
   - use direct Mol* behind the same BMS adapter for capabilities the wrapper cannot expose safely.
8. Record explicit `supported`, `partial`, and `unsupported` results. Do not fake support with a test-only adapter field.

**Acceptance:**

- exact runtime evidence is current and reproducible;
- StrictMode leaves one live usable viewer and teardown metrics plateau;
- unrepresentable identities fail closed;
- no package upgrade or workflow feature behavior changes;
- independent frontend/runtime review says `GO`.

**STOP:** renderer crash, context/listener/memory growth, ambiguous identity broadening, private API without a bounded adapter test, dirty-path drift, or inability to reproduce the production resolution.

---

### M1 — Create the shared contracts, host, controller, and engine adapter

**Goal:** establish one runtime owner and declarative scene state without changing workflow-facing feature semantics.

**Create:**

- `platform/frontend/src/structureViewer/contracts/structureIdentity.ts`;
- `platform/frontend/src/structureViewer/contracts/sceneState.ts`;
- `platform/frontend/src/structureViewer/contracts/viewerEvents.ts`;
- `platform/frontend/src/structureViewer/contracts/viewerResults.ts`;
- `platform/frontend/src/structureViewer/runtime/MolstarEngineAdapter.ts`;
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`;
- `platform/frontend/src/structureViewer/runtime/resourceOwnership.ts`;
- `platform/frontend/src/structureViewer/runtime/sceneReconciler.ts`;
- `platform/frontend/src/structureViewer/StructureViewerHost.tsx`;
- focused tests under `platform/frontend/tests/structureViewer*.test.ts`.

**Reconcile/move, do not discard:**

- `platform/frontend/src/lib/molstar-loader.ts`;
- dirty `platform/frontend/src/components/molstarLifecycle.ts`;
- `platform/frontend/src/components/molstarTouchInteraction.ts`.

**Work:**

1. Implement the canonical document/entity/residue/atom contracts from the v1 spec.
2. Implement typed `ViewerResult` states: `ok`, `unsupported`, `ambiguous`, `cancelled`, and `error`.
3. Put every third-party import, custom-element creation, `viewerInstance`, plugin call, and shadow-DOM hook inside `MolstarEngineAdapter`.
4. Give the scene controller one generation token and one owner for every async task and disposable resource.
5. Reconcile document replacement, representations, overlays, layers, selections, camera, and filters without React-key remounts.
6. Scope every event to viewer, scene, document, generation, and origin.
7. Add compact diagnostics with engine/wrapper/adapter versions and scene identity.
8. Prove replacement and teardown through the M0 browser harness.

**Acceptance:**

- no public controller type imports third-party viewer types;
- no stale generation commits after replacement/unmount;
- A → B → A, overlay reorder/removal, selection replacement, and multi-viewer events pass;
- lifecycle metrics plateau;
- no workflow behavior changes yet.

---

### M2 — Migrate every existing viewer consumer

**Goal:** remove duplicate orchestration before adding science-facing capabilities.

**Primary existing files:**

- `platform/frontend/src/components/MolstarViewer.tsx`;
- `platform/frontend/src/components/EpitopeMolstarViewer.tsx`;
- `platform/frontend/src/components/QuickViewer.tsx`;
- `platform/frontend/src/components/FloatingViewer.tsx`;
- `platform/frontend/src/components/StructureViewerPane.tsx`;
- every JSX render site found by the migration inventory.

**Create:**

- `platform/frontend/src/structureViewer/StructureWorkbench.tsx`;
- `platform/frontend/src/structureViewer/workbench/CompactStructureWorkbench.tsx`;
- `platform/frontend/src/structureViewer/workbench/StandardStructureWorkbench.tsx`;
- `platform/frontend/src/structureViewer/extensions/selection/SelectionExtension.ts`;
- migration/no-regression tests.

**Work:**

1. Freeze an exact render-site inventory and expected behavior for all current consumers.
2. Turn `MolstarViewer` into a temporary compatibility facade over `StructureViewerHost`.
3. Replace `EpitopeMolstarViewer` internals with the shared workbench and typed selection extension.
4. Replace `A45` parsing with explicit compatibility parsing and a migration warning; new callers use `ResidueRef` only.
5. Remove unscoped global click handling.
6. Remove arbitrary readiness sleeps/polls where the capability probe provides an event/promise.
7. Remove parent keys used only to recreate the viewer for color/overlay changes.
8. Migrate consumers one at a time with focused typecheck/test after each.
9. Delete compatibility wrappers only after zero remaining direct consumer matches or retain them as thin, documented facades if public stability requires it.

**Acceptance:**

- every generic and epitope/CDR render site uses the shared runtime;
- zero direct `<pdbe-molstar>`, `viewerInstance`, plugin, loader, and unscoped viewer-event use outside the adapter;
- existing selection, display, fullscreen, touch, overlay, blob, and error behavior has no regression;
- canonical frontend test, lint, isolated build, and browser lifecycle probe pass.

**STOP:** any workflow loses selection fidelity, a caller still needs engine internals, or whole-app gates are red because of target changes.

---

### M3 — Metric registry, filtering, linked views, and measurements

**Goal:** support the breadth of AI/ML outputs without hard-coding another RGB map per workflow.

**Create:**

- `platform/frontend/src/structureViewer/metrics/metricContracts.ts`;
- `platform/frontend/src/structureViewer/metrics/MetricRegistry.ts`;
- `platform/frontend/src/structureViewer/metrics/metricProjection.ts`;
- `platform/frontend/src/structureViewer/extensions/metrics/MetricLayerExtension.ts`;
- `platform/frontend/src/structureViewer/extensions/metrics/MetricLegendPanel.tsx`;
- `platform/frontend/src/structureViewer/extensions/filters/FilterPanel.tsx`;
- `platform/frontend/src/structureViewer/extensions/sequence/SequenceTrackExtension.tsx`;
- `platform/frontend/src/structureViewer/extensions/pairMatrix/PairMatrixExtension.tsx`;
- `platform/frontend/src/structureViewer/extensions/measurements/MeasurementExtension.ts`;
- metric/filter/linkage tests.

**Reconcile:**

- dirty `platform/frontend/src/lib/molstar-metrics.ts`;
- dirty `platform/frontend/tests/molstarMetrics.test.ts`;
- metric/color props in `MolstarViewer.tsx` and `StructureViewerPane.tsx`.

**First registered dimensions:**

1. residue scalar and categorical;
2. atom scalar when identity is exact;
3. structure/conformer scalar for labels and ranking;
4. chain-pair/interface scalar;
5. residue-pair matrix;
6. geometry annotation;
7. volume descriptor placeholder, not volume rendering yet.

**First metrics/annotations:**

- pLDDT/local confidence;
- PAE and contact-probability matrices;
- B-factor/SASA/conservation/disorder when supplied;
- FrustraMPNN selected slices with exact semantic limits;
- ipSAE interface quality for Boltz-2, with no iPTM substitution;
- epitope, CDR, mutation, pocket, and residue-set annotations;
- contacts/restraints/clashes supplied by approved analyses.

**Filtering UX:**

- entity type, chain, repeated instance, residue range, ligand/water/ion;
- named selection sets and boolean/range/neighborhood operations;
- metric identity, range, percentile, category, and missingness;
- search by residue identity with visible namespace;
- deterministic layer order, visibility, opacity, palette, and reset;
- linked sequence, matrix, table, and 3D hover/selection.

**Acceptance:**

- RGB is derived output only;
- value, units, direction, missingness, and provenance appear in legends/tooltips;
- matrices are not silently projected to residue colors;
- unsupported identities and projection types fail closed;
- pair-matrix and sequence selections round-trip to exact 3D residues;
- keyboard, touch, and screen-reader labels cover essential controls;
- large matrices and annotations are virtualized/admission-bounded.

---

### M4 — Complex, assembly, interface, and interaction workbench

**Goal:** make protein complexes, protein–DNA/RNA, ligand-bound structures, repeated assemblies, and interfaces inspectable rather than merely renderable.

**Create extensions:**

- `complexComponents`;
- `assemblyAndSymmetry`;
- `interactions`;
- `interfaces`;
- `surfaceAndPocket`;
- `complexComparison`.

**Capabilities:**

1. filter/show/hide/focus protein, DNA, RNA, ligand, glycan, ion, water, and unknown components;
2. inspect entity versus repeated assembly instance explicitly;
3. inspect biological assembly and symmetry/operator provenance;
4. list chain-chain and component-component interfaces;
5. show interface residues and interaction geometry;
6. filter contacts, hydrogen bonds, salt bridges, hydrophobic contacts, clashes, and neighborhoods by declared thresholds/source;
7. display buried-SASA/interface-area data and a linked interface network when supplied by an approved backend analysis;
8. compare complex candidates without collapsing chain/instance mappings;
9. map chain-pair/interface metrics such as ipSAE to the correct interface, not the whole structure;
10. export selected components, residue sets, interaction tables, and provenance-bound images.

**Scientific boundary:**

The frontend renders and filters versioned analysis results. It does not invent buried area, pocket scores, interface confidence, or interaction chemistry unless a separately approved, versioned browser algorithm is explicitly contracted and validated.

**Acceptance:**

- repeated chains and operator instances remain distinct end to end;
- protein–nucleic-acid and ligand interfaces work without protein-only assumptions;
- missing analysis is explicit;
- interaction and interface thresholds are visible and provenance-bound;
- large interaction sets remain responsive and cancellable.

---

### M5 — Conformational mapping, ensemble, comparison, and trajectory workbench

**Normative tranche specification:** `docs/specs/structure_visualization/m5_m6_workbench_spec_v1.md`.

**Goal:** provide the missing conformational-analysis UX without weakening the Conformational Mapping contract.

**Separate authorization:** This phase does not start the separately gated `docs/plans/2026-07-06-conformational-mapping-orchestrator.md` Phase 13. That phase still requires its own Phase 12 `GO`, owner-approved baseline, allowlist, and review.

**Create extensions after authorization:**

- `ensembleBrowser`;
- `structureAlignment`;
- `sideBySideComparison`;
- `differenceLayers`;
- `trajectoryPlayback`;
- `morphVisualization`.

**Capabilities:**

1. browse deterministic candidate/model/frame ordering and backend coordinates;
2. rank/filter candidates by supplied conformer/global metrics and explicit missingness;
3. overlay a bounded number of candidates with stable color/opacity and explicit reference;
4. side-by-side views with camera link on/off and selection synchronization;
5. superposition using a declared reference, atom/residue selection, mapping, transform, and RMSD result;
6. aligned/matched/unmatched residue display;
7. linked charts for supplied RMSD/RMSF/displacement, clusters, PCA/tICA coordinates, energy/confidence, and support metrics;
8. real coordinate-set playback only for authoritative trajectory data;
9. morph interpolation only as a clearly labeled visual interpolation;
10. compare complete complexes while preserving entity/instance composition;
11. save a provenance-bound scene containing candidate IDs, transforms, filters, layers, camera, and metric versions.

**Semantic guardrails:**

- independent generated structures are `independent_hypotheses`, never frames or trajectories;
- array order is not time;
- visual morphs are not mechanisms;
- empirical/post-hoc analysis is labeled separately from generation confidence;
- no equilibrium, free-energy, ΔΔG, or kinetics claim is created by visualization;
- browser code does not calculate scientific values prohibited by the authoritative contract.

**Acceptance:**

- exact candidate identity/order and source hashes round-trip;
- alignment mapping and transforms are exportable and reproducible;
- side-by-side and overlays produce identical selection identities;
- large ensembles use admission limits and lazy loading;
- Conformational Mapping Phase 13 test IDs and no-regression gates pass only under its separate authorization.

---

### M6 — Volumes, advanced analysis presentation, snapshots, and exports

**Normative tranche specification:** `docs/specs/structure_visualization/m5_m6_workbench_spec_v1.md`.

**Goal:** support cryo-EM/density, electrostatics, segmentation, and publication/review workflows after the core platform is stable.

**Capabilities:**

- load versioned scalar volumes with grid transforms and units;
- contour, slice, opacity, channel, and crop controls;
- correlate volume selections with structure components when supplied;
- density/electrostatics/segmentation provenance and recommended contour metadata;
- reproducible workbench snapshots;
- image export with scene metadata;
- selection/measurement/interface/metric table export;
- optional movie export for actual trajectories or explicitly labeled morphs;
- restored sessions validated against document and metric hashes.

**Acceptance:**

- volume transforms and units are explicit;
- stale snapshots fail closed when bound source hashes no longer match;
- exports include engine/adapter version, source identity, scene state, metric provenance, and semantic collection kind;
- large volume admission and renderer-memory limits are enforced.

## 6. Rules for all future viewer additions

Every future feature PR must answer these questions before code review:

1. Which existing workbench composition and extension owns it?
2. What exact scientific question does it answer?
3. What canonical structure/entity/residue/atom identity does its data use?
4. What metric dimension or geometry type is it?
5. Is the value computed by an authoritative backend, or merely displayed/transformed in the browser?
6. How are units, direction, missingness, thresholds, and provenance shown?
7. Which pinned-runtime capabilities are required, and how does unsupported behavior fail closed?
8. How does it behave with repeated instances, insertion codes, multiple models, altlocs, and non-protein components?
9. How do 1D, 2D, and 3D selections synchronize without global event leakage?
10. Who owns every listener, timer, worker, blob, overlay, representation, and WebGL resource?
11. What are the admission/performance limits?
12. Which focused, browser-lifecycle, accessibility, and whole-app tests prove completion?

If the feature cannot answer these within the shared contract, it must not create a new viewer wrapper as a shortcut.

## 7. Verification commands

Use the repository’s actual scripts and isolated outputs. Exact commands may be adjusted only if the package scripts change under review.

```bash
pnpm --dir platform/frontend test
pnpm --dir platform/frontend lint
BMS_FRONTEND_BUILD_OUT_DIR=/tmp/bms_structure_viewer_dist \
  pnpm --dir platform/frontend build:isolated

# After M0 adds an approved browser runner:
pnpm --dir platform/frontend test:viewer-browser

git diff --check
git status --short
```

Focused TypeScript tests are supporting evidence, not substitutes for the actual browser/runtime probe or whole-frontend gates.

## 8. Review and release discipline

Each milestone requires:

- exact start/end branch, HEAD, porcelain, focused hashes, and allowlist;
- RED tests before implementation;
- focused frontend/runtime review;
- scientific-contract review for metrics, interfaces, and conformational semantics;
- resource/lifecycle browser evidence;
- whole-frontend regression evidence;
- dirty-tree-safe rollback;
- final immutable post-implementation review even when a precommit review is waived.

Milestones should land as narrow commits. Do not mix a runtime consolidation with a package upgrade, backend feature, scientific formula, or unrelated UI redesign.

## 9. Recommended first execution slice

After approval, execute **M0 only**:

1. preserve the current dirty Mol* work;
2. write the production 3.3 capability manifest;
3. add the real-browser lifecycle/identity probe;
4. decide the adapter boundary using measured evidence;
5. stop for review.

Do not add conformational, interface, or metric features during M0. The first feature work begins only after the shared runtime has an M1/M2 `GO`.
