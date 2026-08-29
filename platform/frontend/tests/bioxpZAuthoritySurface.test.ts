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
    'oem.z.abort',
  ]) assert.match(source, new RegExp(action.replaceAll('.', '\\.')));
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

test('main Z normal controls fail closed on missing fresh v2 authority', () => {
  assert.match(source, /const zAbsoluteAction = v2NormalActionById\('oem\.z\.move_absolute'\)/);
  assert.match(source, /const zAbsoluteDisabledReason = !v2AuthorityCoherent/);
  assert.match(source, /zActionId\s*\? v2AuthorityCoherent && action\?\.enabled === true/);
  assert.match(source, /Fresh v2 catalog or dashboard authority is unavailable/);
});

test('typed Z absolute target keeps local bounds and v2 admission authority distinct', () => {
  assert.match(source, /const zAbsoluteCatalogSpec = operatorActionById\('oem\.z\.move_absolute'\)/);
  assert.match(source, /const zAbsoluteAction = v2NormalActionById\('oem\.z\.move_absolute'\)/);
  assert.match(source, /absoluteTargets\.z >= zAbsoluteMinimum/);
  assert.match(source, /absoluteTargets\.z <= zAbsoluteMaximum/);
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
  assert.match(source, /invokeInterrupt\('oem\.z\.abort'/);
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
