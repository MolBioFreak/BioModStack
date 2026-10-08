# BioModStack M5/M6 Structure Workbench Specification v1

**Date:** 2026-07-21
**Status:** Normative implementation specification; implementation, Conformational Mapping Phase 13, package changes, and runtime acceptance remain separately authorized.
**Parent contract:** `docs/specs/structure_visualization/structure_visualization_contract_v1.md`
**Roadmap:** `docs/plans/2026-07-18-structure-visualization-platform-roadmap.md`
**Source baseline:** branch `test`, M3/M4 commit `849fb159d8388f6176fea4096560d4aecaacef53`.

## 1. Goal

Specify M5 conformational collections/comparisons/trajectories and M6 volumes/snapshots/exports on the existing BMS-owned direct Mol* runtime without creating another viewer, scientific computation path, or opaque session format.

M5 and M6 MUST compose through `StructureWorkbench`, `StructureViewerHost`, `StructureSceneController`, and `MolstarEngineAdapter`. Workflow components configure these capabilities; they MUST NOT own Mol*, WebGL, scene reconciliation, or scientific identity.

## 2. Current foundation and hard boundaries

The implementation begins from these current facts:

- `StructureCollectionKind` already distinguishes independent hypotheses, experimental ensembles, coordinate trajectories, interpolated morphs, matched state series, and static complex components.
- `StructureSceneState` already carries ordered documents, presentation, provenance, optional molecular-dynamics state, and hash-bound snapshot state.
- `MDSceneState` already requires replica identity, topology/trajectory artifacts, atom-order identity, hashes, source-frame identity, and physical time.
- direct Mol* 4.5 currently supports ordered static overlays and BMS scene snapshots;
- governed XTC/DCD playback remains `unsupported` until exercised against the exact runtime;
- governed volume loading remains `unsupported`;
- model, altloc, operator-instance, and ambiguous repeated-instance identity remain fail-closed gaps;
- M3/M4 metric, filter, exact selection, measurement, component, interface, and interaction state remains authoritative and MUST be reused.

This specification does not authorize Conformational Mapping orchestrator Phase 13. Any Phase 13 producer integration still requires its own upstream gate and owner authorization.

## 3. Global semantic invariants

### 3.1 Collection kinds are not interchangeable

| Kind | Required language | Forbidden language or inference |
|---|---|---|
| `independent_hypotheses` | candidate, prediction, generated conformer | frame, time, pathway, equilibrium, transition |
| `experimental_ensemble` | model, member, experimental ensemble | temporal order unless explicitly supplied |
| `matched_state_series` | state, condition, ordered comparison | continuous dynamics or mechanism |
| `coordinate_trajectory` | replica, frame, source frame, step/time when supplied | independent candidate rank |
| `interpolated_morph` | visual interpolation, endpoint, interpolation fraction | trajectory, mechanism, kinetics, free-energy path |
| `static_complex_components` | component, chain, assembly | conformational order |

Array order MUST NOT create time, rank, probability, confidence, cluster, or mechanistic meaning. Every such coordinate MUST be explicitly supplied with provenance.

### 3.2 Scientific computation boundary

The browser MAY:

- page, sort, filter, and select supplied records;
- apply a supplied rigid transform;
- interpolate camera state and visual morph coordinates under an approved display-only algorithm;
- threshold and color supplied values;
- decode and display approved coordinate or volume formats;
- calculate pixels, UI layout, and display-only bounds.

The browser MUST NOT invent:

- atom/residue mapping or alignment policy;
- RMSD, RMSF, displacement, clustering, PCA, tICA, free energy, kinetics, transition paths, density correlation, electrostatic potential, or segmentation;
- missing time coordinates or frame ordering;
- confidence from rank or rank from confidence;
- a structure-volume registration transform;
- a physical claim from a morph.

These scientific products require a versioned backend producer, declared method/parameters, input hashes, output hash, units, missingness, and identity mapping.

### 3.3 Identity and provenance

Every collection member, alignment, frame, metric point, volume, segment, snapshot, and export MUST bind to opaque artifact identity and SHA-256 content identity where bytes exist. Engine-local object references and atom indices MUST NOT cross scene generations.

