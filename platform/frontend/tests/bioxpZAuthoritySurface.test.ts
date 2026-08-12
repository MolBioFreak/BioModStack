import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('main Z controls use stable provider-owned semantic actions', () => {
  for (const action of [
    'meta.activate_motion',
    'oem.z.manual_home',
    'oem.z.move_steps',
    'oem.z.move_absolute',
    'oem.z.clear',
    'oem.z.stop',
    'oem.z.abort',
  ]) assert.match(source, new RegExp(action.replaceAll('.', '\\.')));
  assert.match(source, /invokeAction\('oem\.z\.manual_home', \{\}\)/);
  assert.match(source, /invokeAction\('oem\.z\.move_steps', \{ steps:/);
  assert.match(source, /invokeAction\('oem\.z\.move_absolute', \{ position_steps:/);
});

test('main Z minus and plus preserve opposite signed payloads', () => {
  assert.match(source, /invokeAction\('oem\.z\.move_steps', \{ steps: -Math\.abs\(manualSteps\.z\) \}\)/);
  assert.match(source, /invokeAction\('oem\.z\.move_steps', \{ steps: Math\.abs\(manualSteps\.z\) \}\)/);
});

test('main Z surface keeps pseudo-home authority on the robot', () => {
  assert.match(source, /invokeAction\('oem\.z\.clear', \{\}\)/);
  assert.match(source, /Z Clear \(automatic OEM position\)/);
  assert.match(source, /OEM moveZ applies the robot-owned PSUDO_Z_HOME as a dynamic minimum target/);
  assert.match(source, /A request below the current value is replaced with that value before dispatch/);
  assert.match(source, /Z does not automatically return to pseudo-home after every movement/);
  assert.match(source, /Z Clear returns to the selected pseudo-home/);
  assert.match(source, /Manual Home follows the OEM homing sequence and establishes controller coordinate 0/);
  assert.doesNotMatch(source, /Tips loaded · 500/);
  assert.doesNotMatch(source, /No tips · 65,000/);
});

test('main Z surface exposes current disabled reasons', () => {
  assert.match(source, /zAbsoluteAction\.provider_unavailable_reason \?\? 'Robot action unavailable\.'/);
  assert.match(source, /zAbsoluteStaticBlocker\.reason \?\? zAbsoluteAction\.disabled_reason/);
  assert.match(source, /title=\{axis === 'z' \? zAbsoluteDisabledReason \?\? 'Robot-owned exact OEM absolute move' : undefined\}/);
  assert.match(source, /Activate: \{operatorActionById\('meta\.activate_motion'\)\?\.disabled_reason/);
});

test('typed Z absolute target uses local catalog bounds before robot revalidation', () => {
  assert.match(source, /dependency\.key !== 'z_target_oem_envelope'/);
  assert.match(source, /zAbsoluteAction\.provider_available !== true/);
  assert.match(source, /absoluteTargets\.z >= zAbsoluteMinimum/);
  assert.match(source, /absoluteTargets\.z <= zAbsoluteMaximum/);
  assert.match(source, /axis === 'z' \? !zAbsoluteEnabled/);
  assert.doesNotMatch(source, /axis === 'z' \? operatorActionById\('oem\.z\.move_absolute'\)\?\.enabled !== true/);
});

test('manual Home and Clear remain distinct robot-owned operations', () => {
  assert.match(source, /invokeAction\('oem\.z\.manual_home', \{\}\)/);
  assert.match(source, /invokeAction\('oem\.z\.clear', \{\}\)/);
  assert.match(source, /Manual Home follows the OEM homing sequence and establishes controller coordinate 0/);
  assert.match(source, /Z Clear returns to the selected pseudo-home/);
});

test('Z stop and abort use the independent emergency mutation lane', () => {
  assert.match(source, /const emergencyAction = useInvokeBioXpOperatorAction\(\)/);
  assert.match(source, /invokeAction\('oem\.z\.stop', \{\}, emergencyAction\)/);
  assert.match(source, /invokeAction\('oem\.z\.abort', \{\}, emergencyAction\)/);
  assert.match(source, /axis === 'z' \? emergencyAction\.isPending : invokeOperatorAction\.isPending/);
});

test('Z dashboard and robot receipt truth remain visible newest-first', () => {
  assert.match(source, /z_axis\.provider\.state/);
  assert.match(source, /left_switch_disabled/);
  assert.match(source, /right_switch_disabled/);
  assert.match(source, /controller_acknowledged/);
  assert.match(source, /physical_effect_verified/);
  assert.match(source, /historyQuery\.data\?\.receipts \?\? \[\]/);
  assert.match(source, /\.slice\(0, 8\)/);
});
