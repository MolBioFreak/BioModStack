"""Selected LigandMPNN context leaf: exact artifact/round, no scientific verdict."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from services.ligandmpnn_interface_context import read_context_result

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / 'scripts/run_ligandmpnn_interface_context.py'
STAGER = ROOT / 'scripts/stage_ligandmpnn_interface_context.py'
WORKFLOW = ROOT / 'workflows/ligandmpnn_interface_context.nf'
JAR = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load(RUNNER, 'run_ligandmpnn_interface_context')
stager = load(STAGER, 'stage_ligandmpnn_interface_context')


def fixture(tmp_path):
    source = ROOT / 'platform/api/assets/md/admitted_structures/1AKI.pdb'
    atoms = [line for line in source.read_text().splitlines(keepends=True)
             if line.startswith('ATOM  ') and line[16] == ' ' and line[21] == 'A']
    ids = list(dict.fromkeys(line[22:27] for line in atoms))[:60]
    assert len(ids) == 60
    pdb = tmp_path / 'candidate.pdb'
    pdb.write_text(''.join(line[:21] + ('B' if line[22:27] in ids[30:] else 'A') + line[22:]
                           for line in atoms if line[22:27] in ids) + 'END\n')
    request = {'candidate_id': 'selected-candidate', 'round_id': 'child-round',
               'source_sha256': hashlib.sha256(pdb.read_bytes()).hexdigest(),
               'structure_path': '/original/immutable/candidate.pdb',
               'binder_chain': 'B', 'target_chain': 'A',
               'target_patch': ['A' + ids[i].strip() for i in (3, 4)],
               'seed': 7, 'samples': 1, 'temperature': 0.1}
    snapshot = tmp_path / 'request.json'
    snapshot.write_text(json.dumps(request))
    return pdb, snapshot, request


def test_model_owned_controls_and_legacy_modes_unchanged():
    model = yaml.safe_load((ROOT / 'platform/api/config/models/ligandmpnn.yaml').read_text())
    assert {mode['id'] for mode in model['modes']} == {
        'ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware', 'interface_context'}
    mode = next(m for m in model['modes'] if m['id'] == 'interface_context')
    assert set(mode['params']) == {'binder_chain', 'target_chain', 'target_patch',
                                   'seed', 'samples', 'temperature'}
    controls = {p['name']: p for p in model['params']}
    assert controls['target_patch']['type'] == 'array'
    assert controls['seed']['maximum'] == 2147483647
    assert controls['samples']['maximum'] == 16
    assert model['experimental'] is False  # historical modes are not reclassified


def test_staging_retains_identity_and_all_scientific_controls(tmp_path):
    pdb, snapshot, original = fixture(tmp_path)
    effective_path = tmp_path / 'effective.json'
    before = (pdb.read_bytes(), snapshot.read_bytes())
    effective = stager.stage(snapshot, pdb, effective_path)
    assert {k: v for k, v in effective.items() if k != 'structure_path'} == {
        k: v for k, v in original.items() if k != 'structure_path'}
    assert effective['structure_path'] == str(pdb.resolve())
    assert (pdb.read_bytes(), snapshot.read_bytes()) == before
    assert json.loads(effective_path.read_text()) == effective
    pdb.write_text(pdb.read_text().replace('END', 'REMARK changed\nEND'))
    with pytest.raises(ValueError, match='digest'):
        stager.stage(snapshot, pdb, effective_path)


def run_nextflow(tmp_path, records, *, native=False):
    manifest = tmp_path / 'selected.json'
    manifest.write_text(json.dumps(records))
    output = tmp_path / 'results'
    env = dict(os.environ, NXF_OFFLINE='true', NXF_ANSI_LOG='false',
               NXF_HOME=str(tmp_path / 'nxf-home'))
    cmd = ['java', '-jar', str(JAR), 'run', str(WORKFLOW), '-profile', 'workstation_ryzen7960x',
           '-w', str(tmp_path / 'work'), '--interface_context_manifest', str(manifest),
           '--out_dir', str(output), '--code_root', str(ROOT),
           '--container_dir', str(Path(os.environ.get('BMS_TEST_FOUNDRY_IMAGE',
                                                     '/mnt/BioModStack/apptainer/foundry.sif')).parent)]
    result = subprocess.run(cmd, cwd=tmp_path, env=env, text=True,
                            capture_output=True, timeout=300 if native else 120)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    return output, result


@pytest.mark.skipif(not JAR.is_file(), reason='pinned Nextflow jar not installed')
def test_no_selection_does_not_prepare_or_run_foundry(tmp_path):
    output, result = run_nextflow(tmp_path, [])
    assert not output.exists()
    assert 'RunLigandMPNNInterfaceContext' not in result.stdout


@pytest.mark.skipif(not JAR.is_file() or not shutil.which('apptainer'), reason='native tools unavailable')
def test_selected_nextflow_native_context_receipt(tmp_path):
    image = os.environ.get('BMS_TEST_FOUNDRY_IMAGE')
    if not image:
        pytest.skip('set BMS_TEST_FOUNDRY_IMAGE for installed Foundry run')
    pdb, snapshot, request = fixture(tmp_path)
    original_source = pdb.read_bytes()
    output, result = run_nextflow(tmp_path, [dict(invocation_id='check-1',
                                                  request_path=str(snapshot), source_path=str(pdb))], native=True)
    target = output / 'ligandmpnn_interface_context/check-1/context_result'
    assert (target / 'result.json').is_file()
    receipt = read_context_result(target / 'result.json', candidate_id=request['candidate_id'],
                                  round_id=request['round_id'], source_sha256=request['source_sha256'])
    assert receipt['status'] == 'completed_unclassified'
    assert receipt['qualification'] == 'unqualified'
    assert receipt['target_patch'] == request['target_patch']
    assert receipt['seed'] == request['seed']
    assert receipt['samples'] == request['samples']
    assert pdb.read_bytes() == original_source
