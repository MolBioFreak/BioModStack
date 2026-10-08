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
        readonly version: '1';
        readonly enginePackage: 'molstar';
        readonly engineVersion: '4.5.0';
        readonly compatibilityReferencePackage: 'pdbe-molstar';
        readonly compatibilityReferenceVersion: '3.3.0';
        readonly wrapperRuntimeDependency: false;
        readonly governedSurface: 'MolstarViewer';
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
const componentSource = 'platform/frontend/src/components/MolstarViewer.tsx';
const browserEvidence = 'docs/reviews/structure_visualization/evidence/m1_direct_molstar_runtime_probe_chrome150.json';

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
        'partial', 'bms-direct-adapter',
        'Canonical metric layers can carry author-chain identity; the generic Selection prop is label-chain only.',
        componentSource,
    ),
    'label-residue-identity': directCapability(
        'supported', 'bms-direct-adapter',
        'Label-chain plus label residue numbering is accepted by governed selections and residue maps.',
        componentSource,
    ),
    'author-residue-identity': directCapability(
        'partial', 'bms-direct-adapter',
        'Canonical metric layers preserve author residue numbering when paired with author-chain identity.',
        componentSource,
    ),
    'insertion-code-identity': directCapability(
        'partial', 'bms-direct-adapter',
        'Canonical metric layers preserve insertion codes; legacy residue-color keys reject insertion-like ambiguity.',
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
        'unsupported', 'not-implemented',
        'No governed BMS measurement contract exists.',
        componentSource,
    ),
    trajectories: directCapability(
        'unsupported', 'not-implemented',
        'No governed trajectory playback or frame-selection contract exists.',
        componentSource,
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
        'unsupported', 'not-implemented',
        'No governed BMS state snapshot/import/export contract exists.',
        componentSource,
    ),
    'event-provenance': directCapability(
        'unsupported', 'not-implemented',
        'The generic facade does not expose click/hover events with explicit model/operator provenance.',
        componentSource,
    ),
};

export const MOLSTAR_DIRECT_45_CAPABILITIES: MolstarDirectCapabilityManifest = Object.freeze({
    schemaVersion: 1,
    auditedAt: '2026-07-18',
    adapter: Object.freeze({
        id: 'bms-molstar-direct',
        version: '1',
        enginePackage: 'molstar',
        engineVersion: '4.5.0',
        compatibilityReferencePackage: 'pdbe-molstar',
        compatibilityReferenceVersion: '3.3.0',
        wrapperRuntimeDependency: false,
        governedSurface: 'MolstarViewer',
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
