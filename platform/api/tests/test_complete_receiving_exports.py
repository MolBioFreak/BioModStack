"""Real publisher -> registered reader, inert source bytes; no native inference."""
import csv
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from database import Job, JobArtifact
from services import rfd4_proteina_results as reader
from test_remote_lifecycle_gaps import store
from test_denovo_canonical_return import cif_ensemble
from scripts.bms_rfd4.publication import NativeObserver, publish
from scripts.lib.rfd4_proteina_contract import normalize_request


def test_complete_receiving_same_source_geometry_is_zero_under_exact_identity():
    from test_binder_pose_comparison import geometry
    from services.binder_pose_comparison import compare_exact
    records, mapping = geometry()
    result = compare_exact(records, records, mapping)
    assert result['status'] == 'ok'
    for field in ('binder_fitted_ca_rmsd', 'target_fit_ca_rmsd', 'target_fitted_binder_ca_rmsd'):
        assert result[field] == pytest.approx(0, abs=1e-12)
    assert result['counts']['binder']['matched'] == result['counts']['target']['matched'] == 4


def qa_specimen():
    """Inert two-MODEL CIF with genuine local-QA syntax only for MODEL 1."""
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    raw = cif_ensemble()
    table = MMCIF2Dict(io.StringIO(raw.decode()))
    fields = ['model_id', 'label_asym_id', 'label_seq_id', 'label_comp_id', 'metric_id', 'metric_value']
    atom = next(i for i, model in enumerate(table['_atom_site.pdbx_PDB_model_num']) if model == '1')
    row = ['1', table['_atom_site.label_asym_id'][atom], table['_atom_site.label_seq_id'][atom], table['_atom_site.label_comp_id'][atom], '1', '0']
    return raw + ('loop_\n_ma_qa_metric.id\n_ma_qa_metric.name\n_ma_qa_metric.type\n_ma_qa_metric.mode\n1 pLDDT pLDDT local\n#\nloop_\n'
        + ''.join('_ma_qa_metric_local.' + field + '\n' for field in fields) + ' '.join(row) + '\n#\n').encode()


@pytest.mark.asyncio
@pytest.mark.parametrize('population', ['zero', 'partial'])
async def test_complete_receiving_real_emitter_page_replay_and_bounded_copy(store, tmp_path, monkeypatch, population):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'output'
    root = output / 'collected/rfd4_proteina'
    (root / 'native').mkdir(parents=True)
    observer = NativeObserver()
    count = 0 if population == 'zero' else 3
    for i in range(count):
        path = root / f'native/inert-{i}.cif'
        path.write_bytes(qa_specimen() if i == 0 else cif_ensemble())
        observer.events.append({'event': 'native_writer', 'example_id': f'input_0_bsc2_{i}',
            'path': str(path), 'population': 'final_beam' if i == 0 else 'completed_rollout'})
    if count:
        metrics = root / 'native/inference_metrics/per_example_metrics.csv'
        metrics.parent.mkdir()
        with metrics.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['example_id', 'reward', 'oracle_config', 'save_id', 'missing'])
            writer.writeheader()
            for i in range(count):
                writer.writerow({'example_id': f'input_0_bsc2_{i}', 'reward': '0', 'oracle_config': 'conditioned-native', 'save_id': '0', 'missing': ''})
    manifest = publish(root, normalize_request({'input': {'kind': 'unconditional', 'name': 'inert',
        'chains': [{'chain_id': 'A', 'min_length': 10, 'max_length': 10}]}, 'settings': {'metrics_tags': []}}),
        {'execution_status': 'failed', 'seed': 0, 'metrics_enabled': False}, observer)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode, job.params, job.status = 'rfd4_proteina', 'generate', {}, 'failed'
        job.output_dir = str(output)
        receipt = await reader.publish_native_generation(session, job, output)
        assert await reader.publish_native_generation(session, job, output) == receipt
        await session.commit()
    async with store() as session:
        job = await session.get(Job, 'job')
        sql = []
        engine = session.bind.sync_engine
        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            sql.append(statement)
        event.listen(engine, 'before_cursor_execute', capture)
        # Readback is registered/read-only: no filesystem reopen or model writer.
        import services.bindcraft2_publication as publication
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            assert not path.is_relative_to(root), 'readback opened native file'
            return original_open(path, *args, **kwargs)
        try:
            with patch.object(publication, '_regular', side_effect=AssertionError('readback reopened native bytes')), patch.object(Path, 'open', guarded_open):
                first = await reader.read_published_generation_page(job, session, limit=1)
                first_sql = len(sql)
                second = await reader.read_published_generation_page(job, session, offset=1, limit=1)
        finally:
            event.remove(engine, 'before_cursor_execute', capture)
        assert (first_sql, len(sql)) == (2, 4)
        assert first['total'] == second['total'] == count
        assert first['execution_status'] == 'failed'
        assert first['receipt']['metrics_enabled'] is False
        assert first['records'] == [] if not count else first['records'][0]['example_id'] == manifest['records'][0]['example_id']
        if count:
            assert first['records'][0]['metrics'] == manifest['records'][0]['metrics']
            assert first['records'][0]['candidate_ref']['kind'] == 'native_document'
            assert first['records'][0]['candidate_key'] != second['records'][0]['candidate_key']
            first['records'][0]['metrics']['reward'] = 'mutated consumer copy'
            again = await reader.read_published_generation_page(job, session, limit=1)
            assert again['records'][0]['metrics']['reward'] == '0'
            first = again
            from services.bindcraft2_scientific_consumer import native_document_metadata, native_artifact_metric
            profiles = []
            for record in [first['records'][0], second['records'][0]]:
                artifact_id = record['candidate_ref']['artifact_id']
                metadata = await native_document_metadata(session, job.id, artifact_id)
                metric = await native_artifact_metric(session, job.id, artifact_id, 'residue_plddt')
                profiles.append({'source': record['candidate_ref'], 'metadata': {**metadata, 'document': metadata['document'].model_dump(mode='json')}, 'metric': metric.model_dump(mode='json')})
            assert profiles[0]['metric']['status'] == 'ok'
            assert profiles[0]['metric']['values'] == [0.0]
            assert profiles[0]['metric']['axis']['residues'][0]['selected_model'] == 1
            assert profiles[1]['metric']['status'] == 'unavailable'
        artifacts = list(await session.scalars(select(JobArtifact)))
        assert len(artifacts) == len(receipt['files'])
        evidence = os.environ.get('BMS_RECEIVING_EVIDENCE')
        if evidence:
            dest = Path(evidence); dest.mkdir(parents=True, exist_ok=True)
            if count:
                (dest / 'native-profiles.json').write_text(json.dumps(profiles, indent=2))
            (dest / f'emitter-reader-{population}.json').write_text(json.dumps({'page': first,
                'native_records': manifest['records'], 'instrumentation': {'one_page_sql': first_sql,
                'two_pages_sql': 4, 'native_file_opens': 0, 'coordinate_hash_bytes': 0,
                'decode_calls': 0, 'method': 'registered reader; _regular forbidden; SQL listener'}}, indent=2))
