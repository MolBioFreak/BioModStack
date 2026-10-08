# BioModStack Structure Visualization Platform Contract v1

**Document ID:** `bms_structure_visualization_contract_v1`
**Status:** Proposed architecture contract; implementation is not yet authorized by this document.
**Date:** 2026-07-18
**Primary engine:** Mol* through a BioModStack-owned adapter.
**Current production constraint:** the frontend deliberately resolves `pdbe-molstar-stable` 3.3.0 because the previously attempted newer runtime caused Chromium renderer crashes.
**Related authority:** `docs/specs/conformational_mapping/cm_contract_definitions_v1.md` remains authoritative for Conformational Mapping identity and scientific semantics.

## 1. Purpose

BioModStack uses molecular structure visualization in structure prediction, antibody design, mutagenesis, docking, epitope/CDR selection, local redesign, result review, and conformational-analysis workflows. Those consumers must not each create their own Mol*/PDBe lifecycle, identity, coloring, filtering, or event conventions.

This contract defines one harmonized platform for:

1. viewer loading, readiness, replacement, teardown, and error handling;
2. canonical structure, entity-instance, residue, atom, and ensemble identity;
3. deterministic visual layers and filters;
4. AI/ML metric registration, projection, legends, missingness, and provenance;
5. complex, interface, interaction, conformational, and volume extensions;
6. linked 1D sequence, 2D charts/matrices/networks, and 3D selections;
7. governed extension and release processes;
8. feature-parity evaluation without exposing third-party internals to workflows.

The platform is a scientific presentation and interaction layer. It must not manufacture scientific values, convert independent predictions into physical trajectories, or hide ambiguity in structure identity.

## 2. Normative language and scope

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative.

This contract governs every frontend structure-viewer consumer. It does **not** by itself authorize:

- the separately gated Conformational Mapping Phase 13 consumer;
- a Mol*/PDBe Molstar version upgrade;
- backend schema, workflow, scoring, persistence, or API changes;
- browser-side scientific analysis that belongs in a versioned backend tool;
- modification of unrelated dirty work.

## 3. Architecture invariants

### 3.1 One runtime boundary

Only a BioModStack engine adapter may import `pdbe-molstar`, create `<pdbe-molstar>`, access `viewerInstance`, or access the underlying Mol* plugin. Workflow components MUST NOT use these APIs directly.

The intended layering is:

```text
Workflow page/template
    -> StructureWorkbench
        -> ViewerExtensionRegistry
        -> StructureSceneController
            -> MolstarEngineAdapter
                -> pinned PDBe Molstar / Mol* runtime
```

| Layer | Owns | Must not own |
|---|---|---|
| Workflow consumer | Domain data, permissions, workflow-specific copy | Viewer lifecycle, third-party APIs, global viewer events |
| `StructureWorkbench` | Shared panels, toolbar, legends, filters, linked views, accessibility | Scientific calculations or runtime internals |
| Extension registry | Typed feature registration and activation | Unscoped DOM mutation or private plugin access |
| Scene controller | Declarative scene reconciliation, selections, layers, camera, events | React page state or API fetching policy |
| Engine adapter | Exact runtime capability translation and disposal | Domain semantics or workflow-specific assumptions |

### 3.2 One workbench, multiple compositions

BMS MAY expose compact, standard, comparison, and analysis layouts, but they MUST compose the same host, controller, contracts, and extension registry. “Epitope viewer,” “quick viewer,” and “conformational viewer” are workbench configurations, not independent runtime implementations.

### 3.3 Declarative state

A serializable BMS scene state is authoritative. The controller reconciles that state into the engine. React `key` changes MUST NOT be the normal mechanism for changing coloring, filters, overlays, models, or conformers.

### 3.4 No silent capability broadening

If the pinned runtime cannot represent a requested identity or operation exactly, the adapter MUST return an explicit `unsupported` or `ambiguous` result. It MUST NOT silently broaden an insertion-coded, model-specific, operator-specific, altloc-specific, or instance-specific request.

## 4. Runtime and version policy

