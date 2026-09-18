#!/usr/bin/env python3
"""Run portable candidate checks; never claim native or biological qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPORT_FILES = (
    'candidate-test-report.json', 'junit.xml', 'pytest.stdout.txt',
    'pytest.stderr.txt', 'diff-check.txt',
)


def _write_report(out: Path, report: dict) -> None:
    (out / 'candidate-test-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n',
        encoding='utf-8',
    )


def _test_status(exit_code: int, totals: dict | None) -> str:
    if exit_code == 2:
        return 'collection_failed' if totals and totals['errors'] else 'collection_or_interruption_failed'
    if exit_code == 5:
        return 'no_tests_collected'
    if exit_code not in (0, 1):
        return 'test_infrastructure_failed'
    if totals is None or totals['tests'] == 0:
        return 'test_result_invalid'
    if exit_code or totals['failures'] or totals['errors']:
        return 'tests_failed'
    if totals['tests'] == totals['skipped']:
        return 'no_tests_executed'
    return 'passed'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True,
        help='external evidence directory, outside the source checkout')
    parser.add_argument('--require-release-qualification', action='store_true',
        help='fail because this runner does not perform release qualification')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    out = args.output_dir.resolve()
    if out.is_relative_to(root):
        parser.error('generated evidence must be outside the source checkout')
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in REPORT_FILES):
        parser.error('output already contains evidence; use a new evidence directory')
    xml = out / 'junit.xml'
    command = [sys.executable, '-m', 'pytest', '-q', '--confcutdir=tests/plasmid',
               'tests/plasmid', f'--junitxml={xml}']
    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0],
        'python_executable': sys.executable,
        'pytest_version': None,
        'status': 'preflight_failed',
        'preflight': {'status': 'failed', 'reason': None},
        'test_command': command,
        'test_exit_code': None,
        'test_totals': None,
        'diff_check_exit_code': None,
        'local_commit': None,
        'source_sha256': {},
        'scope': 'portable_python_regressions_and_source_wiring_checks',
        'native_tools_available': {
            name: bool(shutil.which(name)) for name in ('samtools', 'nextflow', 'apptainer')
        },
        'not_run': ['full_repository_tests', 'locked_runtime_execution', 'nextflow_execution',
                    'frontend_build_and_browser_acceptance', 'empirical_method_calibration',
                    'source_bound_runtime_record_refresh', 'production_deployment'],
        'release_qualified': False,
    }
    # Probe the very interpreter that will execute pytest, including broken
    # installations/import failures. Never install or switch interpreters here.
    probe_command = [sys.executable, '-c',
                     'import json, pytest; print(json.dumps({"version": pytest.__version__}))']
    try:
        probe = subprocess.run(probe_command, cwd=root, capture_output=True, text=True)
        if probe.returncode:
            raise RuntimeError((probe.stderr or probe.stdout).strip() or 'pytest import failed')
        version = json.loads(probe.stdout)['version']
        if not isinstance(version, str) or not version:
            raise ValueError('pytest did not report a version')
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        reason = f'pytest is unavailable under {sys.executable}: {exc}'
        result['preflight']['reason'] = reason
        (out / 'pytest.stderr.txt').write_text(reason + '\n', encoding='utf-8')
        _write_report(out, result)
        print('PRECHECK FAILED; TESTS NOT RUN. ' + reason, file=sys.stderr)
        return 2
    result['pytest_version'] = version
    result['preflight'] = {'status': 'passed', 'reason': None}
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True)
        (out / 'pytest.stdout.txt').write_text(completed.stdout, encoding='utf-8')
        (out / 'pytest.stderr.txt').write_text(completed.stderr, encoding='utf-8')
        result['test_exit_code'] = completed.returncode
        if xml.is_file():
            totals = {key: 0 for key in ('tests', 'failures', 'errors', 'skipped')}
            for suite in ET.parse(xml).iter('testsuite'):
                for key in totals:
                    count = int(suite.attrib.get(key, 0))
                    if count < 0:
                        raise ValueError('negative JUnit test count')
                    totals[key] += count
            result['test_totals'] = totals
        result['status'] = _test_status(completed.returncode, result['test_totals'])
        print(completed.stdout, end='')
        if completed.stderr:
            print(completed.stderr, end='', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError) as exc:
        result['status'] = 'test_infrastructure_failed'
        result['test_error'] = str(exc)
    try:
        diff = subprocess.run(['git', 'diff', '--check'], cwd=root, capture_output=True, text=True)
        (out / 'diff-check.txt').write_text(diff.stdout + diff.stderr, encoding='utf-8')
        result['diff_check_exit_code'] = diff.returncode
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True)
        if head.returncode:
            raise RuntimeError('cannot identify source commit: ' + head.stderr.strip())
        result['local_commit'] = head.stdout.strip()
        source_paths = [*sorted((root / 'tests/plasmid').glob('*.py')),
            *[root / 'scripts' / name for name in (
                'verify_construct.py', 'build_construct_topology_evidence.py',
                'build_fastq_support_tables.py', 'plasmid_evidence.py', 'plasmid_circular.py',
                'validate_clone_input_model.py', 'compare_plasmid_consensus.py',
                'run_plasmid_candidate_tests.py')]]
        for path in source_paths:
            raw = path.read_bytes()
            compile(raw, str(path), 'exec')
            result['source_sha256'][str(path.relative_to(root))] = hashlib.sha256(raw).hexdigest()
        if diff.returncode and result['status'] == 'passed':
            result['status'] = 'source_checks_failed'
    except (OSError, subprocess.SubprocessError, RuntimeError, SyntaxError) as exc:
        result['source_check_error'] = str(exc)
        if result['status'] == 'passed':
            result['status'] = 'source_checks_failed'
    _write_report(out, result)
    print('Candidate checks only. Native execution and scientific release qualification NOT RUN.')
    if result['status'] != 'passed':
        return 1
    return 2 if args.require_release_qualification else 0


if __name__ == '__main__':
    raise SystemExit(main())
