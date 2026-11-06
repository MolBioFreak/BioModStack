import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { LaProteinaMotifInput } from '../../src/components/LaProteinaMotifInput';
import { api } from '../../src/lib/api';
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: () => null }));
const atom = (name: string, residue: number) => `ATOM      1 ${name.padStart(4)} ALA A${String(residue).padStart(4)}       7.000   2.000   3.000  1.00 20.00           C  `;
const pdb = [atom('CA', 1), atom('O', 1), atom('CA', 2), 'END', ''].join('\n');
let root: Root; let client: QueryClient; let saved: any;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function settle(condition: () => boolean) { for (let n = 0; n < 100; n++) { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); if (condition()) return; } throw Error(document.body.textContent ?? 'timeout'); }
async function click(text: string) { const button = [...document.querySelectorAll('button')].find(b => b.textContent === text)!; expect(button).toBeTruthy(); await act(async () => button.click()); }
async function mount() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.spyOn(api, 'get').mockImplementation(async url => ({ data: String(url).includes('presets') ? [] : { cached: [] } }) as any);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => pdb })));
    function Harness() { const [state, setState] = useState({ path: 'inputs/source.pdb', contig: '5/A1/5', segmentOrder: 'A', atomSpec: 'A1: [CA]' as string | null }); saved = state; return <LaProteinaMotifInput {...state} onChange={patch => setState(previous => ({ ...previous, ...patch }))} />; }
    await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
    await settle(() => !!document.querySelector('[title="A1 (ALA)"]'));
    await act(async () => (document.querySelector('[title="A1 (ALA)"]') as HTMLElement).click());
    const label = [...document.querySelectorAll('label')].find(l => l.textContent === 'A1 · O')!; expect(label).toBeTruthy(); await act(async () => label.querySelector('input')!.click());
}
it('atom adoption materializes the same selected source and sends native spec without removing unselected atoms', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { path: 'inputs/exact-motif.pdb' } }); await mount(); await click('Use selected motif atoms'); await settle(() => saved.path === 'inputs/exact-motif.pdb');
    expect(saved.atomSpec).toBe('A1: [O]'); expect(saved.contig).toBe('5/A1/5');
    const file = (post.mock.calls[0][1] as FormData).get('file') as File;
    const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(file); });
    expect(text).toBe([atom('CA', 1), atom('O', 1), 'END', ''].join('\n'));
    await click('Clear motif input'); expect(saved.atomSpec).toBe('A1: [O]');
});
it('a late atom adoption cannot overwrite a deliberate native specification edit', async () => {
    let resolve!: (value: any) => void; vi.spyOn(api, 'post').mockImplementation(() => new Promise(yes => { resolve = yes; })); await mount(); await click('Use selected motif atoms');
    const input = document.querySelector<HTMLInputElement>('[aria-label="Native motif atom specification"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'A1: [CA, O]'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => resolve({ data: { path: 'inputs/stale.pdb' } }));
    expect(saved.path).toBe('inputs/source.pdb'); expect(saved.atomSpec).toBe('A1: [CA, O]');
});
