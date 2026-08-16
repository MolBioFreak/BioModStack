import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('manual controls expose only finite robot-owned operations', () => {
  for (const value of ["'x'", "'y'", "'z'", "'g'", "'door'", "'move-negative'", "'move-positive'", "'home'", "'commission-home'", "'open'", "'close'", "'open-wide'"]) assert.match(source, new RegExp(value));
  assert.match(source, /command: 'run_axis_diagnostic'/);
  assert.match(source, /command: 'stop_axis_diagnostic'/);
});
test('manual controls contain no arbitrary motor or prompt surface', () => {
  for (const value of ['window.prompt', 'window.confirm', 'distance', 'velocity', 'motor current', 'raw transport']) assert.doesNotMatch(source, new RegExp(value, 'i'));
});
