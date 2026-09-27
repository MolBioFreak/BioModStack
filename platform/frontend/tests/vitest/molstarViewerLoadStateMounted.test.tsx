import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { useTheme } from '../../src/components/themeContext';

const controllerState = vi.hoisted(() => ({
    loadResult: { status: 'ok', value: undefined } as { status: string; value?: undefined; error?: Error },
    reconcileResult: { status: 'ok', value: undefined } as { status: string; value?: undefined; error?: Error },
    loadedScenes: [] as Array<Record<string, unknown>>,
    mounted: 0,
    backgrounds: [] as string[],
}));

vi.mock('../../src/structureViewer/adapters/MolstarDirectAdapter', () => ({
    MolstarDirectAdapterCancelledError: class MolstarDirectAdapterCancelledError extends Error {},
    MolstarDirectAdapter: class MolstarDirectAdapter {
        async mount() { controllerState.mounted++; }
        async setBackground(color: string) { controllerState.backgrounds.push(color); }
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
    controllerState.mounted = 0;
    controllerState.backgrounds = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('Mol* public load-state contract', () => {
    it('keeps the structure caption in a bounded header before the native controls', async () => {
        const label = 'candidate_with_a_long_native_structure_identifier_0_0_model_0';
        await act(async () => {
            root.render(<StructureViewerHost structureUrl="/target.cif" label={label} />);
        });
        const viewer = container.querySelector('[data-bms-molstar-adapter]')!;
        const caption = viewer.querySelector('[title]') as HTMLElement;
        const mount = viewer.querySelector('[data-bms-molstar-mount]') as HTMLElement;
        expect(viewer.classList.contains('flex-col')).toBe(true);
        expect(caption.textContent).toBe(label);
        expect(caption.title).toBe(label);
        expect(caption.classList.contains('truncate')).toBe(true);
        expect(caption.classList.contains('shrink-0')).toBe(true);
        expect(caption.classList.contains('absolute')).toBe(false);
        expect(caption.nextElementSibling).toBe(mount);
        expect(mount.classList.contains('flex-1')).toBe(true);
        expect(mount.classList.contains('absolute')).toBe(false);
        expect(controllerState.mounted).toBe(1);
    });

    it('updates global theme and explicit background without reloading the scene', async () => {
        const style = document.createElement('style');
        style.textContent = '[data-theme="midnight"] { --bg-primary: #0b1020; } [data-theme="light"] { --bg-primary: #ffffff; }';
        document.head.append(style);
        localStorage.setItem('bms-theme', 'midnight');
        function SwitchTheme() {
            const { setTheme } = useTheme();
            return <button onClick={() => setTheme('light')}>Light theme</button>;
        }
        const render = async (backgroundColor?: string) => act(async () => {
            root.render(<ThemeProvider><SwitchTheme /><StructureViewerHost structureUrl="/target.cif" backgroundColor={backgroundColor} /></ThemeProvider>);
        });
        try {
            await render();
            await vi.waitFor(() => expect(controllerState.backgrounds.at(-1)).toBe('#0b1020'));
            await act(async () => container.querySelector('button')!.click());
            await vi.waitFor(() => expect(controllerState.backgrounds.at(-1)).toBe('#ffffff'));
            await render('#123456');
            await vi.waitFor(() => expect(controllerState.backgrounds.at(-1)).toBe('#123456'));
            await render();
            await vi.waitFor(() => expect(controllerState.backgrounds.at(-1)).toBe('#ffffff'));
            expect(controllerState.mounted).toBe(1);
            expect(controllerState.loadedScenes).toHaveLength(1);
        } finally {
            style.remove();
            localStorage.removeItem('bms-theme');
            document.documentElement.removeAttribute('data-theme');
        }
    });

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

    it('passes native SDF through the public host without reinterpreting it as protein PDB', async () => {
        const onLoadStateChange = vi.fn();
        await act(async () => {
            root.render(<StructureViewerHost structureUrl="/api/jobs/dock/docking-results/run/diffdock/results/complex-b/rank1.sdf"
                format="sdf" onLoadStateChange={onLoadStateChange} showMetricWorkbench={false}
                showSequenceTrack={false} showMeasurements={false} showComplexWorkbench={false} showM6Workbench={false} />);
        });
        await vi.waitFor(() => expect(onLoadStateChange).toHaveBeenLastCalledWith('loaded', undefined));
        expect(controllerState.loadedScenes[0]).toMatchObject({
            documents: [{ sourceKind: 'sdf', sourceUrl: new URL('/api/jobs/dock/docking-results/run/diffdock/results/complex-b/rank1.sdf', window.location.href).href }],
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