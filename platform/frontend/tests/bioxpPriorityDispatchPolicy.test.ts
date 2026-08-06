import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source = fs.readFileSync(new URL('../src/lib/bioxpClient.ts', import.meta.url), 'utf8');

function hookBody(name: string, nextName: string): string {
    const start = source.indexOf(`export const ${name}`);
    const nextMarker = nextName.startsWith('export ') ? nextName : `export const ${nextName}`;
    const end = source.indexOf(nextMarker, start + 1);
    assert.ok(start >= 0 && end > start, `${name} hook body must be present`);
    return source.slice(start, end);
}

test('operator command completion refreshes only Z state without awaiting it', () => {
    const body = hookBody('useInvokeBioXpOperatorAction', 'useAssessBioXpOperatorAction');
    assert.doesNotMatch(body, /useRefreshMutation/);
    assert.doesNotMatch(body, /onSuccess: async/);
    assert.match(body, /variables\.actionId === 'meta\.activate_motion'/);
    assert.match(body, /variables\.actionId\.startsWith\('oem\.z\.'\)/);
    assert.match(body, /invalidateQueries\(\{ queryKey: operatorCatalogKey/);
    assert.match(body, /invalidateQueries\(\{ queryKey: operatorDashboardKey/);
    assert.match(body, /setQueryData<BioXpOperatorActionHistory>/);
    assert.match(body, /queryKey: statusKey/);
});

test('operator telemetry stays live without high-frequency robot polling', () => {
    const catalog = hookBody('useBioXpOperatorControlCatalog', 'useBioXpOperatorDashboard');
    const dashboard = hookBody('useBioXpOperatorDashboard', 'useBioXpOperatorActionAdmission');
    const admission = hookBody('useBioXpOperatorActionAdmission', 'useBioXpOperatorActionHistory');
    const history = hookBody('useBioXpOperatorActionHistory', 'useBioXpCameraStatus');
    const camera = hookBody('useBioXpCameraStatus', 'export async function fetchBioXpCameraFrame');

    assert.match(catalog, /refetchInterval: enabled \? 15_000 : false/);
    assert.match(dashboard, /refetchInterval: enabled \? 15_000 : false/);
    assert.doesNotMatch(admission, /refetchInterval:/);
    assert.doesNotMatch(history, /refetchInterval:/);
    assert.match(camera, /refetchInterval: enabled \? 5_000 : false/);
});

test('generic mutation refresh no longer extends pending state', () => {
    const start = source.indexOf('const useRefreshMutation');
    const end = source.indexOf('export const useSaveBioXpProfile', start);
    const body = source.slice(start, end);
    assert.ok(start >= 0 && end > start);
    assert.doesNotMatch(body, /onSuccess: async/);
    assert.doesNotMatch(body, /await Promise\.all/);
    assert.match(body, /void Promise\.all/);
});
