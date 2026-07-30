import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
test('transport claim and non-homing recovery are distinct terse commands', () => {
  assert.match(source, /command: 'activate_usb_for_service'/);
  assert.match(source, /command: 'recover_motion_non_homing'/);
  assert.match(source, />Claim USB Transport<\/button>/);
  assert.match(source, />Non-homing Recovery<\/button>/);
  for (const value of ['operator_ack', 'reason:', 'window.prompt', 'window.confirm']) assert.doesNotMatch(source, new RegExp(value));
});
test('frontend payload requires no recovery essay or acknowledgement', () => {
  assert.doesNotMatch(client, /operator_ack: 'RECOVER_MOTION'/);
  assert.match(source, /maintenance_state|recovery_required|motion_blocked/);
});
