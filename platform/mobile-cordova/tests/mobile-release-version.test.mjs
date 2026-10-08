import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const mobileRoot = new URL('../', import.meta.url);

test('native shell release version advances to the themed nick-label repair', async () => {
  const [configXml, packageJsonText, packageLockText] = await Promise.all([
    readFile(new URL('config.xml', mobileRoot), 'utf8'),
    readFile(new URL('package.json', mobileRoot), 'utf8'),
    readFile(new URL('package-lock.json', mobileRoot), 'utf8'),
  ]);
  const packageJson = JSON.parse(packageJsonText);
  const packageLock = JSON.parse(packageLockText);

  assert.match(configXml, /version="0\.4\.13"/);
  assert.match(configXml, /android-versionCode="413"/);
  assert.equal(packageJson.version, '0.4.13');
  assert.equal(packageLock.version, '0.4.13');
  assert.equal(packageLock.packages[''].version, '0.4.13');
});
