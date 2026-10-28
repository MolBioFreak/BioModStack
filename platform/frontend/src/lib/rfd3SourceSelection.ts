import type { Rfd4NativeSelectionIdentity, Rfd4NativeSelectionSource } from './rfd4NativeSelection';

/** Original author selectors; the existing Python source-map owner lowers these. */
export type Rfd3SourceSelection = {
    identities: readonly Rfd4NativeSelectionIdentity[];
    source: Rfd4NativeSelectionSource;
    /** Must be the actual active source, not an ancestral inspection document. */
    inputPath: string;
    sourceMapping?: string;
};
export type Rfd3SelectionProjection = { residues?: string; atoms?: Record<string, string[]>; issue?: string };
const clean = (v: unknown) => v == null || v === '.' || v === '?' ? '' : String(v);
export function projectRfd3SourceSelection(visual: Rfd3SourceSelection): Rfd3SelectionProjection {
    const fail = (issue: string) => ({ issue });
    if (visual.sourceMapping !== 'bms.rfd3.source-map.v1') return fail('Visual adoption needs the existing source map. Historical mapping-off and manual selectors are unchanged.');
    if (visual.inputPath !== visual.source.documentId) return fail('Selection receipt does not belong to the active source.');
    const rows = visual.source.source_positions ?? [];
    const models = [...new Set(rows.map(r => Number(r.model_number)))];
    const model = visual.source.model_number ?? (models.length === 1 && (visual.source.model_numbers?.length ?? 1) === 1 ? models[0] : undefined);
    if (model === undefined || !Number.isSafeInteger(model)) return fail('Choose an exact source MODEL for visual adoption.');
    const atoms: Record<string, string[]> = {};
    for (const ref of visual.identities) {
        if (ref.documentId !== visual.source.documentId || (ref.modelNumber !== undefined && ref.modelNumber !== model) || (ref.modelId !== undefined && Number(ref.modelId) !== model)) return fail('Selection document/MODEL differs from the retained source.');
        if (ref.assemblyId || ref.operatorInstanceId || ref.sourceInstanceId || clean(ref.altLoc)) return fail('Copy/alternate-location scope cannot be expressed by this native selector; manual input remains available.');
        if (!((ref.labelAsymId !== undefined && ref.labelSeqId !== undefined) || (ref.authAsymId !== undefined && ref.authSeqId !== undefined))) return fail('Selection lacks a complete source residue identity.');
        const matches = rows.filter(r => Number(r.model_number) === model &&
            (ref.labelAsymId === undefined || r.label_asym_id === ref.labelAsymId) &&
            (ref.labelSeqId === undefined || Number(r.label_seq_id) === ref.labelSeqId) &&
            (ref.authAsymId === undefined || r.auth_asym_id === ref.authAsymId) &&
            (ref.authSeqId === undefined || Number(r.auth_seq_id) === ref.authSeqId) &&
            (ref.insertionCode === undefined || clean(r.pdbx_PDB_ins_code) === clean(ref.insertionCode)) &&
            (ref.componentId === undefined || r.label_comp_id === ref.componentId) &&
            (ref.entityId === undefined || r.label_entity_id === ref.entityId) &&
            (ref.sourceEntityId === undefined || r.label_entity_id === ref.sourceEntityId));
        if (matches.length !== 1) return fail('Selection does not resolve to one retained source residue.');
        const row = matches[0];
        const chain = clean(row.auth_asym_id), residue = clean(row.auth_seq_id), insertion = clean(row.pdbx_PDB_ins_code);
        // lower_selection matches exact author chain+residue+insertion tokens first.
        if (!chain || /[,\s]/.test(chain + insertion) || !/^-?\d+$/.test(residue)) return fail('Author identity requires manual native syntax.');
        const token = chain + residue + insertion;
        if (rows.some(r => Number(r.model_number) === model && clean(r.auth_asym_id) + clean(r.auth_seq_id) + clean(r.pdbx_PDB_ins_code) === token && r !== row)) return fail('Native author token would broaden this source identity.');
        const allAtoms = (Array.isArray(row.atoms) ? row.atoms : []) as Record<string, unknown>[];
        const atomScoped = ref.labelAtomId !== undefined || ref.authAtomId !== undefined || ref.atomIndex !== undefined;
        const selected = atomScoped ? allAtoms.filter(a =>
            (ref.labelAtomId === undefined || a.label_atom_id === ref.labelAtomId) &&
            (ref.authAtomId === undefined || a.auth_atom_id === ref.authAtomId) &&
            (ref.atomIndex === undefined || Number(a.model_atom_index) === ref.atomIndex) &&
            (ref.element === undefined || a.type_symbol === ref.element)) : allAtoms;
        if (!selected.length || (atomScoped && selected.length !== 1)) return fail('Atom identity is unavailable or ambiguous.');
        const names = selected.map(a => clean(a.auth_atom_id) || clean(a.label_atom_id));
        if (selected.some(a => clean(a.label_alt_id)) || names.some(name => !name || allAtoms.filter(a => (clean(a.auth_atom_id) || clean(a.label_atom_id)) === name).length !== 1)) return fail('Native atom names would broaden alternate locations.');
        atoms[token] = [...new Set([...(atoms[token] ?? []), ...names])];
    }
    return Object.keys(atoms).length ? { residues: Object.keys(atoms).join(','), atoms } : fail('Select source residues or atoms before Apply.');
}
