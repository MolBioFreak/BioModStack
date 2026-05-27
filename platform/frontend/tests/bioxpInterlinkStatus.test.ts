import * as assert from 'node:assert/strict';
import { test } from 'node:test';

import { deriveBioXpInterlinkMenuStatus, isFreshBioXpProbe } from '../src/components/bioxpInterlinkStatus.js';

const NOW = Date.parse('2026-05-23T21:00:00.000Z');
const freshProbe = '2026-05-23T20:59:30.000Z';
const staleProbe = '2026-05-23T20:58:00.000Z';

test('BioXP interlink only shows LINKED for a fresh successful robot API probe', () => {
    const status = deriveBioXpInterlinkMenuStatus({
        active: true,
        configured: true,
        reachable: true,
        lastProbeAt: freshProbe,
        nowMs: NOW,
    });

    assert.equal(status.state, 'linked');
    assert.equal(status.statusLabel, 'LINKED');
    assert.equal(status.humanStatusLabel, 'Robot API reachable');
    assert.equal(status.isRobotReachabilityProven, true);
    assert.match(status.indicatorClass, /emerald/);
});

test('BioXP interlink does not show LINKED when active but unprobed', () => {
    const status = deriveBioXpInterlinkMenuStatus({
        active: true,
        configured: true,
        reachable: null,
        lastProbeAt: null,
        nowMs: NOW,
    });

    assert.equal(status.state, 'unverified');
    assert.equal(status.statusLabel, 'UNVERIFIED');
    assert.equal(status.isRobotReachabilityProven, false);
    assert.match(status.reachabilityText, /hardware state unknown/);
    assert.notEqual(status.statusLabel, 'LINKED');
});

test('BioXP interlink shows unreachable when the robot API probe fails or times out', () => {
    const status = deriveBioXpInterlinkMenuStatus({
        active: true,
        configured: true,
        reachable: false,
        lastProbeAt: freshProbe,
        nowMs: NOW,
    });

    assert.equal(status.state, 'unreachable');
    assert.equal(status.statusLabel, 'UNREACHABLE');
    assert.equal(status.humanStatusLabel, 'Robot API unreachable');
    assert.equal(status.isRobotReachabilityProven, false);
    assert.match(status.indicatorClass, /red/);
    assert.match(status.reachabilityText, /timed out/);
});

test('BioXP interlink marks old successful probes as stale instead of linked', () => {
    const status = deriveBioXpInterlinkMenuStatus({
        active: true,
        configured: true,
        reachable: true,
        lastProbeAt: staleProbe,
        nowMs: NOW,
    });

    assert.equal(status.state, 'stale');
    assert.equal(status.statusLabel, 'STALE');
    assert.equal(status.isRobotReachabilityProven, false);
    assert.match(status.reachabilityText, /stale/);
});

test('BioXP interlink saved profile remains separate from live robot reachability', () => {
    const status = deriveBioXpInterlinkMenuStatus({
        active: false,
        configured: true,
        reachable: true,
        lastProbeAt: freshProbe,
        nowMs: NOW,
    });

    assert.equal(status.state, 'saved');
    assert.equal(status.statusLabel, 'SAVED');
    assert.equal(status.isRobotReachabilityProven, false);
});

test('BioXP probe freshness helper rejects missing, future, or old timestamps', () => {
    assert.equal(isFreshBioXpProbe({ lastProbeAt: freshProbe, nowMs: NOW }), true);
    assert.equal(isFreshBioXpProbe({ lastProbeAt: staleProbe, nowMs: NOW }), false);
    assert.equal(isFreshBioXpProbe({ lastProbeAt: null, nowMs: NOW }), false);
    assert.equal(isFreshBioXpProbe({ lastProbeAt: '2026-05-23T21:01:00.000Z', nowMs: NOW }), false);
});
