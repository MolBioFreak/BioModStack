import React, { act } from 'react';
// jsdom's File lacks Blob.text(); use the Web File implementation from Node.
import { File } from 'node:buffer';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Rfd4SequenceEntry, type Rfd4SequenceDraft } from '../../src/components/Rfd4SequenceEntry';

let host: HTMLDivElement, root: Root;
const change = vi.fn();
const noNetwork = vi.fn(() => { throw new Error('Sequence entry must remain local'); });
beforeEach(() => {
    noNetwork.mockClear();
    vi.spyOn(globalThis, 'fetch').mockImplementation(noNetwork);
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(noNetwork);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host); change.mockReset(); });
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); expect(noNetwork).not.toHaveBeenCalled(); });
function render(value?: Rfd4SequenceDraft) {
    act(() => root.render(<Rfd4SequenceEntry value={value} onChange={change}><span>Strand builder</span></Rfd4SequenceEntry>));
}
function edit(node: HTMLTextAreaElement | HTMLSelectElement, value: string) {
    act(() => {
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value);
        node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
}
it('emits the raw paste as a draft only and restores it from parent props', () => {
    render();
    const text = '>same name\nMk V\n>same name\nGG\n';
    edit(host.querySelector('textarea')!, text);
    expect(change.mock.lastCall).toEqual([{ molecule: 'protein', text }]);
    expect(host.querySelector('textarea')!.value).toBe('');
    act(() => root.render(null));
    render(JSON.parse(JSON.stringify(change.mock.lastCall![0])));
    expect(host.querySelector('textarea')!.value).toBe(text);
    expect(host.textContent).toContain('2 FASTA records');
    expect(host.textContent).not.toContain('Strand builder');
});

it.each(['dna', 'rna'] as const)('keeps the raw draft when selecting %s and shows its construction controls', molecule => {
    const text = 'aC gN';
    render({ molecule: 'protein', text });
    edit(host.querySelector('select')!, molecule);
    expect(change.mock.lastCall).toEqual([{ molecule, text }]);
    render({ molecule, text });
    expect(host.querySelector('textarea')!.value).toBe(text);
    expect(host.textContent).toContain('Strand builder');
});
async function choose(file: File) {
    const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
}
it('imports FASTA locally without changing record headers, wrapping, or molecule', async () => {
    render({ molecule: 'rna', text: 'old' });
    const text = '>same\r\naU\r\nGc\r\n>same\r\nNN\r\n';
    await choose(new File([text], 'strand.fa'));
    expect(change.mock.calls).toEqual([[{ molecule: 'rna', text }]]);
});
it('keeps the existing draft when a file is not FASTA', async () => {
    render({ molecule: 'dna', text: 'old' });
    await choose(new File(['ATOM 1 N'], 'coords.pdb'));
    expect(host.querySelector('[role=alert]')?.textContent).toContain('Could not import FASTA');
    expect(change).not.toHaveBeenCalled();
    expect(host.querySelector('textarea')!.value).toBe('old');
});
it('discards a late file read after replacing the draft', async () => {
    render();
    const file = new File([], 'late.fa');
    let finish!: (text: string) => void;
    Object.defineProperty(file, 'text', { value: () => new Promise<string>(resolve => { finish = resolve; }) });
    await choose(file);
    render({ molecule: 'dna', text: 'new' });
    await act(async () => finish('>old\nMK'));
    expect(change).not.toHaveBeenCalled();
});
