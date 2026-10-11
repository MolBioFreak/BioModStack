"""Selected publication -> real preflight model reader -> exact sealing shell.

Weights are labelled inert placement bytes, not inference/model qualification.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import ont_submission_trust as trust
from scripts import dorado_p4_preflight as preflight

ROOT = Path(__file__).resolve().parents[3]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('placement', ['published', 'layout', 'mutable'])
def test_real_selected_binding_preflight_and_concurrent_shell(tmp_path, monkeypatch, placement):
    import paths
    import model_registry
    from services.remote_execution import hf_assets
    weights = tmp_path/'weights'
    weights.mkdir()
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    lock = json.loads((ROOT/'config/ngs/dorado_v2.1.2.lock.json').read_text())
    selected = lock['models']['dna']['sup']
    payload = b'INERT selected model fixture; NOT model weights\n' * 128
    digest = _sha(payload)
    entries = [{'member_path': selected['id']+'/weights.pt', 'manifest': {'sha256': digest, 'size_bytes': len(payload)}}]
    dependency = {'kind':'weights', 'relative_path':'dorado'}
    layout_digest = _sha(json.dumps({'dependency':dependency, 'members':entries}, sort_keys=True,separators=(',',':')).encode())
    model_root = weights/'dorado/2.1.2'
    if placement == 'layout':
        model_root = weights/'layouts/dorado'/layout_digest
    model = model_root/selected['id']
    model.mkdir(parents=True)
    native = model/'weights.pt'
    if placement == 'layout':
        body = weights/'objects/sha256'/digest/'runtime.sif'
        body.parent.mkdir(parents=True)
        body.write_bytes(payload)
        body.chmod(0o400)
        native.symlink_to(os.path.relpath(body, native.parent))
        model.chmod(0o500)
        model_root.chmod(0o500)
    else:
        body = native
        body.write_bytes(payload)
        body.chmod(0o400)  # mode alone must never manufacture retention
    selected.update(aggregate_sha256=_sha(b'weights.pt\0'+str(len(payload)).encode()+b'\0'+digest.encode()+b'\n'),files=1,bytes=len(payload))
    lock_path = tmp_path/'lock.json'
    lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(model_registry, 'model_acquisition_plan', lambda _: {'artifacts':[dict(dependency=dependency, **e) for e in entries] if placement == 'layout' else []})
    prefix = 'weights/dorado/2.1.2/'+selected['id']
    # Existing publication-index reader is genuine; only task-owned index location.
    index = tmp_path/'index.json'
    index.write_text(json.dumps({'schema':hf_assets.NAMED_INDEX_SCHEMA, 'archive':{'sha256':'a'*64,'size_bytes':123}, 'dependencies':['weights/dorado'], 'artifacts':[dict(name=prefix+'/weights.pt',sha256=digest,size_bytes=len(payload),mode=0o400)]}))
    index.chmod(0o600)
    monkeypatch.setattr(hf_assets,'publication_index_path',lambda: index if placement == 'published' else None)
    monkeypatch.setattr(hf_assets,'weights_archive',lambda: ('a'*64,123))
    params = {'dorado_lock_manifest':str(lock_path),'dorado_model_root':str(model_root)}
    native_parameters = trust.dorado_preflight_parameters({**params, 'dorado_input_binding_json':'FORGED CLIENT VALUE'})
    assert native_parameters['dorado_model_root'] == str(model_root.resolve())
    binding = json.loads(native_parameters['dorado_input_binding_json'])
    # Genuine generated POD5 parsing with an inert runtime-capability double;
    # model member/aggregate verification remains the production implementation.
    pod5_root = tmp_path/'pod5'
    _native_pod5(pod5_root)
    from services import nextflow
    invocation = nextflow.compile_nextflow_invocation(
        'nanopore', 'basecall_dna',
        {**params, 'pod5_dir': str(pod5_root), 'gpu_id': 0,
         'dorado_input_binding_json': 'FORGED CLIENT VALUE'},
        str(tmp_path / 'compiled-output'), job_id='fixture')
    assert invocation.native_parameters['dorado_model_root'] == str(model_root.resolve())
    assert json.loads(invocation.native_parameters['dorado_input_binding_json']) == binding
    assert not (tmp_path / 'compiled-output').exists()
    runtime = tmp_path/'runtime.sif'
    runtime.write_bytes(b'NOT AN IMAGE; capability-double fixture')
    lock['dorado']['sif_sha256'] = _sha(runtime.read_bytes())
    lock_path.write_text(json.dumps(lock))
    import types
    model_reads = []
    original_hash = preflight._sha256
    def count_hash(path):
        if Path(path).resolve() == body.resolve():
            model_reads.append(str(path))
        return original_hash(path)
    with monkeypatch.context() as runtime_double:
        runtime_double.setattr(preflight, '_sha256', count_hash)
        runtime_double.setattr(preflight, '_verified_runtime_location', lambda path, expected: (path, None))
        runtime_double.setattr(preflight, '_verify_runtime_capabilities', lambda *a, **k: {'scope':'inert-capability-double'})
        runtime_double.setattr(preflight.subprocess, 'run', lambda *a, **k: types.SimpleNamespace(returncode=0,stdout=lock['dorado']['version'],stderr=''))
        receipt = preflight.build_preflight(lock_path=lock_path,pod5_root=pod5_root,molecule='dna',quality='sup',mode='simplex',
            model_root=model_root,runtime_sif=runtime,selected_binding=binding)
    record = receipt['runtime']['assets']['models']['base']
    assert len(model_reads) == 1  # The original identity pass also supplies retention.
    retained = placement != 'mutable'
    assert record.get('retained',False) is retained
    before = body.stat()
    source = (ROOT/'modules/ngs/dorado_basecall.nf').read_text()
    shell = source[source.index('    verify_model_tree() {'):source.index('    runtime_expected=')]
    shell = shell.replace('/weights/dorado/2.1.2',str(model_root)).replace('\\$','$').replace('\\\\0','\\0').replace('\\\\n','\\n')
    def consume(name):
        work = tmp_path/name
        work.mkdir()
        (work/'dorado_preflight.json').write_text(json.dumps({'model':record}))
        result = subprocess.run(['bash','-euc',shell+'\nmkdir sealed_models; snapshot_model '+selected['id']+' .model'],cwd=work,capture_output=True,text=True)
        assert result.returncode == 0, result.stderr
        return work/'sealed_models'/selected['id']/'weights.pt'
    cold = consume('cold')
    warm = consume('warm')
    with ThreadPoolExecutor(2) as pool:
        concurrent = list(pool.map(consume,['concurrent-a','concurrent-b']))
    outputs = [cold,warm,*concurrent]
    assert all((path.stat().st_ino == before.st_ino) is retained for path in outputs)
    if retained:
        assert body.stat().st_ctime_ns == before.st_ctime_ns
    shutil.rmtree(tmp_path/'cold')
    assert all(path.read_bytes()==payload for path in outputs[1:])
    print(json.dumps({'placement':placement,'model_bytes':len(payload),'consumers':len(outputs),'avoided_task_model_bytes':len(payload)*len(outputs) if retained else 0}))
    if placement == 'layout':
        with pytest.raises(ValueError,match='undeclared symlink'):
            preflight.verify_model_identity(selected,model)
        # Same bytes at an undeclared alias are not a declared acquisition.
        model.chmod(0o700)
        native.unlink(); native.symlink_to(body)
        with pytest.raises(ValueError,match='undeclared symlink'):
            preflight.verify_model_identity(selected,model,binding['models'][selected['id']])
        model_root.chmod(0o700)


def test_binding_is_server_controlled():
    assert 'dorado_input_binding_json' in trust.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS


def _native_pod5(root):
    from datetime import datetime, timezone
    import uuid
    import numpy as np
    import pod5
    root.mkdir()
    source = root/'read.pod5'
    now = datetime(2020,1,1,tzinfo=timezone.utc)
    info = pod5.RunInfo(acquisition_id='native-one-copy-fixture', acquisition_start_time=now,
        adc_max=8191,adc_min=-8192,context_tags={'sample_frequency':'5000'},experiment_name='fixture',
        flow_cell_id='fixture',flow_cell_product_code='FLO-MIN114',protocol_name='fixture',protocol_run_id='fixture',
        protocol_start_time=now,sample_id='fixture',sample_rate=5000,sequencing_kit='SQK-LSK114',
        sequencer_position='fixture',sequencer_position_type='MinION',software='fixture',system_name='fixture',system_type='fixture',tracking_id={})
    read = pod5.Read(read_id=uuid.UUID('00000000-0000-4000-8000-000000000001'),pore=pod5.Pore(channel=1,well=1,pore_type='not_set'),
        calibration=pod5.Calibration(offset=0,scale=1),read_number=1,start_sample=0,median_before=0,
        end_reason=pod5.EndReason(pod5.EndReasonEnum.UNKNOWN,False),run_info=info,signal=np.array([1,2,3,4],dtype=np.int16))
    with pod5.Writer(source) as writer:
        writer.add_read(read)
    return source


@pytest.mark.asyncio
async def test_native_pod5_owner_preflight_and_shell(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import uuid
    import numpy as np
    import pod5
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import OntInstrumentRun, OntInstrumentRunEvent, OntRawSignalRepresentation
    from services import ont_raw_signal as raw
    root = tmp_path/'pod5'
    source = _native_pod5(root)
    now = datetime(2020,1,1,tzinfo=timezone.utc)
    # Real terminal owner, real SQL round trip; no copied/invented preflight input inventory.
    manifest = {'run_id':'fixture-run','observed_generation':1,'artifacts':[raw._file_artifact(source,'fixture-pod5',kind='pod5')]}
    from sqlalchemy import event, select
    loaded = []
    def observed_load(record, _context):
        loaded.append(record.id)
    listening = False
    engine = create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'fixture.db'))
    async with engine.begin() as connection:
        for table in (OntInstrumentRun.__table__,OntInstrumentRunEvent.__table__,OntRawSignalRepresentation.__table__):
            await connection.run_sync(table.create)
    try:
        async with async_sessionmaker(engine,expire_on_commit=False)() as session:
            session.add(OntInstrumentRun(id='fixture-run',position_id='fixture',state='completed',observed_at=now,observed_generation=1,
                terminal_artifact_manifest=manifest,terminal_artifact_manifest_sha256=raw._digest(manifest)))
            session.add(OntInstrumentRunEvent(id='fixture-event',run_id='fixture-run',event_type='observed',state='completed',observed_at=now,observed_generation=1))
            await session.commit()
            await raw.register_native_pod5_generation(session,run_id='fixture-run',observed_generation=1)
            await session.commit()
            original = (await session.execute(select(OntRawSignalRepresentation))).scalar_one()
            for index in range(20):
                unrelated = json.loads(json.dumps(original.artifact_manifest))
                for item in unrelated['artifacts']:
                    item['path'] = str(tmp_path / f'other-{index}' / Path(item['path']).name)
                session.add(OntRawSignalRepresentation(
                    id=f'unrelated-{index}', run_id='fixture-run', observed_generation=1,
                    role='source', source_kind='minknow_native', format='pod5',
                    state=original.state, reason_code=original.reason_code,
                    artifact_manifest=unrelated, manifest_sha256=raw._digest(unrelated),
                    retention_pinned_at=now))
            await session.commit()
        event.listen(OntRawSignalRepresentation, 'load', observed_load)
        listening = True
        async with async_sessionmaker(engine,expire_on_commit=False)() as session:
            owned = await raw.retained_pod5_launch_binding(session,root)
            assert owned is not None
            from routers import ont_runs
            monkeypatch.setattr(ont_runs, 'get_allowed_roots', lambda: {'inputs': tmp_path})
            request = ont_runs.OntNgsSubmitRequest(name='retained-native-fixture', params={'pod5_dir':str(root)})
            job, replay, _, _ = await ont_runs._prepare_ont_ngs_workflow('ont_basecall_dna',request,session,session)
            assert job.params['ont_input_provenance']['source'] == 'retained_pod5_generation'
            assert replay.params == request.params  # no client retention flag
            projected = trust.dorado_preflight_parameters(job.params)
            selected_binding = json.loads(projected['dorado_input_binding_json'])
            assert selected_binding['inputs'] == owned
            assert set(loaded) == {owned['representation_id']}
        kwargs = dict(lock_path=ROOT/'config/ngs/dorado_v2.1.2.lock.json',pod5_root=root,molecule='dna',quality='sup',mode='simplex',
            model_root=tmp_path/'unused-models',runtime_sif=tmp_path/'unused-image',verify_assets=False)
        receipt = preflight.build_preflight(**kwargs,selected_binding=selected_binding)
        assert receipt['inputs']['retained'] is True
        assert receipt['inputs']['read_count']==1
        assert 'retained' not in preflight.build_preflight(**kwargs)['inputs']
        module=(ROOT/'modules/ngs/dorado_basecall.nf').read_text()
        start=module.index('    retained_pod5=')
        end=module.index('    if [[ -n "\\${pair_relative}"',start)
        shell=module[start:end].replace('\\$','$')
        before=source.stat()
        def consume(name):
            work=tmp_path/name;work.mkdir()
            (work/'dorado_preflight.json').write_text(json.dumps(receipt))
            command='source "'+str(ROOT/'scripts/ngs_input_snapshot.sh')+'"; pod5_source="'+str(root)+'"; '+shell
            result=subprocess.run(['bash','-euc',command],cwd=work,capture_output=True,text=True)
            assert result.returncode==0,result.stderr
            output=work/'sealed_pod5/read.pod5'
            with pod5.Reader(output) as reader:
                assert list(next(reader.reads()).signal)==[1,2,3,4]
            return output
        cold=consume('cold');warm=consume('warm')
        with ThreadPoolExecutor(2) as pool:
            concurrent=list(pool.map(consume,['concurrent-a','concurrent-b']))
        assert all(p.stat().st_ino==before.st_ino for p in [cold,warm,*concurrent])
        assert source.stat().st_ctime_ns==before.st_ctime_ns
        shutil.rmtree(tmp_path/'cold')
        assert warm.read_bytes()==source.read_bytes()
        async with async_sessionmaker(engine)() as session:
            assert await raw.retained_pod5_launch_binding(session,root)==owned
        # Genuinely mutable/legacy source keeps the independent snapshot.
        receipt['inputs'].pop('retained')
        legacy=consume('legacy')
        assert legacy.stat().st_ino!=before.st_ino
        print(json.dumps({'native_pod5_bytes':before.st_size,'cold_warm_concurrent_consumers':4,'avoided_task_pod5_bytes':before.st_size*4,'mutable_snapshot_bytes':legacy.stat().st_size}))
    finally:
        if listening:
            event.remove(OntRawSignalRepresentation, 'load', observed_load)
        await engine.dispose()
