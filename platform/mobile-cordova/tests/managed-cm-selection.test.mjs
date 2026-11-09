import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { validateTailnetSelectionPayload } from '../scripts/prepare-bms-assets.mjs';
const actual = JSON.parse(readFileSync(new URL('./fixtures/development-managed-cm-selection.json', import.meta.url)));
const origin = 'https://bigboy.taileb3a90.ts.net';
test('actual HTTPS Development selection accepts installed shared CM Python module launches', () => {
  assert.doesNotThrow(() => validateTailnetSelectionPayload(actual, 'development', origin));
});

// Mutate both public and authoritative copies so rejection is identity-policy
// evidence, not merely a duplicate-report comparison failure.
const api = (p) => p.development_api_listener.listener_reports[0];
const adapter = (p) => p.workflow_adapter_listener.listener_reports[0];
function sync(p) {
  for (const report of [api(p), adapter(p)]) report.cmdline = report.argv.join(' ');
  p.api_listeners = structuredClone(p.development_api_listener.listener_reports);
}
for (const target of [api, adapter]) {
  for (const [name, mutate] of [
    ['malformed runtime hash', (r) => { r.argv[0] = r.argv[0].replace(/runtime-[0-9a-f]{64}/, 'runtime-not-a-digest'); }],
    ['wrong runtime hash in executable', (r) => { r.executable = r.executable.replace(/runtime-[0-9a-f]{64}/, `runtime-${'a'.repeat(64)}`); }],
    ['different complete runtime release', (r) => { r.argv[0] = r.argv[0].replace(/runtime-[0-9a-f]{64}/, `runtime-${'b'.repeat(64)}`); r.executable = r.executable.replace(/runtime-[0-9a-f]{64}/, `runtime-${'b'.repeat(64)}`); }],
    ['split runtime executable', (r) => { r.executable = '/usr/bin/python3.12'; }],
    ['arbitrary Python path', (r) => { r.argv[0] = '/tmp/alien/python'; r.executable = '/tmp/alien/python3.12'; }],
    ['non-CM release layout', (r) => { r.argv[0] = r.argv[0].replace('/cm-api-python/', '/alien/'); r.executable = r.executable.replace('/cm-api-python/', '/alien/'); }],
    ['arbitrary module', (r) => { r.argv[2] = 'alien'; }],
    ['wrong application argv', (r) => { r.argv[3] = 'other:app'; }],
    ['trailing argument', (r) => { r.argv.push('--proxy-headers'); }],
    ['wrong source unit', (r) => { r.cgroup = r.cgroup.replace('.service', '-alien.service'); }],
    ['wrong cwd', (r) => { r.cwd += '/alien'; }],
    ['wrong revision', (r) => { r.build_revision = 'a'.repeat(40); }],
  ]) {
    test(`${target === api ? 'API' : 'adapter'} rejects ${name}`, () => {
      const p = structuredClone(actual);
      mutate(target(p)); sync(p);
      assert.throws(() => validateTailnetSelectionPayload(p, 'development', origin), /mismatched runtime identity/);
    });
  }
}
for (const [name, mutate] of [
  ['source root', (p) => { p.development_api_listener.source_root += '/alien'; }],
  ['service owner', (p) => { p.workflow_adapter_listener.systemd_service = 'alien.service'; }],
  ['listener closure', (p) => { p.development_api_listener.listener_inode_owners = {}; }],
  ['update auth route', (p) => { delete p.serve_handlers['/api/mobile-apk']; }],
]) {
  test(`managed receipt retains strict ${name} checks`, () => {
    const p = structuredClone(actual); mutate(p); sync(p);
    assert.throws(() => validateTailnetSelectionPayload(p, 'development', origin));
  });
}
