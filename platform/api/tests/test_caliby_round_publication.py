"""Exact native Caliby document return; inert structures, no inference."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import select

from database import Design, Job
from services.result_ingester import ingest_job_results
from test_binder_round_lineage import source, step
from test_core_protein_scientific_admission import admission
from test_selected_binder_publication import script, PDB


@pytest.mark.asyncio
@pytest.mark.parametrize('native_state', ['ready', 'missing', 'changed', 'outside'])
async def test_caliby_native_identity_survives_return_without_gating_candidate(admission, tmp_path, monkeypatch, native_state):
    gemmi = pytest.importorskip('gemmi')
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / 'scripts'))
    await source(admission, tmp_path)
    worker = tmp_path / 'worker'
    terminal = worker / 'collected/binder_generation/caliby'
    terminal.mkdir(parents=True)
    native = worker / 'sample.cif'
    gemmi.read_pdb_string(PDB.replace('ALA', 'GLY')).make_mmcif_document().write_file(str(native))
    native_bytes = native.read_bytes()
    results = {'example_id': ['source-explicit'], 'out_pdb': [str(native)],
               'seq': ['G'], 'input_seq': ['A'], 'U': [0.0]}
    manifest = script('caliby_runtime').normalize_sampling_results(
        results=results, output_pdb_dir=terminal, output_meta_dir=terminal,
        prefix='caliby', source='caliby', stage_mode='sequence_design',
        extra_metadata={'terminal_producer': 'caliby', 'validation_status': 'unvalidated'})
    script('run_caliby_sequence_design').annotate_native_outputs(manifest, results, 'A', '')
    returned = tmp_path / 'returned'
    shutil.copytree(worker / 'collected', returned / 'collected')
    shutil.rmtree(worker)
    published = returned / 'collected/binder_generation/caliby'
    native_path = published / manifest[0]['native_output_structure']['relative_path']
    if native_state == 'missing':
        native_path.unlink()
    elif native_state == 'changed':
        native_path.write_bytes(b'different optional artifact')
    elif native_state == 'outside':
        metadata_path = published / 'generator_caliby_0001.json'
        metadata = json.loads(metadata_path.read_text())
        metadata['native_output_structure']['relative_path'] = '../../outside.cif'
        metadata_path.write_text(json.dumps(metadata))
    child = Job(id='caliby-child', name='caliby-child', model_id='caliby_binder',
                mode='design', status='completed', params={}, output_dir=str(returned),
                provenance={'binder_round_step': step(binder_chains=['A'], target_chains=[])})
    admission.add(child)
    await admission.commit()
    assert await ingest_job_results(child.id, returned, admission) == 1
    row = await admission.scalar(select(Design).where(Design.job_id == child.id))
    assert row.parent_design_id == 'candidate-explicit'
    assert Path(row.pdb_path) == published / 'caliby_0001.pdb'
    assert row.plddt_overall is None and row.iptm is None
    generator = row.provenance['generator']
    assert generator['sequence'] == 'G' and generator['input_sequence'] == 'A'
    assert generator['designed_chain_sequences'] == {'A': 'G'}
    identity = generator['native_output_structure']
    assert identity['example_id'] == 'source-explicit'
    assert identity['sha256'] == hashlib.sha256(native_bytes).hexdigest()
    if native_state == 'ready':
        assert identity['state'] == 'ready'
        assert Path(identity['path']) == native_path
        assert native_path.read_bytes() == native_bytes
    else:
        assert identity['state'] == 'unavailable'
        assert identity['path'] is None and identity['reason']
    assert await ingest_job_results(child.id, returned, admission) == 0
