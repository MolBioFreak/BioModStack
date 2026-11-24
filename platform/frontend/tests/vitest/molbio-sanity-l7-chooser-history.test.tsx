import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    chooser: vi.fn(), relatives: vi.fn(), summaries: vi.fn(), revision: vi.fn(),
}));
vi.mock('../../src/lib/api', async (original) => ({
    ...await original<any>(),
    fetchChooser: mocks.chooser,
    fetchRevisionRelatives: mocks.relatives,
    fetchMolecularRevisionSummaries: mocks.summaries,
    fetchMolecularRevision: mocks.revision,
}));
import { FragmentChooser, fragmentFromChooserItem, loadChooserFragment } from '../../src/components/MolBioToolkit/FragmentChooser';
import { HistoryPanel } from '../../src/components/MolBioToolkit/panels/HistoryPanel';

let root: Root, host: HTMLDivElement;
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); Object.values(mocks).forEach((m) => m.mockReset()); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
const button = (text: string | RegExp) => [...host.querySelectorAll('button')].find((b) => typeof text === 'string' ? b.textContent?.trim() === text : text.test(b.textContent ?? ''));

const item = (n: number, extra: Record<string, unknown> = {}) => ({
    document_id: `doc-${n}`, revision_id: `rev-${n}`, revision_number: n, sequence_id: null, name: `Frag ${n}`,
    topology: 'linear', length: 100 + n, is_current: true, origin: 'retained',
    produced_by: { operation_id: `opabcdef${n}0000`, operation_kind: 'restriction_digest', role: 'digest_fragment' }, ...extra,
});

it('chooser loads on demand, filters by search, pages, and labels producing operations', async () => {
    mocks.chooser.mockImplementation(async (params: any) => params.cursor
        ? { items: [item(3)], next_cursor: null }
        : params.q ? { items: [item(9, { name: 'Needle', origin: 'shelf', sequence_id: 's9', produced_by: null })], next_cursor: null }
            : { items: [item(1), item(2)], next_cursor: 'c2' });
    const picked = vi.fn();
    await act(async () => root.render(<FragmentChooser onPick={picked} kinds={['restriction_digest']} />));
    expect(mocks.chooser).not.toHaveBeenCalled(); // nothing is fetched on mount
    await act(async () => button('Add from saved molecules')!.click());
    await flush();
    expect(mocks.chooser).toHaveBeenCalledTimes(1);
    expect(mocks.chooser.mock.calls[0][0]).toMatchObject({ kinds: ['restriction_digest'], cursor: null });
    expect(host.textContent).toContain('Frag 1');
    expect(host.textContent).toContain('restriction digest opabcdef');
    await act(async () => button('Load more')!.click());
    await flush();
    expect(mocks.chooser.mock.calls[1][0]).toMatchObject({ cursor: 'c2' });
    expect(host.textContent).toContain('Frag 3');
    expect(host.textContent).toContain('Frag 1'); // earlier page kept
    const search = host.querySelector<HTMLInputElement>('input[aria-label="Search saved molecules"]')!;
    await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        setter.call(search, 'Needle'); search.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(mocks.chooser.mock.calls.at(-1)![0]).toMatchObject({ q: 'Needle' });
    expect(host.textContent).toContain('Needle');
    expect(host.textContent).toContain('saved molecule');
    expect(host.textContent).not.toContain('Frag 1');
    await act(async () => button(/Needle/)!.click());
    expect(picked).toHaveBeenCalledWith(expect.objectContaining({ revision_id: 'rev-9' }));
});

it('fragmentFromChooserItem pins document + revision number, full-source slice and the item ends', () => {
    const fragment = fragmentFromChooserItem(item(4, {
        topology: 'circular',
        left_end: { kind: 'five_prime_overhang', overhang_sequence_5to3: 'GATC', protruding_strand: 'top', length_nt: 4 },
        right_end: { kind: 'blunt', overhang_sequence_5to3: null, protruding_strand: null, length_nt: 0 },
    }) as any, 'ACGT');
    expect(fragment).toMatchObject({
        sequence: 'ACGT', circular: true, orientation: 'forward',
        source_sequence_id: 'doc-4', source_revision: 4,
        left_end: { type: 'sticky_5', overhang: 'GATC', protruding_strand: 'top' },
        right_end: { type: 'blunt' },
    });
    expect(fragment.source_start).toBeUndefined(); // full-source slice
    expect(fragment.source_end).toBeUndefined();
});

it('loadChooserFragment reads bases from the immutable revision and reads digest geometry ends', async () => {
    mocks.revision.mockResolvedValue({
        document_id: 'doc-5', sequence_id: 'doc-5', revision_id: 'rev-5', revision_number: 5, document_name: 'Frag 5',
        relation: 'historical', content_length: 6, snapshot: { sequence: 'GGATCC', is_circular: false },
        provenance: { schema: 'bms.molbio.restriction-digest-fragment-provenance.v1', geometry: {
            left_end: { kind: 'three_prime_overhang', overhang_sequence_5to3: 'TG', protruding_strand: 'bottom' },
            right_end: { kind: 'blunt' } } },
    });
    const fragment = await loadChooserFragment(item(5) as any);
    expect(mocks.revision).toHaveBeenCalledWith('doc-5', 'rev-5');
    expect(fragment).toMatchObject({ sequence: 'GGATCC', source_revision: 5, left_end: { type: 'sticky_3', overhang: 'TG', protruding_strand: 'bottom' } });
});

