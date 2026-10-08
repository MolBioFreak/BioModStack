import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('unverified gripper and generic motion contracts are retired', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of ['gripper', 'axis/relative', 'axis/absolute', 'motion/gripper', 'operator_ack']) {
        assert.doesNotMatch(combined, new RegExp(marker, 'i'));
    }
});
