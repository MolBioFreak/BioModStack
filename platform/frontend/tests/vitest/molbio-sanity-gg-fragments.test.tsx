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
let workflowResult: unknown;
let unexpected: string[];
let pendingSuggestion: Promise<unknown> | null;
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
    const el = [...host.querySelectorAll('input,select,textarea')].find(el => el.getAttribute('aria-label') === label || el.closest('label')?.textContent?.trim().startsWith(label)) as HTMLInputElement;
    expect(el, label).toBeDefined();
    if (el instanceof HTMLSelectElement) await vi.waitFor(async () => { await act(async () => { await new Promise(r => setTimeout(r, 5)); }); expect([...el.options].some(o => o.value === value)).toBe(true); });
    await act(async () => {
        const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    suggestion = { proposals: [], enzymes: [], diagnostics: [] };
    workflowResult = fixture.split_target.result; unexpected = []; pendingSuggestion = null;
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); calls = [];
    api.defaults.adapter = async config => {
        const url = config.url!;
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body });
        let data: unknown;
        if (url.endsWith('/golden-gate/options')) data = options;
        else if (url.endsWith('/gibson/workups')) data = [];
        else if (url.endsWith('/golden-gate/suggest')) {
            expect(typeof config.data).toBe('string');
            expect(config.method).toBe('post');
            data = pendingSuggestion ? await pendingSuggestion : suggestion;
        }
        else if (url.endsWith('/primer-tm/options')) data = fixture.tm_options;
        else if (url.endsWith('/assembly-workups')) data = [];
        else if (url.endsWith('/design')) data = workflowResult;
        else if (url.endsWith('/design/save')) data = fixture.saved;
        else if (url.endsWith('/design/export')) data = new Blob(['Transport-only response; archive bytes qualified by the API owner']);
        else { unexpected.push(url); throw new Error(`Unexpected HTTP ${url}`); }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => {
    if (process.env.BMS_GG_FRAG_UI_EVIDENCE) {
        mkdirSync(process.env.BMS_GG_FRAG_UI_EVIDENCE, { recursive: true });
        writeFileSync(join(process.env.BMS_GG_FRAG_UI_EVIDENCE, `${expect.getState().currentTestName!.replace(/[^a-z0-9]+/gi, '-').slice(0, 180)}.json`), JSON.stringify(calls, null, 2));
    }
    await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = original; vi.restoreAllMocks(); vi.unstubAllGlobals();
    expect(unexpected).toEqual([]);
});

const report = (o: any) => ({ index: 0, name: 'A', length: 40, topology: 'linear', enzyme_id: 'native-BsaI', enzyme_name: 'BsaI', requested_kind: 'auto', kind: 'bare', site_count: 0, site_positions: [], candidates: [], message: 'Bare DNA: no BsaI sites.', ...o });
const proposal = (o: any = {}) => ({
    rank: 1, enzyme_id: 'native-BsaI', enzyme_name: 'BsaI', empirical: 'x', ranking_mode: 'm', internal_sites: 0, junction_mode: 'chosen', junctions: ['AATG', 'GCTT'],
    cuts: [], fragment_lengths: [], f_set: 0.91, split_request: null, pcr_products: [], donor_retained_fragment_index: null,
    assemble_request: { schema_version: 'bms.golden-gate-design.v1', task: 'assemble_parts', marker: 'assemble' }, part_order: [1, 0],
    junction_slots: [{ index: 0, overhang: 'AATG', origin: 'optimizer', between: [] }, { index: 1, overhang: 'GCTT', origin: 'prepared', between: [] }],
    prepared_fragments: [
        { part_id: 'p1', name: 'A', preparation: 'synthesis', part_kind: 'bare', input_index: 0, order_sequence: 'GGTCTCAAATGCCCC', tailed_length: 15, payload_length: 4, primers: [], digested_sequence: null, left_overhang: 'AATG', right_overhang: 'GCTT' },
        { part_id: 'p2', name: 'B', preparation: 'synthesis', part_kind: 'prepared', input_index: 1, order_sequence: null, tailed_length: null, payload_length: 30, primers: [], digested_sequence: null, left_overhang: 'GCTT', right_overhang: 'AATG' },
    ],
    order_summary: { bare_parts: 1, prepared_parts: 1, order_lengths: [15], order_total_bp: 15, method: 'synthesis' },
    final_sequence: 'A'.repeat(34), notes: ['B was used as its reverse complement so that its overhangs chain.'], ...o });
