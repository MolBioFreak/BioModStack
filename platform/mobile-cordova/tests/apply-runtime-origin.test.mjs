import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { assertOperatorOrigin, runtimeOrigin, stampConfigXml } from '../scripts/apply-runtime-origin.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OPERATOR = 'https://lab.tail1234.ts.net';

test('only an exact HTTPS apiBaseUrl becomes the native update origin', () => {
  assert.equal(runtimeOrigin(OPERATOR), OPERATOR);
  assert.equal(runtimeOrigin(`${OPERATOR}/`), OPERATOR);
  assert.equal(runtimeOrigin('http://10.0.2.2:8000'), null);
  assert.throws(() => runtimeOrigin(`${OPERATOR}/api`), /exact HTTPS origin/);
  assert.throws(() => runtimeOrigin('https://user@lab.tail1234.ts.net'), /exact HTTPS origin/);
});

test('the tracked placeholder origin is refused for a build', () => {
  assert.throws(() => assertOperatorOrigin('https://node.example.ts.net', 'cordova.runtime.json'), /placeholder origin/);
  assert.doesNotThrow(() => assertOperatorOrigin(OPERATOR, 'cordova.runtime.phone.local.json'));
});

test('stamping changes the operator access origin and BMS_API_ORIGIN and nothing else', async () => {
  const tracked = await readFile(path.join(root, 'config.xml'), 'utf8');
  const stamped = stampConfigXml(tracked, OPERATOR);
  const changed = stamped.split('\n').filter((line, index) => line !== tracked.split('\n')[index]);
  assert.equal(changed.length, 2);
  assert.ok(changed.some((line) => line.includes(`<access origin="${OPERATOR}"`)));
  assert.ok(changed.some((line) => line.includes(`name="BMS_API_ORIGIN" value="${OPERATOR}"`)));
  assert.ok(stamped.includes('<access origin="https://localhost"'));
  assert.ok(stamped.includes('<access origin="http://10.0.2.2:8000"'));
  assert.equal(stampConfigXml(stamped, OPERATOR), stamped);
  assert.throws(() => stampConfigXml('<widget/>', OPERATOR), /exactly one/);
});

test('tracked templates carry no operator host', async () => {
  for (const file of ['config.xml', 'cordova.runtime.json', 'cordova.runtime.phone.json']) {
    const text = await readFile(path.join(root, file), 'utf8');
    const hosts = [...text.matchAll(/https:\/\/([a-z0-9.-]+)/g)].map((match) => match[1]).filter((host) => host !== 'localhost');
    assert.ok(hosts.every((host) => host.endsWith('.example.ts.net') || host.endsWith('w3.org') || host.endsWith('apache.org') || host.endsWith('biomodstack.invalid') || host.endsWith('android.com')), `${file}: ${hosts}`);
  }
});

test('the command line stamps a copy for an HTTPS config and leaves it alone for the emulator', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'bms-origin-'));
  const xml = path.join(dir, 'config.xml');
  const phone = path.join(dir, 'phone.json');
  const emulator = path.join(dir, 'emulator.json');
  const placeholder = path.join(dir, 'placeholder.json');
  const base = { frontendCheckout: '../..', routerBasename: '/' };
  await writeFile(phone, JSON.stringify({ ...base, apiBaseUrl: OPERATOR, remoteUiUrl: `${OPERATOR}/` }));
  await writeFile(emulator, JSON.stringify({ ...base, apiBaseUrl: 'http://10.0.2.2:8000' }));
  await writeFile(placeholder, JSON.stringify({ ...base, apiBaseUrl: 'https://node.example.ts.net' }));
  const tracked = await readFile(path.join(root, 'config.xml'), 'utf8');
  const run = (config) => execFileSync(process.execPath, [path.join(root, 'scripts/apply-runtime-origin.mjs'), '--config', config, '--config-xml', xml], { encoding: 'utf8' });
  await writeFile(xml, tracked);
  run(emulator);
  assert.equal(await readFile(xml, 'utf8'), tracked);
  run(phone);
  const stamped = await readFile(xml, 'utf8');
  assert.equal(stamped, stampConfigXml(tracked, OPERATOR));
  await writeFile(xml, tracked);
  assert.throws(() => run(placeholder), /placeholder origin/);
  assert.equal(await readFile(xml, 'utf8'), tracked);
});
