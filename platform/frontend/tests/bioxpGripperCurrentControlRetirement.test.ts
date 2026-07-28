import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('the internal OEM gripper action-current write is not an operator control', () => {
    assert.doesNotMatch(cockpit, /gripper-current-31/);
    assert.doesNotMatch(cockpit, /Gripper current 31/);
    assert.doesNotMatch(cockpit, /command:\s*'run_oem_motor_stage'/);
    assert.doesNotMatch(cockpit, /OEM_MOTOR_STAGE_CONTROLS/);
    assert.match(cockpit, /Temporary OEM action current is internal/);
    assert.match(cockpit, /idle 10\/10 readback/);
    assert.match(cockpit, /operation: 'commission-home'/);
    assert.match(cockpit, /Atomic clear and home transaction with unconditional idle-current cleanup/);
});
