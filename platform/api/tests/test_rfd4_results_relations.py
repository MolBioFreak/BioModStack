"""Publisher/cold reader relations, plus opt-in retained scientific evidence replay."""
import copy
import csv
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from database import Job
from test_remote_lifecycle_gaps import store
from scripts.bms_rfd4.publication import NativeObserver, publish
from services.rfd4_proteina_results import publish_native_generation, read_published_generation_page


def test_exact_path_fields_and_ambiguous_ids(tmp_path):
    for name in ['generated.cif', 'arbitrary.cif', 'input.cif']:
        (tmp_path / name).write_text('data_transport_fixture\n')
    observer = NativeObserver()
    observer.events = [{'event': 'native_writer', 'example_id': 'same', 'path': str(tmp_path / 'generated.cif'), 'population': 'generated'}]
    metrics = tmp_path / 'native/inference_metrics'; metrics.mkdir(parents=True)
    def write(rows):
        with (metrics / 'per_example_metrics.csv').open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    row = {'example_id': 'same', 'input_cif_path': str(tmp_path / 'input.cif'), 'af2.refolded_structure_path': str(tmp_path / 'arbitrary.cif'), 'af2.confidence_json_path': '/outside/missing.json'}
    write([row])
    result = publish(tmp_path, {'mode': 'generate'}, {'execution_status': 'completed'}, observer)
    relations = result['records'][0]['relations']
    assert [(r['role'], r['document']) for r in relations] == [('input', 'input.cif'), ('refold', 'arbitrary.cif'), ('refold_confidence', None)]
    write([row, dict(row, input_cif_path='/other/input.cif')])
    result = publish(tmp_path, {'mode': 'generate'}, {'execution_status': 'completed'}, observer)
    assert result['records'][0]['relations'] == []
    assert result['records'][0]['metric_association']['state'] == 'ambiguous'


def test_worker_relations_preserve_dataset_task_ambiguity(tmp_path):
    observer = NativeObserver()
    observer.events = [{'event': 'native_writer', 'example_id': 'same', 'dataset_name': dataset,
                        'path': str(tmp_path / f'{dataset}.cif')} for dataset in ('a', 'b')]
    relation = {'event': 'native_relation', 'role': 'trajectory', 'example_id': 'same',
                'path': str(tmp_path / 'not-retained.xtc'), 'producer_field': 'transport-only'}
    observer.events.append(relation)
    result = publish(tmp_path, {'mode': 'generate'}, {'execution_status': 'completed'}, observer)
    assert all(not row['relations'] for row in result['records'])
    assert all(row['ambiguous_relations'][0]['matching_record_count'] == 2 for row in result['records'])
    relation['dataset_name'] = 'b'
    result = publish(tmp_path, {'mode': 'generate'}, {'execution_status': 'completed'}, observer)
    assert not result['records'][0]['relations']
    assert result['records'][1]['relations'][0]['availability'] == 'not_retained'


def test_source_metric_definitions_keep_counts_and_unknown_distinct():
    from scripts.bms_rfd4.metric_definitions import descriptors
    raw = {'sse_distribution.num_ss_elements_coi': '0', 'sequence_adherence.n_violations': '0',
           'sequence_probability_adherence.n_one_conditions': '2', 'unknown.metric': '1'}
    result = descriptors(raw)
    assert result['sse_distribution.num_ss_elements_coi']['unit'] == 'regions'
    assert 'including loop' in result['sse_distribution.num_ss_elements_coi']['description']
    assert result['sequence_adherence.n_violations']['unit'] == 'residues'
    assert result['sequence_probability_adherence.n_one_conditions']['unit'] == 'conditions'
    assert 'unknown.metric' not in result
    assert raw['sse_distribution.num_ss_elements_coi'] == '0'


def test_native_writer_return_seams_do_not_guess_filenames():
    def frame(file, name, values, caller=None):
        return SimpleNamespace(f_code=SimpleNamespace(co_filename=file, co_name=name), f_locals=values, f_back=caller)
    observer = NativeObserver()
    caller = frame('/rfproteina/metrics/utils/metrics_utils.py', '_write_trajectories_if_present', {'example_id': 'actual', 'traj_key': 'trajectory_xt'})
    observer.observe(frame('/atomworks/io/utils/io_utils.py', 'to_cif_file', {'path': '/unrelated-name.cif'}, caller), 'return', '/unrelated-name.cif')
    assert observer.events[0]['example_id'] == 'actual'
    assert observer.events[0]['path'] == '/unrelated-name.cif'
    assert observer.events[0]['role'] == 'trajectory'
    class Indices(list):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            return Indices(value) if isinstance(key, slice) else value
        def tolist(self): return list(self)
    caller = frame('/rfproteina/search/beam_search.py', 'generate', {'rank': 1, 'nsamples': 2, 'select_indices': Indices([3, 1, 2, 0]), 'path': '/unrelated-state.pt', 'checkpoint_design_ids': ['a', 'b', 'c', 'd'], 'batch_ids': ['input1', 'input2'], 'end_step': 20})
    observer.observe(frame('/torch/serialization.py', 'save', {}, caller), 'return', None)
    assert observer.events[1]['native_example_ids'] == ['c', 'a']
    assert observer.events[1]['scope'] == 'checkpoint_batch'
    assert not observer.errors


