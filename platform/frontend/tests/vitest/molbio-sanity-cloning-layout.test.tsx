import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { FragmentChooser } from '../../src/components/MolBioToolkit/FragmentChooser';
import { enzymePairLabel, junctionLines, proposalPairLabel } from '../../src/components/MolBioToolkit/panels/SuggestPlan';
import { circularCaptionName } from '../../src/components/MolBioToolkit/SequenceViewer';

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(), fetchChooser: vi.fn(),
    suggestRestrictionCloning: vi.fn(), fetchMolecularRevision: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/panels/golden-gate/GoldenGateWorkflowWorkspace', () => ({ default: () => <div /> }));

const seq = { name: 'open plasmid', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: true, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
let root: Root, host: HTMLDivElement, client: QueryClient;
const button = (label: string) => [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
async function click(label: string) { const b = button(label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }
beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.clearAllMocks();
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [] } } as never);
    vi.mocked(api.fetchChooser).mockResolvedValue({ items: [{ document_id: 'd', revision_id: 'r', revision_number: 1, name: 'A very long saved molecule name for clipping', length: 1800, topology: 'circular', produced_by: null }], next_cursor: null } as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });
const render = async () => act(async () => root.render(<QueryClientProvider client={client}>
    <AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId="seq-open" onLoadProduct={() => {}} onLoadSavedWorkup={() => {}} />
</QueryClientProvider>));

