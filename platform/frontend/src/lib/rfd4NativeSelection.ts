import type { AtomRef } from '../structureViewer/contracts/structureIdentity';

/** Original-document viewer identity; modelNumber is a source model, not a scene index. */
export type Rfd4NativeSelectionIdentity = AtomRef & { readonly modelNumber?: number };
export interface Rfd4NativeSelectionSource {
    /** The original-qualified viewer document backed by this receipt (not a PDB derivative). */
    readonly documentId: string;
    readonly source_positions?: readonly Readonly<Record<string, unknown>>[];
    readonly model_number?: number | null;
    readonly model_numbers?: readonly number[];
}
export interface Rfd4NativeSelectionResult {
    readonly expression?: string;
    /** Observational only: leave manual native authoring and execution unchanged. */
    readonly issue?: string;
}

const text = (v: unknown): string | undefined => typeof v === 'string' && v !== '.' && v !== '?' && v !== '' ? v : undefined;
const integer = (v: unknown): number | undefined => {
    if (typeof v !== 'number' && !(typeof v === 'string' && /^-?\d+$/.test(v))) return undefined;
    const n = Number(v);
    return Number.isSafeInteger(n) ? n : undefined;
};
const records = (v: unknown): Readonly<Record<string, unknown>>[] => Array.isArray(v)
    ? v.filter((x): x is Readonly<Record<string, unknown>> => x !== null && typeof x === 'object' && !Array.isArray(x)) : [];
const issue = (message: string): Rfd4NativeSelectionResult => ({ issue: message });

/**
 * Build one source-local QueryExpression, never a contig or condition/slot assignment.
 * Pinned RFD4 AtomWorks io_utils.read() uses use_author_fields=False: chain_id,
 * res_id and atom_name are label_asym_id, label_seq_id and label_atom_id.
 * Author/insertion/entity identities qualify ledger lookup, never replace labels.
 * Copy/altloc scopes have no qualified native mapping here and remain observations.
 * No partial expression is returned if any selected identity is unresolved.
 */
export function buildRfd4NativeSelection(
    identities: readonly Rfd4NativeSelectionIdentity[],
    source: Rfd4NativeSelectionSource,
): Rfd4NativeSelectionResult {
    if (!identities.length) return issue('No visual identities selected.');
    const positions = records(source.source_positions);
    if (!positions.length) return issue('Retained source positions are unavailable.');
    const models = [...new Set(positions.map(p => integer(p.model_number)))];
    const model = source.model_number ?? (source.model_numbers?.length === 1 ? source.model_numbers[0] : models.length === 1 ? models[0] : undefined);
    if (model === undefined || !Number.isSafeInteger(model) ||
        (source.model_number == null && (models.length !== 1 || (source.model_numbers?.length ?? 0) > 1))) {
        return issue('A source-local model mapping is ambiguous; this query cannot distinguish models.');
    }
    const rows = positions.filter(p => integer(p.model_number) === model);
    const clauses: string[] = [];
    for (const ref of identities) {
        if (!source.documentId || ref.documentId !== source.documentId) return issue('Selection belongs to a different source document.');
        if (ref.assemblyId || ref.operatorInstanceId || ref.sourceInstanceId) return issue('Assembly/copy identity has no qualified native source mapping.');
        if (text(ref.altLoc)) return issue('Alternate-location identity has no qualified native source mapping.');
        if ((ref.modelNumber !== undefined && ref.modelNumber !== model) ||
            (ref.modelId !== undefined && integer(ref.modelId) !== model)) return issue('Viewer model identity does not match the retained source model.');
        const labelComplete = text(ref.labelAsymId) !== undefined && integer(ref.labelSeqId) !== undefined;
        const authorComplete = text(ref.authAsymId) !== undefined && integer(ref.authSeqId) !== undefined;
        if (!labelComplete && !authorComplete) return issue('Selection lacks a complete label or author residue identity.');
        const matches = rows.filter(p =>
            (ref.labelAsymId === undefined || p.label_asym_id === ref.labelAsymId) &&
            (ref.labelSeqId === undefined || integer(p.label_seq_id) === ref.labelSeqId) &&
            (ref.authAsymId === undefined || p.auth_asym_id === ref.authAsymId) &&
            (ref.authSeqId === undefined || integer(p.auth_seq_id) === ref.authSeqId) &&
            (ref.insertionCode === undefined || (text(p.pdbx_PDB_ins_code) ?? '') === (text(ref.insertionCode) ?? '')) &&
            (ref.entityId === undefined || p.label_entity_id === ref.entityId) &&
            (ref.sourceEntityId === undefined || p.label_entity_id === ref.sourceEntityId) &&
            (ref.componentId === undefined || p.label_comp_id === ref.componentId));
        if (matches.length !== 1) return issue('Selection does not resolve to exactly one retained source position.');
        const p = matches[0];
        const chain = text(p.label_asym_id);
        const residue = integer(p.label_seq_id);
        if (!chain || residue === undefined) return issue('Native label residue mapping is unavailable (including nonpolymer label_seq_id).');
        // A label-only native clause must not broaden a qualified author/insertion match.
        if (rows.filter(r => r.label_asym_id === chain && integer(r.label_seq_id) === residue).length !== 1) {
            return issue('Native label query would include multiple retained source positions.');
        }
        let clause = `chain_id == ${JSON.stringify(chain)} and res_id == ${residue}`;
        if (ref.labelAtomId !== undefined || ref.authAtomId !== undefined || ref.atomIndex !== undefined) {
            const atoms = records(p.atoms);
            const matches = atoms.filter(a =>
                (ref.labelAtomId === undefined || a.label_atom_id === ref.labelAtomId) &&
                (ref.authAtomId === undefined || a.auth_atom_id === ref.authAtomId) &&
                (ref.atomIndex === undefined || integer(a.model_atom_index) === ref.atomIndex) &&
                (ref.element === undefined || a.type_symbol === ref.element));
            if (matches.length !== 1 || !text(matches[0].label_atom_id)) return issue('Atom identity does not resolve to one retained label atom.');
            const atom = matches[0];
            if (text(atom.label_alt_id) || atoms.filter(a => a.label_atom_id === atom.label_atom_id).length !== 1) {
                return issue('Native atom-name query has ambiguous alternate locations.');
            }
            clause += ` and atom_name == ${JSON.stringify(atom.label_atom_id)}`;
        }
        clauses.push(`(${clause})`);
    }
    return { expression: [...new Set(clauses)].join(' or ') };
}
