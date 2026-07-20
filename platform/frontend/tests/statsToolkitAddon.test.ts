import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const launcherSource = readFileSync(resolve('src/components/StatsToolkitLauncher.tsx'), 'utf8');

test('core embeds the isolated Stats Toolkit inside the BioModStack tab', () => {
  assert.match(appSource, /path="\/stats" element=\{<StatsToolkitLauncher \/>\}/);
  assert.match(layoutSource, /to="\/stats"/);
  assert.match(layoutSource, />\s*Stats Toolkit\s*<\/Link>/);
  assert.match(launcherSource, /fetch\('\/api\/system\/stats-toolkit'/);
  assert.match(launcherSource, /<iframe/);
  assert.match(launcherSource, /src=\{status\.entry_url\}/);
  assert.match(launcherSource, /BioModStack Stats Toolkit workspace/);
  assert.doesNotMatch(launcherSource, /Open Stats Toolkit/);
  assert.doesNotMatch(launcherSource, /href=\{status\?\.entry_url/);
});
