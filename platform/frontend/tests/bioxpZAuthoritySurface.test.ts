import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('main Z controls use the typed v2 provider-owned action lane', () => {
  for (const action of [
    'oem.z.manual_home',
    'oem.z.move_steps',
    'oem.z.move_absolute',
    'oem.z.clear',
    'oem.z.stop',
  ]) assert.match(source, new RegExp(action.replaceAll('.', '\\.')));
  // REPAIR-PLAN R2 / UI-03: addressed Z Stop is distinct from all-board
  // logical forceAbort. A standalone Z Abort is a retired duplicate.
  assert.doesNotMatch(source, /oem\.z\.abort/);
  assert.match(source, /invokeInterrupt\('oem\.abort_all'/);
  assert.match(source, /submitV2\(\{ \.\.\.envelope, action_id: 'oem\.z\.manual_home', inputs: \{\} \}\)/);
  assert.match(source, /submitV2\(\{ \.\.\.envelope, action_id: 'oem\.z\.move_steps', inputs:/);
  assert.match(source, /submitV2\(\{ \.\.\.envelope, action_id: 'oem\.z\.move_absolute', inputs:/);
});

test('main Z minus and plus preserve opposite signed payloads', () => {
  assert.match(source, /action_id: 'oem\.z\.move_steps', inputs: \{ steps: -Math\.abs\(manualSteps\.z\) \}/);
  assert.match(source, /action_id: 'oem\.z\.move_steps', inputs: \{ steps: Math\.abs\(manualSteps\.z\) \}/);
});

test('main Z surface keeps pseudo-home authority on the robot', () => {
  assert.match(source, /submitV2\(\{ \.\.\.envelope, action_id: 'oem\.z\.clear', inputs: \{\} \}\)/);
  assert.match(source, /Z Clear \(automatic OEM position\)/);
  assert.match(source, /OEM moveZ applies the robot-owned PSUDO_Z_HOME as a dynamic minimum target/);
  assert.match(source, /A request below the current value is replaced with that value before dispatch/);
  assert.match(source, /Z does not automatically return to pseudo-home after every movement/);
  assert.match(source, /Z Clear returns to the selected pseudo-home/);
  assert.match(source, /Manual Home follows the OEM homing sequence and establishes controller coordinate 0/);
  assert.doesNotMatch(source, /Tips loaded · 500/);
  assert.doesNotMatch(source, /No tips · 65,000/);
});

test('main Z normal controls fail closed when current robot control state is unavailable', () => {
  // The shared normal-action guard now owns the same fail-closed contract.
  assert.match(source, /const v2NormalActionById = \(actionId: string\) => v2AuthorityCoherent/);
  assert.match(source, /if \(!v2AuthorityCoherent\) return 'Current robot control state is unavailable\.'/);
  assert.match(source, /const action = v2NormalActionById\(actionId\);\s*if \(!action\) return 'Robot action unavailable\.'/);
  assert.match(source, /return action.enabled === true \? null : action.disabled_reason \?\? 'Robot action unavailable\.'/);
  assert.match(source, /const zAbsoluteDisabledReason = integerInputError\(absoluteTargets.z, zAbsoluteInput, 'Requested Z target'\)\s*\?\? v2ActionDisabledReason\('oem.z.move_absolute'\)/);
  assert.match(source, /const zAbsoluteEnabled = zAbsoluteDisabledReason === null/);
  assert.match(source, /if \(!zAbsoluteEnabled\) return/);
  for (const direction of ['Negative', 'Positive', 'Home']) {
    assert.ok(source.includes(`const z${direction}Enabled = z${direction}DisabledReason === null`));
    assert.match(source, new RegExp(`isZ${direction}\\s*\\? z${direction}Enabled`));
  }
  assert.match(source, /Current robot control state is unavailable/);
  const normalGuardStart = source.indexOf('const v2ActionDisabledReason =');
  const normalGuardEnd = source.indexOf('const xNegativeInputs =', normalGuardStart);
  assert.ok(normalGuardStart >= 0 && normalGuardEnd > normalGuardStart);
  // Only the Z normal-control reason is governed here. Deck/XY diagnostics
  // retain their own exact failure context rather than a whole-file word ban.
  assert.doesNotMatch(source.slice(normalGuardStart, normalGuardEnd), /Fresh v2 catalog or dashboard authority is unavailable/);
});

test('typed Z absolute target keeps local bounds and v2 admission authority distinct', () => {
  assert.match(source, /const zAbsoluteCatalogAction = operatorActionById\('oem\.z\.move_absolute'\)/);
  assert.match(source, /const zAbsoluteInput = zAbsoluteCatalogAction\?\.inputs.find\(\(input\) => input.name === 'position_steps'\)/);
  assert.match(source, /const zAbsoluteMinimum = integerMinimum\(zAbsoluteInput\)/);
  assert.match(source, /const zAbsoluteMaximum = integerMaximum\(zAbsoluteInput\)/);
  assert.match(source, /integerInputError\(absoluteTargets.z, zAbsoluteInput, 'Requested Z target'\)\s*\?\? v2ActionDisabledReason\('oem.z.move_absolute'\)/);
  assert.match(source, /if \(!Number.isInteger\(value\)\) return/);
  assert.match(source, /minimum !== undefined && value < minimum/);
  assert.match(source, /maximum !== undefined && value > maximum/);
  assert.match(source, /min=\{axis === 'x' \? xAbsoluteMinimum : axis === 'z' \? zAbsoluteMinimum : undefined\}/);
  assert.match(source, /max=\{axis === 'x' \? xAbsoluteMaximum : axis === 'z' \? zAbsoluteMaximum : undefined\}/);
  assert.match(source, /axis === 'z' \? !zAbsoluteEnabled/);
});

test('manual Home and Clear remain distinct typed robot-owned operations', () => {
  assert.match(source, /action_id: 'oem\.z\.manual_home', inputs: \{\}/);
  assert.match(source, /action_id: 'oem\.z\.clear', inputs: \{\}/);
  assert.match(source, /Manual Home follows the OEM homing sequence and establishes controller coordinate 0/);
  assert.match(source, /Z Clear returns to the selected pseudo-home/);
});

test('Z stop and abort use the independent typed interrupt lane', () => {
  assert.match(source, /invokeInterrupt\('oem\.z\.stop'/);
  assert.doesNotMatch(source, /oem\.z\.abort/);
  assert.match(source, /const abortXAggregate = \(\) => invokeInterrupt\('oem\.abort_all'/);
  assert.match(source, /if \(actionId === 'oem.z.stop'\) return interruptZStop/);
  assert.match(source, /return interruptAggregateAbort/);
  assert.match(source, /useInterruptBioXpOperatorActionV1/);
});

test('Z dashboard and robot receipt truth remain visible newest-first', () => {
  assert.match(source, /z_axis\.provider\.state/);
  assert.match(source, /left_switch_disabled/);
  assert.match(source, /right_switch_disabled/);
  assert.match(source, /controller_acknowledged/);
  assert.match(source, /physical_effect_verified/);
  assert.match(source, /historyQuery\.data\?\.receipts \?\? \[\]/);
  assert.match(source, /\.slice\(0, historyLimit\)/);
});
