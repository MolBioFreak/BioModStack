"""Source-qualified discovery observations, not request defaults or inference."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routers import models
from scripts.lib.laproteina_contract import CONTROLS, PROFILES, SOURCE_REVISION, native_profile_defaults


@pytest.mark.asyncio
async def test_http_profile_observations():
    app = FastAPI()
    app.include_router(models.router, prefix='/models')
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        for url in ('/models/protein_modification_experimental/generation-settings?mode=de_novo_design&generator=laproteina',):
            response = await client.get(url)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['native_profile_defaults'] == native_profile_defaults()
            assert all('default' not in prop for prop in data['parameter_schema']['properties'].values())
            profiles = data['native_profile_defaults']['profiles']
            assert profiles['ucond_notri_long']['bb_ca_sc_scale_noise'] == 0.15
            assert profiles['ucond_notri_long']['local_latents_sc_scale_noise'] == 0.05
            assert profiles['ucond_notri']['bb_ca_sc_scale_noise'] == 0.1
            assert profiles['motif_idx_tip']['bb_ca_center_every_step'] is False
            assert profiles['ucond_notri']['bb_ca_center_every_step'] is True
            assert profiles['motif_idx_tip']['max_nsamples_per_batch'] == 5
            assert profiles['ucond_notri']['max_nsamples_per_batch'] == 10
            assert profiles['ucond_notri']['bb_ca_gt_clamp_val'] is None


def test_snapshot_against_pinned_source():
    source = os.environ.get('LAPROTEINA_TEST_SOURCE')
    if not source:
        pytest.skip('LAPROTEINA_TEST_SOURCE must identify pinned upstream source')
    hydra = pytest.importorskip('hydra')
    from omegaconf import OmegaConf
    root = Path(source)
    # The reviewed source export includes the GitHub commit tree and blob IDs.
    tree = json.loads((root / 'tree.json').read_text())
    assert tree['sha'] == SOURCE_REVISION
    files = [item for item in tree['tree'] if item['type'] == 'blob' and item['path'].startswith('configs/')]
    assert files
    for item in files:
        content = (root / item['path']).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        assert actual == item['sha'], item['path']
    assert hydra.__version__ == '1.3.1'
    expected = native_profile_defaults()['profiles']
    with hydra.initialize_config_dir(config_dir=str(root / 'configs'), version_base='1.3'):
        for name in PROFILES:
            cfg = hydra.compose(config_name='inference_' + name)
            actual = {key: OmegaConf.select(cfg, spec['native'], throw_on_missing=True)
                      for key, spec in CONTROLS.items()}
            assert actual == expected[name], name
