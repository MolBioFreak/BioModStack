import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
test('controller initialization is a direct terse command', () => {
  assert.match(source, /command: 'recover_motion_non_homing'/);
  assert.match(source, />Initialize Controllers<\/button>/);
  for (const value of ['operator_ack', 'reason:', 'window.prompt', 'window.confirm']) assert.doesNotMatch(source, new RegExp(value));
});
test('frontend payload requires no recovery essay or acknowledgement', () => {
  assert.doesNotMatch(client, /operator_ack: 'RECOVER_MOTION'/);
  assert.doesNotMatch(source, /maintenance_state|recovery_required|motion_blocked/);
});
