import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

test('stale and unknown telemetry are rendered explicitly', () => {
    for (const marker of ['STALE', 'UNKNOWN', 'fresh', 'runtime_ready', 'hardware_ready', 'last_error']) {
        assert.match(cockpit, new RegExp(marker));
    }
    assert.doesNotMatch(cockpit, /reachable\s*!==\s*false/);
});

test('emergency stop is separate and never claims physical effect', () => {
    assert.match(cockpit, /available_controls.includes\('emergency_stop'\)/);
    assert.match(cockpit, /Emergency Stop Delivery/);
    assert.match(cockpit, /remote_acknowledged/);
    assert.match(cockpit, /physical_effect_verified/);
    assert.match(cockpit, /does not prove physical effect/i);
    assert.doesNotMatch(cockpit, /EMERGENCY STOP succeeded/i);
});

test('operator token is transient input rather than embedded credential', () => {
    assert.match(cockpit, /type="password"/);
    assert.match(cockpit, /token: operatorToken/);
    assert.doesNotMatch(cockpit, /X-BMS-BioXP-Operator-Token['"]\s*:/);
});
