import { assessResidueRef, canonicalSpatialRefKey, type AtomRef, type StructureDocumentRef } from '../structureViewer/contracts/structureIdentity';

export type NativePaeToken = { index: number; residue_index: number | null; entity_id: number | null };
export type NativePaeTokenAxis = {
    candidate_id: string; document_id: string; source_sha256: string;
    axis_kind: 'model_token'; producer_version: string;
    mapping_reason: 'native_token_to_structure_mapping_unavailable'; orientation: 'native_output_order';
    tokens: NativePaeToken[];
};
type PaeBase = { status: 'ok'; reason: null; matrix: number[][]; artifactSha256: string; document: StructureDocumentRef };
export type ScientificPae = { status: 'unavailable'; reason: string } | (PaeBase & (
    { axisKind: 'structure'; rows: AtomRef[]; columns: AtomRef[] } |
    { axisKind: 'model_token';
      // No molecular identities: existing spatial consumers must never project these tokens.
      rows: []; columns: []; rowTokens: NativePaeToken[]; columnTokens: NativePaeToken[];
      rowAxis: NativePaeTokenAxis; columnAxis: NativePaeTokenAxis;
      nativeRowPositions: number[]; nativeColumnPositions: number[];
      sampledRowIndices: number[]; sampledColumnIndices: number[]; nativeShape: [number, number] }
));
const record = (v: unknown): Record<string, unknown> => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw Error('invalid object');
    return v as Record<string, unknown>;
};
const exact = (v: unknown, keys: string[]): Record<string, unknown> => {
    const r = record(v);
    if (Object.keys(r).length !== keys.length || keys.some(k => !Object.hasOwn(r, k))) throw Error('unknown or missing fields');
    return r;
};
const text = (v: unknown): string => { if (typeof v !== 'string' || !v.trim()) throw Error('invalid identity string'); return v; };
const integer = (v: unknown): number => { if (typeof v !== 'number' || !Number.isSafeInteger(v)) throw Error('invalid integer'); return v; };
const hash = (v: unknown): string => { if (typeof v !== 'string' || !/^[a-f0-9]{64}$/.test(v)) throw Error('invalid hash'); return v; };
const axisKeys = ['candidate_id','document_id','source_sha256','residues'];
const residueKeys = ['index','chain_id','residue_name','insertion_code','selected_model','selected_altloc','auth_asym_id','auth_seq_id','label_asym_id','label_seq_id','source_entity_id','entity_instance_id'];
const envelopeKeys = ['schema_name','schema_version','contract_revision','design_id','design_name','metric','status','reason','document','artifact_sha256','producer_binding','row_axis','column_axis','native_shape','native_row_positions','native_column_positions','sampled_row_indices','sampled_column_indices','pae_matrix','size'];
const nullableFields = ['document','artifact_sha256','producer_binding','row_axis','column_axis','native_shape','native_row_positions','native_column_positions','sampled_row_indices','sampled_column_indices','pae_matrix','size'];

