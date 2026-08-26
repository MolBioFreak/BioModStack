import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const mobileRoot = new URL('../', import.meta.url);

test('native shell release version advances beyond the published 0.4.5 APK', async () => {
  const [configXml, packageJsonText] = await Promise.all([
    readFile(new URL('config.xml', mobileRoot), 'utf8'),
    readFile(new URL('package.json', mobileRoot), 'utf8'),
  ]);
  const packageJson = JSON.parse(packageJsonText);

  assert.match(configXml, /version="0\.4\.6"/);
  assert.match(configXml, /android-versionCode="406"/);
  assert.equal(packageJson.version, '0.4.6');
});
