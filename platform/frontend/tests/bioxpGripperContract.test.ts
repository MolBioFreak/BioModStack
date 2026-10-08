import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('generic gripper routes and the internal M02 current write are retired from operator controls', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of ['axis/relative', 'axis/absolute', 'motion/gripper', "command: 'gripper'"]) {
        assert.doesNotMatch(combined, new RegExp(marker, 'i'));
    }
    assert.doesNotMatch(cockpit, /gripper-current-31|Gripper current 31/);
    for (const marker of ['gripper-clear-10000', 'gripper-home']) {
        assert.match(cockpit, new RegExp(marker));
    }
});
