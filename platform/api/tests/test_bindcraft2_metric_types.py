"""Pinned expression resolution and honest unresolved native types."""
import json
from pathlib import Path

import pytest

from services.bindcraft2_typed import schema, validate_request


def test_all_fourteen_nonliteral_metric_defaults_are_accounted_for():
    data = schema()
    unresolved = []
    resolved = []
    for group, metrics in data['registered_metrics'].items():
        for metric, entry in metrics.items():
            for parameter, descriptor in entry['params'].items():
                if descriptor['default_literal'] is None:
                    (resolved if descriptor.get('request_types') else unresolved).append((group, metric, parameter))
    assert len(resolved) == 14
    assert unresolved == []
    assert data['registered_metrics']['filters']['Binder_RMSD']['params']['reference_state']['resolved_default'] == 'binder_alone'
    assert data['registered_metrics']['filters']['Epitope_Residues_Contacted']['params']['epitope_cutoff']['resolved_default'] == 10.0
    assert data['registered_metrics']['losses']['binder_contacts']['params']['contact_residue_count']['native_default_encoding'] == '+Infinity'
    assert 'portable numeric interface masks' in data['coverage_status']
    assert 'registry-owned' in data['coverage_status']


def test_metric_request_accepts_source_typed_overrides():
    base = {'max_trajectories': 2}
    for additional in (
        {'filters': {'Binder_RMSD': {'threshold': 1.0, 'params': {'reference_state': 'binder_alone'}}}},
        {'filters': {'Epitope_Residues_Contacted': {'threshold': 4, 'params': {'epitope_cutoff': 9.0}}}},
        {'filters': {'Unbound_Binder_pLDDT': {'threshold': 0.8, 'params': {'chain': None}}}},
        {'losses': {'binder_pae': {'params': {'chain': None}}}},
        {'losses': {'binder_contacts': {'params': {'contact_residue_count': 12}}}},
    ):
        assert validate_request({**base, **additional}) == {**base, **additional}
    for additional in (
        {'losses': {'binder_contacts': {'params': {'contact_residue_count': float('inf')}}}},
        {'filters': {'Binder_RMSD': {'params': {'reference_state': 7}}}},
        {'filters': {'Epitope_Residues_Contacted': {'params': {'epitope_cutoff': '10'}}}},
    ):
        with pytest.raises(ValueError):
            validate_request({**base, **additional})


def test_source_annotation_is_not_a_default_for_untyped_campaign_fields():
    data = schema()
    original = json.loads((Path(__file__).parents[1] / 'config/models/bindcraft2_native_inventory.json').read_text())
    assert set(data['fields']) == set(original['fields'])
    register = data['typed_evidence']['top_level_unresolved']
    assert set(register) == set(original['unresolved_fields'])
    assert len(register) == 109
    assert sum(entry['classification'] == 'system_or_runtime_owned' for entry in register.values()) == 8
    assert sum(entry['classification'] != 'system_or_runtime_owned' for entry in register.values()) == 101
    assert register['cyclic_offset_mode']['source_observations']
    assert data['fields']['number_of_final_designs']['status'] == 'typed'
    assert data['fields']['number_of_final_designs']['has_native_default'] is False
    assert validate_request({'max_trajectories': 2, 'number_of_final_designs': 1, 'oligomer_tie': 'none'})
    with pytest.raises(ValueError, match='unknown native choice'):
        validate_request({'max_trajectories': 2, 'cyclic_offset_mode': 'invented'})
    assert data['fields']['binder_shapes']['status'] == 'typed'
    assert validate_request({'max_trajectories': 2, 'binder_shapes': [['complex']]})
    assert data['fields']['relax_steps']['status'] == 'typed'
    assert validate_request({'max_trajectories': 2, 'relax_steps': 4})
    with pytest.raises(ValueError, match='system-owned'):
        validate_request({'max_trajectories': 2, 'gpu_ids': '0'})
