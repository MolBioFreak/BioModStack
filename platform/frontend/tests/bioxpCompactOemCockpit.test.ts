import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const lifecycleActionSlice = cockpit.slice(
    cockpit.indexOf('const invokeLifecycleAction ='),
    cockpit.indexOf('const operatorPathForControl ='),
);

test('BioXP cockpit is a compact OEM operator surface', () => {
    for (const marker of [
        'BioXP 3200',
        'Connection',
        'Controller Activation & Recovery',
        'Activate 24 V / Prepare Motion',
        'Non-homing Recovery',
        'Manual Controls',
        'Camera',
        'Physical Aggregate Emergency Stop',
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
        'window.prompt',
        'window.confirm',
        'operator_ack',
    ]) {
        assert.doesNotMatch(cockpit, new RegExp(rejected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('compact cockpit reconnects and sends canonical lifecycle actions through V2', () => {
    assert.match(cockpit, /useConnectBioXp/);
    assert.match(cockpit, /useDisconnectBioXp/);
    assert.match(lifecycleActionSlice, /invokeLifecycleAction\('meta\.activate_motion'\)/);
    assert.match(lifecycleActionSlice, /invokeLifecycleAction\('meta\.recover_motion_non_homing'\)/);
    assert.doesNotMatch(lifecycleActionSlice, /operator_ack/);
    assert.doesNotMatch(lifecycleActionSlice, /reason\s*:/);
    assert.doesNotMatch(client, /operator_ack:\s*'RECOVER_MOTION'/);
    assert.doesNotMatch(client, /command:\s*'activate_usb_for_service'/);
    assert.doesNotMatch(client, /command:\s*'recover_motion_non_homing'/);
});
