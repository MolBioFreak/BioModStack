"""Runner failures must not be confused with executed scientific checks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/run_plasmid_candidate_tests.py'


def load_runner():
    spec = importlib.util.spec_from_file_location('candidate_runner_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_pytest_interpreter_reports_not_run(tmp_path):
    env = tmp_path / 'empty-env'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(env)
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    out = tmp_path / 'missing-pytest'
    environ = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    result = subprocess.run([str(python), str(SCRIPT), '--output-dir', str(out)],
                            capture_output=True, text=True, env=environ)
    assert result.returncode == 2
    report = json.loads((out / 'candidate-test-report.json').read_text())
    assert report['status'] == 'preflight_failed'
    assert report['preflight']['status'] == 'failed'
    assert report['test_exit_code'] is None
    assert report['test_totals'] is None
    assert report['pytest_version'] is None
    assert not (out / 'junit.xml').exists()
    assert 'TESTS NOT RUN' in result.stderr
    # Reusing a failed evidence directory must not erase its earlier diagnosis.
    again = subprocess.run([str(python), str(SCRIPT), '--output-dir', str(out)],
                           capture_output=True, text=True, env=environ)
    assert again.returncode == 2
    assert 'already contains evidence' in again.stderr
    assert json.loads((out / 'candidate-test-report.json').read_text()) == report


@pytest.mark.parametrize('exit_code,counts,status', [
    (0, (4, 0, 0, 0), 'passed'),
    (1, (4, 1, 0, 0), 'tests_failed'),
    (2, (1, 0, 1, 0), 'collection_failed'),
    (2, None, 'collection_or_interruption_failed'),
    (3, None, 'test_infrastructure_failed'),
    (5, (0, 0, 0, 0), 'no_tests_collected'),
    (0, None, 'test_result_invalid'),
    (0, (4, 0, 0, 4), 'no_tests_executed'),
])
def test_runner_preserves_test_execution_state(tmp_path, monkeypatch, exit_code, counts, status):
    module = load_runner()
    out = tmp_path / 'evidence'
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == [sys.executable, '-c']:
            return subprocess.CompletedProcess(argv, 0, json.dumps({'version': 'test-version'}), '')
        if argv[:3] == [sys.executable, '-m', 'pytest']:
            if counts is not None:
                names = ('tests', 'failures', 'errors', 'skipped')
                attrs = ' '.join(f'{name}="{value}"' for name, value in zip(names, counts))
                (out / 'junit.xml').write_text(f'<testsuites><testsuite {attrs}/></testsuites>')
            return subprocess.CompletedProcess(argv, exit_code, 'test stdout\n', 'test stderr\n')
        if argv == ['git', 'diff', '--check']:
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv == ['git', 'rev-parse', 'HEAD']:
            return subprocess.CompletedProcess(argv, 0, 'a' * 40 + '\n', '')
        raise AssertionError(argv)
    monkeypatch.setattr(module.subprocess, 'run', run)
    rc = module.main(['--output-dir', str(out)])
    report = json.loads((out / 'candidate-test-report.json').read_text())
    assert report['preflight']['status'] == 'passed'
    assert report['pytest_version'] == 'test-version'
    assert report['python_executable'] == sys.executable
    assert report['test_exit_code'] == exit_code
    assert report['status'] == status
    assert report['release_qualified'] is False
    assert rc == (0 if status == 'passed' else 1)
    assert len([cmd for cmd in calls if '-m' in cmd]) == 1
