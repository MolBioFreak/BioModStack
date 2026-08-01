import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('operator surface is compact and server-command driven', () => {
  for (const label of ['BioXP 3200', 'Connection', 'Controller Transport & Recovery', 'Claim USB Transport', 'Non-homing Recovery', 'Manual Controls', 'BioXpCameraPanel', 'Physical Emergency Abort Unavailable', 'Recent Commands']) assert.match(source, new RegExp(label));
  assert.match(source, /available_commands/);
});
test('operator surface excludes rejected planning and evidence UI', () => {
  for (const label of ['runtime_ready', 'hardware_ready', 'Maintenance motion state', 'Full OEM Lifecycle', 'Offline Protocol', 'Local Jobs', 'Profile', 'evidence_lock', 'mutationAccessSetting']) assert.doesNotMatch(source, new RegExp(label));
});
