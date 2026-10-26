"""Private integrated receiving; scientific execution remains inert."""
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
from Bio.PDB import MMCIFIO
from sqlalchemy.ext.asyncio import async_sessionmaker
from database import Design, Job, get_session
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store
from test_protenix_chemistry_transport import compiler_environment
from services import nextflow


def bonded_cif():
    cols = {'data_': 'bonded'}
    rows = []
    for model in (2,):
        for chain in ('Y', 'Z'):
            for pos in (1, 2, 3):
                rows.append([str(len(rows)+1), 'ATOM', 'C', 'CA', '.', 'CYS', chain,
                    '1' if chain == 'Y' else '2', str(pos), '?', str(pos), '0', str(model),
                    '1', '20', str(pos), 'CYS', chain, 'CA', str(model)])
    keys = 'id group_PDB type_symbol label_atom_id label_alt_id label_comp_id label_asym_id label_entity_id label_seq_id pdbx_PDB_ins_code Cartn_x Cartn_y Cartn_z occupancy B_iso_or_equiv auth_seq_id auth_comp_id auth_asym_id auth_atom_id pdbx_PDB_model_num'.split()
    cols.update({'_atom_site.'+key: list(values) for key, values in zip(keys, zip(*rows))})
    cols.update({'_entity_poly.entity_id':['1','2'], '_entity_poly.type':['polypeptide(L)']*2})
    cols.update({'_struct_conn.id':['b1'], '_struct_conn.conn_type_id':['covale'],
        '_struct_conn.ptnr1_label_asym_id':['Y'], '_struct_conn.ptnr1_label_seq_id':['2'], '_struct_conn.ptnr1_label_atom_id':['CA'],
        '_struct_conn.ptnr2_label_asym_id':['Z'], '_struct_conn.ptnr2_label_seq_id':['2'], '_struct_conn.ptnr2_label_atom_id':['CA']})
    writer=MMCIFIO(); writer.set_dict(cols); out=io.StringIO(); writer.save(out)
    return out.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['esmfold2', 'protenix'])
@pytest.mark.parametrize('authority', ['omitted', 'empty', 'changed'])
async def test_registered_bonds_http_persisted_child_cold_reader(selected, setup_store, compiler_environment, model, authority):
    client, session, root, _, tmp = selected
    path=tmp/'registered.cif'; path.write_text(bonded_cif())
    design=await session.get(Design, 'd0'); design.pdb_path=str(path)
    design.provenance={**design.provenance, 'binder_chains':['Y','Z']}
    await session.commit()
    inherited=dict(left_entity=1,right_entity=2,left_copy=1,right_copy=1,left_position=2,right_position=2,left_atom='CA',right_atom='CA')
    changed=({**inherited, 'left_position':3, 'right_position':3} if model=='protenix' else
             dict(chain_id1='Y',chain_id2='Z',res_idx1=2,res_idx2=2,atom_idx1=1,atom_idx2=1))
    params={'esmf_use_msa' if model=='esmfold2' else 'protenix_use_msa':False}
    if authority!='omitted': params['covalent_bonds']=[] if authority=='empty' else [changed]
    ref={'kind':'design','job_id':'root','design_id':'d0'}
    payload={'operation':'round','source_job_id':'root','idempotency_key':f'{model}-{authority}',
        'selection':{'candidate_refs':[ref]},'execution_target_id':None,
        'round_plan':{'schema_version':2,'stages':[{'id':'p','kind':'prediction',
        'input':{'selected':True,'scope':'whole_composition'},'models':[{'invocation_id':'one','model_id':model,
        'mode':'predict' if model=='esmfold2' else 'complex','params':params}]}]}}
    from routers.user_templates import router as templates_router
    client._transport.app.include_router(templates_router, prefix='/api/user-templates')
    saved=await client.post('/api/user-templates',json={'name':'Selected chemistry','model_id':model,
        'mode':'predict' if model=='esmfold2' else 'complex','params':{'selected_round_request':payload}})
    assert saved.status_code in (200,201),saved.text
    template_url='/api/user-templates/'+saved.json()['id']
    saved=await client.put(template_url,json={'params':{'selected_round_request':payload}})
    assert saved.status_code==200,saved.text
    cold_template_sessions=async_sessionmaker(session.bind,expire_on_commit=False)
    async def template_core():
        async with cold_template_sessions() as cold: yield cold
    client._transport.app.dependency_overrides[get_session]=template_core
    fetched=await client.get(template_url)
    assert fetched.status_code==200,fetched.text
    restored=fetched.json()['params']['selected_round_request']
    assert restored==payload
    assert ('covalent_bonds' in restored['round_plan']['stages'][0]['models'][0]['params'])==(authority!='omitted')
    response=await client.post('/api/binder-continuation/selected',json=restored)
    assert response.status_code==201,response.text
    body=response.json()
    assert len(body['launched_jobs'])==1,body
    job_id=body['launched_jobs'][0]['id']
    expected=[inherited] if authority=='omitted' else ([] if authority=='empty' else [changed])
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    async with factory() as cold:
        child=await cold.get(Job,job_id)
        assert child.params['covalent_bonds']==expected
        assert child.params['source_design_id']=='d0'
        assert child.params['complex_components'][0]['sequence']=='CCC'
        assert not any(child.params.get(k) for k in ['input_pdb','templates','pocket','contact'])
        output=tmp/'compiled'; compiled=nextflow.compile_nextflow_invocation(model,child.mode,child.params,str(output))
        compiled.materialize_inputs(output)
        if model=='esmfold2': document=json.loads((output/'esmfold2_complex_components.json').read_text())
        else:
            from scripts.prepare_protenix_msa import load_native_protenix_input
            document=load_native_protenix_input(compiled.native_parameters)[0]
        assert document['covalent_bonds']==expected
    async def cold_dependency():
        async with factory() as cold: yield cold
    client._transport.app.dependency_overrides[get_session]=cold_dependency
    read=await client.get('/api/binder-continuation/root/round')
    assert read.status_code==200,read.text
    round_row=next(r for r in read.json()['selected_rounds'] if r['round_id']==body['round_id'])
    assert round_row['steps'][0]['source_ref']==ref
    replay=await client.post('/api/binder-continuation/selected',json=payload)
    assert replay.status_code==201 and replay.json()['launched_jobs'][0]['id']==job_id,replay.text
