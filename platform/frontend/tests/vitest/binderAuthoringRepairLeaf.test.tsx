import React, { act, useState } from 'react';
import { webcrypto, createHash } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { NativeBinderSource } from '../../src/components/NativeBinderSource';
import { NativeBinderGeneration } from '../../src/components/NativeBinderGeneration';
import { BindCraft2MetricEditor } from '../../src/components/BindCraft2MetricEditor';
import { hydrateBinderRound, toggleRoundStage } from '../../src/lib/binderRound';
import { readBC2Source } from '../../src/lib/bindcraft2StructureInputs';
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (p: any) => <output data-viewer>{p.structureData}</output> }));
let root: Root | undefined; let client: QueryClient; let values: any;
async function mount(element: React.ReactNode) { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); await act(async () => root!.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await settle(); }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const pdb = ['ATOM      1  CA  GLY A   5       0.000   0.000   0.000  1.00 20.00           C  ', 'ATOM      2  CA  GLY A  19       4.000   0.000   0.000  1.00 20.00           C  ', 'ATOM      3  CA  GLY A  19B      8.000   0.000   0.000  1.00 20.00           C  ', 'ATOM      4  CA  GLY A  42      12.000   0.000   0.000  1.00 20.00           C  ', 'END', ''].join('\n');
async function click(text: string) { const b = [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!; expect(b, text).toBeTruthy(); await act(async () => b.click()); await settle(); }
it('exact author insertion selection adopts native BoltzGen positions without replacing source or other settings', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const transport = vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => pdb })));
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { schema_version: 1, source_sha256: createHash('sha256').update(pdb).digest('hex'), positions: [[5, '', 1], [19, '', 2], [19, 'B', 3], [42, '', 4]].map(([number, insertion, position]) => ({ model_number: 1, auth_asym_id: 'A', auth_seq_id: number, insertion_code: insertion, native_chain: 'A', native_position: position })) } });
    function Harness() { const [state, set] = useState({ target_pdb: 'inputs/exact.pdb', seed: 0, target_binding_positions: '', target_pdb_inspection_residues: [{ documentId: 'inputs/exact.pdb', authAsymId: 'A', authSeqId: 19, insertionCode: 'B' }, { documentId: 'inputs/exact.pdb', authAsymId: 'A', authSeqId: 42 }] }); values = state; return <NativeBinderSource model="boltzgen" mode="protein_binder" field="target_pdb" label="Target" values={state} onPatch={patch => set(old => ({ ...old, ...patch }))} onChains={() => {}} />; }
    await mount(<Harness />); await click('Use inspected residues as native BoltzGen binding positions');
    expect(post).toHaveBeenCalledWith('/api/boltzgen/source-positions', { path: 'inputs/exact.pdb' }, expect.objectContaining({ timeout: 30000 }));
    expect(values).toMatchObject({ target_pdb: 'inputs/exact.pdb', seed: 0, target_binding_positions: 'A:3,A:4' }); expect(transport).toBeDefined();
});
it('PPIFlow required copy follows PDB-or-CSV edits while preserving both requested values', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => pdb })));
    const parameters: any[] = [{ name: 'target_pdb', type: 'file', required: true, display_required: true, required_when: { field: 'input_csv', operator: 'falsy' } }, { name: 'input_csv', type: 'file', required_when: { field: 'target_pdb', operator: 'falsy' } }];
    function Harness() { const [state, set] = useState({ input_csv: 'inputs/table.csv', target_pdb: '' }); values = state; return <><NativeBinderGeneration model="ppiflow" mode="protein_binder" parameters={parameters} values={state} onPatch={patch => set(old => ({ ...old, ...patch }))} /><button onClick={() => set({ input_csv: '', target_pdb: 'inputs/exact.pdb' })}>Choose PDB</button></>; }
    await mount(<Harness />); expect(document.querySelector('[data-native-required-summary]')!.textContent).not.toContain('Target pdb'); expect(values).toEqual({ input_csv: 'inputs/table.csv', target_pdb: '' });
    await click('Choose PDB'); expect(document.querySelector('[data-native-required-summary]')!.textContent).toContain('Target pdb'); expect(document.querySelector('[data-native-required-summary]')!.textContent).not.toContain('Input csv');
});
it('finite, null and native nonfinite thresholds remain readable and serialized unchanged', async () => {
    const inherited = { finite: { threshold: 0, higher: true }, negative: { threshold: { $bc2_nonfinite_float: '-Infinity' }, higher: true }, positive: { threshold: { $bc2_nonfinite_float: 'Infinity' }, higher: false }, missing: { threshold: { $bc2_nonfinite_float: 'NaN' } }, off: { threshold: null } };
    const change = vi.fn(); const before = JSON.stringify(inherited);
    await mount(<BindCraft2MetricEditor kind="filters" inventory={{ registered_metrics: { filters: Object.fromEntries(Object.keys(inherited).map(key => [key, { params: {} }])) } } as any} value={{}} inherited={inherited} onChange={change} />);
    expect(document.body.textContent).not.toContain('[object Object]'); for (const text of ['≥ 0', '≥ -Infinity', '≤ Infinity', 'NaN', 'Disabled (null)']) expect(document.body.textContent).toContain(text);
    expect(JSON.stringify(inherited)).toBe(before); expect(change).not.toHaveBeenCalled();
});
it('null and multi-engine envelopes retain explicit false zero null and empty through JSON reopen', () => {
    expect(hydrateBinderRound({ binder_round: null }).binder_round.enabled).toBe(false);
    let draft = hydrateBinderRound(); draft = toggleRoundStage(draft, 'prediction', 'boltz2'); draft.binder_round_drafts.esmfold2 = { seed: 0, custom: null, empty: '', enabled: false }; draft = toggleRoundStage(draft, 'prediction', 'esmfold2');
    expect(hydrateBinderRound(JSON.parse(JSON.stringify(draft)))).toEqual(draft);
});
it('historical exact source reopens offline without reacquiring an ancestor or replacing scientific fields', async () => {
    const fetcher = vi.fn(async (_url: string) => ({ ok: false, status: 404 })); vi.stubGlobal('fetch', fetcher);
    const post = vi.spyOn(api, 'post'); const patch = vi.fn();
    const retained = { target_pdb: 'inputs/abcd.pdb', target_pdb_source_reference: { name: 'Exact retained', path: 'inputs/abcd.pdb', url: '/ancestor/primary', designId: 'deleted-worker-design' }, target_binding_positions: 'A:3' };
    await mount(<NativeBinderSource model="boltzgen" mode="protein_binder" field="target_pdb" label="Target" values={retained} onPatch={patch} onChains={() => {}} />);
    expect(document.body.textContent).toContain('no substitute was loaded'); expect(patch).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
    await click('Retry exact source'); expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls.every(call => String(call[0]) === '/api/files/download/inputs%2Fabcd.pdb')).toBe(true);
});
it('historical path read times out boundedly without producer/primary fallback', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_url: string, options: any) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))));
    vi.stubGlobal('fetch', fetcher);
    const pending = readBC2Source({ name: 'Historical exact', path: 'results/exact.cif', url: '/never-primary', designId: 'old' });
    const check = expect(pending).rejects.toThrow('exact source remains selected');
    // Attach rejection observation before advancing the timer.
    await vi.advanceTimersByTimeAsync(30000);
    await check;
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe('/api/files/download/results%2Fexact.cif');
});
