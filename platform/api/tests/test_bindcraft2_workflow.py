"""BC2 model-owned workflow image and executable registry contracts (no GPU claim)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_registry_declares_campaign_and_model_owned_native_lifecycle_routes():
    model = yaml.safe_load((ROOT / "platform/api/config/models/bindcraft2.yaml").read_text())
    assert model["id"] == "bindcraft2"
    assert model["container"] == "bindcraft2.sif"
    assert model["enabled"] is True and model["public_launch"] is True
    assert [mode['id'] for mode in model['modes']] == ['campaign', 'resume', 'rank', 'filter', 'campaign_output', 'archive', 'unarchive', 'score']
    assert model['modes'][0]['params'] == ['bindcraft2_settings', 'bc2_preview_digest']
    assert {param['name'] for param in model['params']} == {'bindcraft2_settings', 'bc2_preview_digest', 'bc2_source_job_id', 'bc2_action_options'}
    assert all(mode['params'] == ['bc2_source_job_id', 'bc2_action_options'] for mode in model['modes'][1:])
    assert next(p for p in model['params'] if p['name'] == 'bc2_preview_digest')['pattern'] == '^[0-9a-f]{64}$'


