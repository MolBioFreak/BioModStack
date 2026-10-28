"""Pinned source composition differential, without importing native model code."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.lib.disco_contract import native_profile_reference, _SETTINGS_HELP, _GUIDANCE_HELP, _SEQUENCE_HELP


def test_source_compositions():
    config = os.environ.get('DISCO_CONFIG_DIR')
    python = os.environ.get('DISCO_HYDRA_PYTHON')
    if not config or not python:
        pytest.skip('Set DISCO_CONFIG_DIR and DISCO_HYDRA_PYTHON for pinned source composition')
    root = Path(config).resolve().parent
    tree = json.loads((root / 'tree.json').read_text())
    assert tree['sha'] == native_profile_reference()['source_revision']
    blobs = [item for item in tree['tree'] if item['type'] == 'blob' and item['path'].startswith('configs/') and item['path'].endswith(('.yaml', '.yml'))]
    assert blobs
    for item in blobs:
        content = (root / item['path']).read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == item['sha'], item['path']
    script = '''
import json,sys
from hydra import initialize_config_dir,compose
from omegaconf import OmegaConf
out={}
with initialize_config_dir(config_dir=sys.argv[1],version_base='1.3'):
 for experiment in ('designable','diverse'):
  for effort in ('fast','max'):
   out[experiment+'/'+effort]=OmegaConf.to_container(compose(config_name='inference',overrides=['experiment='+experiment,'effort='+effort]),resolve=False)
print(json.dumps(out))
'''
    composed = json.loads(subprocess.check_output([python, '-c', script, str(Path(config).resolve())], text=True))
    paths = {k: v[1] for k, v in _SETTINGS_HELP.items() if k not in ('experiment', 'effort')}
    paths.update({'noisy_guidance.' + k: 'sample_diffusion.noisy_guidance.' + k for k in _GUIDANCE_HELP})
    paths.update({'sequence_sampling.' + k: 'sequence_sampling_strategy.' + ('sequence_noise_scheduler.power' if k == 'power' else k) for k in _SEQUENCE_HELP})
    variants = native_profile_reference()['variants']
    assert set(variants) == set(composed)
    for variant, config_data in composed.items():
        expected = {}
        for key, path in paths.items():
            node = config_data
            for part in path.split('.'):
                if not isinstance(node, dict) or part not in node:
                    break
                node = node[part]
            else:
                if (node is None or isinstance(node, (str, int, float, bool))) and not (isinstance(node, str) and '${' in node):
                    expected[key] = {'path': path, 'value': node}
        assert variants[variant] == expected
        assert 'N_step' in expected and 'N_cycle' in expected
        assert 'noisy_guidance.guide_struct' not in expected
        assert all('guid_struct' not in entry['path'] for entry in expected.values())
