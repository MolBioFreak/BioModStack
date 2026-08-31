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

test('static BioXP metadata hooks are request-driven rather than timer-polled', () => {
    for (const [start, end] of [
        ['export const useBioXpOperatorControlCatalog', 'export const useBioXpOperatorDashboard'],
        ['export const useBioXpCameraStatus', 'export async function fetchBioXpCameraFrame'],
        ['export const useBioXpOemFullLifecycleContract', 'export const useBioXpOemFullLifecycleRun'],
    ]) {
        const source = hookSource(start, end);
        assert.doesNotMatch(source, /refetchInterval/u, `${start} must not create unsolicited polling`);
    }
    const catalog = hookSource(
        'export const useBioXpOperatorControlCatalog',
        'export const useBioXpOperatorDashboard',
    );
    assert.match(catalog, /queryKey:\s*\[\.\.\.operatorCatalogKey, connectionGeneration, enabled, lifecycleState \?\? null\]/u);
    assert.match(catalog, /enabled:\s*enabled && connectionGeneration > 0/u);
});

test('cockpit keeps bounded status and compact dashboard freshness loops', () => {
    const status = hookSource('export const useBioXpStatus', 'export const useBioXpOperatorControlCatalog');
    const dashboard = hookSource('export const useBioXpOperatorDashboard', 'export const useBioXpOperatorActionHistory');

    assert.match(status, /refetchInterval:\s*enabled\s*\?\s*10_000\s*:\s*false/u);
    assert.match(dashboard, /queryKey:\s*\[\.\.\.operatorDashboardKey, connectionGeneration, enabled\]/u);
    assert.match(dashboard, /refetchInterval:\s*enabled && connectionGeneration > 0\s*\?\s*15_000\s*:\s*false/u);
    assert.match(dashboard, /refetchIntervalInBackground:\s*false/u);
    const dashboardConsumers = `${cockpit}\n${quickDashboard}`.match(/useBioXpOperatorDashboard\(/gu) ?? [];
    assert.equal(dashboardConsumers.length, 1);
    assert.match(cockpit, /useBioXpOperatorDashboard\(generation, linkConnected\)/u);
    assert.match(cockpit, /!linkConnected \|\| dashboardQuery\.isError \? undefined/u);
    assert.match(quickDashboard, /\{connected && data && \(/u);
    assert.match(cockpit, /useBioXpOperatorActionHistory\(generation, linkConnected, historyLimit\)/u);
    assert.match(cockpit, /!linkConnected \|\| operatorCatalog\.isError \? undefined/u);
    assert.match(cockpit, /!linkConnected \|\| historyQuery\.isError \? \[\]/u);
});

test('user-triggered camera reads refresh status without a network polling timer', () => {
    assert.match(cameraPanel, /await refetchStatus\(\)/u);
    assert.match(cameraPanel, /deriveBioXpCameraPresentation/u);
    assert.match(cameraPanel, /statusReceivedAtRef/u);
    assert.match(cameraPanel, /lastSequenceAdvanceAtRef/u);
    assert.match(cameraPanel, /window\.setTimeout/u);
    assert.match(cameraPanel, /await refetchStatus\(\)[\s\S]*owner\.isCurrent\(token\)[\s\S]*setPendingAction\(null\)/u);
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

test('operator mutations fence history races and refresh every authority projection', () => {
    const invoke = hookSource('export const useInvokeBioXpOperatorAction', 'export const useAssessBioXpOperatorAction');
    const assess = hookSource('export const useAssessBioXpOperatorAction', 'export const usePlanBioXpOemFullLifecycle');
    assert.match(invoke, /invalidateQueries\(\{ queryKey: operatorCatalogKey \}\)/u);
    for (const source of [invoke, assess]) {
        assert.match(source, /cancelQueries\(\{ queryKey: operatorHistoryKey \}\)/u);
        for (const key of ['operatorDashboardKey', 'operatorHistoryKey']) {
            assert.match(source, new RegExp(`invalidateQueries\\(\\{ queryKey: ${key} \\}\\)`));
        }
    }
});
