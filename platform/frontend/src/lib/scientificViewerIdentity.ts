import { assessResidueRef, canonicalResidueRefKey, type ResidueRef, type StructureDocumentRef } from '../structureViewer/contracts/structureIdentity';

export type ScientificPae = { status: 'unavailable'; reason: string } | {
    status: 'ok'; reason: null; rows: ResidueRef[]; columns: ResidueRef[];
    matrix: number[][]; artifactSha256: string; document: StructureDocumentRef;
};
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

function axis(value: unknown, expected: StructureDocumentRef, binding: Record<string, unknown>, count: number): ResidueRef[] {
    const a = exact(value, axisKeys);
    if (a.candidate_id !== binding.candidate_id || a.document_id !== binding.document_id || hash(a.source_sha256) !== expected.contentSha256) throw Error('axis source mismatch');
    if (!Array.isArray(a.residues) || a.residues.length !== count) throw Error('native axis dimension mismatch');
    const keys = new Set<string>();
    const positions = new Set<number>();
    return a.residues.map((raw) => {
        const r = exact(raw, residueKeys);
        const position = integer(r.index);
        if (position < 0 || position >= count || positions.has(position) || integer(r.selected_model) < 1) throw Error('native axis index mismatch');
        positions.add(position);
        text(r.chain_id);
        if (typeof r.insertion_code !== 'string' || typeof r.selected_altloc !== 'string') throw Error('missing insertion/altloc state');
        const ref: ResidueRef = {
            documentId: expected.documentId, modelId: String(r.selected_model),
            ...(r.source_entity_id === null ? {} : {sourceEntityId: text(r.source_entity_id)}),
            ...(r.entity_instance_id === null ? {} : {sourceInstanceId: text(r.entity_instance_id)}),
            ...(r.auth_asym_id === null ? {} : {authAsymId: text(r.auth_asym_id)}),
            ...(r.auth_seq_id === null ? {} : {authSeqId: integer(r.auth_seq_id)}),
            ...(r.label_asym_id === null ? {} : {labelAsymId: text(r.label_asym_id)}),
            ...(r.label_seq_id === null ? {} : {labelSeqId: integer(r.label_seq_id)}),
            insertionCode: r.insertion_code, componentId: text(r.residue_name), altLoc: r.selected_altloc,
        };
        if (assessResidueRef(ref).status !== 'ok') throw Error('incomplete residue namespace');
        if (r.chain_id !== (ref.authAsymId ?? ref.labelAsymId)) throw Error('contradictory chain identity');
        const key = canonicalResidueRefKey(ref);
        if (keys.has(key)) throw Error('duplicate native residue identity');
        keys.add(key);
        return ref;
    });
}
function sampled(value: unknown, count: number): number[] {
    if (!Array.isArray(value) || !value.length) throw Error('missing sampled indexes');
    let previous = -1;
    return value.map(v => { const n = integer(v); if (n <= previous || n >= count) throw Error('invalid sampled indexes'); previous = n; return n; });
}

