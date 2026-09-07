"""Future-only native metric and filter evidence controls (no model runtime)."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import filter_boltzgen as bg
import run_boltzgen_wrapper as wrapper
from lib.filtering.base import StructureFilter
from lib.filtering import metrics as sm


def test_native_csv_no_fabricated_confidence(tmp_path):
    source = tmp_path / 'metrics.csv'
    source.write_text('id,design_ptm,custom_metric\na,0.8,4\n')
    assert wrapper.create_metadata_json(source, tmp_path, core_protein_scientific_contract=1)
    data = json.loads((tmp_path / 'confidence_a.json').read_text())
    assert data['design_ptm'] == .8
    assert data['plddt'] is None and data['filter_rmsd'] is None
    assert data['custom_metric'] == 4
    assert data['metric_evidence']['plddt']['state'] == 'unavailable'
    assert data['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize('value,units,expected', [(0., 'percent', 0.), (.5, 'percent', .5), (1., 'fraction', 100.)])
def test_npz_explicit_plddt_units(tmp_path, value, units, expected):
    np.savez(tmp_path / 'a.npz', plddt=[value], plddt_units=units, design_ptm=.8)
    assert wrapper.extract_metrics_from_npz(tmp_path, tmp_path, core_protein_scientific_contract=1) == 1
    data = json.loads((tmp_path / 'confidence_a.json').read_text())
    assert data['plddt'] == expected
    assert data['design_ptm'] == .8


def test_npz_missing_not_ptm_substitute(tmp_path):
    np.savez(tmp_path / 'a.npz', design_ptm=.8, ptm=.9)
    wrapper.extract_metrics_from_npz(tmp_path, tmp_path, core_protein_scientific_contract=1)
    data = json.loads((tmp_path / 'confidence_a.json').read_text())
    assert data['plddt'] is None
    assert bg.get_metric_value(data, {}, 'plddt') is None


def test_native_rank_defaults_preserve_complete_rank_algorithm():
    specs = bg.resolve_metric_specs({}, core_protein_scientific_contract=1)
    assert [s['name'] for s in specs] == ['design_ptm', 'affinity_probability', 'filter_rmsd']
    designs = [{'design_id': str(i), 'metrics': dict(design_ptm=v, affinity_probability=v, filter_rmsd=1-v)} for i,v in enumerate([.9,.5,.1])]
    legacy = [{'design_id': d['design_id'], 'metrics': {**d['metrics'], 'plddt': d['metrics']['design_ptm']*100}} for d in designs]
    bg.apply_metric_ranking(legacy, bg.resolve_metric_specs({}))
    bg.apply_metric_ranking(designs, specs, core_protein_scientific_contract=1)
    assert [d['quality_score'] for d in designs] == [d['quality_score'] for d in legacy]
    missing = [{'design_id': 'missing', 'metrics': {'design_ptm': .8}}]
    bg.apply_metric_ranking(missing, specs, core_protein_scientific_contract=1)
    assert missing[0]['quality_score'] is None
    assert missing[0]['quality_rank_key'] is None


def test_boltzgen_cli_publishes_every_input(tmp_path, monkeypatch):
    paths = []
    jsons = []
    for name, metric in [('pass', 0.), ('bad', float('nan')), ('missing', None), ('reject', 1.)]:
        path = tmp_path / f'{name}.pdb'
        path.write_text('MODEL')
        paths.append(str(path))
        meta = tmp_path / f'confidence_{name}.json'
        meta.write_text(json.dumps({'design_id': name, 'design_ptm': .8, 'filter_rmsd': metric, 'affinity_probability': .9}))
        jsons.append(str(meta))
    out = tmp_path/'out'
    monkeypatch.setattr(sys, 'argv', ['filter_boltzgen', '--pdbs', *paths, '--jsons', *jsons, '--out_dir', str(out), '--filter-biased', 'false', '--boltzgen-max-rmsd', '0', '--additional-filters', 'design_ptm>0.8', '--core-protein-scientific-contract', '1'])
    bg.main()
    summary = json.loads((out/'filter_summary.json').read_text())
    assert [r['disposition'] for r in summary['dispositions']] == ['passed','invalid_evidence','unevaluable_missing','rejected_threshold']
    assert summary['effective_metrics'][0]['name'] == 'design_ptm'
    assert len(list(out.glob('*.pdb'))) == 1
    assert json.loads((out/'confidence_pass.json').read_text())['core_protein_scientific_contract'] == 1
    json.dumps(summary, allow_nan=False)


def test_unified_invalid_metrics_still_publish(tmp_path):
    (tmp_path/'a.cif').write_text('same')
    (tmp_path/'a.json').write_text('{"ptm": NaN}')
    f = Filter(tmp_path, tmp_path/'out', {'ptm': (0,None)}, core_protein_scientific_contract=1)
    result = f.run()
    f.write_results(result)
    assert json.loads((tmp_path/'out/filtered.jsonl').read_text())['disposition'] == 'invalid_evidence'


def test_secondary_computed_zero_vs_exception(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(sm, 'HAS_BIOTITE', True)
    monkeypatch.setattr(sm, 'struc', SimpleNamespace(annotate_sse=lambda _: ['c','c']), raising=False)
    assert sm.calculate_secondary_structure(object(), core_protein_scientific_contract=1)['helices'] == 0
    def fail(_):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(sm.struc, 'annotate_sse', fail)
    assert sm.calculate_secondary_structure(object(), core_protein_scientific_contract=1)['helices'] is None


def test_wrapper_cli_accepts_future_marker(tmp_path):
    import subprocess
    result = subprocess.run([sys.executable, str(SCRIPTS/'run_boltzgen_wrapper.py'), '--help'], text=True, capture_output=True, cwd=tmp_path)
    assert result.returncode == 0
    assert '--core-protein-scientific-contract' in result.stdout


def test_prediction_cli_zero_confidence_and_dispositions(tmp_path):
    import subprocess
    (tmp_path/'a.cif').write_text('same')
    source = tmp_path/'a_summary_confidences.json'
    source.write_text('{"plddt": 0, "plddt_units": "percent", "ptm": 0}')
    result = subprocess.run([sys.executable, str(SCRIPTS/'filter_structures.py'), 'prediction', '--input-dir', str(tmp_path), '--output-dir', str(tmp_path/'out'), '--min-plddt', '0', '--core-protein-scientific-contract', '1'], text=True, capture_output=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path/'filtered.jsonl').read_text())
    assert record['disposition'] == 'passed'
    assert record['plddt'] == 0
    assert record['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_foreign_same_name_metadata_is_invalid(tmp_path):
    (tmp_path/'a.cif').write_text('same')
    (tmp_path/'a_summary_confidences.json').write_text('{"design_id": "b", "ptm": 0.9}')
    f = Filter(tmp_path, tmp_path/'out', {'ptm': (0,None)}, core_protein_scientific_contract=1)
    assert f.run()[0]['disposition'] == 'invalid_evidence'


def test_workflow_marker_and_zero_transport():
    source = (SCRIPTS.parent/'modules/boltzgen.nf').read_text()
    assert source.count('--core-protein-scientific-contract') >= 2
    assert '${minPlddt != null ?' in source
    assert '${maxRmsd != null ?' in source


@pytest.mark.parametrize('values', [[float('nan'), .8], [float('inf')], [-.1, 1.1]])
def test_npz_invalid_member_not_hidden_by_mean(tmp_path, values):
    np.savez(tmp_path/'a.npz', plddt=values, plddt_units='fraction')
    wrapper.extract_metrics_from_npz(tmp_path, tmp_path, core_protein_scientific_contract=1)
    data = json.loads((tmp_path/'confidence_a.json').read_text())
    assert data['metric_evidence']['plddt']['state'] == 'invalid'
    assert data['plddt'] is None


def test_unit_schema_rejects_wrong_native_metric_units():
    from lib.filtering.evidence import evaluate
    result = evaluate([('ptm', 0, None)], {}, 'a', {'ptm': {'state': 'ok', 'value': .8, 'units': 'percent', 'reason_code': None}})
    assert result['disposition'] == 'invalid_evidence'


def test_filter_transport_publishes_rf3_rfd3_dispositions():
    for name in ('rf3', 'rfd3'):
        source = (SCRIPTS.parent/f'modules/{name}.nf').read_text()
        assert '--core-protein-scientific-contract' in source
        assert f'pattern: "{name}_data_*.jsonl"' in source


def test_native_ui_labels_do_not_conflate_metrics():
    source = (SCRIPTS.parent/'platform/frontend/src/components/AntibodyDenovoTemplate.tsx').read_text()
    assert 'Min native pLDDT (0–100; not design pTM)' in source
    assert 'Min affinity probability (0–1)' in source
    assert '<BoltzGenRankControls' in source
    controls = (SCRIPTS.parent/'platform/frontend/src/components/BoltzGenRankControls.tsx').read_text()
    assert 'v1 defaults: design pTM ↑, affinity probability ↑, refold RMSD ↓' in controls


class Filter(StructureFilter):
    def extract_metrics(self, structure_path, metadata):
        return metadata


@pytest.mark.parametrize('value,state', [(None,'unevaluable_missing'), (False,'invalid_evidence'), (float('nan'),'invalid_evidence'), (float('inf'),'invalid_evidence'), (-1,'invalid_evidence'), (0,'passed'), (1,'rejected_threshold')])
def test_strict_threshold_zero(tmp_path, value, state):
    f = Filter(tmp_path, tmp_path / 'out', {'rmsd': (0,0)}, core_protein_scientific_contract=1)
    result = f.evaluate_thresholds({'rmsd': value}, 'candidate')
    assert result['disposition'] == state
    assert result['criteria'][0]['candidate_id'] == 'candidate'
    json.dumps(result, allow_nan=False)


def test_disabled_gate_requires_no_evidence(tmp_path):
    f = Filter(tmp_path, tmp_path/'out', {'rmsd': (None,None)}, core_protein_scientific_contract=1)
    assert f.check_thresholds({}) == (True, None)


def test_secondary_failure_not_real_zero(monkeypatch):
    monkeypatch.setattr(sm, 'HAS_BIOTITE', False)
    assert sm.calculate_secondary_structure(None, core_protein_scientific_contract=1)['helices'] is None


def test_exact_rf3_sidecar_and_all_dispositions(tmp_path):
    for name in ['a','b']:
        (tmp_path / f'{name}.cif').write_text('same')
    (tmp_path / 'a_summary_confidences.json').write_text('{"ptm": 0.8}')
    (tmp_path / 'foreign_summary_confidences.json').write_text('{"ptm": 0.9}')
    f = Filter(tmp_path, tmp_path/'out', {'ptm': (.8,None)}, core_protein_scientific_contract=1)
    assert f.find_metadata_file(tmp_path/'a.cif').name == 'a_summary_confidences.json'
    assert f.find_metadata_file(tmp_path/'b.cif') is None
    results = f.run()
    assert [r['disposition'] for r in results] == ['passed', 'unevaluable_missing']
    f.write_results(results)
    assert len((tmp_path/'out/filtered.jsonl').read_text().splitlines()) == 2


@pytest.mark.parametrize('dialect', ['csv', 'npz'])
@pytest.mark.parametrize('missing', ['design_ptm', 'filter_rmsd'])
@pytest.mark.parametrize('budget', [1, 10])
def test_real_wrapper_to_filter_required_metrics(tmp_path, dialect, missing, budget):
    import subprocess
    rows = {
        'best': dict(design_ptm=.9, affinity_probability=.9, filter_rmsd=0.),
        'worse': dict(design_ptm=.5, affinity_probability=.5, filter_rmsd=1.),
        'missing': dict(design_ptm=.8, affinity_probability=.8, filter_rmsd=.2),
    }
    rows['missing'].pop(missing)
    paths, jsons = [], []
    for name, values in rows.items():
        path = tmp_path / f'{name}.pdb'
        path.write_text('MODEL')
        paths.append(str(path))
        jsons.append(str(tmp_path / f'confidence_{name}.json'))
        if dialect == 'npz':
            np.savez(tmp_path / f'{name}.npz', **values)
    if dialect == 'csv':
        import csv
        source = tmp_path / 'metrics.csv'
        with source.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=['id', 'design_ptm', 'affinity_probability', 'filter_rmsd'])
            writer.writeheader()
            writer.writerows(dict(id=name, **values) for name, values in rows.items())
        assert wrapper.create_metadata_json(source, tmp_path, core_protein_scientific_contract=1)
    else:
        assert wrapper.extract_metrics_from_npz(tmp_path, tmp_path, core_protein_scientific_contract=1) == 3
    for path in jsons:
        assert 'metric_evidence' in json.loads(Path(path).read_text())
    for zero_gate in (False, True):
        out = tmp_path / f'out-{zero_gate}'
        command = [sys.executable, str(SCRIPTS/'filter_boltzgen.py'), '--pdbs', *paths,
                   '--jsons', *jsons, '--out_dir', str(out), '--filter-biased', 'false',
                   '--budget', str(budget), '--alpha', '0', '--core-protein-scientific-contract', '1']
        if zero_gate:
            command += ['--boltzgen-max-rmsd', '0']
        run = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path)
        assert run.returncode == 0, run.stderr
        summary = json.loads((out/'filter_summary.json').read_text())
        records = {r['candidate_id']: r for r in summary['dispositions']}
        assert records['missing']['disposition'] == 'unevaluable_missing'
        assert not records['missing']['selected']
        assert any(c['criterion'] == missing and c['disposition'] == 'unevaluable_missing'
                   for c in records['missing']['criteria'])
        assert records['best']['selected']
        assert records['best']['disposition'] == 'passed'
        assert records['worse']['disposition'] == ('rejected_threshold' if zero_gate else 'passed')
        assert records['worse']['selected'] == (not zero_gate and budget == 10)
        assert summary['final_count'] == (2 if not zero_gate and budget == 10 else 1)
        json.dumps(summary, allow_nan=False)
    # The real producer's complete candidates retain the established ranks.
    designs = [{'design_id': name, 'metrics': json.loads(Path(path).read_text())}
               for name, path in zip(rows, jsons) if name != 'missing']
    bg.apply_metric_ranking(designs, bg.resolve_metric_specs({}, 1), 1)
    assert [d['quality_score'] for d in designs] == [1., 0.]
    assert [d['quality_rank_key'] for d in designs] == [0., 1.]


@pytest.mark.parametrize('budget', [1, 10])
@pytest.mark.parametrize('aggregate', [None, float('nan'), float('inf'), False])
def test_selection_excludes_unrankable_before_shortcut_and_sort(budget, aggregate):
    valid = {'design_id': 'valid', 'quality_score': 1., 'quality_rank_key': 0.}
    invalid = {'design_id': 'invalid', 'quality_score': aggregate, 'quality_rank_key': aggregate}
    assert bg.select_diverse_subset([invalid, valid], budget,
                                   core_protein_scientific_contract=1) == [valid]


def backbone_fixture(path):
    from biotite.structure import AtomArray
    from biotite.structure.io import pdb, pdbx
    import gzip
    atoms = AtomArray(9)
    atoms.chain_id[:] = 'A'
    atoms.res_id = np.repeat([1, 2, 3], 3)
    atoms.res_name[:] = 'ALA'
    atoms.atom_name = np.tile(['N', 'CA', 'C'], 3)
    atoms.element = np.tile(['N', 'C', 'C'], 3)
    atoms.coord = np.array([[i, i % 2, 0] for i in range(9)], dtype=float)
    if path.suffix == '.pdb':
        file = pdb.PDBFile()
        file.set_structure(atoms)
        file.write(path)
    else:
        import io
        file = pdbx.CIFFile()
        pdbx.set_structure(file, atoms)
        text = io.StringIO()
        file.write(text)
        raw = text.getvalue().encode()
        path.write_bytes(gzip.compress(raw) if path.suffix == '.gz' else raw)


@pytest.mark.parametrize('suffix', ['.pdb', '.cif', '.cif.gz'])
@pytest.mark.parametrize('sidecar', [False, True])
def test_backbone_snapshot_provenance_reaches_cli_jsonl(tmp_path, monkeypatch, suffix, sidecar):
    monkeypatch.chdir(tmp_path)
    import filter_structures as fs
    source = tmp_path / ('a' + suffix)
    backbone_fixture(source)
    original = source.read_bytes()
    metadata = tmp_path/'a.json'
    if sidecar:
        metadata.write_text('{"rog": 999, "helices": 999}')
    expected = fs.calculate_backbone_metrics(fs.load_structure(source), 1)
    assert expected['helices'] == 0
    real_load = fs.load_structure
    parsed = []
    def replace_after_load(snapshot):
        parsed.append(Path(snapshot).read_bytes())
        structure = real_load(snapshot)
        source.write_bytes(b'replaced after parse')
        return structure
    monkeypatch.setattr(fs, 'load_structure', replace_after_load)
    monkeypatch.setattr(sys, 'argv', ['filter_structures', 'backbone', '--input-dir', str(tmp_path),
        '--output-dir', str(tmp_path/'out'), '--output-jsonl', str(tmp_path/'result.jsonl'),
        '--min-helices', '0', '--core-protein-scientific-contract', '1'])
    fs.main()
    record = json.loads((tmp_path/'result.jsonl').read_text())
    assert parsed == [original]
    assert source.read_bytes() != original
    assert record['disposition'] == 'passed'
    assert record['source_sha256'] == (hashlib.sha256(metadata.read_bytes()).hexdigest() if sidecar else None)
    for name, column in [('rog', 'rfd_RoG'), ('helices', 'rfd_helices'),
                         ('strands', 'rfd_strands'), ('total_ss', 'rfd_total_ss')]:
        descriptor = record['descriptor_provenance'][name]
        assert descriptor['source'] == str(source)
        assert descriptor['source_sha256'] == hashlib.sha256(parsed[0]).hexdigest()
        assert descriptor['source_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest()
        assert descriptor['calculation'] and descriptor['biotite_version']
        assert descriptor['calculation_version'] == 1 and descriptor['model'] == 1
        assert descriptor['evidence']['state'] == 'ok'
        assert descriptor['evidence']['value'] == pytest.approx(expected[name])
        assert record[column] == pytest.approx(expected[name])


@pytest.mark.parametrize('failure', ['ss_error', 'nonfinite'])
def test_backbone_cli_preserves_unavailable_and_invalid(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    import filter_structures as fs
    source = tmp_path/'a.pdb'
    backbone_fixture(source)
    if failure == 'ss_error':
        def fail(_):
            raise RuntimeError('SS failed')
        monkeypatch.setattr(sm.struc, 'annotate_sse', fail)
        gate, metric = '--min-helices', 'helices'
        state, disposition = 'unavailable', 'unevaluable_missing'
    else:
        # Inject an invalid numerical result at the existing calculation boundary.
        monkeypatch.setattr(sm, 'calculate_radius_of_gyration', lambda _: float('nan'))
        gate, metric = '--min-rog', 'rog'
        state, disposition = 'invalid', 'invalid_evidence'
    monkeypatch.setattr(sys, 'argv', ['filter_structures', 'backbone', '--input-dir', str(tmp_path),
        '--output-dir', str(tmp_path/'out'), gate, '0', '--core-protein-scientific-contract', '1'])
    fs.main()
    record = json.loads((tmp_path/'filtered.jsonl').read_text())
    assert record['disposition'] == disposition
    descriptor = record['descriptor_provenance'][metric]
    assert descriptor['evidence']['state'] == state
    assert descriptor['evidence']['value'] is None
    assert descriptor['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert not record['passed']


def test_legacy_backbone_jsonl_shape_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import filter_structures as fs
    backbone_fixture(tmp_path/'a.pdb')
    monkeypatch.setattr(sys, 'argv', ['filter_structures', 'backbone', '--input-dir', str(tmp_path),
        '--output-dir', str(tmp_path/'out')])
    fs.main()
    record = json.loads((tmp_path/'filtered.jsonl').read_text())
    assert set(record) == {'description', 'fold_id', 'seq_id', 'file', 'passed', 'reason',
                           'rfd_helices', 'rfd_strands', 'rfd_total_ss', 'rfd_RoG'}