1. Production version resolution MUST be established from package declaration, lockfile, bundler aliases, and runtime import resolution.
2. The current stable 3.3.0 alias remains pinned until a separately reviewed browser/Electron probe clears a replacement.
3. Current upstream documentation is not proof of the pinned runtime contract.
4. Private or underlying plugin APIs MAY be used only inside the engine adapter when:
   - no public wrapper API exists;
   - the exact installed implementation has been inspected;
   - the capability is recorded in the manifest;
   - compatibility and teardown probes cover it;
   - unsupported versions fail closed.
5. Every runtime exposes a generated capability manifest:

```ts
interface StructureViewerCapabilityManifest {
  engine: 'molstar';
  wrapper: 'pdbe-molstar' | 'direct-molstar';
  resolvedVersion: string;
  adapterVersion: string;
  capabilities: Record<StructureViewerCapability, 'supported' | 'partial' | 'unsupported'>;
  identityLimits: string[];
  privateApiUses: Array<{ path: string; reason: string }>;
}
```

6. Tests and UI copy MUST reference the production-resolved runtime, not an aspirational version.

## 5. Canonical structure identity

### 5.1 Document and scene identity

Every loaded artifact MUST have a BMS-owned identity independent of URL or filename.

```ts
interface StructureDocumentRef {
  documentId: string;
  sourceKind: 'pdb' | 'mmcif' | 'bcif' | 'sdf' | 'mol2' | 'trajectory' | 'volume';
  contentSha256?: string;
  sourceUrl?: string;
  candidateId?: string;
  provenanceRef?: string;
}

interface StructureSceneRef {
  viewerId: string;
  sceneId: string;
  generation: number;
}
```

URLs and blob URLs are transport details. They are not stable scientific identity.

### 5.2 Entity-instance identity

Repeated copies of the same entity MUST remain distinguishable.

```ts
interface EntityInstanceRef {
  documentId: string;
  modelId?: string;
  entityId?: string;
  sourceEntityId?: string;
  sourceInstanceId?: string;
  labelAsymId?: string;
  authAsymId?: string;
  assemblyId?: string;
  operatorInstanceId?: string;
}
```

An entity-level identifier MUST NOT be used as a substitute for an instance identifier when an assembly contains repeated copies.

### 5.3 Residue identity

```ts
interface ResidueRef extends EntityInstanceRef {
  labelSeqId?: number;
  authSeqId?: number;
  insertionCode?: string;
  componentId?: string;
  altLoc?: string;
}
```

Rules:

1. A residue MUST provide one complete supported label or author namespace.
2. `A45` and other concatenated compatibility keys are forbidden in new contracts.
3. `A:45` remains a compatibility string only when its namespace is explicitly declared.
4. Multi-character chain IDs, negative author residue numbers, insertion codes, multiple models, altlocs, assembly operators, and repeated instances MUST be retained.
5. Mapping between source, normalized structure, API, metric, and viewer identities MUST be explicit and provenance-bound.
6. If exact selection is unsupported by the engine, BMS MUST display the limitation and refuse the exact action rather than selecting a broader residue set.

### 5.4 Atom identity

```ts
interface AtomRef extends ResidueRef {
  labelAtomId?: string;
  authAtomId?: string;
  element?: string;
  atomIndex?: number;
}
```

An engine-local atom index MAY be cached for a loaded generation but MUST NOT cross document or generation boundaries.

## 6. Structure collections and conformational semantics

Every multi-structure scene MUST declare its scientific relationship:

```ts
type StructureCollectionKind =
  | 'independent_hypotheses'
  | 'experimental_ensemble'
  | 'coordinate_trajectory'
  | 'interpolated_morph'
  | 'matched_state_series'
  | 'static_complex_components';
```

