export type ViewerCapabilityStatus = 'supported' | 'partial' | 'unsupported';

export type ViewerCapabilityId =
    | 'load-completion'
    | 'load-errors'
    | 'disconnect-disposal'
    | 'label-chain-identity'
    | 'author-chain-identity'
    | 'label-residue-identity'
    | 'author-residue-identity'
    | 'insertion-code-identity'
    | 'model-identity'
    | 'alternate-location-identity'
    | 'operator-instance-identity'
    | 'repeated-entity-instance-identity'
    | 'selection'
    | 'coloring'
    | 'overlays'
    | 'overlay-removal'
    | 'measurements'
    | 'trajectories'
    | 'assemblies'
    | 'symmetry'
    | 'volumes'
    | 'snapshots'
    | 'event-provenance';

export type ViewerCapabilityBoundary =
    | 'pdbe-wrapper'
    | 'pdbe-wrapper-private-instance'
    | 'direct-molstar-only'
    | 'bms-direct-adapter'
    | 'bms-engine-owner'
    | 'molstar-public-ui'
    | 'molstar-public-builders'
    | 'molstar-default-policy'
    | 'not-implemented'
    | 'not-available';

export interface ViewerCapabilityEvidence {
    readonly source: 'installed-type' | 'installed-source' | 'bms-source' | 'unit-test' | 'browser-probe';
    readonly reference: string;
}

export interface ViewerCapability {
    readonly status: ViewerCapabilityStatus;
    readonly boundary: ViewerCapabilityBoundary;
    readonly summary: string;
    readonly failClosed: boolean;
    readonly evidence: readonly ViewerCapabilityEvidence[];
}

export interface ViewerRuntimeIdentity {
    readonly packageName: 'pdbe-molstar';
    readonly packageVersion: string;
    readonly packageAlias: string;
    readonly engineName: 'molstar';
    readonly engineVersion: string;
    readonly productionResolution: string;
}

export interface ViewerRuntimeCapabilities {
    readonly schemaVersion: 1;
    readonly auditedAt: string;
    readonly runtime: ViewerRuntimeIdentity;
    readonly capabilities: Readonly<Record<ViewerCapabilityId, ViewerCapability>>;
}
