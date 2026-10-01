import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const cameraPanel = readFileSync(resolve('src/components/BioXpCameraPanel.tsx'), 'utf8');
const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const quickDashboard = readFileSync(resolve('src/components/BioXpQuickDashboard.tsx'), 'utf8');

const hookSource = (start: string, end: string): string => {
    const from = client.indexOf(start);
    const to = client.indexOf(end, from + start.length);
    assert.ok(from >= 0, `${start} hook marker is missing`);
    assert.ok(to > from, `${end} hook boundary is missing`);
    return client.slice(from, to);
};

test('one catalog owns live enablement and embedded dashboards; camera status is request-driven', () => {
    const catalog = hookSource('export const useBioXpOperatorControlCatalog', 'export const BIOXP_Y_RELATIVE_MIN_STEPS');
    assert.match(catalog, /operatorCatalogKey, connectionGeneration, enabled, lifecycleState \?\? null, zTargetSteps/);
    assert.match(catalog, /cached_projection_stale/);
    assert.match(catalog, /1_000 : 5_000/);
    assert.match(catalog, /signal, timeout: 12_000/);
    assert.match(catalog, /refetchIntervalInBackground: false/);
    assert.doesNotMatch(client, /export const useBioXpOperatorDashboard|export const useBioXpOperatorControlCatalogV2/);
    assert.match(hookSource('export const useBioXpCameraStatus', 'export const useBioXpCameraStreamState'), /refetchInterval: enabled \? 2_000 : false/);
});

test('cockpit keeps one bounded catalog loop and uses age only as presentation', () => {
    assert.match(hookSource('export const useBioXpStatus', 'export const useBioXpOperatorControlCatalog'), /refetchInterval: enabled \? 10_000 : false/);
    assert.equal((cockpit.match(/useBioXpOperatorControlCatalog\(/g) ?? []).length, 1);
    assert.match(cockpit, /data: operatorCatalog.data\?\.canonical/);
    assert.match(cockpit, /const currentDashboardV2 = currentCatalogV2\?\.dashboard/);
    assert.match(cockpit, /const displayTelemetry = displayDashboardV2\?\.telemetry/);
    assert.match(cockpit, /localAgeMs >= 15_000 \|\| upstreamAgeMs >= 15_000/);
    assert.doesNotMatch(cockpit, /setInterval/);
    assert.match(quickDashboard, /Last-known observation/);
    assert.match(cockpit, /useBioXpOperatorActionHistory\(generation, linkConnected, historyLimit, historyPagination.cursor\)/);
    assert.match(cockpit, /!displayConnected \? \[\]/);
});

test('user-triggered camera reads refresh status without a network polling timer', () => {
    assert.match(cameraPanel, /await refetchStatus\(\)/u);
    assert.match(cameraPanel, /deriveBioXpCameraPresentation/u);
    assert.match(cameraPanel, /statusReceivedAtRef/u);
    assert.match(cameraPanel, /lastSequenceAdvanceAtRef/u);
    assert.match(cameraPanel, /window\.setTimeout/u);
    assert.match(cameraPanel, /const isCurrent = \(\) => sessionRef\.current === session && owner\.isCurrent\(token\) && mountedRef\.current/u);
    assert.match(cameraPanel, /await refetchStatus\(\)[\s\S]*if \(isCurrent\(\)\)[\s\S]*setPendingAction\(null\)/u);
    assert.doesNotMatch(cameraPanel, /setInterval|refetchInterval/u);
});

test('operator receipt type strictly exposes startup reconciliation and durable timing state', () => {
    assert.match(client, /status:.*'reconciliation_required'/u);
    for (const field of [
        'idempotency_replay_enabled', 'request_received_at', 'lock_acquired_at',
        'admission_completed_at', 'provider_entry_at', 'provider_returned_at',
        'receipt_persist_started_at', 'controller_terminal_state_verified',
        'automatic_retry', 'physical_outcome', 'persistence_fallback',
        'authority_receipt_id', 'authority_receipt_status', 'authority_fingerprint', 'observation_receipt_id',
        'observes_command_id',
    ]) {
        assert.match(client, new RegExp(`\\n\\s*${field}:`));
        assert.doesNotMatch(client, new RegExp(`\\n\\s*${field}\\?:`));
    }
});

test('operator mutations invalidate canonical history and the shared authority projection', () => {
    const invoke = hookSource('export const useInvokeBioXpOperatorAction =', 'export const useAssessBioXpOperatorAction');
    const assess = client.slice(client.indexOf('export const useAssessBioXpOperatorAction'));
    for (const source of [invoke, assess]) {
        assert.match(source, /cancelQueries\(\{ queryKey: operatorHistoryKey \}\)/);
        assert.match(source, /invalidateQueries\(\{ queryKey: operatorCatalogKey \}\)/);
        assert.match(source, /refreshBioXpHistoryCaches\(queryClient, variables.connectionGeneration\)/);
        assert.doesNotMatch(source, /updateBioXpHistoryCaches/);
    }
});