Any operation requiring model, altloc, operator, repeated-instance, atom-order, mapping, or transform identity that is unavailable MUST return `unsupported` or `ambiguous`; it MUST NOT broaden selection.

## 4. M5 contracts

### 4.1 Collection manifest

Create `contracts/structureCollections.ts` with the equivalent of:

```ts
interface StructureCollectionManifestV1 {
  schemaVersion: 1;
  collectionId: string;
  kind: StructureCollectionKind;
  orderedMemberIds: string[];
  ordering: {
    semantic: 'producer_order' | 'rank' | 'condition' | 'time' | 'none';
    coordinateName?: string;
    units?: string;
    producerRef: string;
  };
  membersPageRef: string;
  memberCount: number;
  provenanceRef: string;
  manifestSha256: string;
}

interface StructureCollectionMemberV1 {
  memberId: string;
  documentId: string;
  candidateId?: string;
  structureArtifactId: string;
  structureSha256: string;
  sourceFormat: 'pdb' | 'mmcif' | 'bcif' | 'sdf';
  rank?: number;
  backendCoordinate?: Record<string, number | string>;
  metricRefs: string[];
  mappingRef?: string;
  provenanceRef: string;
  missingness?: 'missing' | 'unsupported' | 'ambiguous' | 'not_applicable';
}
```

Rules:

1. `orderedMemberIds` contains each admitted member exactly once.
2. Rank, backend coordinates, cluster labels, and conditions remain independent fields.
3. Members are paged; collection metadata MUST NOT inline all structure bytes or unbounded metrics.
4. A member without structure bytes may remain listed as unavailable, but MUST NOT become an empty structure.
5. Changing filters MUST NOT mutate authoritative collection order.

### 4.2 Ensemble browser

Create `extensions/ensemble/EnsembleBrowserExtension.tsx`.

The M5A browser MUST provide:

- paged deterministic member inventory;
- explicit collection-kind badge and semantic warning;
- rank/filter controls for supplied conformer/global metrics with missingness;
- lazy structure loading;
- one explicit active member;
- bounded overlay selection with stable user-visible colors and opacity;
- exact provenance and source-hash inspection;
- linked member/chart/table/3D selection;
- no autoplay for independent hypotheses or experimental ensembles.

Initial admission defaults:

- metadata page: at most 200 members;
- simultaneously resident static structures: at most 8;
- overlaid structures: at most 4;
- side-by-side canvases: exactly 2 for v1;
- all limits MUST be configuration/capability driven and lowerable for mobile or renderer pressure.

Limit refusal MUST preserve current scene state and explain the limit.

### 4.3 Alignment and comparison contract

Create `contracts/structureComparison.ts` with:

```ts
interface StructureAlignmentV1 {
  schemaVersion: 1;
  alignmentId: string;
  referenceDocumentId: string;
  mobileDocumentId: string;
  referenceSelectionRef: string;
  mobileSelectionRef: string;
  mappingArtifactId: string;
  mappingSha256: string;
  method: string;
  producerVersion: string;
  parameters: Record<string, string | number | boolean | null>;
  transformRowMajor4x4: readonly number[];
  matchedAtomCount: number;
  matchedResidueCount: number;
  unmatchedReferenceCount: number;
  unmatchedMobileCount: number;
  rmsd?: { value: number; units: 'Å'; provenanceRef: string };
  provenanceRef: string;
}
```

Validation MUST require 16 finite transform values, distinct declared documents, exact mapping identity, nonnegative counts, and an input mapping hash. Applying a transform is display behavior; producing the transform or mapping is a scientific analysis.

Create `extensions/comparison/StructureComparisonExtension.tsx` with:

- explicit reference/mobile roles;
- overlay and side-by-side modes;
- camera link `off | orientation | full`;
- selection link `off | mapped_only`;
- matched/unmatched display from the supplied mapping;
- transform, mapping, method, parameters, and RMSD inspection;
- no selection propagation for unmapped or ambiguous residues;
- identical canonical residue identity regardless of overlay or side-by-side layout.

