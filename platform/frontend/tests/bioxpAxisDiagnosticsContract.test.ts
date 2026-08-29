import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('manual controls expose finite robot-owned operations with dedicated Serial-206 Y authority', () => {
  for (const value of ["'x'", "'z'", "'g'", "'door'", "'move-negative'", "'move-positive'", "'home'", "'commission-home'", "'open'", "'close'", "'open-wide'"]) {
    assert.match(source, new RegExp(value));
  }
  assert.doesNotMatch(source, /type Axis = [^;]*'y'/);
  assert.match(source, /Serial-206 Y authority/);
  for (const actionId of ['oem.y.move_steps', 'oem.y.move_absolute', 'oem.y.manual_panel_home', 'oem.y.stop']) {
    assert.match(source, new RegExp(actionId.replaceAll('.', '\\.')));
  }
  assert.doesNotMatch(source, /command: 'run_axis_diagnostic'/);
  assert.doesNotMatch(source, /command: 'stop_axis_diagnostic'/);
});

test('manual controls contain no arbitrary motor or prompt surface', () => {
  for (const value of ['window.prompt', 'window.confirm', 'distance', 'velocity', 'motor current', 'raw transport']) {
    assert.doesNotMatch(source, new RegExp(value, 'i'));
  }
});
