import type { AtomRef } from './structureIdentity.js';

export interface StructureAlignmentV1 {
    readonly schemaVersion: 1;
    readonly alignmentId: string;
    readonly referenceDocumentId: string;
    readonly mobileDocumentId: string;
    readonly referenceSelectionRef: string;
    readonly mobileSelectionRef: string;
    readonly mappingArtifactId: string;
    readonly mappingSha256: string;
    readonly method: 'supplied_transform_v1' | 'kabsch_exact_atom_mapping_v1';
    readonly producerVersion: string;
    readonly parameters: Readonly<Record<string, string | number | boolean | null>>;
    readonly transformRowMajor4x4: readonly number[];
    readonly matchedAtomCount: number;
    readonly matchedResidueCount: number;
    readonly unmatchedReferenceCount: number;
    readonly unmatchedMobileCount: number;
    readonly rmsd?: { readonly value: number; readonly units: 'Å'; readonly provenanceRef: string };
    readonly provenanceRef: string;
}

export interface StructureAtomMappingV1 {
    readonly schema: 'bms.viewer.atom-mapping.v1';
    readonly mappingId: string;
    readonly referenceDocumentId: string;
    readonly mobileDocumentId: string;
    readonly pairs: readonly {
        readonly pairId: string;
        readonly reference: AtomRef;
        readonly mobile: AtomRef;
    }[];
    readonly rejectedPairs: readonly {
        readonly reference: AtomRef;
        readonly mobile: AtomRef;
        readonly reason: string;
    }[];
    readonly provenanceRef: string;
}

export interface StructureComparisonStateV1 {
    readonly schema: 'bms.viewer.comparison-state.v1';
    readonly alignmentId: string;
    readonly alignmentSha256: string;
    readonly referenceDocumentId: string;
    readonly mobileDocumentId: string;
    readonly mode: 'overlay' | 'side_by_side';
    readonly cameraLink: 'off' | 'orientation' | 'full';
    readonly selectionLink: 'off' | 'mapped_only';
}
