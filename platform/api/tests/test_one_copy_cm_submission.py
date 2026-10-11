"""Both real submission writers omit managed checkpoint bytes, private DB only."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job, ConformationalMappingSource
from routers import conformational_mapping as cm
from services.conformational_mapping import global_adapter
from tests.test_conformational_mapping_routing import _request_params


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('writer', ['operator', 'global'])
@pytest.mark.parametrize('alias', ['regular', 'symlink', 'hardlink'])
async def test_submission_stages_science_not_checkpoint(tmp_path, monkeypatch, writer, alias):
    results = tmp_path/'results'; results.mkdir()
    weights = tmp_path/'weights'; (weights/'openfold3').mkdir(parents=True)
    checkpoint = weights/'openfold3/of3-p2-155k.pt'
    payload = bytes(range(256))*256
    checkpoint.write_bytes(payload)
    checkpoint.chmod(0o444)
    if alias != 'regular':
        donor = weights/'retained-version.pt'
        checkpoint.rename(donor)
        if alias == 'symlink':
            checkpoint.symlink_to(donor)
        else:
            os.link(donor, checkpoint)
    inputs = tmp_path/'inputs'; inputs.mkdir()
    controls = _request_params('confornets')
    sequence = controls['confornets']['sequence']
    (inputs/'sequence.txt').write_text(sequence)
    source_id = 'cm_src_server_confornets_checkpoint_'+sha(payload)[:32]
    monkeypatch.setenv('BMS_CM_AUTHORIZATION_ENABLED','0')
    monkeypatch.setattr(cm,'get_results_dir',lambda:results)
    monkeypatch.setattr(global_adapter,'get_results_dir',lambda:results)
    monkeypatch.setattr(cm,'get_weights_root',lambda:weights)
    identity = controls['confornets']['backend_identity']
    monkeypatch.setattr(cm,'_server_confornets_identity',lambda:dict(identity))
    monkeypatch.setattr(global_adapter,'_server_confornets_identity',lambda:dict(identity))
    monkeypatch.setattr(cm,'_runtime_registry',lambda _:dict(identity))
    monkeypatch.setattr(global_adapter,'_runtime_registry',lambda _:dict(identity))
    staged = []
    owner = cm if writer == 'operator' else global_adapter
    original_stager = owner.stage_registered_assets
    def stage(artifacts, **kwargs):
        staged.extend(item.artifact_id for item in artifacts)
        return original_stager(artifacts, **kwargs)
    monkeypatch.setattr(owner,'stage_registered_assets',stage)
    engine = create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'core.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with sessionmaker(engine,class_=AsyncSession,expire_on_commit=False)() as session:
            session.add(ConformationalMappingSource(source_id='sequence',principal_id='local-personal-workflow',
                source_kind='protein_sequence',storage_root=str(inputs),relative_path='sequence.txt',
                content_sha256=sha(sequence.encode()),size_bytes=len(sequence),immutable=True,
                metadata_json={'sequence':sequence,'target_id':'target-a'}))
            await session.commit()
            forbidden = {'sequence','chain_id','test_case_id','benchmark_name','checkpoint','config','references','transfer_source','backend_identity'}
            fields = dict(name='one-copy fixture',backend='confornets',registered_sequence_id='sequence',
                registered_checkpoint_id=source_id,confornets={k:v for k,v in controls['confornets'].items() if k not in forbidden},
                ordered_seeds=controls['ordered_seeds'],samples_per_seed=controls['samples_per_seed'],
                feature_policy=controls['feature_policy'],runtime_policy=controls['runtime_policy'],analysis_policy=controls['analysis_policy'])
            if writer == 'operator':
                request = Request({'type':'http','method':'POST','scheme':'http','path':'/api/conformational-mapping/requests',
                    'headers':[], 'client':('testclient',50000),'server':('testserver',80)})
                reply = await cm.submit_request(cm.SubmitRequest(**fields),request,Response(),session)
                job_id = reply['job_id']
            else:
                job_id = '9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb'
                scheduler = dict(model_id='conformational_mapping',mode='map',params=dict(
                    workflow_adapter='bms.cm.confornets.adapter.v1',cm_submission=fields,
                    cm_source_receipt_ids=['sequence',source_id]))
                await global_adapter.materialize_preallocated_cm_job(session,attempt_id=job_id,scheduler=scheduler,run_group_id='fixture-group')
            job = await session.get(Job,job_id)
            document = json.loads(Path(job.params['cm_request_path']).read_bytes())
            logical = document['confornets']['checkpoint']
            assert logical == dict(path=f'registered/{source_id}.pt',sha256=sha(payload))
            assert not (Path(job.output_dir)/logical['path']).exists()
            assert (Path(job.output_dir)/'registered/sequence.txt').read_text() == sequence
            assert staged == ['sequence']
            assert checkpoint.read_bytes() == payload
            assert all(row['sha256'] != sha(payload) for row in job.provenance['cm_retry_authority_v1']['files'].values())
            print(json.dumps(dict(writer=writer,checkpoint_bytes=len(payload),checkpoint_stage_copy_bytes=0,
                                  biological_snapshot_bytes=len(sequence))))
    finally:
        await engine.dispose()
