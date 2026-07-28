import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('retired OEM startup remains display-only lifecycle evidence', () => {
    for (const marker of [
        'OEM Startup Lifecycle',
        'constructor_pipette_stage',
        'initialization_without_motion',
        'initial_check',
    ]) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    for (const retiredControl of [
        'Initialize BioXP OEM Environment',
        'initialize_oem_environment',
        'Initialize/Verify Four Pipette Controllers',
        'Initialize Controllers Without Motion',
        'Run OEM Initial Check',
        "command: 'construct_pipettes'",
        "command: 'initialize_without_motion'",
        "command: 'run_initial_check'",
    ]) {
        assert.doesNotMatch(cockpit, new RegExp(retiredControl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('retired OEM startup has no acknowledgement or click-time command producer', () => {
    assert.doesNotMatch(cockpit, /Run the complete OEM non-motion startup sequence/);
    assert.doesNotMatch(cockpit, /operator_ack: 'INITIALIZE'/);
    assert.doesNotMatch(cockpit, /Type INITIALIZE to acknowledge/);
    assert.doesNotMatch(cockpit, /oemStartupAck/);
});

test('operator receives full handler evidence and explicit non-secret mutation setup', () => {
    for (const marker of [
        'Latest Delivery Result',
        'handler_response',
        'remote_acknowledged',
        'physical_effect_verified',
        'executeCommand.data.command_id',
        'executeCommand.data.command',
        'executeCommand.data.idempotency_key',
        'executeCommand.data.generation',
        'executeCommand.data.started_at',
        'executeCommand.data.finished_at',
        'server_setting',
        'No API key or secret is required',
    ]) {
        assert.match(cockpit + client, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});