| Kind | Permitted UI language | Forbidden implication |
|---|---|---|
| `independent_hypotheses` | candidate, prediction, generated conformer | time, transition, equilibrium, free-energy path |
| `experimental_ensemble` | model, member, experimental ensemble | ordered dynamics unless supplied by source |
| `coordinate_trajectory` | frame, time/step when provided | independent model ranking |
| `interpolated_morph` | visual interpolation | physical trajectory or mechanism |
| `matched_state_series` | state, condition, ordered comparison | continuous dynamics unless demonstrated |
| `static_complex_components` | component, chain, ligand, assembly | conformational sequence |

The controller MUST preserve candidate/frame ordering from the authoritative contract. It MUST NOT infer time from array order.

## 7. Metric and annotation model

### 7.1 Metric dimensions

BMS MUST distinguish at least:

- `atom_scalar`;
- `residue_scalar`;
- `residue_categorical`;
- `residue_vector`;
- `residue_pair_matrix`;
- `chain_scalar`;
- `chain_pair_scalar`;
- `interface_scalar`;
- `structure_scalar`;
- `conformer_scalar`;
- `frame_series`;
- `spatial_volume`;
- `geometry_annotation`.

A numeric result is not automatically residue-colorable.

### 7.2 Metric descriptor

```ts
interface ViewerMetricDescriptor {
  metricId: string;
  displayName: string;
  semanticType: string;
  dimension: ViewerMetricDimension;
  units?: string;
  direction: 'higher_better' | 'lower_better' | 'unsigned' | 'categorical';
  domain?: { min?: number; max?: number; categories?: string[] };
  missingPolicy: 'explicit' | 'not_applicable' | 'unsupported';
  projectionPolicy: 'direct' | 'uniform_structure' | 'selected_slice' | 'none';
  normalization: 'none' | 'fixed_domain' | 'dataset_minmax' | 'percentile' | 'zscore';
  paletteId: string;
  provenanceRef: string;
  producerVersion?: string;
  inputHash?: string;
  description?: string;
}
```

Every displayed layer MUST retain metric value, units, direction, missingness, provenance, and identity. RGB values are derived presentation output and MUST NOT become the metric contract.

### 7.3 AI/ML metric rules

Examples of required handling:

| Metric class | Correct viewer treatment |
|---|---|
| pLDDT/local confidence | `residue_scalar`, fixed documented domain, explicit missing residues |
| PAE/contact probability/distance-error matrix | `residue_pair_matrix`; linked 2D matrix and 3D pair/region selection, never flattened into a residue profile without a named reduction |
| Boltz-2 interface confidence | `interface_scalar` or `chain_pair_scalar`; use **ipSAE** for BMS Boltz-2 quality reporting and do not substitute iPTM |
| Affinity/global ranking score | `structure_scalar` or `conformer_scalar`; ranking/labels, not fake per-residue color |
| FrustraMPNN landscape | residue-by-mutation data; a selected mutation or explicit reduction may create a labeled residue slice, but raw values are not ΔΔG/free energy |
| Conservation/disorder/SASA/B-factor | `residue_scalar` with source-specific units and provenance |
| Epitope/CDR/pocket/secondary-structure class | `residue_categorical` or geometry annotation |
| Clash/contact/hydrogen bond/restraint | `geometry_annotation` with endpoints, type, threshold, and source |
| Density/electrostatic potential | `spatial_volume` with grid transform, units, contour/range, and source |
| RMSF or per-state displacement | `residue_scalar` or `frame_series` only after a versioned backend analysis and explicit alignment/mapping |

Browser code MAY perform display-only operations such as thresholding, palette lookup, deterministic slicing, visibility filtering, and camera transforms. Scientific reductions, alignment policies, interface scoring, clustering, PCA/tICA, energetics, and confidence derivation belong in versioned, testable services unless a separately reviewed contract says otherwise.

### 7.4 Missingness

Missing, unsupported, ambiguous, filtered-out, and not-applicable are distinct. Zero MUST NOT represent any of them. Legends and tooltips MUST expose the distinction.

## 8. Visual layers and deterministic composition

A scene is composed from ordered layers rather than independent effects that clear and repaint the viewer.

