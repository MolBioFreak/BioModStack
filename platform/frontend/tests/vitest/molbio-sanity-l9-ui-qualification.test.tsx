import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { fragmentFromRevision } from '../../src/components/MolBioToolkit/FragmentChooser';
import { parseRestrictionDigestSimulation, type RestrictionDigestSimulation } from '../../src/lib/restrictionAnalysis';

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(), simulateLigationAssembly: vi.fn(), endOperations: vi.fn(),
}));

const fixture = JSON.parse(readFileSync('../api/tests/fixtures/restriction_digest/circular_single_ecori.json', 'utf8'));
const groupId: string = fixture.cleavages[0].contributor_group_id;
const seq = { name: 'editable', sequence: 'ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT', circular: false, sequenceType: 'dna' as const, features: [] as never[], primers: [] as never[] };
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
    vi.mocked(api.simulateLigationAssembly).mockResolvedValue({ data: { product: { sequence: 'ACGT', length: 4, circular: false, mode: 'ligation', fragments: [], golden_gate_authority: null, validation_notes: [], warnings: [], junctions: [] }, message: 'ok' } } as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

const end = (strand: 'top' | 'bottom') => ({ kind: 'five_prime_overhang', overhang_sequence_5to3: 'TGCA', protruding_strand: strand, length_nt: 4, five_prime_phosphate: 'phosphorylated' });
const savedRevision = (physicalEnds: unknown) => ({
    document_id: 'doc-1', sequence_id: 'doc-1', document_name: 'saved TGCA fragment', revision_id: 'rev-1', revision_number: 1, relation: 'current', content_length: 12,
    snapshot: { sequence: 'TGCAACGTACGT', is_circular: false }, provenance: physicalEnds ? { physical_ends: physicalEnds } : {},
}) as never;

describe('Assembly keeps the ends of a saved molecule when the mode is chosen', () => {
    it('a saved sticky-ended fragment is still sticky after clicking Ligation, and the server is sent those ends', async () => {
        const fragment = fragmentFromRevision(savedRevision({ left_end: end('top'), right_end: end('bottom') }));
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} transferredFragments={[fragment]} />);
        await click('Ligation');
        await click('Simulate');
        const sent = vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].fragments[0];
        expect(sent.left_end).toMatchObject({ type: 'sticky_5', overhang: 'TGCA', protruding_strand: 'top' });
        expect(sent.right_end).toMatchObject({ type: 'sticky_5', overhang: 'TGCA', protruding_strand: 'bottom' });
    });

    it('a fragment with no recorded ends still takes the mode default (behaviour unchanged)', async () => {
        const fragment = { id: 'x', name: 'pasted-like', sequence: 'GATCAAAG', orientation: 'forward', left_end: { type: 'sticky_5', overhang: 'GATC' }, right_end: { type: 'sticky_5', overhang: 'GATC' } };
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} transferredFragments={[fragment] as never} />);
        await click('Ligation');
        await click('Simulate');
        expect(vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].fragments[0].left_end).toMatchObject({ type: 'blunt' });
    });

    it('a saved fragment carries its revision id so End operations read the saved molecule', () => {
        const fragment = fragmentFromRevision(savedRevision({ left_end: end('top'), right_end: end('bottom') }));
        expect(fragment.metadata).toMatchObject({ revision_id: 'rev-1', retained_ends: true });
    });
});

describe('Digest cut-site list after a re-digest', () => {
    const panel = (simulation: RestrictionDigestSimulation) => (
        <DigestPanel sequenceData={{ name: 'ring', sequence: 'ACGTACGTAC', circular: true, sequenceType: 'dna', moleculeStrandedness: 'double', features: [] } as never}
            sequenceId="source" onHighlight={noop} catalog={simulation.catalog} catalogRecords={simulation.selected_enzymes} analysis={null} authorityLoading={false}
            authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={noop} onSimulateDigest={vi.fn()} />
    );

    it('keeps an excluded site in the list, unchecked, so it can be switched back on', async () => {
        const all = parseRestrictionDigestSimulation(fixture);
        const simulate = vi.fn();
        const node = (simulation: RestrictionDigestSimulation) => React.cloneElement(panel(simulation), { onSimulateDigest: simulate });
        await render(node(all));
        await click('Digest');
        const box = () => host.querySelector(`input[aria-label="Cut site ${groupId}"]`) as HTMLInputElement | null;
        await act(async () => box()!.click());              // switch the only site off
        await click('Re-digest with selected sites');
        expect(simulate).toHaveBeenLastCalledWith(['EcoRI'], [groupId]);
        // The server answers with no cleavages and echoes the excluded id.
        await render(node({ ...all, cleavages: [], excluded_cleavage_group_ids: [groupId] } as RestrictionDigestSimulation));
        expect(box(), 'excluded site is still listed').not.toBeNull();
        expect(box()!.checked).toBe(false);
        await act(async () => box()!.click());              // switch it back on
        expect(box()!.checked).toBe(true);
        await click('Re-digest with selected sites');
        expect(simulate).toHaveBeenLastCalledWith(['EcoRI']);
    });
});