Camera linkage MUST be loop-free, owner-scoped, and generation-scoped. `orientation` links rotation only; `full` links orientation, target, distance, and clipping state.

### 4.4 Supplied difference and conformational analyses

Extend metric dimensions/contracts as needed for `conformer-scalar` and `frame-series`; do not overload residue scalar or structure scalar.

The workbench MAY render supplied:

- global or selection RMSD;
- per-residue displacement;
- RMSF;
- radius of gyration and other admitted frame series;
- cluster labels and medoid identity;
- PCA/tICA coordinates;
- energy, confidence, support, and convergence diagnostics.

Every analysis MUST identify:

- collection and member/frame domain;
- alignment/mapping reference;
- producer/method/version and parameters;
- input/output artifact hashes;
- units and direction;
- missingness;
- whether the result is empirical/post-hoc, producer confidence, or experimental data.

PCA/tICA axes MUST be labeled as model coordinates, not physical reaction coordinates unless the producing scientific contract explicitly establishes that interpretation.

### 4.5 Coordinate trajectory contract

Retain `MDTrajectoryArtifactRef`, `MDSourceFrameRef`, and `MDSceneState`, then add an implementation manifest equivalent to:

```ts
interface CoordinateTrajectoryManifestV1 {
  schemaVersion: 1;
  trajectoryId: string;
  replica: number;
  topologyArtifactId: string;
  topologySha256: string;
  trajectoryArtifactId: string;
  trajectorySha256: string;
  atomOrderIdentity: string;
  format: 'xtc' | 'dcd';
  frameCount: number;
  frameMapArtifactId: string;
  frameMapSha256: string;
  timeBasis: 'explicit_ps' | 'explicit_step';
  byteLength: number;
  rangeCapable: true;
  provenanceRef: string;
}
```

The authoritative frame map MUST map displayed frame index to replica, source frame, and explicit time/step. The frontend MUST NOT infer time from frame number.

M5C playback requirements:

- topology hash and atom-order identity validated before trajectory attachment;
- lazy, authenticated, bounded range delivery;
- one active replica/trajectory decoder in v1;
- frame change events include replica, source frame, time/step, scene, generation, and origin;
- Plotly/chart selection and Mol* frame selection round-trip through the frame map;
- play, pause, seek, step, speed, loop, and stride are display controls only;
- changing replica retires decoder, buffers, workers, subscriptions, and trajectory state transactionally;
- static representative structures remain available when playback is unsupported;
- no whole trajectory bytes in job/detail JSON or React state.

Admission MUST be based on byte length, frame count, atom count, range capability, decoder support, and owned resident-memory budget. Unsupported formats, absent range service, missing frame map, hash mismatch, or atom-order mismatch fail closed.

### 4.6 Morph visualization contract

Create `contracts/morphVisualization.ts`:

```ts
interface MorphVisualizationV1 {
  schemaVersion: 1;
  morphId: string;
  firstDocumentId: string;
  secondDocumentId: string;
  mappingRef: string;
  mappingSha256: string;
  transformRef?: string;
  interpolationMethod: string;
  interpolationVersion: string;
  interpolationSteps: number;
  provenanceRef: string;
  semanticLabel: 'visual_interpolation_not_physical_trajectory';
}
```

Morphs require exact matched atom identity. Unmatched atoms MUST be omitted, held at an explicitly declared endpoint policy, or rendered separately; the choice must be visible. Morph export and playback MUST retain the nonphysical label. Morph frames MUST NOT enter trajectory analyses.

### 4.7 M5 events and scene state

Add scoped events:

- `collection-page-loaded`;
- `candidate-changed`;
- `comparison-mode-changed`;
- `alignment-applied`;
- `camera-link-changed`;
- `frame-changed`;
- `playback-changed`;
- `replica-changed`;
- `morph-step-changed`.

Extend scene state with versioned, serializable collection-browser, comparison, and playback state. Runtime decoder buffers and Mol* state refs MUST NOT enter serializable scene state.

## 5. M5 delivery gates

