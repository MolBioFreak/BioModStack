import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const controls = readFileSync(resolve('src/components/BioXpOperatorControlTabs.tsx'), 'utf8');

test('OEM operator surface retains connection, recovery, camera, stop, and mounts the route plane', () => {
    for (const label of ['Connection', 'Claim USB Transport', 'Non-homing Recovery', 'Manual Controls', 'BioXpCameraPanel', 'Emergency Stop', 'Recent Commands', 'BioXpOperatorControlTabs']) {
        assert.match(cockpit, new RegExp(label));
    }
});

test('commissioning and provenance are robot-owned receipt fields, not duplicate cockpit state', () => {
    for (const value of ['collect_hardware_snapshot', 'OEM Startup Lifecycle', 'startup_lifecycle', 'registry_sha256', 'physical_effect_verified', 'Local Jobs']) {
        assert.doesNotMatch(cockpit, new RegExp(value));
    }
    for (const value of ['source_authority_verified', 'physical_effect_verified', 'remote_acknowledged', 'Record PASS', 'Record FAIL', 'Your physical observation']) {
        assert.match(controls, new RegExp(value));
    }
});
