import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const statePath = resolve('src/components/bioxpCameraState.ts');
const panelPath = resolve('src/components/BioXpCameraPanel.tsx');
const clientPath = resolve('src/lib/bioxpClient.ts');
const cockpitPath = resolve('src/components/BioXpCockpit.tsx');

test('BioXP camera state helper exists for executable lifecycle behavior', () => {
    assert.equal(existsSync(statePath), true, 'camera state helper must exist');
});

test('camera health keeps LIVE, STALE, frozen, and UNAVAILABLE independent from robot commands', async () => {
    const module = await import('../src/components/bioxpCameraState.js') as Record<string, unknown>;
    const derive = module.deriveBioXpCameraPresentation as ((input: {
        status: Record<string, unknown> | null;
        statusReceivedAtMs: number;
        lastSequenceAdvanceAtMs: number;
        nowMs: number;
        error: string | null;
    }) => { label: string; detail: string; effectiveFrameAgeSeconds: number | null });
    assert.equal(typeof derive, 'function');
    const liveStatus = {
        state: 'live', available: true, frame_sequence: 9, frame_age_seconds: 0.25,
        freshness_budget_seconds: 2, provider_generation: 4,
    };
    assert.equal(derive({ status: liveStatus, statusReceivedAtMs: 1_000, lastSequenceAdvanceAtMs: 1_000, nowMs: 1_500, error: null }).label, 'LIVE');
    assert.equal(derive({ status: { ...liveStatus, state: 'stale' }, statusReceivedAtMs: 1_000, lastSequenceAdvanceAtMs: 1_000, nowMs: 1_500, error: null }).label, 'STALE');
    const frozen = derive({ status: liveStatus, statusReceivedAtMs: 3_000, lastSequenceAdvanceAtMs: 0, nowMs: 3_000, error: null });
    assert.equal(frozen.label, 'STALE');
    assert.match(frozen.detail, /sequence has not advanced/i);
    const errored = derive({ status: liveStatus, statusReceivedAtMs: 1_000, lastSequenceAdvanceAtMs: 1_000, nowMs: 1_500, error: 'proxy offline' });
    assert.equal(errored.label, 'UNAVAILABLE');
    assert.match(errored.detail, /proxy offline/);
});

test('camera object URL owner cleans replacement, stale completion, and unmount exactly once', async () => {
    const module = await import('../src/components/bioxpCameraState.js') as Record<string, unknown>;
    const createOwner = module.createCameraObjectUrlOwner as ((operations: {
        create: (blob: Blob) => string;
        revoke: (url: string) => void;
    }) => {
        begin(): number;
        adopt(token: number, blob: Blob): string | null;
        clear(): void;
        dispose(): void;
        isCurrent(token: number): boolean;
    });
    const revoked: string[] = [];
    let created = 0;
    const owner = createOwner({
        create: () => `blob:camera-${++created}`,
        revoke: (url) => revoked.push(url),
    });
    const older = owner.begin();
    const newer = owner.begin();
    assert.equal(owner.adopt(newer, new Blob(['newer'])), 'blob:camera-1');
    assert.equal(owner.adopt(older, new Blob(['older'])), null);
    assert.deepEqual(revoked, ['blob:camera-2']);

    const replacement = owner.begin();
    assert.equal(owner.adopt(replacement, new Blob(['replacement'])), 'blob:camera-3');
    assert.deepEqual(revoked, ['blob:camera-2', 'blob:camera-1']);
    owner.dispose();
    owner.dispose();
    assert.deepEqual(revoked, ['blob:camera-2', 'blob:camera-1', 'blob:camera-3']);

    assert.equal(owner.adopt(replacement, new Blob(['after-dispose'])), null);
    assert.deepEqual(revoked, ['blob:camera-2', 'blob:camera-1', 'blob:camera-3', 'blob:camera-4']);
});

test('camera browser client exposes only the finite same-origin BMS allowlist', async () => {
    const module = await import('../src/lib/bioxpClient.js') as Record<string, unknown>;
    assert.deepEqual(module.BIOXP_CAMERA_ENDPOINTS, {
        status: '/api/bioxp/camera/status',
        latest: '/api/bioxp/camera/frame/latest',
        snapshot: '/api/bioxp/camera/snapshot',
    });
    const panel = readFileSync(panelPath, 'utf8');
    assert.doesNotMatch(panel, /https?:\/\//i);
    assert.doesNotMatch(panel, /target_url|robot_url|\/camera\/stream|oem\/check|calibration|inspect/i);
});

test('camera panel has explicit safe refresh/snapshot actions and no motion-capable controls', () => {
    assert.equal(existsSync(panelPath), true, 'camera panel must exist');
    const panel = readFileSync(panelPath, 'utf8');
    assert.match(panel, /Refresh frame/);
    assert.match(panel, /Capture snapshot/);
    assert.match(panel, /fetchBioXpCameraFrame\(connectionGeneration\)/);
    assert.match(panel, /captureBioXpCameraSnapshot\(connectionGeneration\)/);
    assert.doesNotMatch(panel, /Run camera check|Calibrat|Deck inspect|Move camera/i);
});

test('cockpit integrates camera truth separately from command state', () => {
    const cockpit = readFileSync(cockpitPath, 'utf8');
    assert.match(cockpit, /BioXpCameraPanel/);
    assert.match(cockpit, /connectionGeneration=\{connection\?\.generation \?\? null\}/);
    assert.match(cockpit, /connected=\{connection\?\.active === true\}/);

    const client = readFileSync(clientPath, 'utf8');
    assert.match(client, /expected_generation: connectionGeneration/);
    assert.match(client, /responseType: 'blob'/);
});
