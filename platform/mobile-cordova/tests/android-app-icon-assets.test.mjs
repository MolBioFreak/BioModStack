import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const configXmlPath = path.join(projectRoot, 'config.xml');

const expectedLegacyIconSizes = new Map([
  ['ldpi', 36],
  ['mdpi', 48],
  ['hdpi', 72],
  ['xhdpi', 96],
  ['xxhdpi', 144],
  ['xxxhdpi', 192],
]);

const expectedAdaptiveIconSizes = new Map([
  ['ldpi', 81],
  ['mdpi', 108],
  ['hdpi', 162],
  ['xhdpi', 216],
  ['xxhdpi', 324],
  ['xxxhdpi', 432],
]);

function parseAttributes(tag) {
  const attributes = {};
  for (const match of tag.matchAll(/\s([\w:-]+)="([^"]*)"/g)) {
    attributes[match[1]] = match[2];
  }
  return attributes;
}

async function readPngDimensions(relativePath) {
  const buffer = await readFile(path.join(projectRoot, relativePath));
  assert.equal(buffer.toString('ascii', 1, 4), 'PNG', `${relativePath} should be a PNG`);
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

test('Android Cordova config wires branded BMS launcher and splash icons', async () => {
  const xml = await readFile(configXmlPath, 'utf8');
  assert.match(
    xml,
    /<preference\s+name="AndroidWindowSplashScreenAnimatedIcon"\s+value="resources\/android\/icon\/bms-splash-icon\.png"\s*\/>/,
    'Android 12+ splash icon should use the BMS asset instead of the Cordova default',
  );

  const iconTags = [...xml.matchAll(/<icon\b[^>]*\/>/g)].map((match) => match[0]);
  assert.equal(iconTags.length, expectedLegacyIconSizes.size, 'all Android density launcher icons should be declared');

  const declaredDensities = new Set();
  for (const tag of iconTags) {
    const attrs = parseAttributes(tag);
    const density = attrs.density;
    assert.ok(expectedLegacyIconSizes.has(density), `unexpected icon density: ${density}`);
    declaredDensities.add(density);

    assert.equal(attrs.src, `resources/android/icon/bms-icon-${density}.png`);
    assert.equal(attrs.background, `resources/android/icon/bms-icon-background-${density}.png`);
    assert.equal(attrs.foreground, `resources/android/icon/bms-icon-foreground-${density}.png`);
    assert.equal(attrs.monochrome, `resources/android/icon/bms-icon-monochrome-${density}.png`);
  }
  assert.deepEqual([...declaredDensities].sort(), [...expectedLegacyIconSizes.keys()].sort());
});

test('branded launcher icon assets exist at Cordova/Android expected pixel sizes', async () => {
  for (const [density, size] of expectedLegacyIconSizes) {
    const dimensions = await readPngDimensions(`resources/android/icon/bms-icon-${density}.png`);
    assert.deepEqual(dimensions, { width: size, height: size }, `legacy ${density} launcher icon size`);
  }

  for (const [density, size] of expectedAdaptiveIconSizes) {
    for (const layer of ['background', 'foreground', 'monochrome']) {
      const dimensions = await readPngDimensions(`resources/android/icon/bms-icon-${layer}-${density}.png`);
      assert.deepEqual(dimensions, { width: size, height: size }, `adaptive ${density} ${layer} icon size`);
    }
  }

  assert.deepEqual(
    await readPngDimensions('resources/android/icon/bms-splash-icon.png'),
    { width: 432, height: 432 },
    'Android splash icon size',
  );
});
