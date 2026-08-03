import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('Z controls use stable provider-owned semantic actions and input-specific admission', () => {
  for (const action of [
    'oem.z.prepare',
    'oem.z.reconcile_switch_masks',
    'oem.z.manual_home',
    'oem.z.diagnostic_home_axis',
    'oem.z.move_steps',
    'oem.z.move_absolute',
    'oem.z.stop',
    'oem.z.observe',
  ]) assert.match(source, new RegExp(action.replaceAll('.', '\\.')));
  assert.match(source, /useBioXpOperatorActionAdmission/);
  assert.match(source, /zMoveNegativeAdmission\.data\?\.enabled/);
  assert.match(source, /zMovePositiveAdmission\.data\?\.enabled/);
  assert.match(source, /zAbsoluteAdmission\.data\?\.enabled/);
  assert.match(source, /zHomeAdmission\.data\?\.enabled/);
});

test('browser cannot supply Z pseudo-home and confirmations are honored', () => {
  assert.doesNotMatch(source, /zPseudoHome|setZPseudoHome/);
  assert.match(source, /Robot-owned PSUDO_Z_HOME/);
  assert.match(source, /requires_confirmation/);
  assert.match(source, /window\.confirm/);
});

test('Z dashboard and robot receipt truth remain visible newest-first', () => {
  assert.match(source, /z_axis\.provider\.state/);
  assert.match(source, /left_switch_disabled/);
  assert.match(source, /right_switch_disabled/);
  assert.match(source, /controller_acknowledged/);
  assert.match(source, /physical_effect_verified/);
  assert.match(source, /receipts \?\? \[\]\)\.slice\(0, 8\)/);
  assert.doesNotMatch(source, /slice\(-8\)\.reverse\(\)/);
});
