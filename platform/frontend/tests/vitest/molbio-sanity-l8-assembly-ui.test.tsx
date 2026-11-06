import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { digestFragmentsForAssembly } from '../../src/lib/digestAssemblyTransfer';
import { inlineFragmentFeatures } from '../../src/components/MolBioToolkit/utils/assemblyFeatures';
import { parseRestrictionDigestSimulation, simulateRestrictionDigest, saveRestrictionDigest, type RestrictionDigestSimulation } from '../../src/lib/restrictionAnalysis';

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(),
    simulateLigationAssembly: vi.fn(), saveLigationAssembly: vi.fn(), simulateGibsonAssembly: vi.fn(), saveGibsonAssembly: vi.fn(),
    planDnaWeaverGibsonAssembly: vi.fn(), saveDnaWeaverGibsonAssembly: vi.fn(),
    designGibsonAssembly: vi.fn(), saveDesignedGibsonAssembly: vi.fn(), endOperations: vi.fn(),
}));
// l6 owns the real CSV/FASTA builders; the contract shapes are stubbed here.
vi.mock('../../src/components/MolBioToolkit/utils/exportData', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/components/MolBioToolkit/utils/exportData')>()),
    buildAssemblyPrimersCsv: (rows: Array<{ id: string; tm_model: string }>) => rows.map((r) => `${r.id},${r.tm_model}`).join('\n'),
    buildFragmentsFasta: (rows: Array<{ name: string; sequence: string }>) => rows.map((r) => `>${r.name}\n${r.sequence}`).join('\n'),
}));
// l7 owns the real chooser; the panel only needs its two exports.
vi.mock('../../src/components/MolBioToolkit/FragmentChooser', () => ({
    FragmentChooser: ({ onPick, label }: { onPick: (item: unknown) => void; label?: string }) =>
        <button type="button" onClick={() => onPick({ document_id: 'doc-9', revision_id: 'rev-9', revision_number: 2, name: 'saved child', length: 6 })}>{label}</button>,
    fragmentFromChooserItem: (item: { document_id: string; revision_id: string; revision_number: number; name: string; length: number }) => ({
        id: item.revision_id, name: item.name, sequence: 'AATTCG', source_sequence_id: item.document_id, source_revision: item.revision_number,
        source_start: 0, source_end: item.length, left_end: { type: 'sticky_5', overhang: 'AATT', protruding_strand: 'top' }, right_end: { type: 'blunt', overhang: '' },
        metadata: { revision_id: item.revision_id },
    }),
}));

