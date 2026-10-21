"""Focused CPU producer-contract regressions; sentinel values are not predictions."""
import importlib.util
import json
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from caliby_runtime import canonicalize_self_consistency_metrics, normalize_sampling_results
from lib.filtering.metrics import extract_confidence_metrics
from run_af2_initial_guess import corrected_source


def test_native_caliby_metrics_and_sample_identity(tmp_path, monkeypatch):
    raw = {'avg_ca_plddt': 0.0, 'sc_ca_rmsd': 1.25, 'tmalign_score': 0.75}
    expected = {'caliby_sc_plddt': 0.0, 'caliby_sc_rmsd': 1.25, 'caliby_sc_tm': 0.75}
    assert canonicalize_self_consistency_metrics(raw) == expected
    paths = []
    for index in range(2):
        p = tmp_path / f'backbone_sample_{index}.pdb'
        p.write_text('END\n')
        paths.append(str(p))
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    normalize_sampling_results(results={'example_id': ['backbone', 'backbone'], 'out_pdb': paths},
        output_pdb_dir=tmp_path / 'pdbs', output_meta_dir=metadata,
        prefix='probe', source='caliby', stage_mode='test',
        self_consistency={'backbone_sample_0': raw,
                          'backbone_sample_1': {'avg_ca_plddt': 90.0, 'sc_ca_rmsd': .5}})
    first = json.loads((metadata / 'generator_probe_0001.json').read_text())
    second = json.loads((metadata / 'generator_probe_0002.json').read_text())
    assert first['caliby_sc_plddt'] == 0
    assert second['caliby_sc_plddt'] == 90
    assert first['self_consistency'] == raw
    assert 'caliby_sc_ptm' not in first  # TM-align is not predicted TM
    from filter_caliby import main
    monkeypatch.setattr(sys, 'argv', ['filter_caliby', '--jsons', str(metadata),
        '--pdbs', str(tmp_path / 'pdbs'), '--output-dir', str(tmp_path / 'passed'),
        '--min-sc-plddt', '0', '--max-sc-rmsd', '2'])
    main()
    assert len(list((tmp_path / 'passed').glob('*.pdb'))) == 2


def test_rf3_native_units_and_zeros():
    for contract in (None, 1):
        values = extract_confidence_metrics({'overall_plddt': .5, 'overall_pae': 16, 'ptm': .8}, contract)
        assert (values['plddt'], values['pae'], values['ptm']) == (50, 16, .8)
        zeros = extract_confidence_metrics({'overall_plddt': 0, 'overall_pae': 0, 'ptm': 0}, contract)
        assert (zeros['plddt'], zeros['pae'], zeros['ptm']) == (0, 0, 0)


def test_af2_boundary_is_inclusive_and_idempotent():
    source = 'mask = [int(i) > feat_holder.binderlen for i in range(len(feat_holder.seq))]'
    namespace = {'feat_holder': types.SimpleNamespace(binderlen=2, seq='ACDEF')}
    exec(corrected_source(source), namespace)
    assert namespace['mask'] == [False, False, True, True, True]
    assert corrected_source(corrected_source(source)) == corrected_source(source)


def test_bc2_score_zero_readings():
    path = ROOT / 'scripts/bindcraft2_native_adapter/sitecustomize.py'
    spec = importlib.util.spec_from_file_location('bc2_test_adapter', path)
    adapter = importlib.util.module_from_spec(spec)
    before = list(sys.meta_path)
    try:
        spec.loader.exec_module(adapter)
        module = types.SimpleNamespace(__name__='bindcraft.score', math=math,
            binder_confidence=lambda *_: None, recorded_number=lambda v: v,
            read_structure_atoms=lambda _: [
                {'chain_id': 'B', 'name': 'CA', 'b_factor': value}
                for value in [0., 100., float('nan')]])
        adapter._adapt(module)
        assert module.binder_confidence('sentinel', ['B']) == .5
        assert module.binder_confidence('sentinel', ['A']) is None
        module.read_structure_atoms = lambda _: [{'chain_id': 'B', 'name': 'CA', 'b_factor': 0.}]
        assert module.binder_confidence('sentinel', ['B']) == 0.
    finally:
        sys.meta_path[:] = before
