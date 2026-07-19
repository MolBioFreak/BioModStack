import type {
    ViewerCapability,
    ViewerCapabilityId,
    ViewerRuntimeCapabilities,
} from '../contracts/viewerCapabilities.js';

const typeEvidence = (reference: string) => ({
    source: 'installed-type' as const,
    reference,
});

const sourceEvidence = (reference: string) => ({
    source: 'installed-source' as const,
    reference,
});

const capability = (
    value: Omit<ViewerCapability, 'evidence'> & {
        evidence: ViewerCapability['evidence'];
    },
): ViewerCapability => value;

export const MOLSTAR_STABLE_33_CAPABILITY_IDS = [
    'load-completion',
    'load-errors',
    'disconnect-disposal',
    'label-chain-identity',
    'author-chain-identity',
    'label-residue-identity',
    'author-residue-identity',
    'insertion-code-identity',
    'model-identity',
    'alternate-location-identity',
    'operator-instance-identity',
    'repeated-entity-instance-identity',
    'selection',
    'coloring',
    'overlays',
    'overlay-removal',
    'measurements',
    'trajectories',
    'assemblies',
    'symmetry',
    'volumes',
    'snapshots',
    'event-provenance',
] as const satisfies readonly ViewerCapabilityId[];

export const MOLSTAR_STABLE_33_CAPABILITIES = {
    schemaVersion: 1,
    auditedAt: '2026-07-18',
    runtime: {
        packageName: 'pdbe-molstar',
        packageVersion: '3.3.0',
        packageAlias: 'pdbe-molstar-stable',
        engineName: 'molstar',
        engineVersion: '4.5.0',
        productionResolution: "Vite alias 'pdbe-molstar' -> pdbe-molstar-stable/package.json directory",
    },
    capabilities: {
        'load-completion': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'viewerInstance.events.loadComplete exists, but the custom element does not expose an awaited ready/error contract.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts:16-18'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/pdbe-molstar-component-build.js custom element connectedCallback'),
            ],
        }),
        'load-errors': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'Direct PDBeMolstarPlugin render/load promises can reject, but the custom element discards those promises and publishes no wrapper error event.',
            failClosed: true,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/pdbe-molstar-component-build.js custom element connectedCallback')],
        }),
        'disconnect-disposal': capability({
            status: 'unsupported',
            boundary: 'direct-molstar-only',
            summary: 'The custom-element subclass has no disconnectedCallback, PDBeMolstarPlugin exposes no public dispose method, and the bundled React 18 renderer discards its root handle so plugin.dispose cannot unmount that UI root.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/pdbe-molstar-component-build.js custom element class'),
                sourceEvidence('molstar@4.5.0/lib/mol-plugin-ui/react18.js'),
                sourceEvidence('molstar@4.5.0/lib/mol-plugin/context.js dispose/unmount'),
            ],
        }),
        'label-chain-identity': capability({
            status: 'supported',
            boundary: 'pdbe-wrapper',
            summary: 'struct_asym_id is consumed as label_asym_id.',
            failClosed: false,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/helpers.js QueryHelper.getQueryObject')],
        }),
        'author-chain-identity': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper',
            summary: 'auth_asym_id is consumed only when struct_asym_id is absent; the wrapper does not cross-check both namespaces.',
            failClosed: true,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/helpers.js QueryHelper.getQueryObject')],
        }),
        'label-residue-identity': capability({
            status: 'supported',
            boundary: 'pdbe-wrapper',
            summary: 'residue_number is consumed as label_seq_id, including numeric ranges.',
            failClosed: false,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/helpers.js QueryHelper.getQueryObject')],
        }),
        'author-residue-identity': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper',
            summary: 'auth_seq_id/auth_residue_number are consumed, but author insertion-code identity is not.',
            failClosed: true,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/helpers.js QueryHelper.getQueryObject')],
        }),
        'insertion-code-identity': capability({
            status: 'unsupported',
            boundary: 'not-available',
            summary: 'auth_ins_code_id is declared but ignored by the 3.3 residue predicate, which broadens to auth_seq_id.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts QueryParam'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/helpers.js QueryHelper.getQueryObject'),
            ],
        }),
        'model-identity': capability({
            status: 'unsupported',
            boundary: 'not-available',
            summary: 'QueryParam has no model identifier field.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts QueryParam')],
        }),
        'alternate-location-identity': capability({
            status: 'unsupported',
            boundary: 'not-available',
            summary: 'QueryParam has no alternate-location field.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts QueryParam')],
        }),
        'operator-instance-identity': capability({
            status: 'unsupported',
            boundary: 'not-available',
            summary: 'QueryParam cannot select a specific assembly/operator instance.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts QueryParam')],
        }),
        'repeated-entity-instance-identity': capability({
            status: 'unsupported',
            boundary: 'not-available',
            summary: 'QueryParam has no repeated entity-instance identifier.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts QueryParam')],
        }),
        selection: capability({
            status: 'partial',
            boundary: 'pdbe-wrapper',
            summary: 'visual.select, focus, highlight, and clearSelection are public, but selection is only safe for the wrapper identity subset enforced by BMS.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts:90-155')],
        }),
        coloring: capability({
            status: 'partial',
            boundary: 'pdbe-wrapper',
            summary: 'visual.select supports overpaint colors and removal through clearSelection, bounded by the same incomplete residue identity contract.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts:125-144'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/viewer.js visual.select/clearSelection'),
            ],
        }),
        overlays: capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'PDBeMolstarPlugin.load can append ID-addressable structures, but it is reached through viewerInstance rather than a custom-element contract.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts:41-46')],
        }),
        'overlay-removal': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'deleteStructure removes an ID/index-addressed structure through viewerInstance; reconciliation is not declarative.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts:42-46')],
        }),
        measurements: capability({
            status: 'unsupported',
            boundary: 'direct-molstar-only',
            summary: 'No stable PDBe wrapper measurement API is declared.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts')],
        }),
        trajectories: capability({
            status: 'unsupported',
            boundary: 'direct-molstar-only',
            summary: 'Mol* trajectory machinery is bundled, but no stable PDBe wrapper trajectory/frame API is declared.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/viewer.js imports model-index animation'),
            ],
        }),
        assemblies: capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'LoadParams supports assemblyId, but repeated operator-instance identity and declarative assembly switching are not exposed.',
            failClosed: true,
            evidence: [typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts LoadParams')],
        }),
        symmetry: capability({
            status: 'unsupported',
            boundary: 'direct-molstar-only',
            summary: 'Assembly-symmetry code is bundled, but no governed PDBe wrapper API is available.',
            failClosed: true,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/viewer.js assembly-symmetry import/spec')],
        }),
        volumes: capability({
            status: 'partial',
            boundary: 'pdbe-wrapper-private-instance',
            summary: 'PDBe map streaming is available through loadMaps/mapSettings, not a general governed volume contract.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/helpers.d.ts MapParams'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/viewer.js volume-streaming path'),
            ],
        }),
        snapshots: capability({
            status: 'unsupported',
            boundary: 'direct-molstar-only',
            summary: 'Mol* snapshot machinery is bundled, but PDBeMolstarPlugin declares no stable snapshot API.',
            failClosed: true,
            evidence: [
                typeEvidence('pdbe-molstar@3.3.0/lib/viewer.d.ts'),
                sourceEvidence('pdbe-molstar@3.3.0/lib/viewer.js state-snapshots import'),
            ],
        }),
        'event-provenance': capability({
            status: 'partial',
            boundary: 'pdbe-wrapper',
            summary: 'Bubbling custom events expose the owning host through event.target, but carry no BMS viewer, scene, document, or generation identity.',
            failClosed: true,
            evidence: [sourceEvidence('pdbe-molstar@3.3.0/lib/custom-events.js dispatchCustomEvent')],
        }),
    },
} as const satisfies ViewerRuntimeCapabilities;

