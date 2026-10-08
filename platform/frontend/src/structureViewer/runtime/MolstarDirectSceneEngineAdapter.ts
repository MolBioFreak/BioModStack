import {
    MolstarDirectAdapter,
    MolstarDirectAdapterCancelledError,
} from '../adapters/MolstarDirectAdapter';
import type { StructureSceneState } from '../contracts/sceneState.js';
import {
    viewerCancelled,
    viewerError,
    viewerOk,
    type ViewerResult,
} from '../contracts/viewerResults.js';
import type { StructureSceneEngineAdapter } from './StructureSceneController.js';
import { documentsForDirectMolstar } from './directSceneDocuments.js';

export class MolstarDirectSceneEngineAdapter implements StructureSceneEngineAdapter {
    private readonly adapter: MolstarDirectAdapter;

    constructor(adapter: MolstarDirectAdapter) {
        this.adapter = adapter;
    }

    async loadScene(state: StructureSceneState, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Scene load was cancelled before translation');
        const documents = documentsForDirectMolstar(state);
        if (documents.status !== 'ok') return documents;
        try {
            await this.adapter.loadScene(documents.value);
            if (signal.aborted) return viewerCancelled('Scene load was cancelled after engine reconciliation');
            return viewerOk(undefined);
        } catch (error) {
            if (signal.aborted || error instanceof MolstarDirectAdapterCancelledError) {
                return viewerCancelled('Scene load was superseded or cancelled');
            }
            return viewerError(error);
        }
    }

    async dispose(): Promise<void> {
        this.adapter.dispose();
    }
}
