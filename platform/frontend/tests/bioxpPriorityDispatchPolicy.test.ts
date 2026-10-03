import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source = fs.readFileSync(new URL('../src/lib/bioxpClient.ts', import.meta.url), 'utf8');

function hookBody(name: string, nextName: string): string {
    const start = source.indexOf(`export const ${name} =`);
    const nextMarker = nextName.startsWith('export ') ? nextName : `export const ${nextName}`;
    const next = source.indexOf(nextMarker, start + 1);
    const end = next === -1 && nextName === 'EOF' ? source.length : next;
    assert.ok(start >= 0 && end > start, `${name} hook body must be present`);
    return source.slice(start, end);
}

test('operator command completion refreshes all dependent state without awaiting it', () => {
    const body = hookBody('useInvokeBioXpOperatorAction', 'useAssessBioXpOperatorAction');
    assert.doesNotMatch(body, /useRefreshMutation/);
    assert.doesNotMatch(body, /onSuccess: async/);
    assert.doesNotMatch(body, /variables\.actionId/);
    assert.match(body, /invalidateQueries\(\{ queryKey: operatorCatalogKey/);
    assert.match(body, /invalidateQueries\(\{ queryKey: operatorCatalogKey/);
});

test('operator assessment completion upserts history and refreshes admission dependencies', () => {
    const body = hookBody('useAssessBioXpOperatorAction', 'EOF');
    assert.doesNotMatch(body, /useRefreshMutation/);
    assert.doesNotMatch(body, /onSuccess: async/);
    assert.match(body, /refreshBioXpHistoryCaches\(queryClient, variables.connectionGeneration\)/);
    assert.match(body, /invalidateQueries\(\{ queryKey: operatorCatalogKey/);
});

test('operator polling budget retains bounded live catalog and cross-client history discovery', () => {
    const catalog = hookBody('useBioXpOperatorControlCatalog', 'export const BIOXP_Y_RELATIVE_MIN_STEPS');
    const admission = hookBody('useBioXpOperatorActionAdmission', 'useBioXpOperatorActionHistory');
    const history = hookBody('useBioXpOperatorActionHistory', 'useBioXpOperatorReportSummary');
    const camera = hookBody('useBioXpCameraStatus', 'export async function fetchBioXpCameraFrame');
    assert.match(catalog, /1_000 : 5_000/);
    assert.doesNotMatch(admission, /refetchInterval:/);
    assert.doesNotMatch(history, /refetchInterval/);
    assert.match(history, /useBioXpOperatorUpdates\(connectionGeneration, enabled\)/);
    assert.match(camera, /refetchInterval: enabled \? interval : false/);
});

test('generic mutation refresh no longer extends pending state', () => {
    const start = source.indexOf('const useRefreshMutation');
    const end = source.indexOf('export const useConnectBioXp', start);
    const body = source.slice(start, end);
    assert.ok(start >= 0 && end > start);
    assert.doesNotMatch(body, /onSuccess: async/);
    assert.doesNotMatch(body, /await Promise\.all/);
    assert.match(body, /void Promise\.all/);
});
