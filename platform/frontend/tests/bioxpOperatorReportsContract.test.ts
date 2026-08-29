import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), 'utf8');

test('BioXP cockpit mounts robot-owned reports without a local history writer', () => {
  const cockpit = read('src/components/BioXpCockpit.tsx');
  const panel = read('src/components/BioXpOperatorReports.tsx');
  const client = read('src/lib/bioxpClient.ts');
  assert.match(cockpit, /BioXpOperatorReports/);
  assert.match(panel, /Robot-owned audit data/);
  assert.match(panel, /No local history is shown/);
  assert.match(panel, /Export JSON/);
  assert.match(panel, /CAN exchanges/);
  assert.match(client, /operator-controls\/reports\/summary/);
  assert.match(client, /operator-controls\/reports\/commands/);
  assert.doesNotMatch(panel, /localStorage|indexedDB|sqlite|INSERT INTO/i);
});