const lastSuggest = () => calls.filter(c => c.url.endsWith('/golden-gate/suggest')).at(-1)!.body;
async function openFragments() {
    await render(); await click('Golden Gate'); await click('Assemble from fragments');
}
async function addPasted(name: string, seq: string) {
    await input('Pasted part name', name); await input('Pasted part sequence', seq); await click('Add pasted sequence as part');
}
const aria = (label: string) => host.querySelector(`[aria-label="${label}"]`) as HTMLElement;

it('keeps the split mode as the default and every existing control after switching back', async () => {
    await render(); await click('Golden Gate');
    expect(host.textContent).toContain('Split one sequence or plasmid');
    expect(host.textContent).toContain('Maximum homopolymer run at junction (nt)');
    await click('Assemble from fragments');
    expect(host.textContent).not.toContain('Maximum homopolymer run at junction (nt)');
    await click('Split one sequence or plasmid');
    expect(host.textContent).toContain('Homopolymer junction buffer (bp)');
    expect(host.textContent).toContain('Recommend cuts');
});

it('sends the exact parts request: order, kinds, overhangs, orientation, ticked enzymes, circular', async () => {
    await openFragments();
    await addPasted('A', 'ACGTACGTACGTACGTACGT'); await addPasted('B', 'TTGGCCAATTGGCCAATTGG');
    await input('Part 2 orientation', 'reverse'); await input('Part 2 kind', 'prepared'); await input('Part 1 left overhang', 'aatg');
    await input('Part order', 'auto');
    const boxes = [...aria('Type IIS enzymes for bare DNA').querySelectorAll('input')] as HTMLInputElement[];
    await act(async () => boxes[1].click());
    expect(boxes.every(b => b.checked)).toBe(true);
    await click('Build plan from fragments');
    const b = lastSuggest();
    expect(b.target).toBeUndefined();
    expect(b).toMatchObject({ enzymes: ['native-new-enzyme', 'native-BsaI'], order: 'auto', circular: true, preparation_kind: 'synthesis' });
    expect(b.parts).toHaveLength(2);
    expect(b.parts[0]).toMatchObject({ name: 'A', orientation: 'forward', kind: 'auto', left_overhang: 'AATG', source: { source: { kind: 'inline', sequence: 'ACGTACGTACGTACGTACGT', topology: 'linear' } } });
    expect(b.parts[1]).toMatchObject({ name: 'B', orientation: 'reverse', kind: 'prepared' });
    expect(b.parts[1].left_overhang).toBeUndefined();
});


async function clickAria(label: string) {
    const b = host.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement; expect(b, label).toBeTruthy(); await act(async () => b.click());
}
it('reorders and removes parts and sends them in the new order', async () => {
    await openFragments();
    await addPasted('A', 'ACGTACGTAC'); await addPasted('B', 'TTGGCCAATT'); await addPasted('C', 'GGGGCCCCAA');
    await clickAria('Move part 1 down');
    await clickAria('Remove part 3');
    await click('Build plan from fragments');
    expect(lastSuggest().parts.map((p: any) => p.name)).toEqual(['B', 'A']);
});

it('adds the open sequence as a part and sends vector and circular=false with a pasted vector', async () => {
    await openFragments();
    await click('Add open sequence as part');
    await input('Destination vector', 'pasted'); await input('Pasted vector sequence', 'ggtctcaaaaggagacc');
    await click('Build plan from fragments');
    const b = lastSuggest();
    expect(b.parts[0].source.source).toMatchObject({ kind: 'inline', sequence, topology: 'circular' });
    expect(b.vector.source).toMatchObject({ kind: 'inline', sequence: 'GGTCTCAAAAGGAGACC' });
    expect(b.circular).toBe(false);
});

