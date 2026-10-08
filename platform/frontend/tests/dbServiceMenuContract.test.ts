import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const readSource = (...parts: string[]) => fs.readFileSync(path.join(process.cwd(), ...parts), 'utf8');

test('BMS DB service top-bar menu and control panel expose runtime contract', () => {
  const layoutSource = readSource('src', 'components', 'Layout.tsx');
  const controlSource = readSource('src', 'components', 'DbServiceControlPanel.tsx');

  assert.match(layoutSource, /import \{ DbServiceMenu \} from '\.\/DbServiceControlPanel';/);
  assert.match(layoutSource, /<DbServiceMenu \/>/);
  assert.match(controlSource, /data-bms-db-service-menu="true"/);
  assert.match(controlSource, /data-bms-db-service-control-panel=\{embeddedContext\}/);
  assert.match(controlSource, /\/api\/system\/db-service/);
  assert.match(controlSource, /\/api\/system\/db-service\/\$\{action\}/);
  assert.match(controlSource, /BMS DB/);
  assert.match(controlSource, /Optional DB runtime: start\/restart, health, logs\./);
  assert.match(controlSource, /Start DB/);
  assert.match(controlSource, /Restart DB/);
  assert.match(controlSource, /db_service_offline — press Start DB/);
  assert.match(controlSource, /CLI commands/);
  assert.match(controlSource, /bms db-service status/);
  assert.match(controlSource, /bms db-service start/);
  assert.match(controlSource, /bms db-service restart/);
  assert.match(controlSource, /bms db-service logs --tail 120/);
  assert.match(controlSource, /Documentation/);
  assert.match(controlSource, /BMS DB spec/);
  assert.match(controlSource, /https:\/\/github\.com\/MolBioFreak\/BioModStack\/blob\/main\/docs\/plans\/2026-05-06-bms-db-service-host-agent-code-level-spec\.md/);
  assert.match(controlSource, /PostgreSQL docs/);
  assert.match(controlSource, /https:\/\/www\.postgresql\.org\/docs\/current\//);
  assert.match(controlSource, /SQLite docs/);
  assert.match(controlSource, /https:\/\/www\.sqlite\.org\/docs\.html/);
  assert.doesNotMatch(controlSource, /Stop BMS DB service/);
  assert.doesNotMatch(controlSource, /Start BMS DB service/);
  assert.doesNotMatch(controlSource, /Restart BMS DB service/);
  assert.doesNotMatch(controlSource, /db_service_offline — use BMS DB service → Start/);
});
