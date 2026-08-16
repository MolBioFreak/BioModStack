import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('connection state is connected, connection error, or disconnected without a synthetic unknown state', () => {
    assert.match(cockpit, /connection\?\.active === true/);
    assert.match(cockpit, /connection\?\.reachable === false \? 'Connection error' : 'Connected'/);
    assert.match(cockpit, /: 'Disconnected'/);
    assert.doesNotMatch(cockpit, /UNKNOWN|STALE|runtime_ready|hardware_ready/);
});

test('saved connection is explicitly reconnectable and disconnectable', () => {
    assert.match(client, /\/api\/bioxp\/connection\/connect/);
    assert.match(client, /\/api\/bioxp\/connection\/disconnect/);
    assert.match(cockpit, /active \? 'Reconnect BMS Link' : 'Connect BMS Link'/);
    assert.match(cockpit, /onClick=\{\(\) => disconnect\.mutate\(undefined\)\}/);
});

test('operator errors stay visible without exposing configuration scaffolding', () => {
    assert.match(cockpit, /connection\?\.last_error/);
    assert.match(cockpit, /bioXpErrorText\(error\)/);
    assert.doesNotMatch(cockpit, /server_setting|mutationAccessSetting|target_url|Profile/);
});
