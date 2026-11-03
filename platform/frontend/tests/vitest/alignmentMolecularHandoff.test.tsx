import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter, Routes, Route, useNavigate} from 'react-router-dom';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {beforeEach, expect, it, vi} from 'vitest';
import {SequenceLibrary} from '../../src/components/MolBioToolkit/MolBioToolkitV2';
import AlignmentPage from '../../src/components/alignments/AlignmentPage';
import {molecularAlignmentHref} from '../../src/components/alignments/MolecularAlignmentSources';
import type {NucleotideSequenceListItem} from '../../src/components/MolBioToolkit/types';
import schema from '../../src/components/alignments/alignment-request.schema.json';

const mocks = vi.hoisted(() => ({summaries: vi.fn(), revision: vi.fn(), submit: vi.fn(), opened: vi.fn()}));
vi.mock('../../src/lib/api', async original => ({...(await original<object>()),
    fetchMolecularRevisionSummaries: mocks.summaries, fetchMolecularRevision: mocks.revision,
    submitJob: mocks.submit, api: {get: vi.fn(async () => ({data: schema}))},
}));
const records = ['a', 'b'].map(id => ({id, name: 'Same name', sequence_type: 'dna', length: 12, feature_count: 0})) as NucleotideSequenceListItem[];
const revision = (id: string, molecule = 'dna') => ({sequence_id: id, revision_id: `${id}-r1`, revision_number: 1,
    document_name: 'Same name', content_sha256: id.repeat(64),
    snapshot: {name: 'Same name', sequence: molecule === 'dna' ? 'ACGTACGTACGT' : 'ACGUACGUACGU', sequence_type: molecule, is_circular: true},
});
beforeEach(() => {
    vi.clearAllMocks();
    mocks.summaries.mockImplementation(async id => [{revision_id: `${id}-r1`, is_current: true}]);
    mocks.revision.mockImplementation(async id => revision(id));
    mocks.submit.mockResolvedValue({data: {id: 'fixture-job'}});
});
function Shelf({sequences = records}: {sequences?: NucleotideSequenceListItem[]}) {
    const navigate = useNavigate();
    return <SequenceLibrary sequences={sequences} demos={[]} demoLoading={false} selectedId={null}
        onSelect={mocks.opened} onRefresh={() => {}} onLoadDemo={() => {}} loading={false}
        width={280} projectScoped={false} showAllConstructs onToggleAllConstructs={() => {}}
        onAlign={selected => { void molecularAlignmentHref(selected).then(navigate); }} />;
}
async function mounted(path = '/shelf', sequences = records) {
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host), client = new QueryClient({defaultOptions: {queries: {retry: false}}});
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes>
        <Route path="/shelf" element={<Shelf sequences={sequences}/>}/><Route path="/alignments/new" element={<AlignmentPage/>}/>
    </Routes></MemoryRouter></QueryClientProvider>));
    return {host, close: async () => { await act(() => root.unmount()); client.clear(); host.remove(); }};
}
const button = (host: Element, text: string) => [...host.querySelectorAll('button')].find(node => node.textContent === text)!;

it('selects shelf checkboxes without opening records, then submits both exact saved revisions through the existing editor', async () => {
    const {host, close} = await mounted();
    try {
        expect(button(host, 'Align selected (0)').disabled).toBe(true);
        for (const checkbox of host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')) await act(() => checkbox.click());
        expect(mocks.opened).not.toHaveBeenCalled();
        await act(async () => button(host, 'Align selected (2)').click());
        expect(mocks.summaries).toHaveBeenCalledTimes(2);
        expect(mocks.revision).toHaveBeenCalledWith('a', 'a-r1');
        expect(mocks.revision).toHaveBeenCalledWith('b', 'b-r1');
        expect(host.textContent).toContain('Selected sequences (2)');
        expect((host.querySelector('[aria-label="Molecule"]') as HTMLSelectElement).value).toBe('dna');
        expect(mocks.submit).not.toHaveBeenCalled();
        await act(async () => button(host, 'Start run').click());
        const request = mocks.submit.mock.calls[0][0];
        expect(request.model_id).toBe('family_msa_nucleic');
        expect(request.params.roster.rows.map((row: {header: string}) => row.header)).toEqual(['Same name', 'Same name']);
        expect(new Set(request.params.roster.rows.map((row: {row_id: string}) => row.row_id)).size).toBe(2);
        expect(request.params.roster.rows[0]).toMatchObject({sequence: 'ACGTACGTACGT', source_member_id: '["a","a-r1"]'});
        expect(request.params.roster.source_evidence).toEqual(expect.arrayContaining([expect.objectContaining({sequence_id: 'b', revision_id: 'b-r1', content_sha256: 'b'.repeat(64), topology: 'circular'})]));
    } finally { await close(); }
});

it('keeps pinned shelf revisions and RNA type in a reloadable URL without sequence data', async () => {
    const sequences = records.map(row => ({...row, sequence_type: 'rna' as const, revision_id: `${row.id}-r1`}));
    const href = await molecularAlignmentHref(sequences);
    expect(mocks.summaries).not.toHaveBeenCalled();
    expect(href).not.toContain('ACGU');
    mocks.revision.mockImplementation(async id => revision(id, 'rna'));
    const {host, close} = await mounted(href);
    try {
        expect((host.querySelector('[aria-label="Molecule"]') as HTMLSelectElement).value).toBe('rna');
        expect(host.textContent).toContain('Selected sequences (2)');
        await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Remove Same name from alignment"]')!.click());
        expect(host.textContent).toContain('Selected sequences (1)');
        await act(async () => button(host, 'Clear selected sequences').click());
        expect(host.textContent).not.toContain('Selected sequences');
    } finally { await close(); }
});

it('refuses mixed molecules and missing saved revisions instead of handing off a partial selection', async () => {
    await expect(molecularAlignmentHref([records[0], {...records[1], sequence_type: 'rna'}])).rejects.toThrow('DNA');
    mocks.summaries.mockResolvedValue([]);
    await expect(molecularAlignmentHref(records)).rejects.toThrow('No saved revision');
    expect(mocks.revision).not.toHaveBeenCalled();
});

it('blocks submission until all sources load, exposes retry, and rejects an identity mismatch', async () => {
    const href = await molecularAlignmentHref(records);
    let finish!: (value: unknown) => void;
    mocks.revision.mockImplementation(id => id === 'a' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(revision(id)));
    const {host, close} = await mounted(href);
    try {
        expect(button(host, 'Start run').disabled).toBe(true);
        await act(async () => finish({...revision('a'), revision_id: 'wrong'}));
        expect(host.textContent).toContain('does not match');
        expect(button(host, 'Start run').disabled).toBe(true);
        expect(mocks.submit).not.toHaveBeenCalled();
        mocks.revision.mockImplementation(async id => revision(id));
        await act(async () => button(host, 'Retry').click());
        expect(button(host, 'Start run').disabled).toBe(false);
    } finally { await close(); }
});
