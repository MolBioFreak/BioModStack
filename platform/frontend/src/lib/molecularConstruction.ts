import axios from 'axios';
import { api } from './api';
import type { BioXpOperatorJsonSchema as Schema } from './bioxpClient';

export type ConstructionEditor = Record<string, unknown>;
export interface ConstructionStrand {
    id: string; polymer: 'DNA' | 'RNA'; copy_ids: string[];
    residues: Array<{ id: string; base: 'A' | 'C' | 'G' | 'T' | 'U' }>;
    five_prime: 'hydroxyl' | 'phosphate_neutral'; three_prime: 'hydroxyl' | 'phosphate_neutral';
    nicks?: Array<{ after: string; left_three_prime: string; right_five_prime: string }>;
}
export interface ResidueEndpoint { strand_id: string; copy_id: string; residue_id: string }
export interface AtomEndpoint extends ResidueEndpoint { atom_name: string }
export interface ConstructionRecipe {
    schema_version?: 'bms.molecular-construction.v1';
    graph_provider: 'rdkit'; phosphate_state: 'neutral_acid'; geometry: 'unpositioned';
    strands: ConstructionStrand[];
    pairing?: Array<{ id: string; orientation: 'antiparallel' | 'parallel' | 'unspecified'; pairs: Array<{ first: ResidueEndpoint; second: ResidueEndpoint; relation: 'canonical' | 'mismatch' | 'unspecified' }> }>;
    replacements?: Array<{ id: string; kind: 'base' | 'sugar' | 'linkage' | 'terminal'; strand_id: string; copy_id: string; residue_ids: string[]; ccd_id: string; omit_atoms?: string[]; atom_map: Record<string, { residue_id: string; atom_name: string }> }>;
    covalent_links?: Array<{ first: AtomEndpoint; second: AtomEndpoint; order: 'single' | 'double' | 'triple' }>;
}
export interface ConstructionComplementRequest {
    source: ConstructionStrand; new_strand_id: string; new_copy_ids: string[]; new_residue_ids: string[];
    five_prime: ConstructionStrand['five_prime']; three_prime: ConstructionStrand['three_prime'];
}
export interface ConstructionGraph {
    schema_version: 'bms.molecular-graph.v1'; recipe: ConstructionRecipe;
    geometry: 'unpositioned'; coordinates: null;
    atoms: Array<{ id: string; strand_id: string; copy_id: string; residue_id: string; atom_name: string; native_atom_name: string; component_id: string; element: string; formal_charge: number; chiral_tag: string; cip: string | null; hydrogen_count: number; [key: string]: unknown }>;
    bonds: Array<{ first: string; second: string; order: number; aromatic: boolean; stereo: string }>;
    connected_components: string[][]; isomeric_smiles: string;
    provenance: Record<string, string>; observations: string[];
    [key: string]: unknown;
}
export interface ConstructionSnapshot { editor: ConstructionEditor; graph?: ConstructionGraph; [key: string]: unknown }
/** Durable authoring companion only. Never merge this into scientific params or CIF sources. */
export interface MolecularConstructionDraft extends ConstructionSnapshot {
    schema_version: 'bms.molecular-construction-editor.v1';
    undo: ConstructionSnapshot[];
    complement: ConstructionEditor;
}
export interface ConstructionSchemas { recipe: Schema; complement: Schema; result: Schema }
export const constructionApi = {
    schemas: async (): Promise<ConstructionSchemas> => (await api.get('/api/molecular-construction/schema')).data,
    graph: async (recipe: ConstructionRecipe | ConstructionEditor): Promise<ConstructionGraph> => (await api.post('/api/molecular-construction/graph', recipe)).data,
    complement: async (request: ConstructionComplementRequest | ConstructionEditor): Promise<ConstructionStrand> => (await api.post('/api/molecular-construction/complement', request)).data,
};
export function constructionError(error: unknown): string {
    if (axios.isAxiosError(error) && error.response?.data?.detail !== undefined) {
        const detail: unknown = error.response.data.detail;
        return typeof detail === 'string' ? detail : JSON.stringify(detail);
    }
    return String(error);
}
export function emptyConstructionDraft(): MolecularConstructionDraft {
    return { schema_version: 'bms.molecular-construction-editor.v1', editor: { geometry: 'unpositioned', strands: [] }, complement: {}, undo: [] };
}
export function editConstruction(draft: MolecularConstructionDraft, editor: ConstructionEditor): MolecularConstructionDraft {
    const { editor: prior, graph, undo, ...rest } = draft;
    return { ...rest, editor: structuredClone(editor), undo: [...undo, { editor: structuredClone(prior), ...(graph ? { graph: structuredClone(graph) } : {}) }] };
}
export function undoConstruction(draft: MolecularConstructionDraft): MolecularConstructionDraft {
    if (!draft.undo.length) return draft;
    const snapshot = draft.undo[draft.undo.length - 1];
    const { graph: _old, ...rest } = draft;
    return { ...rest, ...structuredClone(snapshot), undo: draft.undo.slice(0, -1) };
}
export function schemaDefinition(root: Schema, name: string): Schema {
    return { $ref: `#/$defs/${name}`, $defs: root.$defs };
}
export function schemaEnums(root: Schema, definition: string | null, property: string): unknown[] {
    const owner = definition ? (root.$defs as Record<string, Schema> | undefined)?.[definition] : root;
    const field = owner?.properties?.[property];
    return field?.enum ?? (field && Object.hasOwn(field, 'const') ? [field.const] : []);
}
