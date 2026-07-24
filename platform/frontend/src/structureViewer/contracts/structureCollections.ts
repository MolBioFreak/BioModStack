import type { StructureCollectionKind } from './sceneState.js';

export interface StructureCollectionManifestV1 {
    readonly schemaVersion: 1;
    readonly collectionId: string;
    readonly kind: StructureCollectionKind;
    readonly orderedMemberIds: readonly string[];
    readonly ordering: {
        readonly semantic: 'producer_order' | 'rank' | 'condition' | 'time' | 'none';
        readonly coordinateName?: string;
        readonly units?: string;
        readonly producerRef: string;
    };
    readonly membersResource: 'job_viewer_collection_members_v1';
    readonly memberCount: number;
    readonly provenanceRef: string;
    readonly manifestSha256: string;
}

export interface StructureCollectionMemberV1 {
    readonly memberId: string;
    readonly documentId: string;
    readonly candidateId?: string;
    readonly structureArtifactId: string;
    readonly structureSha256: string;
    readonly sourceFormat: 'pdb' | 'mmcif' | 'bcif' | 'sdf';
    readonly rank?: number;
    readonly backendCoordinate?: Readonly<Record<string, number | string>>;
    readonly metricRefs: readonly string[];
    readonly mappingRef?: string;
    readonly provenanceRef: string;
    readonly missingness?: 'missing' | 'unsupported' | 'ambiguous' | 'not_applicable';
}

export interface CollectionBrowserStateV1 {
    readonly schema: 'bms.viewer.collection-browser-state.v1';
    readonly collectionId: string;
    readonly manifestSha256: string;
    readonly activeMemberId: string | null;
    readonly residentMemberIds: readonly string[];
    readonly overlayMemberIds: readonly string[];
    readonly sort: {
        readonly field: 'authoritative_order' | 'rank' | 'backend_coordinate' | 'metric';
        readonly fieldId: string | null;
        readonly direction: 'ascending' | 'descending';
    };
    readonly filters: readonly {
        readonly fieldId: string;
        readonly operator: 'eq' | 'lt' | 'lte' | 'gt' | 'gte' | 'present';
        readonly value: string | number | boolean | null;
    }[];
}
