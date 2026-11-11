import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import fixture from '../fixtures/golden-gate/workflow-receiving.json';
import { expandGoldenGateWire } from '../../src/lib/goldenGateWorkflowWire';
import { mapSeqVizSelectionToSource } from '../../src/components/MolBioToolkit/utils/selectionActions';
import type { SelectionInfo, SequenceData } from '../../src/components/MolBioToolkit/types';

vi.mock('seqviz', async original => {
    const actual = await original<typeof import('seqviz')>();
    return { ...actual, SeqViz: (props: React.ComponentProps<typeof actual.SeqViz>) => <actual.SeqViz {...props} disableExternalFonts testSize={{ width: 1000, height: 700 }} /> };
});
const sequence = 'ACGTTGCAAGTCCTAGGATCACTGAGTACCGT';
const seq = { name: 'opened DNA', sequence, circular: true, sequenceType: 'dna' as const, features: [], primers: [] };
const original = api.defaults.adapter;
let host: HTMLDivElement, root: Root, client: QueryClient;
let suggestion: unknown;
let calls: Array<{ url: string; body: any }>;
const options = { enzymes: [
    { enzyme_id: 'native-new-enzyme', canonical_name: 'NewTypeIIS', overhang_length: 4 },
    { enzyme_id: 'native-BsaI', canonical_name: 'BsaI', overhang_length: 4 },
], raw_design: { raw_enzyme_adapters: ['NewTypeIIS', 'BsaI'], datasets: [], limitations: [] } };
async function render(selection: SelectionInfo | null = null, sequenceData: SequenceData = seq) {
    await act(async () => root.render(<QueryClientProvider client={client}><AssemblyPanel sequenceData={sequenceData} selection={selection} selectedSequenceId={null} onLoadProduct={() => {}} onLoadSavedWorkup={() => {}} /></QueryClientProvider>));
}
async function click(text: string) {
    const b = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text);
    expect(b, text).toBeDefined(); await act(async () => b!.click());
}
async function input(label: string, value: string) {
    const el = [...host.querySelectorAll('input,select')].find(el => el.getAttribute('aria-label') === label || el.closest('label')?.textContent?.trim().startsWith(label)) as HTMLInputElement;
    expect(el, label).toBeDefined();
    if (el instanceof HTMLSelectElement) await vi.waitFor(async () => { await act(async () => { await new Promise(r => setTimeout(r, 5)); }); expect([...el.options].some(o => o.value === value)).toBe(true); });
    await act(async () => {
        const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    suggestion = { proposals: [], enzymes: [], diagnostics: [] };
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); calls = [];
    api.defaults.adapter = async config => {
        const url = config.url!;
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body });
        let data: unknown;
        if (url.endsWith('/golden-gate/options')) data = options;
        else if (url.endsWith('/gibson/workups')) data = [];
        else if (url.endsWith('/golden-gate/suggest')) data = suggestion;
        else if (url.endsWith('/primer-tm/options')) data = fixture.tm_options;
        else if (url.endsWith('/assembly-workups')) data = [];
        else if (url.endsWith('/design')) data = fixture.split_target.result;
        else if (url.endsWith('/design/save')) data = fixture.saved;
        else if (url.endsWith('/design/export')) data = new Blob(['Transport-only response; archive bytes qualified by the API owner']);
        else throw new Error(`Unexpected HTTP ${url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => {
    if (process.env.BMS_GG_TARGET_UI_EVIDENCE) {
        mkdirSync(process.env.BMS_GG_TARGET_UI_EVIDENCE, { recursive: true });
        writeFileSync(join(process.env.BMS_GG_TARGET_UI_EVIDENCE, `${expect.getState().currentTestName!.replace(/[^a-z0-9]+/gi, '-').slice(0, 180)}.json`), JSON.stringify(calls, null, 2));
    }
    await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = original; vi.restoreAllMocks(); vi.unstubAllGlobals();
});
it.each([
    { selection: { start: 26, end: 5 }, expected: sequence.slice(26) + sequence.slice(0, 5) },
    { selection: { start: 4, end: 20 }, expected: sequence.slice(4, 20) },
    // The shared SeqViz owner already maps reverse-display drags to source coordinates.
    { selection: mapSeqVizSelectionToSource({ start: 12, end: 28 }, sequence.length, false, true, null)!, expected: sequence.slice(4, 20) },
])('serializes selected source-space range $selection as its own linear target with exact fixed cuts and sizing', async ({ selection, expected }) => {
    await render(selection); await click('Golden Gate');
    await input('Target molecule', 'selection');
    await input('Type IIS enzyme', 'native-BsaI');
    await input('Target fragment length (bp)', '6');
    await input('Minimum fragment length (bp)', '4');
    await input('Maximum fragment length (bp)', '12');
    await input('Fixed boundaries', '3, 7');
    await input('Terminal right fusion', 'TGAC');
    expect(calls.filter(c => c.url.endsWith('/suggest'))).toHaveLength(0);
    await click('Recommend cuts');
    expect(calls.filter(c => c.url.endsWith('/golden-gate/suggest')).at(-1)?.body).toMatchObject({
        target: { source: { kind: 'inline', sequence: expected, topology: 'linear' } },
        enzymes: ['native-BsaI'], fixed_boundaries: [3, 7], fragment_count: 3,
        target_fragment_length: 6, min_fragment_length: 4, max_fragment_length: 12, avoid_repeats: true,
    });
    expect(host.textContent).toContain('Cut after bp N = boundary N');
});
it('shows the selected enzyme’s real internal-site refusal instead of a prepared-fragment error', async () => {
    suggestion = { proposals: [], enzymes: [{ enzyme_id: 'native-BsaI', name: 'BsaI', internal_sites: 4, empirical: 'no empirical data', status: 'no proposal: target contains internal recognition sites' }], diagnostics: [] };
    await render(); await click('Golden Gate'); await input('Type IIS enzyme', 'native-BsaI'); await click('Recommend cuts');
    expect(host.textContent).toContain('4 internal sites');
    expect(host.textContent).toContain('target contains internal recognition sites');
    expect(host.textContent).not.toContain('at least 2');
});
it('fixed-only circular cuts imply exactly as many fragments, with no off-by-one conversion', async () => {
    await render(); await click('Golden Gate');
    await input('Fixed boundaries', '0, 16');
    await click('Recommend cuts');
    expect(calls.filter(c => c.url.endsWith('/suggest')).at(-1)?.body).toMatchObject({ fixed_boundaries: [0, 16], fragment_count: 2 });
});
it('opens target synthesis rather than the prepared-fragment basket and serializes the whole opened DNA only on Run', async () => {
    await render(); await click('Golden Gate');
    expect(host.textContent).toContain('Build target');
    expect(host.textContent).not.toContain('No fragments in the basket yet.');
    expect(calls.filter(c => c.url.endsWith('/suggest'))).toHaveLength(0);
    await input('Type IIS enzyme', 'native-new-enzyme');
    await input('Fragment count', '3');
    await click('Recommend cuts');
    expect(calls.filter(c => c.url.endsWith('/golden-gate/suggest')).map(c => c.body)).toEqual([expect.objectContaining({
        target: { id: 'target', source: { kind: 'inline', sequence, topology: 'circular', features: [] } },
        enzymes: ['native-new-enzyme'], preparation_kind: 'synthesis', fragment_count: 3, fixed_boundaries: [],
        target_fragment_length: null, avoid_repeats: true,
    })]);
});

it('previews recommended cuts on unchanged target DNA, permits exact editing, and hands off to the existing run/save/export owner', async () => {
    // Native captured split result, not a mock numerical planner. This test qualifies transport and receiving only.
    const split = fixture.split_target;
    const target = { ...seq, sequence: split.request.target.source.sequence, circular: false };
    suggestion = { proposals: [{ rank: 1, enzyme_id: split.request.enzyme.enzyme_id, enzyme_name: 'BsaI',
        empirical: 'no empirical data', internal_sites: 0, junction_mode: 'scarless', junctions: ['AAAA', 'CCCC'],
        cuts: [20, 80], fragment_lengths: [20, 60, target.sequence.length - 80],
        split_request: split.request, pcr_products: [], candidate: split.result.solutions[0] }], enzymes: [], diagnostics: [] };
    await render(null, target); await click('Golden Gate');
    await input('Terminal right fusion', 'TGAC'); await click('Recommend cuts');
    expect(host.querySelector('[aria-label="Target cut preview"]')?.textContent).toContain('20');
    expect(host.querySelector('[data-target-bases]')?.textContent).toBe(target.sequence);
    expect(host.querySelector('[aria-label="Prepared fragment sequences"]')?.textContent).toContain(split.result.solutions[0].design.materials.find(m => m.stage === 'prepared')!.sequence);
    await click('Edit these cuts');
    const boundaries = host.querySelector('input[aria-label="Fixed boundaries"]') as HTMLInputElement;
    expect(boundaries.value).toBe('20, 80');
    expect(host.textContent).not.toContain('Use this plan');
    await input('Fixed boundaries', '21, 80'); await click('Recommend cuts');
    expect(calls.filter(c => c.url.endsWith('/suggest')).at(-1)?.body.fixed_boundaries).toEqual([21, 80]);
    // Restore capture-matching cuts before using this capture's workflow request.
    await input('Fixed boundaries', '20, 80'); await click('Recommend cuts'); await click('Use this plan');
    for (let i = 0; i < 30 && !host.querySelector('[aria-label="Golden Gate task"]'); i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
    expect(calls.filter(c => c.url.endsWith('/design'))).toHaveLength(0);
    expect((host.querySelector('[aria-label="Golden Gate task"]') as HTMLSelectElement).value).toBe('split_target');
    await click('Run Golden Gate task');
    const design = calls.find(c => c.url.endsWith('/design'))!;
    expect(expandGoldenGateWire(design.body, 'request')).toEqual(split.request);
    expect(host.querySelector('[aria-label="Selected product DNA"]')).not.toBeNull();
    const create = URL.createObjectURL, revoke = URL.revokeObjectURL;
    URL.createObjectURL = () => 'blob:transport-test'; URL.revokeObjectURL = () => {};
    const anchorClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = () => {};
    try { await click('Export frozen workup ZIP'); } finally { URL.createObjectURL = create; URL.revokeObjectURL = revoke; HTMLAnchorElement.prototype.click = anchorClick; }
    const exported: any = expandGoldenGateWire(calls.find(c => c.url.endsWith('/design/export'))!.body, 'result');
    expect(exported.solutions[0].design.materials.filter((m: any) => m.stage === 'prepared')).toEqual(split.result.solutions[0].design.materials.filter(m => m.stage === 'prepared'));
    await click('Save fixed selected candidate');
    const saved: any = expandGoldenGateWire(calls.find(c => c.url.endsWith('/design/save'))!.body, 'save');
    expect(saved.selection.request).toEqual(split.result.solutions[0].fixed_request);
    expect(saved.selection.authored_request).toEqual(split.request);
});

it('places an exact manual boundary from a real SeqViz drag without running science', async () => {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 700, width: 1000, height: 700, toJSON() {} });
    await render(); await click('Golden Gate');
    for (const [type, x] of [['mousedown', 100], ['mousemove', 200], ['mouseup', 200]] as const) {
        const surface = host.querySelector('[data-testid="la-vz-seqblock"]');
        expect(surface).not.toBeNull();
        await act(async () => { surface!.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: 100, button: 0, buttons: type === 'mouseup' ? 0 : 1 })); await new Promise(r => setTimeout(r, 30)); });
    }
    const add = [...host.querySelectorAll('button')].find(b => b.textContent?.startsWith('Add cut at selection end'))!;
    expect(add.disabled).toBe(false);
    const end = Number(add.textContent!.match(/after bp (\d+)/)![1]);
    expect(end).toBeGreaterThan(0);
    await act(async () => add.click());
    const cut = seq.circular && end === sequence.length ? 0 : end;
    expect(host.querySelector<HTMLInputElement>('[aria-label="Fixed boundaries"]')!.value).toBe(String(cut));
    expect(calls.filter(c => c.url.endsWith('/suggest'))).toHaveLength(0);
    await click('Recommend cuts');
    expect(calls.filter(c => c.url.endsWith('/suggest')).at(-1)!.body.fixed_boundaries).toEqual([cut]);
});

it('preserves annotation coordinates when a wrapped selection becomes a linear target', async () => {
    const data: SequenceData = { ...seq, features: [{ id: 'wrap-feature', name: 'feature across origin', type: 'misc_feature', start: 28, end: 3, strand: 1, segments: [{ start: 28, end: 3 }] }] };
    await render({ start: 26, end: 5 }, data); await click('Golden Gate'); await input('Target molecule', 'selection');
    await input('Type IIS enzyme', 'native-BsaI'); await input('Terminal right fusion', 'TGAC'); await click('Recommend cuts');
    const source = calls.filter(c => c.url.endsWith('/suggest')).at(-1)!.body.target.source;
    expect(source.features).toEqual([expect.objectContaining({ id: 'wrap-feature', name: 'feature across origin', segments: [{ start: 28 - 26, end: sequence.length - 26 + 3, wraps_origin: false }] })]);
    expect(data.features[0].segments).toEqual([{ start: 28, end: 3 }]);
});

it('keeps prepared-fragment end controls shrinkable and gives notation a full row inside a narrow pane', async () => {
    await render(); await click('Golden Gate'); await click('Manual / prepared fragments'); await click('Add whole construct');
    const notation = host.querySelector<HTMLSelectElement>('[aria-label="Left end notation"]')!;
    expect(notation).not.toBeNull();
    expect(notation.selectedOptions[0].textContent).toBe('Historical / unspecified');
    expect(notation.classList.contains('col-span-full')).toBe(true);
    expect(notation.classList.contains('w-full')).toBe(true);
    const editorGrid = notation.parentElement!;
    expect(editorGrid.className).toContain('grid-cols-[minmax(0,1fr)_minmax(0,1fr)]');
    expect(editorGrid.querySelector('input')!.classList.contains('min-w-0')).toBe(true);
    expect(editorGrid.parentElement!.parentElement!.className).toContain('grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))]');
});


it('gives the real raw-base cut editor an explicit visible height', async () => {
    await render(); await click('Golden Gate');
    const box = host.querySelector<HTMLElement>('[aria-label="Target sequence cut editor"]');
    expect(box).not.toBeNull();
    expect(Number.parseInt(box!.style.height)).toBeGreaterThanOrEqual(260);
    expect(box!.querySelector('.sequence-viewer')).not.toBeNull();
});

it('shows native repeat compromises beside the exact-cut proposal', async () => {
    const split = fixture.split_target;
    const message = 'Repeat compromise: 3 repeat groups are split across fragments under the selected boundaries.';
    suggestion = { proposals: [{ rank: 1, enzyme_id: split.request.enzyme.enzyme_id, enzyme_name: 'BsaI',
        empirical: 'no empirical data', internal_sites: 0, junctions: ['AAAA', 'CCCC'], cuts: [20, 80],
        fragment_lengths: [20, 60, 40], split_request: split.request, pcr_products: [], notes: [message],
        candidate: split.result.solutions[0] }], enzymes: [], diagnostics: [] };
    await render(); await click('Golden Gate'); await input('Type IIS enzyme', 'native-BsaI'); await click('Recommend cuts');
    expect(host.textContent).toContain(message);
});
