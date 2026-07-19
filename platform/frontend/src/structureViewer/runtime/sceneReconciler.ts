import type { StructureSceneState } from '../contracts/sceneState.js';

export interface SceneReconciliation {
    readonly documentsChanged: boolean;
    readonly activeDocumentChanged: boolean;
    readonly collectionChanged: boolean;
    readonly representationChanged: boolean;
    readonly layerChanged: boolean;
    readonly selectionChanged: boolean;
    readonly filterChanged: boolean;
    readonly cameraChanged: boolean;
    readonly measurementChanged: boolean;
    readonly presentationChanged: boolean;
    readonly molecularDynamicsChanged: boolean;
}

const stable = (value: unknown): string => JSON.stringify(value ?? null);

export const reconcileSceneState = (
    previous: StructureSceneState | undefined,
    next: StructureSceneState,
): SceneReconciliation => ({
    documentsChanged: !previous || stable(previous.documents) !== stable(next.documents),
    activeDocumentChanged: !previous || previous.activeDocumentId !== next.activeDocumentId,
    collectionChanged: !previous || stable(previous.collection) !== stable(next.collection),
    representationChanged: !previous || stable(previous.presentation?.representations) !== stable(next.presentation?.representations),
    layerChanged: !previous || stable(previous.presentation?.layers) !== stable(next.presentation?.layers),
    selectionChanged: !previous || stable(previous.presentation?.selection) !== stable(next.presentation?.selection),
    filterChanged: !previous || stable(previous.presentation?.filters) !== stable(next.presentation?.filters),
    cameraChanged: !previous || stable(previous.presentation?.camera) !== stable(next.presentation?.camera),
    measurementChanged: !previous || stable(previous.presentation?.measurements) !== stable(next.presentation?.measurements),
    presentationChanged: !previous
        || stable(previous.presentation?.colorQueries) !== stable(next.presentation?.colorQueries)
        || stable(previous.presentation?.tooltipQueries) !== stable(next.presentation?.tooltipQueries)
        || stable(previous.presentation?.nonSelectedColor) !== stable(next.presentation?.nonSelectedColor),
    molecularDynamicsChanged: !previous || stable(previous.molecularDynamics) !== stable(next.molecularDynamics),
});