```ts
interface ViewerLayer {
  layerId: string;
  kind: 'base-representation' | 'metric' | 'annotation' | 'interaction' | 'selection' | 'comparison' | 'volume';
  priority: number;
  visibility: boolean;
  opacity?: number;
  blendMode?: 'replace' | 'overlay' | 'outline';
  provenanceRef?: string;
}
```

Default precedence, low to high:

1. structure/component visibility and base representation;
2. base chain/entity/component coloring;
3. active metric layer;
4. categorical or geometric annotations;
5. comparison/difference layer;
6. persistent named selection;
7. current selection;
8. hover/focus;
9. safety or validation alerts such as severe clashes.

One reconciler MUST apply the complete desired layer transaction. Independent React effects MUST NOT race by each clearing color or selection state.

## 9. Filtering and selection

### 9.1 Shared filter state

Every workbench configuration uses one serializable filter state supporting:

- document, candidate, model, or frame;
- assembly and symmetry/operator instance;
- entity type: protein, DNA, RNA, ligand, glycan, ion, water, other;
- entity, chain, repeated instance, component, residue range, atom, altloc;
- representation and visibility;
- named selection sets;
- metric identity, value range, percentile, category, missingness, and threshold direction;
- interface pair and buried-area threshold when supplied;
- interaction type and distance/score threshold when supplied;
- neighborhood radius around a selection;
- aligned/matched/unmatched residue status;
- conformer cluster, rank, backend coordinate, or analysis status;
- volume channel, contour, slice, and opacity.

### 9.2 Selection algebra

Selections MUST support typed `and`, `or`, `not`, set membership, ranges, and neighborhood operations over canonical references. Engine-specific selection strings MAY be generated by the adapter but MUST NOT be stored as the only BMS selection representation.

### 9.3 Linked views

The same selection state SHOULD drive:

- 3D structure;
- sequence/annotation tracks;
- metric tables and legends;
- PAE/contact/distance matrices;
- interface networks;
- conformer/state tables and charts.

Every event MUST include scene, document, generation, and origin so multiple simultaneous viewers cannot consume each other’s clicks.

## 10. Controller command and event contract

The public controller SHOULD expose typed operations such as:

```ts
interface StructureSceneController {
  loadScene(state: StructureSceneState): Promise<ViewerResult>;
  reconcileScene(state: StructureSceneState): Promise<ViewerResult>;
  setSelection(selection: StructureSelection): Promise<ViewerResult>;
  setHover(selection?: StructureSelection): Promise<ViewerResult>;
  focus(selection: StructureSelection): Promise<ViewerResult>;
  setCamera(camera: CameraState): Promise<ViewerResult>;
  captureSnapshot(): Promise<ViewerSnapshot>;
  dispose(): Promise<void>;
  subscribe(handler: (event: ViewerEvent) => void): () => void;
}
```

Required events include:

- `runtime-ready`;
- `scene-loading`, `scene-ready`, `scene-error`;
- `selection-changed`, `hover-changed`, `focus-changed`;
- `frame-changed`, `candidate-changed`;
- `camera-changed`;
- `measurement-created`, `measurement-removed`;
- `layer-changed`, `filter-changed`;
- `capability-unsupported`;
- `disposed`.

Events MUST be scoped to the owning viewer and generation. Document-global Mol* events are forbidden outside the engine adapter and MUST be provenance-filtered there if unavoidable.

## 11. Lifecycle and ownership

For each host generation, exactly one controller owns:

- runtime/plugin instance;
- custom-element host and shadow-DOM hooks;
- structure and volume objects;
- overlay models and representations;
- blob/object URLs;
- event and subscription handles;
- timers, readiness polls, workers, and abort controllers;
- WebGL canvas/context;
- temporary measurements and snapshots.

Rules:

1. Use runtime load-complete events or promises when available; bounded polling is a documented fallback, never an arbitrary sleep.
2. Every asynchronous operation carries an owner generation or abort signal. Late work MUST NOT mutate a replaced scene.
3. Replacing URL A with B retires A’s scene resources before B becomes authoritative.
4. Overlay add/remove/reorder MUST reconcile explicitly.
5. Cleanup MUST be idempotent without disposing a still-live StrictMode-probed host.
6. Wrapper disconnect and plugin disposal are distinct and both must be verified against the exact runtime.
7. Repeated lifecycle probes MUST plateau in live plugin count, canvases/WebGL contexts, listeners, timers, blobs, detached nodes, and renderer memory.