it('shows reports, per-enzyme outcomes, plan card, order list and copies the order sequences', async () => {
    suggestion = {
        proposals: [proposal()],
        part_reports: [report({ index: 0, name: 'A' }), report({ index: 1, name: 'B', kind: 'prepared', left_overhang: 'GCTT', right_overhang: 'AATG', payload_length: 30, site_count: 2, site_positions: [0, 36], message: 'Pre-configured: carries two BsaI sites.' })],
        enzymes: [{ enzyme_id: 'native-BsaI', name: 'BsaI', empirical: 'x', status: 'proposal ready', internal_sites: 0 }, { enzyme_id: 'native-new-enzyme', name: 'NewTypeIIS', empirical: 'x', status: 'no proposal: B has a site', internal_sites: 3 }],
        diagnostics: [],
    };
    const writeText = vi.fn(); vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    await openFragments(); await addPasted('A', 'ACGTACGTAC'); await addPasted('B', 'TTGGCCAATT');
    await click('Build plan from fragments');
    const t = host.textContent!;
    expect(t).toContain('Bare DNA: no BsaI sites.');
    expect(t).toContain('Pre-configured · GCTT … AATG · 30 bp payload');
    expect(t).toContain('Pre-configured: carries two BsaI sites.');
    expect(t).toContain('NewTypeIIS: no proposal: B has a site · 3 internal sites');
    expect(t).toContain('Part order: B → A');
    expect(t).toContain('F_set 0.910');
    expect(t).toContain('1 to order by synthesis · total 15 bp · final product 34 bp');
    expect(t).toContain('used as supplied, nothing to order');
    expect(t).toContain('B was used as its reverse complement so that its overhangs chain.');
    const rows = [...aria('Junctions').querySelectorAll('tbody tr')].map(r => r.textContent);
    expect(rows).toEqual(['1AATGoptimizer', '2GCTTprepared']);
    await click('Copy sequence for A');
    expect(writeText).toHaveBeenLastCalledWith('GGTCTCAAATGCCCC');
    await click('Copy all as FASTA');
    expect(writeText).toHaveBeenLastCalledWith('>A 15 bp\nGGTCTCAAATGCCCC');
    await click('Copy all as CSV');
    expect(writeText).toHaveBeenLastCalledWith('name,length_bp,sequence\nA,15,GGTCTCAAATGCCCC');
});

it('lets the user pick one candidate for an ambiguous part and re-runs with retained_fragment_index', async () => {
    suggestion = { proposals: [], enzymes: [{ enzyme_id: 'native-new-enzyme', name: 'NewTypeIIS', empirical: 'x', status: 'no proposal: A: pick one' }],
        part_reports: [report({ kind: 'ambiguous', message: 'Two fragments.', candidates: [{ fragment_index: 0, length: 10, left_overhang: 'AATG', right_overhang: 'GCTT' }, { fragment_index: 1, length: 22, left_overhang: 'GCTT', right_overhang: 'TTAC' }] })], diagnostics: [] };
    await openFragments(); await addPasted('A', 'ACGTACGTAC');
    await click('Build plan from fragments');
    expect(host.textContent).toContain('Needs a choice');
    expect(host.textContent).toContain('no proposal: A: pick one');
    await click('#1 · 22 bp · GCTT … TTAC');
    await vi.waitFor(() => expect(calls.filter(c => c.url.endsWith('/golden-gate/suggest'))).toHaveLength(2));
    expect(lastSuggest().parts[0].retained_fragment_index).toBe(1);
});

it('Use this plan hands the assemble request to the existing Golden Gate workflow', async () => {
    suggestion = { proposals: [proposal({ assemble_request: fixture.assemble_parts.request })], part_reports: [], enzymes: [], diagnostics: [] };
    workflowResult = fixture.assemble_parts.result;
    await openFragments(); await addPasted('A', 'ACGTACGTAC'); await click('Build plan from fragments');
    await click('Use this plan');
    await vi.waitFor(() => expect(host.textContent).toContain('Run Golden Gate task'));
});

it('split mode shows the order list from prepared_fragments without changing the cut preview', async () => {
    suggestion = { proposals: [proposal({ cuts: [8], fragment_lengths: [8, 24], part_order: undefined, assemble_request: null, split_request: {} })], enzymes: [], diagnostics: [] };
    await render(); await click('Golden Gate'); await input('Type IIS enzyme', 'native-BsaI'); await click('Recommend cuts');
    expect(host.textContent).toContain('Cut after bp 8');
    expect(host.textContent).toContain('1 to order by synthesis · total 15 bp');
    expect(host.textContent).toContain('Copy sequence for A');
});
