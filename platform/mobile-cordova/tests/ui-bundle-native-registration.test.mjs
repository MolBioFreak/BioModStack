import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pluginXmlPath = path.join(
  projectRoot,
  'local-plugins',
  'cordova-plugin-bms-ui-bundle',
  'plugin.xml',
);
const preflightPath = path.join(projectRoot, 'scripts', 'prepare-bms-assets.mjs');

test('UI bundle native plugin registers through the Cordova Android config target', async () => {
  const pluginXml = await readFile(pluginXmlPath, 'utf8');
  assert.match(
    pluginXml,
    /<config-file target="res\/xml\/config\.xml" parent="\/\*">[\s\S]*?<feature name="BmsUiBundle">/,
  );
  assert.doesNotMatch(pluginXml, /target="app\/src\/main\/res\/xml\/config\.xml"/);
});

test('UI bundle install and rollback remount the shell root from nested routes', async () => {
  const source = await readFile(preflightPath, 'utf8');
  assert.match(source, /function remountShellRoot\(\)[\s\S]*window\.location\.replace\('\/'\)/u);
  assert.equal((source.match(/remountShellRoot\(\);/gu) || []).length, 2);
  assert.equal((source.match(/window\.location\.reload\(\)/gu) || []).length, 1);
});
