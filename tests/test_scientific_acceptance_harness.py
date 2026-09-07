"""Harness-only checks: never execute the scientific acceptance gate."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_scientific_acceptance as driver


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_external_output_rejects_repo_and_stale_directory(self):
        root = self.base / 'repo'
        root.mkdir()
        for path in (root, root / 'inside'):
            with self.assertRaises(ValueError):
                driver.external_output(root, path)
        outside = self.base / 'output'
        outside.mkdir()
        self.assertEqual(driver.external_output(root, outside), outside)
        (outside / 'old.json').write_text('{}')
        with self.assertRaises(ValueError):
            driver.external_output(root, outside)
        (self.base / 'alias').symlink_to(root, target_is_directory=True)
        with self.assertRaises(ValueError):
            driver.external_output(root, self.base / 'alias/inside')

    def test_missing_inputs_fail_before_any_subprocess(self):
        with patch.object(driver.subprocess, 'run') as run:
            for value in (None, str(self.base / 'missing')):
                with self.assertRaises(ValueError):
                    driver.require_path(value, 'fixture input')
            run.assert_not_called()

    def test_top_level_nextflow_collection_is_separate(self):
        root = driver.pytest_command('python', ['tests/toy.py'], 'root')
        api = driver.pytest_command('python', ['tests/toy.py'], 'api')
        self.assertIn('--confcutdir=tests', root)
        self.assertNotIn('--confcutdir=tests', api)
        self.assertIn('scientific_acceptance_inventory', api)
        self.assertFalse(any('install' in arg or 'sync' in arg for arg in root + api))

    def test_all_required_wires_including_openmm_and_g09(self):
        for name in driver.GATES['wire_files'].values():
            path = self.base / 'wires' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"synthetic":true}')
        for case in driver.GATES['boltzgen_cases']:
            path = self.base / 'wires/boltzgen' / (case + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"synthetic":true}')
        hashes = driver.wire_hashes(self.base, require=True)
        self.assertTrue(all(item['synthetic'] for item in hashes.values()))
        for key in ('BMS_WP06_OPENMM_WIRE', 'BMS_G09_FRONTEND_FIXTURE'):
            path = self.base / 'wires' / driver.GATES['wire_files'][key]
            data = path.read_bytes()
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'missing fresh'):
                driver.wire_hashes(self.base, require=True)
            path.write_bytes(data)

    def test_junit_empty_skipped_and_failure_are_distinct(self):
        path = self.base / 'results.xml'
        path.write_text('<testsuites/>')
        with self.assertRaises(ValueError):
            driver.junit_inventory(path)
        path.write_text('<testsuite><testcase name="ok"/><testcase name="bad"><failure/></testcase><testcase name="omit"><skipped/></testcase></testsuite>')
        self.assertEqual([r['outcome'] for r in driver.junit_inventory(path)], ['passed', 'failed', 'skipped'])

    def test_real_subprocess_records_only_allowlisted_environment(self):
        # This is a tiny Python subprocess, not any scientific gate.
        record = driver.run_gate('probe', [sys.executable, '-c', 'print("synthetic harness probe")'],
                                 self.base, dict(os.environ, BMS_SECRET='do-not-record', BMS_LIVE_DATABASE='do-not-record'), self.base)
        self.assertEqual(record['exit_code'], 0)
        self.assertNotIn('BMS_SECRET', record['environment'])
        self.assertNotIn('BMS_LIVE_DATABASE', record['environment'])
        self.assertEqual(record['output_hashes']['output.log'], driver.digest(self.base / 'probe/output.log'))

    def test_nonzero_subprocess_retains_failure_receipt(self):
        with self.assertRaises(RuntimeError):
            driver.run_gate('probe', [sys.executable, '-c', 'raise SystemExit(7)'], self.base, {}, self.base)
        record = json.loads((self.base / 'probe/receipt.json').read_text())
        self.assertEqual(record['exit_code'], 7)
        self.assertIn('validation_error', record)

    def test_selected_inventory_cannot_silently_omit_requested_file(self):
        def fake_run(command, **kwargs):
            folder = self.base / 'api'
            (folder / 'results.xml').write_text('<testsuite><testcase name="toy"/></testsuite>')
            (folder / 'selected.json').write_text('["tests/present.py::test_toy"]')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(driver.subprocess, 'run', side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, 'missing from actual'):
                driver.run_gate('api', ['unused'], self.base, {}, self.base,
                                ['tests/present.py', 'tests/omitted.py'], True)

    def test_source_snapshot_hashes_symlink_not_external_target(self):
        tests = self.base / 'tests'
        tests.mkdir()
        outside = self.base / 'outside'
        outside.write_text('first')
        (self.base / 'tracked-link').symlink_to(outside)
        with patch.object(driver, 'HERE', tests), patch.object(driver, 'git', return_value='tracked-link\0'):
            before = driver.source_snapshot(self.base)
            outside.write_text('changed')
            self.assertEqual(before, driver.source_snapshot(self.base))
        self.assertEqual(before['tracked-link'], hashlib.sha256(str(outside).encode()).hexdigest())

    def test_mapping_has_48_distinct_source_resolvable_cases(self):
        root = driver.HERE.parent
        mapping = json.loads((driver.HERE / 'scientific_acceptance_cases.json').read_text())
        self.assertEqual(len(mapping['cases']), 48)
        self.assertEqual(len({r['id'] for r in mapping['cases']}), 48)
        for row in mapping['cases']:
            self.assertTrue(row['requirement'] and row['limitations'] and row['tests'])
            for item in row['tests']:
                path, *names = item['selector'].split('::')
                text = (root / path).read_text()
                if path.endswith('.py'):
                    nodes = ast.parse(text).body
                    for name in names:
                        node = next(n for n in nodes if getattr(n, 'name', None) == name)
                        nodes = node.body
                else:
                    self.assertIn('::'.join(names), text)

    def test_vitest_manifest_is_unconditional_and_unique(self):
        self.assertEqual(len(driver.GATES['frontend']), len(set(driver.GATES['frontend'])))
        config = (driver.HERE.parent / 'platform/frontend/tests/vitest.scientific.config.ts').read_text()
        self.assertIn('include: gates.frontend', config)
        self.assertIn('Object.keys(gates.wire_files)', config)
        self.assertNotIn('vitest.md.config', config)


if __name__ == '__main__':
    unittest.main()
