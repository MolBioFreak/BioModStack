"""Current owner seams; private specimens, no scientific inference."""
from copy import deepcopy
import hashlib

from scripts.lib.protein_cad_settings import bind_new_cad_settings, replay_cad_settings, settings_projection
from scripts.lib.rfd3_scientific_settings import conditioning_settings
from scripts.rfd3_local_redesign.contract import build_request
from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native
from test_rfd3_bounded_corrections import cif_source, local


def test_shell_resolves_author_insertions_multichain_and_replays(tmp_path, monkeypatch):
    from services import rfd3_local_redesign as service
    source = tmp_path / 'source.cif'
    source.write_text(cif_source())
    monkeypatch.setattr(service, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(service, '_resolve_local_redesign_source', lambda value: source)
    params = dict(input_structure=str(source), design_chains=['A', 'AA'], context_chains=['B'],
                  region_mode='interface_shell', interface_cutoff=2.0, region_padding=0,
                  source_mapping='bms.rfd3.source-map.v1')
    before = source.read_bytes()
    assert service.resolve_interface_shell(source, {**params, 'interface_cutoff': 1}) == 'AA2'
    normalized, request, digest = service.normalize_local_redesign_params(params, job_name='shell')
    assert normalized['redesign_ranges'] == 'A2A,AA2'
    assert request['rfd3']['select_fixed_atoms']['A2A'] == []
    assert request['rfd3']['select_fixed_atoms']['A2'] == ['ALL']
    saved = {key: value for key, value in normalized.items() if key in service.RFD3_SCIENTIFIC_PARAM_KEYS}
    monkeypatch.setattr(service, 'resolve_interface_shell', lambda *args: (_ for _ in ()).throw(AssertionError('replay re-resolved')))
    assert service.normalize_local_redesign_params(saved, job_name='shell')[1:] == (request, digest)
    assert source.read_bytes() == before


def test_native_recall_false_null_and_symmetry_selector_lowering(tmp_path):
    request = build_request(local('native_recall', contig='A2,A5,/0,B7,B9,/0,C4',
        select_fixed_atoms=False, select_unfixed_sequence='B7', sequence_policy='explicit_positions',
        partial_t=None, symmetry={'id': 'C2', 'is_symmetric_motif': False, 'is_unsym_motif': 'B7'},
        plddt_enhanced=False, ori_token=[0, 0, 0]))
    assert request['rfd3']['select_fixed_atoms'] is False
    assert request['rfd3']['partial_t'] is None
    assert request['rfd3']['plddt_enhanced'] is False
    source = tmp_path / 'source.cif'
    source.write_text(cif_source())
    mapping = materialize_source(source, tmp_path / 'native.cif')
    native = conditioning_settings({'symmetry': {'id': 'C2', 'is_symmetric_motif': False, 'is_unsym_motif': 'AA2'},
                                   'select_hotspots': ['A2A', 'AA2'], 'redesign_motif_sidechains': 'AA2'})
    lowered = lower_native(native, mapping, 'native.cif')
    assert lowered['symmetry']['is_unsym_motif'] == 'B1'
    assert lowered['symmetry']['is_symmetric_motif'] is False
    assert lowered['select_hotspots'] == 'A2,B1'
    assert lowered['redesign_motif_sidechains'] == 'B1'
    assert native['symmetry']['is_unsym_motif'] == 'AA2'


def test_cad_projection_presence_precedence_and_replay_boundary():
    params = {'backend': 'disco', 'design_task': 'dna_conditioned', 'disco_effort': 'fast',
              'disco_seeds': '0,19', 'disco_use_deepspeed_evo_attention': False,
              'laproteina_seed': 0, 'laproteina_self_cond': False, 'laproteina_bb_ca_gt_clamp_val': None,
              'laproteina_settings': {'num_steps': 17}, 'disco_settings': {'N_step': 0, 'output_format': None}}
    before = deepcopy(params)
    projection = settings_projection(params)
    assert projection['pcad_laproteina_settings_json'] == {'seed': 0, 'self_cond': False, 'bb_ca_gt_clamp_val': None, 'num_steps': 17}
    assert projection['pcad_disco_settings_json']['seeds'] == [0, 19]
    assert projection['pcad_disco_settings_json']['use_deepspeed_evo_attention'] is False
    assert projection['pcad_disco_settings_json']['N_step'] == 0
    assert projection['pcad_disco_settings_json']['output_format'] is None
    fresh = bind_new_cad_settings('protein_cad_experimental', 'generate', params)
    assert replay_cad_settings('protein_cad_experimental', 'generate', fresh, fresh) == fresh
    old = {k: v for k, v in params.items() if k != 'disco_settings'}
    replay = replay_cad_settings('protein_cad_experimental', 'generate', old, old)
    assert replay['pcad_disco_settings_json']['effort'] == 'max'
    assert replay_cad_settings('protein_cad_experimental', 'generate', old, old, overrides={'disco_effort': 'fast'})['pcad_disco_settings_json']['effort'] == 'fast'
    assert replay['pcad_disco_settings_json']['seeds'] == [0, 19]
    assert params == before
    for task in ['custom_json', 'unconditional']:
        source = {**old, 'design_task': task}
        assert replay_cad_settings('protein_cad_experimental', 'generate', source, source)['pcad_disco_settings_json']['effort'] == 'fast'
    empty = bind_new_cad_settings('protein_cad_experimental', 'generate', {'backend': 'disco', 'design_task': 'dna_conditioned'})
    assert empty['pcad_disco_settings_json'] == {}
    assert replay_cad_settings('protein_cad_experimental', 'generate', empty, empty) == empty
