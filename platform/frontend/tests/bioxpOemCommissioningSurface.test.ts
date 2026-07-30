import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const source = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('OEM operator surface is connection, ownership, recovery, finite controls, camera and stop', () => {
  for (const label of ['Connection', 'Claim USB Transport', 'Non-homing Recovery', 'Manual Controls', 'BioXpCameraPanel', 'Emergency Stop', 'Recent Commands']) assert.match(source, new RegExp(label));
});
test('commissioning and provenance scaffolding are absent', () => {
  for (const value of ['collect_hardware_snapshot', 'OEM Startup Lifecycle', 'startup_lifecycle', 'registry_sha256', 'physical_effect_verified', 'Local Jobs']) assert.doesNotMatch(source, new RegExp(value));
});
