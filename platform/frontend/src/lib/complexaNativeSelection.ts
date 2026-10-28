import type { Rfd4NativeSelectionIdentity, Rfd4NativeSelectionSource } from './rfd4NativeSelection';
export interface ComplexaAtomSelection { chain: string; residue: number; insertion_code: string; atoms: string[] }
/** The pinned target parser uses comma-separated inclusive ranges and hotspot
 * chain+residue tokens; unlike motif preparation it cannot address insertions. */
export function complexaTargetSelection(atoms: readonly ComplexaAtomSelection[]): { input_spec?: string; target_hotspots?: string[]; issue?: string } {
    if (!atoms.length) return { issue: 'No residues selected for target adoption.' };
    if (atoms.some(a => !/^[A-Za-z]$/.test(a.chain) || !Number.isSafeInteger(a.residue) || a.residue < 0 || a.insertion_code))
        return { issue: 'Native target ranges/hotspots cannot represent this author insertion or chain identity. Manual native values remain unchanged.' };
    const tokens = [...new Set(atoms.map(a => `${a.chain}${a.residue}`))];
    return { input_spec: [...new Set(atoms.map(a => `${a.chain}${a.residue}-${a.residue}`))].join(', '), target_hotspots: tokens };
}
const text = (v: unknown) => typeof v === 'string' && !['.', '?'].includes(v) ? v : '';
const rows = (v: unknown): Record<string, any>[] => Array.isArray(v) ? v : [];
/** Complexa's source preparer consumes author PDB identities, NOT RFD4 label queries.
 * No truncation, chain reassignment, insertion removal or coordinate synthesis here.
 */
export function buildComplexaNativeSelection(identities: readonly Rfd4NativeSelectionIdentity[], source: Rfd4NativeSelectionSource): { atoms?: ComplexaAtomSelection[]; issue?: string } {
    const fail = (issue: string) => ({ issue });
    if (!identities.length) return fail('No visual identities selected.');
    const positions = rows(source.source_positions);
    const models = [...new Set(positions.map(p => Number(p.model_number)))];
    const model = source.model_number ?? (models.length === 1 ? models[0] : undefined);
    if (model === undefined) return fail('Choose an exact source MODEL before visual adoption. Manual native values are unchanged.');
    const result: ComplexaAtomSelection[] = [];
    for (const ref of identities) {
        if (ref.documentId !== source.documentId) return fail('Selection belongs to another source document.');
        if (ref.assemblyId || ref.operatorInstanceId || ref.sourceInstanceId || text(ref.altLoc)) return fail('Copy/alternate-location mapping is unresolved.');
        if (ref.modelNumber !== undefined && ref.modelNumber !== model || ref.modelId !== undefined && Number(ref.modelId) !== model) return fail('Selection MODEL differs from the source.');
        if (!(ref.authAsymId !== undefined && ref.authSeqId !== undefined) && !(ref.labelAsymId !== undefined && ref.labelSeqId !== undefined)) return fail('Selection lacks a complete residue identity.');
        const matched = positions.filter(p => Number(p.model_number) === model &&
            (ref.authAsymId === undefined || p.auth_asym_id === ref.authAsymId) && (ref.authSeqId === undefined || Number(p.auth_seq_id) === ref.authSeqId) &&
            (ref.labelAsymId === undefined || p.label_asym_id === ref.labelAsymId) && (ref.labelSeqId === undefined || Number(p.label_seq_id) === ref.labelSeqId) &&
            (ref.insertionCode === undefined || text(p.pdbx_PDB_ins_code) === text(ref.insertionCode)) &&
            (ref.entityId === undefined || p.label_entity_id === ref.entityId) && (ref.sourceEntityId === undefined || p.label_entity_id === ref.sourceEntityId) &&
            (ref.componentId === undefined || p.label_comp_id === ref.componentId));
        if (matched.length !== 1) return fail('Selection does not resolve to one retained residue.');
        const p = matched[0], chain = text(p.auth_asym_id), residue = Number(p.auth_seq_id), insertion_code = text(p.pdbx_PDB_ins_code);
        if (chain.length !== 1 || !Number.isSafeInteger(residue) || p.auth_seq_id == null || insertion_code.length > 1) return fail('Complexa author PDB identity is not representable; original identity is retained without truncation.');
        const selected = rows(p.atoms).filter(a => (ref.labelAtomId === undefined || a.label_atom_id === ref.labelAtomId) &&
            (ref.authAtomId === undefined || a.auth_atom_id === ref.authAtomId) && (ref.atomIndex === undefined || Number(a.model_atom_index) === ref.atomIndex) &&
            (ref.element === undefined || a.type_symbol === ref.element));
        if (!selected.length || selected.some(a => text(a.label_alt_id) || !text(a.auth_atom_id)) || new Set(selected.map(a => a.auth_atom_id)).size !== selected.length) return fail('Selected author atom names are absent or ambiguous.');
        const existing = result.find(r => r.chain === chain && r.residue === residue && r.insertion_code === insertion_code);
        const atoms = selected.map(a => String(a.auth_atom_id));
        if (existing) existing.atoms = [...new Set([...existing.atoms, ...atoms])]; else result.push({ chain, residue, insertion_code, atoms });
    }
    return { atoms: result };
}
