import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const contract = readFileSync(resolve('../../docs/BioXP_Compact_Control_Plane.md'), 'utf8');
const labContract = readFileSync(resolve('../../docs/Lab_Automation_MolBio_and_Sequencing.md'), 'utf8');
const activeContract = contract.split('The retired routes are absent:')[0];
const lifecycleAction = source.slice(
  source.indexOf('const invokeLifecycleAction ='),
  source.indexOf('const operatorPathForControl ='),
);
test('activation and non-homing recovery use the robot-owned v2 action catalog', () => {
  assert.match(source, /v2ActionDisabledReason\('meta\.activate_motion'\)/);
  assert.match(source, /v2ActionDisabledReason\('meta\.recover_motion_non_homing'\)/);
  assert.match(source, /invokeLifecycleAction\('meta\.activate_motion'\)/);
  assert.match(source, /invokeLifecycleAction\('meta\.recover_motion_non_homing'\)/);
  assert.match(source, /invokeLifecycleActionMutation\.mutate/);
  assert.match(source, /lifecycleReceipt/);
  assert.match(source, />Non-homing Recovery<\/button>/);
  assert.ok(lifecycleAction.length > 0);
  for (const value of ['operator_ack', 'operator_reason']) assert.doesNotMatch(lifecycleAction, new RegExp(value));
  for (const value of ['window.prompt', 'window.confirm']) assert.doesNotMatch(source, new RegExp(value));
});
test('frontend removes the separate recovery relay', () => {
  assert.doesNotMatch(client, /operator_ack: 'RECOVER_MOTION'/);
  assert.doesNotMatch(client, /connection\/recover-motion-non-homing/);
  assert.doesNotMatch(source, /useRecoverBioXpMotion/);
  assert.doesNotMatch(activeContract, /connection\/recover-motion-non-homing/);
  assert.match(contract, /The retired routes are absent:[\s\S]*connection\/recover-motion-non-homing/);
  assert.match(contract, /meta\.activate_motion/);
  assert.match(contract, /meta\.recover_motion_non_homing/);
  assert.doesNotMatch(labContract, /typed non-homing recovery relay/);
  assert.match(labContract, /meta\.recover_motion_non_homing/);
});
