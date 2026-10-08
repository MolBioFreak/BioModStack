import type { StructureSceneState } from '../contracts/sceneState.js';
import {
    viewerOk,
    viewerUnsupported,
    type ViewerResult,
} from '../contracts/viewerResults.js';

export type DirectMolstarDocumentFormat = 'mmcif' | 'pdb' | 'sdf';

export interface DirectMolstarDocument {
    readonly id: string;
    readonly url: string;
    readonly format: DirectMolstarDocumentFormat;
    readonly isBinary?: boolean;
}

export const documentsForDirectMolstar = (
    state: StructureSceneState,
): ViewerResult<readonly DirectMolstarDocument[]> => {
    const documents: DirectMolstarDocument[] = [];
    for (const document of state.documents) {
        if (!document.sourceUrl?.trim()) {
            return viewerUnsupported(`Document ${document.documentId} has no transport URL`, 'coordinate-loading');
        }
        if (document.sourceKind === 'pdb' || document.sourceKind === 'mmcif' || document.sourceKind === 'sdf') {
            documents.push({
                id: document.documentId,
                url: document.sourceUrl,
                format: document.sourceKind,
            });
            continue;
        }
        if (document.sourceKind === 'bcif') {
            documents.push({
                id: document.documentId,
                url: document.sourceUrl,
                format: 'mmcif',
                isBinary: true,
            });
            continue;
        }
        return viewerUnsupported(
            `Direct Mol* ${document.sourceKind} integration is not enabled`,
            document.sourceKind,
        );
    }
    return viewerOk(documents);
};
