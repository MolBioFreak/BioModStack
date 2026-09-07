#!/usr/bin/env python3
"""Bounded synthetic BMS-CP-SCI-01 acceptance, using installed dependencies only.

No model performance, live database, deployment, or activation claim is made.
The mapping is a source-owned evidence index, not 48 asserted PASS results.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).absolute().parent
GATES = json.loads((HERE / 'scientific_acceptance_gates.json').read_text())
RECORDED_ENV = frozenset(GATES['wire_files']) | {
    'BMS_TEST_NEXTFLOW_JAR', 'BMS_G09_FAMPNN_SOURCE', 'BMS_G09_PPIFLOW_SOURCE',
    'BMS_BOLTZGEN_ANALYTICS_WIRES', 'BMS_SCIENTIFIC_VITE_CACHE', 'BMS_WP06_OPENMM_RECEIPT',
    'BMS_SCIENTIFIC_INVENTORY', 'NXF_OFFLINE', 'NXF_DISABLE_CHECK_LATEST',
    'NXF_PLUGINS_DEFAULT', 'PYTHONPATH', 'PATH', 'TMPDIR',
    'PYTHONDONTWRITEBYTECODE', 'UV_NO_SYNC', 'PYTEST_ADDOPTS',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


def source_snapshot(root):
    # Track additions/deletions too. Include untracked harness files explicitly,
    # without following linked dependency directories or hashing model trees.
    paths = set(git(root, 'ls-files', '-z').split('\0')) - {''}
    paths.update(str(p.relative_to(root)) for p in HERE.glob('*scientific_acceptance*') if p.is_file())
    paths.update({'platform/frontend/tests/vitest.scientific.config.ts',
                  'tests/scientific_acceptance_cases.json'})
    return {p: (hashlib.sha256(os.readlink(root / p).encode()).hexdigest()
                if (root / p).is_symlink() else digest(root / p)
                if (root / p).is_file() else None) for p in sorted(paths)}


def external_output(root, value):
    path = Path(value).expanduser().resolve()
    if path == root or path.is_relative_to(root):
        raise ValueError('--output-dir must be outside the repository')
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('--output-dir must be new or empty; stale evidence cannot be reused')
    return path


def require_path(value, name, directory=False):
    if not value:
        raise ValueError(f'{name} is required via CLI or environment')
    path = Path(value).expanduser().absolute()
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f'{name} does not name an existing {"directory" if directory else "file"}: {path}')
    return path


def native_sources(root, sources):
    # Read sealed literal data, never import native training/model modules.
    tree = ast.parse((root / 'scripts/maturation_native_adapter.py').read_text())
    seal = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SOURCE_SHA256' for t in node.targets))
    result = {}
    for producer, source in sources.items():
        for relative, expected in seal[producer].items():
            path = require_path(source / relative, relative)
            actual = digest(path)
            if actual != expected:
                raise ValueError(f'sealed native source mismatch: {producer}/{relative}')
            result[f'{producer}/{relative}'] = actual
    constants = sources['fampnn'] / 'fampnn/data/residue_constants.py'
    result['fampnn/fampnn/data/residue_constants.py'] = digest(require_path(constants, 'native constants'))
    return result


def wire_hashes(output, require=False):
    paths = list(GATES['wire_files'].values()) + [f'boltzgen/{n}.json' for n in GATES['boltzgen_cases']]
    result = {}
    for name in paths:
        path = output / 'wires' / name
        if not path.is_file():
            if require:
                raise ValueError(f'missing fresh synthetic API wire: {name}')
            continue
        if require:
            json.loads(path.read_text())
        result[name] = {'sha256': digest(path), 'synthetic': True}
    return result


def junit_inventory(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if not cases:
        raise ValueError(f'empty actual test inventory: {path}')
    return [{'class': t.get('classname'), 'name': t.get('name'),
             'outcome': 'failed' if t.find('failure') is not None or t.find('error') is not None
             else 'skipped' if t.find('skipped') is not None else 'passed'} for t in cases]


def pytest_command(python, files, directory):
    # Never collect top-level Nextflow tests under the API's subprocess policy.
    cut = ['--confcutdir=tests'] if directory == 'root' else []
    return [str(python), '-m', 'pytest', *cut, '-p', 'no:cacheprovider',
            '-p', 'scientific_acceptance_inventory', *files]


def run_gate(name, command, cwd, env, output, expected=(), pytest_gate=False):
    gate = output / name
    gate.mkdir()
    inventory_path = gate / 'selected.json'
    effective = dict(env, BMS_SCIENTIFIC_INVENTORY=str(inventory_path))
    if pytest_gate:
        command = [*command, f'--basetemp={gate / "tmp"}', f'--junitxml={gate / "results.xml"}']
    elif name == 'mounted':
        command = [*command, '--reporter=junit', f'--outputFile={gate / "results.xml"}']
    record = {'command': command, 'cwd': str(cwd), 'synthetic': True,
              'environment': {k: v for k, v in effective.items() if k in RECORDED_ENV},
              'fixtures_before': wire_hashes(output)}
    save(gate / 'receipt.json', record)
    with (gate / 'output.log').open('w') as stream:
        result = subprocess.run(command, cwd=cwd, env=effective, stdout=stream, stderr=subprocess.STDOUT)
    record['exit_code'] = result.returncode
    try:
        if pytest_gate or name == 'mounted':
            record['testcases'] = junit_inventory(gate / 'results.xml')
            if pytest_gate:
                selected = json.loads(inventory_path.read_text())
                record['selected'] = selected
                if not selected or any(not any(node.split('::')[0].endswith(file) for node in selected) for file in expected):
                    raise ValueError('requested pytest suite missing from actual collection')
            else:
                selected = sorted({t['class'] for t in record['testcases']})
                save(inventory_path, selected)
                if any(not any((entry or '').endswith(file) for entry in selected) for file in expected):
                    raise ValueError('requested mounted suite missing from actual JUnit inventory')
            if any(t['outcome'] != 'passed' for t in record['testcases']):
                raise ValueError('failed or skipped acceptance test')
        elif name == 'pure':
            # Node's TAP output contains the actual names and subtest inventory.
            log = (gate / 'output.log').read_text()
            selected = [line.strip()[len('# Subtest: '):] for line in log.splitlines() if line.strip().startswith('# Subtest: ')]
            if not selected or '# tests 0' in log or any('# skip ' in line and not line.endswith(' 0') for line in log.splitlines()):
                raise ValueError('empty or skipped pure test inventory')
            save(inventory_path, selected)
        if result.returncode:
            raise ValueError(f'command exited {result.returncode}')
    except (ValueError, OSError, ET.ParseError) as exc:
        record['validation_error'] = str(exc)
        raise RuntimeError(f'{name}: {exc}; see {gate}') from exc
    finally:
        record['fixtures_after'] = wire_hashes(output)
        record['output_hashes'] = {p.name: digest(p) for p in gate.iterdir()
                                   if p.is_file() and p.name != 'receipt.json'}
        save(gate / 'receipt.json', record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--python', default=os.environ.get('BMS_ACCEPTANCE_PYTHON'))
    parser.add_argument('--nextflow-jar', default=os.environ.get('BMS_TEST_NEXTFLOW_JAR'))
    parser.add_argument('--fampnn-source', default=os.environ.get('BMS_G09_FAMPNN_SOURCE'))
    parser.add_argument('--ppiflow-source', default=os.environ.get('BMS_G09_PPIFLOW_SOURCE'))
    args = parser.parse_args(argv)
    root = HERE.parent.resolve()
    output = external_output(root, args.output_dir)
    python = require_path(args.python or root / 'platform/api/.venv/bin/python', 'installed API Python')
    jar = require_path(args.nextflow_jar, '--nextflow-jar')
    sources = {p: require_path(getattr(args, p + '_source'), '--' + p + '-source', True) for p in ('fampnn', 'ppiflow')}
    sealed = native_sources(root, sources)
    for command in ('pnpm', 'java'):
        if not shutil.which(command):
            raise ValueError(f'installed command required: {command}')
    for group in ('root', 'api', 'frontend', 'pure', 'wires'):
        base = root / 'platform/api' if group in ('api', 'wires') else root / 'platform/frontend' if group in ('frontend', 'pure') else root
        for file in GATES[group]:
            require_path(base / file, f'{group} suite')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'wires/boltzgen').mkdir(parents=True)
    (output / 'tmp').mkdir()
    # Clear inherited test inputs/opt-ins, without changing repository safety policy.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('BMS_', 'BIOXP_', '_BIOXP_', 'PYTEST_', 'NXF_')) and k not in ('DATABASE_URL', 'DATABASE_ASYNC_URL')}
    env.update(BMS_TEST_NEXTFLOW_JAR=str(jar), BMS_G09_FAMPNN_SOURCE=str(sources['fampnn']),
               BMS_G09_PPIFLOW_SOURCE=str(sources['ppiflow']), PYTHONDONTWRITEBYTECODE='1',
               PYTEST_ADDOPTS='', UV_NO_SYNC='1', TMPDIR=str(output / 'tmp'),
               PYTHONPATH=os.pathsep.join([str(HERE), str(root / 'scripts'), str(root / 'platform/api')]),
               NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_PLUGINS_DEFAULT='',
               BMS_SCIENTIFIC_VITE_CACHE=str(output / 'vite-cache'),
               BMS_WP06_OPENMM_RECEIPT=str(output / 'wires/openmm-command-receipt.json'),
               BMS_BOLTZGEN_ANALYTICS_WIRES=str(output / 'wires/boltzgen'))
    env.update({key: str(output / 'wires' / file) for key, file in GATES['wire_files'].items()})
    before = source_snapshot(root)
    save(output / 'source-before.json', before)
    report = {'head': git(root, 'rev-parse', 'HEAD'), 'tree': git(root, 'rev-parse', 'HEAD^{tree}'),
              'status_before': git(root, 'status', '--porcelain'), 'synthetic': True,
              'limitations': 'Software fixtures only; no live models, services, activation or 48-case closure assertion.',
              'native_sources': sealed, 'nextflow_jar_sha256': digest(jar), 'gates': []}
    try:
        for name, files, cwd, layer in (
            ('root', GATES['root'], root, 'root'),
            ('wires', GATES['wires'], root / 'platform/api', 'api'),
            ('api', GATES['api'], root / 'platform/api', 'api'),
        ):
            report['gates'].append(run_gate(name, pytest_command(python, files, layer), cwd, env, output, files, True))
            if name == 'root':
                report['openmm_command_receipt_sha256'] = digest(
                    require_path(env['BMS_WP06_OPENMM_RECEIPT'], 'fresh Nextflow OpenMM receipt'))
            if name == 'wires':
                save(output / 'fresh-wire-hashes.json', wire_hashes(output, require=True))
        front = root / 'platform/frontend'
        wire_hashes(output, require=True)
        report['gates'].append(run_gate('mounted', ['pnpm', 'exec', 'vitest', 'run', '--config', 'tests/vitest.scientific.config.ts'], front, env, output, GATES['frontend']))
        report['gates'].append(run_gate('typecheck', ['pnpm', 'exec', 'tsc', '--noEmit', '--incremental', 'false', '-p', 'tsconfig.app.json'], front, env, output))
        report['gates'].append(run_gate('pure', ['pnpm', 'exec', 'tsx', '--test', '--test-reporter=tap', *GATES['pure']], front, env, output))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        after = source_snapshot(root)
        save(output / 'source-after.json', after)
        report['source_drift'] = [p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
        try:
            report['native_sources_after'] = native_sources(root, sources)
        except (ValueError, OSError) as exc:
            report['native_sources_after'] = None
            report['error'] = str(exc)
        report['nextflow_jar_sha256_after'] = digest(jar) if jar.is_file() else None
        report['success'] = (not report.get('error') and not report['source_drift']
                             and len(report['gates']) == 6 and report['native_sources_after'] == sealed
                             and report['nextflow_jar_sha256_after'] == report['nextflow_jar_sha256'])
        report['fixture_hashes'] = wire_hashes(output)
        save(output / 'acceptance.json', report)
    print(output / 'acceptance.json')
    return 0 if report['success'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(f'Acceptance preflight failed: {error}', file=sys.stderr)
        sys.exit(2)