const fixture = JSON.parse(readFileSync('../api/tests/fixtures/restriction_digest/circular_single_ecori.json', 'utf8'));
const groupId: string = fixture.cleavages[0].contributor_group_id;
const seq = { name: 'editable', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: false, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
const product = (junctions: unknown[] = []) => ({ sequence: 'ACGT', length: 4, circular: false, mode: 'ligation', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions });
const noop = () => {};
let root: Root, host: HTMLDivElement, client: QueryClient;
async function render(node: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); }
const button = (label: string) => [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
async function click(label: string) { const b = button(label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }

beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.clearAllMocks();
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { enzymes: [] } } as never);
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:x') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('digest per-site selection', () => {
    it('parser accepts the new optional response fields and still rejects unknown ones', () => {
        const withNew = { ...structuredClone(fixture), not_cuttable: [{ enzyme_id: 'EcoRI', site_start: 2, reason: 'overlaps an unpaired overhang' }], excluded_cleavage_group_ids: [] };
        expect(parseRestrictionDigestSimulation(withNew).not_cuttable).toHaveLength(1);
        expect(parseRestrictionDigestSimulation(fixture).not_cuttable).toBeUndefined();
        expect(() => parseRestrictionDigestSimulation({ ...structuredClone(fixture), surprise: 1 })).toThrow();
        expect(() => parseRestrictionDigestSimulation({ ...structuredClone(fixture), not_cuttable: [{ enzyme_id: 'EcoRI' }] })).toThrow();
        const phosphate = structuredClone(fixture); phosphate.fragments[0].left_end.five_prime_phosphate = 'phosphorylated';
        expect(parseRestrictionDigestSimulation(phosphate).fragments[0].left_end.five_prime_phosphate).toBe('phosphorylated');
        phosphate.fragments[0].left_end.five_prime_phosphate = 'maybe';
        expect(() => parseRestrictionDigestSimulation(phosphate)).toThrow();
    });

    it('sends excluded_cleavage_group_ids only when non-empty, on simulate and save', async () => {
        const bodies: Array<Record<string, unknown>> = [];
        const transport = vi.fn(async (_url: unknown, init?: RequestInit) => { bodies.push(JSON.parse(String(init?.body))); return new Response(JSON.stringify(fixture)); });
        const args = { source: { kind: 'inline_dna' as const, name: 'x', dna: 'ACGT', topology: 'circular' as const }, catalog: { catalog_id: 'c', expected_catalog_sha256: 'a'.repeat(64) }, enzymeIds: ['EcoRI'], transport };
        await simulateRestrictionDigest({ ...args, excludedCleavageGroupIds: [groupId] }).catch(() => undefined);
        await simulateRestrictionDigest(args).catch(() => undefined);
        expect(bodies[0].excluded_cleavage_group_ids).toEqual([groupId]);
        expect('excluded_cleavage_group_ids' in bodies[1]).toBe(false);
        const saveBodies: Array<Record<string, unknown>> = [];
        vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => { saveBodies.push(JSON.parse(String(init?.body))); return new Response('{}'); }));
        const simulation = { ...parseRestrictionDigestSimulation(fixture), source: { ...fixture.source, kind: 'molecular_revision', sequence_id: 's', revision_id: 'r', revision_number: 1 } } as RestrictionDigestSimulation;
        await saveRestrictionDigest(simulation, 'key', [groupId]).catch(() => undefined);
        await saveRestrictionDigest(simulation, 'key').catch(() => undefined);
        expect(saveBodies[0].excluded_cleavage_group_ids).toEqual([groupId]);
        expect('excluded_cleavage_group_ids' in saveBodies[1]).toBe(false);
    });

    const panel = (extra: Record<string, unknown> = {}, sequenceData: Record<string, unknown> = { name: 'ring', sequence: 'ACGTACGTAC', circular: true, sequenceType: 'dna', moleculeStrandedness: 'double', features: [] }) => {
        const simulate = vi.fn();
        const simulation = { ...parseRestrictionDigestSimulation(fixture), ...extra } as RestrictionDigestSimulation;
        const node = (
            <DigestPanel sequenceData={sequenceData as never} sequenceId="source" onHighlight={noop} catalog={simulation.catalog} catalogRecords={simulation.selected_enzymes}
                analysis={null} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null}
                onDigestSelectionChange={noop} onSimulateDigest={simulate} />
        );
        return { node, simulate };
    };

    it('lists one checkbox per cleavage event and re-digests with the unchecked group id', async () => {
        const { node, simulate } = panel();
        await render(node);
        await click('Digest');
        const site = host.querySelector(`input[aria-label="Cut site ${groupId}"]`) as HTMLInputElement;
        expect(site.checked).toBe(true);
        await act(async () => site.click());
        expect(site.checked).toBe(false);
        await click('Re-digest with selected sites');
        expect(simulate).toHaveBeenLastCalledWith(['EcoRI'], [groupId]);
        // Default run (nothing excluded) keeps the original single-argument call.
        await act(async () => (host.querySelector(`input[aria-label="Cut site ${groupId}"]`) as HTMLInputElement).click());
        await click('Run Digest (1 enzyme)');
        expect(simulate).toHaveBeenLastCalledWith(['EcoRI']);
    });

    it('shows sites that are not cuttable on this fragment', async () => {
        const { node } = panel({ not_cuttable: [{ enzyme_id: 'EcoRI', site_start: 4, reason: 'overlaps an unpaired overhang' }] });
        await render(node);
        expect(host.textContent).toContain('EcoRI site at 5: not cuttable on this fragment');
    });

    it('allows digesting a retained sticky fragment (no unsupported notice)', async () => {
        const { node } = panel({}, { name: 'sticky', sequence: 'AATTCAATTG', circular: false, sequenceType: 'dna', moleculeStrandedness: 'double', features: [], digestFragment: fixture.fragments[0] });
        await render(node);
        expect(host.textContent).not.toContain('unsupported');
        await click('Digest');
        expect((button('Run Digest (1 enzyme)') as HTMLButtonElement).disabled).toBe(false);
    });
});

