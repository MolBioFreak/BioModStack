import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const launcherSource = readFileSync(resolve('src/components/StatsToolkitLauncher.tsx'), 'utf8');

test('core keeps Stats Toolkit inside the canonical BioModStack navigation plane', () => {
  assert.match(appSource, /path="\/stats" element=\{<StatsToolkitLauncher \/>\}/);
  assert.match(layoutSource, /to="\/stats"/);
  assert.match(layoutSource, />\s*Stats Toolkit\s*<\/Link>/);
  assert.match(launcherSource, /fetch\('\/api\/system\/stats-toolkit'/);
  assert.match(launcherSource, /<iframe/);
  assert.match(launcherSource, /src=\{resolveStatsToolkitEntryUrl\(status\.entry_url, window\.location\.hostname\)\}/);
  assert.match(readFileSync(resolve('src/runtime/tailnetEnvironment.ts'), 'utf8'), /\/stats\/embed\/\?ui=5eff945/);
  assert.match(launcherSource, /BioModStack Stats Toolkit workspace/);
  assert.doesNotMatch(launcherSource, /window\.location\.replace/);
  assert.doesNotMatch(launcherSource, /Back to BioModStack/);
});