describe('Molecular Tools layout fixes (class/text checks only; pixels are covered by screenshots)', () => {
    it('chooser rows truncate inside the panel and carry title tooltips; Close cannot shrink', async () => {
        await act(async () => root.render(<FragmentChooser />));
        await click('Add from saved molecules');
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        const section = host.querySelector('section[aria-label="Fragment chooser"]')!;
        expect(section.className).toContain('min-w-0');
        expect(section.className).toContain('max-w-full');
        expect(button('Close')!.className).toContain('shrink-0');
        const spans = [...section.querySelectorAll('li button > span')] as HTMLElement[];
        expect(spans).toHaveLength(2);
        for (const s of spans) { expect(s.className).toContain('break-words'); expect(s.className).toContain('min-w-0'); expect(s.className).not.toContain('truncate'); expect(s.title).not.toBe(''); }
        expect(spans[0].parentElement!.className).toContain('flex-col');
        expect(spans[0].title).toContain('A very long saved molecule name');
        expect(spans[1].title).toContain('1,800 bp');
    });

    it('single-enzyme pairs are named once; mixed pairs keep the plus form', () => {
        expect(enzymePairLabel(['EcoRI', 'EcoRI'])).toBe('EcoRI (single enzyme)');
        expect(enzymePairLabel(['EcoRI', 'BamHI'])).toBe('EcoRI + BamHI');
    });

    it('restriction proposal headings include orientation and single-enzyme wording', async () => {
        const proposal = (o: 'forward' | 'reverse') => ({
            kind: 'direct', vector_enzymes: ['EcoRI', 'EcoRI'], insert_enzymes: ['EcoRI', 'EcoRI'], directional: false, insert_orientation: o,
            vector_self_ligation: 'possible', vector_length: 10, insert_payload_length: 5, product_length: 15, product_sequence: 'ACGT',
            vector_source_segments: [[0, 10]], insert_source_segments: [[0, 5]], assembly_request: { fragments: [], circular: true },
            selection: { primers: [], primer_notes: [] }, junction_sites: [],
        });
        vi.mocked(api.suggestRestrictionCloning).mockResolvedValue({ data: { proposals: [proposal('forward'), proposal('reverse')], no_suitable_pair: false, alternatives: [], notes: [] } } as never);
        vi.mocked(api.fetchMolecularRevision).mockResolvedValue({ document_id: 'd', revision_id: 'r', revision_number: 1, document_name: 'x', snapshot: { sequence: 'GGGG', is_circular: false } } as never);
        await render(); await click('Suggest plan'); await click('Restriction-ligation');
        await click('Add saved source');
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        await act(async () => (host.querySelector('section[aria-label="Fragment chooser"] li button') as HTMLButtonElement).click());
        await click('Suggest');
        const heads = [...host.querySelectorAll('li div.font-medium')].map((d) => d.textContent);
        expect(heads).toEqual([
            '#1 EcoRI (single enzyme) · insert forward',
            '#2 EcoRI (single enzyme) · insert reverse',
        ]);
        const card = host.querySelector('li div.font-medium')!.parentElement!;
        expect(card.textContent).not.toContain('Prepare creates reusable');
        expect(card.textContent).not.toContain('insert orientation');
        expect([...card.querySelectorAll('details')].map((d) => d.querySelector('summary')!.textContent)).toContain('Segments');
        expect(card.querySelector('details')!.textContent).toContain('Retained backbone');
        expect(card.querySelector('button[title^="Creates reusable"]')).not.toBeNull();
    });

    it('Golden Gate sub-mode switches are separate styled buttons in a wrapping row', async () => {
        await render(); await click('Golden Gate');
        const a = button('Manual / prepared fragments')!, b = button('Raw design / evaluate / optimize / split')!;
        expect(a.type).toBe('button'); expect(b.type).toBe('button');
        expect(a.parentElement).toBe(b.parentElement);
        expect(a.parentElement!.className).toContain('flex-wrap');
        expect(a.parentElement!.className).toContain('gap-2');
        expect(a.className).toContain('rounded-full'); expect(a.className).toContain('bg-violet-600');
        expect(b.className).toContain('bg-slate-800');
    });

    it('circular map caption keeps the whole name, wraps at separators and survives SeqViz 30-character slicing', () => {
        const slices = (s: string) => (s.match(/[\s\S]{1,30}/g) ?? []).map((x) => x.trim());
        for (const name of ['CGV-vector-EcoRI-BamHI (1.8 kb)', 'CGV-donor-EcoRI-payload140-EcoRI', 'UI-PASS Gibson EcoRI-vector (2,206 bp)', 'A'.repeat(70)]) {
            for (const max of [30, 21, 12]) {
                const lines = slices(circularCaptionName(name, max));
                expect(lines.join('').replace(/ /g, '')).toBe(name.replace(/ /g, ''));
                for (const line of lines) { expect(line.length).toBeLessThanOrEqual(max); expect(line.startsWith(')')).toBe(false); }
            }
        }
        expect(slices(circularCaptionName('CGV-vector-EcoRI-BamHI (1.8 kb)', 30))).toEqual(['CGV-vector-EcoRI-', 'BamHI (1.8 kb)']);
        expect(circularCaptionName('pUC19')).toBe('pUC19');
    });

    it('Acquire dialog names wrap instead of truncating, and the lone-chip / plural / GC text is correct', async () => {
        const src = (await import('node:fs')).readFileSync((await import('node:path')).resolve(process.cwd(), 'src/components/MolBioToolkit/MolecularInputModal.tsx'), 'utf8');
        expect(src).not.toMatch(/className="truncate text-base font-(semibold|medium) text-slate-100"/);
        expect(src).toContain("featureCount === 1 ? 'feature' : 'features'");
        expect(src).toContain("'GC n/a'");
        expect(src).toContain('.dna files are supported');
        expect(src).not.toContain('`.dna`');
    });

    it('proposal helpers: shared pair is named once, identical junctions collapse', () => {
        expect(proposalPairLabel({ vector_enzymes: ['EcoRI', 'BamHI'], insert_enzymes: ['EcoRI', 'BamHI'] })).toBe('EcoRI + BamHI');
        expect(proposalPairLabel({ vector_enzymes: ['BamHI'], insert_enzymes: ['BglII'] })).toBe('Vector: BamHI · Insert: BglII');
        const site = (between: string, d: string[] = []) => ({ between, regenerated: ['EcoRI'], destroyed: d, note: '' });
        expect(junctionLines([site('vector → insert'), site('insert → vector')])).toEqual(['Both junctions: regenerated EcoRI · destroyed none']);
        expect(junctionLines([site('vector → insert'), site('insert → vector', ['BamHI'])])).toHaveLength(2);
    });
});
