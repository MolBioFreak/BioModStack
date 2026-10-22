"""Fresh discovery differs from retained request compatibility, not admission."""
from copy import deepcopy

import pytest

from routers import models
from services.boltzgen_request_compatibility import (
    BOLTZGEN_GENERATION_PROTOCOLS,
    normalize_boltzgen_generation_request,
)


@pytest.mark.asyncio
async def test_fresh_discovery_excludes_legacy_but_detail_and_settings_preserve_it():
    expected = set(BOLTZGEN_GENERATION_PROTOCOLS) - {'backbone_docking'}
    for compact in (True, False):
        listing = await models.list_models(include_experimental=True, compact=compact)
        boltzgen = next(item for item in listing if item['id'] == 'boltzgen')
        assert {mode['id'] for mode in boltzgen['modes']} == expected
    modes = await models.get_model_modes('boltzgen')
    assert {mode['id'] for mode in modes['modes']} == expected
    detail = await models.get_model('boltzgen')
    historical = next(mode for mode in detail['modes'] if mode['id'] == 'backbone_docking')
    assert historical['compatibility_only'] is True
    assert 'Zero-design' in historical['description']
    for mode in detail['modes']:
        inventory = await models.get_generation_settings('boltzgen', mode['id'])
        assert inventory['compatibility_only'] == mode['compatibility_only']
        assert inventory['parameters']
    # Five actual native protocols; NTP and coordinate-conditioned ligand are
    # supported input variants, not invented extra native protocols.
    assert {BOLTZGEN_GENERATION_PROTOCOLS[m] for m in expected} == {
        'protein-anything', 'peptide-anything', 'nanobody-anything',
        'antibody-anything', 'protein-small_molecule',
    }


def test_project_picker_omits_legacy_but_saved_setup_lookup_survives():
    from services.protein_project_capabilities import protein_capability_inventory, protein_capability_record, protein_setup_parameter_schema
    legacy = 'protein.native.boltzgen.backbone_docking'
    for ready_only in (False, True):
        inventory = protein_capability_inventory(project_ready_only=ready_only)
        assert legacy not in {item['capability_id'] for item in inventory['capabilities']}
        assert all(legacy not in item.get('follow_up_compatible_capability_ids', [])
                   for item in inventory['capabilities'])
    retained = protein_capability_record(legacy)
    assert 'compatibility only' in retained['label']
    assert retained['project_setup_state'] == 'ready'
    assert protein_setup_parameter_schema(legacy)['x-bms-native-editor-draft'] is True


def test_legacy_request_is_not_refused_or_silently_converted_to_redesign():
    request = {'input_pdb': 'inputs/backbone.pdb', 'ntp_type': 'ATP',
               'protocol': 'auto', 'skip_inverse_folding': False,
               'alpha': 0, 'min_plddt': None, 'yaml_config': 'inputs/explicit-native.yaml'}
    original = deepcopy(request)
    normalized = normalize_boltzgen_generation_request('backbone_docking', request)
    assert request == original
    assert normalized['boltzgen_mode'] == 'backbone_docking'
    assert normalized['boltzgen_protocol'] == 'auto'
    assert normalized['boltzgen_input_pdb'] == request['input_pdb']
    assert normalized['boltzgen_yaml_config'] == request['yaml_config']
    assert normalized['boltzgen_skip_inverse_folding'] is False
    assert normalized['boltzgen_alpha'] == 0
    assert normalized['boltzgen_min_plddt'] is None
    assert normalized.get('boltzgen_scaffold_design_ranges') is None
    assert normalize_boltzgen_generation_request('backbone_docking', normalized) == normalized
