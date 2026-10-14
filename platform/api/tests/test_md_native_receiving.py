"""Real native CPU output through managed transport and result receiving.

BMS_TEST_GMX selects an installed read-only runtime; all science is a private
synthetic two-argon software fixture, not a physical-system qualification.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bms_md import gromacs_pipeline as pipeline
from scripts.bms_md.aggregate_children import collect_children
from scripts.bms_md.collect_analysis import collect_analysis
from services.md import launch_contract, results
from test_md_results_trim import store, _seed
from database import Job, MdAttemptSegment, MdRun
from services.md.completion import validate_and_finalize_md_job
from routers.md_results import get_md_summary, get_md_artifacts, get_md_analysis
from fastapi import HTTPException


@pytest.mark.parametrize('defect', ['parent', 'symlink', 'dangling_escape', 'size', 'count'])
def test_native_topology_transport_keeps_existing_security_bounds(tmp_path, monkeypatch, defect):
    source = tmp_path / 'input'
    source.mkdir()
    topology = source / 'system.top'
    included = source / 'part.itp'
    included.write_text('native include bytes')
    topology.write_text('#include "part.itp"\n')
    if defect == 'parent':
        topology.write_text('#include "../outside.itp"\n')
    elif defect in {'symlink', 'dangling_escape'}:
        included.unlink()
        outside = tmp_path / 'outside.itp'
        if defect == 'symlink':
            outside.write_text('outside managed input root')
        included.symlink_to(outside)
    elif defect == 'size':
        monkeypatch.setattr(launch_contract, 'MAX_TOPOLOGY_CLOSURE_BYTES', 1)
    else:
        monkeypatch.setattr(launch_contract, 'MAX_TOPOLOGY_LOCAL_INCLUDES', 0)
    with pytest.raises(launch_contract.MDLaunchError) as error:
        launch_contract._materialize_topology_closure(source_topology=topology,
            topology_snapshot=topology, contract_dir=source, published_snapshots=[],
            runtime_identity_resolver=lambda: None, inspect_only=True, native_preprocessing=True)
    assert error.value.code in {'MD_TOPOLOGY_INCLUDE_FORBIDDEN', 'MD_TOPOLOGY_INCLUDE_LIMIT_EXCEEDED'}


@pytest.fixture
def native_system(tmp_path, monkeypatch):
    gmx = os.environ.get('BMS_TEST_GMX')
    if not gmx:
        pytest.skip('BMS_TEST_GMX must select the installed native runtime')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'system.gro').write_text('Synthetic argon fixture\n2\n    1AR      AR    1   1.000   1.000   1.000\n    2AR      AR    2   1.600   1.000   1.000\n   3.00000   3.00000   3.00000\n')
    (source / 'system.top').write_text('[ defaults ]\n1 2 yes 1.0 1.0\n#include "argon.itp"\n[ system ]\nSynthetic argon\n[ molecules ]\nARG 2\n')
    (source / 'argon.itp').write_text('[ atomtypes ]\nAR 18 39.948 0.0 A 0.34 0.997\n[ moleculetype ]\nARG 1\n[ atoms ]\n1 AR 1 AR AR 1 0.0 39.948\n')
    runtime = {'image_name': 'gromacs-md-2025.3.sif', 'sif_sha256': '97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b'}
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setenv('BMS_MD_RESULT_ROOT', str(tmp_path))
    monkeypatch.setattr(launch_contract, '_bound_engine_runtime_identity', lambda _: runtime)
    spec = {'schema': 'bms.md.job.v3', 'engine': 'gromacs', 'job_id': 'md-job-1', 'replicas': 1, 'random_seed': 1234,
            'input': {'coordinates': str(source / 'system.gro'), 'topology': str(source / 'system.top')},
            'execution': {'gpu_id': '0', 'ntmpi': 1, 'ntomp': 1, 'gpu_offload': 'none', 'pin': 'off'},
            'stages': [{'name': 'sample', 'mdp': {'integrator': 'md', 'dt': .001, 'nsteps': 12,
                'cutoff-scheme': 'Verlet', 'nstlist': 10, 'rlist': .8, 'coulombtype': 'Cut-off',
                'rcoulomb': .8, 'rvdw': .8, 'pbc': 'xyz', 'tcoupl': 'no', 'pcoupl': 'no',
                'constraints': 'none', 'gen-vel': 'yes', 'gen-temp': 10, 'gen-seed': 731,
                'ld-seed': 997, 'nstxout-compressed': 5, 'nstenergy': 1, 'nstlog': 1,
                'tinit': -7, 'comm-mode': 'Linear'}}]}
    return spec, gmx


def _run(tmp_path, native_system):
    spec, gmx = native_system
    params = launch_contract.materialize_md_job_spec(params={'md_job_spec': spec}, job_id='md-job-1',
        output_dir=tmp_path / 'launch', resolve_runtime_path=lambda value: value)
    output = tmp_path / 'child'
    manifest = pipeline.run_gromacs_job(Path(params['md_job_config']), output, gmx_binary=gmx)
    return params['md_job_spec'], manifest


def _collect(tmp_path, spec, manifest):
    status = tmp_path / 'children.json'
    status.write_text(json.dumps({'child_output_dirs': [str(manifest.parent)], 'completed': 1,
        'failed': 0, 'cancelled': 0, 'child_ids': ['replica-child-0']}))
    root = tmp_path / 'parent'
    collect_children(status, root)
    aggregate = root / 'manifest.json'
    (root / 'md_completion_barrier.json').write_text(json.dumps({'schema': 'bms.md.completion-barrier.v1',
        'status': 'completed', 'job_id': 'md-job-1', 'aggregate_manifest_sha256': hashlib.sha256(aggregate.read_bytes()).hexdigest()}))
    return SimpleNamespace(id='md-job-1', model_id='molecular_dynamics', output_dir=str(root),
        child_output_dir=None, params={'md_job_spec': spec}, provenance={}), root


@pytest.mark.parametrize('active', [False, True])
def test_native_missing_include_is_decided_by_grompp(tmp_path, native_system, active):
    spec, _ = native_system
    topology = Path(spec['input']['topology'])
    topology.write_text(topology.read_text() + '#ifdef UNUSED_RESTRAINTS\n#include "absent.itp"\n#endif\n')
    # This guarded self-include is valid native preprocessing, not a transport cycle.
    include = topology.parent / 'argon.itp'
    include.write_text('#ifndef ARGON_INCLUDED\n#define ARGON_INCLUDED\n' + include.read_text()
                       + '#include "argon.itp"\n#endif\n')
    original = topology.read_bytes()
    launch_contract.native_input_identity(spec)
    if active:
        spec['stages'][0]['mdp']['define'] = '-DUNUSED_RESTRAINTS'
        with pytest.raises(RuntimeError, match='MD command failed'):
            _run(tmp_path, native_system)
        log = (tmp_path / 'child/sample/grompp.command.log').read_text()
        assert 'absent.itp' in log and 'not found' in log
        ledger = json.loads((tmp_path / 'child/stage_state.json').read_text())
        assert ledger['stages']['sample']['status'] == 'failed'
        assert not (tmp_path / 'child/manifest.json').exists()
    else:
        saved, manifest = _run(tmp_path, native_system)
        assert Path(saved['input']['topology']).read_bytes() == original
        assert json.loads(manifest.read_text())['status'] == 'completed'
        assert (manifest.parent / 'sample/sample.tpr').is_file()


def test_native_mdrun_failure_is_not_optional_publication(tmp_path, monkeypatch, native_system):
    original = pipeline._run_command
    def run(command, **kwargs):
        if command[1] == 'mdrun' and '-deffnm' in command:
            command = list(command) + ['-s', str(tmp_path / 'absent.tpr')]
        return original(command, **kwargs)
    monkeypatch.setattr(pipeline, '_run_command', run)
    with pytest.raises(RuntimeError, match='MD command failed'):
        _run(tmp_path, native_system)
    child = tmp_path / 'child'
    assert (child / 'sample/sample.tpr').is_file()  # Genuine grompp succeeded.
    assert 'absent.tpr' in (child / 'sample/mdrun.command.log').read_text()
    assert json.loads((child / 'stage_state.json').read_text())['stages']['sample']['status'] == 'failed'
    assert not (child / 'manifest.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('publication', ['normal', 'editconf', 'atom_map', 'no_trajectory', 'trr', 'analysis', 'bad_collection', 'bad_wham'])
async def test_native_completed_output_reopens_without_optional_publication(store, tmp_path, monkeypatch, native_system, publication):
    spec, _ = native_system
    if publication == 'editconf':
        original = pipeline._run_command
        def run(command, **kwargs):
            if command[1] == 'editconf':
                # Real editconf fails on an absent input AFTER genuine mdrun.
                command = list(command)
                command[command.index('-f') + 1] = str(tmp_path / 'absent.gro')
            return original(command, **kwargs)
        monkeypatch.setattr(pipeline, '_run_command', run)
    elif publication == 'atom_map':
        # Let the real atom-map writer encounter an OS publication failure.
        (tmp_path / 'child/analysis/atom-order-manifest.json').mkdir(parents=True)
    elif publication == 'no_trajectory':
        spec['stages'][0]['mdp']['nstxout-compressed'] = 0
    elif publication == 'trr':
        spec['stages'][0]['mdp'].update({'nstxout-compressed': 0, 'nstxout': 5})
    saved, manifest_path = _run(tmp_path, native_system)
    manifest = json.loads(manifest_path.read_text())
    assert manifest['status'] == 'completed'
    assert manifest['native_endpoints']['sample']['step'] == 12
    assert manifest['native_endpoints']['sample']['time_ps'] == pytest.approx(-6.988)
    if publication in {'editconf', 'atom_map'}:
        assert publication.replace('editconf', 'representative_structure').replace('atom_map', 'atom_order_manifest') in manifest['publication_errors']
    if publication != 'no_trajectory':
        frames = json.loads((manifest_path.parent / manifest['artifacts']['trajectory_frame_map']['path']).read_text())['frames']
        assert [row['step'] for row in frames] == [0, 5, 10]
        assert frames[-1]['time_ps'] == pytest.approx(-6.99)
    if 'representative_structure' in manifest['artifacts']:
        assert manifest['artifacts']['representative_structure']['source_frame'] is None
    job, root = _collect(tmp_path, saved, manifest_path)
    if publication in {'analysis', 'bad_collection'}:
        python = os.environ['BMS_TEST_MDANALYSIS_PYTHON']
        child = tmp_path / 'analysis-child'
        code = ('from pathlib import Path; from scripts.bms_md.analysis import write_analysis_report; '
                'import sys; path, ok = write_analysis_report(Path(sys.argv[1]), Path(sys.argv[2]), '
                'runtime_sha256="3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"); '
                'assert ok, path.read_text()')
        subprocess.run([python, '-c', code, str(root / 'replicas/replica_0/manifest.json'),
            str(child / 'md_analysis_replica_0.json')], check=True)
        report = json.loads((child / 'md_analysis_replica_0.json').read_text())
        assert report['status'] == 'not_applicable'
        if publication == 'bad_collection':
            (child / 'md_analysis_replica_0.artifacts.json').write_text('[]')
        child_status = tmp_path / 'analysis-status.json'
        child_status.write_text(json.dumps({'child_output_dirs': [str(child)], 'completed': 1,
            'failed': 0, 'cancelled': 0, 'child_ids': ['analysis-child-0']}))
        collection = collect_analysis(child_status, root / 'manifest.json', root)
        assert collection['status'] == ('completed' if publication == 'analysis' else 'partial_failure')
    elif publication == 'bad_wham':
        (root / 'analysis').mkdir()
        (root / 'analysis/manifest.json').write_text(json.dumps({'job_id': job.id,
            'aggregate_manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
            'wham': ['partial']}))
    assert results.completion_barrier(job)['dynamics_state'] == 'completed'
    inventory = results.artifact_inventory(job)
    if publication == 'normal':
        assert results.summary(job)['trajectory_playback']['replicas'][0]['first_time_ps'] == -7
    assert any(row['name'] == 'coordinates' for row in inventory['artifacts'])
    if publication in {'trr', 'atom_map'}:
        playback = results.summary(job)['trajectory_playback']
        assert not playback['supported'] and playback['error']
    # Real finalizer, durable endpoint ingestion and API readback in scratch SQLite.
    _, maker = store
    _, segment_id = await _seed(maker, root, saved)
    async with maker() as session:
        parent = await session.get(Job, job.id)
        snapshot = await validate_and_finalize_md_job(parent, session)
        assert snapshot['dynamics_state'] == 'completed'
        if publication in {'bad_collection', 'bad_wham'}:
            assert snapshot['analysis_state'] == 'failed' and snapshot['analysis_error']
        await session.commit()
    async with maker() as reader:
        assert (await reader.get(Job, job.id)).status == 'completed'
        assert (await reader.get(MdRun, job.id)).normalized_request == saved
        assert (await get_md_summary(job.id, reader))['dynamics_state'] == 'completed'
        assert (await get_md_artifacts(job.id, reader))['artifacts']
        if publication == 'bad_wham':
            with pytest.raises(HTTPException) as error:
                await get_md_analysis(job.id, reader)
            assert error.value.detail['code'] == 'MD_ANALYSIS_COLLECTION_INVALID'
        else:
            report = await get_md_analysis(job.id, reader)
            if publication == 'bad_collection':
                assert report['collection']['collection_errors']
            elif publication == 'analysis':
                assert report['reports'][0]['status'] == 'not_applicable'
        segment = await reader.get(MdAttemptSegment, segment_id)
        assert segment.end_step == 12
        assert segment.end_time_ps == pytest.approx(-6.988)
