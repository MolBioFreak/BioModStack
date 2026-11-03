"""The compiler's per-task weight placement hint must not be visible to directory-wide JSON scanners.

Live finding (A6000, FilterMPNN): the hint was written as `.bms-task-weights.json` (a JSON list; now extensionless `.bms-task-weights`) into
every task directory. FilterMPNN/FilterFAMPNN scan their working directory for *.json, hit the list,
died with TypeError behind `| tee`, and the review gate never opened. Real setup script + real filters.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _setup_script(tmp_path):
    from native_components import native_resource_policy
    from scripts.lib.component_adapter import native_resource_config
    component = dict(authority='modules/proteinmpnn.nf:FilterMPNN', selection_json={},
                     resources_json=json.loads(native_resource_policy({})), dependency_ids=[])
    plan = {'metadata': {'static_components': [component], 'dependencies': []}}
    config = native_resource_config(plan, {'required': {'cpus': 1, 'memory_bytes': 1024}},
                                    str(tmp_path / 'lock'), ROOT)
    return ast.literal_eval(re.search(r'return (.+?) \+ \(nativeSetup', config)[1])


@pytest.mark.parametrize('script,prefix,extra', [
    ('filter_mpnn.py', 'mpnn_', {}),
    ('filter_fampnn.py', '', {'fampnn_avg_psce': 1.0}),
])
def test_native_filter_survives_the_compiler_task_hint(tmp_path, script, prefix, extra):
    work = tmp_path / 'task'
    work.mkdir()
    for index in range(2):
        name = f'design_{index}'
        (work / f'{name}.pdb').write_text('ATOM\n')
        (work / f'{prefix}{name}.json').write_text(json.dumps(
            {'design': name, 'sequence': 'ACDE', 'score': '0.5', **extra}))
    subprocess.run(['bash', '-euc', _setup_script(tmp_path)], cwd=work, check=True)
    assert (work / '.bms-task-weights').exists()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), '--jsons', '.', '--pdbs', '.',
                             '--output-dir', 'filtered_output'], cwd=work, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Pre-filter designs: 2' in result.stdout
    assert sorted(p.name for p in (work / 'filtered_output').glob('*.pdb')) == ['design_0.pdb', 'design_1.pdb']


def test_hint_round_trips_through_the_container_reader(tmp_path):
    from tools import bms_container as c
    work = tmp_path / 'task'
    work.mkdir()
    subprocess.run(['bash', '-euc', _setup_script(tmp_path)], cwd=work, check=True)
    assert c.task_weight_paths(work) == []
