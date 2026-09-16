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
    const labelExpression = cockpit.match(/const connectedLabel = ([\s\S]*?);/)?.[1];
    assert.ok(labelExpression, 'connection label expression must exist');
    // R5: connection label is separate from potentially unknown/stale motion
    // evidence. Test the label itself, not a ban on internal telemetry fields.
    assert.doesNotMatch(labelExpression, /UNKNOWN|STALE|runtime_ready|hardware_ready/);
    const label = new Function('active', 'connection', `return (${labelExpression});`);
    assert.equal(label(false, undefined), 'Disconnected');
    assert.equal(label(false, { reachable: true }), 'Disconnected');
    assert.equal(label(true, { reachable: false }), 'Connection error');
    assert.equal(label(true, { reachable: true }), 'Connected');
    assert.equal(label(true, { reachable: undefined }), 'Connected');
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
    const start = cockpit.indexOf('>Connection & Robot State</h2>');
    const end = cockpit.indexOf('</section>', start);
    assert.ok(start >= 0 && end > start, 'visible connection section must exist');
    const connectionPanel = cockpit.slice(start, end);
    assert.match(connectionPanel, /\{connectedLabel\}/);
    assert.match(connectionPanel, /\{connection.last_error\}/);
    // A Y-axis hardware Profile readback is not connection configuration.
    assert.doesNotMatch(connectionPanel, /server_setting|mutationAccessSetting|target_url|Profile/);
});
