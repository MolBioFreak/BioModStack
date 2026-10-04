import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import deckCatalog from '../fixtures/bioxp_deck_admission_catalog.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';
import retainedFailure from '../fixtures/bioxp_retained_deck_failure_followup.json';
import retainedWasteFailure from '../fixtures/bioxp_retained_waste_failure_followup.json';
import type { BioXpOperatorUpdates } from '../../src/lib/bioxpClient';
const connection = vi.hoisted(() => ({ generation: 7, active: true, reachable: true }));
vi.mock('../../src/lib/bioxpClient', async original => ({
    ...await original<typeof import('../../src/lib/bioxpClient')>(),
    useBioXpStatus: () => ({ data: { connection: { active: connection.active, configured: true, generation: connection.generation, reachable: connection.reachable, runtime_ready: true }, mutation_access: { enabled: true } }, isError: false }),
}));
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
let root: Root, host: HTMLDivElement, client: QueryClient;
let catalog: any, rows: Map<string, any>, wire: BioXpOperatorUpdates;
let send: ((value: any) => void) | undefined;
let postBodies: any[];
const tick = async (ms = 1) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const mount = async () => { await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit initialTab="live-deck" /></QueryClientProvider>)); await tick(); };
async function publish(ids: string[] = [], x = 51000, y = 16000) {
    wire = { ...wire, next_after_sequence: wire.next_after_sequence + 1, pose_sequence: wire.pose_sequence + 1,
        changed_command_ids: ids, pose: { ownership_generation: wire.ownership_generation, axes: [
            { axis: 'x', position_steps: x, observed_at: wire.pose_sequence + 10 }, { axis: 'y', position_steps: y, observed_at: wire.pose_sequence + 10 },
        ] } };
    await act(async () => { expect(send).toBeDefined(); send!({ data: structuredClone(wire) }); }); await tick();
}
const compact = () => vi.mocked(api.get).mock.calls.filter(([u, c]) => u.includes('/receipts/') && c?.params?.detail === false);
const detail = () => vi.mocked(api.get).mock.calls.filter(([u, c]) => u.includes('/receipts/') && c?.params?.detail === true);
const catalogueReads = () => vi.mocked(api.get).mock.calls.filter(([u]) => u.endsWith('/catalog')).length;
const click = async (el: Element) => { await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))); await tick(); };
beforeEach(() => {
    vi.useFakeTimers(); connection.generation = 7; connection.active = true; connection.reachable = true; postBodies = []; rows = new Map(); send = undefined;
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    catalog = structuredClone(metadata.catalog); catalog.dashboard.generated_at = 0;
    catalog.dashboard.deck = structuredClone(deckCatalog.deck); catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.actions = catalog.actions.filter((a: any) => !a.action_id.startsWith('oem.deck.move_to_')).concat(structuredClone(deckCatalog.action), {
        ...structuredClone(deckCatalog.action), action_id: 'oem.deck.move_to_well', enabled: true, disabled_reason: null,
    });
    wire = { schema_version: 'bioxp.operator_updates.v1', source_instance_id: 'fixture-source', ownership_generation: catalog.ownership_generation ?? catalog.dashboard.ownership_generation,
        next_after_sequence: 1, pose_sequence: 0, changed_command_ids: [], active_command_ids: [], has_more: false, reset: false, pose: null };
    vi.spyOn(api, 'get').mockImplementation(async (url, options) => {
        if (url === '/api/bioxp/service') return { data: { available: true, detail: null, unit: 'bioxp-api.service', restart_in_progress: false } };
        if (url.endsWith('/updates')) {
            if (options?.params?.after_sequence === undefined) return { data: structuredClone(wire) };
            return new Promise((resolve, reject) => {
                const finish = (value: any) => { options?.signal?.removeEventListener?.('abort', abort); resolve(value); };
                const abort = () => { if (send === finish) send = undefined; reject(new Error('aborted')); };
                send = finish; options?.signal?.addEventListener?.('abort', abort);
            });
        }
        if (url.endsWith('/catalog')) return { data: catalogWireFixture({ actions: [], dashboard: { pipettes: { channels: [] }, snapshot: { freshness: null } }, canonical: structuredClone(catalog) }, options?.params?.view) };
        if (url.includes('/requests/')) { const value = rows.get('lookup'); if (!value) throw { response: { status: 404 } }; return { data: value }; }
        if (url.includes('/receipts/')) return { data: rows.get(url.split('/').at(-1)!) };
        if (url.includes('/history')) return { data: { items: [...rows.values()], next_cursor: null } };
        if (url.endsWith('/calibration-settings')) return { data: { active_motion_positions: [], saved_motion_positions: [] } };
        if (url.includes('/camera/')) return { data: { active: false, available: false, state: 'unavailable' } };
        throw new Error(`Unmatched offline read ${url}`);
    });
    vi.spyOn(api, 'post').mockImplementation(async (url, body: any) => {
        if (url === '/api/bioxp/service/restart') { postBodies.push(body); return { data: { restarted: true, unit: 'bioxp-api.service', active_state: 'active', sub_state: 'running', invocation_id: 'fixture-restart', pid: 123 } }; }
        postBodies.push(body); const command_id = `intent-${postBodies.length}`;
        const row = { ...park, command_id, action_id: url.split('/').at(-1), status: 'dispatched', terminal: false, terminal_receipt_id: null, completion_class: null };
        rows.set(command_id, row); return { data: row };
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('service restart remains a single-click action while the native API is unreachable and the BMS link is disconnected', async () => {
    connection.active = false; connection.reachable = false;
    await mount();
    const panel = host.querySelector('[aria-label="Robot service restart"]')!;
    expect(panel.closest('[hidden]')).toBeNull();
    const button = panel.querySelector('button')!;
    expect(button.disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
    await click(button);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/api/bioxp/service/restart', {}, { timeout: 75000 });
    expect(panel.textContent).toContain('Robot service restarted');
    expect(panel.textContent).toContain('does not home motors or reset coordinates');
    await tick(60000);
    expect(api.post).toHaveBeenCalledTimes(1);
});

it('service restart reports an inactive unit honestly instead of claiming that the API recovered', async () => {
    await mount();
    vi.mocked(api.post).mockResolvedValueOnce({ data: { restarted: true, unit: 'bioxp-api.service', active_state: 'failed', sub_state: 'failed', invocation_id: '', pid: 0 } });
    const panel = host.querySelector('[aria-label="Robot service restart"]')!;
    await click(panel.querySelector('button')!);
    expect(panel.querySelector('[role="status"]')?.textContent).toContain('robot service reports failed / failed');
    expect(panel.textContent).not.toContain('Robot service restarted.');
    expect(api.post).toHaveBeenCalledTimes(1);
});

it('service restart is visible on all four tabs and never submits on navigation', async () => {
    await mount();
    for (const tab of ['robot', 'pipettes', 'workflows', 'live-deck']) {
        await click(host.querySelector(`#control-tab-${tab}`)!);
        expect(host.querySelector('[aria-label="Robot service restart"]')!.closest('[hidden]')).toBeNull();
    }
    expect(api.post).not.toHaveBeenCalled();
});

it('service restart suppresses a rapid second click and never retries an uncertain result', async () => {
    await mount();
    let reject!: (reason: unknown) => void;
    vi.mocked(api.post).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    const panel = host.querySelector('[aria-label="Robot service restart"]')!;
    const button = panel.querySelector('button')!;
    await act(async () => {
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(button.disabled).toBe(true);
    await act(async () => reject(new Error('Restart outcome unknown')));
    await tick(60000);
    expect(panel.querySelector('[role="alert"]')?.textContent).toContain('No automatic retry was made');
    expect(panel.textContent).not.toContain('Robot service restarted');
    expect(api.post).toHaveBeenCalledTimes(1);
});

it('named and well gestures keep exact FIFO inputs; actual updates move the map independently of stale catalog and chosen destination', async () => {
    await mount();
    await publish(); const first = host.querySelector('[data-gun-module]')!.getAttribute('transform');
    const select = [...host.querySelectorAll('label')].find(e => e.textContent?.startsWith('Robot destination'))!.querySelector('select')!;
    await act(async () => { select.value = 'LOC_TC'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(postBodies).toHaveLength(0); expect(host.querySelector('[data-gun-module]')!.getAttribute('transform')).toBe(first);
    await click([...host.querySelectorAll('button')].find(e => e.textContent === 'Move to destination')!);
    expect(postBodies[0].inputs).toEqual({ target: 'LOC_TC', camera_offset: false });
    await click(host.querySelector('[data-station="LOC_MS"][data-well="B2"]')!);
    expect(postBodies[1].inputs).toEqual({ location_id: 0, well: 'B2', position_flag: 1 });
    expect(new Set(postBodies.map(b => b.idempotency_key)).size).toBe(2);
    const cold = catalogueReads();
    await publish(['intent-1', 'intent-2'], 62000, 22000);
    expect(host.querySelector('[data-gun-module]')!.getAttribute('transform')).not.toBe(first);
    expect(select.value).toBe('LOC_TC'); expect(host.querySelector('[data-selected-station]')?.getAttribute('data-selected-station')).toBe('LOC_TC');
    expect(catalogueReads()).toBe(cold); expect(postBodies).toHaveLength(2); expect(detail()).toHaveLength(0);
    expect(host.querySelectorAll('[data-gun-module]')).toHaveLength(4);
});
it('failed/ambiguous rows stay visible without periodic reads; selected recovery and Stop update only on notifications', async () => {
    await mount();
    await click(host.querySelector('[data-move-to="LOC_TC"]')!);
    rows.set('intent-1', { ...rows.get('intent-1'), terminal: true, status: 'ambiguous', completion_class: 'recovery_required' });
    await publish(['intent-1']);
    expect(host.querySelector('[data-request-key]')?.textContent).toContain('ambiguous');
    const reads = compact().length; await tick(60000); expect(compact()).toHaveLength(reads);
    expect(detail()).toHaveLength(0); expect(postBodies).toHaveLength(1);
    const disclosure = [...host.querySelectorAll('details')].find(d => d.querySelector(':scope > summary')?.textContent === 'Command details')!;
    await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); }); await tick();
    expect(detail()).toHaveLength(1);
    rows.set('intent-1', { ...rows.get('intent-1'), deck_movement: { recovery_resolution: { command_id: 'intent-1', decision_id: 'decision', semantic_state_revision: 1, transition_sequence: 9 } } });
    await publish(['intent-1']); expect(detail()).toHaveLength(2);
    expect(host.textContent).toContain('Earlier move reconciled');
    const stop = [...host.querySelectorAll('button')].find(b => b.textContent === 'Stop X')!;
    expect(stop.disabled).toBe(false); await click(stop);
    expect(vi.mocked(api.post).mock.calls.at(-1)?.[0]).toContain('/interrupts/oem.x.stop');
    rows.set('intent-2', { ...rows.get('intent-2'), terminal: true, status: 'failed' });
    await publish(['intent-2']); const stoppedReads = compact().length;
    await tick(60000); expect(compact()).toHaveLength(stoppedReads); expect(postBodies).toHaveLength(2);
});
it('uncertain admission reads its original key once and wakes on commit without resubmitting', async () => {
    await mount();
    vi.mocked(api.post).mockRejectedValueOnce(new Error('transport timeout'));
    await click(host.querySelector('[data-move-to="LOC_TC"]')!);
    const post = vi.mocked(api.post).mock.calls[0];
    const requestReads = () => vi.mocked(api.get).mock.calls.filter(([u]) => u.includes('/requests/'));
    expect(requestReads()).toHaveLength(1);
    expect(requestReads()[0][0]).toContain((post[1] as any).idempotency_key);
    await tick(60000); expect(requestReads()).toHaveLength(1); expect(api.post).toHaveBeenCalledTimes(1);
    const recovered = { ...park, command_id: 'recovered-original', action_id: 'oem.deck.move_to_location', status: 'ambiguous', terminal: true };
    rows.set('lookup', recovered); rows.set(recovered.command_id, recovered);
    await publish([recovered.command_id]);
    expect(requestReads()).toHaveLength(2); expect(api.post).toHaveBeenCalledTimes(1);
    expect(host.textContent).not.toContain('admission uncertain /');
    expect(host.textContent).toContain('recovered-original');
});

it.each([retainedFailure, retainedWasteFailure])('retained physical failure $command_id is explained without blocking fresh movement or retrying history', async (retainedFailure) => {
    catalog.dashboard.latest_receipts = [retainedFailure];
    rows.set(retainedFailure.command_id, structuredClone(retainedFailure));
    await mount();
    expect(detail()).toHaveLength(0);
    await click([...host.querySelectorAll('button')].find(b => b.textContent === 'Explain this move')!);
    const evidence = host.querySelector('[aria-label="Movement failure evidence"]');
    expect(evidence?.textContent).toContain('Z');
    expect(evidence?.textContent).toContain('500');
    expect(evidence?.textContent).toContain('14336');
    expect(evidence?.textContent).toContain('No target-reached event');
    expect(evidence?.textContent).toContain('100');
    expect(evidence?.textContent).toContain('not establish arrival');
    expect(detail()).toHaveLength(1);
    expect(postBodies).toHaveLength(0);
    expect([...host.querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!.disabled).toBe(false);
});

it('explicit Z switch-search recovery home is separate from ordinary Home and sends only the native empty-input operation', async () => {
    // Exact action descriptor captured in live-bms-initial.json; no invented native inputs.
    catalog.actions.push({ action_id: 'oem.z.diagnostic_home_axis', request_schema_version: 'bioxp.operator_action_request.v2', response_schema_version: 'bioxp.operator_action_receipt.v2', interrupt: false, enabled: true, disabled_reason: null });
    await mount();
    await click(host.querySelector('#control-tab-robot')!);
    const button = [...host.querySelectorAll('button')].find(b => b.textContent === 'Z switch-search recovery home')!;
    expect(button).toBeDefined();
    expect(button.disabled).toBe(false);
    await click(button);
    expect(postBodies).toHaveLength(1);
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/bioxp/operator-controls/v2/actions/oem.z.diagnostic_home_axis');
    expect(postBodies[0].inputs).toEqual({});
    expect(host.textContent).toContain('without the ordinary Home preposition');
});

it('recovery controls are one shared presentation across operational tabs and prepared files live only in Workflows', async () => {
    await mount();
    const recovery = host.querySelector('[aria-label="Controller preparation and recovery"]')!;
    expect(recovery).not.toBeNull();
    expect(recovery.closest('[hidden]')).toBeNull();
    expect([...host.querySelectorAll('summary')].some(e => e.textContent === 'Prepared workflows')).toBe(false);
    await click(host.querySelector('#control-tab-robot')!);
    expect(host.querySelector('[aria-label="Controller preparation and recovery"]')).toBe(recovery);
    await click(host.querySelector('#control-tab-pipettes')!);
    expect(recovery.closest('[hidden]')).toBeNull();
    await click(host.querySelector('#control-tab-workflows')!);
    expect(host.querySelector('#control-panel-workflows')?.textContent).toContain('Prepared request files');
    expect(postBodies).toHaveLength(0);
});

it('retains one real camera panel and its controls over subtab changes and hides the feed for a hidden document', async () => {
    await mount();
    const capture = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Capture'))!;
    expect(capture).toBeDefined();
    const tabs = [...host.querySelectorAll('[role="tab"]')];
    await click(tabs.find(t => t.textContent === 'Robot controls')!); await click(tabs.find(t => t.textContent === 'Live deck movement')!);
    expect([...host.querySelectorAll('button')].filter(b => b.textContent?.includes('Capture'))).toEqual([capture]);
    const reads = vi.mocked(api.get).mock.calls.length;
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    await tick(60000); expect(vi.mocked(api.get).mock.calls.length).toBe(reads);
    expect(postBodies).toHaveLength(0);
});