describe('digest → assembly transfer', () => {
    const simulation = parseRestrictionDigestSimulation(fixture);
    it('default stays the slice reference; saved digests add the child revision', () => {
        const unsaved = digestFragmentsForAssembly(simulation, [0], 'parent-doc', 'ring')[0];
        expect(unsaved.source_sequence_id).toBe('parent-doc');
        expect(unsaved.source_revision).toBeUndefined();
        expect(unsaved.source_start).toBe(simulation.fragments[0].top_start_boundary_normalized);
        const saved = { outputs: [{ fragment_index: 0, document_id: 'child-doc', revision_id: 'child-rev', output_edge_id: 'e', name: 'child', topology: 'linear' as const, content_sha256: 'a'.repeat(64), content_length: 10 }] };
        const carried = digestFragmentsForAssembly(simulation, [0], 'parent-doc', 'ring', saved)[0];
        expect(carried).toMatchObject({ source_sequence_id: 'child-doc', source_revision: 1, source_start: 0, source_end: 10, name: expect.any(String) });
        expect(carried.metadata).toMatchObject({ saved_digest_child: { document_id: 'child-doc', revision_id: 'child-rev', revision_number: 1 }, parent_slice: { source_sequence_id: 'parent-doc' } });
    });
    it('restriction-cut ends read 5\u2032 phosphorylated; ends not made by an enzyme stay unknown', () => {
        const [cut] = digestFragmentsForAssembly(parseRestrictionDigestSimulation(structuredClone(fixture)), [0], null, 'x');
        expect(cut.left_end).toMatchObject({ five_prime_phosphate: 'phosphorylated' });
        expect(cut.right_end).toMatchObject({ five_prime_phosphate: 'phosphorylated' });
        const natural = structuredClone(fixture); natural.fragments[0].right_end.enzyme_created = false;
        const [kept] = digestFragmentsForAssembly(parseRestrictionDigestSimulation(natural), [0], null, 'x');
        expect('five_prime_phosphate' in (kept.right_end ?? {})).toBe(false);
        const stated = structuredClone(fixture); stated.fragments[0].left_end.five_prime_phosphate = 'unphosphorylated';
        const [dephos] = digestFragmentsForAssembly(parseRestrictionDigestSimulation(stated), [0], null, 'x');
        expect(dephos.left_end).toMatchObject({ five_prime_phosphate: 'unphosphorylated' });
    });
});

