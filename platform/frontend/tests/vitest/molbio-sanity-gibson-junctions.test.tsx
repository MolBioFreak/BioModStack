import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { GibsonDesignWorkspace } from '../../src/components/MolBioToolkit/panels/GibsonDesignWorkspace';

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(), fetchMolecularRevision: vi.fn(),
    designGibsonAssembly: vi.fn(), saveDesignedGibsonAssembly: vi.fn(), suggestGibsonPlan: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/FragmentChooser', () => ({
    FragmentChooser: ({ label }: { label?: string }) => <button type="button">{label}</button>,
    fragmentFromChooserItem: () => ({ id: 'x', name: 'x', sequence: '' }),
}));

const dna = (n: number, unit = 'ACGT') => unit.repeat(Math.ceil(n / unit.length)).slice(0, n);
const frag = (id: string, name: string, n: number) => ({ id, name, sequence: dna(n), orientation: 'forward' as const });
// A (60 nt, PCR) · L (20 nt linker, PCR) · B (60 nt, ready linear): L is carried, so the joins are A→B and B→A.
const fragments = [frag('a', 'Alpha', 60), frag('l', 'Linker', 20), frag('b', 'Beta', 60)];
const preparations = { a: 'pcr', l: 'pcr', b: 'ready_linear' } as const;
const designResponse = (junctions: unknown[] = []) => ({
    engine: 'pydna', engine_version: '1', circular: true, overlap: 30, target_tm: 60, min_anneal: 13,
    candidates: [{}], selected_candidate_checksum: 'c'.repeat(64), computation_id: 'comp',
    selected_product: { sequence: 'ACGT', length: 140, circular: true, mode: 'gibson', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions },
    designed_fragments: [], primers: [], warnings: [], source_provenance: [], message: 'ok',
});
const joined = (extra: Record<string, unknown> = {}) => ({
    left_fragment_id: 'a', right_fragment_id: 'b', left_fragment_name: 'Alpha', right_fragment_name: 'Beta', mode: 'gibson',
    overlap_length: 22, overlap_sequence: 'ACGTACGTACGTACGTACGTAC', junction_sequence: '', validation: 'ok', notes: [], overlap_tm: 61.4, ...extra,
});

let root: Root, host: HTMLDivElement, client: QueryClient;
const noop = () => {};
const button = (label: string) => [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
async function click(label: string) { const b = button(label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }
async function type(el: HTMLInputElement, value: string) {
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); });
}
async function choose(el: HTMLSelectElement, value: string) {
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('change', { bubbles: true })); });
}
const row = (afterId: string) => host.querySelector<HTMLElement>(`[data-junction-row="${afterId}"]`)!;
const rowOverlap = (afterId: string) => row(afterId).querySelector<HTMLInputElement>('input[type="number"]')!;
const rowSelect = (afterId: string) => row(afterId).querySelector<HTMLSelectElement>('select')!;
const optionState = (afterId: string, value: string) => { const o = [...rowSelect(afterId).options].find((x) => x.value === value)!; return { disabled: o.disabled, text: o.textContent }; };

async function renderWorkspace(props: Partial<React.ComponentProps<typeof GibsonDesignWorkspace>> = {}) {
    await act(async () => root.render(<QueryClientProvider client={client}>
        <GibsonDesignWorkspace fragments={fragments} preparations={preparations} saveName="" saveDescription="" sequenceName="plasmid"
            initialCircular onLoadProduct={noop} {...props} />
    </QueryClientProvider>));
}

beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.clearAllMocks();
    vi.mocked(api.designGibsonAssembly).mockResolvedValue({ data: designResponse() } as never);
    vi.mocked(api.saveDesignedGibsonAssembly).mockResolvedValue({ data: designResponse() } as never);
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [], raw_design: { raw_enzyme_adapters: [] } } } as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('Gibson junction editor', () => {
    it('lists one row per join of fragments longer than 40 nt, including the circular closure, and explains bottom-up building', async () => {
        await renderWorkspace();
        const rows = [...host.querySelectorAll('[data-junction-row]')];
        expect(rows.map((r) => r.getAttribute('data-junction-row'))).toEqual(['a', 'b']);
        expect(rows[0].textContent).toContain('Alpha → Beta');
        expect(rows[1].textContent).toContain('Beta → Alpha');
        expect((rowOverlap('a')).placeholder).toBe('30');
        expect(host.textContent).toContain("Build bottom-up: order your parts, then set each junction's overlap; a part of 40 nt or less is carried as a linker on its neighbours' primers.");
    });

    it('the circular closure row disappears for a linear product', async () => {
        await renderWorkspace({ initialCircular: false });
        expect([...host.querySelectorAll('[data-junction-row]')].map((r) => r.getAttribute('data-junction-row'))).toEqual(['a']);
    });

    it('an edited overlap and distribution produce exactly that junctions entry and nothing else changes', async () => {
        await renderWorkspace();
        await type(rowOverlap('a'), '22');
        await choose(rowSelect('a'), 'left');
        await click('Design & Simulate');
        const body = vi.mocked(api.designGibsonAssembly).mock.calls[0][0];
        expect(body.junctions).toEqual([{ after_fragment_id: 'a', overlap: 22, distribution: 'left' }]);
        const { junctions: _junctions, ...rest } = body;
        expect(rest).toEqual({
            fragments: fragments.map((f) => ({ ...f, preparation: preparations[f.id as 'a' | 'l' | 'b'] })),
            circular: true, overlap: 30, target_tm: 60, min_anneal: 13,
        });
        await click('Save as new construct');
        expect(vi.mocked(api.saveDesignedGibsonAssembly).mock.calls[0][0].junctions).toEqual([{ after_fragment_id: 'a', overlap: 22, distribution: 'left' }]);
    });

    it('a distribution-only edit sends overlap null; setting it back to Auto and blank removes the entry', async () => {
        await renderWorkspace();
        await choose(rowSelect('b'), 'right');
        await click('Design & Simulate');
        expect(vi.mocked(api.designGibsonAssembly).mock.calls[0][0].junctions).toEqual([{ after_fragment_id: 'b', overlap: null, distribution: 'right' }]);
        await choose(rowSelect('b'), '');
        await click('Design & Simulate');
        expect('junctions' in vi.mocked(api.designGibsonAssembly).mock.calls[1][0]).toBe(false);
    });

    it('with no junction edits the request has no junctions key', async () => {
        await renderWorkspace();
        await click('Design & Simulate');
        expect('junctions' in vi.mocked(api.designGibsonAssembly).mock.calls[0][0]).toBe(false);
    });

    it('disables distribution options the preparations cannot satisfy, with a reason', async () => {
        await renderWorkspace();
        // Alpha (PCR) → Beta (ready linear): only the left primer can carry the overlap.
        expect(optionState('a', '').disabled).toBe(false);
        expect(optionState('a', 'left').disabled).toBe(false);
        expect(optionState('a', 'split').disabled).toBe(true);
        expect(optionState('a', 'right').disabled).toBe(true);
        expect(optionState('a', 'right').text).toMatch(/Beta.*ready linear/i);
        // Beta (ready linear) → Alpha (PCR): only the right primer can.
        expect(optionState('b', 'left').disabled).toBe(true);
        expect(optionState('b', 'split').disabled).toBe(true);
        expect(optionState('b', 'right').disabled).toBe(false);
    });

    it('restores junctions from a saved design_request onto the rows', async () => {
        await renderWorkspace({ restored: { fragments: fragments.map((f) => ({ ...f, preparation: preparations[f.id as 'a' | 'l' | 'b'] })), circular: true, overlap: 30, junctions: [{ after_fragment_id: 'b', overlap: 44, distribution: 'right' }] } });
        expect(rowOverlap('b').value).toBe('44');
        expect(rowSelect('b').value).toBe('right');
        expect(rowOverlap('a').value).toBe('');
        expect(rowSelect('a').value).toBe('');
        await click('Design & Simulate');
        expect(vi.mocked(api.designGibsonAssembly).mock.calls[0][0].junctions).toEqual([{ after_fragment_id: 'b', overlap: 44, distribution: 'right' }]);
    });

    it('shows overlap length, Tm, GC %, distribution and notes for each designed junction', async () => {
        vi.mocked(api.designGibsonAssembly).mockResolvedValue({ data: designResponse([
            joined({ overlap_gc: 54.5, distribution: 'split', notes: ['NEB 2/4 Tm below 48 C'] }),
            joined({ left_fragment_id: 'b', right_fragment_id: 'a', left_fragment_name: 'Beta', right_fragment_name: 'Alpha', overlap_length: 30, overlap_tm: null, overlap_gc: 41, distribution: 'homology' }),
        ]) } as never);
        await renderWorkspace();
        await click('Design & Simulate');
        const text = host.textContent ?? '';
        expect(text).toContain('22 nt overlap');
        expect(text).toContain('overlap Tm 61.4 °C');
        expect(text).toContain('GC 54.5 %');
        expect(text).toContain('split between both primers');
        expect(text).toContain('NEB 2/4 Tm below 48 C');
        expect(text).toContain('GC 41.0 %');
        expect(text).toContain('shared homology, no tail needed');
    });
});

