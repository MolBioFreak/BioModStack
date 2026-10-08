import * as assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const cockpitSource = readFileSync(new URL('./src/components/BioXpCockpit.tsx', `file://${process.cwd()}/`), 'utf8');
const clientSource = readFileSync(new URL('./src/lib/bioxpClient.ts', `file://${process.cwd()}/`), 'utf8');

const sourceBetween = (source: string, start: string, end: string) => {
    const startIndex = source.indexOf(start);
    assert.notEqual(startIndex, -1, `missing start marker ${start}`);
    const endIndex = source.indexOf(end, startIndex + start.length);
    assert.notEqual(endIndex, -1, `missing end marker ${end}`);
    return source.slice(startIndex, endIndex);
};

test('BioXP client exposes dedicated OEM gripper routes instead of generic G moves', () => {
    assert.match(clientSource, /useGripperStatus/);
    assert.match(clientSource, /\/api\/bioxp\/motion\/gripper\/status/);
    assert.match(clientSource, /\/api\/bioxp\/motion\/gripper\/restore_idle_current/);
    assert.match(clientSource, /\/api\/bioxp\/motion\/gripper\/clear/);
    assert.match(clientSource, /operator_ack: 'GRIPPER_CLEAR'/);
    assert.match(clientSource, /operator_ack: 'GRIPPER_HOME'/);
});

test('BioXP cockpit surfaces OEM Gripper Contract with raw truth and fail-closed copy', () => {
    const panel = sourceBetween(cockpitSource, 'const gripperPanel = (', '    return (');
    assert.match(panel, /OEM Gripper Contract/);
    assert.match(panel, /G LIMITS/);
    assert.match(panel, /CURRENT/);
    assert.match(panel, /OEM HOME/);
    assert.match(panel, /both G limits are active, robot returns 409 before motion/);
    assert.match(panel, /Restore G idle current/);
    assert.match(panel, /OEM Gripper Clear/);
    assert.match(panel, /OEM Gripper Home/);
});

test('commissioning motion demotes generic G jogs in favor of OEM gripper contract', () => {
    const manualTab = sourceBetween(cockpitSource, "{activeTab === 'manual'", "{activeTab === 'controls'");
    assert.doesNotMatch(manualTab, /<AxisControls axis="g" label="Gripper"/);
    assert.match(manualTab, /Generic G jog controls are disabled here/);
    assert.match(manualTab, /\{gripperPanel\}/);
});
