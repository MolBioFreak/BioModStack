import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const controls = readFileSync(resolve('src/components/BioXpOperatorControlTabs.tsx'), 'utf8');

test('full lifecycle is a robot-owned meta action, never a client-side dry-run planner', () => {
    for (const value of ['Dry-run Contract', 'usePlanBioXpOemFullLifecycle', 'useCancelBioXpOemFullLifecycle']) {
        assert.doesNotMatch(cockpit, new RegExp(value));
        assert.doesNotMatch(controls, new RegExp(value));
    }
    assert.match(cockpit, /BioXpOperatorControlTabs/);
    assert.match(controls, /Meta Actions/);
    assert.match(controls, /action\.kind === pane/);
    assert.match(controls, /selected\.stages/);
    assert.match(controls, /selected\.enabled/);
    assert.match(controls, /selected\.disabled_reason/);
});
