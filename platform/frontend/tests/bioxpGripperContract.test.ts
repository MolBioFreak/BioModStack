import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('gripper uses finite home/open/close positions only', () => {
  assert.match(source, /label: 'Gripper'/);
  for (const op of ["'commission-home'", "'open'", "'close'", "'open-wide'"]) assert.match(source, new RegExp(op));
  for (const forbidden of ['current_ma', 'current_a', 'motor_current', 'Current (mA)', 'Current (A)']) {
    assert.doesNotMatch(source, new RegExp(forbidden, 'i'));
  }
});