## 12. Extension model

Every optional capability is a registered extension:

```ts
interface StructureViewerExtensionManifest {
  extensionId: string;
  version: string;
  title: string;
  requiredCapabilities: StructureViewerCapability[];
  supportedMetricDimensions?: ViewerMetricDimension[];
  uiSlots: Array<'toolbar' | 'left-panel' | 'right-panel' | 'bottom-panel' | 'canvas-overlay'>;
  scientificContractRef?: string;
  featureFlag?: string;
}
```

An extension receives only a typed BMS context: controller, scene state, selection state, metric registry, service clients, and event bus. It MUST NOT:

- create its own viewer runtime;
- attach unscoped document-global listeners;
- mutate another extension’s representations directly;
- use undocumented engine internals;
- calculate unstated scientific values;
- redefine residue identity;
- bypass feature flags, permissions, or provenance.

Initial extension families:

- metric and annotation layers;
- sequence/track view;
- PAE and pair-matrix view;
- measurements;
- interactions and clashes;
- complex/interface analysis;
- conformer/ensemble comparison;
- trajectory/frame playback;
- volume/density/electrostatics;
- snapshots, figures, and export.

## 13. Shared workbench layouts

All layouts share the same state and contracts.

| Layout | Required behavior |
|---|---|
| Compact | Structure canvas, minimal controls, selection events, status/error indicator |
| Standard | Representations, component filters, metric/annotation legend, sequence linkage, measurements |
| Comparison | Two or more synchronized or independently controllable scenes, explicit alignment transform and reference |
| Analysis | Standard capabilities plus matrices, interface network, ensemble/state browser, plots, provenance, exports |

Mobile/touch behavior MUST be first-class. Hiding controls MUST NOT remove keyboard-accessible alternatives for essential operations.

## 14. Feature-admission process

No new workflow-specific viewer feature may be added without the following artifacts:

1. **Capability proposal**
   - user/scientific question;
   - data source and authoritative identity;
   - metric dimension or geometry type;
   - required engine capabilities;
   - expected filtering and linked-view behavior;
   - missingness and unsupported behavior;
   - performance envelope;
   - security and provenance considerations.
2. **Contract update**
   - typed data and event shapes;
   - exact semantic labels and forbidden claims;
   - compatibility and migration impact.
3. **Adapter proof**
   - exact production-runtime source/type evidence;
   - compile-time boundary tests;
   - fail-closed unsupported tests.
4. **RED tests before implementation**
   - identity and scientific semantics;
   - deterministic layer/filter behavior;
   - lifecycle/replacement;
   - multi-viewer event scoping;
   - accessibility and touch where applicable.
5. **Pilot** in one designated workbench configuration.
6. **Migration review** for every affected consumer.
7. **Independent post-implementation review** covering frontend architecture, scientific contract, and runtime/resource behavior.

A feature is not complete because it renders once. It is complete only after focused gates, whole-frontend gates, and the production-runtime browser/Electron probe pass.

## 15. Test and acceptance matrix

### 15.1 Contract tests

- label and author residue namespaces;
- multi-character chain IDs;
- insertion codes and negative author residue numbers;
- repeated entity instances and assembly operators;
- models, altlocs, ligands, nucleic acids, and missing backbone atoms;
- explicit refusal when identity cannot be represented;
- metric dimensions, projection policies, missingness, units, direction, provenance;
- collection-kind language and candidate/frame ordering.

### 15.2 Scene tests

- deterministic layer precedence;
- metric/filter replacement without stale visuals;
- named selection and hover isolation;
- overlay add/remove/reorder;
- A → B → A document replacement;
- synchronized and independent comparison cameras;
- late-load cancellation.

### 15.3 Real-runtime lifecycle tests

