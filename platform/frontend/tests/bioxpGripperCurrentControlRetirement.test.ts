import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('internal gripper current is not an operator control', () => {
  for (const value of ['gripper-current', 'action current', 'M02', 'set current']) assert.doesNotMatch(source, new RegExp(value, 'i'));
});
