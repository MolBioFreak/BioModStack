# BioModStack M5/M6 Structure Workbench Specification v1

**Date:** 2026-07-21
**Status:** Complete normative implementation specification. It contains no unresolved product choices. Implementation, Conformational Mapping Phase 13, package changes, and runtime acceptance remain separately authorized.
**Parent contract:** `docs/specs/structure_visualization/structure_visualization_contract_v1.md`
**Roadmap:** `docs/plans/2026-07-18-structure-visualization-platform-roadmap.md`
**Source baseline:** branch `test`, direct Mol* runtime fix `c807a2a483b1e43c366da792b704a7c8ede07f4a`.

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

Create `contracts/structureCollections.ts` with exactly these required fields:

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
  membersResource: 'job_viewer_collection_members_v1';
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
6. A v1 manifest is limited to 100,000 members and 8 MiB of RFC 8785 canonical JSON. Larger collections require a separately versioned paged-ordering contract and are refused by v1 rather than truncated.

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
  method: 'supplied_transform_v1' | 'kabsch_exact_atom_mapping_v1';
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

interface StructureAtomMappingV1 {
  schema: 'bms.viewer.atom-mapping.v1';
  mappingId: string;
  referenceDocumentId: string;
  mobileDocumentId: string;
  pairs: Array<{
    pairId: string;
    reference: AtomRef;
    mobile: AtomRef;
  }>;
  rejectedPairs: Array<{
    reference: AtomRef;
    mobile: AtomRef;
    reason: string;
  }>;
  provenanceRef: string;
}
```

Validation MUST require 16 finite transform values, distinct declared documents, exact mapping identity, nonnegative counts, and an input mapping hash. Every `AtomRef` must resolve exactly, including model/operator/altloc when required; duplicate atom endpoints or pair IDs are invalid. Applying a transform is display behavior; producing the transform or mapping is a scientific analysis.

The transform maps mobile coordinates in Å into reference-world coordinates using the column-vector convention `p_reference = M × [x_mobile, y_mobile, z_mobile, 1]`. It MUST be rigid: final row `[0, 0, 0, 1]`, orthonormal 3×3 rotation within `1e-6`, determinant within `1e-6` of `+1`, and no scale, reflection, shear, or perspective. RMSD, when supplied, is calculated over the admitted mapping pairs after this transform and uses Å.

`supplied_transform_v1` displays a producer-supplied mapping and transform without recalculation. `kabsch_exact_atom_mapping_v1` is backend-only, requires at least three non-collinear exact atom pairs, and fits only the pairs listed in the mapping artifact; it MUST NOT discover correspondence, substitute nearby atoms, or discard outliers unless an explicit rejected-pair list and producer parameter record are returned. No other alignment method is triggerable in v1.

Create `extensions/comparison/StructureComparisonExtension.tsx` with:

- explicit reference/mobile roles;
- overlay and side-by-side modes;
- camera link `off | orientation | full`;
- selection link `off | mapped_only`;
- matched/unmatched display from the supplied mapping;
- transform, mapping, method, parameters, and RMSD inspection;
- no selection propagation for unmapped or ambiguous residues;
- identical canonical residue identity regardless of overlay or side-by-side layout.

Camera linkage MUST be loop-free, owner-scoped, and generation-scoped. `orientation` links normalized view direction and up vectors only; `full` links the complete admitted `StructureCameraState` fields: mode, target, position, up, and radius.

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
  topologyFormat: 'gro' | 'pdb';
  format: 'xtc' | 'dcd';
  frameCount: number;
  frameMapArtifactId: string;
  frameMapSha256: string;
  frameMapFormat: 'bms.md.frame-map.v1+json';
  timeBasis: 'explicit_ps' | 'explicit_step';
  byteLength: number;
  rangeCapable: true;
  provenanceRef: string;
}

interface CoordinateTrajectoryFrameMapV1 {
  schema: 'bms.md.frame-map.v1';
  trajectoryId: string;
  replica: number;
  atomOrderIdentity: string;
  frames: Array<{
    displayFrame: number;
    sourceFrame: number;
    step?: number;
    timePs?: number;
  }>;
}
```

