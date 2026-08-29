import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const controllerState = vi.hoisted(() => ({
    loadResult: { status: 'ok', value: undefined } as { status: string; value?: undefined; error?: Error },
    reconcileResult: { status: 'ok', value: undefined } as { status: string; value?: undefined; error?: Error },
    loadedScenes: [] as Array<Record<string, unknown>>,
}));

vi.mock('../../src/structureViewer/adapters/MolstarDirectAdapter', () => ({
    MolstarDirectAdapterCancelledError: class MolstarDirectAdapterCancelledError extends Error {},
    MolstarDirectAdapter: class MolstarDirectAdapter {
        async mount() { return undefined; }
        dispose() { return undefined; }
        resetCamera() { return { status: 'ok', value: undefined }; }
    },
}));

vi.mock('../../src/structureViewer/runtime/StructureSceneController', () => ({
    StructureSceneController: class StructureSceneController {
        currentScene = null;
        async loadScene(scene: Record<string, unknown>) {
            controllerState.loadedScenes.push(scene);
            return controllerState.loadResult;
        }
        async reconcileScene() { return controllerState.reconcileResult; }
        subscribe() { return () => undefined; }
        async dispose() { return undefined; }
    },
}));

import StructureViewerHost from '../../src/structureViewer/StructureViewerHost';

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
    controllerState.loadResult = { status: 'ok', value: undefined };
    controllerState.reconcileResult = { status: 'ok', value: undefined };
    controllerState.loadedScenes = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('Mol* public load-state contract', () => {
    it('forwards real scene loading and loaded states through the host and maps public cif to mmcif', async () => {
        const onLoadStateChange = vi.fn();
        await act(async () => {
            root.render(
                <StructureViewerHost
                    structureUrl="/api/structure.cif"
                    format="cif"
                    onLoadStateChange={onLoadStateChange}
                    showMetricWorkbench={false}
                    showSequenceTrack={false}
                    showMeasurements={false}
                    showComplexWorkbench={false}
                    showM6Workbench={false}
                />,
            );
        });
        await vi.waitFor(() => expect(onLoadStateChange).toHaveBeenLastCalledWith('loaded', undefined));
        expect(onLoadStateChange.mock.calls.some(([state]) => state === 'loading')).toBe(true);
        expect(controllerState.loadedScenes[0]).toMatchObject({
            documents: [{ sourceKind: 'mmcif' }],
        });
    });

    it('publishes a bounded failed state when the active scene load fails', async () => {
        controllerState.loadResult = { status: 'error', error: new Error('decoder rejected current structure') };
        const onLoadStateChange = vi.fn();
        await act(async () => {
            root.render(
                <StructureViewerHost
                    structureUrl="/api/structure.pdb"
                    format="pdb"
                    onLoadStateChange={onLoadStateChange}
                    showMetricWorkbench={false}
                    showSequenceTrack={false}
                    showMeasurements={false}
                    showComplexWorkbench={false}
                    showM6Workbench={false}
                />,
            );
        });
        await vi.waitFor(() => expect(onLoadStateChange).toHaveBeenLastCalledWith('failed', 'decoder rejected current structure'));
    });
});