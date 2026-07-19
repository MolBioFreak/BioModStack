import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const launcherSource = readFileSync(resolve('src/components/StatsToolkitLauncher.tsx'), 'utf8');

test('core exposes one external Stats Toolkit launcher without bundling analytics', () => {
  assert.match(appSource, /path="\/stats" element=\{<StatsToolkitLauncher \/>\}/);
  assert.match(layoutSource, /to="\/stats"/);
  assert.match(layoutSource, />\s*Stats Toolkit\s*<\/Link>/);
  assert.match(launcherSource, /fetch\('\/api\/system\/stats-toolkit'/);
  assert.match(launcherSource, /Open Stats Toolkit/);
  assert.match(launcherSource, /href=\{status\?\.entry_url/);
  assert.doesNotMatch(launcherSource, /iframe/i);
});