export interface MolstarStable33IdentityRequest {
    readonly entityId?: string;
    readonly labelAsymId?: string;
    readonly authorAsymId?: string;
    readonly labelSeqId?: number;
    readonly authorSeqId?: number;
    readonly insertionCode?: string;
    readonly modelId?: string;
    readonly alternateLocation?: string;
    readonly operatorInstanceId?: string;
    readonly repeatedEntityInstanceId?: string;
}

export type MolstarStable33UnsupportedIdentityField =
    | 'insertionCode'
    | 'modelId'
    | 'alternateLocation'
    | 'operatorInstanceId'
    | 'repeatedEntityInstanceId';

export type MolstarStable33IdentityReason =
    | 'identityFieldUnsupported'
    | 'missingChainOrEntity'
    | 'residueWithoutChain'
    | 'mixedChainResidueNamespace'
    | 'dualChainNamespace'
    | 'dualResidueNamespace';

export type MolstarStable33IdentityAssessment =
    | {
        readonly status: 'supported';
        readonly unsupportedFields: readonly [];
        readonly reasons: readonly [];
    }
    | {
        readonly status: 'ambiguous';
        readonly unsupportedFields: readonly [];
        readonly reasons: readonly MolstarStable33IdentityReason[];
    }
    | {
        readonly status: 'unsupported';
        readonly unsupportedFields: readonly MolstarStable33UnsupportedIdentityField[];
        readonly reasons: readonly MolstarStable33IdentityReason[];
    };

