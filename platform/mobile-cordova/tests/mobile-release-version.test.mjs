import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const mobileRoot = new URL('../', import.meta.url);

test('native shell manifests match approved artifact version metadata', async () => {
  const [configXml, packageJsonText, packageLockText, artifactReadme] = await Promise.all([
    readFile(new URL('config.xml', mobileRoot), 'utf8'),
    readFile(new URL('package.json', mobileRoot), 'utf8'),
    readFile(new URL('package-lock.json', mobileRoot), 'utf8'),
    readFile(new URL('../../artifacts/android/README.md', mobileRoot), 'utf8'),
  ]);
  const packageJson = JSON.parse(packageJsonText);
  const packageLock = JSON.parse(packageLockText);

  const widget = configXml.match(/<widget\b[^>]*>/)?.[0];
  assert.ok(widget, 'config.xml widget is required');
  const version = widget.match(/\bversion="([^"]+)"/)?.[1];
  const versionCode = widget.match(/\bandroid-versionCode="(\d+)"/)?.[1];
  const artifact = artifactReadme.match(/- Version: `([^`]+)` \(`versionCode (\d+)`\)/);
  assert.ok(artifact, 'approved artifact version metadata is required');
  assert.equal(version, artifact[1]);
  assert.equal(versionCode, artifact[2]);
  assert.equal(packageJson.version, version);
  assert.equal(packageLock.version, version);
  assert.equal(packageLock.packages[''].version, version);
});