describe('inline fragment features', () => {
    const features = [
        { id: 'a', name: 'A', type: 'gene', start: 2, end: 6, strand: 1 as const },
        { id: 'b', name: 'B', type: 'CDS', start: 8, end: 10, strand: -1 as const, segments: [{ start: 8, end: 9 }, { start: 9, end: 10 }] },
        { id: 'c', name: 'C', type: 'gene', start: 0, end: 2, strand: 1 as const },
    ];
    it('whole construct keeps coordinates; selection shifts, clips and drops', () => {
        expect(inlineFragmentFeatures(features, 12)?.map((f) => [f.name, f.start, f.end])).toEqual([['A', 2, 6], ['B', 8, 10], ['C', 0, 2]]);
        const selection = inlineFragmentFeatures(features, 12, { start: 4, end: 9 })!;
        expect(selection.map((f) => [f.name, f.start, f.end])).toEqual([['A', 0, 2], ['B', 4, 5]]);
        expect(selection[0].provenance).toMatchObject({ clipped_by_fragment: true });
    });
    it('a wrapping selection is two runs in biological order', () => {
        const wrap = inlineFragmentFeatures(features, 12, { start: 10, end: 3, wrapsOrigin: true })!;
        expect(wrap.map((f) => f.name).sort()).toEqual(['A', 'C']);
        expect(wrap.find((f) => f.name === 'C')).toMatchObject({ start: 2, end: 4 });
        expect(wrap.find((f) => f.name === 'A')).toMatchObject({ start: 4, end: 5 });
    });
});

