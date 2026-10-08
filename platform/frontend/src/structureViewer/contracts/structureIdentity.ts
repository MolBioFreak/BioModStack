import { viewerAmbiguous, viewerOk, viewerUnsupported, type ViewerResult } from './viewerResults.js';

export type StructureDocumentSourceKind = 'pdb' | 'mmcif' | 'bcif' | 'sdf' | 'mol2' | 'trajectory' | 'volume';

export interface StructureDocumentRef {
    readonly documentId: string;
    readonly sourceKind: StructureDocumentSourceKind;
    readonly contentSha256?: string;
    readonly sourceUrl?: string;
    readonly candidateId?: string;
    readonly provenanceRef?: string;
}

export interface StructureSceneRef {
    readonly viewerId: string;
    readonly sceneId: string;
    readonly generation: number;
}

export interface EntityInstanceRef {
    readonly documentId: string;
    readonly modelId?: string;
    readonly entityId?: string;
    readonly sourceEntityId?: string;
    readonly sourceInstanceId?: string;
    readonly labelAsymId?: string;
    readonly authAsymId?: string;
    readonly assemblyId?: string;
    readonly operatorInstanceId?: string;
}

export interface ResidueRef extends EntityInstanceRef {
    readonly labelSeqId?: number;
    readonly authSeqId?: number;
    readonly insertionCode?: string;
    readonly componentId?: string;
    readonly altLoc?: string;
}

export interface AtomRef extends ResidueRef {
    readonly labelAtomId?: string;
    readonly authAtomId?: string;
    readonly element?: string;
    readonly atomIndex?: number;
}

const present = (value: string | undefined): boolean => Boolean(value?.trim());
const integer = (value: number | undefined): boolean => value !== undefined && Number.isInteger(value);

export const assessResidueRef = (residue: ResidueRef): ViewerResult<ResidueRef> => {
    if (!present(residue.documentId)) return viewerUnsupported('Residue identity requires a documentId', 'residue-identity');
    const labelComplete = present(residue.labelAsymId) && integer(residue.labelSeqId);
    const authorComplete = present(residue.authAsymId) && integer(residue.authSeqId);
    if (!labelComplete && !authorComplete) {
        return viewerAmbiguous('Residue identity requires one complete label or author namespace');
    }
    if (residue.insertionCode !== undefined && !authorComplete) {
        return viewerAmbiguous('Insertion-code identity requires a complete author namespace');
    }
    return viewerOk(residue);
};

const keyFields = (residue: ResidueRef): ReadonlyArray<readonly [string, string]> => [
    ['document', residue.documentId],
    ['model', residue.modelId ?? ''],
    ['entity', residue.entityId ?? ''],
    ['source_entity', residue.sourceEntityId ?? ''],
    ['source_instance', residue.sourceInstanceId ?? ''],
    ['label_asym', residue.labelAsymId ?? ''],
    ['auth_asym', residue.authAsymId ?? ''],
    ['label_seq', residue.labelSeqId === undefined ? '' : String(residue.labelSeqId)],
    ['auth_seq', residue.authSeqId === undefined ? '' : String(residue.authSeqId)],
    ['ins', residue.insertionCode ?? ''],
    ['component', residue.componentId ?? ''],
    ['altloc', residue.altLoc ?? ''],
    ['assembly', residue.assemblyId ?? ''],
    ['operator', residue.operatorInstanceId ?? ''],
];

export const canonicalResidueRefKey = (residue: ResidueRef): string => (
    keyFields(residue).map(([name, value]) => `${name}=${encodeURIComponent(value)}`).join('|')
);
