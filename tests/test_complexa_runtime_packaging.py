"""Real image qualification; opt in with COMPLEXA_TEST_IMAGE (no inference)."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def image():
    value = os.environ.get("COMPLEXA_TEST_IMAGE")
    if not value:
        pytest.skip("COMPLEXA_TEST_IMAGE is required for real native image checks")
    assert Path(value).exists()
    return value


def run_image(image, tmp_path, *args):
    command = [
        "apptainer", "exec", "--cleanenv", "--no-home",
        "--bind", f"{tmp_path}:/qualification", "--bind", f"{ROOT}:/bms:ro",
        "--pwd", "/qualification",
        "--env", "XDG_CACHE_HOME=/qualification/cache",
        "--env", "TORCH_EXTENSIONS_DIR=/qualification/torch-extensions",
        "--env", "TMPDIR=/qualification", "--env", "MAX_JOBS=2",
        "--env", "JAX_PLATFORMS=cpu", "--env", "CUDA_VISIBLE_DEVICES=",
        image, "env", "HOME=/qualification", *args,
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=900)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_embedded_build_inputs(image, tmp_path):
    definition = subprocess.run(
        ["apptainer", "inspect", "--deffile", image],
        text=True, capture_output=True, check=True,
    ).stdout
    assert definition.strip() == (ROOT / "apptainer/complexa.def").read_text().strip()
    code = r'''
from pathlib import Path
import hashlib, json, subprocess
for name in ('requirements.txt', 'build.txt', 'NOTICES.md'):
    assert Path('/opt/locks', name).read_bytes() == Path('/bms/apptainer/locks', 'complexa-' + name).read_bytes()
metadata = json.loads(Path('/bms/scripts/bms_complexa/assets.json').read_text())
git = ['git', '-c', 'safe.directory=/opt/complexa', '-c', 'lfs.storage=/qualification/git-lfs', '-C', '/opt/complexa']
assert subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() == metadata['source']['revision']
subprocess.run(git + ['diff', '--exit-code', 'HEAD'], check=True)
for name, digest in metadata['upstream_files'].items():
    assert hashlib.sha256(Path('/opt/complexa', name).read_bytes()).hexdigest() == digest, name
print('BUILD_INPUTS_MATCH')
'''
    assert "BUILD_INPUTS_MATCH" in run_image(image, tmp_path, "python", "-c", code)


def test_real_cli_import_and_locked_distributions(image, tmp_path):
    output = run_image(image, tmp_path, "complexa", "--help")
    assert "generate" in output and "evaluate" in output
    for command in ("design", "generate", "filter", "evaluate", "analyze", "analysis"):
        assert command in run_image(image, tmp_path, "complexa", command, "--help")
    code = r'''
import importlib, importlib.metadata, json, re
from pathlib import Path
for line in (Path('/opt/locks/requirements.txt').read_text() + Path('/opt/locks/build.txt').read_text()).splitlines():
    match = re.match(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', line)
    if match:
        name, expected = match.groups()
        assert importlib.metadata.version(name) == expected, (name, expected)
modules = [
    'proteinfoundation.generate', 'proteinfoundation.filter',
    'proteinfoundation.evaluate', 'proteinfoundation.analyze',
    'proteinfoundation.rewards.alphafold2_reward',
    'proteinfoundation.rewards.rf3_reward',
    'proteinfoundation.rewards.tmol_reward',
    'proteinfoundation.rewards.bioinformatics_reward',
    'proteinfoundation.search.search_factory', 'rf3',
]
for module in modules:
    importlib.import_module(module)
    print(module, 'OK', flush=True)
assert importlib.metadata.version('proteinfoundation') == '1.1.0'
assert importlib.metadata.version('colabdesign') == '1.1.1.1'
tmol_source = json.loads(importlib.metadata.distribution('tmol').read_text('direct_url.json'))
assert tmol_source['vcs_info']['commit_id'] == 'd8a6f7f9649d36e74440bca25246ee7c467ce490'
print('LOCK_AND_IMPORTS_OK')
'''
    assert "LOCK_AND_IMPORTS_OK" in run_image(image, tmp_path, "python", "-u", "-c", code)


def test_all_native_profile_compositions_in_image(image, tmp_path):
    # Scientific images contain native dependencies, not the controller API.
    # Keep the existing profile/search/source assertions in their native owner;
    # exercise the mixed owner's API half separately with controller deps below.
    output = run_image(
        image, tmp_path, "env", "PYTHONPATH=/bms:/opt/complexa/src:/opt/complexa",
        "COMPLEXA_TEST_SOURCE=/opt/complexa", "python", "-m", "pytest",
        "-q", "-o", "addopts=", "-p", "no:cacheprovider",
        "--basetemp=/qualification/pytest", "/bms/tests/test_complexa_contract.py",
        "-k", "not prepare_only_and_api_discovery",
    )
    assert "9 passed, 1 deselected" in output
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
    assert 'NATIVE_PREPARE_ONLY_OK' in run_image(image, tmp_path, 'env', 'PYTHONPATH=/bms:/opt/complexa/src:/opt/complexa', 'python', '-c', code)


def test_complexa_api_discovery_with_controller_interpreter():
    sys.path.insert(0, str(ROOT / 'platform/api'))
    sys.path.insert(0, str(ROOT))
    from services import complexa
    from scripts.lib.complexa_contract import TASKS
    view = complexa.discovery()
    assert set(view['profiles']) == set(TASKS)
    assert view['native_profile_defaults']['tasks']['AME']['reward_model'] is None
    assert view['schema']['additionalProperties'] is False
    with pytest.raises(ValueError):
        complexa.normalize_job_request('evaluate', {'task': 'AME', 'task_name': 'M0024_1nzy_v3'})
