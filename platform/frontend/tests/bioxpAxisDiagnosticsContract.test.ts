import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('cockpit exposes complete finite OEM diagnostic blocks for every motion component', () => {
    for (const marker of [
        'Per-axis OEM capability diagnostics',
        "axis: 'x'",
        "axis: 'y'",
        "axis: 'z'",
        "axis: 'g'",
        "axis: 'door'",
        "operation: 'move-negative'",
        "operation: 'move-positive'",

        "operation: 'home'",
        "operation: 'park-6000'",
        "operation: 'commission-home'",
        "operation: 'close'",
        "operation: 'open'",
        "operation: 'open-wide'",
        "command: 'collect_axis_diagnostics'",
        "command: 'run_axis_diagnostic'",
        "command: 'stop_axis_diagnostic'",
        'Collect live axis status',
        'Stop X axis',
        'Stop Y axis',
        'Stop Z axis',
        'Stop Gripper',
        'Stop Thermal door',
    ]) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('axis diagnostics never expose raw transport or standalone current controls', () => {
    for (const forbidden of [
        'OEM_MOTOR_STAGE_CONTROLS',
        'runOemMotorStage',
        "command: 'run_oem_motor_stage'",
        'Queue M01',
        'Queue M03',
        'Queue M04',
        'gripper-current-31',
        'Gripper current 31',
        'raw TMCL',
        'board_id',
        'motor_id',
    ]) {
        assert.doesNotMatch(cockpit, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(cockpit, /Temporary OEM action current is internal/);
    assert.match(cockpit, /idle 10\/10 readback/);
});

test('physical actions require click-time confirmation and carry generation-bound audit reason', () => {
    assert.match(cockpit, /window\.confirm/);
    assert.match(cockpit, /window\.prompt/);
    assert.match(cockpit, /expected_generation/);
    assert.match(cockpit, /operator_ack: 'RUN_AXIS_DIAGNOSTIC'/);
    assert.match(cockpit, /operator_ack: 'STOP_AXIS'/);
    assert.match(cockpit, /Physical motion may occur/);
});

test('component stop has an independent mutation lane and stays available while a run is pending', () => {
    for (const marker of [
        'const stopCommand = useBioXpCommand();',
        'stopCommand.mutate({',
        'disabled={!axisStopAvailable || stopCommand.isPending}',
    ]) assert.ok(cockpit.includes(marker), marker);
    assert.ok(!cockpit.includes('disabled={!axisStopAvailable || executeCommand.isPending}'));
});
