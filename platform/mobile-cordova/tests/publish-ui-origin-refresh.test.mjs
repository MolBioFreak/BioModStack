import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';

test('origin refresh reuses verified bundle and preserves release identity', async () => {
  const root = await mkdtemp(path.join(process.env.TMPDIR || tmpdir(), 'bms-origin-'));
  try {
    const updates = path.join(root, 'updates');
    await mkdir(path.join(updates, 'channels/phone'), { recursive: true });
    await mkdir(path.join(updates, 'bundles/phone'), { recursive: true });
    const bundle = Buffer.from('retained bundle test bytes');
    const manifest = { channel: 'phone', version: 'retained-v1', sha256: crypto.createHash('sha256').update(bundle).digest('hex'), published_at: 'retained-time', descriptor: {version: 'retained-v1'}, download_url: 'https://old.test/bundle.zip', asset_base_url: 'https://old.test/files/', files: [{path: 'assets/a b.js', url: 'https://old.test/file'}] };
    const manifestPath = path.join(updates, 'channels/phone/manifest.json');
    await writeFile(manifestPath, JSON.stringify(manifest));
    await writeFile(path.join(updates, 'bundles/phone/retained-v1.zip'), bundle);
    const config = path.join(root, 'config.json');
    await writeFile(config, JSON.stringify({ frontendCheckout: path.resolve('../..'), apiBaseUrl: 'https://new.test' }));
    const run = () => spawnSync(process.execPath, ['scripts/publish-ui-update-bundle.mjs', '--config', config, '--updates-dir', updates, '--refresh-origin'], { encoding: 'utf8' });
    const result = run();
    assert.equal(result.status, 0, result.stderr);
    const refreshed = JSON.parse(await readFile(manifestPath));
    assert.equal(refreshed.version, manifest.version);
    assert.equal(refreshed.sha256, manifest.sha256);
    assert.equal(refreshed.published_at, manifest.published_at);
    assert.deepEqual(refreshed.descriptor, manifest.descriptor);
    assert.equal(refreshed.download_url, 'https://new.test/api/mobile-ui/bundles/phone/retained-v1.zip');
    assert.equal(refreshed.files[0].url, 'https://new.test/api/mobile-ui/files/phone/retained-v1/assets/a%20b.js');
    assert.deepEqual(await readFile(path.join(updates, 'bundles/phone/retained-v1.zip')), bundle);
    await writeFile(path.join(updates, 'bundles/phone/retained-v1.zip'), 'changed bytes');
    assert.notEqual(run().status, 0);
    assert.deepEqual(JSON.parse(await readFile(manifestPath)), refreshed);
  } finally { await rm(root, {recursive: true, force: true}); }
});
