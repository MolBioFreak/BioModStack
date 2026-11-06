import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { removeMolecularProjectMember, addMolecularProjectMember, exportMolecularProjectHistory, sequenceInventoryPolicy, type MolecularProjectHistory } from '../../src/lib/molecularProjectMembership';
import { ExportDropdown } from '../../src/components/MolBioToolkit/ExportDropdown';

afterEach(() => vi.restoreAllMocks());
const identity = 'sequence_id=sequence&revision_id=revision';
function reads(current: unknown = null, generation = 0) {
    return vi.spyOn(api, 'get').mockImplementation(async (url) => ({ data:
        String(url) === '/api/molbio-ngs/experiments/domain/state' ? { head_generation: generation, current_state_revision_id: current ? 'state' : null }
            : String(url).includes('/state/revisions/') ? current
            : { current_revision_id: 'domain-revision', head_generation: 7 },
    } as never));
}
function writes() {
    return vi.spyOn(api, 'post').mockImplementation(async (url, data) => ({ data:
        String(url).includes('member-receipts') ? { receipt_id: 'molecular-receipt' } : { id: 'next-state', ...(data as object) },
    } as never));
}

describe('native molecular Project export', () => {
    it('preserves the full policy and existing members when adding an exact saved revision', async () => {
        const payload = sequenceInventoryPolicy();
        payload.acquisition_policy.platform = 'ont';
        payload.analysis_policy.allowed_workflow_ids = ['ont_clone_validation'];
        payload.notes = 'Keep this operator policy exactly';
        reads({ payload, members: [{ receipt_id: 'existing', role: 'ngs_instrument_run', ordinal: 4, sample_revision_id: 'sample' }] }, 8);
        const post = writes();
        await addMolecularProjectMember('project', 'experiment', 'domain', identity, 'state');
        expect(post.mock.calls.at(-1)?.[1]).toMatchObject({ payload, expected_head_generation: 8, parent_revision_id: 'state',
            members: [{ receipt_id: 'existing', role: 'ngs_instrument_run', ordinal: 4, sample_revision_id: 'sample' }, { receipt_id: 'molecular-receipt', role: 'molecular_expected_construct', ordinal: 5, sample_revision_id: null }] });
    });
    it('advances an expected construct without duplicating the current plasmid card', async () => {
        reads({ payload: sequenceInventoryPolicy(), members: [{ receipt_id: 'old-receipt', role: 'molecular_expected_construct', ordinal: 0,
            sample_revision_id: null, entity_kind: 'molecular_revision', reopen_destination: { params: { sequence_id: 'sequence' } } }] }, 1);
        const post = writes();
        await addMolecularProjectMember('project', 'experiment', 'domain', identity);
        expect((post.mock.calls.at(-1)?.[1] as { members: unknown[] }).members).toEqual([{ receipt_id: 'molecular-receipt', role: 'molecular_expected_construct', ordinal: 0, sample_revision_id: null }]);
    });
    it('can add the first sequence to an empty acknowledged workspace', async () => {
        reads(); const post = writes();
        await addMolecularProjectMember('project', 'experiment', 'domain', identity);
        expect(post.mock.calls.at(-1)?.[1]).toMatchObject({ expected_head_generation: 0, parent_revision_id: null, payload: sequenceInventoryPolicy() });
    });
    it('rejects a stale selected state without advancing membership', async () => {
        reads({ payload: sequenceInventoryPolicy(), members: [] }, 2); const post = writes();
        await expect(addMolecularProjectMember('project', 'experiment', 'domain', identity, 'older')).rejects.toThrow('workspace advanced');
        expect(post.mock.calls.some(([url]) => String(url).endsWith('/state/revisions'))).toBe(false);
    });
    it('reuses a saved native member when retrying after a partial PM failure', async () => {
        const current = { id: 'already-saved', members: [{ receipt_id: 'molecular-receipt' }] };
        reads(current, 2); const post = writes();
        expect(await addMolecularProjectMember('project', 'experiment', 'domain', identity, 'older')).toEqual(current);
        expect(post).toHaveBeenCalledTimes(1);
    });
    it('offers Export to Project in the actual viewer Export menu', async () => {
        const host = document.createElement('div'); document.body.append(host); const root = createRoot(host); const choose = vi.fn();
        try {
            await act(() => root.render(<ExportDropdown sequenceData={{ name: 'Plasmid', sequence: 'ACGT' }} onExportToProject={choose} />));
            await act(() => host.querySelector('button')!.click());
            const option = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Export to Project…');
            expect(option).toBeTruthy();
            await act(() => option!.click());
            expect(choose).toHaveBeenCalledOnce();
        } finally { await act(() => root.unmount()); host.remove(); }
    });
    it('exports old revisions as history references and preserves the complete local journal', async () => {
        const history = { schema: 'bms.molbio.project-export-history.v1', sequence_id: 'sequence', selected_revision_id: 'revision', manifest_sha256: 'a'.repeat(64), name: 'Plasmid', complete: true, entries: [
            { kind: 'revision', id: 'old', adapter_id: 'bms.molbio.revision-reference.adapter.v1', entity_id: 'sequence_id=sequence&revision_id=old', content_sha256: 'b'.repeat(64), reopen_href: '/designer?molbio_revision_id=old' },
            { kind: 'revision', id: 'revision', adapter_id: 'bms.molbio.revision-reference.adapter.v1', entity_id: identity, content_sha256: 'c'.repeat(64), reopen_href: '/designer?molbio_revision_id=revision' },
        ] } as MolecularProjectHistory;
        vi.spyOn(api, 'get').mockResolvedValue({ data: { head_generation: 9 } });
        const post = vi.spyOn(api, 'post').mockImplementation(async (url, data) => ({ data: String(url).endsWith('/attach') ? {
            schema: 'bms.global.attachment-receipt.v1', attachment_receipt_id: 'attachment', project_id: 'project', global_experiment_id: 'experiment', domain_experiment_id: 'domain', adapter_id: 'bms.molbio.revision-reference.adapter.v1', adapter_version: 1, source_receipt_id: 'old-receipt', source_receipt: {}, lineage_edge_id: 'edge', operation: 'attach_reference', role: 'references', note: null, project_head_generation: 10, normalized_request_sha256: 'f'.repeat(64), attached_at: '2026-10-22T00:00:00Z',
        } : data } as never));
        await exportMolecularProjectHistory('project', 'experiment', 'domain', history, [{ timestamp: '2026-10-22', label: 'Annotate', summary: 'Added promoter feature' }], 'current-receipt');
        expect(post.mock.calls.filter(([url]) => String(url).endsWith('/attach'))).toHaveLength(2);
        const note = post.mock.calls.find(([url]) => String(url).endsWith('/records'))?.[1] as { body: string; source_receipt_ids: string[] };
        expect(note.body).toContain('Added promoter feature');
        expect(note.body).toContain('molbio_revision_id=old');
        expect(note.source_receipt_ids).toEqual(['current-receipt', 'old-receipt']);
        expect(post.mock.calls.some(([url]) => String(url).endsWith('/state/revisions'))).toBe(false);
    });
});

it('removes only the selected Project sequence membership and preserves policy, other members and evidence', async () => {
    const payload=sequenceInventoryPolicy();payload.notes='Keep policy';
    reads({payload,members:[
        {receipt_id:'remove',entity_kind:'molecular_revision',role:'molecular_expected_construct',ordinal:0,sample_revision_id:null,reopen_destination:{params:{sequence_id:'sequence'}}},
        {receipt_id:'keep',entity_kind:'molecular_revision',role:'molecular_expected_construct',ordinal:1,sample_revision_id:null,reopen_destination:{params:{sequence_id:'other'}}},
        {receipt_id:'evidence',entity_kind:'ngs_job',role:'ngs_analysis',ordinal:2,sample_revision_id:null},
    ]},9);
    const post=writes();await removeMolecularProjectMember('project','experiment','domain','sequence','state');
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toMatchObject({expected_head_generation:9,parent_revision_id:'state',payload,members:[{receipt_id:'keep'},{receipt_id:'evidence'}]});
});
it('rejects stale Project deletion before writing membership', async () => {
    reads({members:[]},9);const post=writes();
    await expect(removeMolecularProjectMember('project','experiment','domain','sequence','old')).rejects.toThrow('Project changed');expect(post).not.toHaveBeenCalled();
});