Use the actual production-resolved viewer under React StrictMode and Electron/Chromium where applicable. Exercise at least 50 bounded cycles of:

- mount/unmount and reconnect;
- document replacement;
- overlay replacement;
- metric and selection replacement;
- multiple simultaneous viewers;
- blob URL replacement;
- delayed completion after replacement/unmount.

Record and require plateaus for plugin count, canvases/WebGL contexts, listeners, timers, workers, blobs, detached nodes, console errors, and renderer memory.

### 15.4 Migration exit criteria

- zero direct `<pdbe-molstar>` creation outside the engine adapter;
- zero direct `viewerInstance` or plugin access outside the adapter;
- zero independent structure loader implementations;
- zero unscoped document-global viewer click consumers;
- all generic and epitope/CDR render sites use the shared platform;
- no concatenated residue key in a new contract;
- pinned-runtime capability manifest and probes are green;
- focused and canonical frontend test/typecheck/lint/build gates are green;
- no target workflow behavior regression.

## 16. Performance and safety

1. Large structures, ensembles, pair matrices, and trajectories MUST have explicit admission and virtualization limits.
2. The UI MUST not load every conformer, frame, matrix cell, interaction, or atom annotation eagerly.
3. Scene updates SHOULD be transactional and diff-based.
4. Work SHOULD move off the main thread only through owned, cancellable workers.
5. External structure/volume URLs MUST follow existing BMS authentication and content-security rules.
6. Viewer errors MUST be bounded, user-visible, and include runtime/adapter versions and scene identity in diagnostics without leaking secrets.

## 17. External capability evidence informing this contract

Authoritative documentation reviewed 2026-07-18:

- Mol* plugin state/interactivity: selections and Loci, persistent selection, focus interactions, camera focus, state-managed representations — <https://molstar.org/docs/plugin/viewer-state/>
- Mol* superposition, custom trajectories, interactions extension, viewer measurements, volume streaming, assemblies/symmetry, and sessions — <https://molstar.org/docs/plugin/superposition/>, <https://molstar.org/docs/plugin/transforms/custom-trajectory/>, <https://molstar.org/docs/extensions/interactions/>, <https://molstar.org/viewer-docs/>
- ChimeraX structural matching, RMSD, morph interpolation, coordinate-set playback, clashes/contacts, buried-SASA interfaces, AlphaFold PAE, and volumes — <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/matchmaker.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/morph.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/coordset.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/clashes.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/interfaces.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/alphafold.html>, <https://www.cgl.ucsf.edu/chimerax/docs/user/commands/volume.html>
- NGL representations, selection language, contact/distance representations, and trajectory loading — <https://nglviewer.org/ngl/api/manual/molecular-representations.html>, <https://nglviewer.org/ngl/api/manual/selection-language.html>, <https://nglviewer.org/ngl/api/manual/file-formats.html>
- VMD atom selections, fit/RMSD, trajectories, and analysis-oriented measurement model — <https://www.ks.uiuc.edu/Research/vmd/current/ug/>, <https://www.ks.uiuc.edu/Research/vmd/current/ug/node138.html>
- iCn3D linked structure/sequence/alignment, interfaces, custom tracks/colors, contact maps, mutation/electrostatics, saved selections, and side-by-side views — <https://www.ncbi.nlm.nih.gov/Structure/icn3d/docs/icn3d_help.html>, <https://www.ncbi.nlm.nih.gov/Structure/icn3d/docs/icn3d_about.html>

These products are capability references, not implementation requirements. BMS remains web/Electron-first, API-backed, provenance-aware, and governed by its own scientific contracts.

## 18. Approval decisions still required

Before implementation, the frontend and scientific owners must approve:

1. this architecture boundary and extension process;
2. whether the long-term adapter remains PDBe Molstar or moves to direct Mol* after a measured spike;
3. the first shared workbench pilot;
4. metric-registry ownership and API boundary;
5. the phase ordering in `docs/plans/2026-07-18-structure-visualization-platform-roadmap.md`;
6. a separate authorization for any Conformational Mapping Phase 13 work.