@pytest.mark.asyncio
async def test_retained_publication_cold_read(store, tmp_path, monkeypatch):
    source = os.environ.get('BMS_RETAINED_RFD4')
    if not source: pytest.skip('requires retained native evidence; never substitute synthetic science')
    source = Path(source)
    retained = json.loads((source / 'manifest.json').read_text())
    # The original publisher binds each exact writer path to its retained document.
    writer = next(e for e in retained['events'] if e['event'] == 'native_writer')
    record = next(r for r in retained['records'] if r['example_id'] == writer['example_id'])
    original = Path(writer['path'])
    for _ in Path(record['document']).parts: original = original.parent
    output = tmp_path / 'output'; native = output / 'collected/rfd4_proteina'
    shutil.copytree(source, native)
    observer = NativeObserver(); observer.events = copy.deepcopy(retained['events']); observer.errors = retained.get('observation_errors', [])
    manifest = publish(native, retained['request'], retained['receipt'], observer, producer_root=original)
    monkeypatch.setenv('BMS_DATA', str(tmp_path)); monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    async with store() as session:
        job = await session.get(Job, 'job'); job.model_id = 'rfd4_proteina'; job.mode = 'generate'; job.params = {}; job.status = 'completed'; job.output_dir = str(output)
        await publish_native_generation(session, job, output); await session.commit()
    async with store() as session:
        page = await read_published_generation_page(await session.get(Job, 'job'), session)
        row = next(row for row in page['records'] if row.get('document') == record['document'])
        assert row['metrics'] == record['metrics']
        assert row['relations'][0]['role'] == 'input'
        if row['relations'][0]['availability'] == 'stored':
            assert row['relations'][0]['artifact_id']
            assert row['relations'][0]['sha256']
            assert row['structures'][1]['relation_role'] == 'input'
        else:
            assert row['relations'][0]['availability'] == 'not_retained'
            assert 'artifact_id' not in row['relations'][0]
        assert row['metric_descriptors']['amino_acid_composition.ala_fraction']['unit'] == 'fraction'
        assert (native / record['document']).read_bytes() == (source / record['document']).read_bytes()
        # Retained search runs contain real checkpoints, but old observations do
        # not supply ancestry. Verify byte custody without loading pickle/state.
        import hashlib
        for artifact in page['artifacts']:
            if artifact.get('role') == 'sampling_state':
                relative = Path(artifact['path']).relative_to('collected/rfd4_proteina')
                original_bytes = (source / relative).read_bytes()
                assert artifact['sha256'] == hashlib.sha256(original_bytes).hexdigest()
                assert artifact['artifact_id']
                assert not any(r.get('role') == 'sampling_state' for r in row.get('relations', []))
        if os.environ.get('BMS_RETAINED_RESULTS_WIRE'):
            Path(os.environ['BMS_RETAINED_RESULTS_WIRE']).write_text(json.dumps(page))

@pytest.mark.asyncio
async def test_existing_state_hash_acquisition(tmp_path, monkeypatch):
    import hashlib
    from fastapi import HTTPException
    from services.native_generation_sources import prepare_native_sources
    from routers import files
    import paths
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'fixture': tmp_path})
    monkeypatch.setattr(paths, 'resolve_allowed_path', lambda value: tmp_path / value)
    monkeypatch.setattr(paths, 'to_allowed_relative', lambda value: str(Path(value).relative_to(tmp_path)))
    async def no_governed(_): return []
    monkeypatch.setattr(files, 'get_governed_ngs_result_roots', no_governed)
    # Retained native checkpoint bytes, when supplied, are never unpickled or
    # executed. Otherwise this is explicitly an inert transport-only specimen.
    state = tmp_path / 'state.pt'
    retained_root = Path(os.environ['BMS_RETAINED_RFD4']) if os.environ.get('BMS_RETAINED_RFD4') else None
    states = []
    if retained_root:
        states = [item for item in json.loads((retained_root / 'manifest.json').read_text())['artifacts']
                  if item.get('role') == 'sampling_state']
    state.write_bytes((retained_root / states[0]['path']).read_bytes() if states else b'inert state custody specimen')
    digest = hashlib.sha256(state.read_bytes()).hexdigest()
    params = {'rfd4_request': {'input': {'kind': 'unconditional', 'name': 'custody', 'chains': [{'chain_id': 'A', 'min_length': 2, 'max_length': 2}]}, 'resume_slot': 'resume', 'settings': {'metrics_tags': []}},
              'native_sources': {'resume': {'kind': 'file', 'path': 'state.pt', 'expected_sha256': digest}}}
    job = SimpleNamespace(model_id='rfd4_proteina', mode='generate', params=copy.deepcopy(params))
    await prepare_native_sources(job, None)
    payload = json.loads(Path(job.params['rfd4_prepared_request']).read_text())
    assert payload['runtime_identity']['sources']['resume']['sha256'] == digest
    bindings = json.loads(Path(job.params['rfd4_bindings']).read_text())
    assert (Path(job.params['rfd4_bindings']).parent / bindings['resume']).read_bytes() == state.read_bytes()
    state.write_bytes(b'changed inert specimen')
    with pytest.raises(HTTPException, match='Selected source bytes changed'):
        await prepare_native_sources(SimpleNamespace(model_id='rfd4_proteina', mode='generate', params=params), None)
