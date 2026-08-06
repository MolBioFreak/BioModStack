import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('main Z controls use stable provider-owned semantic actions', () => {
  for (const action of [
    'meta.activate_motion',
    'oem.z.prepare',
    'oem.z.reconcile_switch_masks',
    'oem.z.set_home',
    'oem.z.manual_home',
    'oem.z.diagnostic_home_axis',
    'oem.z.move_steps',
    'oem.z.move_absolute',
    'oem.z.stop',
    'oem.z.abort',
  ]) assert.match(source, new RegExp(action.replaceAll('.', '\\.')));
  assert.match(source, /operatorActionById\('oem\.z\.manual_home'\)/);
  assert.match(source, /operatorActionById\('oem\.z\.move_steps'\)/);
  assert.match(source, /operatorActionById\('oem\.z\.move_absolute'\)/);
});

test('main Z surface exposes every disabled reason', () => {
  assert.match(source, /zPrimaryDisabledReasons/);
  assert.match(source, /Absolute move', zAbsoluteDisabledReason/);
  assert.match(source, /Activate: \{operatorActionById\('meta\.activate_motion'\)\?\.disabled_reason/);
});

test('typed Z absolute target uses local catalog bounds before robot revalidation', () => {
  assert.match(source, /dependency\.key !== 'z_target_oem_envelope'/);
  assert.match(source, /absoluteTargets\.z >= zAbsoluteMinimum/);
  assert.match(source, /absoluteTargets\.z <= zAbsoluteMaximum/);
  assert.match(source, /axis === 'z' \? !zAbsoluteEnabled/);
  assert.doesNotMatch(source, /axis === 'z' \? operatorActionById\('oem\.z\.move_absolute'\)\?\.enabled !== true/);
});

test('set-home requires a known stationary provider state', () => {
  assert.match(source, /zStatus\?\.position_steps == null/);
  assert.match(source, /zStatus\.speed_steps_s !== 0/);
  assert.match(source, /Set Home is unavailable until the provider reports the current Z position/);
  assert.match(source, /Set Home is unavailable until Z is confirmed stationary/);
  assert.match(source, /invokeAction\('oem\.z\.set_home', \{\}\)/);
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
  assert.match(source, /receipts \?\? \[\]\)\.slice\(0, 8\)/);
});