function axis(value: unknown, expected: StructureDocumentRef, binding: Record<string, unknown>, count: number, cifConfidence = false): AtomRef[] {
    const a = exact(value, [...axisKeys, ...(cifConfidence ? ['producer_version','confidence_scope','stored_units'] : [])]);
    if (cifConfidence && (expected.sourceKind !== 'mmcif' || a.confidence_scope !== 'collapsed_residue' || a.stored_units !== 'percent')) throw Error('invalid CIF confidence dialect');
    if (cifConfidence) text(a.producer_version);
    if (a.candidate_id !== binding.candidate_id || a.document_id !== binding.document_id || hash(a.source_sha256) !== expected.contentSha256) throw Error('axis source mismatch');
    if (!Array.isArray(a.residues) || a.residues.length !== count) throw Error('native axis dimension mismatch');
    const keys = new Set<string>();
    const positions = new Set<number>();
    return a.residues.map((raw) => {
        const atomKeys = ['label_atom_id','auth_atom_id','element'];
        const hasAtom = atomKeys.some(k => Object.hasOwn(record(raw), k));
        const r = exact(raw, [...residueKeys, ...(hasAtom ? atomKeys : [])]);
        if (hasAtom) atomKeys.forEach(k => text(r[k]));
        const position = integer(r.index);
        if (position < 0 || position >= count || positions.has(position) || integer(r.selected_model) < 1) throw Error('native axis index mismatch');
        positions.add(position);
        text(r.chain_id);
        if (typeof r.insertion_code !== 'string' || typeof r.selected_altloc !== 'string') throw Error('missing insertion/altloc state');
        const ref: AtomRef = {
            documentId: expected.documentId, modelId: String(r.selected_model),
            ...(r.source_entity_id === null ? {} : {sourceEntityId: text(r.source_entity_id)}),
            ...(r.entity_instance_id === null ? {} : {sourceInstanceId: text(r.entity_instance_id)}),
            ...(r.auth_asym_id === null ? {} : {authAsymId: text(r.auth_asym_id)}),
            ...(r.auth_seq_id === null ? {} : {authSeqId: integer(r.auth_seq_id)}),
            ...(r.label_asym_id === null ? {} : {labelAsymId: text(r.label_asym_id)}),
            ...(r.label_seq_id === null ? {} : {labelSeqId: integer(r.label_seq_id)}),
            ...Object.fromEntries([['label_atom_id','labelAtomId'],['auth_atom_id','authAtomId'],['element','element']].flatMap(([wire,key]) => r[wire] == null ? [] : [[key,text(r[wire])]])),
            insertionCode: r.insertion_code, componentId: text(r.residue_name), altLoc: r.selected_altloc,
        };
        if (assessResidueRef(ref).status !== 'ok') throw Error('incomplete residue namespace');
        if (r.chain_id !== (ref.authAsymId ?? ref.labelAsymId)) throw Error('contradictory chain identity');
        const key = canonicalSpatialRefKey(ref);
        if (keys.has(key)) throw Error('duplicate native residue identity');
        keys.add(key);
        return ref;
    });
}
function tokenAxis(value: unknown, expected: StructureDocumentRef, binding: Record<string, unknown>, count: number): NativePaeTokenAxis {
    const a = exact(value, ['candidate_id','document_id','source_sha256','axis_kind','producer_version','mapping_reason','orientation','tokens']);
    if (a.candidate_id !== binding.candidate_id || a.document_id !== binding.document_id || hash(a.source_sha256) !== expected.contentSha256) throw Error('axis source mismatch');
    if (a.axis_kind !== 'model_token' || a.mapping_reason !== 'native_token_to_structure_mapping_unavailable' || a.orientation !== 'native_output_order') throw Error('invalid token axis evidence');
    text(a.producer_version);
    if (!Array.isArray(a.tokens) || a.tokens.length !== count) throw Error('native axis dimension mismatch');
    const positions = new Set<number>();
    const tokens = a.tokens.map(raw => {
        const t = exact(raw, ['index','residue_index','entity_id']);
        const index = integer(t.index);
        if (index < 0 || index >= count || positions.has(index)) throw Error('native axis index mismatch');
        positions.add(index);
        return { index, residue_index: t.residue_index === null ? null : integer(t.residue_index), entity_id: t.entity_id === null ? null : integer(t.entity_id) };
    });
    return { candidate_id: text(a.candidate_id), document_id: text(a.document_id), source_sha256: hash(a.source_sha256),
        axis_kind: 'model_token', producer_version: text(a.producer_version), mapping_reason: a.mapping_reason,
        orientation: a.orientation, tokens };
}
function sampled(value: unknown, count: number): number[] {
    if (!Array.isArray(value) || !value.length) throw Error('missing sampled indexes');
    let previous = -1;
    return value.map(v => { const n = integer(v); if (n <= previous || n >= count) throw Error('invalid sampled indexes'); previous = n; return n; });
}

