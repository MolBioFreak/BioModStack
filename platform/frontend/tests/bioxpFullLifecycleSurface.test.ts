import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('full OEM lifecycle surface is contract-gated and explicitly dry-run only', () => {
    for (const marker of [
        'Full OEM Lifecycle · Dry-run Contract',
        'Planning only · no hardware command',
        'Create persisted dry-run plan',
        'Cancel dry-run record',
        'plan_available',
        'live_creation_enabled',
        'source_authority_verified',
        'evidence_lock_sha256',
        'physical_command_sent',
        'physical_effect_verified',
        'would_command_hardware',
        'would_command_physical_motion',
    ]) {
        assert.match(cockpit, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(cockpit, /lifecycleContract\?\.plan_available === true/);
    assert.match(cockpit, /mutationAccessEnabled[\s\S]*controlPlaneFresh[\s\S]*plan_available/);
    assert.match(cockpit, /lifecycleContract\?\.plan_blockers\?\.join/);
    assert.doesNotMatch(cockpit, /full OEM parity (?:complete|achieved)/i);
});

test('browser lifecycle client exposes only typed BMS routes and never transports robot credentials', () => {
    for (const route of [
        '/api/bioxp/oem-full-lifecycle/contract',
        '/api/bioxp/oem-full-lifecycle/runs',
        '/ledger',
        '/cancel',
    ]) {
        assert.match(client, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(client, /expected_generation/);
    assert.match(client, /expected_machine_serial/);
    assert.match(client, /expected_registry_sha256/);
    assert.match(client, /expected_evidence_lock_sha256/);
    assert.doesNotMatch(client, /X-BioXP-OEM-Token|BMS_BIOXP_OEM_RUNTIME_TOKEN_FILE/);
    const planHook = client.slice(
        client.indexOf('usePlanBioXpOemFullLifecycle'),
        client.indexOf('useCancelBioXpOemFullLifecycle'),
    );
    assert.doesNotMatch(planHook, /axis\s*:|raw_frame\s*:|stage\s*:/);
});
