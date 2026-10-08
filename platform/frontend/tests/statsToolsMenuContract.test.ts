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

  assert.match(assaySource, /label: 'Runtime'/);
  assert.match(assaySource, /<StatsToolsControlPanel\s+embeddedContext="stats-toolkit-debug"/);
  assert.match(assaySource, /title="Stats Tools"/);

  assert.match(controlSource, /data-bms-stats-tools-control-panel=\{embeddedContext\}/);
  assert.match(controlSource, /\/api\/system\/stats-tools/);
  assert.match(controlSource, /\/api\/system\/stats-tools\/\$\{action\}/);
  assert.match(controlSource, /Stats Tools/);
  assert.doesNotMatch(controlSource, /Actions \+ logs\./);
  assert.match(controlSource, />Start<\/button>/);
  assert.match(controlSource, />Stop<\/button>/);
  assert.match(controlSource, />Restart<\/button>/);
  assert.match(controlSource, /Health/);
  assert.match(controlSource, /Logs/);
  assert.match(controlSource, /CLI commands/);
  assert.match(controlSource, /bms stats-tools status/);
  assert.match(controlSource, /bms stats-tools start/);
  assert.match(controlSource, /bms stats-tools stop/);
  assert.match(controlSource, /bms stats-tools restart/);
  assert.match(controlSource, /bms stats-tools logs --tail 120/);
  assert.match(controlSource, /stats_tools_offline — press Start/);
  assert.match(controlSource, /data-bms-stats-tools-menu="true"/);
  assert.doesNotMatch(controlSource, /Documentation/);
  assert.doesNotMatch(controlSource, /BMS stats plan/);
  assert.doesNotMatch(controlSource, /R Project/);
  assert.doesNotMatch(controlSource, /Plotly docs/);
  assert.doesNotMatch(controlSource, /Start stats-tools/);
  assert.doesNotMatch(controlSource, /Stop stats-tools/);
  assert.doesNotMatch(controlSource, /Restart stats-tools/);
  assert.doesNotMatch(controlSource, /stats_tools_offline — use Stats Toolkit → Debug → Start stats-tools/);
  assert.doesNotMatch(controlSource, /Optional runtime: start\/stop\/restart, health, logs\./);
});