const edge = (n: number, role: string, name = `Mol ${n}`) => ({
    revision_id: `rev-${n}`, document_id: `doc-${n}`, document_name: name, revision_number: 1, role, position: 0, topology: 'linear', length: 50 + n,
});
const op = (id: string, kind: string, inputs: any[], outputs: any[]) => ({ operation_id: id, operation_kind: kind, created_at: '2026-01-01T00:00:00Z', inputs, outputs });

it('HistoryPanel shows derived + source operations, expands a further generation, and fires actions', async () => {
    mocks.summaries.mockResolvedValue([{ id: 'r', revision_id: 'rev-1', sequence_id: 'seq-1', revision_number: 1, change_kind: 'create', content_sha256: 'x', content_length: 10, topology: 'linear', created_at: '2026-01-01T00:00:00Z', created_by: null, is_current: true }]);
    mocks.relatives.mockImplementation(async (revisionId: string, params: any) => {
        if (revisionId === 'rev-1') {
            return {
                revision: { revision_id: 'rev-1', document_id: 'seq-1', document_name: 'Parent', revision_number: 1 },
                sources: [op('opsrc000', 'golden_gate_design', [edge(7, 'part', 'Part A')], [edge(1, 'product')])],
                derived: params.cursor
                    ? { items: [op('opd2xxxx', 'pcr', [edge(1, 'template')], [edge(3, 'pcr_product', 'PCR out')])], next_cursor: null }
                    : { items: [op('opd1xxxx', 'restriction_digest', [edge(1, 'source')], [edge(2, 'digest_fragment', 'Child')])], next_cursor: 'cur2' },
            };
        }
        return { revision: { revision_id: revisionId, document_id: 'x', document_name: 'Child', revision_number: 1 }, sources: [],
            derived: { items: [op('opgrand0', 'assembly', [edge(2, 'fragment')], [edge(8, 'product', 'Grandchild')])], next_cursor: null } };
    });
    const useIn = vi.fn(), openFor = vi.fn();
    await act(async () => root.render(<HistoryPanel
        sequenceData={{ name: 'Parent', sequence: 'ACGT', sequenceType: 'dna', circular: false, features: [], primers: [] } as any}
        selectedSequenceId="seq-1" historyJournal={[]} workspaces={[]} activeWorkspaceId="w" onActivateWorkspace={() => {}}
        revisionHref={(s, r) => `/molbio?molbio_sequence_id=${s}&molbio_revision_id=${r}`}
        relativeActions={{ operationHref: (id) => `/molbio?molbio_operation_id=${id}`, onUseInAssembly: useIn, onOpenForPanel: openFor }}
    />));
    await flush();
    expect(mocks.relatives).toHaveBeenCalledTimes(1);
    expect(mocks.relatives.mock.calls[0][0]).toBe('rev-1');
    const text = host.textContent!;
    expect(text).toContain('Made by golden gate design'); // source / producing operation
    expect(text).toContain('Part A');
    expect(text).toContain('Used in restriction digest'); // derived
    expect(text).toContain('Child');
    expect(host.querySelector('a[href="/molbio?molbio_operation_id=opd1xxxx"]')!.textContent).toBe('Open plan');
    // children expose actions; the source parents do not
    expect(host.querySelectorAll('button').length).toBeGreaterThan(0);
    await act(async () => button('Use in assembly')!.click());
    expect(useIn).toHaveBeenCalledWith(expect.objectContaining({ revision_id: 'rev-2' }));
    await act(async () => button('Digest')!.click());
    expect(openFor).toHaveBeenCalledWith(expect.objectContaining({ revision_id: 'rev-2' }), 'digest');
    // paging derived
    await act(async () => button('Load more')!.click());
    await flush();
    expect(mocks.relatives.mock.calls.at(-1)![1]).toMatchObject({ cursor: 'cur2' });
    expect(host.textContent).toContain('PCR out');
    // expand next generation on demand: nothing fetched for rev-2 until clicked
    expect(mocks.relatives.mock.calls.some((c) => c[0] === 'rev-2')).toBe(false);
    const childRow = [...host.querySelectorAll('li')].find((li) => li.textContent?.includes('Child'))!;
    await act(async () => [...childRow.querySelectorAll('button')].find((b) => b.textContent === 'Show related')!.click());
    await flush();
    expect(mocks.relatives.mock.calls.some((c) => c[0] === 'rev-2')).toBe(true);
    expect(host.textContent).toContain('Grandchild');
    // viewing history never writes: only GET-style readers were called
    expect(mocks.revision).not.toHaveBeenCalled();
});
