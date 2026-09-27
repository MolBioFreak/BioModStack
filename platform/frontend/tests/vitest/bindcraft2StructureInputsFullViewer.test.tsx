import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, test, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import EpitopeMolstarViewer from '../../src/components/EpitopeMolstarViewerImpl';

// Keep the full Workbench → Host → Viewer → controller → loader path real.
// Only Mol*'s WebGL/plugin owner and byte transport are replaced.
const stats = vi.hoisted(() => ({ initialized: 0, disposed: 0, served: '', parsed: [] as string[] }));
vi.mock('../../src/structureViewer/runtime/createDirectMolstarEngineOwner', () => ({
    createDirectMolstarEngineOwner: () => {
        let disposed = false;
        return {
            initialize: async () => {
                stats.initialized++;
                return { status: 'ok', generation: stats.initialized, plugin: {
                    commands: { dispatch: async () => undefined },
                    canvas3d: { setProps: () => undefined, camera: { setState: () => undefined }, requestCameraReset: () => undefined },
                    managers: { interactivity: { setProps: () => undefined }, structure: { hierarchy: { current: { structures: [] } } } },
                    behaviors: { interaction: { click: { subscribe: () => ({ unsubscribe: () => undefined }) } } },
                    builders: {
                        data: { download: async () => { stats.parsed.push(stats.served); return {}; }, rawData: async (value: { data: string }) => { stats.parsed.push(value.data); return {}; } },
                        structure: { parseTrajectory: async () => ({}), hierarchy: { applyPreset: async () => undefined } },
                    },
                    clear: async () => undefined,
                } };
            },
            dispose: () => { if (!disposed) { disposed = true; stats.disposed++; } },
        };
    },
}));
let root: Root | undefined;
let host: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; host?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function settle(condition: () => boolean) {
    for (let i = 0; i < 100; i++) { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); if (condition()) return; }
    throw new Error(`Viewer did not settle: ${host.textContent}`);
}
test('full target/template inspection exposes real shared tools without reconstructing or reparsing the runtime', async () => {
    stats.initialized = 0; stats.disposed = 0; stats.parsed = [];
    vi.stubGlobal('crypto', webcrypto);
    const content = 'ATOM      1  CA  ALA A  10       1.000   2.000   3.000  1.00 20.00           C  \nEND\n';
    stats.served = content;
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => new TextEncoder().encode(content).buffer })));
    host = document.createElement('div'); document.body.append(host);
    await act(async () => { root = createRoot(host); root.render(<EpitopeMolstarViewer structureUrl="/fixture.pdb" sourceLabel="Template fixture" />); });
    await settle(() => stats.parsed.length === 1);
    const tools = host.querySelector('aside[aria-label="Structure metric workbench"]') as HTMLElement;
    expect(tools.hidden).toBe(true);
    const click = async (label: string) => {
        const node = [...host.querySelectorAll('button')].find(button => button.textContent === label);
        expect(node).toBeDefined();
        await act(async () => node!.click());
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    };
    await click('Open full viewer');
    expect(tools.hidden).toBe(false);
    expect(host.textContent).toContain('Exact atom measurements');
    expect(host.textContent).toContain('Filters');
    expect(stats.initialized).toBe(1);
    expect(stats.parsed).toEqual([content]);
    await click('Close full viewer');
    expect(tools.hidden).toBe(true);
    await click('Open full viewer');
    expect(host.querySelector('aside[aria-label="Structure metric workbench"]')).toBe(tools);
    expect(tools.hidden).toBe(false);
    expect(stats.initialized).toBe(1);
    expect(stats.disposed).toBe(0);
    expect(stats.parsed).toEqual([content]);
});
