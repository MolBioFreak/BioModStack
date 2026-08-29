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

test('UI bundle native plugin registers through the Cordova Android config target', async () => {
  const pluginXml = await readFile(pluginXmlPath, 'utf8');
  assert.match(
    pluginXml,
    /<config-file target="app\/src\/main\/res\/xml\/config\.xml" parent="\/\*">[\s\S]*?<feature name="BmsUiBundle">/,
  );
  assert.doesNotMatch(pluginXml, /target="res\/xml\/config\.xml"/);
});
