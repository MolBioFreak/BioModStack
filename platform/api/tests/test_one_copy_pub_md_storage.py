"""Storage/control qualification only: real BMS writers, inert engine boundaries."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bms_md import aggregate_children as collection
from scripts.bms_md.chemistry.prepare import (
    REQUIRED_OUTPUTS, build_preparation_bundle, retain_prepared_file, verify_preparation_bundle,
)
from scripts.bms_md.contract import build_run_manifest, normalize_job_config, write_atom_order_manifest
from scripts.bms_md.gromacs_pipeline import _consume_preparation_bundle
from scripts.bms_md.runner import StageLedger

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(root):
    return {str(p.relative_to(root)): (digest(p), p.stat().st_ino, p.stat().st_mode)
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / 'input.pdb'
    source.write_text('END\n')
    lock = tmp_path / 'runtime.lock'
    lock.write_text('inert fixture lock\n')

    def inert_chemistry(command, *, cwd, **kwargs):
        for name in REQUIRED_OUTPUTS:
            if name != 'source.pdb':
                (cwd / name).write_text(f'inert prepared {name}\n')
        (cwd / 'preparation_report.json').write_text('{}')
        return subprocess.CompletedProcess(command, 0, '', '')

    bundle = tmp_path / 'bundle'
    build_preparation_bundle(source_structure=source, destination=bundle,
        profile_id='amber_ff19sb_opc_protein_v1', profile_sha256='a' * 64,
        runtime_lock=lock, runner=inert_chemistry)
    return bundle


def test_prepared_replicas_share_retained_inputs_not_outputs(prepared, tmp_path, record_property):
    before = identity(prepared)
    replicas = []
    for index in range(2):
        out = tmp_path / f'replica-{index}'
        out.mkdir()
        paths = _consume_preparation_bundle({}, prepared, out, StageLedger(out / 'stage_state.json'))
        replicas.append(paths)
        for donor, retained in [('system.gro', paths[0]), ('system.top', paths[1]),
                                ('posre.itp', paths[2][-1])]:
            assert os.path.samefile(prepared / donor, retained)
            assert not retained.is_symlink()
        native = out / 'production.gro'
        native.write_text('new engine output\n')
        assert native.stat().st_ino != paths[0].stat().st_ino
    assert identity(prepared) == before
    shared = [prepared / 'system.gro', replicas[0][0], replicas[1][0]]
    record_property('prepared_coordinate_payload', json.dumps({
        'names': len(shared), 'inodes': len({p.stat().st_ino for p in shared}),
        'bytes': shared[0].stat().st_size, 'allocated_bytes': shared[0].stat().st_blocks * 512}))
    # Removing one consumer and even the source owner cannot break the sibling.
    shutil.rmtree(replicas[0][0].parent.parent)
    shutil.rmtree(prepared)
    assert replicas[1][0].read_text() == 'inert prepared system.gro\n'


@pytest.mark.parametrize('exception', ['writable', 'cross_device', 'retry'])
def test_preparation_legacy_and_private_replacement(prepared, tmp_path, monkeypatch, exception):
    source = prepared / 'system.gro'
    destination = tmp_path / 'retained.gro'
    if exception == 'writable':
        source.chmod(0o644)
    elif exception == 'cross_device':
        def exdev(*args, **kwargs):
            raise OSError(errno.EXDEV, 'fixture cross-device boundary')
        monkeypatch.setattr(os, 'link', exdev)
    retain_prepared_file(source, destination)
    before = source.read_bytes()
    if exception == 'retry':
        assert os.path.samefile(source, destination)
        fresh = tmp_path / 'replacement.gro'
        fresh.write_bytes(b'private changed preparation')
        retain_prepared_file(fresh, destination)
        assert source.read_bytes() == before
        assert destination.read_bytes() == fresh.read_bytes()
    else:
        assert not os.path.samefile(source, destination)
        assert destination.read_bytes() == before


def emit_native(root, *, engine='gromacs', status='completed'):
    """Use the actual native manifest/atom-map writers with known format bytes."""
    root.mkdir(parents=True, exist_ok=True)
    config = {'schema': 'bms.md.job.v1', 'engine': engine, 'job_id': 'storage-parent',
              'replicas': 1, 'random_seed': 123,
              'input': {'coordinates': str(root / 'system.gro'), 'topology': str(root / 'system.top')},
              'engine_runtime': {'sif_sha256': 'a' * 64, 'image_name': 'inert.sif'}}
    fixture = ROOT / 'tests/fixtures/bms_md_analysis/gromacs_1u19_format_smoke'
    for source, target in [('system.gro', 'system.gro'), ('trajectory.xtc', 'production.xtc')]:
        shutil.copyfile(fixture / source, root / target)
    (root / 'production.cpt').write_bytes(b'inert checkpoint seed')
    (root / '.private-continuation').mkdir()
    (root / '.private-continuation/seed').write_bytes(b'must stay at original attempt')
    (root / 'undeclared-cache').write_bytes(b'not a terminal artifact')
    atom_map = root / 'atom-order.json'
    _, atom_identity = write_atom_order_manifest(root / 'system.gro', atom_map)
    artifacts = {'coordinates': root / 'system.gro', 'trajectory': root / 'production.xtc',
                 'checkpoint': root / 'production.cpt', 'atom_order_manifest': atom_map}
    manifest = build_run_manifest(output_dir=root, job_config=config, replica_index=0,
        engine_version='inert-storage-boundary', platform='CPU', artifacts=artifacts, stages={})
    config = normalize_job_config(config)
    manifest['status'] = status
    from scripts.bms_md.runner import replica_seed
    manifest['replica_seed'] = replica_seed(config['random_seed'], 0)
    for key, role in [('coordinates', 'analysis_topology'), ('trajectory', 'analysis_trajectory'),
                      ('atom_order_manifest', 'atom_order_manifest')]:
        manifest['artifacts'][key].update(semantic_role=role, atom_order_identity=atom_identity)
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return config, manifest


def collect(tmp_path, child):
    status = tmp_path / 'children.json'
    status.write_text(json.dumps({'child_output_dirs': [str(child)], 'completed': 1,
                                  'child_ids': ['replica-0']}))
    parent = tmp_path / 'parent'
    return parent, collection.collect_children(status, parent)


def test_declared_collection_reopens_known_coordinates_and_survives_continuation(tmp_path, monkeypatch, record_property):
    from services.md import results
    child = tmp_path / 'child/native'
    config, manifest = emit_native(child)
    before = identity(child)
    parent, aggregate = collect(tmp_path, child.parent)
    assert identity(child) == before
    target = parent / aggregate['replicas'][0]['manifest']
    declared = {'manifest.json'} | {r['path'] for r in manifest['artifacts'].values()}
    assert {str(p.relative_to(target.parent)) for p in target.parent.rglob('*') if p.is_file()} == declared
    for name in declared:
        assert (target.parent / name).read_bytes() == (child / name).read_bytes()
        assert not os.path.samefile(target.parent / name, child / name)
    monkeypatch.setenv('BMS_MD_RESULT_ROOT', str(tmp_path))
    job = SimpleNamespace(id='storage-parent', model_id='molecular_dynamics', output_dir=str(parent),
                          child_output_dir=None, params={'md_job_spec': config}, provenance={})
    inventory = results.artifact_inventory(job)
    assert inventory['artifacts']
    for row in inventory['artifacts']:
        artifact, handle = results.open_verified_artifact(job, row['id'])
        with handle:
            assert hashlib.sha256(handle.read()).hexdigest() == artifact.sha256
    # Known physical coordinate and trajectory paths remain a contained pair.
    assert (target.parent / 'system.gro').read_bytes() == (child / 'system.gro').read_bytes()
    accepted = identity(parent)
    record_property('terminal_snapshot', json.dumps({
        'declared_names': len(declared), 'trajectory_source_inode': (child / 'production.xtc').stat().st_ino,
        'trajectory_parent_inode': (target.parent / 'production.xtc').stat().st_ino,
        'trajectory_bytes': (child / 'production.xtc').stat().st_size,
        'private_seed_collected': (target.parent / '.private-continuation').exists()}))
    collect(tmp_path, child.parent)
    assert identity(parent) == accepted
    with (child / 'production.xtc').open('ab') as stream:
        stream.write(b'append from private continuation')
    (child / 'production.cpt').write_bytes(b'new continuation checkpoint')
    assert identity(parent) == accepted
    shutil.rmtree(child.parent)
    assert identity(parent) == accepted


def test_interrupted_collection_does_not_publish_partial_or_touch_source(tmp_path, monkeypatch):
    child = tmp_path / 'child/native'
    emit_native(child)
    before = identity(child)
    real = collection.publish_file_immutable
    calls = []
    def interrupted(source, destination, **kwargs):
        calls.append(str(source))
        if len(calls) == 2:
            raise OSError('inert collection interruption')
        return real(source, destination, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(collection, 'publish_file_immutable', interrupted)
        with pytest.raises(OSError, match='interruption'):
            collect(tmp_path, child.parent)
    assert identity(child) == before
    assert not (tmp_path / 'parent/replicas/replica_0').exists()
    assert not (tmp_path / 'parent/manifest.json').exists()
    assert not list((tmp_path / 'parent/replicas').glob('.*tmp*'))
    collect(tmp_path, child.parent)
    assert identity(child) == before


@pytest.mark.parametrize('status', ['paused', 'failed', 'running'])
def test_nonterminal_native_output_is_not_collected_or_linked(tmp_path, status):
    child = tmp_path / 'child/native'
    emit_native(child, status=status)
    before = identity(child)
    with pytest.raises(ValueError, match='not completed'):
        collect(tmp_path, child.parent)
    assert identity(child) == before
    assert not (tmp_path / 'parent').exists()


def test_native_writer_declares_prepared_closure_and_parent_reuses_it(
        prepared, tmp_path, monkeypatch, native_double, record_property):
    from test_md_native_engine import config as native_config
    from scripts.bms_md.gromacs_pipeline import run_gromacs_job
    config = native_config(tmp_path)
    config['input'] = {'structure': str(prepared / 'source.pdb')}
    config['chemistry'] = {'profile_id': 'amber_ff19sb_opc_protein_v1', 'profile_sha256': 'a' * 64}
    before = identity(prepared)
    child = tmp_path / 'child/native'
    manifest_path = run_gromacs_job(tmp_path / 'unused', child, _prepared_config=config,
                                    preparation_bundle=prepared, gmx_binary='fixture-gmx')
    manifest = json.loads(manifest_path.read_text())
    assert {record['path'] for name, record in manifest['artifacts'].items()
            if name.startswith('preparation_')} == {'system/prepared.gro', 'system/topol.top', 'system/posre.itp'}
    before_child = identity(child)
    parent, aggregate = collect(tmp_path, child.parent)
    for relative, donor in [('system/prepared.gro', 'system.gro'), ('system/topol.top', 'system.top'),
                            ('system/posre.itp', 'posre.itp')]:
        assert os.path.samefile(parent / 'replicas/replica_0' / relative, prepared / donor)
    assert identity(prepared) == before and identity(child) == before_child
    assert not os.path.samefile(parent / 'replicas/replica_0/sample/sample.cpt', child / 'sample/sample.cpt')
    record_property('native_manifest_preparation_reused', 3)


# Reuse the existing explicit inert GROMACS subprocess boundary; the pipeline,
# stage ledger, native manifest writer and collector are the product functions.
from test_md_native_engine import native_double


def test_openmm_consumes_shared_preparation_then_keeps_native_output_private(prepared, tmp_path, monkeypatch):
    from scripts.bms_md import openmm_pipeline
    before = identity(prepared)
    seen = []
    class StopBeforeNumericalEngine(Exception):
        pass
    class App:
        @staticmethod
        def GromacsGroFile(path):
            seen.append(Path(path))
            assert os.path.samefile(path, prepared / 'system.gro')
            return SimpleNamespace(getPeriodicBoxVectors=lambda: None)
        @staticmethod
        def GromacsTopFile(path, **kwargs):
            seen.append(Path(path))
            assert os.path.samefile(path, prepared / 'system.top')
            assert os.path.samefile(Path(kwargs['includeDir']) / 'posre.itp', prepared / 'posre.itp')
            raise StopBeforeNumericalEngine
    monkeypatch.setattr(openmm_pipeline, 'assert_single_cuda_device', lambda _: {})
    monkeypatch.setattr(openmm_pipeline, '_require_openmm_cuda', lambda: (None, App, None, None, None, None, 'inert'))
    config = {'schema': 'bms.md.job.v2', 'engine': 'openmm', 'replicas': 2}
    for index in range(2):
        out = tmp_path / f'openmm-{index}'
        with pytest.raises(StopBeforeNumericalEngine):
            openmm_pipeline.run_openmm_job(tmp_path / 'unused', out, replica_index=index,
                                           _prepared_config=config, preparation_bundle=prepared)
        assert verify_preparation_bundle(out / 'preparation')['bundle_sha256'] == verify_preparation_bundle(prepared)['bundle_sha256']
        assert not (out / 'production').exists()
        assert (out / 'job.normalized.json').stat().st_nlink == 1
    assert len(seen) == 4 and identity(prepared) == before


def test_native_manifest_retains_nested_topology_includes_not_worker_caches(tmp_path):
    child = tmp_path / 'child/native'
    config, _ = emit_native(child)
    topology_root = child / '.worker_inputs/topology_closure'
    topology_root.mkdir(parents=True)
    topology = topology_root / 'topology.top'
    topology.write_text('#include "nested/chain.itp"\n')
    include = topology_root / 'nested/chain.itp'
    include.parent.mkdir()
    include.write_text('inert nested include\n')
    config['input']['topology_closure'] = {'root': str(topology_root), 'files': [{'path': 'nested/chain.itp'}]}
    manifest = build_run_manifest(output_dir=child, job_config=config, replica_index=0,
        engine_version='inert', platform='CPU', artifacts={'topology': topology}, stages={})
    manifest['status'] = 'completed'
    (child / 'manifest.json').write_text(json.dumps(manifest))
    parent, _ = collect(tmp_path, child.parent)
    target = parent / 'replicas/replica_0/.worker_inputs/topology_closure'
    assert (target / 'topology.top').read_text() == topology.read_text()
    assert (target / 'nested/chain.itp').read_text() == include.read_text()
    assert not (parent / 'replicas/replica_0/undeclared-cache').exists()


def test_legacy_external_resume_keeps_original_seed_and_only_retains_terminal_files(tmp_path):
    source = tmp_path / 'old-work/replica_0'
    emit_native(source)
    original = identity(source)
    result = tmp_path / 'child'
    retained = collection.durable_run_output(source, result)
    assert retained == result / 'native'
    assert identity(source) == original
    assert not (retained / '.private-continuation').exists()
    assert not (retained / 'undeclared-cache').exists()
    assert not os.path.samefile(source / 'production.xtc', retained / 'production.xtc')
    accepted = identity(retained)
    assert collection.durable_run_output(source, result) == retained
    assert identity(retained) == accepted
    assert collection.durable_run_output(retained, result) == retained
    assert identity(retained) == accepted
    with (source / 'production.xtc').open('ab') as stream:
        stream.write(b'legacy continuation remains private')
    assert identity(retained) == accepted
