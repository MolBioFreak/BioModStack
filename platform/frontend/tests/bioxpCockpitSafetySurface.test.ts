import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('unproven physical emergency abort is explicit and cannot dispatch', () => {
  // Installed BioXPControlLib.il:41706–41748 and ClassCanLib.il:11035–11067:
  // forceAbortMotion signals software waiters; it does not StopMotor fan out.
  assert.match(source, /Cancels software waiters, not motor motion\. Motors may continue/);
  assert.match(source, /This is not a physical emergency stop/);
  assert.match(source, /const abortXAggregate = \(\) => invokeInterrupt\('oem\.abort_all', 'BMS operator requested OEM software Abort: cancel waiters only; motors may continue'\)/);
  assert.doesNotMatch(source, />Emergency Stop<|Aggregate Emergency Stop|aggregate component stops|emergencyStop\.mutate/);
  for (const axis of ['x', 'y', 'z']) assert.match(source, new RegExp("invokeInterrupt\\('oem\\." + axis + "\\.stop'"));
  assert.match(source, /invokeAction\(action.action_id, \{ axis \}, componentStop\)/);
  assert.doesNotMatch(source, /invokeInterrupt\('oem\.z\.abort'/);

});

test('cockpit has no stale or unknown derived authorization model', () => {
  for (const value of ['UNKNOWN', 'STALE', 'deriveBioXpStatus', 'isBioXpControlPlaneFresh', 'maintenanceState']) assert.doesNotMatch(source, new RegExp(value));
});
