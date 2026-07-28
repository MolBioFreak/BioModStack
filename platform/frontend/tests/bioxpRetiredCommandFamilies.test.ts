import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

const retiredCommands = [
    'activate_usb_for_service',
    'initialize_oem_environment',
    'run_oem_motor_stage',
    'record_oem_motor_stage_observation',
] as const;

test('retired robot command families have no active cockpit control or payload producer', () => {
    for (const command of retiredCommands) {
        assert.doesNotMatch(cockpit, new RegExp(command));
    }
    for (const label of [
        'Activate USB for BioXP Service',
        'Initialize BioXP OEM Environment',
        'Run the complete OEM non-motion startup sequence',
    ]) {
        assert.doesNotMatch(cockpit, new RegExp(label));
    }
    assert.match(cockpit, /command: 'collect_hardware_snapshot'/);
    assert.match(cockpit, /command: 'collect_axis_diagnostics'/);
    assert.match(cockpit, /command: 'run_axis_diagnostic'/);
    assert.match(cockpit, /command: 'stop_axis_diagnostic'/);
    assert.match(cockpit, /command: 'recover_motion_non_homing'/);
});

test('frontend executable payload type excludes retired command families', () => {
    const activeType = client.match(
        /export type BioXpActiveCommandName =([\s\S]*?);\n\nexport type BioXpCommandPayload/,
    )?.[1];
    assert.ok(activeType, 'BioXpActiveCommandName must explicitly bound executable UI payloads');
    for (const command of retiredCommands) {
        assert.doesNotMatch(activeType, new RegExp(command));
    }
    for (const command of [
        'collect_hardware_snapshot',
        'collect_axis_diagnostics',
        'run_axis_diagnostic',
        'stop_axis_diagnostic',
        'recover_motion_non_homing',
    ]) {
        assert.match(activeType, new RegExp(command));
    }
    assert.match(client, /Exclude<BioXpActiveCommandName, 'recover_motion_non_homing'>/);
});
