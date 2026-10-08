import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('the internal OEM gripper action-current write is not an operator control', () => {
    assert.doesNotMatch(cockpit, /gripper-current-31/);
    assert.doesNotMatch(cockpit, /Gripper current 31/);
    assert.match(cockpit, /OEM M02 is internal action-current setup and is never exposed as a standalone operator action/);
    assert.doesNotMatch(cockpit, /Only the first four completed OEM stages are exposed/);
    assert.match(cockpit, /stage: 'z-home'/);
    assert.match(cockpit, /stage: 'gripper-clear-10000'/);
    assert.match(cockpit, /stage: 'gripper-home'/);
});