export type ScientificNativeMetric = { status: 'unavailable'; reason: string } | {
    status: 'ok'; reason: null; metric: 'residue_plddt' | 'atom_plddt' | 'token_plddt' | 'chain_metrics';
    document: StructureDocumentRef; artifactSha256: string; residues: AtomRef[];
    confidenceSource?: { producerVersion: string; scope: 'collapsed_residue'; storedUnits: 'percent' };
    values: number[]; chains: { providerIndex: string; chainId: string; residues: AtomRef[]; ptm: number }[];
    pairChainsIptm: Record<string, Record<string, number>>;
};
const nativeBaseKeys = ['schema_name','schema_version','contract_revision','design_id','design_name','metric','status','reason','document','artifact_sha256','producer_binding','axis','native_positions'];
const fraction = (v: unknown): number => {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1) throw Error('invalid native fraction');
    return v;
};
/** Native arrays remain native fractions. Only a labeled display may use percent. */
export function parseScientificNativeMetric(raw: unknown, expected: StructureDocumentRef | null | undefined, metric: 'residue_plddt' | 'chain_metrics', selectedCandidateId = expected?.candidateId): ScientificNativeMetric {
    try {
        if (raw == null) return {status:'unavailable',reason:'Native metric not loaded'};
        const initial = record(raw);
        const actualMetric: 'residue_plddt' | 'atom_plddt' | 'token_plddt' | 'chain_metrics' = metric === 'residue_plddt' && (initial.metric === 'atom_plddt' || initial.metric === 'token_plddt') ? initial.metric : metric;
        const p = exact(raw, initial.status === 'unavailable' ? envelopeKeys : [...nativeBaseKeys,
            ...(metric === 'residue_plddt' ? ['units','values'] : ['chain_index_map','chains_ptm','pair_chains_iptm','role_assignment','role_reason'])]);
        if (p.schema_name !== 'core_protein_viewer_metric' || p.schema_version !== 1 || p.contract_revision !== 1 || p.metric !== actualMetric) throw Error('unsupported native metric schema');
        text(p.design_id); text(p.design_name);
        if (!selectedCandidateId || p.design_id !== selectedCandidateId) throw Error('selected candidate mismatch');
        if (p.status === 'unavailable') {
            if (nullableFields.some(k => p[k] !== null)) throw Error('contradictory unavailable metric');
            return {status:'unavailable',reason:text(p.reason)};
        }
        if (p.status !== 'ok' || p.reason !== null) throw Error('invalid status');
        if (!expected?.candidateId) throw Error('missing selected structure identity');
        const doc=exact(p.document,['documentId','candidateId','contentSha256','sourceKind']);
        if (doc.documentId !== expected.documentId || doc.candidateId !== expected.candidateId || hash(doc.contentSha256) !== hash(expected.contentSha256) || doc.sourceKind !== expected.sourceKind) throw Error('selected structure mismatch');
        const binding=exact(p.producer_binding,['candidate_id','document_id']);
        text(binding.candidate_id);text(binding.document_id);
        const rawAxis=record(p.axis);
        if (!Array.isArray(rawAxis.residues) || !rawAxis.residues.length) throw Error('empty axis');
        const rawResidues = rawAxis.residues;
        const cifConfidence = ['producer_version','confidence_scope','stored_units'].some(k => Object.hasOwn(rawAxis,k));
        if (cifConfidence && actualMetric !== 'residue_plddt') throw Error('invalid collapsed confidence metric');
        const residues=axis(p.axis,expected,binding,rawResidues.length,cifConfidence);
        if (!Array.isArray(p.native_positions) || p.native_positions.length !== residues.length || p.native_positions.some((n,i)=>integer(n)!==record(rawResidues[i]).index)) throw Error('native position mismatch');
        const base={status:'ok' as const,reason:null,metric:actualMetric,document:expected,artifactSha256:hash(p.artifact_sha256),residues};
        if (metric === 'residue_plddt') {
            if(p.units !== 'fraction' || !Array.isArray(p.values) || p.values.length !== residues.length) throw Error('native vector dimension mismatch');
            p.values.forEach(fraction);
            if (actualMetric === 'atom_plddt' && residues.some(r => !r.labelAtomId && !r.authAtomId)) throw Error('missing native atom identity');
            if (actualMetric === 'residue_plddt' && residues.some(r => r.labelAtomId || r.authAtomId)) throw Error('atom confidence mislabeled as residue');
            return {...base,values:p.values as number[],chains:[],pairChainsIptm:{}, ...(cifConfidence ? {confidenceSource: {producerVersion: text(rawAxis.producer_version), scope: 'collapsed_residue' as const, storedUnits: 'percent' as const}} : {})};
        }
        if(p.role_assignment !== null || p.role_reason !== 'missing_role_assignment' || !Array.isArray(p.chain_index_map) || !p.chain_index_map.length) throw Error('invalid chain ledger or role');
        const ids=new Set<string>(), names=new Set<string>();
        const maps=p.chain_index_map.map(rawChain=>{
            const c=exact(rawChain,['native_asym_id','source_chain_index','output_asym_id','chain_id','native_entity_id','native_sym_id']);
            for(const k of ['native_asym_id','source_chain_index','native_entity_id','native_sym_id']) if(integer(c[k])<0)throw Error('invalid provider index');
            // Some native writers publish chain labels but no numeric output-asym identity.
            if(c.output_asym_id !== null && integer(c.output_asym_id)<0)throw Error('invalid output asym index');
            const providerIndex=String(c.native_asym_id),chainId=text(c.chain_id);
            if(ids.has(providerIndex)||names.has(chainId))throw Error('duplicate provider chain');
            ids.add(providerIndex);names.add(chainId);
            const members=residues.filter(r=>(r.authAsymId??r.labelAsymId)===chainId);
            if(!members.length || new Set(members.map(r=>JSON.stringify([r.modelId,r.sourceEntityId,r.sourceInstanceId,r.authAsymId,r.labelAsymId]))).size!==1)throw Error('ambiguous chain entity/model');
            return {providerIndex,chainId,residues:members};
        });
        if(residues.some(r=>!names.has((r.authAsymId??r.labelAsymId)!)))throw Error('incomplete chain map');
        const keys=[...ids],ptm=exact(p.chains_ptm,keys),pairs=exact(p.pair_chains_iptm,keys);
        const pairChainsIptm:Record<string,Record<string,number>>={};
        for(const id of keys){const row=exact(pairs[id],keys);pairChainsIptm[id]=Object.fromEntries(keys.map(k=>[k,fraction(row[k])]));}
        return {...base,values:[],chains:maps.map(c=>({...c,ptm:fraction(ptm[c.providerIndex])})),pairChainsIptm};
    } catch(error) {return {status:'unavailable',reason:`Native metric identity unavailable: ${error instanceof Error?error.message:'invalid payload'}`};}
}

