"""Pinned La parser CPU atom scopes; no network, weights or inference."""
import os
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('LAPROTEINA_TEST_SOURCE'),
                                reason='selected installed La-Proteina required')


@pytest.mark.parametrize('preset', ['ucond_notri', 'ucond_tri', 'ucond_notri_long', 'motif_idx_aa', 'motif_idx_tip', 'motif_uidx_aa', 'motif_uidx_tip'])
def test_seven_installed_profiles_sparse_composition(tmp_path, monkeypatch, preset):
    from scripts.lib.laproteina_contract import resolve_profile
    from scripts.run_laproteina_inference import _configure_laproteina
    monkeypatch.setenv('LAPROTEINA_REPO', os.environ['LAPROTEINA_TEST_SOURCE'])
    source_free = preset.startswith('ucond_')
    request = {'job_name': 'exact', 'task': resolve_profile(preset)['task'],
        'target_lengths': [12, 21], 'num_designs': 3,
        'laproteina': {'preset': preset, 'samples_per_length': 2, 'num_steps': 400,
            'checkpoint_dir': '/unread-weights', 'motif_task_name': '1YCR_AA',
            'seed': 0, 'self_cond': False}}
    receipt = _configure_laproteina(request, tmp_path)['receipt']['effective_config']
    assert receipt['seed'] == 0
    assert receipt['generation']['args']['self_cond'] is False
    assert receipt['generation']['dataset']['nsamples'] == (2 if source_free else 3)
    assert receipt['generation']['model']['bb_ca']['simulation_step_params']['sc_scale_noise'] == (0.15 if preset.endswith('long') else 0.1)
    assert receipt['generation']['model']['local_latents']['simulation_step_params']['sc_scale_noise'] == (0.05 if preset.endswith('long') else 0.1)


def test_all_52_pinned_named_tasks_preserve_exact_inherited_dictionary(tmp_path, monkeypatch):
    import yaml
    from scripts.run_laproteina_inference import _configure_laproteina
    root = Path(os.environ['LAPROTEINA_TEST_SOURCE'])
    monkeypatch.setenv('LAPROTEINA_REPO', str(root))
    catalog = yaml.safe_load((root/'configs/generation/motif_dict.yaml').read_text())['dataset']['motif_dict_cfg']
    assert len(catalog) == 52
    observed = {}
    for name, task in catalog.items():
        request = {'job_name': 'catalog', 'task': 'motif_scaffolding', 'target_lengths': [17], 'num_designs': 2,
            'laproteina': {'preset': 'motif_idx_aa', 'samples_per_length': 9, 'num_steps': 400,
                'checkpoint_dir': '/unread-weights', 'motif_task_name': name,
                'contig_string': 'historical-unused-primary', 'atom_selection_mode': 'ca'}}
        cfg = _configure_laproteina(request, tmp_path)['receipt']['effective_config']
        observed[name] = cfg['generation']['dataset']['motif_dict_cfg'][name]
        assert cfg['generation']['dataset']['nsamples'] == 2
    assert observed == catalog


@pytest.fixture
def pdb(tmp_path):
    path = tmp_path / 'motif-only.pdb'
    path.write_text(''.join(
        f'ATOM  {((r-1)*5+i):5d} {atom:^4s} ALA A{r:4d}    {(r*4+dx):8.3f}{dy:8.3f}{0:8.3f}  1.00 20.00          {atom[0]:>2s}  \n'
        for r in (1, 2) for i, atom, dx, dy in [(1, 'N', -1.2, 0.), (2, 'CA', 0., 0.),
            (3, 'C', 1.2, 0.), (4, 'O', 1.8, 1.), (5, 'CB', 0., 1.5)]) + 'END\n')
    return path


@pytest.mark.parametrize('scope,expected', [
    ('all_atom', {'N', 'CA', 'C', 'O', 'CB'}), ('ca', {'CA'}),
    ('bb3o', {'N', 'CA', 'C', 'O'}), ('tip_atoms', {'CA', 'CB'}),
])
def test_exact_native_atom_contents(pdb, scope, expected):
    from proteinfoundation.utils.motif_utils import extract_motif_from_pdb, atom_order
    mask, xyz, sequence = extract_motif_from_pdb('A1-1/A2-2', str(pdb), motif_only=True,
                                               atom_selection_mode=scope)
    names = [{name for name, index in atom_order.items() if row[index]} for row in mask]
    assert names == [expected, expected]
    assert tuple(xyz.shape) == (2, 37, 3)
    assert sequence.tolist() == [0, 0]


def test_explicit_atom_spec_precedence_and_native_null_restores_contig(pdb):
    from proteinfoundation.utils.motif_utils import extract_motif_from_pdb, atom_order
    mask, _, _ = extract_motif_from_pdb('A99-99', str(pdb), motif_only=True,
        motif_atom_spec='A1: [CB]; A2: [N, O]', atom_selection_mode='ca')
    assert [{name for name, i in atom_order.items() if row[i]} for row in mask] == [{'CB'}, {'N', 'O'}]
    restored, _, _ = extract_motif_from_pdb('A1-2', str(pdb), motif_only=True,
        motif_atom_spec=None, atom_selection_mode='bb3o')
    assert restored.sum().item() == 8


def test_motif_only_whole_chain_once_not_contig_shortening(pdb):
    from proteinfoundation.utils.motif_utils import extract_motif_from_pdb
    full, _, _ = extract_motif_from_pdb('A1-1/A1-1', str(pdb), motif_only=True,
                                       atom_selection_mode='all_atom')
    assert full.shape[0] == 2
    assert full.sum().item() == 10
    bounded, _, _ = extract_motif_from_pdb('A1-1', str(pdb), motif_only=False,
                                          atom_selection_mode='all_atom')
    assert bounded.shape[0] == 1
    assert bounded.sum().item() == 5
