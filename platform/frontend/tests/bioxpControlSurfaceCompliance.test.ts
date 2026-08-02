import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('operator surface is compact and robot-authority driven', () => {
  for (const label of ['BioXP 3200', 'Connection', 'Controller Activation & Recovery', 'Activate 24 V / Prepare Motion', 'Non-homing Recovery', 'Exact OEM Manual Controls', 'BioXpCameraPanel', 'Physical Aggregate Emergency Stop', 'Recent Robot Actions']) assert.match(source, new RegExp(label));
  for (const stale of ['available_commands', 'useBioXpCommand', 'BMS relay receipts', 'Physical Emergency Abort Unavailable']) assert.doesNotMatch(source, new RegExp(stale));
});
test('operator surface excludes rejected planning and evidence UI', () => {
  for (const label of ['runtime_ready', 'hardware_ready', 'Maintenance motion state', 'Full OEM Lifecycle', 'Offline Protocol', 'Local Jobs', 'Profile', 'evidence_lock', 'mutationAccessSetting']) assert.doesNotMatch(source, new RegExp(label));
});
