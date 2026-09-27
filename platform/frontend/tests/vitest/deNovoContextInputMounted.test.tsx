import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DeNovoContextInput, type DeNovoContextInputProps } from '../../src/components/DeNovoContextInput';
import { FileBrowser } from '../../src/components/FileBrowser';

const transport = vi.hoisted(() => ({ files: vi.fn(), upload: vi.fn() }));
vi.mock('../../src/lib/api', () => ({
    fetchFiles: transport.files, uploadFile: transport.upload,
}));
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const change = vi.fn();
const file = (name: string, path = `/inputs/${name}`) => ({ name, path, size_bytes: 128, is_directory: false });
const initial = { ligandPath: '', ligandName: '', nativeJsonPath: '', sequence: '' };
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); };
const click = async (text: string) => {
    const el = [...container.querySelectorAll<HTMLElement>('button,[role="button"]')].find(node => node.textContent?.trim() === text);
    expect(el, text).toBeTruthy();
    await act(async () => el!.click()); await settle();
};
function Harness({ task }: { task: DeNovoContextInputProps['task'] }) {
    const [state, setState] = useState(initial);
    return <DeNovoContextInput task={task} {...state} onChange={patch => { change(patch); setState(old => ({ ...old, ...patch })); }} />;
}
async function render(node: React.ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
    await settle();
}
async function upload(name: string) {
    const input = container.querySelector<HTMLInputElement>('input[type=file]')!;
    await act(async () => {
        Object.defineProperty(input, 'files', { configurable: true, value: [new File(['inert fixture'], name)] });
        input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();
}
beforeEach(() => {
    vi.clearAllMocks();
    transport.files.mockResolvedValue({ data: { entries: [file('one.sdf', '/inputs/exact path/../one.sdf'), file('two.json'), file('other.pdb'), { name: 'inputs', path: '/inputs', is_directory: true }] } });
    transport.upload.mockResolvedValue({ data: { path: '/inputs/new.sdf' } });

    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });

it('preserves historical unfiltered browsing, root uploads, navigation and explicit selection', async () => {
    const select = vi.fn();
    await render(<FileBrowser onSelect={select} onCancel={() => {}} />);
    expect(container.textContent).toContain('Select File');
    expect(container.textContent).toContain('other.pdb');
    await upload('new.pdb');
    expect(transport.upload).toHaveBeenCalledWith('inputs', expect.any(File));
    expect(select).not.toHaveBeenCalled();
    await click('Dirinputs');
    expect(transport.files).toHaveBeenCalledWith('/inputs');
    await click('Up');
    await click('Fileone.sdf0.1 KB');
    expect(select).toHaveBeenCalledWith('/inputs/exact path/../one.sdf');
});

it.each([['ligand_conditioned', 'Ligand SDF', '.sdf', 'one.sdf', 'two.json', 'ligandPath', '/inputs/exact path/../one.sdf'], ['custom_json', 'Native input JSON', '.json', 'two.json', 'one.sdf', 'nativeJsonPath', '/inputs/two.json']] as const)
('uses the actual managed picker for %s and emits only the exact active path', async (task, label, accept, visible, hidden, key, path) => {
    await render(<Harness task={task} />);
    expect(container.querySelector('details')?.open).toBe(false);
    await click(`Upload / browse ${label}`);
    expect(container.querySelector('input[type=file]')?.getAttribute('accept')).toBe(accept);
    expect(container.textContent).toContain(visible); expect(container.textContent).not.toContain(hidden);
    await click(`File${visible}0.1 KB`);
    expect(change).toHaveBeenLastCalledWith({ [key]: path });
    expect(container.querySelector(`[aria-label="Selected ${label}"]`)?.textContent).toBe(path);
});

it.each(['dna_conditioned', 'rna_conditioned'] as const)('preserves native scalar values for %s without protein-library or duplex semantics', async task => {
    await render(<Harness task={task} />);
    const input = container.querySelector('textarea')!;
    const literal = ' aCgU n-\n';
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, literal);
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(change).toHaveBeenLastCalledWith({ sequence: literal });
    expect(input.value).toBe(literal);
    expect(container.textContent).not.toContain('Sequence Library');
    expect(transport.files).not.toHaveBeenCalled();
    await click('Clear sequence'); expect(change).toHaveBeenLastCalledWith({ sequence: '' });
});

it('refreshes the managed listing after upload and selects only on explicit choice', async () => {
    await render(<Harness task="ligand_conditioned" />);
    await click('Upload / browse Ligand SDF');
    transport.files.mockResolvedValue({ data: { entries: [file('new.sdf', '/inputs/Uploaded Name.sdf')] } });
    await upload('new.sdf');
    expect(transport.upload).toHaveBeenCalledWith('inputs', expect.any(File));
    expect(change).not.toHaveBeenCalled();
    await click('Filenew.sdf0.1 KB');
    expect(change).toHaveBeenLastCalledWith({ ligandPath: '/inputs/Uploaded Name.sdf' });
});

it.each(['clear', 'switch', 'cancel'] as const)('does not publish a late upload after %s', async action => {
    let finish!: (value: unknown) => void;
    transport.upload.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await render(<Harness task="ligand_conditioned" />);
    await click('Upload / browse Ligand SDF'); await upload('late.sdf');
    if (action === 'clear') await click('Clear Ligand SDF');
    else if (action === 'switch') await render(<Harness task="custom_json" />);
    else await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Close file browser"]')!.click());
    change.mockClear();
    await act(async () => finish({ data: { path: '/inputs/late.sdf' } })); await settle();
    expect(change).not.toHaveBeenCalled();
    expect(container.querySelector('input[type=file]')).toBeNull();
});

it('shows managed browse and upload failures without changing the selected source', async () => {
    transport.files.mockRejectedValue(new Error('Browse unavailable'));
    await render(<FileBrowser onSelect={change} onCancel={() => {}} accept=".sdf" />);
    expect(container.querySelector('[role=alert]')?.textContent).toBe('Browse unavailable');
    transport.files.mockResolvedValue({ data: { entries: [] } });
    await act(async () => { await client.invalidateQueries({ queryKey: ['files'] }); }); await settle();
    transport.upload.mockRejectedValue(new Error('Upload refused'));
    await upload('bad.sdf');
    expect(container.querySelector('[role=alert]')?.textContent).toBe('Upload refused');
    expect(change).not.toHaveBeenCalled();
});
