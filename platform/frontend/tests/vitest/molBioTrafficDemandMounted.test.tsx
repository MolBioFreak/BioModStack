import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';
import { api } from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { PrimerPanel } from '../../src/components/MolBioToolkit/panels/PrimerPanel';
import { PCRPanel } from '../../src/components/MolBioToolkit/panels/PCRPanel';

vi.mock('../../src/components/MolBioToolkit/PrimerTmSettingsPanel', () => ({ PrimerTmSettingsPanel: () => null }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }) }));
const seq = { name: 'editable', sequence: 'ACGT'.repeat(20), circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const settings = { algorithm: 'wallace', salt_correction: 'none', primer_concentration_nM: 250, template_concentration_nM: 0, na_mM: 50, k_mM: 0, tris_mM: 0, mg_mM: 0, dntps_mM: 0, dmso_percent: 0, formamide_percent: 0, self_complementary: false };
const noop = () => {};
const options = { catalog: { catalog_id: 'test-catalog', catalog_sha256: 'a'.repeat(64) }, enzymes: [{ enzyme_id: 'BsaI', canonical_name: 'BsaI', overhang_length: 4 }, { enzyme_id: 'SapI', canonical_name: 'SapI', overhang_length: 3 }] };
const product = { sequence: 'ACGT', length: 4, circular: false, mode: 'ligation', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions: [] };
const library = Array.from({ length: 101 }, (_, i) => ({ id: `w-${i}`, name: `Workup ${i}`, length: 42, topology: 'linear', fragment_count: 2, primer_count: 4 }));
const assemblyProps = { sequenceData: seq, selection: null, selectedSequenceId: null, onLoadProduct: vi.fn(), onLoadSavedWorkup: vi.fn() };
const primerProps = { sequenceData: seq, selection: null, onHighlight: noop, onAddPrimer: vi.fn(), onRemovePrimer: noop, tmOptions: null, tmSettings: settings, onTmSettingsChange: noop };
let root: Root, host: HTMLDivElement, client: QueryClient;
let requests: InternalAxiosRequestConfig[];
let held: string | null;
let complete: (() => void)[];
const originalAdapter = api.defaults.adapter;
function count(path: string) { return requests.filter(r => r.url?.endsWith(path)); }
async function flush(ms = 1) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function render(node: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)); await flush(); }
async function click(label: string) { const button = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(button, label).toBeDefined(); await act(async () => button!.click()); await flush(); }
async function input(el: HTMLInputElement, value: string) { await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); }); }
beforeEach(() => {
    vi.useFakeTimers(); requests = []; held = null; complete = [];
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.defaults.adapter = async config => {
        requests.push(config);
        let data: unknown;
        const body = config.data ? JSON.parse(config.data) : null;
        if (config.url?.endsWith('/golden-gate/options')) data = options;
        else if (config.url?.endsWith('/assembly-workups')) data = library.slice(config.params?.offset ?? 0, (config.params?.offset ?? 0) + (config.params?.limit ?? 100));
        else if (config.url?.endsWith('/primer-tm/calculate')) data = body.primers.map((p: { id?: string }) => ({ id: p.id, tm: 60, gc_percent: 50, algorithm: 'wallace', salt_correction: 'none', warnings: [] }));
        else if (config.url?.endsWith('/primer-design')) data = { pairs: [], pair_count: 0, warnings: [], target_start: 0, target_end: 80 };
        else if (config.url?.endsWith('/gibson/dnaweaver/plan')) data = { ordered_fragments: [], selected_product: { ...product, mode: 'gibson' }, quality_checks: [], order_ready: false };
        else if (config.url?.endsWith('/primer-qc')) data = { primers: [], pairwise: [] };
        else if (config.url?.endsWith('/primers')) data = [];
        else if (config.url?.endsWith('/ligation/simulate') || config.url?.endsWith('/ligation/save')) data = { product };
        else if (config.url?.endsWith('/pcr')) data = { product: { sequence: 'ACGT', length: 4 }, experiment_id: null, experiment_revision_id: null };
        else throw Error(`Unexpected transport: ${config.url}`);
        // Deliberately complete even after abort, to exercise stale-result guards.
        if (held && config.url?.endsWith(held)) await new Promise<void>(resolve => complete.push(resolve));
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = originalAdapter; vi.useRealTimers(); });

it('cold Ligation/remount/idle does no options or shelf work; GG discovery is shared, fresh and release-invalidated', async () => {
    await render(<AssemblyPanel {...assemblyProps} />);
    expect(requests.map(request => request.url)).toEqual([]);
    expect(host.textContent).toContain('Saved workups (not loaded)');
    await flush(30_000); await render(null); await render(<AssemblyPanel {...assemblyProps} />);
    expect(requests).toHaveLength(0);
    await click('Golden Gate'); expect(count('/golden-gate/options')).toHaveLength(1);
    await flush(30_000); expect(requests).toHaveLength(1);
    await render(null); await render(<AssemblyPanel {...assemblyProps} />); await click('Golden Gate');
    expect(requests).toHaveLength(1);
    await act(async () => { client.setQueryData(['molbio-restriction-catalog'], { catalog: { catalog_id: 'updated' } }); }); await flush();
    expect(count('/golden-gate/options')).toHaveLength(2);
    await click('Ligation'); await flush(300_001); await click('Golden Gate');
    expect(count('/golden-gate/options')).toHaveLength(3);
    await render(null); await flush(1);
    client.setQueryData(['molbio-restriction-product-release'], { release_id: 'new-release' });
    await render(<AssemblyPanel {...assemblyProps} />); await click('Golden Gate');
    expect(count('/golden-gate/options')).toHaveLength(4);
    expect(count('/assembly-workups')).toHaveLength(0);
});
it('explicit shelf open/pages/remount/refresh reaches every record and selected historical detail only', async () => {
    const load = vi.fn(); await render(<AssemblyPanel {...assemblyProps} onLoadSavedWorkup={load} />);
    await click('Saved workups (not loaded)');
    expect(count('/assembly-workups').map(r => r.params)).toEqual([{ limit: 50, offset: 0 }]);
    expect(host.querySelectorAll('article')).toHaveLength(50);
    await click('Load more workups'); expect(host.querySelectorAll('article')).toHaveLength(100);
    await click('Close'); await render(null); await render(<AssemblyPanel {...assemblyProps} onLoadSavedWorkup={load} />);
    await click('Saved workups (100 loaded)'); expect(count('/assembly-workups')).toHaveLength(2);
    await click('Load more workups');
    expect(count('/assembly-workups').map(r => r.params.offset)).toEqual([0, 50, 100]);
    expect(host.querySelectorAll('article')).toHaveLength(101); expect(host.textContent).toContain('All saved workups loaded.');
    expect(host.textContent).not.toContain('Load more workups');
    await act(async () => host.querySelectorAll<HTMLButtonElement>('article button')[100].click());
    expect(load).toHaveBeenCalledExactlyOnceWith('w-100');
    await click('Refresh'); expect(count('/assembly-workups').map(r => r.params.offset)).toEqual([0, 50, 100, 0, 50, 100]);
});
it('empty continuation ends an exactly-full shelf', async () => {
    const adapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const response = await (adapter as Function)(config);
        if (config.params.offset === 100) response.data = [];
        return response;
    };
    await render(<AssemblyPanel {...assemblyProps} />); await click('Saved workups (not loaded)'); await click('Load more workups'); await click('Load more workups');
    expect(host.textContent).toContain('All saved workups loaded.'); expect(host.querySelectorAll('article')).toHaveLength(100);
});
it('failed first shelf page remains unknown until explicit refresh succeeds', async () => {
    const adapter = api.defaults.adapter;
    api.defaults.adapter = async () => { throw Error('Shelf temporarily unavailable'); };
    await render(<AssemblyPanel {...assemblyProps} />); await click('Saved workups (not loaded)');
    expect(host.textContent).toContain('Could not load saved workups');
    expect(host.textContent).not.toContain('No saved Gibson workups yet');
    expect(host.textContent).not.toContain('(0 loaded)');
    api.defaults.adapter = adapter; await click('Refresh');
    expect(host.querySelectorAll('article')).toHaveLength(50);
});
it('options abort on mode switch and unmount; late discovery cannot replace a restored enzyme', async () => {
    held = '/golden-gate/options'; await render(<AssemblyPanel {...assemblyProps} />); await click('Golden Gate');
    const request = count('/golden-gate/options')[0]; expect(request.signal?.aborted).toBe(false);
    await click('Ligation'); expect(request.signal?.aborted).toBe(true);
    await act(async () => complete.shift()!()); await flush();
    await click('Golden Gate'); const next = count('/golden-gate/options')[1];
    await render(null); expect(next.signal?.aborted).toBe(true);
    await act(async () => complete.shift()!()); held = null;
    const operationParams = { mode: 'golden_gate', assembly_request: { enzyme_id: 'SapI', fragments: [] } };
    await render(<AssemblyPanel {...assemblyProps} sequenceData={{ ...seq, operationParams }} />); await flush();
    expect([...host.querySelectorAll('select')].find(s => [...s.options].some(o => o.value === 'SapI'))?.value).toBe('SapI');
});
it('preview aborts on edit but persistence stays in flight and invalidates only an explicitly opened shelf', async () => {
    await render(<AssemblyPanel {...assemblyProps} />); await click('Add whole construct');
    held = '/ligation/simulate'; await click('Simulate'); const preview = count('/ligation/simulate')[0];
    await click('Golden Gate'); expect(preview.signal?.aborted).toBe(true);
    await act(async () => complete.shift()!()); await flush(); expect(host.textContent).not.toContain('Load product');
    await click('Ligation'); held = '/ligation/save'; await click('Validate + Save');
    const save = count('/ligation/save')[0]; expect(save.signal).toBeUndefined();
    await render(null); await act(async () => complete.shift()!());
    expect(count('/assembly-workups')).toHaveLength(0);
    held = null; await render(<AssemblyPanel {...assemblyProps} />); await click('Saved workups (not loaded)'); await click('Close');
    await click('Add whole construct'); await click('Validate + Save'); expect(count('/assembly-workups')).toHaveLength(1);
    await click('Saved workups (50 loaded)'); expect(count('/assembly-workups')).toHaveLength(2);
});
it('Primer debounce sends final draft only, aborts superseded Tm/QC and keeps inactive drafts idle', async () => {
    await render(<PrimerPanel {...primerProps} />);
    const field = host.querySelector<HTMLInputElement>('input[placeholder="Sequence (5\'→3\')"]')!;
    await input(field, 'ACGTACGTACGT'); await input(field, 'ACGTACGTACGTACGT');
    expect(count('/primer-tm/calculate')).toHaveLength(0);
    held = '/primer-tm/calculate'; await flush(250); const first = count('/primer-tm/calculate')[0];
    expect(JSON.parse(first.data).primers).toHaveLength(1);
    await input(field, 'ACGTACGTACGTACGTACGT'); expect(first.signal?.aborted).toBe(true);
    await flush(250); expect(count('/primer-tm/calculate')).toHaveLength(2);
    const second = count('/primer-tm/calculate')[1]; await click('Library'); expect(second.signal?.aborted).toBe(true);
    await act(async () => complete.splice(0).forEach(resolve => resolve())); await flush(30_000);
    expect(count('/primer-tm/calculate')).toHaveLength(2);
});
it('explicit primer design and vendor plan abort on changed inputs without automatic replacement computation', async () => {
    held = '/primer-design'; await render(<PrimerPanel {...primerProps} />); await click('Design');
    const run = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Design') && b.textContent?.trim() !== 'Design')!;
    await act(async () => run.click()); await flush();
    const design = count('/primer-design')[0]; expect(design.signal?.aborted).toBe(false);
    await render(<PrimerPanel {...primerProps} tmSettings={{ ...settings, na_mM: 80 }} />);
    expect(design.signal?.aborted).toBe(true); await flush(30_000); expect(count('/primer-design')).toHaveLength(1);
    await act(async () => complete.splice(0).forEach(resolve => resolve()));
    held = '/gibson/dnaweaver/plan'; await render(<AssemblyPanel {...assemblyProps} />); await click('Gibson'); await click('Plan vendor fragments');
    const plan = count('/gibson/dnaweaver/plan')[0]; expect(plan.signal?.aborted).toBe(false);
    await render(<AssemblyPanel {...assemblyProps} sequenceData={{ ...seq, sequence: seq.sequence + 'A' }} />);
    expect(plan.signal?.aborted).toBe(true); await flush(30_000); expect(count('/gibson/dnaweaver/plan')).toHaveLength(1);
    await act(async () => complete.splice(0).forEach(resolve => resolve())); expect(host.textContent).not.toContain('Load product');
});
it('PCR keeps one batched debounced Tm request and cancels preview, never a persisted operation', async () => {
    const pcr = (sequenceId?: string) => <PCRPanel sequenceData={seq} sequenceId={sequenceId} onHighlight={noop} tmOptions={null} tmSettings={settings} onTmSettingsChange={noop} />;
    await render(pcr()); let fields = host.querySelectorAll<HTMLInputElement>('input[placeholder="ATGC..."]');
    await input(fields[0], 'ACGTACGTACGTACGT'); await input(fields[1], 'ACGTACGTACGTACGT'); held = '/primer-tm/calculate'; await flush(250);
    expect(count('/primer-tm/calculate')).toHaveLength(1); const tm = count('/primer-tm/calculate')[0];
    expect(JSON.parse(tm.data).primers.map((p: { id: string }) => p.id)).toEqual(['forward', 'reverse']);
    await input(fields[0], 'ACGTACGTACGTACGTACGT'); expect(tm.signal?.aborted).toBe(true);
    held = '/pcr'; await click('Run PCR'); const preview = count('/pcr')[0]; await render(null); expect(preview.signal?.aborted).toBe(true);
    await act(async () => complete.splice(0).forEach(resolve => resolve()));
    await render(pcr('stored')); fields = host.querySelectorAll<HTMLInputElement>('input[placeholder="ATGC..."]');
    await input(fields[0], 'ACGTACGTACGTACGT'); await input(fields[1], 'ACGTACGTACGTACGT'); await click('Run PCR');
    const saved = count('/pcr')[1]; expect(JSON.parse(saved.data).persist_experiment).toBe(true); expect(saved.signal).toBeUndefined();
    await render(null); await act(async () => complete.splice(0).forEach(resolve => resolve()));
});
