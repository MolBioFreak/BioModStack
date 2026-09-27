"""Sealed filter/publication and consumer transport; no science or live Jobs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from database import Design, Job
from test_project_manager_adapters import stores
from test_boltzgen_generation_launch import target
from services import boltzgen_candidate_publication as publication
from services.binder_round_inputs import _target_correspondence

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from lib.filtering.evidence import correspondence_metadata, csv_metadata, npz_metadata
from filter_boltzgen import run_strict_filter


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence', ['native', 'absent', 'invalid', 'null', 'list'])
async def test_native_mapping_survives_filter_publication_reopen(stores, evidence, monkeypatch):
    root, _, factory = stores
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(root / 'artifacts'))
    source = root / 'inputs'
    source.mkdir(exist_ok=True)
    key = 'candidate-False'
    pdb = source / f'{key}.pdb'
    if evidence == 'native':
        fixture = os.environ.get('BMS_TEST_BOLTZGEN_CORRESPONDENCE_FIXTURE')
        if not fixture:
            pytest.skip('run root pinned-native suite and set its observed output fixture directory')
        fixture = Path(fixture)
        shutil.copyfile(fixture / pdb.name, pdb)
        shutil.copyfile(fixture / f'{key}.correspondence.json', source / f'{key}.correspondence.json')
    else:
        pdb.write_text('ATOM      1  CA  GLY A   1       1.000   2.000   3.000  1.00 20.00           C\nEND\n')
        if evidence == 'invalid':
            (source / f'{key}.correspondence.json').write_text('{invalid')
        elif evidence in {'null', 'list'}:
            (source / f'{key}.correspondence.json').write_text('null' if evidence == 'null' else '[]')
    expected = correspondence_metadata(source, key)
    assert bool(expected) == (evidence == 'native')
    decoded = json.loads(expected['target_residue_mapping']) if expected else None
    csv = source / 'metrics.csv'
    csv.write_text(f'id,design_ptm,affinity_probability,filter_rmsd\n{key},0.9,0.8,0\n')
    csv_metadata(csv, source, {key})
    metadata = source / f'confidence_{key}.json'
    assert json.loads(metadata.read_text()).get('target_residue_mapping') == expected.get('target_residue_mapping')
    # Selection metrics are explicitly inert fixtures, not claimed native scores.
    # Use the existing historical scalar path; correspondence remains the exact
    # installed writer's record, tested separately from scalar-source authority.
    metadata.write_text(json.dumps({'design_id': key, 'design_ptm': .9,
        'affinity_probability': .8, 'filter_rmsd': 0., 'source': 'boltzgen',
        'metrics_source': 'npz', **expected}))
    output = root / 'results'
    filtered = output / 'collected/boltzgen_filtered'
    filtered.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(pdb)], jsons=[str(metadata)], out_dir=str(filtered),
        filter_biased='false', metrics_override=None, additional_filters=None, size_buckets=None,
        boltzgen_min_plddt=None, boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=1, alpha=0))
    async with factory() as db:
        job = Job(id='correspondence', name='inert-native-output', model_id='boltzgen', mode='protein_binder',
                  params={}, output_dir=str(output), provenance={'core_protein_scientific_contract': 1})
        db.add(job)
        await db.flush()
        assert await publication.ingest(job, output, db) == 1
    # Fresh session readback and immutable replay, not a transient ORM projection.
    async with factory() as db:
        job = await db.get(Job, 'correspondence')
        design = await db.scalar(select(Design).where(Design.job_id == job.id))
        assert design.provenance.get('target_residue_mapping') == decoded
        first = await publication.read_published_generation_results(job, db)
        assert await publication.ingest(job, output, db) == 0
        assert await publication.read_published_generation_results(job, db) == first
        if expected:
            for group in decoded:
                mapped = _target_correspondence(design.provenance['target_residue_mapping'], group['source_sha256'])
                assert mapped
                for row in group['residues']:
                    src = row['source']
                    assert mapped[(src['chain_id'], src['auth_seq_id'], src['insertion_code'])] == row['output']
            assert _target_correspondence(design.provenance['target_residue_mapping'], '0' * 64) == {}
        else:
            assert 'target_residue_mapping' not in design.provenance


def test_correspondence_wrong_document_and_npz_retention(tmp_path):
    import numpy as np
    key = 'emitted-key'
    pdb = tmp_path / f'{key}.pdb'
    pdb.write_bytes(b'original bytes')
    mapping = [{'source_sha256': 'a' * 64, 'residues': []}]
    sidecar = tmp_path / f'{key}.correspondence.json'
    sidecar.write_text(json.dumps({'candidate_key': key, 'structure_sha256': hashlib.sha256(pdb.read_bytes()).hexdigest(),
                                  'target_residue_mapping': mapping}))
    np.savez(tmp_path / f'{key}.npz', design_ptm=np.array(.9))
    npz_metadata(tmp_path, tmp_path, {key})
    assert json.loads(json.loads((tmp_path / f'confidence_{key}.json').read_text())['target_residue_mapping']) == mapping
    pdb.write_bytes(b'changed bytes')
    assert correspondence_metadata(tmp_path, key) == {}
    assert correspondence_metadata(tmp_path, 'different-key') == {}


@pytest.mark.parametrize('parent', ['native', 'historical'])
@pytest.mark.asyncio
async def test_compiled_wrapper_dependencies_survive_remote_source_archive(tmp_path, parent, target):
    from component_runtime import SourceIdentity
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    from services.nextflow import compile_nextflow_invocation
    from services.remote_execution import bundle
    from test_antibody_remote_closeout import CASES
    import subprocess
    root = SCRIPTS.parent
    if parent == 'native':
        model, mode = 'boltzgen', 'protein_binder'
        params = {'target_pdb': str(target)}
    else:
        model, mode = 'antibody_denovo', CASES[0][0]
        params = dict(CASES[0][3])
    normalized = normalize_job_request(JobCreate(name='source-transport', model_id=model, mode=mode, params=params))
    if parent == 'native':
        from services.boltzgen_scaffolding import prepare_boltzgen_generation_input
        normalized.params = await prepare_boltzgen_generation_input(normalized.params,
            tmp_path / 'prepared', allowed_input_roots=(target.parent,))
    invocation = compile_nextflow_invocation(model, mode, normalized.params,
        str(tmp_path / 'output'), job_id='offline-ledger', requested_params=params,
        source_identity=SourceIdentity.from_checkout(root))
    expected = {'scripts/run_boltzgen_wrapper.py', 'scripts/boltzgen_source_correspondence.py',
                'scripts/lib/boltzgen_native_source.json'}
    dependencies = {item.logical_id: item.relative_path for item in invocation.execution_plan.dependencies}
    stage = next(item for item in invocation.execution_plan.metadata.static_components
                 if item.authority == 'modules/boltzgen.nf:RunBoltzGen')
    assert expected <= {dependencies[key] for key in stage.dependency_ids}
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    returned = tmp_path / 'remote-source'
    archive_digest = bundle._staged_source_archive(root, tmp_path / 'data', revision, returned)
    assert archive_digest == hashlib.sha256((returned / '.bms-source.tar.gz').read_bytes()).hexdigest()
    for relative in expected:
        assert (returned / relative).read_bytes() == (root / relative).read_bytes()
    assert not (tmp_path / 'output').exists()
