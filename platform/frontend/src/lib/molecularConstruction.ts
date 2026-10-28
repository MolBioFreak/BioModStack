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
export interface MolecularGeometryRequest {
    schema_version?: 'bms.molecular-geometry-request.v1';
    operation: 'create_new_idealized_geometry'; provider: 'ambertools24_fd_helix';
    form: 'abdna' | 'adna' | 'arna'; topology: 'canonical_antiparallel_duplex';
    hydrogen_policy: 'heavy_atom_coordinates_graph_implicit_hydrogens'; construction: ConstructionRecipe;
}
export interface MolecularGeometryResult {
    schema_version: 'bms.molecular-geometry.v1'; request: MolecularGeometryRequest;
    graph: ConstructionGraph; format: 'pdb'; coordinate_text: string;
    coordinate_sha256: string; coordinate_size_bytes: number;
    atom_map: Array<{ graph_atom_id: string; pdb_serial: number; native_chain: string; native_residue_number: number; native_component: string; native_atom_name: string; x: number; y: number; z: number; [key: string]: unknown }>;
    provenance: Record<string, string>; observations: string[]; [key: string]: unknown;
}
export interface MolecularGeometryDraft {
    choices: Partial<Omit<MolecularGeometryRequest, 'construction'>> & Record<string, unknown>;
    request?: MolecularGeometryRequest | ConstructionEditor;
    result?: MolecularGeometryResult;
    [key: string]: unknown;
}
export const geometryApi = {
    schemas: async (): Promise<{ request: Schema; result: Schema }> => (await api.get('/api/molecular-geometry/schema')).data,
    construct: async (request: ConstructionEditor): Promise<MolecularGeometryResult> => (await api.post('/api/molecular-geometry/construct', request)).data,
};
export interface ConstructionSnapshot { editor: ConstructionEditor; graph?: ConstructionGraph; geometry?: MolecularGeometryDraft; [key: string]: unknown }
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
    const { undo, ...snapshot } = draft;
    const { graph: _graph, geometry, ...rest } = snapshot;
    const { result: _result, ...retainedGeometry } = geometry ?? { choices: {} };
    return { ...rest, ...(geometry ? { geometry: retainedGeometry } : {}), editor: structuredClone(editor), undo: [...undo, structuredClone(snapshot)] };
}
export function editGeometry(draft: MolecularConstructionDraft, choices: MolecularGeometryDraft['choices']): MolecularConstructionDraft {
    const next = editConstruction(draft, draft.editor);
    return { ...next, ...(draft.graph ? { graph: draft.graph } : {}), geometry: { ...next.geometry, choices: structuredClone(choices) } };
}
export function undoConstruction(draft: MolecularConstructionDraft): MolecularConstructionDraft {
    if (!draft.undo.length) return draft;
    const snapshot = draft.undo[draft.undo.length - 1];
    const { graph: _old, geometry: _geometry, ...rest } = draft;
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