/** One transport adapter; no scientific computation or inferred residue order. */
export function parseScientificPae(raw: unknown, expected: StructureDocumentRef | null | undefined, selectedCandidateId = expected?.candidateId): ScientificPae {
    try {
        if (raw == null) return {status:'unavailable',reason:'PAE not loaded'};
        const p = exact(raw, envelopeKeys);
        if (p.schema_name !== 'core_protein_viewer_metric' || p.schema_version !== 1 || p.contract_revision !== 1 || p.metric !== 'pae') throw Error('unsupported metric schema');
        text(p.design_id); text(p.design_name);
        if (selectedCandidateId && p.design_id !== selectedCandidateId) throw Error('candidate mismatch');
        if (p.status === 'unavailable') {
            if (nullableFields.some(k => p[k] !== null)) throw Error('contradictory unavailable metric');
            return {status: 'unavailable', reason: text(p.reason)};
        }
        if (p.status !== 'ok' || p.reason !== null) throw Error('invalid metric status');
        if (!expected?.candidateId || !expected.contentSha256) throw Error('missing selected structure identity');
        const doc = exact(p.document, ['documentId','candidateId','contentSha256','sourceKind']);
        if (doc.documentId !== expected.documentId || doc.candidateId !== expected.candidateId || hash(doc.contentSha256) !== hash(expected.contentSha256) || doc.sourceKind !== expected.sourceKind) throw Error('selected structure mismatch');
        if (!Array.isArray(p.native_shape) || p.native_shape.length !== 2) throw Error('invalid native dimensions');
        const [nr, nc] = p.native_shape.map(integer);
        if (nr < 1 || nc < 1) throw Error('empty native axis');
        const binding = exact(p.producer_binding, ['candidate_id','document_id']);
        text(binding.candidate_id); text(binding.document_id);
        const isToken = record(p.row_axis).axis_kind === 'model_token';
        if (isToken !== (record(p.column_axis).axis_kind === 'model_token')) throw Error('contradictory axis types');
        const rowTokenAxis = isToken ? tokenAxis(p.row_axis, expected, binding, nr) : null;
        const columnTokenAxis = isToken ? tokenAxis(p.column_axis, expected, binding, nc) : null;
        const rowAxis = isToken ? [] : axis(p.row_axis, expected, binding, nr), columnAxis = isToken ? [] : axis(p.column_axis, expected, binding, nc);
        const rawRows = (isToken ? rowTokenAxis!.tokens : record(p.row_axis).residues) as Record<string, unknown>[];
        const rawColumns = (isToken ? columnTokenAxis!.tokens : record(p.column_axis).residues) as Record<string, unknown>[];
        if (!Array.isArray(p.native_row_positions) || !Array.isArray(p.native_column_positions)
            || p.native_row_positions.length !== nr || p.native_column_positions.length !== nc
            || p.native_row_positions.some((n, i) => integer(n) !== rawRows[i].index)
            || p.native_column_positions.some((n, i) => integer(n) !== rawColumns[i].index)) throw Error('native position ledger mismatch');
        const rowKey = (i: number) => isToken ? JSON.stringify(rowTokenAxis!.tokens[i]) : canonicalSpatialRefKey(rowAxis[i]);
        const columnKey = (i: number) => isToken ? JSON.stringify(columnTokenAxis!.tokens[i]) : canonicalSpatialRefKey(columnAxis[i]);
        const bySourceIndex = new Map(rawRows.map((r, i) => [r.index, rowKey(i)]));
        if (nr !== nc || rawColumns.some((r, i) => bySourceIndex.get(r.index) !== columnKey(i))) throw Error('contradictory axes');
        const ri = sampled(p.sampled_row_indices, nr), ci = sampled(p.sampled_column_indices, nc);
        if (integer(p.size) !== ri.length || !Array.isArray(p.pae_matrix) || p.pae_matrix.length !== ri.length) throw Error('sampled matrix mismatch');
        for (const row of p.pae_matrix) {
            if (!Array.isArray(row) || row.length !== ci.length || row.some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0)) throw Error('invalid matrix value or dimension');
        }
        const base: PaeBase = {status: 'ok', reason: null, matrix: p.pae_matrix as number[][], artifactSha256: hash(p.artifact_sha256), document: expected};
        if (rowTokenAxis && columnTokenAxis) return {...base, axisKind: 'model_token', rows: [], columns: [],
            rowAxis: rowTokenAxis, columnAxis: columnTokenAxis,
            rowTokens: ri.map(i => rowTokenAxis.tokens[i]), columnTokens: ci.map(i => columnTokenAxis.tokens[i]),
            nativeRowPositions: rawRows.map(r => integer(r.index)), nativeColumnPositions: rawColumns.map(r => integer(r.index)),
            sampledRowIndices: ri, sampledColumnIndices: ci, nativeShape: [nr,nc]};
        return {...base, axisKind: 'structure', rows: ri.map(i => rowAxis[i]), columns: ci.map(i => columnAxis[i])};
    } catch (error) {
        return {status: 'unavailable', reason: `PAE identity unavailable: ${error instanceof Error ? error.message : 'invalid payload'}`};
    }
}
