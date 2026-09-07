"""Real pinned data writer -> producer manifest -> dispatcher -> SQLite."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import select
from database import Design
from services.result_ingester import ingest_job_results
from test_core_protein_candidates import setup, job as base_job
from services.boltz_launch_authority import build_authority, sequence_metadata, digest
from services.frustrampnn.contracts import canonical_json_bytes


def job(tmp_path, marked=True):
    current = base_job(tmp_path, marked=marked)
    inputs = tmp_path / 'fixture_launch.json'
    if not marked or not inputs.exists():
        return current
    current.model_id, current.mode = 'boltz2', 'predict'
    current.retry_count = 0
    current.params = json.loads(inputs.read_text())
    current.mode = 'complex' if current.params.get('complex_json_path') else 'predict'
    command = ['nextflow', 'run', 'fixture.nf']
    for key, value in current.params.items():
        command += ['--' + key, str(value)]
    authority = build_authority(current, command)
    current.provenance = {**current.provenance, 'boltz_launch_authority': authority}
    inventory = dict(schema_name='boltz_workflow_inventory', schema_version=1,
        job_id=current.id, attempt=0, result_root=str(tmp_path), tasks=authority['tasks'],
        launch_sha256=digest(canonical_json_bytes(authority)))
    (tmp_path / 'scientific/boltz_workflow_inventory.json').write_bytes(canonical_json_bytes(inventory))
    for task in authority['tasks']:
        binding = dict(schema_name='boltz_task_binding', schema_version=1, job_id=current.id,
            attempt=0, result_root=str(tmp_path), launch_sha256=inventory['launch_sha256'], task=task)
        (tmp_path / 'scientific/boltz' / task['namespace'] / 'boltz_task_binding.json').write_bytes(canonical_json_bytes(binding))
    return current

ROOT = Path(__file__).resolve().parents[3]


def publication(tmp_path, sequence=False, count=1, native_scalars=None):
    # Unit fixtures declare launch inputs independently of all result files.
    source = tmp_path / 'complex_input.json'
    source.write_text(json.dumps({'components': [{'id': 'A', 'type': 'protein', 'sequence': 'AAA:AA:A'}]}))
    inputs = dict(sequence_name='sample', **({'sequence_input': 'AAA:AA:A'} if sequence else {'complex_json_path': str(source)}))
    (tmp_path / 'fixture_launch.json').write_text(json.dumps(inputs))
    spec = importlib.util.spec_from_file_location('native_fixture', ROOT / 'tests/test_boltz_native_producer_identity.py')
    import sys
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fixture = module.NativeIdentityTest()
    fixture.setUp()
    try:
        for index in range(1, count):
            for path in list(fixture.orig.iterdir()):
                if '_model_0' in path.name:
                    (path.parent / path.name.replace('_model_0', f'_model_{index}')).write_bytes(path.read_bytes())
            (fixture.pred / f'input_model_{index}.pdb').write_bytes(fixture.structure.read_bytes())
        if native_scalars is not None:
            assert len(native_scalars) == count
            for index, scalars in enumerate(native_scalars):
                confidence = fixture.orig / f"confidence_input_model_{index}.json"
                payload = json.loads(confidence.read_text())
                payload.update(scalars)
                confidence.write_text(json.dumps(payload, allow_nan=False))
        if sequence:
            from write_sequence_producer_manifest import build_manifest
            metadata = sequence_metadata('AAA:AA:A', 'sample')
            manifest = build_manifest(metadata=metadata, predictions_dir=fixture.pred, producer_method='boltz',
                protein_science_contract_revision=1, boltz_native_root=fixture.native)
        else:
            manifest = fixture.build()
        root = tmp_path / 'scientific/boltz/sample'
        root.mkdir(parents=True)
        shutil.copytree(fixture.pred, root / 'predictions')
        (root / 'producer_candidates.json').write_text(json.dumps(manifest))
        return root, manifest
    finally:
        fixture.doCleanups()


@pytest.mark.asyncio
@pytest.mark.parametrize('sequence', [False, True])
async def test_real_publisher_to_sqlite(tmp_path, sequence):
    root, manifest = publication(tmp_path, sequence)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            current.mode = 'predict' if sequence else 'complex'
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session, commit=False) == 1
            await session.commit()
        async with factory() as session:
            row = (await session.execute(select(Design))).scalar_one()
            block = row.confidence_metrics['core_protein_scientific']
            assert block['schema_version'] == 1
            assert block['design_id'] == row.id
            assert block['candidate_id'] == ('sample' if sequence else 'input_model_0.pdb')
            assert block['document_id'] == manifest['candidates'][0]['producer_output_key']
            records = {m['metric_key']:m for m in block['metrics']}
            assert records['ptm']['value'] == .6
            assert records['ptm']['unit'] == 'dimensionless'
            assert records['complex_plddt']['value'] == .6
            assert records['complex_plddt']['unit'] == 'fraction'
            assert records['ptm']['source']['artifact_sha256'] == block['artifacts']['metrics']['sha256']
            assert not any(k in json.dumps(block) for k in ['"residues"', '"token_to_structure"', '"pair_chains_iptm"'])
            before = row.id, row.confidence_metrics
            assert await ingest_job_results('job', str(tmp_path), session) == 0
            assert (row.id, row.confidence_metrics) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['axes', 'chain_map', 'hash', 'source', 'foreign_key', 'missing',
    'duplicate', 'substitute', 'scalar_bool', 'scalar_string', 'scalar_range', 'symlink',
    'directory_symlink', 'duplicate_json', 'empty_inventory', 'foreign_root'])
async def test_last_candidate_failure_is_atomic(tmp_path, damage):
    import hashlib
    root, manifest = publication(tmp_path, count=2)
    last = manifest['candidates'][-1]
    native = last['boltz_native_identity']
    pred = root / 'predictions'
    if damage == 'axes':
        for axis in [native['aligned_error']['identity_evidence']['row_axis'],
                     native['aligned_error']['identity_evidence']['column_axis'], native['vectors'][0]['axis']]:
            axis['residues'][0], axis['residues'][3] = axis['residues'][3], axis['residues'][0]
    elif damage == 'chain_map':
        native['chain_index_map'][0]['native_asym_id'] = 0
    elif damage == 'hash':
        native['confidence']['artifact_sha256'] = '0' * 64
    elif damage == 'source':
        (pred / last['producer_output_key']).write_text('foreign')
    elif damage == 'foreign_key':
        native['confidence']['artifact_key'] = '../confidence.json'
    elif damage == 'missing':
        (pred / last['producer_output_key']).unlink()
    elif damage == 'duplicate':
        manifest['candidates'][-1] = manifest['candidates'][0]
    elif damage == 'substitute':
        last['producer_output_key'] = 'input_model_0.pdb'
    elif damage.startswith('scalar_'):
        path = pred / native['confidence']['artifact_key']
        payload = json.loads(path.read_text())
        payload['ptm'] = {'scalar_bool':True, 'scalar_string':'0.6', 'scalar_range':1.1}[damage]
        path.write_text(json.dumps(payload))
        native['confidence']['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == 'symlink':
        path = pred / last['producer_output_key']
        replacement = root / 'replacement.pdb'
        path.rename(replacement)
        path.symlink_to(replacement)
    elif damage == 'directory_symlink':
        moved = root / 'moved'
        pred.rename(moved)
        pred.symlink_to(moved, target_is_directory=True)
    elif damage == 'empty_inventory':
        manifest['candidates'] = []
    manifest_path = root / 'producer_candidates.json'
    manifest_path.write_text(json.dumps(manifest))
    if damage == 'duplicate_json':
        manifest_path.write_text(manifest_path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            if damage == 'foreign_root':
                current.output_dir = str(tmp_path / 'different_job')
            session.add_all([current, Design(id='review', job_id='job', name='keep', source_stage='review', pdb_path='review.pdb')])
            await session.commit()
            current.name = 'pending caller edit'
            from sqlalchemy import event
            flushed = []
            event.listen(session.sync_session, 'before_flush', lambda *args: flushed.append(True))
            with pytest.raises(RuntimeError):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert not flushed
            assert not session.deleted and not session.new
            await session.commit()
        async with factory() as session:
            from database import Job
            assert (await session.get(Job, 'job')).name == 'pending caller edit'
            rows = list((await session.execute(select(Design))).scalars())
            assert [(r.id, r.name) for r in rows] == [('review', 'keep')]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_path_changed_after_snapshot_is_rejected(tmp_path, monkeypatch):
    from services import boltz_scientific_persistence as owner
    root, _ = publication(tmp_path)
    original = owner._prepare
    def change_after(*args):
        prepared = original(*args)
        (root / 'predictions/confidence_input_model_0.json').write_text('{}')
        return prepared
    monkeypatch.setattr(owner, '_prepare', change_after)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            with pytest.raises(RuntimeError, match='changed'):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            await session.commit()
            assert list((await session.execute(select(Design))).scalars()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_zero_native_scalars_and_changed_replay(tmp_path):
    import hashlib
    root, manifest = publication(tmp_path)
    path = root / 'predictions/confidence_input_model_0.json'
    payload = json.loads(path.read_text())
    payload.update(ptm=0, complex_plddt=0)
    path.write_text(json.dumps(payload))
    manifest['candidates'][0]['boltz_native_identity']['confidence']['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'producer_candidates.json').write_text(json.dumps(manifest))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 1
            row = (await session.execute(select(Design))).scalar_one()
            assert all(m['value'] == 0 and m['state'] == 'ok' for m in row.confidence_metrics['core_protein_scientific']['metrics'])
            payload['ptm'] = .9
            path.write_text(json.dumps(payload))
            manifest['candidates'][0]['boltz_native_identity']['confidence']['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (root / 'producer_candidates.json').write_text(json.dumps(manifest))
            with pytest.raises(RuntimeError, match='replay'):
                await ingest_job_results('job', str(tmp_path), session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['name', 'missing', 'scalar', 'design_id'])
async def test_persisted_candidate_substitution_is_not_replay(tmp_path, damage):
    import copy
    root, _ = publication(tmp_path, count=2)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 2
            rows = list((await session.execute(select(Design).order_by(Design.name))).scalars())
            if damage == 'name':
                rows[-1].name = 'foreign'
            elif damage == 'missing':
                await session.delete(rows[-1])
            else:
                confidence = copy.deepcopy(rows[-1].confidence_metrics)
                if damage == 'scalar':
                    confidence['core_protein_scientific']['metrics'][0]['value'] = .99
                else:
                    confidence['core_protein_scientific']['design_id'] = 'foreign'
                rows[-1].confidence_metrics = confidence
            await session.commit()
            with pytest.raises(RuntimeError):
                await ingest_job_results('job', str(tmp_path), session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_params_and_file_marker_cannot_authorize_legacy(tmp_path):
    root, manifest = publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path, marked=False)
            current.model_id = 'boltz2'
            current.params = {'core_protein_scientific_contract':1, 'protein_science_contract_revision':1}
            session.add(current)
            await session.commit()
            before = (root / 'producer_candidates.json').read_bytes()
            await ingest_job_results('job', str(tmp_path), session)
            rows = list((await session.execute(select(Design))).scalars())
            assert rows
            assert all('core_protein_scientific' not in (row.confidence_metrics or {}) for row in rows)
            assert (root / 'producer_candidates.json').read_bytes() == before
            assert 'core_protein_candidate_publication' not in current.provenance
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_false_leaves_publication_to_caller(tmp_path):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session, commit=False) == 1
            async with factory() as observer:
                assert list((await observer.execute(select(Design))).scalars()) == []
            await session.rollback()
        async with factory() as observer:
            assert list((await observer.execute(select(Design))).scalars()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_specialized_owner_keeps_precedence(tmp_path, monkeypatch):
    from services import result_ingester
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            async def specialized(*args, **kwargs):
                return 17
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', specialized)
            assert await ingest_job_results('job', str(tmp_path), session) == 17
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['schema_bool', 'zero_bool', 'receipt_bool', 'receipt_string'])
async def test_replay_json_types_reject_without_repair_or_autoflush(tmp_path, damage):
    import copy
    import hashlib
    from sqlalchemy import event
    from database import Job
    root, manifest = publication(tmp_path)
    path = root / 'predictions/confidence_input_model_0.json'
    payload = json.loads(path.read_text())
    payload['ptm'] = 0.0
    path.write_text(json.dumps(payload))
    manifest['candidates'][0]['boltz_native_identity']['confidence']['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'producer_candidates.json').write_text(json.dumps(manifest))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 1
            assert await ingest_job_results('job', str(tmp_path), session) == 0
            row = (await session.execute(select(Design))).scalar_one()
            confidence = copy.deepcopy(row.confidence_metrics)
            provenance = copy.deepcopy(current.provenance)
            if damage == 'schema_bool':
                confidence['core_protein_scientific']['schema_version'] = True
            elif damage == 'zero_bool':
                confidence['core_protein_scientific']['metrics'][0]['value'] = False
            else:
                provenance['core_protein_candidate_publication']['summary']['generated_count'] = True if damage == 'receipt_bool' else '1'
            row.confidence_metrics = confidence
            current.provenance = provenance
            # SQLAlchemy's JSON dirty comparison also uses Python equality.
            # Force the intentional corruption to reach actual SQLite bytes.
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(row, 'confidence_metrics')
            flag_modified(current, 'provenance')
            await session.commit()
        async with factory() as session:
            current = await session.get(Job, 'job')
            row = (await session.execute(select(Design))).scalar_one()
            saved = json.dumps([row.confidence_metrics, current.provenance], sort_keys=True)
            current.name = 'pending caller edit'
            flushed = []
            event.listen(session.sync_session, 'before_flush', lambda *args: flushed.append(True))
            with pytest.raises(RuntimeError, match='replay'):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert not flushed
            assert not session.deleted and not session.new
            await session.commit()
        async with factory() as session:
            current = await session.get(Job, 'job')
            row = (await session.execute(select(Design))).scalar_one()
            assert current.name == 'pending caller edit'
            assert json.dumps([row.confidence_metrics, current.provenance], sort_keys=True) == saved
    finally:
        await engine.dispose()


def test_transport_keeps_task_scoped_manifest_and_native_artifacts():
    source = (ROOT / 'modules/structure_prediction.nf').read_text()
    for process in ['BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask', 'BoltzFromComplex']:
        body = source.split('process ' + process + ' {', 1)[1].split('\n}', 1)[0]
        assert '/scientific/boltz/' in body
        assert 'pattern: "producer_candidates.json"' in body
        assert 'pattern: "predictions/*"' in body
