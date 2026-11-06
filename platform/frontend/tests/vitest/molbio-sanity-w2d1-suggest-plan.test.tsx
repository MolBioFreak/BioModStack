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
    fetchMolecularRevision: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateWorkflowWorkspace', () => ({
    default: ({ suggested }: { suggested?: { request: { task: string; enzyme: { enzyme_id: string } } } | null }) =>
        <div data-testid="gg-workspace">{suggested ? `loaded ${suggested.request.task} ${suggested.request.enzyme.enzyme_id}` : 'empty'}</div>,
}));
vi.mock('../../src/components/MolBioToolkit/FragmentChooser', () => ({
    FragmentChooser: ({ onPick, label }: { onPick: (item: unknown) => void; label?: string }) =>
        <button type="button" onClick={() => onPick({ document_id: 'doc-src', revision_id: 'rev-src', revision_number: 3, name: 'chosen source', length: 8 })}>{label}</button>,
    fragmentFromChooserItem: () => ({ id: 'x', name: 'x', sequence: '' }),
}));

const seq = { name: 'open plasmid', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: true, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
const noop = () => {};
const design = {
    circular: true, overlap: 28, target_tm: 62, min_anneal: 15,
    fragments: [
        { id: 'f1', name: 'backbone', sequence: 'ACGTACGTAC', preparation: 'pcr', orientation: 'forward' },
        { id: 'f2', name: 'cassette', sequence: 'TTGGCCAATT', preparation: 'ready_linear', orientation: 'forward' },
    ],
};
const gibsonProposal = (rank: number) => ({
    rank, summary: `Plan ${rank}: PCR backbone + synthesized cassette`, counts: { pcr: 1, synthesis: 1, existing: 0 },
    fragments: [
        { id: 'f1', name: 'backbone', preparation: 'pcr', orientation: 'forward', source: { id: 'src1', start: 10, end: 30, strand: 1, wraps_origin: true }, sequence: null },
        { id: 'f2', name: 'cassette', preparation: 'synthesis', orientation: 'forward', source: null, sequence: 'TTGGCCAATT'.repeat(5) },
    ],
    design_request: design, features: { kept: ['ori'], removed: ['KanR'], clipped: ['lacZ'] }, notes: [],
});

let root: Root, host: HTMLDivElement, client: QueryClient;
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}>
        <AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId="seq-open" onLoadProduct={noop} onLoadSavedWorkup={noop} />
    </QueryClientProvider>));
}
const button = (label: string) => [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
async function click(label: string) { const b = button(label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }

beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.clearAllMocks();
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [] } } as never);
    vi.mocked(api.fetchMolecularRevision).mockResolvedValue({
        document_id: 'doc-src', revision_id: 'rev-src', revision_number: 3, document_name: 'chosen source',
        snapshot: { sequence: 'GGGGCCCCAAAATTTT', is_circular: false },
    } as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

describe('Suggest plan', () => {
    it('is collapsed and sends no request until opened and run', async () => {
        await render();
        expect(host.querySelector('[aria-label="Suggest plan"]')).toBeNull();
        await click('Suggest plan');
        expect(host.querySelector('[aria-label="Suggest plan"]')).not.toBeNull();
        expect([api.suggestGibsonPlan, api.suggestGoldenGatePlan, api.suggestRestrictionCloning].map((f) => vi.mocked(f).mock.calls.length)).toEqual([0, 0, 0]);
    });

    it('Gibson: posts only the selected sources and shows three proposals with their numbers', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [1, 2, 3].map(gibsonProposal), diagnostics: [] } } as never);
        await render(); await click('Suggest plan');
        await click('Add saved source');
        await click('Suggest');
        const body = vi.mocked(api.suggestGibsonPlan).mock.calls[0][0];
        expect(body.sources).toEqual([expect.objectContaining({ name: 'chosen source', sequence: 'GGGGCCCCAAAATTTT', source_sequence_id: 'doc-src', source_revision: 3 })]);
        expect(body).toMatchObject({ overlap: 30, target_tm: 60, min_anneal: 13, min_match: 30, limit: 3, target: { name: 'open plasmid', circular: true } });
        expect([...host.querySelectorAll('button')].filter((b) => b.textContent === 'Use this plan').length).toBe(3);
        expect(host.textContent).toContain('1 PCR · 1 synthesis (50 bp) · 0 existing');
        expect(host.textContent).toContain('removed: KanR');
        expect(host.textContent).toContain('clipped: lacZ');
    });

    it('Gibson: Use this plan restores the Gibson design workspace from design_request', async () => {
        vi.mocked(api.suggestGibsonPlan).mockResolvedValue({ data: { proposals: [gibsonProposal(1)], diagnostics: [] } } as never);
        await render(); await click('Suggest plan'); await click('Suggest'); await click('Use this plan');
        const overlap = host.querySelector('input[value="28"]');
        expect(overlap).not.toBeNull();
        expect(host.textContent).toContain('backbone');
        expect(host.textContent).toContain('cassette');
    });

    it('Golden Gate: posts the enzyme set and boundaries, loads split_request into the workflow', async () => {
        vi.mocked(api.suggestGoldenGatePlan).mockResolvedValue({ data: {
            proposals: [1, 2, 3].map((rank) => ({
                rank, enzyme_id: 'BsaI', enzyme_name: 'BsaI', empirical: rank === 3 ? 'no empirical data' : 'pryor2020-s001', ranking_mode: 'empirical', internal_sites: 0,
                junction_mode: 'scarless', junctions: ['AATG', 'GGAG'], cuts: [10, 20], fragment_lengths: [10, 30], f_set: 0.91,
                split_request: { task: 'split_target', enzyme: { enzyme_id: 'BsaI' } }, pcr_products: [{ part_id: 'p1', tailed_length: 86, payload_length: 60 }],
                candidate: { fidelity: { per_junction: [{ representative: 'AATG', probability: 0.97 }] } }, donor_retained_fragment_index: null,
            })),
            enzymes: [], diagnostics: [],
        } } as never);
        await render(); await click('Suggest plan'); await click('Golden Gate');
        await click('Suggest');
        const body = vi.mocked(api.suggestGoldenGatePlan).mock.calls[0][0];
        expect(body).toMatchObject({ enzymes: ['BsaI', 'BsmBI', 'Esp3I', 'BbsI', 'SapI', 'PaqCI', 'BtgZI'], fixed_boundaries: [], preparation_kind: 'synthesis', proposals: 3, target: { id: 'target', source: { kind: 'inline', topology: 'circular' } } });
        expect(host.textContent).toContain('(97.0%)');
        expect(host.textContent).toContain('86 bp with tails (60 bp payload)');
        expect(host.textContent).toContain('No empirical data for this enzyme');
        await click('Use this plan');
        expect(host.querySelector('[data-testid="gg-workspace"]')?.textContent).toBe('loaded split_target BsaI');
    });

    it('Restriction: posts backbone and the selected insert, shows pair, orientation, background and tails', async () => {
        vi.mocked(api.suggestRestrictionCloning).mockResolvedValue({ data: {
            proposals: [{
                kind: 'primer_tail', vector_enzymes: ['EcoRI', 'BamHI'], insert_enzymes: ['EcoRI', 'BamHI'], directional: true, insert_orientation: 'forward',
                vector_ends_match_each_other: false, vector_self_ligation: 'not possible', vector_end_seal: 'both', vector_length: 3000, insert_payload_length: 500, product_length: 3500,
                junction_sites: [{ between: 'vector → insert', regenerated: ['EcoRI'], destroyed: [], note: '' }],
                primer_tails: { forward_primer_5prime_tail: 'GCGCGCGAATTC', reverse_primer_5prime_tail: 'GCGCGCGGATCC', note: 'tails' },
            }], no_suitable_pair: false, alternatives: [], notes: [],
        } } as never);
        await render(); await click('Suggest plan'); await click('Restriction-ligation');
        await click('Add saved source'); await click('Suggest');
        expect(vi.mocked(api.suggestRestrictionCloning).mock.calls[0][0]).toEqual({
            backbone_sequence: seq.sequence, backbone_topology: 'circular', insert_sequence: 'GGGGCCCCAAAATTTT', limit: 3,
        });
        const text = host.textContent ?? '';
        for (const part of ['EcoRI + BamHI', 'insert orientation: forward', 'vector self-ligation: not possible', 'regenerated EcoRI', 'GCGCGCGAATTC', 'GCGCGCGGATCC']) expect(text).toContain(part);
    });

    it('shows an API error as text', async () => {
        vi.mocked(api.suggestRestrictionCloning).mockRejectedValue(new Error('boom'));
        await render(); await click('Suggest plan'); await click('Restriction-ligation');
        await click('Add saved source'); await click('Suggest');
        expect(host.querySelector('[role="alert"]')?.textContent).toBe('boom');
    });
});