export function assessMolstarStable33Identity(
    identity: MolstarStable33IdentityRequest,
): MolstarStable33IdentityAssessment {
    const unsupportedFields: MolstarStable33UnsupportedIdentityField[] = [];
    if (identity.insertionCode !== undefined) unsupportedFields.push('insertionCode');
    if (identity.modelId !== undefined) unsupportedFields.push('modelId');
    if (identity.alternateLocation !== undefined) unsupportedFields.push('alternateLocation');
    if (identity.operatorInstanceId !== undefined) unsupportedFields.push('operatorInstanceId');
    if (identity.repeatedEntityInstanceId !== undefined) unsupportedFields.push('repeatedEntityInstanceId');

    if (unsupportedFields.length > 0) {
        return { status: 'unsupported', unsupportedFields, reasons: ['identityFieldUnsupported'] };
    }

    const hasLabelChain = identity.labelAsymId !== undefined;
    const hasAuthorChain = identity.authorAsymId !== undefined;
    const hasLabelResidue = identity.labelSeqId !== undefined;
    const hasAuthorResidue = identity.authorSeqId !== undefined;

    if (hasLabelChain && hasAuthorChain) {
        return { status: 'unsupported', unsupportedFields: [], reasons: ['dualChainNamespace'] };
    }
    if (hasLabelResidue && hasAuthorResidue) {
        return { status: 'unsupported', unsupportedFields: [], reasons: ['dualResidueNamespace'] };
    }
    if ((hasLabelChain && hasAuthorResidue) || (hasAuthorChain && hasLabelResidue)) {
        return { status: 'unsupported', unsupportedFields: [], reasons: ['mixedChainResidueNamespace'] };
    }

    const hasChainOrEntity = identity.entityId !== undefined || hasLabelChain || hasAuthorChain;
    if (!hasChainOrEntity) {
        return { status: 'ambiguous', unsupportedFields: [], reasons: ['missingChainOrEntity'] };
    }

    const hasResidue = hasLabelResidue || hasAuthorResidue;
    if (hasResidue && !hasLabelChain && !hasAuthorChain) {
        return { status: 'ambiguous', unsupportedFields: [], reasons: ['residueWithoutChain'] };
    }

    return { status: 'supported', unsupportedFields: [], reasons: [] };
}
