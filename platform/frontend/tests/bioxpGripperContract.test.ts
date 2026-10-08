import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('generic gripper routes remain retired while typed OEM M02-M04 stages are explicit', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of ['axis/relative', 'axis/absolute', 'motion/gripper', "command: 'gripper'"]) {
        assert.doesNotMatch(combined, new RegExp(marker, 'i'));
    }
    for (const marker of ['gripper-current-31', 'gripper-clear-10000', 'gripper-home']) {
        assert.match(cockpit, new RegExp(marker));
    }
});
