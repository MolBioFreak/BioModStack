"""P1 retained counterexamples through real converters, CLIs and publication."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from lib.filtering.evidence import csv_metadata, native_metadata
from filter_boltzgen import run_strict_filter
from test_boltzgen_native_scalars import observed_source

PDB = 'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n'


def test_duplicate_csv_rejected_before_any_conversion(tmp_path):
    source = tmp_path / 'metrics.csv'
    source.write_text('id,design_ptm,affinity_probability_binary1,filter_rmsd\na,.1,.1,5\na,.9,.9,0\n')
    with pytest.raises(ValueError, match='duplicate'):
        csv_metadata(source, tmp_path, {'a'})
    assert not list(tmp_path.glob('confidence_*.json'))
    assert not list(tmp_path.glob('native_*.csv'))


@pytest.mark.parametrize('value', [.1, None, True])
def test_affinity_alias_conflicts_not_overwritten(value):
    data = native_metadata({'affinity_probability': value, 'affinity_probability_binary1': .9}, 'a', b'fixture', 'csv')
    assert data['metric_evidence']['affinity_probability']['state'] == 'invalid'


@pytest.mark.parametrize('payload', ['{"ptm":0.9,"pTM":0.1}', '{"ptm":0.1,"ptm":0.9}'])
def test_prediction_alias_cli(tmp_path, payload):
    (tmp_path / 'a.pdb').write_text(PDB)
    (tmp_path / 'a.json').write_text(payload)
    result = subprocess.run([sys.executable, str(SCRIPTS / 'filter_structures.py'), 'prediction',
        '--input-dir', str(tmp_path), '--output-dir', str(tmp_path / 'out'), '--min-ptm', '.8',
        '--core-protein-scientific-contract', '1'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    row = json.loads((tmp_path / 'filtered.jsonl').read_text())
    assert row['disposition'] == 'invalid_evidence'
    assert not list((tmp_path / 'out').glob('*.pdb'))


@pytest.mark.parametrize('dialect', ['csv', 'npz'])
@pytest.mark.parametrize('conflict', [False, True])
def test_converter_measurement_aliases(tmp_path, dialect, conflict):
    import run_boltzgen_wrapper as wrapper
    values = {'design_ptm': .9, 'affinity_probability_binary1': .9,
              'affinity_probability': .1 if conflict else .9, 'filter_rmsd': 0}
    if dialect == 'npz':
        np.savez(tmp_path / 'a.npz', **values)
        wrapper.extract_metrics_from_npz(tmp_path, tmp_path, core_protein_scientific_contract=1)
    else:
        source = tmp_path / 'metrics.csv'
        source.write_text('id,' + ','.join(values) + '\na,' + ','.join(str(v) for v in values.values()) + '\n')
        wrapper.create_metadata_json(source, tmp_path, core_protein_scientific_contract=1)
    data = json.loads((tmp_path / 'confidence_a.json').read_text())
    assert data['metric_evidence']['affinity_probability']['state'] == ('invalid' if conflict else 'ok')


def test_publication_rechecks_native_decisive_source(tmp_path):
    from services.boltzgen_candidate_publication import prepare
    filter_native(tmp_path)
    out = tmp_path / 'collected/boltzgen_filtered'
    report = json.loads((out / 'filter_summary.json').read_text())
    meta = out / 'confidence_a.json'
    payload = json.loads(meta.read_text())
    native = out / payload['native_scalar_source']['artifact']['path']
    with native.open('ab') as stream: stream.write(b'a,.1,.1,5\n')
    digest = hashlib.sha256(native.read_bytes()).hexdigest()
    payload['native_scalar_source']['artifact']['sha256'] = digest
    report['publication']['a']['native']['sha256'] = digest
    meta.write_text(json.dumps(payload))
    report['publication']['a']['metrics']['sha256'] = hashlib.sha256(meta.read_bytes()).hexdigest()
    (out / 'filter_summary.json').write_text(json.dumps(report))
    from services.core_protein_result_contract import CandidateIntegrityError
    with pytest.raises(CandidateIntegrityError, match='decisive native'):
        prepare(SimpleNamespace(provenance={}), tmp_path)


def filter_native(tmp_path, *, damage=None):
    identity, _ = observed_source(tmp_path)
    (tmp_path / 'a.pdb').write_text(PDB)
    source = tmp_path / 'metrics.csv'
    source.write_text('id,design_ptm,affinity_probability_binary1,filter_rmsd\na,.9,.9,0\n')
    csv_metadata(source, tmp_path, {'a'}, producer_identity=identity, filter_from_inverse_folded=True)
    meta = tmp_path / 'confidence_a.json'
    data = json.loads(meta.read_text())
    if damage == 'sidecar':
        data['design_ptm'] = .1  # Evidence block still claims .9.
    elif damage == 'duplicate':
        native = tmp_path / data['native_scalar_source']['artifact']['path']
        native.write_bytes(source.read_bytes() + b'a,.1,.1,5\n')
        data['native_scalar_source']['artifact']['sha256'] = hashlib.sha256(native.read_bytes()).hexdigest()
    meta.write_text(json.dumps(data))
    out = tmp_path / 'collected/boltzgen_filtered'; out.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(tmp_path / 'a.pdb')], jsons=[str(meta)], out_dir=str(out),
        filter_biased='false', metrics_override=None, additional_filters=None, size_buckets=None,
        boltzgen_min_plddt=None, boltzgen_min_conf_score=.8, boltzgen_max_rmsd=1, budget=1, alpha=0))
    return json.loads((out / 'filter_summary.json').read_text())


@pytest.mark.parametrize('damage', ['sidecar', 'duplicate'])
def test_decisive_native_evidence_revalidated_before_selection(tmp_path, damage):
    report = filter_native(tmp_path, damage=damage)
    assert report['dispositions'][0]['disposition'] == 'invalid_evidence'
    assert report['final_count'] == 0


@pytest.mark.parametrize('damage', [None, 'summary', 'structure', 'missing_binding'])
def test_native_rf3_export_to_prediction_cli(tmp_path, damage):
    import io
    from Bio.PDB import PDBParser, MMCIFIO
    native = tmp_path / 'native/sample'; native.mkdir(parents=True)
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('fixture', io.StringIO(PDB)))
    writer.save(str(native / 'sample_seed-1_sample-0_model.cif'))
    (native / 'sample_seed-1_sample-0_summary_confidences.json').write_text('{"ptm":0.9}')
    staged = tmp_path / 'staged'
    exported = subprocess.run([sys.executable, str(SCRIPTS / 'lib/filtering/rf3_association.py'),
        '--native-root', str(native.parent), '--output', str(staged)], cwd=tmp_path, capture_output=True, text=True)
    assert exported.returncode == 0, exported.stderr
    if damage == 'summary':
        (staged / 'sample_seed-1_sample-0_summary_confidences.json').write_text('{"ptm":0.1}')
    if damage == 'structure':
        with (staged / 'sample_seed-1_sample-0_model.cif.gz').open('ab') as f:
            f.write(b'foreign')
    if damage == 'missing_binding':
        next(staged.glob('*_binding.json')).unlink()
    result = subprocess.run([sys.executable, str(SCRIPTS / 'filter_structures.py'), 'prediction',
        '--input-dir', str(staged), '--output-dir', str(tmp_path / 'out'), '--min-ptm', '.8',
        '--convert-to-pdb', '--core-protein-scientific-contract', '1',
        '--stage-receipt-dir', str(tmp_path / 'run/filter_rf3/rf3_filter_1'),
        '--stage-id', 'rf3_prediction_filter', '--job-id', 'filter-test', '--task-id', '1'],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    row = json.loads((tmp_path / 'filtered.jsonl').read_text())
    assert row['disposition'] == ('passed' if damage is None else 'invalid_evidence')


@pytest.mark.parametrize('replacement', ['metadata', 'structure'])
def test_rf3_uses_parsed_snapshot_for_binding_and_publication(tmp_path, replacement):
    from lib.filtering.base import StructureFilter
    from lib.filtering.metrics import extract_confidence_metrics
    from test_wp03_filter_evidence import bind_summary_fixture
    structure = tmp_path / 'a.pdb'; structure.write_text(PDB)
    summary = tmp_path / 'a_summary_confidences.json'; summary.write_text('{"ptm":0.9}')
    bind_summary_fixture(structure, summary)
    class SwappingFilter(StructureFilter):
        def load_metadata(self, path):
            parsed = super().load_metadata(path)
            if replacement == 'metadata':
                summary.write_text('{"ptm":0.1}')
                bind_summary_fixture(structure, summary)
            return parsed

        def extract_metrics(self, path, metadata):
            if replacement == 'structure':
                path.write_text('foreign')
            return extract_confidence_metrics(metadata, 1)
    out = tmp_path / 'out'
    row = SwappingFilter(tmp_path, out, {'ptm': (.8, None)}, core_protein_scientific_contract=1).run()[0]
    if replacement == 'metadata':
        assert row['disposition'] == 'invalid_evidence'
    else:
        assert row['disposition'] == 'passed'
        assert (out / 'a.pdb').read_text() == PDB


@pytest.mark.asyncio
async def test_rf3_export_filter_receipt_sqlite_api(tmp_path, monkeypatch):
    from test_rf_filter_stage_accounting import job
    from test_core_protein_candidates import setup
    from services import result_ingester, rf_filter_task_roster as roster
    from routers.jobs import get_job
    test_native_rf3_export_to_prediction_cli(tmp_path, None)
    publication = tmp_path / 'results/best_designs'; publication.mkdir(parents=True)
    selected = next((tmp_path / 'out').glob('*.pdb'))
    (publication / 'candidate_terminal-a.pdb').write_bytes(selected.read_bytes())
    terminal = tmp_path / 'terminal.json'
    terminal.write_text(json.dumps({'candidate_id': 'terminal-a', 'parent_job_id': 'filter-test',
        'parent_workflow_id': 'protein_design', 'producer_method': 'rf3',
        'producer_output_key': 'rf3_terminal/' + selected.name,
        'producer_artifact_sha256': hashlib.sha256(selected.read_bytes()).hexdigest()}))
    stages = tmp_path / 'run/filter_stages'; stages.mkdir()
    for stage in ('rf3_prediction_filter', 'rfd3_backbone_filter'):
        active = stage == 'rf3_prediction_filter'
        command = [sys.executable, str(SCRIPTS / 'collect_rf_filter_stage.py'), '--job-id', 'filter-test',
            '--owner', 'protein_design', '--stage-id', stage, '--role', 'selected_publication' if active else 'skipped',
            '--expected-tasks', '1' if active else '0', '--output', str(stages / (stage + '.json'))]
        if active:
            command += ['--receipt', str(tmp_path / 'run/filter_rf3/rf3_filter_1'), '--terminal-manifest', str(terminal)]
        completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
    owner = job(tmp_path)
    owner.params = {**owner.params, 'rf3_min_ptm': .8}
    owner.provenance = {'core_protein_scientific_contract': 1}
    roster.begin(owner, owner.params); roster.observe(owner, '[ab/123456] Submitted process > FilterRF3 (1)'); roster.finish(owner, 0)
    async def later_owner(*a, **k): return 17
    monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', later_owner)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            db.add(owner); await db.commit()
            assert await result_ingester.ingest_job_results(owner.id, str(tmp_path), db) == 17
            await db.commit()
        async with factory() as db:
            wire = (await get_job(owner.id, db)).model_dump(mode='json')
            record = wire['provenance']['rf_filter_stages']['rf3_prediction_filter']['dispositions'][0]
            assert record['disposition'] == 'passed' and record['ptm'] == .9
            assert record['rf3_binding']['sha256']
    finally:
        await engine.dispose()


@pytest.mark.parametrize('damage', ['terminal_key', 'producer_binding'])
def test_terminal_full_key_substitution_rejected(tmp_path, monkeypatch, damage):
    from test_rf_filter_stage_accounting import publish, job
    from services.rf_filter_stage_accounting import prepare_filter_stages
    publish(tmp_path, monkeypatch)
    path = tmp_path / 'run/filter_stages/rf3_prediction_filter.json'
    stage = json.loads(path.read_text())
    if damage == 'terminal_key':
        stage['selection'][0]['producer_output_key'] = 'foreign_task/a.pdb'
    else:
        (tmp_path / 'run/filter_rf3/rf3_filter_1/inputs/a_binding.json').write_text('{}')
    path.write_text(json.dumps(stage))
    with pytest.raises(ValueError, match='filter'):
        prepare_filter_stages(job(tmp_path), tmp_path)
