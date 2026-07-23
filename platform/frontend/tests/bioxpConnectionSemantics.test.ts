import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const panel = readFileSync(resolve('src/components/BioXpInterlinkControlPanel.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const status = readFileSync(resolve('src/components/bioxpInterlinkStatus.ts'), 'utf8');

test('connection UI distinguishes configuration, activation, transport, runtime and hardware truth', () => {
    const combined = `${cockpit}\n${panel}`;
    for (const marker of ['configured', 'active', 'reachable', 'runtime_ready', 'hardware_ready', 'generation']) {
        assert.match(combined, new RegExp(marker));
    }
    assert.match(combined, /SAVED \/ DISCONNECTED/);
    assert.match(combined, /hardware state/i);
});

test('status surfaces mask the target and do not render a raw saved URL', () => {
    assert.match(cockpit, /target_url/);
    assert.doesNotMatch(cockpit, /robot_api_url/);
    assert.doesNotMatch(panel, /recommended_url|robot_ssh_host|connection_mode/);
});

test('failed refreshes and aged cached evidence fail closed in both operator surfaces', () => {
    for (const source of [cockpit, panel]) {
        assert.match(source, /statusQuery\.isError/);
        assert.match(source, /setInterval/);
    }
    assert.match(client, /freshness_budget_seconds/);
    assert.match(client, /retry: false/);
    assert.match(status, /observedMs \+ budgetMs/);
    assert.match(cockpit, /isBioXpCommandAvailable\(status\?\.available_commands, command, derived\?\.label\)/);
    assert.match(status, /return serverAdmitted/);
    assert.doesNotMatch(status, /displayState === '(?:HARDWARE NOT READY|STALE)'/);
    assert.match(cockpit, /disabled=\{!available \|\| executeCommand\.isPending\}/);
    assert.match(cockpit, /cached readiness and controls are suppressed/i);
});

test('malformed profile and backend refusal details are operator-visible', () => {
    assert.match(panel, /Invalid saved profile/);
    assert.match(panel, /profileQuery\.data\.detail/);
    assert.match(client, /response\?\.data\?\.detail/);
});
