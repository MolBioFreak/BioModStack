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




for (const interleave of [false, true]) {
  test(`desktop verifies immutable release generation (interleave=${interleave})`, () => {
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'bms-desktop-release-'));
    try {
      const home = path.join(fixture, 'home');
      fs.mkdirSync(home);
      const env = { HOME: home, XDG_CONFIG_HOME: path.join(fixture, 'config'),
        XDG_STATE_HOME: path.join(fixture, 'state'), PATH: process.env.PATH };
      const project = path.resolve(process.cwd(), '../..');
      const document = path.join(fixture, 'document.json');
      fs.writeFileSync(document, JSON.stringify({schema_version: 'bms.install.v1', profile: {}, ingress: {mode: 'local-only'}}));
      execFileSync('bash', [path.join(project, 'start_ui.sh'), 'configure', '--document', document, '--json'], {env});
      const commit = () => execFileSync('python3', ['-B', '-c', `
import sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(project)})
from scripts.biomodstack_release import ProductionReleaseBackend, BuildIdentity, BUILD_SERVICES
b=ProductionReleaseBackend(repo_root=Path(${JSON.stringify(project)}), allow_first_install=True)
b._candidate_running_image_ids=lambda: {k:'sha256:'+'b'*64 for k in BUILD_SERVICES}
b.commit_known_good({}, BuildIdentity('a'*40,'desktop-release','2026-09-07T00:00:00Z'))
`], {env});
      let switched = false;
      const options = {homeDir: home, env, projectRoot: project};
      if (interleave) {
        assert.throws(() => resolveShellPaths({...options, readText: target => {
          const text = fs.readFileSync(target, 'utf8');
          if (target.endsWith('/install_profile.json') && !switched) { switched = true; commit(); }
          return text;
        }}), /Configuration changed/);
      } else commit();
      assert.equal(resolveShellPaths(options).dataRoot, path.join(fixture, 'state', 'biomodstack'));
      const root = path.join(env.XDG_CONFIG_HOME, 'biomodstack', 'configuration-v1');
      const generation = fs.readlinkSync(path.join(root, 'active'));
      const good = JSON.parse(fs.readFileSync(path.join(env.XDG_STATE_HOME, 'biomodstack', 'releases', 'known-good.json'), 'utf8'));
      assert.equal(good.configuration_generation_id, generation);
      const manifest = path.join(root, generation, 'manifest.json');
      const parsed = JSON.parse(fs.readFileSync(manifest, 'utf8'));
      parsed.receipt.BMS_DATA = '/foreign';
      fs.writeFileSync(manifest, JSON.stringify(parsed));
      assert.throws(() => resolveShellPaths(options), /Invalid release manifest/);
    } finally { fs.rmSync(fixture, {recursive: true, force: true}); }
  });
}

for (const alias of ['config', 'home', 'state', 'default-config', 'config-relative-link']) {
  test(`canonical Python/desktop identity through ${alias} alias`, () => {
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'bms-alias-'));
    try {
      const home = path.join(fixture, 'home');
      fs.mkdirSync(home);
      const env = { HOME: home, XDG_CONFIG_HOME: path.join(fixture, 'config'),
        XDG_STATE_HOME: path.join(fixture, 'state'), PATH: process.env.PATH };
      if (alias === 'default-config') env.XDG_CONFIG_HOME = '';
      const target = path.join(fixture, 'real-' + alias, 'nested');
      fs.mkdirSync(target, { recursive: true });
      const link = alias === 'default-config' ? path.join(home, '.config') : path.join(fixture, alias);
      if (alias === 'home') fs.rmdirSync(home);
      let linkTarget = target;
      if (alias === 'config-relative-link') {
        fs.mkdirSync(path.join(path.dirname(target), 'config'));
        linkTarget = `${path.relative(fixture, target)}/../config`;
        env.XDG_CONFIG_HOME = link;
      }
      fs.symlinkSync(linkTarget, link);
      const project = path.resolve(process.cwd(), '../..');
      const document = path.join(fixture, 'document.json');
      fs.writeFileSync(document, JSON.stringify({ schema_version: 'bms.install.v1', profile: {}, ingress: { mode: 'local-only' } }));
      const receipt = JSON.parse(execFileSync('bash', [path.join(project, 'start_ui.sh'),
        'configure', '--document', document, '--json'], { env, encoding: 'utf8' }));
      assert.equal(receipt.status, 'configured');
      const paths = resolveShellPaths({ env, projectRoot: project });
      const profile = JSON.parse(fs.readFileSync(receipt.destinations.profile, 'utf8'));
      assert.equal(paths.dataRoot, profile.data_root);
      assert.equal(path.join(paths.configDir, 'install_profile.json'), receipt.destinations.profile);
      assert.equal(paths.stateDir, profile.data_root);
    } finally { fs.rmSync(fixture, { recursive: true, force: true }); }
  });
}

for (const result of ['missing', 'null', '[]', 'broken', '{}', 'heuristic', 'committed']) {
  test(`interleaved real writer rejects ${result} profile read`, () => {
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'bms-reader-race-'));
    try {
      const home = path.join(fixture, 'home');
      fs.mkdirSync(home);
      const env = { HOME: home, XDG_CONFIG_HOME: path.join(fixture, 'config'),
        XDG_STATE_HOME: path.join(fixture, 'state'), PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: '1' };
      const project = path.resolve(process.cwd(), '../..');
      const document = path.join(fixture, 'doc.json');
      fs.writeFileSync(document, JSON.stringify({ schema_version: 'bms.install.v1', profile: {}, ingress: { mode: 'local-only' } }));
      let injected = false;
      const pathExists = (target: string): boolean => {
        const trigger = result === 'heuristic' ? target === '/mnt/BioModStack/biomodstack.db' : target.endsWith('/install_profile.json');
        if (trigger && !injected) {
          injected = true;
          const code = `import sys; from pathlib import Path; sys.path.insert(0,${JSON.stringify(project)}); import biomodstack_configuration as tx
def fail(name):
 if name=='journal' and ${JSON.stringify(result)}!='committed': raise OSError('interrupted writer')
tx._checkpoint=fail
import json; print(json.dumps(tx.configuration_report('configure',project_root=Path(${JSON.stringify(project)}),document=Path(${JSON.stringify(document)}))))`;
          const receipt = JSON.parse(execFileSync('python3', ['-B', '-c', code], { env, encoding: 'utf8' }));
          assert.equal(receipt.configured, result === 'committed');
          if (result !== 'committed') assert.equal(receipt.recovery_available, true);
          return result !== 'missing';
        }
        return fs.existsSync(target) || target === '/mnt/BioModStack/biomodstack.db';
      };
      assert.throws(() => resolveShellPaths({ env, homeDir: home, projectRoot: project,
        pathExists, readText: (target) => result === 'committed' ? fs.readFileSync(target, 'utf8') : result }), /Configuration changed/);
      assert.equal(injected, true);
    } finally { fs.rmSync(fixture, { recursive: true, force: true }); }
  });
}
