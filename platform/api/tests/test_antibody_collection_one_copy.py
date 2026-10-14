"""Tiny closed scientific payloads, real collectors and offline Nextflow."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import child_job_utils as owner

ROOT = Path(__file__).resolve().parents[3]
JAR = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR') or os.environ.get('ONE_COPY_NEXTFLOW_JAR') or Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
PDB = b'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C  \nTER\nEND\n'


def child(root, *, published=True):
    directory = root / ('pdb_files' if published else 'run/rfantibody/output')
    directory.mkdir(parents=True)
    for name, body in [('candidate.pdb', PDB), ('candidate.trb', pickle.dumps({'sample': 1}))]:
        path = directory / name
        path.write_bytes(body)
        if published:
            path.chmod(0o444)
    trajectory = directory / 'traj' / 'candidate.pdb'
    trajectory.parent.mkdir()
    trajectory.write_bytes(PDB)
    if published:
        trajectory.chmod(0o444)
    return root


def run_nf(tmp_path, harness, params, *, resume=False):
    config = tmp_path / 'local.config'
    config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    parameter_file = tmp_path / 'params.json'
    parameter_file.write_text(json.dumps(params))
    command = ['java', '--add-opens=java.base/java.util=ALL-UNNAMED', '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.base/java.io=ALL-UNNAMED', '-jar', str(JAR), '-trace', 'nextflow.processor.TaskProcessor,nextflow.cache', 'run', str(harness), '-c', str(config),
               '-params-file', str(parameter_file), '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false', '-dump-hashes', 'json', '-with-trace', str(tmp_path / ('resume-trace.tsv' if resume else 'trace.tsv'))]
    if resume:
        command.append('-resume')
    shim_dir = tmp_path / 'bin'
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / 'python3'
    if not shim.exists():
        shim.write_text(f'#!/bin/bash\ncase "$1" in\n"{ROOT}/scripts/result_ingester.py"|"{ROOT}/scripts/stage_reporter.py") exit 0;;\nesac\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)
    result = subprocess.run(command, cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true',
        NXF_DISABLE_CHECK_LATEST='true', PATH=str(shim_dir) + os.pathsep + os.environ['PATH']),
        capture_output=True, text=True, timeout=150)
    (tmp_path / ('resume.log' if resume else 'nextflow.log')).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_real_nextflow_normal_recovery_parity_and_cached_republish(tmp_path, monkeypatch, record_property):
    from services import gpu_orchestrator as recovery
    source = child(tmp_path / 'child')
    receipt = tmp_path / 'children.json'
    receipt.write_text(json.dumps({'child_output_dirs': [str(source)]}))
    output = tmp_path / 'normal'
    harness = tmp_path / 'collection.nf'
    harness.write_text(f"include {{ CollectChildOutputs; StageRFantibodyBackbones }} from '{ROOT}/workflows/antibody_denovo'\n"
        "workflow {\nCollectChildOutputs(Channel.value(file(params.fixture)), 'rfantibody')\n"
        "StageRFantibodyBackbones(CollectChildOutputs.out.pdbs.flatten().collect())\n}\n")
    params = {'code_root': str(ROOT), 'out_dir': str(output), 'fixture': str(receipt), 'job_id': 'parent'}
    run_nf(tmp_path, harness, params)
    recovered_root = tmp_path / 'recovered'
    job = SimpleNamespace(id='parent', name='parent', mode='antibody_refinement_pipeline',
        params={}, output_dir=str(recovered_root), completed_stages=[], stage_outputs={})
    result = recovery._recover_rfantibody_parent_after_child_wait(job, {'output_dirs': [str(source)]})
    assert result['count'] == 1
    for suffix in ('pdb', 'trb'):
        native = source / 'pdb_files' / f'candidate.{suffix}'
        for root in (output, recovered_root):
            collected = root / 'collected/rfantibody' / f'job0_candidate.{suffix}'
            raw = root / 'collected/rfantibody_raw' / collected.name
            assert raw.samefile(collected) and native.samefile(raw)
            assert not raw.is_symlink()
    manifests = list((tmp_path / 'work').rglob('collection_manifest.json'))
    manifest = json.loads(manifests[0].read_text())
    assert manifest['count'] == 1 and 'component_collection' in manifest
    published_manifest = output / 'collected/rfantibody/collection_manifest.json'
    assert json.loads(published_manifest.read_text()) == manifest
    assert not published_manifest.samefile(manifests[0])
    assert json.loads((output / 'collected/rfantibody_raw/rfantibody_stage_summary.json').read_text())['total_designs'] == 1
    names = [source / 'pdb_files/candidate.pdb'] + [r / 'collected' / stage / 'job0_candidate.pdb'
        for r in (output, recovered_root) for stage in ('rfantibody', 'rfantibody_raw')]
    identities = {(p.stat().st_dev, p.stat().st_ino) for p in names}
    assert len(identities) == 1
    record_property('retained_pdb_names', len(names))
    record_property('unique_pdb_payload_inodes', len(identities))
    record_property('pdb_allocated_bytes', names[0].stat().st_blocks * 512)
    # Deleting a child/controller namespace cannot invalidate independent names.
    shutil.rmtree(source)
    shutil.rmtree(recovered_root)
    shutil.rmtree(output)
    log = run_nf(tmp_path, harness, params, resume=True)
    assert 'Cached process' in log, log
    raw = output / 'collected/rfantibody_raw/job0_candidate.pdb'
    trb = raw.with_suffix('.trb')
    assert raw.read_bytes() == PDB
    assert pickle.loads(trb.read_bytes()) == {'sample': 1}
    shutil.rmtree(tmp_path / 'work')
    assert raw.read_bytes() == PDB
    from Bio.PDB import PDBParser
    assert len(list(PDBParser(QUIET=True).get_structure('candidate', raw).get_atoms())) == 1


def test_nextflow_publication_across_real_private_filesystems(tmp_path):
    """A namespace-local tmpfs exercises actual Nextflow copy publication."""
    source = child(tmp_path / 'child')
    receipt = tmp_path / 'children.json'
    receipt.write_text(json.dumps({'child_output_dirs': [str(source)]}))
    destination = tmp_path / 'separate-results'
    destination.mkdir()
    harness = tmp_path / 'collection.nf'
    harness.write_text(f"include {{ CollectChildOutputs }} from '{ROOT}/workflows/antibody_denovo'\n"
                       "workflow { CollectChildOutputs(Channel.value(file(params.fixture)), 'rfantibody') }\n")
    driver = tmp_path / 'crossfs.py'
    driver.write_text('''import importlib.util, json, shutil, subprocess, sys
from pathlib import Path
base, module_path = Path(sys.argv[1]), Path(sys.argv[2])
out = base / 'separate-results'
mounted = subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(out)], capture_output=True, text=True)
if mounted.returncode:
    print(mounted.stderr)
    raise SystemExit(77)
spec = importlib.util.spec_from_file_location('crossfs_receiving', module_path)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
params = {'code_root': str(owner.ROOT), 'out_dir': str(out), 'fixture': str(base / 'children.json'), 'job_id': 'parent'}
owner.run_nf(base, base / 'collection.nf', params)
source = base / 'child/pdb_files/candidate.pdb'
published = out / 'collected/rfantibody/job0_candidate.pdb'
assert source.stat().st_dev != published.stat().st_dev
assert source.read_bytes() == published.read_bytes()
shutil.rmtree(out / 'collected')
assert 'Cached process' in owner.run_nf(base, base / 'collection.nf', params, resume=True)
shutil.rmtree(base / 'work')
shutil.rmtree(base / 'child')
assert published.read_bytes() == owner.PDB
(base / 'crossfs-result.json').write_text(json.dumps({'different_devices': True, 'cached_republish': True, 'retained_after_work_deletion': True}))
''')
    result = subprocess.run(['unshare', '--user', '--map-root-user', '--mount', '--net',
                             sys.executable, str(driver), str(tmp_path), str(Path(__file__))],
                            cwd=tmp_path, capture_output=True, text=True, timeout=300)
    if result.returncode == 77 or (result.returncode and 'unshare failed' in result.stderr):
        pytest.skip('private user/mount namespace unavailable: ' + result.stderr + result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'crossfs-result.json').read_text()) == {
        'different_devices': True, 'cached_republish': True, 'retained_after_work_deletion': True,
    }


@pytest.mark.parametrize('readonly', [False, True])
def test_raw_resume_never_donates_even_readonly(tmp_path, readonly):
    source = child(tmp_path / 'child', published=False)
    native = source / 'run/rfantibody/output/candidate.pdb'
    if readonly:
        native.chmod(0o444)
    retained, _ = owner.collect_rfantibody_outputs([str(source)], tmp_path / 'collection', raw_destination=tmp_path / 'raw')
    collected = Path(retained['collected_pdbs'][0])
    raw = tmp_path / 'raw' / collected.name
    assert collected.samefile(raw) and not native.samefile(raw)
    native.chmod(0o644)
    native.write_bytes(b'producer resume changed')
    assert raw.read_bytes() == PDB
    replacement = tmp_path / 'new.pdb'
    replacement.write_bytes(PDB.replace(b'1.000', b'4.000', 1))
    owner.publish_collected_file(replacement, collected)
    assert raw.read_bytes() == PDB and collected.read_bytes() == replacement.read_bytes()
    assert not raw.samefile(collected)


def test_interrupted_publish_preserves_sibling_and_replays(tmp_path, monkeypatch):
    source = child(tmp_path / 'child') / 'pdb_files/candidate.pdb'
    destination = tmp_path / 'retained.pdb'
    owner.publish_collected_file(source, destination, published=True)
    inode = destination.stat().st_ino
    original = owner.os.replace
    def interrupted(*args):
        raise OSError('interrupted publication')
    monkeypatch.setattr(owner.os, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        owner.publish_collected_file(source, destination, published=True)
    assert destination.stat().st_ino == inode and destination.read_bytes() == PDB
    assert not list(tmp_path.glob('.*.publish'))
    monkeypatch.setattr(owner.os, 'replace', original)
    owner.publish_collected_file(source, destination, published=True)
    assert destination.samefile(source)


def test_cross_filesystem_retention_is_independent(tmp_path, monkeypatch):
    source = child(tmp_path / 'child') / 'pdb_files/candidate.pdb'
    def exdev(*args):
        raise OSError(errno.EXDEV, 'fixture boundary')
    monkeypatch.setattr(owner.os, 'link', exdev)
    target = tmp_path / 'target.pdb'
    owner.publish_collected_file(source, target, published=True)
    assert target.read_bytes() == PDB and not target.samefile(source)


@pytest.mark.parametrize('validation', [True, False])
def test_actual_closeout_ingestion_and_http_path_reader(tmp_path, monkeypatch, validation):
    monkeypatch.setenv('BMS_TEST_NEXTFLOW_JAR', str(JAR))
    spec = importlib.util.spec_from_file_location('antibody_root_proof', ROOT / 'tests/test_antibody_full_root_publication.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.test_native_full_root_publication_and_readback(tmp_path, monkeypatch, validation, 'antibody_denovo')


def test_validation_finalizer_and_readonly_gate_staging(tmp_path):
    import io
    import numpy as np
    payload = io.BytesIO()
    np.savez(payload, pae=np.array([[0.0]]))
    source = tmp_path / 'native_validation'
    source.mkdir()
    files = {'pdbs': ('candidate.pdb', PDB), 'cifs': ('candidate.cif', b'data_fixture\n'),
             'aligned_error': ('candidate.npz', payload.getvalue()),
             'scores': ('candidate.json', b'{"plddt": 80}')}
    manifest = {}
    for role, (name, body) in files.items():
        path = source / name
        path.write_bytes(body)
        if role != 'scores':
            path.chmod(0o444)
        manifest[role] = [str(path)]
    manifest_file = tmp_path / 'artifacts.json'
    manifest_file.write_text(json.dumps(manifest))
    pdb_list = tmp_path / 'pdbs.list'
    pdb_list.write_text(str(source / 'candidate.pdb') + '\n')
    output = tmp_path / 'published'
    legacy = tmp_path / 'legacy/output'
    legacy.mkdir(parents=True)
    (legacy / 'raw.pdb').write_bytes(PDB)
    (legacy / 'raw.trb').write_bytes(pickle.dumps({'sample': 2}))
    for path in legacy.iterdir():
        path.chmod(0o444)
    harness = tmp_path / 'finalization.nf'
    harness.write_text(f"include {{ FinalizeSequentialValidationOutputs }} from '{ROOT}/modules/antibody_output_finalization'\n"
        f"include {{ StageStructureValidationArtifacts; StageMaturationInputs; StageRFantibodyBackbones }} from '{ROOT}/workflows/antibody_denovo'\n"
        "workflow {\nFinalizeSequentialValidationOutputs(Channel.value(file(params.fixture)))\n"
        "StageStructureValidationArtifacts(Channel.value(file(params.pdb_list)))\n"
        "StageMaturationInputs(Channel.value(file(params.pdb)))\n"
        "StageRFantibodyBackbones(Channel.value(file(params.legacy)))\n}\n")
    params = {'code_root': str(ROOT), 'out_dir': str(output), 'fixture': str(manifest_file),
              'pdb_list': str(pdb_list), 'pdb': str(source / 'candidate.pdb'), 'job_id': 'parent',
              'api_url': 'http://callback.invalid', 'legacy': str(legacy / 'raw.pdb')}
    run_nf(tmp_path, harness, params)
    for role, (name, body) in files.items():
        final = output / 'pdb_files/validated_designs' / name
        gate = output / 'collected/structure_validation' / name
        assert final.read_bytes() == gate.read_bytes() == body
        assert final.samefile(source / name) is (role != 'scores')
        assert gate.samefile(final) is (role != 'scores')
    retained_raw = output / 'collected/rfantibody_raw/raw.pdb'
    assert retained_raw.read_bytes() == PDB
    assert not retained_raw.samefile(legacy / 'raw.pdb')
    assert not retained_raw.with_suffix('.trb').samefile(legacy / 'raw.trb')
    staged = output / 'ppiflow/input_pdbs/candidate.pdb'
    assert staged.samefile(source / 'candidate.pdb')
    shutil.rmtree(source)
    # Explicit input PDB channel cannot replay without its declared input;
    # published scientific paths nevertheless remain independent after deletion.
    shutil.rmtree(tmp_path / 'work')
    assert staged.read_bytes() == PDB
    assert (output / 'pdb_files/validated_designs/candidate.cif').read_bytes() == b'data_fixture\n'


@pytest.mark.parametrize('full_parent', [False, True])
def test_native_controller_closed_root(tmp_path, monkeypatch, full_parent):
    # A symlink exposes the existing offline binary; no native fixture tree copy.
    jar = Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    jar.parent.mkdir(parents=True, exist_ok=True)
    if not jar.exists():
        jar.symlink_to(JAR)
    from tests.test_antibody_remote_closeout import test_actual_native_spawn_child_and_global_collection
    test_actual_native_spawn_child_and_global_collection(tmp_path, monkeypatch, full_parent)


def test_interrupted_collection_recovery_replays_same_finished_leaves(tmp_path, monkeypatch):
    source = child(tmp_path / 'child')
    original = owner.os.replace
    calls = []
    def interrupted(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError('second publication interrupted')
        return original(src, dst)
    monkeypatch.setattr(owner.os, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        owner.collect_rfantibody_outputs([str(source)], tmp_path / 'collected', raw_destination=tmp_path / 'raw')
    assert (tmp_path / 'collected/job0_candidate.pdb').read_bytes() == PDB
    assert not list(tmp_path.rglob('*.publish'))
    monkeypatch.setattr(owner.os, 'replace', original)
    result, _ = owner.collect_rfantibody_outputs([str(source)], tmp_path / 'collected', raw_destination=tmp_path / 'raw')
    assert len(result['collected_pdbs']) == len(result['collected_trbs']) == 1
    assert (tmp_path / 'raw/job0_candidate.trb').samefile(source / 'pdb_files/candidate.trb')