The only valid topology/trajectory pairs are `gro + xtc` and `pdb + dcd`; mixed pairs are invalid. The authoritative frame map MUST contain exactly `frameCount` records with contiguous zero-based `displayFrame`, unique nonnegative `sourceFrame`, and the manifest replica. For `explicit_ps`, every record has finite nondecreasing `timePs`; for `explicit_step`, every record has a nonnegative nondecreasing integer `step`. `timePs` may accompany explicit steps only when supplied by the producer. The frontend MUST NOT infer time from frame number, step, stride, or adjacent records. The v1 frame-map artifact is RFC 8785 canonical UTF-8 JSON, at most 1,000,000 records and 64 MiB; larger maps are refused pending a separately versioned paged format.

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
  interpolationMethod: 'linear_cartesian_exact_matched_atoms_v1';
  interpolationVersion: 1;
  interpolationSteps: number;
  provenanceRef: string;
  semanticLabel: 'visual_interpolation_not_physical_trajectory';
}
```

Morphs require exact matched atom identity and an integer `interpolationSteps` from 2 through 600 inclusive. Unmatched atoms MUST be omitted, held at an explicitly declared endpoint policy, or rendered separately; the choice must be visible. Morph export and playback MUST retain the nonphysical label. Morph frames MUST NOT enter trajectory analyses.

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

Extend `ViewerEventOrigin` with `ensemble | comparison | trajectory | volume | snapshot | export`. Evolve `ViewerEvent.documentId` and `ViewerEventInput.documentId` to `string | null`; collection-page, snapshot, and export events use null when no one document owns the event. Add `resourceId: string | null` to both shapes. Existing event names and origins remain source-compatible. Every new event still carries viewer ID, scene ID, generation, origin, payload, and ISO timestamp; consumers reject viewer/scene/generation mismatches.

M6 adds `volume-loaded`, `volume-presentation-changed`, `segment-selection-changed`, `snapshot-restore-state-changed`, and `export-state-changed`. Event payloads contain IDs, hashes, state enums, and canonical identities only—never engine objects, raw bytes, Blob URLs, credentials, or file paths.

Extend scene state with these exact serializable records:

```ts
interface CollectionBrowserStateV1 {
  schema: 'bms.viewer.collection-browser-state.v1';
  collectionId: string;
  manifestSha256: string;
  activeMemberId: string | null;
  residentMemberIds: string[];
  overlayMemberIds: string[];
  sort: {
    field: 'authoritative_order' | 'rank' | 'backend_coordinate' | 'metric';
    fieldId: string | null;
    direction: 'ascending' | 'descending';
  };
  filters: Array<{
    fieldId: string;
    operator: 'eq' | 'lt' | 'lte' | 'gt' | 'gte' | 'present';
    value: string | number | boolean | null;
  }>;
}

interface StructureComparisonStateV1 {
  schema: 'bms.viewer.comparison-state.v1';
  alignmentId: string;
  alignmentSha256: string;
  referenceDocumentId: string;
  mobileDocumentId: string;
  mode: 'overlay' | 'side_by_side';
  cameraLink: 'off' | 'orientation' | 'full';
  selectionLink: 'off' | 'mapped_only';
}
```

Resident and overlay member IDs are unique. Resident IDs occur in manifest order. Overlays are a subset of resident members; `overlayMemberIds` order is the explicit user-selected presentation order and respects admission limits. Runtime decoder buffers, page caches, in-flight requests, and Mol* state refs MUST NOT enter serializable scene state.

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
  format: 'ccp4';
  byteLength: number;
  dimensions: readonly [number, number, number];
  axisOrder: readonly [number, number, number];
  gridToWorldRowMajor4x4: readonly number[];
  coordinateUnits: 'Å';
  valueUnits?: 'e/Å³' | 'V' | 'kT/e' | 'dimensionless' | 'arbitrary';
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

Validation MUST require finite positive integer dimensions, 16 finite transform values, `axisOrder` as a permutation of `[0, 1, 2]`, hash, byte length, channel bounds, units, and finite statistics with `min <= max` and `sigma > 0` when supplied. Integer grid coordinates identify voxel centers; `gridToWorldRowMajor4x4` maps `[i, j, k, 1]` to world Å using the same column-vector convention as structure transforms. Density and electrostatic volumes require explicit `valueUnits`; `other_scalar` is unsupported in v1. Sigma contours are unavailable unless authoritative mean and sigma are supplied. A file header alone does not establish structure registration provenance.

### 6.2 Volume presentation state

Create the exact serializable contract:

```ts
interface VolumePresentationStateV1 {
  schema: 'bms.viewer.volume-presentation.v1';
  volumeId: string;
  channel: number;
  visible: boolean;
  opacity: number;
  contour: {
    mode: 'absolute' | 'sigma';
    value: number;
  };
  color: number;
  representation: 'isosurface' | 'slice';
  slice: {
    axis: 0 | 1 | 2;
    index: number;
  } | null;
  crop: {
    namespace: 'grid' | 'world';
    min: readonly [number, number, number];
    max: readonly [number, number, number];
  } | null;
  visibleSegmentIds: number[];
  registrationRef: string | null;
}
```

Opacity is finite in `[0, 1]`; color is packed 24-bit RGB; slice index is an in-bounds integer; crop bounds are finite and ordered componentwise. `visibleSegmentIds` must be empty for non-segmentation volumes. All controls MUST be declarative scene layers. Separate React effects MUST NOT independently create, clear, or repaint volume objects.

### 6.3 Structure-volume linkage

Structure and volume selection may synchronize only when supplied registration and mapping make the relationship exact. The frontend MAY focus a supplied mapped region but MUST NOT calculate correlation or registration.

Correlation coefficients, local-resolution values, fit scores, electrostatic samples, and segment/component associations are metric or mapping artifacts with their own producer, units, method, parameters, hashes, and missingness.

### 6.4 Segmentation contract

Create the exact contract:

```ts
interface VolumeSegmentationV1 {
  schema: 'bms.viewer.volume-segmentation.v1';
  segmentationId: string;
  volumeId: string;
  artifactId: string;
  artifactSha256: string;
  labels: Array<{
    segmentId: number;
    parentSegmentId: number | null;
    label: string | null;
    recommendedColor: number | null;
  }>;
  provenanceRef: string;
}
```

Segment IDs are unique nonnegative integers. Parent IDs must exist, must differ from the child, and the hierarchy must be acyclic. `recommendedColor` is a packed 24-bit RGB integer. Segment color is presentation metadata; segment meaning remains producer data. Null labels remain unknown and MUST NOT be guessed from nearby structures.

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

The exact outer contract is:

```ts
interface ViewerSnapshotBindingV2 {
  kind: 'document' | 'trajectory' | 'frame_map' | 'volume' | 'metric' |
    'mapping' | 'alignment' | 'segmentation' | 'analysis';
  resourceId: string;
  sha256: string;
  required: boolean;
  capabilityId?: string;
}

