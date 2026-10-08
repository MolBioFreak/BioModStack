import type {
    ViewerCapability,
    ViewerCapabilityBoundary,
    ViewerCapabilityEvidence,
    ViewerCapabilityId,
    ViewerCapabilityStatus,
} from '../contracts/viewerCapabilities.js';

export interface MolstarDirectPrivateApiUse {
    readonly symbol: string;
    readonly classification: 'unstable-vendor-helper' | 'private-diagnostics-only';
    readonly purpose: string;
    readonly productionBehaviorDependsOnIt: boolean;
    readonly containment: string;
}

export interface MolstarDirectCapabilityManifest {
    readonly schemaVersion: 1;
    readonly auditedAt: '2026-07-18';
    readonly adapter: {
        readonly id: 'bms-molstar-direct';
        readonly version: '2';
        readonly enginePackage: 'molstar';
        readonly engineVersion: '4.5.0';
        readonly wrapperRuntimeDependency: false;
        readonly governedSurface: 'StructureViewerHost';
    };
    readonly capabilities: Readonly<Record<ViewerCapabilityId, ViewerCapability>>;
    readonly privateApiInventory: readonly MolstarDirectPrivateApiUse[];
}

const directCapability = (
    status: ViewerCapabilityStatus,
    boundary: ViewerCapabilityBoundary,
    summary: string,
    reference: string,
    failClosed = true,
): ViewerCapability => {
    const source: ViewerCapabilityEvidence['source'] = reference.endsWith('.json')
        ? 'browser-probe'
        : 'bms-source';
    return Object.freeze({
        status,
        boundary,
        summary,
        failClosed,
        evidence: Object.freeze([{ source, reference }]),
    });
};

const adapterSource = 'platform/frontend/src/structureViewer/adapters/MolstarDirectAdapter.ts';
const componentSource = 'platform/frontend/src/structureViewer/StructureViewerHost.tsx';
const browserEvidence = 'docs/reviews/structure_visualization/evidence/m1_direct_molstar_runtime_probe_final_chrome150.json';

const capabilities: Record<ViewerCapabilityId, ViewerCapability> = {
    'load-completion': directCapability(
        'supported', 'bms-direct-adapter',
        'BMS awaits direct Mol* download, trajectory parse, representation preset, and ordered overlay completion.',
        adapterSource,
    ),
    'load-errors': directCapability(
        'supported', 'bms-direct-adapter',
        'Current-generation load failures surface in the BMS viewer error state; stale generations are cancelled.',
        componentSource,
    ),
    'disconnect-disposal': directCapability(
        'supported', 'bms-engine-owner',
        'BMS unmounts its captured React root before plugin disposal and invalidates late generations idempotently.',
        browserEvidence,
    ),
    'label-chain-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'The public Selection.chain_id contract maps only to label/struct chain identity.',
        componentSource,
    ),
    'author-chain-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'Canonical metrics, linked views, and direct queries preserve author-chain identity and fail closed on incomplete namespaces.',
        componentSource,
    ),
    'label-residue-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'Label-chain plus label residue numbering is accepted by governed selections and residue maps.',
        componentSource,
    ),
    'author-residue-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'Canonical metric and measurement contracts preserve author residue numbering when paired with author-chain identity.',
        componentSource,
    ),
    'insertion-code-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'Canonical metrics and exact-atom measurements preserve author insertion codes; legacy ambiguous keys fail closed.',
        'platform/frontend/src/structureViewer/adapters/residueColorSelections.ts',
    ),
    'model-identity': directCapability(
        'unsupported', 'not-implemented',
        'The generic facade does not expose model identity and will not broaden across models by declaration.',
        componentSource,
    ),
    'alternate-location-identity': directCapability(
        'unsupported', 'not-implemented',
        'Alternate-location identity is not represented by the BMS facade.',
        componentSource,
    ),
    'operator-instance-identity': directCapability(
        'unsupported', 'not-implemented',
        'Operator-instance identity is not represented by the BMS facade.',
        componentSource,
    ),
    'repeated-entity-instance-identity': directCapability(
        'unsupported', 'not-implemented',
        'Repeated entity instances cannot be disambiguated without an explicit chain namespace.',
        componentSource,
    ),
    selection: directCapability(
        'partial', 'bms-direct-adapter',
        'Label-chain/range selections and canonical residue metric selections are governed; arbitrary Mol* queries are not exposed.',
        componentSource,
    ),
    coloring: directCapability(
        'partial', 'bms-direct-adapter',
        'BMS governs chain/range colors, legacy residue maps, canonical metrics, and AlphaFold pLDDT only.',
        componentSource,
    ),
    overlays: directCapability(
        'supported', 'bms-direct-adapter',
        'Primary and ordered overlay documents load into one BMS-owned plugin scene.',
        adapterSource,
    ),
    'overlay-removal': directCapability(
        'supported', 'bms-engine-owner',
        'Document changes reconcile on the same owner by transactionally replacing the scene with exactly the requested primary and ordered overlays.',
        componentSource,
    ),
    measurements: directCapability(
        'supported', 'bms-direct-adapter',
        'Distance, angle, and dihedral measurements require exact canonical atoms and provenance and reconcile declaratively.',
        'platform/frontend/src/structureViewer/contracts/measurements.ts',
    ),
    trajectories: directCapability(
        'partial', 'bms-direct-adapter',
        'Governed GRO/XTC replica loading and bounded display-frame selection are supported through pinned Mol* 4.5 state transforms; DCD and continuous playback remain fail-closed.',
        adapterSource,
    ),
    assemblies: directCapability(
        'partial', 'bms-direct-adapter',
        'The adapter document contract accepts an assembly id, but the generic React facade does not expose switching.',
        adapterSource,
    ),
    symmetry: directCapability(
        'unsupported', 'not-implemented',
        'No governed BMS symmetry-mate API or control exists.',
        componentSource,
    ),
    volumes: directCapability(
        'unsupported', 'not-implemented',
        'No governed BMS density/volume loading contract exists.',
        componentSource,
    ),
    snapshots: directCapability(
        'supported', 'bms-engine-owner',
        'Versioned scene snapshots preserve collection, presentation, adapter identity, provenance, and document hashes and reject stale sources.',
        'platform/frontend/src/structureViewer/contracts/sceneState.ts',
    ),
    'event-provenance': directCapability(
        'supported', 'bms-engine-owner',
        'Controller events are scoped to viewer, scene, generation, document, origin, and timestamp; residue clicks retain direct identity.',
        'platform/frontend/src/structureViewer/contracts/viewerEvents.ts',
    ),
};

export const MOLSTAR_DIRECT_45_CAPABILITIES: MolstarDirectCapabilityManifest = Object.freeze({
    schemaVersion: 1,
    auditedAt: '2026-07-18',
    adapter: Object.freeze({
        id: 'bms-molstar-direct',
        version: '2',
        enginePackage: 'molstar',
        engineVersion: '4.5.0',
        wrapperRuntimeDependency: false,
        governedSurface: 'StructureViewerHost',
    }),
    capabilities: Object.freeze(capabilities),
    privateApiInventory: Object.freeze([
        Object.freeze({
            symbol: 'PluginUIContext.disposed',
            classification: 'private-diagnostics-only',
            purpose: 'Expose terminal lifecycle proof to the internal Chrome harness.',
            productionBehaviorDependsOnIt: false,
            containment: 'One isPluginDisposed diagnostics helper; production control flow never reads it.',
        }),
    ]),
});
