import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('operator surface is compact and robot-authority driven', () => {
  // R1/R2: require separated evidence rather than a physical-stop claim.
  assert.match(source, /physical stopping remains unverified/);
  assert.match(source, /Source completion, controller ACK, and terminal readback are separate evidence/);
  for (const label of ['BioXP 3200', 'Connection', 'Controller Activation & Recovery', 'Activate 24 V / Prepare Motion', 'Non-homing Recovery', 'Exact OEM Manual Controls', 'BioXpCameraPanel', 'OEM Software Abort', 'Recent Robot Actions']) assert.match(source, new RegExp(label));
  for (const stale of ['available_commands', 'useBioXpCommand', 'BMS relay receipts', 'Physical Emergency Abort Unavailable']) assert.doesNotMatch(source, new RegExp(stale));
});
test('operator surface excludes rejected planning and evidence UI', () => {
  // R5/UI-06: read-only provider readiness/profile evidence is not a local
  // configuration form. Reject editable impostors rather than all field names.
  for (const field of ['runtime_ready', 'hardware_ready', 'Profile']) {
    assert.doesNotMatch(source, new RegExp(`(?:name|aria-label)=["']${field}["']`));
    assert.doesNotMatch(source, new RegExp(`(?:const|let)\\s+${field}\\s*=\\s*useState`));
  }
  assert.match(source, /SAP12\/13 observed/);
  assert.match(source, /Recovered OEM X initialization writes neither register/);
  assert.match(source, /Controller\/software reference is reported exactly as published by the robot provider/);
  for (const label of ['Maintenance motion state', 'Full OEM Lifecycle', 'Offline Protocol', 'Local Jobs', 'evidence_lock', 'mutationAccessSetting']) assert.doesNotMatch(source, new RegExp(label));
});