interface ViewerSnapshotV2 {
  schema: 'bms.viewer.snapshot.v2';
  schemaVersion: 2;
  snapshotId: string;
  capturedAt: string;
  engine: {
    package: 'molstar';
    engineVersion: '4.5.0';
    adapterId: 'bms-direct';
    adapterVersion: string;
  };
  requiredCapabilities: string[];
  bindings: ViewerSnapshotBindingV2[];
  scene: StructureSceneState;
  collectionState: CollectionBrowserStateV1 | null;
  comparisonState: StructureComparisonStateV1 | null;
  volumeStates: VolumePresentationStateV1[];
  uiComposition: 'standard' | 'compact';
  provenance: StructureSceneProvenance;
}
```

Bindings are unique by `kind + resourceId`; duplicate or conflicting hashes are invalid. Every scene resource is covered by exactly one binding. `requiredCapabilities` is unique and sorted for canonical output.

Restore MUST validate every required binding before mutating the live scene. Hash mismatch, unavailable required artifact, incompatible schema/adapter capability, or ambiguous identity returns a complete refusal report and leaves the current scene unchanged. Optional missing layers may be skipped only when the snapshot marked them optional and the user accepts a partial restore plan before application.

### 6.6 Export manifest

Every export creates an `ExportManifestV1` sidecar with:

- export ID, kind, timestamp, and workflow/job context; actor identity appears only when supplied by trusted server session metadata;
- scene/snapshot ID;
- semantic collection kind;
- source artifact IDs and hashes;
- engine/adapter versions;
- active member/frame/replica and transform/mapping IDs;
- visible layers, palettes, thresholds, contour/slice state, camera, selections, and measurements;
- metric/analysis provenance;
- export parameters and output SHA-256;
- semantic warning for morph or post-hoc analysis.

The exact outer contract is:

```ts
interface ExportManifestV1 {
  schema: 'bms.viewer.export-manifest.v1';
  exportId: string;
  kind: 'snapshot_json' | 'figure_png' | 'table_csv' | 'table_json' |
    'selection_mmcif' | 'trajectory_webm' | 'morph_webm';
  createdAt: string;
  jobId: string;
  workflowContext: Record<string, string | number | boolean | null>;
  actorId?: string;
  snapshotId: string;
  collectionKind: StructureCollectionKind;
  sourceBindings: ViewerSnapshotBindingV2[];
  engine: ViewerSnapshotV2['engine'];
  sceneStateSha256: string;
  exportParameters: Record<string, string | number | boolean | null>;
  outputFileName: string;
  outputMimeType: string;
  outputByteLength: number;
  outputSha256: string;
  semanticWarnings: string[];
}
```

Output filename is a basename with no path separators or control characters. MIME type is derived from `kind`, not accepted from free input. Morph exports require the warning `visual_interpolation_not_physical_trajectory`; trajectory exports forbid that warning.

Admitted v1 exports:

| Export | Format | Rule |
|---|---|---|
| Scene state | BMS snapshot JSON | canonical reproducibility artifact |
| Figure | PNG plus manifest JSON | no claim that pixels preserve underlying numeric data |
| Selection/residue/interface/interaction/measurement/metric tables | CSV and JSON plus manifest | canonical IDs, units, missingness, provenance retained |
| Selected coordinates | mmCIF plus manifest | only exact selected components/atoms; no identity broadening |
| Actual trajectory movie | WebM plus manifest | requires authoritative coordinate trajectory and source-frame range |
| Morph movie | WebM plus manifest | permanently labeled visual interpolation, not trajectory |

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

V1 hard safety ceilings are 4,096 voxels on any axis, 536,870,912 total voxels, 2 GiB encoded artifact bytes, 32 resident volume descriptors, and 4 simultaneously visible volumes. Capability-derived CPU/GPU budgets normally refuse below these ceilings and are authoritative. Crossing a hard or capability ceiling returns `VIEWER_ADMISSION_DENIED`; the viewer never downsamples scientific data silently.

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

## 12. Canonical API resources

### 12.1 Transport rules

All new M5/M6 resources are job-owned and live under `/api/jobs/{job_id}/viewer`. The API MUST resolve the authenticated job first and MUST NOT accept a filesystem path, arbitrary URL, owner ID, or alternate job ID inside a request body. IDs are opaque lowercase UUID strings; SHA-256 values are lowercase 64-character hexadecimal strings.

JSON responses use `application/json`. Binary content uses the authoritative allowlisted MIME type, `ETag: "{sha256}"`, `X-Content-Type-Options: nosniff`, and `Accept-Ranges: bytes`. Binary routes implement one byte range only, capped at 64 MiB per response. A valid range returns `206` and `Content-Range`; a complete response returns `200` only when the artifact is at most 64 MiB; larger artifacts require a range and return `412 VIEWER_RANGE_REQUIRED` without one. An invalid or unsatisfiable range returns `416` with `Content-Range: bytes */{size}`. Compression MUST NOT change byte-range identity.

The existing persisted-analysis routes remain authoritative for scientific analyses:

```text
GET  /api/designs/{design_id}/analyses/{analysis_type}
POST /api/designs/{design_id}/analyses/{analysis_type}
GET  /api/jobs/{job_id}/analyses/{analysis_type}
POST /api/jobs/{job_id}/analyses/{analysis_type}
GET  /api/analyses/{run_id}
```

M5/M6 MUST reuse `PersistedAnalysisRun<T>` and its existing states `missing | queued | running | completed | failed | cancelled | stale`. Viewer code admits `result` and analysis artifacts only when status is `completed`. `stale` is visible provenance, never a completed result.

The existing MD resources remain authoritative and are not duplicated:

```text
GET /api/jobs/{job_id}/md/summary
GET /api/jobs/{job_id}/md/artifacts
GET /api/jobs/{job_id}/md/analysis
GET /api/jobs/{job_id}/md/artifacts/{artifact_id}/content
```

`MDArtifact.content_url` is transport metadata only. Snapshots and manifests store `artifact_id` plus `sha256`, never `content_url`.

### 12.2 Collection resources

```text
GET /api/jobs/{job_id}/viewer/collections
GET /api/jobs/{job_id}/viewer/collections/{collection_id}
GET /api/jobs/{job_id}/viewer/collections/{collection_id}/members?cursor={cursor}&limit={1..200}
GET /api/jobs/{job_id}/viewer/artifacts/{artifact_id}/content
```

Collection list response:

```ts
interface StructureCollectionListResponseV1 {
  schema: 'bms.viewer.collection-list.v1';
  jobId: string;
  collections: Array<{
    collectionId: string;
    kind: StructureCollectionKind;
    memberCount: number;
    manifestSha256: string;
    label: string;
  }>;
}
```

Member page response:

```ts
interface StructureCollectionMemberPageV1 {
  schema: 'bms.viewer.collection-member-page.v1';
  collectionId: string;
  manifestSha256: string;
  members: StructureCollectionMemberV1[];
  nextCursor: string | null;
  complete: boolean;
}
```

The cursor is an opaque server token bound to job, collection, manifest hash, ordering, and page position. A cursor from an obsolete manifest returns `409 VIEWER_CURSOR_STALE`; the frontend clears only its page cache, refetches the manifest, and preserves the active scene when its bound member/hash still exists.

`limit` defaults to 100 and is capped at 200. The server returns members in `orderedMemberIds` order. Client sort/filter views retain each member's authoritative index and MUST NOT rewrite the manifest.

The shared artifact route serves structure, mapping, frame-map, volume, segmentation, and snapshot artifacts admitted by a viewer resource record. It MUST NOT become a generic job-directory file server. Existing MD artifact bytes continue through the MD content route.

### 12.3 Alignment and analysis resources

Alignment and mapping are persisted analysis products, not ad hoc viewer resources. The canonical analysis types are:

```text
structure_alignment
structure_difference
conformer_metrics
trajectory_frame_series
volume_registration
volume_correlation
```

Each completed result contains its v1 contract plus artifact references. `structure_alignment` request parameters contain only exact reference/mobile document IDs, exact selection descriptors, and an admitted method ID. A workflow that already supplies an authoritative alignment may register it as a completed analysis artifact; the frontend does not retrigger it.

### 12.4 Volume resources

```text
GET /api/jobs/{job_id}/viewer/volumes
GET /api/jobs/{job_id}/viewer/volumes/{volume_id}
GET /api/jobs/{job_id}/viewer/artifacts/{artifact_id}/content
```

Volume list response contains only bounded descriptors, never voxel arrays. M6B admits `format: 'ccp4'` only; `.ccp4`, `.map`, and `.mrc` names are accepted only when the parser identifies the CCP4/MRC family. DSN6, DX, cube, TIFF stacks, arbitrary NumPy arrays, and remote volume-server sessions remain unsupported in v1.

### 12.5 Snapshot resources

```text
GET    /api/jobs/{job_id}/viewer/snapshots?cursor={cursor}&limit={1..100}
POST   /api/jobs/{job_id}/viewer/snapshots
GET    /api/jobs/{job_id}/viewer/snapshots/{snapshot_id}
DELETE /api/jobs/{job_id}/viewer/snapshots/{snapshot_id}
```

Create body:

```ts
interface ViewerSnapshotCreateRequestV2 {
  schema: 'bms.viewer.snapshot-create.v2';
  label: string;
  snapshot: ViewerSnapshotV2;
  snapshotSha256: string;
}
```

The server rejects request bodies over 8 MiB, more than 10,000 bindings, more than 32 volume states, or duplicate bindings before persistence. It canonicalizes snapshot JSON with RFC 8785 JSON Canonicalization Scheme (JCS), recomputes SHA-256 over the canonical UTF-8 bytes, and rejects a mismatch. Snapshot labels are 1-120 Unicode characters after trimming. Snapshot rows contain job ID, creator identity from the authenticated request, schema version, hash, created time, and immutable JSON artifact identity. Updating in place is forbidden; recapture creates a new snapshot ID. Delete removes the user's reference according to existing retention policy and MUST NOT unlink source scientific artifacts.

### 12.6 Export resources

M6A PNG, CSV, JSON, mmCIF, and manifest generation is client-owned and immediate. The browser computes the output SHA-256 with Web Crypto and downloads the output plus `ExportManifestV1`; no server execution is needed.

M6D movie export is also client-owned because the authoritative rendered scene already lives in the browser. V1 does not add an export API, server renderer, or second Mol* owner. The current `StructureSceneController` generation owns capture, encoding, cancellation, hashing, and blob release.

The only M6D output codec is WebM/VP9 through a capability-proven browser encoder. MP4/H.264 is not part of v1 and MUST NOT be presented as available. Hard maxima are 1920×1080, 60 seconds, 60 fps, and 3,600 output frames; a lower runtime capability budget is authoritative. Requests exceeding any active limit return runtime refusal `VIEWER_EXPORT_LIMIT_EXCEEDED` before allocating capture or encoder state.

## 13. Lifecycle and transaction state machines

### 13.1 Resource application

Every structure, comparison, trajectory, volume, or snapshot application uses:

```text
idle → resolving → fetching → validating → applying → ready
                                 ↘ refused
            any nonterminal state → cancelled
            any nonterminal state → failed
            any state → disposed
