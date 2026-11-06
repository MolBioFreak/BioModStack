import { expect, it } from 'vitest';
import { buildComplexaNativeSelection } from '../../src/lib/complexaNativeSelection';
import { buildRfd4NativeSelection } from '../../src/lib/rfd4NativeSelection';
const source = { documentId: '/exact', model_number: 2, source_positions: [{ model_number: 2, label_asym_id: 'X', label_seq_id: 7, auth_asym_id: 'A', auth_seq_id: 42, pdbx_PDB_ins_code: 'B', label_comp_id: 'GLY', atoms: [{ label_atom_id: 'CA', auth_atom_id: 'CA', model_atom_index: 0, label_alt_id: '.' }] }] };
const ref = { documentId: '/exact', modelNumber: 2, labelAsymId: 'X', labelSeqId: 7, authAsymId: 'A', authSeqId: 42, insertionCode: 'B', labelAtomId: 'CA' };
it('Complexa author atoms and RFD4 label expressions are distinct native projections', () => {
    expect(buildComplexaNativeSelection([ref], source)).toEqual({ atoms: [{ chain: 'A', residue: 42, insertion_code: 'B', atoms: ['CA'] }] });
    expect(buildRfd4NativeSelection([ref], source).expression).toBe('(chain_id == "X" and res_id == 7 and atom_name == "CA")');
});
it.each([{ documentId: '/other' }, { modelNumber: 1 }, { altLoc: 'A' }, { operatorInstanceId: 'copy' }, { authSeqId: 43 }])('unresolved identity remains an observation: %j', override => {
    expect(buildComplexaNativeSelection([{ ...ref, ...override }], source).issue).toBeTruthy();
    expect(buildComplexaNativeSelection([{ ...ref, ...override }], source).atoms).toBeUndefined();
});
it('multi-character native chains are never truncated', () => {
    const changed = { ...source, source_positions: [{ ...source.source_positions[0], auth_asym_id: 'AA' }] };
    expect(buildComplexaNativeSelection([{ ...ref, authAsymId: 'AA' }], changed).issue).toContain('not representable');
});
it('nonpolymer author residue atoms do not require a fabricated label residue', () => {
    const changed = { ...source, source_positions: [{ ...source.source_positions[0], label_seq_id: null }] };
    expect(buildComplexaNativeSelection([{ ...ref, labelSeqId: undefined }], changed).atoms?.[0].residue).toBe(42);
});