### M5A — Collections and ensemble browser

Deliver contracts, pagination, deterministic ordering, lazy member loading, bounded overlay, linked charts/tables, and provenance. Pilot with existing result collections without authorizing a new producer.

### M5B — Reproducible comparison

Deliver supplied alignment/mapping contract, transform application, overlay/side-by-side modes, camera/selection linking, and matched/unmatched display.

### M5C — Actual trajectory playback

Deliver only after an exact Mol* 4.5 capability probe proves at least one approved topology/trajectory combination, range service, frame map, cancellation, and memory plateau. Initial target is the existing MD contract: representative CIF first, then bounded GRO+XTC and PDB+DCD only when each pairing is proven.

### M5D — Difference analyses and morphs

Admit versioned backend analysis layers first. Add visual morphs only after exact mapping and lifecycle ownership are established. No physical-path claim is permitted.

M5A/B MUST NOT depend on M5C/D.

## 6. M6 contracts

### 6.1 Volume descriptor

Create `contracts/spatialVolumes.ts`:

```ts
interface SpatialVolumeDescriptorV1 {
  schemaVersion: 1;
  volumeId: string;
  semanticKind: 'density' | 'electrostatic_potential' | 'segmentation' | 'other_scalar';
  artifactId: string;
  artifactSha256: string;
  format: string;
  byteLength: number;
  dimensions: readonly [number, number, number];
  axisOrder: readonly [number, number, number];
  gridToWorldRowMajor4x4: readonly number[];
  coordinateUnits: string;
  valueUnits?: string;
  statistics?: { min: number; max: number; mean?: number; sigma?: number };
  channelCount: number;
  recommendedDisplay?: {
    channel?: number;
    contourAbsolute?: number;
    contourSigma?: number;
    opacity?: number;
  };
  registrationRef?: string;
  provenanceRef: string;
}
```

Validation MUST require finite positive dimensions, 16 finite transform values, valid axis order, hash, byte length, channel bounds, units, and finite statistics. Sigma contours are unavailable unless authoritative mean and sigma are supplied. A file header alone does not establish structure registration provenance.

### 6.2 Volume presentation state

Create a serializable `VolumePresentationStateV1` containing:

- volume/channel identity;
- visibility and opacity;
- contour mode `absolute | sigma` and value;
- color/palette identity;
- representation `isosurface | slice` for v1;
- slice axis/index or explicit world plane;
- crop bounds with declared `grid | world` namespace;
- segment visibility only for segmentation volumes;
- registration transform identity.

All controls MUST be declarative scene layers. Separate React effects MUST NOT independently create, clear, or repaint volume objects.

### 6.3 Structure-volume linkage

Structure and volume selection may synchronize only when supplied registration and mapping make the relationship exact. The frontend MAY focus a supplied mapped region but MUST NOT calculate correlation or registration.

Correlation coefficients, local-resolution values, fit scores, electrostatic samples, and segment/component associations are metric or mapping artifacts with their own producer, units, method, parameters, hashes, and missingness.

### 6.4 Segmentation contract

Create `VolumeSegmentationV1` with segmentation artifact/hash, volume identity, label table, segment IDs, optional hierarchy, recommended colors, and provenance. Segment color is presentation metadata; segment meaning remains producer data. Unknown labels remain unknown and MUST NOT be guessed from nearby structures.

### 6.5 Snapshot v2

Evolve `ViewerSnapshot` to a backward-readable v2 rather than storing opaque Mol* plugin state.

`ViewerSnapshotV2` MUST contain:

- BMS scene schema version;
- engine package/version and adapter ID/version;
- document, trajectory, volume, metric, mapping, alignment, segmentation, and analysis bindings with hashes;
- collection kind and authoritative member ordering;
- active member/frame/replica;
- representations, ordered layers, filters, selections, measurements, interfaces, comparison transforms, volume state, camera state, and UI composition;
- capture time and provenance;
- no credentials, filesystem paths, signed URLs, plugin refs, engine-local indices, decoder buffers, or WebGL state.

