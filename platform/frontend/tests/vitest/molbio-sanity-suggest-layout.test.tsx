import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(),
    suggestGibsonPlan: vi.fn(), suggestGoldenGatePlan: vi.fn(), suggestRestrictionCloning: vi.fn(),
    prepareRestrictionCloning: vi.fn(), fetchMolecularRevision: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateWorkflowWorkspace', () => ({ default: () => <div /> }));
vi.mock('../../src/components/MolBioToolkit/FragmentChooser', () => ({
    FragmentChooser: ({ onPick, label }: { onPick: (item: unknown) => void; label?: string }) =>
        <button type="button" onClick={() => onPick({ document_id: 'doc-src', revision_id: 'rev-src', revision_number: 3, name: 'chosen source', length: 8 })}>{label}</button>,
    fragmentFromChooserItem: () => ({ id: 'x', name: 'x', sequence: '' }),
}));

const seq = { name: 'open plasmid', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: true, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
const noop = () => {};
const design = { circular: true, overlap: 28, target_tm: 62, min_anneal: 15, fragments: [{ id: 'f1', name: 'backbone', sequence: 'ACGTACGTAC', preparation: 'pcr', orientation: 'forward' }] };
const gibsonProposal = (rank: number) => ({
    rank, summary: `Plan ${rank}`, counts: { pcr: 1, synthesis: 0, existing: 2 },
    fragments: [{ id: 'f1', name: 'backbone', preparation: 'pcr', orientation: 'forward', source: { id: 'src1', start: 1, end: 9, strand: 1, wraps_origin: false }, sequence: null }],
    design_request: { ...design, overlap: 20 + rank }, features: { kept: ['ori'], removed: ['KanR'], clipped: [] }, notes: ['note-a'],
});
const restrictionProposal = {
    kind: 'primer_tail', vector_enzymes: ['EcoRI', 'BamHI'], insert_enzymes: ['EcoRI', 'BamHI'], directional: true, insert_orientation: 'forward',
    vector_ends_match_each_other: false, vector_self_ligation: 'not possible', vector_end_seal: 'both', vector_length: 3000, insert_payload_length: 500, insert_contributed_length: 500, product_length: 3500,
    product_sequence: 'PRODUCTDNA', prepared_insert_sequence: 'PREPINSERT', vector_source_segments: [[0, 3000]], insert_source_segments: [[0, 500]],
    assembly_request: { fragments: [], circular: true },
    selection: { kind: 'primer_tail', primers: [{ strand: 1, sequence: 'FWDPRIMERSEQ' }, { strand: -1, sequence: 'REVPRIMERSEQ' }], primer_notes: [], parent_revisions: {} },
    junction_sites: [{ between: 'vector → insert', regenerated: ['EcoRI'], destroyed: [], note: '' }],
    primer_tails: { forward_primer_5prime_tail: 'GCGCGCGAATTC', reverse_primer_5prime_tail: 'GCGCGCGGATCC', note: 'tails' },
};

let root: Root, host: HTMLDivElement, client: QueryClient;
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}>
        <AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId="seq-open" onLoadProduct={noop} onLoadSavedWorkup={noop} />
    </QueryClientProvider>));
}
const button = (label: string) => [...host.querySelectorAll('button')].filter((b) => b.textContent?.trim() === label);
async function click(label: string, i = 0) { const b = button(label)[i]; expect(b, label).toBeDefined(); await act(async () => { b.click(); await new Promise((r) => setTimeout(r, 5)); }); }
const stages = () => [...host.querySelectorAll('[data-stage-heading]')].map((e) => e.getAttribute('data-stage-heading'));

beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.clearAllMocks();
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [], raw_design: { raw_enzyme_adapters: [] } } } as never);
    vi.mocked(api.fetchMolecularRevision).mockResolvedValue({ document_id: 'doc-src', revision_id: 'rev-src', revision_number: 3, document_name: 'chosen source', snapshot: { sequence: 'GGGGCCCCAAAATTTT', is_circular: false } } as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('Suggest plan stage layout', () => {
    it('Gibson: Setup, Plans and Workup headings; Use this plan hands over design_request unchanged', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [1, 2].map(gibsonProposal), diagnostics: [] } } as never);
        await render(); await click('Suggest plan');
        expect(stages()).toEqual(['Setup']);
        await click('Add saved source'); await click('Suggest');
        expect(stages()).toEqual(['Setup', 'Plans', 'Workup']);
        expect(host.querySelectorAll('[data-plan-card]').length).toBe(2);
        expect(host.querySelector('[data-stage="plans"]')?.textContent).toContain('1 PCR · 0 synthesis (0 bp) · 2 existing');
        expect(host.querySelector('[data-stage="workup"]')?.textContent).toContain('removed: KanR');
        expect(host.querySelector('[data-stage="workup"]')?.textContent).toContain('note-a');
        expect(button('Use this plan').length).toBe(2);
        await click('Use this plan', 1);
        expect(host.querySelector('input[value="22"]')).not.toBeNull(); // design_request of plan #2 (overlap 22)
    });

    it('Restriction: stage headings, junction lines, primer copy buttons copy the same text', async () => {
        const writeText = vi.fn(); vi.stubGlobal('navigator', { clipboard: { writeText } });
        vi.mocked(api.suggestRestrictionCloning).mockResolvedValue({ data: { proposals: [restrictionProposal], no_suitable_pair: false, alternatives: [], notes: [] } } as never);
        vi.mocked(api.prepareRestrictionCloning).mockResolvedValue({ data: { assembly_request: { fragments: [], circular: true } } } as never);
        await render(); await click('Suggest plan'); await click('Restriction-ligation');
        expect(stages()).toEqual(['Setup']);
        await click('Add saved source'); await click('Suggest');
        expect(stages()).toEqual(['Setup', 'Plans', 'Workup']);
        const plans = host.querySelector('[data-stage="plans"]')?.textContent ?? '';
        expect(plans).toContain('EcoRI + BamHI'); expect(plans).toContain('regenerated EcoRI');
        for (const [label, text] of [['Copy primer', 'FWDPRIMERSEQ'], ['Copy primer', 'REVPRIMERSEQ'], ['Copy prepared insert DNA', 'PREPINSERT'], ['Copy product DNA', 'PRODUCTDNA']] as const) {
            writeText.mockClear();
            await click(label, label === 'Copy primer' && text === 'REVPRIMERSEQ' ? 1 : 0);
            expect(writeText).toHaveBeenCalledWith(text);
        }
        await click('Prepare fragments and use this plan');
        const call = vi.mocked(api.prepareRestrictionCloning).mock.calls[0][0];
        expect(call.selection).toEqual(restrictionProposal.selection);
        expect(call.name).toBe('open plasmid restriction assembly');
    });

    it('keeps the stage shell shrinkable so a 390px viewport has nothing to overflow', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [gibsonProposal(1)], diagnostics: [] } } as never);
        await render(); await click('Suggest plan'); await click('Add saved source'); await click('Suggest');
        for (const el of host.querySelectorAll('[data-stage], [data-plan-card], [data-workup-block]')) expect(el.className, el.outerHTML.slice(0, 60)).toContain('min-w-0');
        expect(host.querySelector('[data-plan-card] .flex-wrap')).not.toBeNull();
        expect(host.querySelector('[data-stage="plans"] .overflow-x-auto, [data-stage="plans"] .overflow-x-scroll')).toBeNull();
    });

    it('Gateway is only a disabled note, never a working method', async () => {
        await render(); await click('Suggest plan');
        expect(button('Gateway').length).toBe(0);
        expect(button('Gateway: not available yet').length).toBe(0);
        const note = [...host.querySelectorAll('[aria-disabled="true"]')].find((e) => e.textContent === 'Gateway: not available yet');
        expect(note).toBeDefined();
        expect(note!.tagName).not.toBe('BUTTON');
    });
});
