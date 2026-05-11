import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const clientSource = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const cockpitSource = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const panelPath = resolve('src/components/BioXpInterlinkControlPanel.tsx');
const panelSource = existsSync(panelPath) ? readFileSync(panelPath, 'utf8') : '';

test('BIOXP LINK topbar menu exists and matches existing utility-menu pattern', () => {
    assert.ok(existsSync(panelPath), 'BioXpInterlinkControlPanel.tsx must exist');
    assert.match(layoutSource, /BioXpInterlinkMenu/);
    assert.match(layoutSource, /<BioXpInterlinkMenu \/>/);
    assert.match(panelSource, /data-bms-bioxp-interlink-menu="true"/);
    assert.match(panelSource, /BIOXP LINK/);
    assert.match(panelSource, /BioXP robot interlink/);
});

test('interlink panel exposes governed connection, diagnostics, and honest log/lifecycle status', () => {
    for (const marker of [
        'Saved profile is inactive',
        'Connect',
        'Disconnect',
        'Forget saved profile',
        'Diagnostics',
        'Fetch robot logs',
        'Robot service logs',
        'Fetches the last 120 robot-local API service lines',
        'Lifecycle actions unavailable here',
        'reset/reboot controls are intentionally not shown',
        'never homes, arms, recovers motion, or moves axes',
    ]) {
        assert.match(panelSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    for (const removed of [
        'optional sudo password',
        'Reset robot local runtime',
        'Restart robot OS',
        'RESET BIOXP RUNTIME',
        'REBOOT ROBOT',
        'Robot service log lines',
    ]) {
        assert.doesNotMatch(panelSource, new RegExp(removed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('frontend client exposes interlink hooks and only the BMS proxy route family', () => {
    for (const marker of [
        'export interface BioXpInterlinkState',
        'export interface BioXpInterlinkSettings',
        'export interface BioXpInterlinkActionRequest',
        'export const useBioXpInterlinkState',
        'export const useSaveBioXpInterlinkSettings',
        'export const useForgetBioXpInterlinkSettings',
        'export const useBioXpInterlinkConnect',
        'export const useBioXpInterlinkDisconnect',
        'export const useBioXpInterlinkDiagnostics',
        'export const useBioXpRuntimeReset',
        'export const useBioXpRobotReboot',
        'export const useBioXpInterlinkLogs',
        '/api/bioxp/interlink/state',
        '/api/bioxp/interlink/settings',
        '/api/bioxp/interlink/connect',
        '/api/bioxp/interlink/disconnect',
        '/api/bioxp/interlink/diagnostics',
        '/api/bioxp/interlink/runtime-reset',
        '/api/bioxp/interlink/robot-reboot',
        '/api/bioxp/interlink/logs',
    ]) {
        assert.match(clientSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.doesNotMatch(panelSource, /8123\/status/);
});

test('BioXP cockpit is gated by active interlink state and does not own reset lifecycle', () => {
    assert.match(cockpitSource, /useBioXpInterlinkState/);
    assert.match(cockpitSource, /Connect from BIOXP LINK first/);
    assert.match(cockpitSource, /interlinkActive/);
    assert.doesNotMatch(cockpitSource, /useMotionHardReset/);
    assert.doesNotMatch(cockpitSource, /motionHardReset/);
    assert.doesNotMatch(cockpitSource, /Reconnect USB Runtime/);
    assert.doesNotMatch(cockpitSource, />\s*Hard Reset\s*</);
    assert.match(cockpitSource, /Reset thermal controller profile/);
    assert.match(cockpitSource, /Reset chiller profile/);
});

test('runtime reset and robot reboot are not exposed as cockpit actions', () => {
    assert.doesNotMatch(panelSource, /Reset robot local runtime/);
    assert.doesNotMatch(panelSource, /Restart robot OS/);
    assert.match(panelSource, /Lifecycle actions unavailable here/);
    assert.doesNotMatch(cockpitSource, /Reset robot local runtime/);
    assert.doesNotMatch(cockpitSource, /Restart robot OS/);
});
