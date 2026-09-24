"""Public discovery/default closure; fixtures do not execute native science."""
from copy import deepcopy
from pathlib import Path
import re

import pytest
import yaml

from services.boltzgen_request_compatibility import (
    BOLTZGEN_GENERATION_METADATA_FIELDS, BOLTZGEN_GENERATION_PROTOCOLS,
    BOLTZGEN_PARAMETER_ALIASES, boltzgen_generation_inventory,
    compile_boltzgen_settings, normalize_boltzgen_generation_request as normalize,
    parameter_contract,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('mode', BOLTZGEN_GENERATION_PROTOCOLS)
def test_complete_defaults_are_closed_idempotent_and_discoverable(mode):
    inventory = boltzgen_generation_inventory(mode)
    fields = parameter_contract(mode)
    names = {p['name'] for p in fields}
    assert len(names) == len(fields)
    assert inventory['parameters'] == fields
    assert all(p['source'] and p['control'] and 'default' in p for p in fields)
    out = normalize(mode, {})
    assert set(out) == names | {'boltzgen_mode', 'boltzgen_generation_mode'}
    assert normalize(mode, out) == out
    assert out['boltzgen_protocol'] == BOLTZGEN_GENERATION_PROTOCOLS[mode]
    assert out['boltzgen_min_plddt'] is None
    assert out['boltzgen_step_scale'] is None
    assert out['boltzgen_filter_biased'] is True
    assert out['boltzgen_skip_inverse_folding'] is False
    assert out['boltzgen_alpha'] == .01
    assert compile_boltzgen_settings(out)['boltzgen_rank_design_ptm_weight'] == 1
    inventory['parameters'][0]['default'] = 'mutation'
    assert parameter_contract(mode)[0]['default'] is None


def test_explicit_zero_false_null_and_alias_precedence():
    request = {'alpha': 0, 'noise_scale': 0, 'step_scale': 0,
               'filter_biased': False, 'skip_inverse_folding': False,
               'rank_design_ptm_weight': None, 'min_plddt': None,
               'max_rmsd': 0, 'batch_size': 2, 'diffusion_batch_size': 3,
               'refolding_rmsd_threshold': 0, 'protocol': 'auto'}
    original = deepcopy(request)
    result = normalize('protein_binder', request)
    for key, value in request.items():
        assert result[BOLTZGEN_PARAMETER_ALIASES[key]] == value
    assert request == original
    with pytest.raises(ValueError):
        normalize('protein_binder', {'filter_biased': False, 'boltzgen_filter_biased': 0})
    assert normalize('protein_binder', result) == result
    assert normalize('protein_binder', {'alpha': 0, 'boltzgen_alpha': 0})['boltzgen_alpha'] == 0
    with pytest.raises(ValueError, match='Conflicting'):
        normalize('protein_binder', {'alpha': 0, 'boltzgen_alpha': .5})


def test_saved_rank_override_precedes_injected_defaults():
    value = 'conf_score=2 rmsd=none'
    out = normalize('peptide_binder', {'boltzgen_metrics_override': value})
    assert out['boltzgen_metrics_override'] == value
    assert out['boltzgen_rank_affinity_probability_weight'] == 2
    assert out['boltzgen_rank_filter_rmsd_weight'] is None
    assert normalize('peptide_binder', out) == out
    with pytest.raises(ValueError, match='conflicting'):
        normalize('peptide_binder', {'metrics_override': value, 'rank_filter_rmsd_weight': 3})


@pytest.mark.parametrize('params', [
    {'boltzgen_unknown': 1}, {'unknown': 1}, {'num_designs': True},
    {'num_designs': '10'}, {'num_designs': 1.2}, {'alpha': float('nan')},
    {'alpha': None}, {'skip_inverse_folding': 'false'}, {'batch_size': 0},
    {'checkpoint_mode': 'invented'}, {'rank_design_ptm_weight': 0},
    {'target_chains': ['A']}, {'boltzgen_mode': 'nanobody_binder'},
])
def test_unknown_wrong_types_and_incompatible_values_are_not_dropped(params):
    with pytest.raises(ValueError):
        normalize('protein_binder', params)


@pytest.mark.parametrize('value', [0, 70])
def test_no_unavailable_plddt_threshold_is_silently_disabled(value):
    with pytest.raises(ValueError, match='native pLDDT'):
        normalize('nanobody_binder', {'min_plddt': value})
    with pytest.raises(ValueError, match='native pLDDT'):
        normalize('nanobody_binder', {'additional_filters': 'plddt>70'})


def test_mode_specific_sources_and_historical_requests():
    generic = {'target_pdb': '/owned/target.cif', 'scaffold_path': '/owned/scaffold.cif',
               'scaffold_chain': 'a1', 'scaffold_design_ranges': '2..4'}
    for mode in ['protein_binder', 'peptide_binder']:
        assert normalize(mode, generic)['boltzgen_scaffold_chain'] == 'a1'
        with pytest.raises(ValueError):
            normalize(mode, {'cdr_h1_length': '5-8'})
    with pytest.raises(ValueError):
        normalize('nanobody_binder', generic)
    assert normalize('nanobody_binder', {'nanobody_framework': 'XXX'})['boltzgen_nanobody_framework'] == 'XXX'
    for specs in ('[]', []):
        assert normalize('nanobody_binder', {'nanobody_scaffold_specs': specs})['boltzgen_nanobody_scaffold_specs'] == specs
    with pytest.raises(ValueError):
        normalize('ligand_binder', {'ligand_smiles': 'CC'})
    model = yaml.safe_load((ROOT / 'platform/api/config/models/boltzgen.yaml').read_text())
    legacy = {p['name']: p for p in model['params']}
    assert legacy['min_plddt']['default'] == 70
    assert legacy['scaffold_length']['default'] == '80-120'
    assert BOLTZGEN_PARAMETER_ALIASES['ligand_description'] == 'boltzgen_ligand_smiles'
    assert BOLTZGEN_PARAMETER_ALIASES['ntp_type'] == 'boltzgen_ntp_type'


def test_generated_receipts_are_retained_not_reinterpreted():
    metadata = {key: {'fixture': 'unchanged'} for key in BOLTZGEN_GENERATION_METADATA_FIELDS
                if key not in {'boltzgen_mode', 'boltzgen_generation_mode'}}
    out = normalize('protein_binder', metadata)
    for key, value in metadata.items():
        assert out[key] == value
        assert out[key] is not value


def test_every_reachable_run_and_selection_parameter_is_accounted_for():
    text = (ROOT / 'modules/boltzgen.nf').read_text()
    text = text[text.index('process RunBoltzGen'):text.index('// SWA')]
    referenced = set(re.findall(r"params\.(?:get|containsKey)\('([^']+)'\)", text))
    known = {p['name'] for p in parameter_contract('protein_binder')}
    # extra_config is the historical raw argv escape, not an operator setting.
    # The new closed contract exposes every supported wrapper argument instead.
    system = {'core_protein_scientific_contract', 'boltzgen_prepared_sha256', 'boltzgen_extra_config'}
    assert referenced <= known | system
    with pytest.raises(ValueError, match='Unknown'):
        normalize('protein_binder', {'boltzgen_extra_config': '--unsupported'})
