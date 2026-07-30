import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const launcherSource = readFileSync(resolve('src/components/StatsToolkitLauncher.tsx'), 'utf8');

test('core routes /stats directly into the isolated interactive application', () => {
  assert.match(appSource, /path="\/stats" element=\{<StatsToolkitLauncher \/>\}/);
  assert.match(layoutSource, /to="\/stats"/);
  assert.match(layoutSource, />\s*Stats Toolkit\s*<\/Link>/);
  assert.match(launcherSource, /fetch\('\/api\/system\/stats-toolkit'/);
  assert.match(launcherSource, /import \{ useEffect \} from 'react'/);
  assert.match(launcherSource, /resolveStatsToolkitEntryUrl\(status\.entry_url, window\.location\.hostname\)/);
  assert.match(readFileSync(resolve('src/runtime/tailnetEnvironment.ts'), 'utf8'), /\/stats\/embed\/\?ui=021b386/);
  assert.match(launcherSource, /window\.location\.replace\(entryUrl\)/);
  assert.match(launcherSource, /Opening BioModStack Stats Toolkit/);
  assert.doesNotMatch(launcherSource, /<iframe/);
  assert.doesNotMatch(launcherSource, /Open Stats Toolkit/);
});
