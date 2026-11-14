// @vitest-environment jsdom
import React from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/lib/api', () => ({
    uploadFile: vi.fn(async (directory: string, file: File) => ({ data: { path: `${directory}/${file.name}` } })),
    importOntRawSignalUpload: vi.fn(async () => ({ run_id: 'run', observed_generation: 1, representation: { representation_id: 'source' } })),
    fetchOntRawSignalCapabilities: vi.fn(async () => ({ representations: [{ format: 'blow5', state: 'ready', representation_id: 'blow5', role: 'derived' }] })),
    requestOntBlow5Preparation: vi.fn(),
    requestOntPod5Restore: vi.fn(async () => ({ state: 'ready', directory: '/restored/A', restored_read_count: 3 })),
    fetchOntPod5Restore: vi.fn(),
}));

import { RawSignalPod5Input } from '../../src/components/ngs/RawSignalPod5Input';

describe('RawSignalPod5Input', () => {
    let container: HTMLDivElement;
    let root: Root;
    beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
    afterEach(() => { act(() => root.unmount()); container.remove(); });

    const choose = async (name: string) => {
        const input = container.querySelector('input[aria-label="BLOW5 or SLOW5 file"]') as HTMLInputElement;
        Object.defineProperty(input, 'files', { configurable: true, value: [new File(['x'], name)] });
        await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
    };

    it('clears the restored POD5 as soon as a different raw file is chosen', async () => {
        const selected: string[] = [];
        await act(async () => root.render(<RawSignalPod5Input domainExperimentId="experiment" onPod5Directory={value => selected.push(value)} onBusyChange={() => {}} />));
        await choose('a.blow5');
        const prepare = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('Prepare POD5')) as HTMLButtonElement;
        await act(async () => { prepare.click(); await new Promise(resolve => setTimeout(resolve, 20)); });
        expect(selected.at(-1)).toBe('/restored/A');
        expect(container.textContent).toContain('/restored/A');

        await choose('b.blow5');
        expect(selected.at(-1)).toBe('');
        expect(container.textContent).not.toContain('/restored/A');
    });
});
