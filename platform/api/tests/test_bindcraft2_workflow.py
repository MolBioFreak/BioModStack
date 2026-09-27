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


def test_native_leaf_has_one_runtime_and_pinned_image():
    entry = (ROOT / "workflows/bindcraft2.nf").read_text()
    module = (ROOT / "modules/bindcraft2.nf").read_text()
    image = (ROOT / "apptainer/bindcraft2.def").read_text()
    assert "RunBindCraft2" in entry and "bc2_compilation" in entry
    assert "--native-source /opt/bindcraft --execute" in module
    assert "run_bindcraft2_campaign.py" in module
    assert "CUDA_VISIBLE_DEVICES=" in module
    assert "ext { containerOptions" in module
    assert "JAX_COMPILATION_CACHE_DIR=/cache/bindcraft2/compile" in module
    assert "BINDCRAFT_AF2_PARAMS=${params.weights_root}/alphafold/params" in module
    assert "bindcraft2.sif" in module
    assert "d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f" in image
    assert "'/opt/bindcraft[cuda13]'" in image
    assert "services/bindcraft2_campaign.py" not in module + entry
    assert not (ROOT / "platform/api/services/bindcraft2_campaign.py").exists()