Restore MUST validate every required binding before mutating the live scene. Hash mismatch, unavailable required artifact, incompatible schema/adapter capability, or ambiguous identity returns a complete refusal report and leaves the current scene unchanged. Optional missing layers may be skipped only when the snapshot marked them optional and the user accepts a partial restore plan before application.

### 6.6 Export manifest

Every export creates an `ExportManifestV1` sidecar with:

- export ID, kind, timestamp, requesting user/workflow/job context;
- scene/snapshot ID;
- semantic collection kind;
- source artifact IDs and hashes;
- engine/adapter versions;
- active member/frame/replica and transform/mapping IDs;
- visible layers, palettes, thresholds, contour/slice state, camera, selections, and measurements;
- metric/analysis provenance;
- export parameters and output SHA-256;
- semantic warning for morph or post-hoc analysis.

Admitted v1 exports:

| Export | Format | Rule |
|---|---|---|
| Scene state | BMS snapshot JSON | canonical reproducibility artifact |
| Figure | PNG plus manifest JSON | no claim that pixels preserve underlying numeric data |
| Selection/residue/interface/interaction/measurement/metric tables | CSV and JSON plus manifest | canonical IDs, units, missingness, provenance retained |
| Selected coordinates | mmCIF plus manifest | only exact selected components/atoms; no identity broadening |
| Actual trajectory movie | WebM/MP4 plus manifest | requires authoritative coordinate trajectory and source-frame range |
| Morph movie | WebM/MP4 plus manifest | permanently labeled visual interpolation, not trajectory |

The browser MUST NOT silently export a sampled chart or colored structure as the scientific numeric dataset.

### 6.7 Artifact delivery and security

Volume and trajectory bytes MUST use authenticated opaque artifact IDs and bounded streaming/range routes. APIs MUST enforce owner/job containment, MIME and format allowlists, `200/206/416` behavior, maximum byte/range policy, and no host-path disclosure. Signed URLs, if used, MUST be short-lived and MUST NOT enter snapshots or manifests.

Remote structure/volume URLs remain subject to BMS content-security, authorization, and origin policy. Parsers/decoders run under owned cancellation and admission limits. Errors expose artifact/scene identity and adapter version without secrets or local paths.

### 6.8 M6 admission and resource ownership

Before loading, evaluate:

- dimensions, voxel count, channels, byte length, format, transform validity;
- renderer capability and estimated GPU/CPU memory;
- concurrently resident structures, trajectories, and volumes;
- mobile versus desktop capability budget.

Only the owner generation may hold volume objects, decode workers, range requests, object URLs, frame buffers, movie encoders, and export blobs. Replacement/unmount MUST cancel and release them. Admission refusal preserves the current scene.

## 7. M6 delivery gates

### M6A — Snapshot v2 and tabular/figure exports

Implement hash-bound restore and export manifests before adding volume rendering. This extends currently supported BMS scene snapshots and provides reproducibility for M5.

### M6B — Scalar volumes

Add one governed scalar-volume path after an exact Mol* 4.5 adapter probe establishes approved format loading, transform fidelity, contour/slice behavior, replacement, and memory plateau. Density and electrostatic potential remain distinct semantic kinds even if they share rendering machinery.

### M6C — Segmentation and structure-volume linkage

Add supplied segment labels, hierarchy, visibility, and supplied registration/mapping. No browser-derived segmentation, fitting, or correlation.

### M6D — Movie export

Add bounded, cancellable export for proven trajectories and separately labeled morphs. Movie export MUST NOT block scene interaction indefinitely or retain encoder buffers after completion/cancellation.

M6A MUST NOT depend on M6B-D. M6B MUST NOT imply M6C scientific linkage.

## 8. Anticipated file ownership

Create or extend only shared platform paths:

