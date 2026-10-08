import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const interlinkStatus = readFileSync(resolve('src/components/bioxpInterlinkStatus.ts'), 'utf8');

test('BioXP page is status-first and command controls are server-driven', () => {
    for (const marker of ['Connection Status', 'Profile', 'Offline Protocol Validation', 'Local Jobs', 'COMMISSIONING_COMMANDS.map']) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(`${cockpit}\n${interlinkStatus}`, /No normal OEM commands are available/);
    assert.match(`${cockpit}\n${interlinkStatus}`, /commands are temporarily locked/);
    assert.match(cockpit, /online contract verification/);
});

test('canonical compact page labels the current commissioning tranche without reviving legacy controls', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of [
        'Collect Hardware Snapshot',
        'Activate USB for BioXP Service',
        'Initialize BioXP OEM Environment',
        'INITIALIZE',
    ]) {
        assert.match(combined, new RegExp(marker));
    }
});

test('retired hardware and host controls are absent', () => {
    const combined = `${cockpit}\n${client}`;
    for (const marker of [
        'Manual Movement', 'Commissioning Motion', 'AxisControls', 'Aspirate', 'Dispense',
        'Generic Gripper', 'USB Capture', 'Robot logs', 'Restart runtime', 'Reboot host',
        'HomeXY', 'InitializeMotion', 'Clear Head Lock', 'CameraHoldJog', 'shell', 'SSH',
    ]) {
        assert.doesNotMatch(combined, new RegExp(marker, 'i'));
    }
    for (const approvedStage of ['M01 · Z reference', 'M02 · Gripper current 31', 'M03 · Gripper clear +10000', 'M04 · Gripper home']) {
        assert.match(cockpit, new RegExp(approvedStage.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});
