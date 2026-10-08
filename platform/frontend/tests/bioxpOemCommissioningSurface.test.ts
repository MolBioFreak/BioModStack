import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('OEM commissioning controls expose exact stage names and corrected operator copy', () => {
    for (const marker of [
        'Initialize/Verify Four Pipette Controllers',
        'Initialize Controllers Without Motion',
        'constructor_pipette_stage',
        'initialization_without_motion',
        'initial_check',
        'Repeatable OEM check',
        'final white-LED sequence',
    ]) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.doesNotMatch(cockpit, /Construct Four Pipettes/);
    assert.doesNotMatch(cockpit, /red LED stage/);
    assert.doesNotMatch(cockpit, /and a final read/);
});

test('operator receives full handler evidence and explicit non-secret mutation setup', () => {
    for (const marker of [
        'Latest Handler Result',
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
