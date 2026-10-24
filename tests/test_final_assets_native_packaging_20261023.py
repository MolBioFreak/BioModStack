"""Native image-only checks; API discovery uses canonical host dependencies.

Keep scientific runtimes free of controller API imports. No checkpoint loads.
"""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('complexa_packaging_owner', ROOT / 'tests/test_complexa_runtime_packaging.py')
assert spec is not None and spec.loader is not None
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
image = owner.image


def test_complexa_image_native_source_composition_and_prepare_only(image, tmp_path):
    output = owner.run_image(
        image, tmp_path, 'env', 'PYTHONPATH=/bms:/opt/complexa/src:/opt/complexa',
        'COMPLEXA_TEST_SOURCE=/opt/complexa', 'python', '-m', 'pytest', '-q',
        '-o', 'addopts=', '-p', 'no:cacheprovider', '--basetemp=/qualification/pytest',
        '/bms/tests/test_complexa_contract.py', '-k', 'not prepare_only_and_api_discovery',
    )
    assert '9 passed, 1 deselected' in output
    code = '''
from pathlib import Path
from scripts.lib.complexa_contract import Request
from scripts.bms_complexa.run import run
receipt = run(Request(task='AME', task_name='M0024_1nzy_v3', reward=None), upstream=Path('/opt/complexa'), weights=Path('/qualification/weights'), output=Path('/qualification/result'), prepare_only=True)
assert receipt['execution'] == 'not_started'
assert receipt['selected_dependencies'] == ['complexa_ame']
assert receipt['commands'][0][:2] == ['complexa', 'generate']
assert not Path('/qualification/result/complexa-results.json').exists()
assert Path('/qualification/result/native/configs/bms_complexa.yaml').exists()
print('NATIVE_PREPARE_ONLY_OK')
'''
    assert 'NATIVE_PREPARE_ONLY_OK' in owner.run_image(image, tmp_path, 'env', 'PYTHONPATH=/bms:/opt/complexa/src:/opt/complexa', 'python', '-c', code)


def test_complexa_api_discovery_with_controller_interpreter():
    sys.path.insert(0, str(ROOT / 'platform/api'))
    sys.path.insert(0, str(ROOT))
    from services import complexa
    from scripts.lib.complexa_contract import TASKS
    view = complexa.discovery()
    assert set(view['profiles']) == set(TASKS)
    assert view['native_profile_defaults']['tasks']['AME']['reward_model'] is None
    assert view['schema']['additionalProperties'] is False
    import pytest
    with pytest.raises(ValueError):
        complexa.normalize_job_request('evaluate', {'task': 'AME', 'task_name': 'M0024_1nzy_v3'})
