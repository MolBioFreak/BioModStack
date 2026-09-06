"""Pure model regressions; no application startup or robot contact.

Fixture: GET robot:8123/operator/dashboard, 2026-09-06, live ea848 predecessor.
Candidate ddf0b490 still emits source_profile.get('axis_max_source'); the
production X profile does not set that key, so its published value is null.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorControlCatalog, OperatorDashboard


def dashboard_payload():
    return json.loads((Path(__file__).parent / 'fixtures' / 'bioxp_legacy_dashboard_source_limit_null.json').read_text())


def test_live_dashboard_preserves_unknown_source_limit_authority():
    payload = dashboard_payload()
    result = OperatorDashboard.model_validate(payload)
    assert result.x_axis.provider.root.source_limit_authority is None
    assert result.x_axis.provider.root.board_generation_fresh is False
    assert result.x_axis.provider.root.lifecycle.state == 'unprepared'
    assert result.x_axis.provider.root.physical_position_verified is False


def test_catalog_embedded_live_dashboard_validates():
    dashboard = dashboard_payload()
    OperatorControlCatalog.model_validate({
        'schema_name': 'bioxp.operator_control_catalog',
        'schema_version': 'bioxp.operator_control_catalog.v1',
        'machine_serial': '206', 'ownership_generation': 1,
        'registry_sha256': 'unavailable', 'evidence_lock_sha256': 'unavailable',
        'source_authority_verified': False, 'dashboard': dashboard, 'actions': [],
    })


def test_older_projection_without_source_limit_authority_remains_valid():
    payload = dashboard_payload()
    del payload['x_axis']['provider']['source_limit_authority']
    OperatorDashboard.model_validate(payload)


@pytest.mark.parametrize('value', ['invented_authority', True, 1, {}, []])
def test_unknown_nonnull_source_limit_authority_is_not_admitted(value):
    payload = dashboard_payload()
    payload['x_axis']['provider']['source_limit_authority'] = value
    with pytest.raises(ValidationError):
        OperatorDashboard.model_validate(payload)


@pytest.mark.parametrize(('key', 'value'), [
    ('invented_field', None), ('source_max_steps', 999999),
    ('physical_position_verified', True), ('board_generation_fresh', 'true'),
])
def test_provider_strictness_is_unchanged(key, value):
    payload = dashboard_payload()
    payload['x_axis']['provider'][key] = value
    with pytest.raises(ValidationError):
        OperatorDashboard.model_validate(payload)
