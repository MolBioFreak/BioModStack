import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const readSource = (...parts: string[]) => fs.readFileSync(path.join(process.cwd(), ...parts), 'utf8');

test('Stats Toolkit nav replaces Assay Analytics user-facing copy', () => {
  const layoutSource = readSource('src', 'components', 'Layout.tsx');
  const assaySource = readSource('src', 'components', 'AssayAnalytics.tsx');

  assert.match(layoutSource, /title="Stats Toolkit"/);
  assert.match(layoutSource, />\s*Stats Toolkit\s*<\/Link>/);
  assert.doesNotMatch(layoutSource, /title="Assay Analytics"/);
  assert.doesNotMatch(layoutSource, />\s*Assay Analytics\s*<\/Link>/);
  assert.match(assaySource, /eyebrow="Stats Toolkit"/);
  assert.doesNotMatch(assaySource, /eyebrow="Assay Analytics"/);
});

test('stats-tools lifecycle controls live in shared control panel and Stats Toolkit debug panel', () => {
  const layoutSource = readSource('src', 'components', 'Layout.tsx');
  const assaySource = readSource('src', 'components', 'AssayAnalytics.tsx');
  const controlSource = readSource('src', 'components', 'StatsToolsControlPanel.tsx');

  assert.match(layoutSource, /import \{ StatsToolsMenu \} from '\.\/StatsToolsControlPanel';/);
  assert.match(layoutSource, /<StatsToolsMenu \/>/);
  assert.doesNotMatch(layoutSource, /function StatsToolsMenu\(/);

  assert.match(assaySource, /label: 'Debug'/);
  assert.match(assaySource, /<StatsToolsControlPanel\s+embeddedContext="stats-toolkit-debug"/);
  assert.match(assaySource, /Stats Toolkit debug/);

  assert.match(controlSource, /data-bms-stats-tools-control-panel=\{embeddedContext\}/);
  assert.match(controlSource, /\/api\/system\/stats-tools/);
  assert.match(controlSource, /\/api\/system\/stats-tools\/\$\{action\}/);
  assert.match(controlSource, /Start stats-tools/);
  assert.match(controlSource, /Stop stats-tools/);
  assert.match(controlSource, /Restart stats-tools/);
  assert.match(controlSource, /Health/);
  assert.match(controlSource, /Logs/);
  assert.match(controlSource, /bms stats-tools status/);
  assert.match(controlSource, /bms stats-tools start/);
  assert.match(controlSource, /bms stats-tools stop/);
  assert.match(controlSource, /bms stats-tools restart/);
  assert.match(controlSource, /bms stats-tools logs --tail 120/);
  assert.match(controlSource, /stats_tools_offline — use Stats Toolkit → Debug → Start stats-tools/);
  assert.match(controlSource, /data-bms-stats-tools-menu="true"/);
});
