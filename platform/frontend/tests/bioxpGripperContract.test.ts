import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('generic and standalone gripper controls remain retired while the cleanup-safe semantic transaction is explicit', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of [
        'axis/relative', 'axis/absolute', 'motion/gripper', "command: 'gripper'",
        'gripper-current-31', 'gripper-clear-10000', 'gripper-home',
    ]) {
        assert.doesNotMatch(combined, new RegExp(marker, 'i'));
    }
    for (const marker of ["operation: 'commission-home'", 'OEM clear + home', 'idle 10/10 readback']) {
        assert.ok(cockpit.includes(marker), `missing semantic gripper marker: ${marker}`);
    }
});