describe('Gibson suggest plan with junctions', () => {
    const seq = { name: 'open plasmid', sequence: dna(80), circular: true, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
    const proposalJunctions = [
        { left_id: 'f1', right_id: 'f2', overlap_length: 22, overlap_sequence: 'ACGTACGTACGTACGTACGTAC', overlap_tm: 61.4, overlap_tm_2_4: 55.2, overlap_gc: 54.5, distribution: 'left', notes: ['GC outside 40-65 %'] },
        { left_id: 'f2', right_id: 'f1', overlap_length: 31, overlap_sequence: 'ACGT'.repeat(8).slice(0, 31), overlap_tm: 66.0, overlap_tm_2_4: 60.1, overlap_gc: 48.3, distribution: 'split', notes: [] },
    ];
    const designRequest = {
        circular: true, overlap: 25, target_tm: 62, min_anneal: 15,
        fragments: [
            { id: 'f1', name: 'backbone', sequence: dna(60), preparation: 'pcr', orientation: 'forward' },
            { id: 'f2', name: 'cassette', sequence: dna(60, 'TTGGCCAA'), preparation: 'ready_linear', orientation: 'forward' },
        ],
        junctions: [{ after_fragment_id: 'f1', overlap: 22, distribution: 'left' }, { after_fragment_id: 'f2', overlap: 31, distribution: null }],
    };
    const proposal = {
        rank: 1, summary: 'Plan 1: two synthetic fragments', counts: { pcr: 0, synthesis: 2, existing: 0 },
        fragments: [
            { id: 'f1', name: 'backbone', preparation: 'synthesis', orientation: 'forward', source: null, sequence: dna(60) },
            { id: 'f2', name: 'cassette', preparation: 'synthesis', orientation: 'forward', source: null, sequence: dna(60, 'TTGGCCAA') },
        ],
        design_request: designRequest, junctions: proposalJunctions, features: { kept: [], removed: [], clipped: [] }, notes: [],
    };
    async function renderPanel() {
        await act(async () => root.render(<QueryClientProvider client={client}>
            <AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId="seq-open" onLoadProduct={noop} onLoadSavedWorkup={noop} />
        </QueryClientProvider>));
    }
    const input = (label: string) => host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;

    it('sends fragment_count and max_synthesis_length only when filled', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [proposal], diagnostics: [] } } as never);
        await renderPanel(); await click('Suggest plan'); await click('Suggest');
        const first = vi.mocked(api.suggestGibsonPlan).mock.calls[0][0];
        expect('fragment_count' in first).toBe(false);
        expect('max_synthesis_length' in first).toBe(false);
        expect('overlap' in first).toBe(false);
        await type(input('Fragment count'), '3');
        await type(input('Max synthesis length'), '1800');
        await click('Suggest');
        const second = vi.mocked(api.suggestGibsonPlan).mock.calls[1][0];
        expect(second).toMatchObject({ fragment_count: 3, max_synthesis_length: 1800 });
        expect('overlap' in second).toBe(false);
        await type(input('Overlap (nt)'), '24');
        await click('Suggest');
        expect(vi.mocked(api.suggestGibsonPlan).mock.calls[2][0]).toMatchObject({ overlap: 24 });
    });

    it('a target with no sources shows synthetic proposals normally, with each proposal junction listed', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [proposal], diagnostics: ['No source matched the target; split into synthetic fragments.'] } } as never);
        await renderPanel(); await click('Suggest plan'); await click('Suggest');
        expect(host.querySelector('[role="alert"]')).toBeNull();
        const card = host.querySelector('[data-plan-card="1"]')!;
        expect(card.textContent).toContain('0 PCR · 2 synthesis (120 bp) · 0 existing');
        expect(card.textContent).toContain('backbone → cassette');
        expect(card.textContent).toContain('22 nt');
        expect(card.textContent).toContain('Tm 61.4 °C');
        expect(card.textContent).toContain('GC 54.5 %');
        expect(card.textContent).toContain('GC outside 40-65 %');
        expect(card.textContent).toContain('cassette → backbone');
        expect(card.textContent).toContain('31 nt');
    });

    it('Use this plan hands the proposal design_request, including junctions, to the workspace unchanged', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [proposal], diagnostics: [] } } as never);
        await renderPanel(); await click('Suggest plan'); await click('Suggest'); await click('Use this plan');
        expect(rowOverlap('f1').value).toBe('22');
        expect(rowSelect('f1').value).toBe('left');
        expect(rowOverlap('f2').value).toBe('31');
        expect(rowSelect('f2').value).toBe('');
        await click('Design & Simulate');
        const body = vi.mocked(api.designGibsonAssembly).mock.calls[0][0];
        expect(body.junctions).toEqual(designRequest.junctions);
        expect(body).toMatchObject({ circular: true, overlap: 25, target_tm: 62, min_anneal: 15 });
    });
});
