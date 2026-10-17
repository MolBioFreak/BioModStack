import { expect, it, vi } from 'vitest';
import { continuationRoleDefaults, continuationHref, prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import { sourceResultReturn } from '../../src/lib/sourceResultReturn';
import { materializeExactStructure, type StructureMaterialization } from '../../src/lib/api';
vi.mock('../../src/lib/api', () => ({ materializeExactStructure: vi.fn() }));
const materialized = (roles?: Record<string, unknown>) => ({ path: 'exact.cif', format: 'cif', sha256: 'hash', native_path: 'exact.cif', native_format: 'cif', native_sha256: 'hash', source_path: 'exact.cif', model_numbers: [2], model_number: 2, author_residues: [], source_identity: { artifact_id: 'alternate', target_state: 'state2', ...(roles ? { producer_document: roles } : {}) } }) as StructureMaterialization;
it('receives only the selected complex roles and leaves identity untouched through PDB conversion', async () => {
    const native = materialized({ binder_chains: 'B', target_chains: 'A' });
    const source = { job_id: 'producer', document: { artifact_id: 'alternate', target_state: 'state2' }, model_number: 2 };
    native.source_structure = source;
    vi.mocked(materializeExactStructure).mockResolvedValueOnce(native).mockResolvedValueOnce({ ...native, path: 'derived.pdb', format: 'pdb', source_identity: {} });
    const result = await prepareDeNovoContinuation(source, 'redesign');
    expect(result).toMatchObject({ design_chains: 'B', context_chains: ['A'], source_structure: source, model_number: 2 });
    expect(result._source_native_prepared).toBe(native);
    expect(materializeExactStructure).toHaveBeenLastCalledWith({ path: 'exact.cif', expected_sha256: 'hash', output_format: 'pdb', model_number: 2 });
    expect(source.document).toEqual({ artifact_id: 'alternate', target_state: 'state2' });
});
it('initializes sequence roles and independent monomer roles without borrowing complex roles', () => {
    expect(continuationRoleDefaults(materialized({ binder_chains: 'B', target_chains: 'A' }), 'sequence')).toEqual({ binder_chains: 'B', target_chains: 'A', design_chain: 'B', target_chain: 'A' });
    expect(continuationRoleDefaults(materialized({ binder_chains: 'A', target_chains: '' }), 'redesign')).toEqual({ design_chains: 'A', context_chains: [] });
});
it('missing and malformed roles never become chain guesses; prediction receives no role changes', () => {
    const native = materialized();
    native.author_residues = [{ model_number: 1, auth_asym_id: 'A', auth_seq_id: 1, insertion_code: '', residue_name: 'ALA' }];
    native.source_identity.binder_chains = 'A';
    expect(continuationRoleDefaults(native, 'sequence')).toEqual({});
    expect(continuationRoleDefaults(native, 'redesign')).toEqual({});
    expect(continuationRoleDefaults(materialized({ binder_chains: [42], target_chains: null }), 'redesign')).toEqual({});
    expect(continuationRoleDefaults(materialized({ binder_chains: '' }), 'redesign')).toEqual({});
    expect(continuationRoleDefaults(materialized({ binder_chains: ['B', 'C'], target_chains: 'A' }), 'redesign')).toEqual({});
    for (const destination of ['redesign', 'sequence'] as const) {
        expect(continuationRoleDefaults(materialized({ target_chains: 'A' }), destination)).toEqual({});
    }
    expect(continuationRoleDefaults(materialized({ binder_chains: [42], target_chains: null }), 'sequence')).toEqual({});
    expect(continuationRoleDefaults(materialized({ binder_chains: 'B', target_chains: 'A' }), 'prediction')).toEqual({});
});
it('preserves explicit edits and clears without changing other scientific settings', () => {
    const draft = { design_chains: '', context_chains: [], temperature: 0, sequence: 'ACD', binder_chains: null, target_chain: '' };
    expect(continuationRoleDefaults(materialized({ binder_chains: 'B', target_chains: 'A' }), 'redesign', draft)).toEqual(draft);
    expect(continuationRoleDefaults(materialized({ binder_chains: 'B', target_chains: 'A' }), 'sequence', draft)).toMatchObject(draft);
});
it('cold handoff preserves exact candidate/document and PM query in the safe return', () => {
    const back = '/designs/producer?native_candidate=candidate1&native_page=3&artifact_id=alternate&target_state=state2&project_id=p&setup_context_id=s&launch_context_id=l#structure';
    window.history.replaceState({}, '', back);
    const source = { job_id: 'producer', document: { artifact_id: 'alternate', target_state: 'state2' } };
    const href = new URL(continuationHref(source, 'sequence'), window.location.origin);
    expect(href.searchParams.get('return_to')).toBe(back);
    expect(href.searchParams.get('setup_context_id')).toBe('s');
    expect(JSON.parse(href.searchParams.get('source_structure')!)).toEqual(source);
    expect(sourceResultReturn(href.searchParams.get('return_to'))).toBe(back);
    for (const bad of ['//evil/a', '/\\evil/a', 'https://evil/designs/a', '/designs/a\\x', '/designs/a\n']) expect(sourceResultReturn(bad)).toBeNull();
});
