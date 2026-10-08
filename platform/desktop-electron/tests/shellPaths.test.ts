import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

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

test('shell paths prefer a persisted install profile before heuristic data-root detection', () => {
  const installProfilePath = '/home/christian/.config/biomodstack/install_profile.json';
  const options = {
    env: {
      BMS_HOME: '/work/biomodstack',
    },
    homeDir: '/home/christian',
    pathExists: (target: string) => target === installProfilePath || target === '/mnt/BioModStack/biomodstack.db',
    readText: (target: string) => {
      assert.equal(target, installProfilePath);
      return JSON.stringify({
        data_root: '/srv/biomodstack-state',
      });
    },
  } as any;

  const paths = resolveShellPaths(options);

  assert.equal(paths.dataRoot, '/srv/biomodstack-state');
  assert.equal(paths.resultsDir, '/srv/biomodstack-state/bms_results');
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

test('desktop consumes real configured generation and fails closed on interrupted activation', () => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'bms-desktop-config-'));
  try {
    const home = path.join(fixture, 'home');
    fs.mkdirSync(home);
    const env = { HOME: home, XDG_CONFIG_HOME: path.join(fixture, 'config'),
      XDG_STATE_HOME: path.join(fixture, 'state'), PATH: process.env.PATH };
    // Owning package cwd, also works when tsc output is in a disposable directory.
    const project = path.resolve(process.cwd(), '../..');
    const document = path.join(fixture, 'document.json');
    fs.writeFileSync(document, JSON.stringify({ schema_version: 'bms.install.v1',
      profile: {}, ingress: { mode: 'local-only' } }));
    const receipt = JSON.parse(execFileSync('bash', [path.join(project, 'start_ui.sh'),
      'configure', '--document', document, '--json'], { env, encoding: 'utf8' }));
    assert.equal(receipt.status, 'configured');
    const options = { homeDir: home, env, projectRoot: project };
    assert.equal(resolveShellPaths(options).dataRoot, path.join(fixture, 'state', 'biomodstack'));
    const active = path.join(env.XDG_CONFIG_HOME, 'biomodstack', 'configuration-v1', 'active');
    fs.unlinkSync(active);
    assert.throws(() => resolveShellPaths(options), /Configuration incomplete/);
    assert.throws(() => resolveShellPaths({ ...options, env: { ...env, BMS_DATA: '/ignored' } }),
      /Configuration incomplete/);
    fs.symlinkSync('generation', active);
    fs.writeFileSync(receipt.destinations.core_runtime_env, 'CORRUPTED=1\n');
    assert.throws(() => resolveShellPaths(options), /generation mismatch/);
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
});
