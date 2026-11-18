import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), 'utf8');

test('BioXP cockpit shows robot-owned recent actions and exports without a local history writer', () => {
  const cockpit = read('src/components/BioXpCockpit.tsx');
  const panel = read('src/components/BioXpRecentActions.tsx');
  const client = read('src/lib/bioxpClient.ts');
  assert.match(cockpit, /BioXpRecentActions/);
  assert.match(panel, /Export JSON/);
  assert.match(panel, /Export CSV/);
  assert.match(panel, /useCreateBioXpOperatorReportExport/);
  assert.match(client, /operator-controls\/reports\/exports/);
  assert.doesNotMatch(panel, /localStorage|indexedDB|sqlite|INSERT INTO/i);
});
