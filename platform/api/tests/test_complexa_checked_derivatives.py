"""Checked Complexa PDB derivatives through the shared selected-source owner."""
import gzip
import hashlib
import json
from pathlib import Path
import pytest
from schemas import JobCreate
from tests.test_binder_source_materialization import cif
from test_project_generation_bound_jobs import isolated_core


@pytest.mark.asyncio
@pytest.mark.parametrize('task,operation', [('AME','generate'), ('monomer_motif_indexed','generate'), ('monomer_motif_unindexed','generate'), ('protein_binder','evaluate'), ('protein_binder','analysis')])
async def test_complexa_checked_derivative_all_pdb_receivers(isolated_core, tmp_path, monkeypatch, task, operation):
    import paths
    from services.native_generation_sources import prepare_native_sources
    roots = {'inputs': tmp_path/'owned/inputs', 'bms_results': tmp_path/'owned/results'}
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    original = roots['inputs']/'source.cif.gz'
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(gzip.compress(cif(models=(3,7)).encode()))
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    request = {'task': task, 'task_name': 'derivative', 'operation': operation, 'source': {'document_id': 'target'}}
    if task.startswith('monomer_motif'):
        table = original.parent/'motifs.csv'
        table.write_text('contig\nA1-2\n')
        request['source']['motif_csv_slot'] = 'table'
    if operation in ('evaluate','analysis'):
        request['evaluation_slots'] = ['target']
    job = JobCreate(name='derivative', model_id='complexa', mode='evaluate' if operation in ('evaluate','analysis') else 'generate',
                    params={'complexa_request': request, 'native_sources': {'target': {'path':'inputs/source.cif.gz','model_number':7}}})
    if task.startswith('monomer_motif'):
        job.params['native_sources']['table'] = {'kind':'file','path':'inputs/motifs.csv'}
    async with isolated_core() as session:
        await prepare_native_sources(job, session)
    closure = Path(job.params['complexa_request_dir'])
    if task.startswith('monomer_motif'):
        assert (closure/'motif.csv').read_bytes() == table.read_bytes()
        assert job.params['native_sources']['table']['kind'] == 'file'
    identity = json.loads((closure/'sources.json').read_text())['target']
    assert identity['original_sha256'] == digest
    assert identity['model_number'] == 7
    assert identity['consumed_format'] == 'pdb'
    assert (closure/'source.pdb').read_text().startswith(('ATOM','MODEL'))
    assert job.params['native_sources']['target']['path'].endswith('original.cif.gz')
    if operation in ('evaluate','analysis'):
        files = list((closure/'evaluation').glob('*.pdb'))
        assert len(files) == 1
        assert files[0].read_bytes() == (closure/'source.pdb').read_bytes()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