- `platform/frontend/src/structureViewer/contracts/structureCollections.ts`;
- `platform/frontend/src/structureViewer/contracts/structureComparison.ts`;
- `platform/frontend/src/structureViewer/contracts/morphVisualization.ts`;
- `platform/frontend/src/structureViewer/contracts/spatialVolumes.ts`;
- `platform/frontend/src/structureViewer/contracts/sceneState.ts`;
- `platform/frontend/src/structureViewer/contracts/mdTrajectory.ts`;
- `platform/frontend/src/structureViewer/contracts/viewerEvents.ts`;
- `platform/frontend/src/structureViewer/extensions/ensemble/*`;
- `platform/frontend/src/structureViewer/extensions/comparison/*`;
- `platform/frontend/src/structureViewer/extensions/trajectory/*`;
- `platform/frontend/src/structureViewer/extensions/volumes/*`;
- `platform/frontend/src/structureViewer/extensions/export/*`;
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`;
- `platform/frontend/src/structureViewer/runtime/MolstarEngineAdapter.ts`;
- `platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts`;
- `platform/frontend/src/structureViewer/StructureWorkbench.tsx`;
- owner-scoped backend artifact/analysis routes and versioned schemas where producer data is required.

Workflow-specific components may adapt authoritative results into these contracts. They MUST NOT create another plugin owner, trajectory decoder, volume loader, alignment implementation, snapshot format, or export pipeline.

## 9. Acceptance gates

### 9.1 Contract and scientific semantics

- every multi-structure scene has one explicit collection kind;
- independent hypotheses never expose time/frame language;
- trajectories require frame map, topology/trajectory hashes, and atom-order identity;
- morphs always retain the nonphysical semantic label;
- alignment/difference/cluster/PCA/tICA/volume correlations are producer artifacts, not browser inventions;
- missing/unsupported/ambiguous/not-applicable remain distinct;
- non-protein components and exact repeated instances are preserved where authoritative and refused where not.

### 9.2 Runtime and linkage

- candidate/member, chart, sequence, matrix, interface, comparison, frame, and 3D selection round-trip through canonical identity;
- overlay and side-by-side use the same mapping and selection semantics;
- camera linking is loop-free and independently disableable;
- trajectory seek/play/replica replacement is cancellation-safe;
- volume replacement and snapshot restore are transactional;
- no stale generation can commit a structure, frame, transform, volume, export, or event.

### 9.3 Reproducibility and export

- transforms, mapping, collection order, frame identity, volume transform, filters, layers, camera, and provenance round-trip;
- snapshot restore rejects stale hashes without mutating the live scene;
- every export includes a manifest and output hash;
- exported tables retain canonical identity, units, missingness, and provenance;
- image/movie exports retain collection semantics and warnings.

### 9.4 Performance and lifecycle

- lists/charts are paged or virtualized;
- coordinate and volume bytes are lazy and bounded;
- no eager all-member/all-frame/all-voxel loading;
- cancellation releases range requests, workers, buffers, blobs, Mol* objects, and encoder state;
- repeated A→B→A structure, trajectory, volume, comparison, snapshot, and export cycles plateau under the exact production runtime;
- mobile and desktop use capability budgets, not scientific shortcuts.

## 10. Explicit non-goals for v1

- browser alignment or structure prediction;
- browser clustering, PCA/tICA, free-energy, kinetics, electrostatics, segmentation, fitting, or density correlation;
- arbitrary third-party plugin-state session import;
- more than two simultaneous comparison canvases;
- concurrent multi-replica trajectory playback;
- trajectory editing;
- volumetric annotation authoring;
- unrestricted movie resolution/duration;
- hidden fallback from unsupported trajectory/volume behavior to misleading static behavior;
- Conformational Mapping Phase 13 producer execution without its separate authorization.

## 11. Implementation order

1. M5A collection/member contracts and browser.
2. M5B supplied comparison/alignment and side-by-side state.
3. M6A snapshot v2 and tabular/figure exports, covering M5 state.
4. M5C one proven authoritative trajectory pairing.
5. M5D supplied difference analyses, then display-only morphs.
6. M6B one proven scalar-volume format/path.
7. M6C supplied segmentation and registration linkage.
8. M6D bounded trajectory/morph movie export.

Each gate lands separately. Failure or deferral of trajectory, morph, volume, segmentation, or movie support MUST NOT block the earlier collection, comparison, snapshot, or table-export gates.
