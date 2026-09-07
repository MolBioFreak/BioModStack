"""Offline/data-only literal Boltz owners -> Nextflow publishDir -> SQLite.
Only model script bodies are replaced; input normalization/precheck, process
input/output declarations and all publishDir directives execute from source.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'platform/api'), str(ROOT / 'platform/api/tests'), str(ROOT / 'scripts')]


def native_payload(destination, name, sequence):
    spec = importlib.util.spec_from_file_location('boltz_transport_native', ROOT / 'tests/test_boltz_native_producer_identity.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fixture = module.NativeIdentityTest()
    fixture.setUp()
    try:
        for path in list(fixture.orig.iterdir()):
            if '_model_0' in path.name:
                (path.parent / path.name.replace('_model_0', '_model_1')).write_bytes(path.read_bytes())
        (fixture.pred / 'input_model_1.pdb').write_bytes(fixture.structure.read_bytes())
        for directory in (fixture.orig, fixture.pred):
            for path in list(directory.iterdir()):
                path.rename(path.with_name(path.name.replace('input', name)))
        fixture.orig.rename(fixture.orig.with_name(name))
        fixture.ledger.rename(fixture.ledger.with_name(name + '.npz'))
        if sequence:
            from write_sequence_producer_manifest import build_manifest
            from services.boltz_launch_authority import sequence_metadata
            manifest = build_manifest(metadata=sequence_metadata('AAA:AA:A', name), predictions_dir=fixture.pred,
                producer_method='boltz', protein_science_contract_revision=1, boltz_native_root=fixture.native)
        else:
            from write_structure_producer_manifest import build_manifest
            manifest = build_manifest(predictions_root=fixture.pred, producer_method='boltz', producer_sample=name,
                formats=['pdb'], protein_science_contract_revision=1, boltz_native_root=fixture.native)
        destination.mkdir(parents=True)
        shutil.copytree(fixture.pred, destination / 'predictions')
        (destination / 'producer_candidates.json').write_text(json.dumps(manifest))
        return manifest
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('owner', ['BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask', 'BoltzFromComplex'])
@pytest.mark.parametrize('marked', [True, False])
def test_literal_owner_to_sqlite(tmp_path, owner, marked):
    wire = asyncio.run(run_transport(tmp_path, owner, marked))
    if marked:
        artifact = tmp_path / 'published-pae-wire.json'
        artifact.write_text(json.dumps(wire, sort_keys=True, separators=(',', ':')))
        env = dict(os.environ, BMS_TEST_BOLTZ_WIRE=str(artifact),
            BMS_TEST_BOLTZ_WIRE_SHA256=hashlib.sha256(artifact.read_bytes()).hexdigest())
        result = subprocess.run(['pnpm', 'exec', 'vitest', 'run', '--config', 'vitest.md.config.ts',
            'tests/vitest/publishedBoltzPaeMounted.test.tsx', 'tests/vitest/publishedBoltzNativeMounted.test.tsx'], cwd=ROOT / 'platform/frontend',
            env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        print(owner + ': published -> SQLite -> ASGI -> mounted PAE\n' + result.stdout)


async def run_transport(tmp_path, owner, marked, damage=None):
    from test_core_protein_candidates import setup, job
    from services.nextflow import _persist_boltz_launch_authority, build_nextflow_command
    from services.result_ingester import ingest_job_results
    from services.boltz_launch_authority import command_params
    from sqlalchemy import select
    from database import Design, Job
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'explicit cached native Nextflow jar required'
    out = tmp_path / 'out'
    out.mkdir()
    sequence = owner != 'BoltzFromComplex'
    msa = tmp_path / 'input.a3m'
    msa.write_text('>input\n' + ('A' * (150 * 1024) if damage == 'large_msa' else 'AAA') + '\n')
    source = tmp_path / 'complex.json'
    definition = {'components': [{'id':'A', 'type':'protein', 'sequence':'AAA:AA:A'}]}
    if damage == 'component_input_race':
        definition['components'][0]['msa_path'] = str(msa)
    source.write_text(json.dumps(definition))
    factory, engine = await setup(tmp_path)
    names = ['sample_job0', 'sample_job1']
    try:
        async with factory() as session:
            current = job(out, marked=marked)
            current.model_id, current.mode = 'boltz2', 'predict' if sequence else 'complex'
            current.params = dict(sequence_name='sample', num_parallel_jobs=2, boltz_use_msa=owner == 'BoltzFromSequenceWithMSATask',
                **({'sequence': 'AAA:AA:A'} if sequence else {'complex_json_path': str(source)}))
            if owner == 'BoltzFromSequenceWithMSATask':
                current.params['msa_path'] = str(msa)
            session.add(current)
            await session.commit()
            command = build_nextflow_command(current.model_id, current.mode, current.params, str(out), job_id=current.id)
            command = await _persist_boltz_launch_authority(session, current, command)
            params = {key: (json.loads(value) if value in ('true', 'false') or value.isdigit() else value)
                for key, value in command_params(command).items()}
            params.update(out_dir=str(out), job_id='job')
            if marked:
                assert params.get('protein_science_contract_revision') == 1, 'trusted launch must transport producer revision'
                authority_args = command[command.index('--boltz_launch_authority_path'):]
                assert len(authority_args) == 4 and max(map(len, authority_args)) < 4096
                authority_path = Path(params.pop('boltz_launch_authority_path'))
                params.pop('boltz_launch_authority_sha256')
                if damage == 'large_msa':
                    assert authority_path.stat().st_size > 150 * 1024
                if damage == 'foreign_authority_path':
                    foreign = tmp_path / 'foreign.json'
                    foreign.write_bytes(authority_path.read_bytes())
                    authority_args[1] = str(foreign)
                elif damage == 'mutated_authority_bytes':
                    authority_path.write_bytes(authority_path.read_bytes() + b' ')
                elif damage == 'symlink_authority_path':
                    foreign = tmp_path / 'foreign.json'
                    authority_path.rename(foreign)
                    authority_path.symlink_to(foreign)
            else:
                authority_args = []
        payload_root = tmp_path / 'payloads'
        for name in names:
            native_payload(payload_root / name, name, sequence)
        module_source = (ROOT / 'modules/structure_prediction.nf').read_text()
        # Literal functions only (remove unrelated module imports).
        functions = module_source[:module_source.index('process GenerateLocalMSA')]
        functions = '\n'.join(line for line in functions.splitlines() if not line.startswith('include '))
        literal = 'process ' + owner + ' {' + module_source.split('process ' + owner + ' {', 1)[1].split('\n}', 1)[0] + '\n}'
        declaration = literal.split('    script:', 1)[0]
        selector = '${producer_meta.producer_artifact_id}' if sequence else '${complex_name}'
        binding_line = next(line for line in literal.split('    script:', 1)[1].splitlines()
            if 'printf' in line and 'boltz_task_binding.json' in line)
        race_line = ''
        if damage == 'input_race':
            original_input = source if owner == 'BoltzFromComplex' else msa
            input_selector = '${complex_yaml}' if owner == 'BoltzFromComplex' else '${msa_file}'
            expected_digest = hashlib.sha256(original_input.read_bytes()).hexdigest()
            race_line = f"    printf CHANGED > '{original_input}'\n    test \\\"\\$(sha256sum '{input_selector}' | cut -d ' ' -f1)\\\" = '{expected_digest}' || exit 74"
        if damage == 'component_input_race':
            expected_digest = hashlib.sha256(msa.read_bytes()).hexdigest()
            race_line = f'''    printf CHANGED > '{msa}'
    '{sys.executable}' -c "import json,hashlib,pathlib; p=json.load(open('${{complex_yaml}}'))['components'][0]['msa_path']; assert hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() == '{expected_digest}'"
'''
        instrumented = declaration + f'''    script:
    """
{binding_line}
{race_line}
    cp -R '{payload_root}/{selector}/predictions' predictions
    cp '{payload_root}/{selector}/producer_candidates.json' producer_candidates.json
    touch fixture.log
    """
}}
'''
        name_expression = "['sample_job0', 'sample_job1']"
        if damage == 'substitute':
            name_expression = "['sample_job0', 'foreign']"
        if sequence:
            inputs = f"normalizeSequenceProducerInputs(Channel.fromList({name_expression}).map {{ name -> tuple('AAA:AA:A', name) }})"
            if owner == 'BoltzFromSequenceWithMSATask':
                inputs += f".map {{ meta, seq, name -> tuple(meta, seq, name, file('{msa}')) }}"
            dispatch = f"{owner}(checkedBoltzInputs(inputs, '{owner}'))"
        else:
            inputs = f"Channel.fromList({name_expression}).map {{ name -> tuple(name, file('{source}'), file('{msa}')) }}"
            dispatch = f"{owner}(checkedBoltzInputs(inputs, '{owner}'))"
        harness = tmp_path / 'main.nf'
        harness.write_text('nextflow.enable.dsl=2\n' + functions + '\n' + instrumented + f'\nworkflow {{\n inputs = {inputs}\n {dispatch}\n}}\n')
        config = tmp_path / 'minimal.config'
        config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\nprocess.container=null\ndocker.enabled=false\napptainer.enabled=false\nsingularity.enabled=false\n')
        parameters = tmp_path / 'params.json'
        parameters.write_text(json.dumps(params))
        home = tmp_path / 'home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home), NXF_HOME=str(home / '.nextflow'), NXF_OFFLINE='true',
            NXF_DISABLE_CHECK_LATEST='true', NXF_ANSI_LOG='false', NXF_PLUGINS_DEFAULT='', NXF_CACHE_DIR=str(tmp_path / 'cache'))
        result = subprocess.run(['java', '-jar', jar, '-C', str(config), 'run', str(harness),
            '-params-file', str(parameters), '-work-dir', str(tmp_path / 'work'), *authority_args],
            env=env, cwd=tmp_path, text=True, capture_output=True, timeout=120)
        if damage in ('substitute', 'foreign_authority_path', 'mutated_authority_bytes', 'symlink_authority_path'):
            assert result.returncode != 0
            assert 'task inventory' in result.stdout + result.stderr, result.stdout + result.stderr
            assert not (out / 'scientific/boltz').exists()
            return
        assert result.returncode == 0, result.stdout + result.stderr
        if not marked:
            assert not (out / 'scientific').exists()
            assert len(list((out / 'pdb_files/predictions').glob('*.pdb'))) == 4
            return
        inventory = json.loads((out / 'scientific/boltz_workflow_inventory.json').read_bytes())
        assert [task['namespace'] for task in inventory['tasks']] == names
        for name in names:
            actual = out / 'scientific/boltz' / name
            original = payload_root / name
            assert (actual / 'producer_candidates.json').read_bytes() == (original / 'producer_candidates.json').read_bytes()
            for path in original.rglob('*'):
                if path.is_file():
                    published = actual / path.relative_to(original)
                    assert published.read_bytes() == path.read_bytes()
        if damage and damage not in ('input_race', 'component_input_race', 'large_msa'):
            task_root = out / 'scientific/boltz'
            if damage == 'missing_task':
                shutil.rmtree(task_root / names[-1])
            elif damage == 'substituted_task':
                (task_root / names[-1]).rename(task_root / 'foreign')
            elif damage == 'extra_task':
                (task_root / 'foreign').mkdir()
            elif damage == 'missing_inventory':
                (out / 'scientific/boltz_workflow_inventory.json').unlink()
            elif damage == 'changed_inventory':
                inventory['tasks'][-1]['namespace'] = 'foreign'
                (out / 'scientific/boltz_workflow_inventory.json').write_text(json.dumps(inventory))
            async with factory() as session:
                loaded = await session.get(Job, 'job')
                if damage == 'changed_job':
                    loaded.retry_count += 1
                elif damage == 'changed_input':
                    loaded.params = dict(loaded.params, sequence='CCCC')
                elif damage == 'changed_roster':
                    import copy
                    from sqlalchemy.orm.attributes import flag_modified
                    value = copy.deepcopy(loaded.provenance)
                    value['boltz_launch_authority']['tasks'][-1] = value['boltz_launch_authority']['tasks'][0]
                    loaded.provenance = value
                    flag_modified(loaded, 'provenance')
                session.add(Design(id='review', job_id='job', name='keep', source_stage='review', pdb_path='review.pdb'))
                await session.commit()
                loaded.name = 'pending'
                from sqlalchemy import event
                flushed = []
                event.listen(session.sync_session, 'before_flush', lambda *args: flushed.append(True))
                with pytest.raises(RuntimeError):
                    await ingest_job_results('job', str(out), session, commit=False)
                assert not flushed
                await session.commit()
            async with factory() as session:
                rows = list((await session.execute(select(Design))).scalars())
                assert [(row.id, row.name) for row in rows] == [('review', 'keep')]
                assert (await session.get(Job, 'job')).name == 'pending'
            return
        async with factory() as session:
            assert await ingest_job_results('job', str(out), session, commit=False) == 4
            await session.commit()
        async with factory() as session:
            loaded = await session.get(Job, 'job')
            rows = list((await session.execute(select(Design))).scalars())
            assert len(rows) == 4
            assert len(loaded.provenance['boltz_launch_authority']['tasks']) == 2
            for row in rows:
                for artifact in row.confidence_metrics['core_protein_candidate_artifacts'].values():
                    path = Path(artifact['path'])
                    assert path.is_relative_to(out)
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
            assert await ingest_job_results('job', str(out), session) == 0
            if damage is None:
                # Full real publication -> committed SQLite reload -> ASGI JSON.
                from fastapi import FastAPI
                from httpx import ASGITransport, AsyncClient
                from routers.designs import router
                from database import get_session
                app = FastAPI()
                app.include_router(router, prefix='/designs')
                async def dependency():
                    yield session
                app.dependency_overrides[get_session] = dependency
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    row = sorted(rows, key=lambda d: d.name)[0]
                    detail = await client.get('/designs/' + row.id)
                    pae = await client.get('/designs/' + row.id + '/pae')
                    listing = await client.get('/designs/by-job/job')
                    assert detail.status_code == pae.status_code == listing.status_code == 200
                    wire = pae.json()
                    assert wire['status'] == 'ok'
                    assert wire['document'] == detail.json()['scientific_structure_document']
                    assert wire['document']['candidateId'] == row.id
                    assert all(d['scientific_structure_document']['candidateId'] == d['id'] for d in listing.json()['designs'])
                    residue = await client.get('/designs/' + row.id + '/residue-metrics')
                    chains = await client.get('/designs/' + row.id + '/chain-metrics')
                    assert residue.status_code == chains.status_code == 200
                    assert residue.json()['status'] == chains.json()['status'] == 'ok'
                    return {'design': detail.json(), 'pae': wire, 'residue': residue.json(), 'chains': chains.json()}
    finally:
        await engine.dispose()


def test_large_msa_authority_uses_compact_real_nextflow_argv(tmp_path):
    asyncio.run(run_transport(tmp_path, 'BoltzFromSequenceWithMSATask', True, damage='large_msa'))


@pytest.mark.parametrize('damage', ['foreign_authority_path', 'mutated_authority_bytes', 'symlink_authority_path'])
def test_real_nextflow_rejects_foreign_or_changed_authority_file(tmp_path, damage):
    asyncio.run(run_transport(tmp_path, 'BoltzFromSequenceTask', True, damage=damage))


def test_component_msa_is_frozen_before_scientific_task(tmp_path):
    asyncio.run(run_transport(tmp_path, 'BoltzFromComplex', True, damage='component_input_race'))


@pytest.mark.parametrize('owner', ['BoltzFromSequenceWithMSATask', 'BoltzFromComplex'])
def test_mutable_input_is_frozen_before_scientific_task(tmp_path, owner):
    asyncio.run(run_transport(tmp_path, owner, True, damage='input_race'))


@pytest.mark.parametrize('damage', ['missing_task', 'substituted_task', 'extra_task', 'missing_inventory',
    'changed_inventory', 'changed_job', 'changed_input', 'changed_roster'])
def test_first_ingestion_rejects_incomplete_actual_publication(tmp_path, damage):
    asyncio.run(run_transport(tmp_path, 'BoltzFromSequenceTask', True, damage=damage))


@pytest.mark.parametrize('owner', ['BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask', 'BoltzFromComplex'])
def test_independent_workflow_rejects_substitution_before_dispatch(tmp_path, owner):
    asyncio.run(run_transport(tmp_path, owner, True, damage='substitute'))
