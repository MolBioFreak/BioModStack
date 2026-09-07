import type { AtomRef, ResidueRef } from '../contracts/structureIdentity.js';

export type MetricDimension =
    | 'residue-scalar'
    | 'atom-scalar'
    | 'structure-scalar'
    | 'chain-pair-scalar'
    | 'residue-pair-matrix'
    | 'geometry-annotation'
    | 'volume-descriptor';

export type MetricDirection = 'higher_is_better' | 'lower_is_better' | 'neutral';
export type MetricMissingness = 'not_computed' | 'not_applicable' | 'unavailable' | 'filtered';
export type MetricProjectionPolicy = 'direct' | 'uniform-structure' | 'selected-slice' | 'none';
export type MetricNormalization = 'none' | 'linear' | 'log' | 'quantile';

export interface MetricPalette {
    readonly colors: readonly string[];
    readonly domain?: readonly [number, number];
    readonly missingColor?: string;
    readonly filteredColor?: string;
}

export interface MetricProvenance {
    readonly source: string;
    readonly sourceVersion?: string;
    readonly workflowId?: string;
    readonly jobId?: string;
    readonly artifactId?: string;
    readonly artifactSha256?: string;
    readonly computedAt?: string;
    readonly parameters?: Readonly<Record<string, string | number | boolean | null>>;
}

export interface MetricDescriptor {
    readonly id: string;
    readonly label: string;
    readonly dimension: MetricDimension;
    readonly units: string | null;
    readonly direction: MetricDirection;
    readonly description?: string;
    readonly semantics?: string;
    readonly formula?: string;
    readonly valueRange?: readonly [number, number];
    readonly categories?: Readonly<Record<string, { readonly label: string; readonly color?: string }>>;
    readonly projectionPolicy: MetricProjectionPolicy;
    readonly normalization: MetricNormalization;
    readonly palette?: MetricPalette;
    readonly provenance: MetricProvenance;
}

export interface MetricValue<TIdentity> {
    readonly identity: TIdentity;
    readonly value: number | string | boolean | null;
    readonly missingness?: MetricMissingness;
    readonly provenance?: MetricProvenance;
    readonly displayColor?: string | { readonly r: number; readonly g: number; readonly b: number };
}

export interface MetricDatasetMetadata {
    readonly datasetId: string;
    readonly descriptorId: string;
    readonly documentIds: readonly string[];
    readonly shape?: readonly [number, number];
    readonly rowAxis?: readonly ResidueRef[];
    readonly columnAxis?: readonly ResidueRef[];
    /** Explicit directed matrix identity; absent retains the legacy symmetric renderer. */
    readonly matrixDirection?: 'directed';
    readonly originalIndices?: readonly number[];
    readonly reduction?: { readonly method: string; readonly sourceShape: readonly [number, number]; readonly parameters: Readonly<Record<string, number | string | boolean>> };
}

export interface StructureMetricIdentity { readonly documentId: string; }
export interface ChainPairIdentity {
    readonly documentId: string;
    readonly firstChainId: string;
    readonly secondChainId: string;
    readonly firstInstanceId?: string;
    readonly secondInstanceId?: string;
}
export interface ResiduePairIdentity {
    readonly first: ResidueRef;
    readonly second: ResidueRef;
}
export interface GeometryAnnotationIdentity {
    readonly annotationId: string;
    readonly documentId: string;
    readonly residues?: readonly ResidueRef[];
    readonly atoms?: readonly AtomRef[];
}
export interface VolumeDescriptorIdentity {
    readonly documentId: string;
    readonly volumeId: string;
}

export type MetricLayer =
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'residue-scalar' }; readonly values: readonly MetricValue<ResidueRef>[] }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'atom-scalar' }; readonly values: readonly MetricValue<AtomRef>[] }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'structure-scalar' }; readonly values: readonly MetricValue<StructureMetricIdentity>[] }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'chain-pair-scalar' }; readonly values: readonly MetricValue<ChainPairIdentity>[] }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'residue-pair-matrix' }; readonly values: readonly MetricValue<ResiduePairIdentity>[]; readonly dataset?: MetricDatasetMetadata }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'geometry-annotation' }; readonly values: readonly MetricValue<GeometryAnnotationIdentity>[] }
    | { readonly descriptor: MetricDescriptor & { readonly dimension: 'volume-descriptor' }; readonly values: readonly MetricValue<VolumeDescriptorIdentity>[] };

export interface MetricSelection {
    readonly metricId: string;
    readonly identities: readonly (ResidueRef | AtomRef | ResiduePairIdentity | ChainPairIdentity)[];
    readonly origin: 'canvas' | 'sequence' | 'matrix' | 'table';
}

export const isFiniteMetricValue = (value: MetricValue<unknown>): value is MetricValue<unknown> & { readonly value: number } => (
    typeof value.value === 'number' && Number.isFinite(value.value) && value.missingness === undefined
);
