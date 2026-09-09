import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const controls = readFileSync(resolve('src/components/BioXpOperatorControlTabs.tsx'), 'utf8');

test('OEM operator surface retains connection, recovery, camera, stop, and mounts the route plane', () => {
    // R2/R5: connection ownership and indexed receipts replace retired commands.
    assert.match(cockpit, /useConnectBioXp\(\)/);
    assert.match(cockpit, /ownership\.transport/);
    assert.match(cockpit, /invokeInterrupt\('oem\.x\.stop'/);
    assert.match(cockpit, /invokeInterrupt\('oem\.z\.stop'/);
    assert.match(cockpit, /physical stopping remains unverified/);
    for (const label of ['Connection', 'Connect', 'Non-homing Recovery', 'Manual Controls', 'BioXpCameraPanel', 'Recent Robot Actions', 'BioXpOperatorControlTabs']) {
        assert.match(cockpit, new RegExp(label));
    }
});

test('commissioning and provenance are robot-owned receipt fields, not duplicate cockpit state', () => {
    // R1/R5 require receipt proof in history, never independently fabricated state.
    assert.match(cockpit, /record\.physical_effect_verified \? 'Physical effect verified' : 'Physical effect unverified'/);
    assert.match(cockpit, /record\.controller_terminal_state_verified/);
    assert.doesNotMatch(cockpit, /setPhysicalEffectVerified|physical_effect_verified\s*:\s*true/);
    for (const value of ['collect_hardware_snapshot', 'OEM Startup Lifecycle', 'startup_lifecycle', 'registry_sha256', 'Local Jobs']) {
        assert.doesNotMatch(cockpit, new RegExp(value));
    }
    for (const value of ['source_authority_verified', 'physical_effect_verified', 'remote_acknowledged', 'Record PASS', 'Record FAIL', 'Your physical observation']) {
        assert.match(controls, new RegExp(value));
    }
});
