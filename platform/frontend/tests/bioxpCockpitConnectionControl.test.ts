import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('BioXP cockpit exposes an obvious process-local reconnect control', () => {
    assert.match(cockpit, /useConnectBioXp/);
    assert.match(cockpit, /const connect = useConnectBioXp\(\)/);
    assert.match(cockpit, /onClick=\{\(\) => connect\.mutate\(undefined\)\}/);
    assert.match(cockpit, /Reconnect BioXP/);
    assert.match(cockpit, /disabled=\{!connection\?\.configured \|\| connection\?\.active === true \|\| connect\.isPending\}/);
    assert.match(cockpit, /connect\.error &&/);
    assert.match(cockpit, /bioXpErrorText\(connect\.error\)/);
});