describe('AssemblyPanel', () => {
    it('adds a saved fragment pinned to its revision with its physical ends', async () => {
        vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: product(), message: 'ok' } } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Add saved fragment');
        expect(host.textContent).toContain('saved child');
        await click('Simulate');
        const sent = vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].fragments[0];
        expect(sent).toMatchObject({ source_sequence_id: 'doc-9', source_revision: 2, left_end: { type: 'sticky_5', overhang: 'AATT' } });
    });

    it('whole-construct fragments send fragment-local features', async () => {
        vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: product(), message: 'ok' } } as never);
        const annotated = { ...seq, features: [{ id: 'f', name: 'F', type: 'gene', start: 3, end: 9, strand: 1 as const }] as never[] };
        await render(<AssemblyPanel sequenceData={annotated} selection={{ start: 2, end: 20 }} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Add whole construct'); await click('Add selection'); await click('Simulate');
        const [whole, part] = vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].fragments;
        expect(whole.features?.[0]).toMatchObject({ start: 3, end: 9 });
        expect(part.features?.[0]).toMatchObject({ start: 1, end: 7 });
    });

    it('end operation previews, then Apply adds the child to the basket', async () => {
        const response = { sequence: 'AATTCGAATT', topology: 'linear', left_end: { type: 'blunt', overhang: '' }, right_end: { type: 'blunt', overhang: '' }, features: [], changed: { bases_added: 4, bases_removed: 0 }, notes: ['right end: fill-in of a 3′ overhang leaves the end unchanged'], operation_id: 'op', document_id: 'doc-new', revision_id: 'rev-new', sequence_id: null };
        vi.mocked(api.endOperations).mockResolvedValue({ data: response } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Add saved fragment');
        expect((button('Apply') as HTMLButtonElement).disabled).toBe(true);
        await click('Preview');
        expect(vi.mocked(api.endOperations).mock.calls[0][0]).toBe('preview');
        expect(vi.mocked(api.endOperations).mock.calls[0][1]).toMatchObject({ source: { kind: 'molecular_revision', revision_id: 'rev-9' }, operation: 'fill_in', end: 'both' });
        expect(host.textContent).toContain('+4 / −0 bases');
        expect(host.textContent).toContain('leaves the end unchanged');
        await click('Apply');
        expect(vi.mocked(api.endOperations).mock.calls[1][0]).toBe('apply');
        expect(host.textContent).toContain('saved child fill in');
        expect(host.querySelectorAll('[data-end-operations]')).toHaveLength(2);
    });

    it('ligation junctions show the sealed label and the unknown note', async () => {
        const junction = (sealed?: string) => ({ left_fragment_id: 'a', right_fragment_id: 'b', left_fragment_name: 'A', right_fragment_name: 'B', mode: 'ligation', overlap_length: 0, junction_sequence: '', validation: 'ok', notes: [], ...(sealed ? { sealed } : {}) });
        vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: product([junction('one_strand'), junction()]), message: 'ok' } } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Add whole construct'); await click('Simulate');
        expect([...host.querySelectorAll('[data-junction-sealed]')].map((e) => e.textContent)).toEqual(['joined, nicked', 'joined, ligation chemistry not established']);
    });

    it('ligation sends the enzymes that made the ends and shows the junction-site note per junction', async () => {
        const frag = (id: string, enzyme: string) => ({ id, name: id, sequence: 'GATCAAAG', orientation: 'forward', metadata: { end_enzymes: { left: [enzyme], right: [enzyme] } } });
        const junction = { left_fragment_id: 'v', right_fragment_id: 'i', left_fragment_name: 'v', right_fragment_name: 'i', mode: 'ligation', overlap_length: 0, junction_sequence: '', validation: 'ok', notes: [] };
        vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: product([junction]), message: 'ok',
            junction_sites: [{ left_fragment_id: 'v', right_fragment_id: 'i', position: 4, regenerated: [], destroyed: ['BamHI', 'BglII'], note: 'Junction is not cleavable by either enzyme (hybrid site)' }] } } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} transferredFragments={[frag('v', 'BamHI'), frag('i', 'BglII')] as never} />);
        await click('Simulate');
        expect(vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].observe_enzymes).toEqual(['BamHI', 'BglII']);
        expect(host.querySelector('[data-junction-site]')?.textContent).toBe('Junction is not cleavable by either enzyme (hybrid site)');
    });

    it('fragments without recorded end enzymes send no observe_enzymes', async () => {
        vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: product(), message: 'ok' } } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Add whole construct'); await click('Simulate');
        expect('observe_enzymes' in vi.mocked(api.simulateLigationAssembly).mock.calls[0][0] && vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].observe_enzymes !== undefined).toBe(false);
    });

    it('digest transfer records the enzymes that made each end', () => {
        const sim = parseRestrictionDigestSimulation(fixture);
        const [first] = digestFragmentsForAssembly(sim, [0], 'src', 'src');
        const f = sim.fragments[0];
        expect(first.metadata?.end_enzymes).toEqual({ left: f.left_end.contributing_enzyme_ids, right: f.right_end.contributing_enzyme_ids });
    });

    it('a plan with blockers no longer disables export, Save or Run', async () => {
        const plan = {
            ordered_fragments: [{ id: 'f1', name: 'one', sequence: 'ACGT', sequence_sha256: 'x' }], selected_product: { ...product(), mode: 'gibson' }, pydna_exact_candidate_count: 1,
            planner_version: '1', validator_version: '1', plan_checksum: 'plan', order_ready: false,
            quality_checks: [{ check_id: 'pieces', status: 'blocker', detail: 'more than 10 pieces' }, { check_id: 'gc', status: 'advisory', detail: 'GC outside 20-80%' }],
        };
        vi.mocked(api.planDnaWeaverGibsonAssembly).mockResolvedValue({ data: plan } as never);
        vi.mocked(api.saveDnaWeaverGibsonAssembly).mockResolvedValue({ data: { ...plan, message: 'saved' } } as never);
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await click('Gibson'); await click('Plan vendor fragments');
        expect(host.textContent).not.toContain('Blocked: do not order or save');
        expect(host.textContent).toContain('more than 10 pieces'); expect(host.textContent).toContain('GC outside 20-80%');
        for (const label of ['Export order FASTA', 'Export order CSV', 'Save computed plan', 'Re-plan and validate']) expect((button(label) as HTMLButtonElement).disabled, label).toBe(false);
        await click('Export order FASTA'); await click('Export order CSV');
        expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
        await click('Save computed plan');
        expect(api.saveDnaWeaverGibsonAssembly).toHaveBeenCalledTimes(1);
    });

    describe('Gibson reopen and design workspace', () => {
        const saved = {
            mode: 'gibson',
            design_request: { fragments: [{ id: 'only', name: 'closing fragment', sequence: 'ACGTACGTACGTACGTACGTAAAA', preparation: 'ready_linear', orientation: 'forward', role: 'vector' }], circular: true, overlap: 25, target_tm: 58, min_anneal: 14 },
        };
        const design = {
            engine: 'pydna', engine_version: '1', circular: true, overlap: 25, target_tm: 58, min_anneal: 14, candidates: [{}], selected_candidate_checksum: 'c'.repeat(64), computation_id: 'comp',
            selected_product: { ...product([{ left_fragment_id: 'a', right_fragment_id: 'a', left_fragment_name: 'A', right_fragment_name: 'A', mode: 'gibson', overlap_length: 25, overlap_sequence: 'ACGT', junction_sequence: '', validation: 'ok', notes: [], overlap_tm: 61.4 }]), mode: 'gibson' },
            designed_fragments: [{ id: 'only', name: 'closing fragment', preparation: 'ready_linear', sequence: 'ACGTACGT', checksum: 'z', primer_ids: ['p1'] }],
            primers: [
                { id: 'p1', fragment_id: 'only', fragment_name: 'closing fragment', direction: 'forward', full_sequence: 'TTTTACGTACGTACGTACGT', annealing_sequence: 'ACGTACGTACGTACGT', tail_sequence: 'TTTT', tm: 60.8, tm_model: 'pydna Tm_NN (SantaLucia 1998)', warnings: [], template_site: { start: 0, end: 16, strand: 1 }, product_site: { start: 4, end: 20, strand: 1 } },
                { id: 'p2', fragment_id: 'only', fragment_name: 'closing fragment', direction: 'reverse', full_sequence: 'AAAAACGT', annealing_sequence: 'ACGT', tail_sequence: 'AAAA', tm: 30, tm_model: 'pydna Tm_NN (SantaLucia 1998)', warnings: [] },
            ],
            warnings: [], source_provenance: [], message: 'ok',
        };
        it('restores parts and settings, and a one-fragment closure submits', async () => {
            vi.mocked(api.designGibsonAssembly).mockResolvedValue({ data: design } as never);
            await render(<AssemblyPanel sequenceData={{ ...seq, operationParams: saved }} selection={null} selectedSequenceId="saved" onLoadProduct={noop} onLoadSavedWorkup={noop} />);
            expect(host.textContent).toContain('closing fragment');
            const numbers = [...host.querySelectorAll('input[type="number"]')].map((i) => (i as HTMLInputElement).value);
            expect(numbers).toEqual(['25', '58', '14']);
            expect((button('Design & Simulate') as HTMLButtonElement).disabled).toBe(false);
            await click('Design & Simulate');
            const request = vi.mocked(api.designGibsonAssembly).mock.calls[0][0];
            expect(request).toMatchObject({ circular: true, overlap: 25, target_tm: 58, min_anneal: 14 });
            expect(request.fragments).toHaveLength(1);
            expect(request.fragments[0]).toMatchObject({ id: 'only', preparation: 'ready_linear', sequence: 'ACGTACGTACGTACGTACGTAAAA' });
        });

        it('shows Tm model, overlap Tm, mapped sites or needs review, and downloads primers CSV and fragments FASTA', async () => {
            vi.mocked(api.designGibsonAssembly).mockResolvedValue({ data: design } as never);
            await render(<AssemblyPanel sequenceData={{ ...seq, operationParams: saved }} selection={null} selectedSequenceId="saved" onLoadProduct={noop} onLoadSavedWorkup={noop} />);
            await click('Design & Simulate');
            expect(host.textContent).toContain('Tm 60.8 °C (pydna Tm_NN (SantaLucia 1998))');
            expect(host.textContent).toContain('overlap Tm 61.4 °C');
            expect(host.textContent).toContain('template 1–16 (+)'); expect(host.textContent).toContain('product 5–20 (+)');
            expect(host.textContent).toContain('Binding site needs review');
            await click('Download primers CSV'); await click('Download fragments FASTA');
            expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
        });
    });
});
