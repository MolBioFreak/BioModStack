import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as api from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';

// Keep the real chooser, immutable-revision loader, and AssemblyPanel together.
vi.mock('../../src/lib/api', async original => ({
    ...await original<typeof import('../../src/lib/api')>(),
    fetchChooser: vi.fn(), fetchMolecularRevision: vi.fn(),
    fetchGoldenGateAssemblyOptions: vi.fn(), fetchSavedGibsonWorkups: vi.fn(),
    simulateLigationAssembly: vi.fn(), simulateGibsonAssembly: vi.fn(),
    simulateGoldenGateAssembly: vi.fn(), designGibsonAssembly: vi.fn(),
}));
const item = {
    document_id: 'doc-9', revision_id: 'exact-rev-2', revision_number: 2,
    sequence_id: null, name: 'Saved child', topology: 'linear', length: 6,
    is_current: false, origin: 'retained', produced_by: null,
};
const revision = {
    ...item, document_name: 'Saved child', content_length: 6, relation: 'historical',
    snapshot: { sequence: 'AATTCG', is_circular: false },
    provenance: { physical_ends: {
        left_end: { kind: 'five_prime_overhang', overhang_sequence_5to3: 'AATT', protruding_strand: 'top', five_prime_phosphate: 'phosphorylated' },
        right_end: { kind: 'blunt', overhang_sequence_5to3: '', five_prime_phosphate: 'unphosphorylated' },
    } },
};
const seq = { name: 'target', sequence: 'ACGT'.repeat(20), circular: true, sequenceType: 'dna' as const, features: [], primers: [] };
let root: Root, host: HTMLDivElement, client: QueryClient;
const findButton = (label: string) => [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label);
async function click(label: string) {
    const b = findButton(label); expect(b, label).toBeDefined();
    await act(async () => b!.click());
}
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId="target-doc" onLoadProduct={() => {}} onLoadSavedWorkup={() => {}} /></QueryClientProvider>));
}
beforeEach(() => {
    vi.resetAllMocks();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.fetchChooser).mockResolvedValue({ items: [item], next_cursor: null } as never);
    vi.mocked(api.fetchMolecularRevision).mockResolvedValue(revision as never);
    vi.mocked(api.fetchSavedGibsonWorkups).mockResolvedValue({ data: [] } as never);
    vi.mocked(api.fetchGoldenGateAssemblyOptions).mockResolvedValue({ data: { catalog: { catalog_id: 'test-catalog', catalog_sha256: 'a'.repeat(64) }, enzymes: [{ enzyme_id: 'BsaI', name: 'BsaI', overhang_length: 4 }] } } as never);
    for (const fn of [api.simulateLigationAssembly, api.simulateGibsonAssembly, api.simulateGoldenGateAssembly, api.designGibsonAssembly]) vi.mocked(fn).mockRejectedValue(new Error('end of request capture'));
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

it.each(['Ligation', 'Gibson', 'Golden Gate', 'Gibson PCR'])('%s loads saved fragment bases from the selected immutable revision before submitting', async mode => {
    await mount();
    if (mode !== 'Ligation') await click(mode.startsWith('Gibson') ? 'Gibson' : mode);
    if (mode === 'Gibson') await click('Validate purchased fragments');
    if (mode === 'Gibson PCR') await click('PCR template route (optional)');
    await click('Add saved fragment');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    await act(async () => { const row = host.querySelector('li button') as HTMLButtonElement; expect(row).not.toBeNull(); row.click(); });
    expect(api.fetchMolecularRevision).toHaveBeenCalledWith('doc-9', 'exact-rev-2');
    expect(host.textContent).toContain('6 nt');
    expect(host.textContent).not.toMatch(/\b0 nt •/);
    await click(mode === 'Gibson PCR' ? 'Design & Simulate' : 'Simulate');
    const fn = mode === 'Ligation' ? api.simulateLigationAssembly : mode === 'Golden Gate' ? api.simulateGoldenGateAssembly : mode === 'Gibson PCR' ? api.designGibsonAssembly : api.simulateGibsonAssembly;
    const fragment = vi.mocked(fn).mock.calls[0][0].fragments[0];
    expect(fragment).toMatchObject({
        sequence: 'AATTCG', source_sequence_id: 'doc-9', source_revision: 2,
        metadata: { revision_id: 'exact-rev-2', retained_ends: true },
        left_end: { type: 'sticky_5', overhang: 'AATT', protruding_strand: 'top', five_prime_phosphate: 'phosphorylated' },
        right_end: { type: 'blunt', five_prime_phosphate: 'unphosphorylated' },
    });
});

it('reports a failed revision read without adding an empty fragment, then permits retry', async () => {
    vi.mocked(api.fetchMolecularRevision).mockRejectedValueOnce(new Error('revision read failed'));
    await mount(); await click('Add saved fragment');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    await act(async () => (host.querySelector('li button') as HTMLButtonElement).click());
    expect(host.textContent).toContain('revision read failed');
    expect(host.textContent).toContain('No fragments in the basket yet.');
    await act(async () => (host.querySelector('li button') as HTMLButtonElement).click());
    expect(host.textContent).toContain('6 nt');
    expect(host.textContent).not.toContain('revision read failed');
});

it('keeps saved fragments in click order when a revision read is slow', async () => {
    const second = { ...item, document_id: 'doc-10', revision_id: 'rev-10', name: 'Second child' };
    vi.mocked(api.fetchChooser).mockResolvedValue({ items: [item, second], next_cursor: null } as never);
    let release!: (value: never) => void;
    vi.mocked(api.fetchMolecularRevision).mockImplementation((doc) => doc === 'doc-9'
        ? new Promise(resolve => { release = resolve; })
        : Promise.resolve({ ...revision, ...second, document_name: 'Second child' } as never));
    await mount(); await click('Add saved fragment');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    await act(async () => {
        const rows = host.querySelectorAll<HTMLButtonElement>('li button');
        rows[0].click(); rows[1].click();
    });
    await act(async () => release(revision as never));
    await click('Simulate');
    expect(vi.mocked(api.simulateLigationAssembly).mock.calls[0][0].fragments.map(f => f.source_sequence_id)).toEqual(['doc-9', 'doc-10']);
});
