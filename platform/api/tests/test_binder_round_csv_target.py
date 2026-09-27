"""Consumer integration with real PPIFlow export; inert source arrays, no inference."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import Design, Job
from services.binder_diagnostic_selection import declared_targets
from services.binder_round_inputs import prediction_request, normalize_request
from services import binder_round as rounds
from services.ppiflow_generation import materialize_ppiflow_generation_request, _generation_design_fields
from test_binder_continuation import selected
from test_binder_pose_comparison import geometry, pdb_bytes
from test_binder_round_orchestration import envelope
from test_project_workflow_setups import setup_store


def producer_export(prepared, output, index, sequence):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('ppiflow_export_consumer_test', root / 'scripts/run_ppiflow_generation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = json.loads((Path(prepared['ppiflow_generation_request']) / 'request.json').read_text())
    context = {'request': request, 'target_writes': {str(output): {
        'source': [{'chain_id': 'R', 'amino_acid': aa, 'feature_index': i, 'input_index': i,
                    'auth_seq_id': i + 17, 'insertion_code': None} for i, aa in enumerate(sequence)],
        'output': {}}}}
    return module.target_metadata(context, index, output), request


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'hash', 'feature', 'row'])
async def test_native_csv_export_binds_input_without_reading_pickle(selected, monkeypatch, fault):
    import pickle
    _, session, root, _, tmp = selected
    feature = tmp / 'features.pkl'
    feature.write_bytes(b'opaque non-executable feature fixture')
    csv = tmp / 'sources.csv'
    csv.write_text('pdb_name,processed_path\nstate-a,features.pkl\n')
    params = {'input_csv': str(csv)}
    prepared = materialize_ppiflow_generation_request('protein_binder', params, tmp / 'prepared')
    root.model_id, root.mode, root.params = 'ppiflow', 'protein_binder', {**params, **prepared}
    sample = await session.get(Design, 'd0')
    Path(sample.pdb_path).write_bytes(pdb_bytes(geometry()[0]))
    metadata, request = producer_export(prepared, Path(sample.pdb_path), 0, 'AGS')
    fields = _generation_design_fields(root, {'candidate_key': 'source:0/sample:0',
        'source': request['source_rows'][0], **metadata}, SimpleNamespace(id='artifact', storage_path=sample.pdb_path))
    provenance = fields['provenance']
    if fault == 'hash':
        provenance['independent_target']['sha256'] = '0' * 64
    elif fault == 'feature':
        provenance['independent_target']['document']['source_feature_sha256'] = '0' * 64
    elif fault == 'row':
        provenance['source']['source_row_index'] = 99
    sample.provenance = provenance
    await session.commit()
    feature.unlink()
    csv.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError('coordinator must not unpickle native features')
    monkeypatch.setattr(pickle, 'load', forbidden)
    monkeypatch.setattr(pickle, 'loads', forbidden)
    targets = await declared_targets(root, session)
    if fault:
        assert targets == []
        assert root.status == 'completed' and Path(sample.pdb_path).is_file()
        return
    assert len(targets) == 1
    assert targets[0]['source_design_ids'] == ['d0']
    settings = normalize_request(envelope(binder_chains=['B'], target_chains=['T']))
    child, binding = prediction_request(root, root, sample, settings, ['B'], ['T'], targets[0])
    assert child.params['complex_components'] == [
        {'id': 'A', 'type': 'protein', 'sequence': 'GGGG'},
        {'id': 'B', 'type': 'protein', 'sequence': 'AGS'}]
    assert binding['input_components'][1]['source_chain'] == 'R'
    assert binding['input_components'][1]['reference_residues'] == [None] * 3
    assert binding['target_binding']['independent_target'] == metadata['independent_target']


@pytest.mark.asyncio
async def test_csv_states_are_associated_by_producer_not_cross_product(selected, setup_store):
    _, session, root, _, tmp = selected
    feature = tmp / 'features.pkl'
    feature.write_bytes(b'opaque feature fixture')
    csv = tmp / 'sources.csv'
    csv.write_text('pdb_name,processed_path\nstate-a,features.pkl\nstate-b,features.pkl\n')
    params = {'input_csv': str(csv)}
    prepared = materialize_ppiflow_generation_request('protein_binder', params, tmp / 'prepared')
    root.model_id, root.mode, root.params = 'ppiflow', 'protein_binder', {**params, **prepared}
    root.provenance = {rounds.REQUEST: envelope(binder_chains=['B'], target_chains=['T'])}
    original = await session.get(Design, 'd0')
    for index, sequence in enumerate(['AGS', 'GSA']):
        path = Path(original.pdb_path) if index == 0 else tmp / 'other.pdb'
        path.write_bytes(pdb_bytes(geometry()[0]))
        metadata, request = producer_export(prepared, path, index, sequence)
        sample = original if index == 0 else Design(id='csv-d1', job_id=root.id, name='csv-d1', pdb_path=str(path))
        # Explicit sequence-bearing fixture isolates consumer scheduling from the
        # native backbone stage, exercised in the designer integration suites.
        sample.artifact_class = 'binder_complex'
        sample.provenance = {'source': request['source_rows'][index], **metadata}
        session.add(sample)
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
    assert not result['errors'], result
    steps = list(result['steps'].values())
    assert len(steps) == 2
    assert {(s['metadata']['source_design_id'], s['metadata']['target_state']) for s in steps} == {
        ('d0', 'state-a'), ('csv-d1', 'state-b')}
    assert all(s['state'] == 'queued' for s in steps)