export type ScientificNativeMetric = { status: 'unavailable'; reason: string } | {
    status: 'ok'; reason: null; metric: 'residue_plddt' | 'chain_metrics';
    document: StructureDocumentRef; artifactSha256: string; residues: ResidueRef[];
    values: number[]; chains: { providerIndex: string; chainId: string; residues: ResidueRef[]; ptm: number }[];
    pairChainsIptm: Record<string, Record<string, number>>;
};
const nativeBaseKeys = ['schema_name','schema_version','contract_revision','design_id','design_name','metric','status','reason','document','artifact_sha256','producer_binding','axis','native_positions'];
const fraction = (v: unknown): number => {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1) throw Error('invalid native fraction');
    return v;
};
/** Native arrays remain native fractions. Only a labeled display may use percent. */
export function parseScientificNativeMetric(raw: unknown, expected: StructureDocumentRef | null | undefined, metric: 'residue_plddt' | 'chain_metrics'): ScientificNativeMetric {
    try {
        const initial = record(raw);
        const p = exact(raw, initial.status === 'unavailable' ? envelopeKeys : [...nativeBaseKeys,
            ...(metric === 'residue_plddt' ? ['units','values'] : ['chain_index_map','chains_ptm','pair_chains_iptm','role_assignment','role_reason'])]);
        if (p.schema_name !== 'core_protein_viewer_metric' || p.schema_version !== 1 || p.contract_revision !== 1 || p.metric !== metric) throw Error('unsupported native metric schema');
        text(p.design_id); text(p.design_name);
        if (!expected?.candidateId || p.design_id !== expected.candidateId) throw Error('selected candidate mismatch');
        if (p.status === 'unavailable') {
            if (nullableFields.some(k => p[k] !== null)) throw Error('contradictory unavailable metric');
            return {status:'unavailable',reason:text(p.reason)};
        }
        if (p.status !== 'ok' || p.reason !== null) throw Error('invalid status');
        const doc=exact(p.document,['documentId','candidateId','contentSha256','sourceKind']);
        if (doc.documentId !== expected.documentId || doc.candidateId !== expected.candidateId || hash(doc.contentSha256) !== hash(expected.contentSha256) || doc.sourceKind !== expected.sourceKind) throw Error('selected structure mismatch');
        const binding=exact(p.producer_binding,['candidate_id','document_id']);
        text(binding.candidate_id);text(binding.document_id);
        const rawAxis=record(p.axis);
        if (!Array.isArray(rawAxis.residues) || !rawAxis.residues.length) throw Error('empty axis');
        const rawResidues = rawAxis.residues;
        const residues=axis(p.axis,expected,binding,rawResidues.length);
        if (!Array.isArray(p.native_positions) || p.native_positions.length !== residues.length || p.native_positions.some((n,i)=>integer(n)!==record(rawResidues[i]).index)) throw Error('native position mismatch');
        const base={status:'ok' as const,reason:null,metric,document:expected,artifactSha256:hash(p.artifact_sha256),residues};
        if (metric === 'residue_plddt') {
            if(p.units !== 'fraction' || !Array.isArray(p.values) || p.values.length !== residues.length) throw Error('native vector dimension mismatch');
            p.values.forEach(fraction);
            return {...base,values:p.values as number[],chains:[],pairChainsIptm:{}};
        }
        if(p.role_assignment !== null || p.role_reason !== 'missing_role_assignment' || !Array.isArray(p.chain_index_map) || !p.chain_index_map.length) throw Error('invalid chain ledger or role');
        const ids=new Set<string>(), names=new Set<string>();
        const maps=p.chain_index_map.map(rawChain=>{
            const c=exact(rawChain,['native_asym_id','source_chain_index','output_asym_id','chain_id','native_entity_id','native_sym_id']);
            for(const k of ['native_asym_id','source_chain_index','output_asym_id','native_entity_id','native_sym_id']) if(integer(c[k])<0)throw Error('invalid provider index');
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
export function parseScientificPae(raw: unknown, expected: StructureDocumentRef | null | undefined): ScientificPae {
    try {
        const p = exact(raw, envelopeKeys);
        if (p.schema_name !== 'core_protein_viewer_metric' || p.schema_version !== 1 || p.contract_revision !== 1 || p.metric !== 'pae') throw Error('unsupported metric schema');
        text(p.design_id); text(p.design_name);
        if (expected?.candidateId && p.design_id !== expected.candidateId) throw Error('candidate mismatch');
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
        const rowAxis = axis(p.row_axis, expected, binding, nr), columnAxis = axis(p.column_axis, expected, binding, nc);
        const rawRows = record(p.row_axis).residues as Record<string, unknown>[];
        const rawColumns = record(p.column_axis).residues as Record<string, unknown>[];
        if (!Array.isArray(p.native_row_positions) || !Array.isArray(p.native_column_positions)
            || p.native_row_positions.length !== nr || p.native_column_positions.length !== nc
            || p.native_row_positions.some((n, i) => integer(n) !== rawRows[i].index)
            || p.native_column_positions.some((n, i) => integer(n) !== rawColumns[i].index)) throw Error('native position ledger mismatch');
        const bySourceIndex = new Map(rawRows.map((r, i) => [r.index, canonicalResidueRefKey(rowAxis[i])]));
        if (nr !== nc || rawColumns.some((r, i) => bySourceIndex.get(r.index) !== canonicalResidueRefKey(columnAxis[i]))) throw Error('contradictory axes');
        const ri = sampled(p.sampled_row_indices, nr), ci = sampled(p.sampled_column_indices, nc);
        if (integer(p.size) !== ri.length || !Array.isArray(p.pae_matrix) || p.pae_matrix.length !== ri.length) throw Error('sampled matrix mismatch');
        for (const row of p.pae_matrix) {
            if (!Array.isArray(row) || row.length !== ci.length || row.some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0)) throw Error('invalid matrix value or dimension');
        }
        return {status: 'ok', reason: null, rows: ri.map(i => rowAxis[i]), columns: ci.map(i => columnAxis[i]),
            matrix: p.pae_matrix as number[][], artifactSha256: hash(p.artifact_sha256), document: expected};
    } catch (error) {
        return {status: 'unavailable', reason: `PAE identity unavailable: ${error instanceof Error ? error.message : 'invalid payload'}`};
    }
}
