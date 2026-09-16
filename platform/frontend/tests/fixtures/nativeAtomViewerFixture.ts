import {fixture} from './scientificViewerFixture';

// Generated mixed-complex transport fixture; no biological sequence or retained result bytes.
export function nativeAtomFixture(count = 5) {
    const pae: any = fixture();
    const document = {...pae.document, sourceKind:'mmcif'};
    const base = pae.row_axis.residues[0];
    const token = (index: number) => {
        const chain = index === 0 ? 'A' : index === 1 ? 'B' : index === 2 ? 'C' : 'E';
        const ref = {...base,index,chain_id:chain,auth_asym_id:chain,label_asym_id:chain,auth_seq_id:1,label_seq_id:index < 2 ? 1 : null,
            residue_name:index === 0 ? 'ALA' : index === 1 ? 'DA' : index === 2 ? 'MN' : 'GTP',source_entity_id:chain,entity_instance_id:chain};
        return index < 2 ? ref : {...ref,label_atom_id:index === 2 ? 'MN' : `C${index-2}`,auth_atom_id:index === 2 ? 'MN' : `C${index-2}`,element:index === 2 ? 'Mn' : 'C'};
    };
    const tokens=Array.from({length:count},(_,index)=>token(index));
    const axis={...pae.row_axis,residues:tokens};
    Object.assign(pae,{document,row_axis:axis,column_axis:structuredClone(axis),native_shape:[count,count],native_row_positions:tokens.map(r=>r.index),native_column_positions:tokens.map(r=>r.index),sampled_row_indices:tokens.map(r=>r.index),sampled_column_indices:tokens.map(r=>r.index),size:count,pae_matrix:tokens.map((_,i)=>tokens.map((_,j)=>i+j/10))});
    const atoms=tokens.map((r,i)=>({...r,label_atom_id:r.label_atom_id ?? 'CA',auth_atom_id:r.auth_atom_id ?? 'CA',element:r.element ?? 'C'}));
    const confidence:any = {schema_name:pae.schema_name,schema_version:pae.schema_version,contract_revision:pae.contract_revision,design_id:pae.design_id,design_name:pae.design_name,metric:'atom_plddt',status:'ok',reason:null,document,artifact_sha256:pae.artifact_sha256,producer_binding:pae.producer_binding,axis:{...axis,residues:atoms},native_positions:atoms.map(r=>r.index),units:'fraction',values:atoms.map((_,i)=>i===3 ? 0.25 : 0.9)};
    return {document,pae,confidence};
}
export function unavailable(metric:string, reason='unsupported_model_native_spatial_metric') {
    const p:any=fixture();
    for(const key of ['document','artifact_sha256','producer_binding','row_axis','column_axis','native_shape','native_row_positions','native_column_positions','sampled_row_indices','sampled_column_indices','pae_matrix','size']) p[key]=null;
    return {...p,status:'unavailable',metric,reason};
}
