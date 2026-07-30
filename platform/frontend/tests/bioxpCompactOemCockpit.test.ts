import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('BioXP cockpit is a compact OEM operator surface', () => {
    for (const marker of [
        'BioXP 3200',
        'Connection',
        'Initialize Controllers',
        'Manual Controls',
        'Camera',
        'Emergency Stop',
        'Move −',
        'Move +',
        'Home',
        'Stop',
    ]) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }

    for (const rejected of [
        'Status-first operator surface',
        'Maintenance motion state',
        'Full OEM Lifecycle',
        'Dry-run Contract',
        'OEM Startup Lifecycle',
        'Per-axis OEM capability diagnostics',
        'Normal Commands',
        'Offline Protocol Validation',
        'Local Jobs',
        'Profile',
        'runtime_ready',
        'hardware_ready',
        'runtime_fresh',
        'hardware_fresh',
        'window.prompt',
        'window.confirm',
        'operator_ack',
        'reason:',
    ]) {
        assert.doesNotMatch(cockpit, new RegExp(rejected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('compact cockpit can reconnect and sends terse typed commands', () => {
    assert.match(cockpit, /useConnectBioXp/);
    assert.match(cockpit, /useDisconnectBioXp/);
    assert.match(cockpit, /command:\s*'recover_motion_non_homing'/);
    assert.match(cockpit, /command:\s*'run_axis_diagnostic'/);
    assert.match(cockpit, /command:\s*'stop_axis_diagnostic'/);

    assert.doesNotMatch(client, /operator_ack:\s*'RECOVER_MOTION'/);
    assert.doesNotMatch(client, /reason:\s*string/);
});
