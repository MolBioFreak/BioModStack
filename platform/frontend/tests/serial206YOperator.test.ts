import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const client = readFileSync(new URL('../src/lib/bioxpClient.ts', import.meta.url), 'utf8');
const cockpit = readFileSync(new URL('../src/components/BioXpCockpit.tsx', import.meta.url), 'utf8');

test('BioXP client exposes v2 Y command and receipt polling seams', () => {
    assert.match(client, /operator-controls\/v2\/dashboard/);
    assert.match(client, /operator-controls\/v2\/actions/);
    assert.match(client, /operator-controls\/v2\/receipts/);
    assert.match(client, /500/);
    assert.match(client, /issued_pending/);
});

test('BioXP cockpit exposes Y authority and typed controls', () => {
    assert.match(cockpit, /y_axis/);
    assert.match(cockpit, /oem\.y\.move_steps/);
    assert.match(cockpit, /oem\.y\.move_absolute/);
    assert.match(cockpit, /oem\.y\.manual_panel_home/);
    assert.match(cockpit, /oem\.y\.stop/);
    assert.match(cockpit, /active_board_epoch/);
    assert.match(cockpit, /physical_position_verified/);
    assert.match(cockpit, /latest_compact_receipt/);
});

test('BioXP v2 Y wire types match the robot-owned authority schema', () => {
    assert.match(client, /position_steps/);
    assert.match(client, /position_reply_valid/);
    assert.match(client, /command_queue/);
    assert.match(client, /request_schema_version/);
    assert.match(client, /physical_position_verified/);
});