```

Only the active scene generation may transition to `applying` or `ready`. `resolving`, `fetching`, and `validating` operate outside the live scene. `applying` stages engine mutations under one replacement transaction. Failure, cancellation, refusal, stale generation, or disposal before commit releases staged resources and leaves the previous ready scene unchanged.

`failed` means an unexpected implementation/runtime failure. `refused` means a deterministic contract, capability, identity, hash, format, or admission rejection. The UI MUST show the distinction.

### 13.2 Playback

```text
unavailable
idle → loading → paused ↔ playing
                    ↕
                  seeking
any active state → error
any active state → disposed
```

Playback controls are enabled only in `paused | playing`. Seek is latest-wins: a newer seek aborts decode/application of the older seek. `playing` advances only after the prior frame commit; it MUST NOT build an unbounded frame queue. End-of-range transitions to `paused` unless loop is enabled. Replica change constructs a new decoder generation before retiring the old ready static scene; it never mixes frame maps.

### 13.3 Snapshot restore

```text
idle → resolving → validating → ready_to_apply → applying → completed
                              ↘ confirmation_required
                              ↘ refused
any nonterminal state → cancelled | failed
```

`confirmation_required` is permitted only for optional bindings explicitly marked optional in the snapshot. The restore report lists every missing optional binding and resulting omitted layer. Confirmation is bound to snapshot hash plus report hash; any source change invalidates it. Required-binding failure is always `refused`.

### 13.4 Export

Static client export uses `preparing → hashing → completed | failed | cancelled`. Movie export uses `preparing → rendering → encoding → hashing → completed | failed | cancelled`. Terminal states are immutable. Cancellation is idempotent. A completed export has output bytes/hash and manifest bytes/hash or it is invalid and reported failed. No export state survives page closure in v1; reproducibility is carried by the downloaded manifest and source snapshot.

## 14. Error and refusal contract

All viewer API failures use:

```ts
interface ViewerApiErrorV1 {
  detail: {
    schema: 'bms.viewer.error.v1';
    code: ViewerErrorCode;
    message: string;
    resourceId?: string;
    retryable: boolean;
    context?: Record<string, string | number | boolean | null>;
  };
}
```

The context allowlist excludes URLs, filesystem paths, credentials, headers, tokens, and raw scientific payloads.

| Code | HTTP/runtime classification | Required behavior |
|---|---|---|
| `VIEWER_RESOURCE_NOT_FOUND` | 404 | retain scene; do not retry automatically |
| `VIEWER_RESOURCE_FORBIDDEN` | 403 | retain scene; reveal no existence/path detail |
| `VIEWER_CURSOR_STALE` | 409 | invalidate collection pages, refetch manifest |
| `VIEWER_STATE_CONFLICT` | 409 | retain scene; present current resource identity |
| `VIEWER_HASH_MISMATCH` | 412 | refuse application/export; never downgrade validation |
| `VIEWER_RANGE_REQUIRED` | 412 | refuse trajectory/large-volume load |
| `VIEWER_RANGE_INVALID` | 416 | correct request from authoritative byte length only |
| `VIEWER_REQUEST_TOO_LARGE` | 413 | do not schedule or allocate decoder/encoder state |
| `VIEWER_FORMAT_UNSUPPORTED` | 415 | report exact format and capability ID |
| `VIEWER_SCHEMA_UNSUPPORTED` | 422 | report supported schema versions |
| `VIEWER_IDENTITY_AMBIGUOUS` | 422 | fail closed; never broaden selection/mapping |
| `VIEWER_MAPPING_INCOMPLETE` | 422 | disable mapped-only operation for unmatched identity |
| `VIEWER_CAPABILITY_UNSUPPORTED` | runtime refusal | retain scene and show capability reason |
| `VIEWER_ADMISSION_DENIED` | runtime refusal | retain scene and show exceeded resource budget |
| `VIEWER_STALE_GENERATION` | internal cancellation | suppress user error; dispose stale work |
| `VIEWER_RESTORE_CONFIRMATION_REQUIRED` | 409 | wait for hash-bound optional-layer confirmation |
| `VIEWER_RESTORE_INCOMPATIBLE` | 422 | refuse restore without scene mutation |
| `VIEWER_EXPORT_LIMIT_EXCEEDED` | runtime refusal | refuse before allocating capture/encoder state |
| `VIEWER_EXPORT_FAILED` | terminal resource failure | retain scene; release encoder/output buffers |

No error path may silently load a different member, frame, model, assembly, volume, mapping, or snapshot.

## 15. Capability manifest and first admitted formats

Add these capability IDs to `molstarDirect45Capabilities.ts`; the default is `unsupported` until the named gate proves it:

```text
collection-pagination-v1
static-overlay-v1
comparison-transform-v1
comparison-side-by-side-v1
comparison-camera-link-v1
trajectory-gro-xtc-v1
trajectory-pdb-dcd-v1
morph-linear-exact-atoms-v1
snapshot-v2
export-png-v1
export-table-v1
export-mmcif-v1
volume-ccp4-v1
volume-slice-v1
volume-segmentation-v1
export-webm-v1
```

The initial M5C trajectory pairing is job-owned GRO topology plus XTC coordinates through the existing MD artifact route. PDB plus DCD remains a separate later capability and MUST NOT be implied by GRO/XTC success.

The initial morph method is `linear_cartesian_exact_matched_atoms_v1`. It linearly interpolates only exact atom pairs supplied by the mapping artifact after applying the supplied alignment transform. Unmatched atoms use policy `endpoint_separate`, rendered separately at their endpoint and excluded from interpolation. The UI and exports display `visual_interpolation_not_physical_trajectory` at all times.

The initial M6B volume capability is single-channel CCP4/MRC scalar data with isosurface and orthogonal slice presentation. Multi-channel volumes, arbitrary oblique slices, segmentation, and structure-volume linkage remain separately gated even when Mol* can display some of them internally.

Capability state is one of `supported | unsupported | experimental`. Production controls render only `supported`. `experimental` may appear only under the existing explicit experimental/debug surface and cannot be stored as a required snapshot binding.

### 15.1 Required engine boundary

M5/M6 extend the sole `MolstarEngineAdapter` boundary; components MUST NOT call plugin APIs directly. The required additions are:

```ts
interface MolstarEngineAdapter {
  applyDocumentTransform(
    documentId: string,
    transformRowMajor4x4: readonly number[],
    signal: AbortSignal,
  ): Promise<ViewerResult<void>>;
  readCamera(): ViewerResult<StructureCameraState>;
  applyCamera(
    camera: StructureCameraState,
    mode: 'orientation' | 'full',
    signal: AbortSignal,
  ): Promise<ViewerResult<void>>;
  loadTrajectory(
    manifest: CoordinateTrajectoryManifestV1,
    frameMap: CoordinateTrajectoryFrameMapV1,
    signal: AbortSignal,
  ): Promise<ViewerResult<void>>;
  unloadTrajectory(signal: AbortSignal): Promise<ViewerResult<void>>;
  loadMorph(morph: MorphVisualizationV1, signal: AbortSignal): Promise<ViewerResult<void>>;
  setMorphStep(step: number, signal: AbortSignal): Promise<ViewerResult<void>>;
  unloadMorph(signal: AbortSignal): Promise<ViewerResult<void>>;
  loadVolume(descriptor: SpatialVolumeDescriptorV1, signal: AbortSignal): Promise<ViewerResult<void>>;
  setVolumePresentation(state: VolumePresentationStateV1, signal: AbortSignal): Promise<ViewerResult<void>>;
  removeVolume(volumeId: string, signal: AbortSignal): Promise<ViewerResult<void>>;
  capturePng(signal: AbortSignal): Promise<ViewerResult<Blob>>;
  exportSelectionMmcif(signal: AbortSignal): Promise<ViewerResult<Blob>>;
}
```

The existing `selectMDSourceFrame`, `setMDPlayback`, scene load/reconcile, diagnostics, click subscription, and disposal methods remain. New methods use the controller's active generation and `AbortSignal`; a stale or aborted call cannot mutate the plugin. Side-by-side mode uses two independently owned workbench/controller instances connected only by scoped camera/selection events; it does not put two plugins behind one adapter.

## 16. Backward and forward compatibility

1. Existing static viewers and M0-M4 scenes require no migration.
2. `ViewerSnapshot` v1 remains readable. Restore converts it in memory to v2 with no trajectory/volume/collection bindings, validates current document hashes, and never rewrites the source artifact.
3. Snapshot v2 writers never emit v1.
4. A schema version greater than supported returns `VIEWER_SCHEMA_UNSUPPORTED`; it is never partially interpreted.
5. Within a supported schema version, unknown optional object keys are ignored for behavior. Required extension data must be represented by a named binding and capability ID, not an unknown key.
6. Required bindings include `required: true`; optional bindings include `required: false`. Absence of the flag in v2 is invalid.
7. `PersistedAnalysisRun<T>` remains the only frontend scientific-analysis envelope. M5/M6 do not create a second run-state model.
8. Existing `MDArtifact` and MD report schemas remain unchanged. The trajectory manifest adapts them and adds a separate hash-bound frame-map artifact; it does not reinterpret analysis point array position as frame identity.
9. Existing result routes may adapt authoritative workflow outputs into collection descriptors. They may not invent order semantics or duplicate structure bytes.
10. Removing support for a previously written required capability requires either a compatible adapter or an explicit snapshot refusal; silent degradation is forbidden.

No database rewrite is required for M5A/B. M6A adds immutable snapshot metadata/artifact records. M6D adds no database or backend worker; its bounded browser-owned export ends at explicit file downloads.

## 17. Exact implementation tranches and exit evidence

### 17.1 M5A — collection foundation

**Create/modify:**

- `platform/frontend/src/structureViewer/contracts/structureCollections.ts`;
- `platform/frontend/src/structureViewer/extensions/ensemble/EnsembleBrowserExtension.tsx`;
- `platform/frontend/src/structureViewer/StructureViewerHost.tsx`;
- `platform/frontend/src/structureViewer/StructureWorkbench.tsx`;
- `platform/frontend/src/structureViewer/runtime/StructureSceneController.ts`;
- `platform/frontend/src/lib/api.ts`;
- `platform/api/routers/viewer_resources.py`;
- `platform/api/services/viewer_resources.py`;
- `platform/api/services/viewer_resource_contracts.py`;
- focused backend contract tests and frontend collection/identity tests.

**Exit evidence:** exact schema fixtures; stable page order over repeated fetches; stale cursor behavior; 1/100/200 member pages; active member retained across filter/sort; overlay add/remove/reorder; admission refusal preserves scene; nonprotein identity; unmount leak check; production build import/mount.

### 17.2 M5B — comparison

**Create/modify:** `structureComparison.ts`, `extensions/comparison/*`, metric registry dimensions, scene/controller/adapter transform and camera APIs, persisted analysis schemas/producers, and focused mapping/transform/linkage tests.

**Exit evidence:** exact 4×4 transform validation; mapping hash mismatch refusal; matched-only bidirectional selection; unmatched no-op; camera-link loop suppression; overlay/side-by-side identity equivalence; A→B→A comparison plateau.

### 17.3 M6A — reproducibility and immediate exports

**Create/modify:** snapshot v2 contracts/migration, `extensions/export/*`, snapshot API schemas/routes/service/storage, canonical JSON helper, and export manifest builders.

**Exit evidence:** v1 read compatibility; v2 deterministic hash; required-binding refusal; optional-binding hash-bound confirmation; no-mutation failed restore; PNG/CSV/JSON/mmCIF plus manifest/output hashes; canonical IDs/units/missingness; no secrets/paths/signed URLs in fixtures.

### 17.4 M5C — one real trajectory lane

**Create/modify:** trajectory manifest/frame-map contracts, `extensions/trajectory/*`, worker/decoder ownership, adapter capability, existing MD route integration, and range/frame-map/cancellation tests. Package changes require explicit gate-local authorization.

**Exit evidence:** exact GRO+XTC fixture; topology/trajectory/frame-map hashes; atom-order mismatch refusal; source frame/time round-trip; rapid seeks latest-wins; play/pause/end/loop; replica replacement; malformed/truncated XTC refusal; range `200/206/412/416`; decoder/worker/buffer plateau in dev and exact production build.

### 17.5 M5D — supplied differences and morph

**Create/modify:** conformer/frame metric dimensions, `morphVisualization.ts`, morph UI/adapter path, supplied analysis adapters, and semantic-label export tests.

**Exit evidence:** no browser scientific computation; exact mapped atoms only; unmatched endpoint policy visible; morph label always present; morph frames rejected by trajectory-analysis adapters; cancellation and replacement plateau.

### 17.6 M6B — scalar CCP4/MRC

**Create/modify:** `spatialVolumes.ts`, `extensions/volumes/*`, controller/adapter volume operations, volume API descriptor adaptation, and format/transform/admission/lifecycle tests.

**Exit evidence:** one exact density fixture and one exact electrostatic fixture; hash/transform/unit validation; absolute and valid sigma contours; orthogonal slices; structure replacement without volume leakage; unsupported format refusal; GPU/CPU resource plateau in dev and production.

### 17.7 M6C — segmentation and linkage

**Create/modify:** segmentation/mapping contracts, supplied registration/association analysis adapters, segment UI, and exact linkage tests.

**Exit evidence:** unknown labels remain unknown; segment hierarchy/visibility round-trip; registration hash mismatch refusal; exact mapped focus only; no client fitting/correlation; snapshot/export provenance.

### 17.8 M6D — WebM export

**Create/modify:** `extensions/export/*`, scene-controller capture ownership, WebM capability UI, cancellation/blob cleanup, manifest hashing, and bounded browser-encoder tests.

**Exit evidence:** all request limits; pre-allocation refusal; cancel rendering/encoding; one trajectory and one morph export; source frame range; permanent morph warning; output and manifest hashes; no partial completed result; encoder/buffer/blob cleanup.

### 17.9 Canonical verification commands

Each tranche adds focused files under:

```text
platform/api/tests/test_viewer_resources.py
platform/api/tests/test_viewer_snapshots.py
platform/frontend/tests/structureViewerCollections.test.ts
platform/frontend/tests/structureViewerComparison.test.ts
platform/frontend/tests/structureViewerSnapshotExport.test.ts
platform/frontend/tests/structureViewerTrajectory.test.ts
platform/frontend/tests/structureViewerVolumes.test.ts
platform/frontend/tests/structureViewerLifecycle.test.ts
```

Run the applicable focused backend tests from repository root:

```bash
pytest -q platform/api/tests/test_viewer_resources.py platform/api/tests/test_viewer_snapshots.py
```

Run the applicable focused frontend contracts from `platform/frontend`:

```bash
pnpm exec tsx --test \
  tests/structureViewerCollections.test.ts \
  tests/structureViewerComparison.test.ts \
  tests/structureViewerSnapshotExport.test.ts \
  tests/structureViewerTrajectory.test.ts \
  tests/structureViewerVolumes.test.ts \
  tests/structureViewerLifecycle.test.ts
```

Run the production build without writing into deployed `dist/`:

```bash
BUILD_DIR="$(mktemp -d /tmp/bms-viewer-build.XXXXXX)"
BMS_FRONTEND_BUILD_OUT_DIR="$BUILD_DIR" pnpm run build
rm -rf -- "$BUILD_DIR"
```

The browser gate uses the exact built assets, a real supported artifact fixture, console capture, and controller diagnostics. It records source commit, Mol* version, adapter version, browser version, fixture hashes, capability IDs, load/replacement sequence, final diagnostics, and console errors. A mocked adapter does not satisfy runtime acceptance.

## 18. Definition of finished specification and implementation

This document is specification-complete when all of the following are true:

- every M5/M6 resource has identity, provenance, transport, lifecycle, error, compatibility, and owner semantics;
- first admitted trajectory, morph, volume, and movie formats are fixed;
- there are no unresolved markers or product alternatives;
- unsupported capabilities and deferred formats are explicit;
- every tranche names exact files/surfaces and exit evidence;
- Conformational Mapping Phase 13 remains separately authorized.

An implementation tranche is complete only when its source, contract fixtures, focused tests, exact production build, browser/runtime exercise, lifecycle/resource evidence, and scoped commit all pass. Source presence, a dev-only screenshot, or an engine-internal capability alone is not acceptance. Broad unrelated frontend/backend failures are reported separately and do not justify weakening a tranche's focused gates.

No implementation tranche may silently alter the scientific or identity contracts frozen here. A necessary contract change first updates this document with a schema/version and compatibility decision in a separate reviewed commit.
