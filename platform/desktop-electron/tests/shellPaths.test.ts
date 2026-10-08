import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveShellPaths, SHELL_STORAGE_PARTITION } from '../src/shellPaths.js';

test('shell paths respect explicit environment overrides for project, data, and state roots', () => {
  const paths = resolveShellPaths({
    env: {
      BMS_HOME: '/work/biomodstack',
      BMS_DATA: '/srv/biomodstack-data',
      XDG_STATE_HOME: '/state-root',
    },
    homeDir: '/home/christian',
  });

  assert.equal(paths.projectRoot, '/work/biomodstack');
  assert.equal(paths.dataRoot, '/srv/biomodstack-data');
  assert.equal(paths.resultsDir, '/srv/biomodstack-data/bms_results');
  assert.equal(paths.logsDir, '/state-root/biomodstack/logs');
  assert.equal(paths.apiLog, '/state-root/biomodstack/logs/api.log');
  assert.equal(paths.frontendLog, '/state-root/biomodstack/logs/frontend.log');
  assert.equal(paths.coreRuntimeLog, '/state-root/biomodstack/logs/core-runtime.log');
});

test('shell paths auto-detect a durable data root before falling back to the repo', () => {
  const paths = resolveShellPaths({
    env: {
      BMS_HOME: '/work/biomodstack',
    },
    homeDir: '/home/christian',
    pathExists: (target: string) => target === '/mnt/BioModStack/biomodstack.db',
  });

  assert.equal(paths.dataRoot, '/mnt/BioModStack');
  assert.equal(paths.resultsDir, '/mnt/BioModStack/bms_results');
});

test('shell storage uses an explicit persistent partition so Electron keeps its own site data', () => {
  assert.equal(SHELL_STORAGE_PARTITION, 'persist:biomodstack-shell');
});
