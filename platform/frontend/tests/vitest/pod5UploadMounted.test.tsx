import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ uploadFile: vi.fn() }));
vi.mock('../../src/lib/api', () => mocks);
import { Pod5Upload } from '../../src/components/ngs/Pod5Upload';
let container: HTMLDivElement; let root: Root; let client: QueryClient;
const selected = vi.fn(); const busy = vi.fn();
beforeEach(async () => {
    vi.clearAllMocks();
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><Pod5Upload selectedDirectory="" onSelectDirectory={selected} onBusyChange={busy} /></QueryClientProvider>));
});
afterEach(() => { act(() => root.unmount()); container.remove(); client.clear(); });
async function choose(names: string[]) {
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', { configurable: true, value: names.map(name => new File(['fixture'], name)) });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function until(assertion: () => void) {
    await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); assertion(); });
}
describe('POD5 analysis input uploads', () => {
    it('selects one isolated folder only after every selected file has uploaded', async () => {
        let release!: (value: unknown) => void;
        mocks.uploadFile.mockImplementationOnce(async (path: string, file: File) => ({ data: { path: `${path}/${file.name}` } }))
            .mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
        await choose(['one.pod5', 'two.pod5']);
        await until(() => expect(mocks.uploadFile).toHaveBeenCalledTimes(2));
        expect(selected.mock.calls).toEqual([['']]); expect(busy).toHaveBeenLastCalledWith(true);
        expect(container.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
        const folder = mocks.uploadFile.mock.calls[0][0];
        expect(folder).toMatch(/^inputs\/ont-analysis\/[0-9a-f-]+$/);
        expect(mocks.uploadFile.mock.calls[1][0]).toBe(folder);
        await act(async () => release({ data: { path: `${folder}/two.pod5` } }));
        await until(() => expect(selected).toHaveBeenLastCalledWith(folder));
        expect(busy).toHaveBeenLastCalledWith(false);
        expect(container.textContent).toContain('2 POD5 files uploaded');
    });
    it('does not admit a partial upload after a transport failure', async () => {
        mocks.uploadFile.mockImplementationOnce(async (path: string, file: File) => ({ data: { path: `${path}/${file.name}` } }))
            .mockRejectedValueOnce(new Error('Connection interrupted'));
        await choose(['one.pod5', 'two.pod5']);
        await until(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain('Connection interrupted'));
        expect(selected.mock.calls).toEqual([['']]); expect(busy).toHaveBeenLastCalledWith(false);
    });
    it('does not replace a later input selection when an upload finishes after leaving the view', async () => {
        let release!: (value: unknown) => void;
        mocks.uploadFile.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
        await choose(['one.pod5', 'two.pod5']);
        await until(() => expect(mocks.uploadFile).toHaveBeenCalledTimes(1));
        await act(async () => root.render(<div>Another input</div>));
        await act(async () => release({ data: { path: `${mocks.uploadFile.mock.calls[0][0]}/one.pod5` } }));
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(mocks.uploadFile).toHaveBeenCalledTimes(1);
        expect(selected.mock.calls).toEqual([['']]);
    });
    it.each([['wrong.fastq'], ['one.pod5', 'ONE.POD5']])('rejects invalid file selections before uploading: %s', async (...names) => {
        await choose(names);
        await until(() => expect(container.querySelector('[role="alert"]')).not.toBeNull());
        expect(mocks.uploadFile).not.toHaveBeenCalled(); expect(selected.mock.calls).toEqual([['']]);
    });
    it('rejects an unusable server path without selecting it', async () => {
        mocks.uploadFile.mockResolvedValueOnce({ data: {} });
        await choose(['one.pod5']);
        await until(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain('usable POD5 input folder'));
        expect(selected.mock.calls).toEqual([['']]);
    });
});
