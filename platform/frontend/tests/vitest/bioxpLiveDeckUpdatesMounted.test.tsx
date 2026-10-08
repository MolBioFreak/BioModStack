import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { useBioXpOperatorUpdates, useBioXpOperatorReceiptV2, useInvokeBioXpDeckActionV2, useBioXpOperatorReceiptDetailV2, type BioXpOperatorUpdates } from '../../src/lib/bioxpClient';
import { BioXpLiveDeck, type BioXpLiveDeckProps } from '../../src/components/BioXpLiveDeck';
import { gunCenters } from '../../src/lib/bioxpLiveDeck';
import destinations from '../fixtures/bioxp_deck_admission_catalog.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';

// Synthetic transport only: actual hooks, QueryClient, map and SVG stay mounted.
let root: Root, host: HTMLDivElement, client: QueryClient;
let wire: BioXpOperatorUpdates;
let pending: Array<{ send: (data: BioXpOperatorUpdates) => void; generation: number }>;
let requests: Array<{ url: string; detail?: boolean; method: string; generation?: number }>;
let receipts: Map<string, any>;
let oldAdapter: typeof api.defaults.adapter;
let maxWaits: number;
let failReceipt: boolean;
const tick = async (ms = 1) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
async function render(children: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)); await tick(); }
async function publish(next: Partial<BioXpOperatorUpdates>) {
    wire = { ...wire, ...next };
    await act(async () => { expect(pending).toHaveLength(1); pending[0].send(structuredClone(wire)); });
    await tick();
}
const compactReads = () => requests.filter(r => r.url.includes('/receipts/') && !r.detail);
const detailReads = () => requests.filter(r => r.detail);
function Receipt({ id, detail = false, generation = 7 }: { id: string; detail?: boolean; generation?: number }) {
    const q = useBioXpOperatorReceiptDetailV2(id, generation, true, true, detail);
    return <p data-receipt={id}>{q.data?.status} {q.data?.deck_movement?.recovery_resolution?.decision_id}</p>;
}
function Fleet({ count = 40, generation = 7 }: { count?: number; generation?: number }) {
    useBioXpOperatorUpdates(generation, true);
    return <>{Array.from({ length: count }, (_, i) => <Receipt key={i} id={`c${i}`} generation={generation} />)}</>;
}
beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    wire = { schema_version: 'bioxp.operator_updates.v1', source_instance_id: 'source-a', ownership_generation: 4,
        next_after_sequence: 10, pose_sequence: 0, changed_command_ids: [], active_command_ids: [], has_more: false, reset: false, pose: null };
    pending = []; requests = []; receipts = new Map(); maxWaits = 0; failReceipt = false;
    oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const url = config.url!;
        requests.push({ url, detail: config.params?.detail, method: config.method!, generation: config.params?.expected_connection_generation });
        const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
        if (config.method !== 'get') throw new Error(`Observation caused mutation: ${url}`);
        if (url.endsWith('/updates')) {
            expect(config.timeout).toBeGreaterThan(config.params.wait_s * 1000);
            if (config.params.after_sequence === undefined) return response(structuredClone(wire));
            return new Promise((resolve, reject) => {
                let timer: ReturnType<typeof setTimeout>;
                const cleanup = () => { clearTimeout(timer); pending = pending.filter(p => p !== waiter); config.signal?.removeEventListener?.('abort', abort); };
                const waiter = { generation: config.params.expected_connection_generation, send: (data: BioXpOperatorUpdates) => { cleanup(); resolve(response(data)); } };
                const abort = () => { cleanup(); reject(new Error('cancelled')); };
                pending.push(waiter); maxWaits = Math.max(maxWaits, pending.length);
                timer = setTimeout(() => waiter.send(structuredClone(wire)), 25000);
                config.signal?.addEventListener?.('abort', abort);
            });
        }
        if (url.includes('/receipts/')) {
            if (failReceipt) throw new Error('offline receipt');
            const id = url.split('/').at(-1)!;
            if (receipts.get(id) instanceof Error) throw receipts.get(id);
            return response(await receipts.get(id) ?? { ...park, command_id: id, terminal: false, status: 'dispatched', terminal_receipt_id: null });
        }
        if (url.endsWith('/calibration-settings')) return response({ active_motion_positions: [], saved_motion_positions: [] });
        throw new Error(`Unexpected fixture route ${url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.useRealTimers(); });

it('one shared wait for forty pending and terminal commands; zero repeating receipt or catalog reads over a minute', async () => {
    for (let i = 20; i < 40; i++) receipts.set(`c${i}`, { ...park, command_id: `c${i}`, terminal: true, status: i % 2 ? 'failed' : 'ambiguous' });
    await render(<Fleet />);
    expect(compactReads()).toHaveLength(40); expect(detailReads()).toHaveLength(0);
    await tick(60000);
    expect(compactReads()).toHaveLength(40); expect(requests.filter(r => r.url.endsWith('/updates'))).toHaveLength(4);
    expect(maxWaits).toBe(1); expect(pending).toHaveLength(1);
    expect(requests.every(r => r.method === 'get')).toBe(true);
    expect(requests.some(r => /catalog|status/.test(r.url))).toBe(false);
    await publish({ next_after_sequence: 11, changed_command_ids: ['c1', 'c1', 'c30', 'not-observed'] });
    expect(compactReads()).toHaveLength(42);
    await publish({}); // same cursor, no spurious reads even when IDs repeat
    expect(compactReads()).toHaveLength(42);
    expect(host.querySelector('[data-receipt="c30"]')?.textContent).toContain('ambiguous');
});
it('only open affected evidence rereads, including recovery with unchanged historical version and status', async () => {
    const failed = { ...park, command_id: 'c0', terminal: true, status: 'ambiguous' };
    receipts.set('c0', failed);
    await render(<Receipt id="c0" />); expect(detailReads()).toHaveLength(0);
    await render(<Receipt id="c0" detail />); expect(detailReads()).toHaveLength(1);
    await tick(60000); expect(detailReads()).toHaveLength(1); expect(compactReads()).toHaveLength(1);
    receipts.set('c0', { ...failed, deck_movement: { recovery_resolution: { command_id: 'c0', decision_id: 'recovered', semantic_state_revision: 2, transition_sequence: 12 } } });
    await publish({ next_after_sequence: 12, changed_command_ids: ['c0'] });
    expect(detailReads()).toHaveLength(2); expect(host.textContent).toContain('recovered');
    await render(<Receipt id="c0" />);
    await publish({ next_after_sequence: 13, changed_command_ids: ['c0'] });
    expect(detailReads()).toHaveLength(2);
    await render(<Receipt id="c0" detail />); expect(detailReads()).toHaveLength(3);
    const before = detailReads().length;
    // Merely changing a compact observer timestamp is not an evidence version.
    await act(async () => { await client.refetchQueries({ queryKey: ['bioxp', 'operator-controls', 'v2', 'receipt', 'c0', 7] }); });
    await tick(); expect(detailReads()).toHaveLength(before);
});
it('hidden suspends the single feed; resume and source/connection replacement reconcile by GET only', async () => {
    await render(<Fleet count={3} />); const before = requests.length;
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    expect(pending).toHaveLength(0); await tick(60000); expect(requests).toHaveLength(before);
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
    await publish({ next_after_sequence: 14, changed_command_ids: ['c0'] });
    expect(compactReads()).toHaveLength(6);
    await publish({ source_instance_id: 'source-b', ownership_generation: 5, reset: true, next_after_sequence: 0, changed_command_ids: [] });
    expect(compactReads()).toHaveLength(9);
    await render(<Fleet count={3} generation={8} />);
    expect(pending.map(p => p.generation)).toEqual([8]); expect(maxWaits).toBe(1);
    expect(requests.every(r => r.method === 'get')).toBe(true);
});
it('drains has_more immediately without waiting for a per-command timer', async () => {
    await render(<Receipt id="c0" />);
    await publish({ next_after_sequence: 11, has_more: true, changed_command_ids: ['c0'] });
    expect(compactReads()).toHaveLength(2);
    await publish({ next_after_sequence: 12, has_more: false, changed_command_ids: ['c0'] });
    expect(compactReads()).toHaveLength(3); expect(maxWaits).toBe(1);
});

it('a failed receipt read is visible and quiet until a committed notification, never a submission retry', async () => {
    function FailedRead() {
        const q = useBioXpOperatorReceiptV2('c0', 7, true);
        return <p>{q.error ? 'Receipt read unavailable' : q.data?.status}</p>;
    }
    failReceipt = true; await render(<FailedRead />);
    expect(host.textContent).toContain('Receipt read unavailable');
    await tick(60000); expect(compactReads()).toHaveLength(1);
    failReceipt = false;
    receipts.set('c0', { ...park, command_id: 'c0', terminal: true, status: 'failed' });
    await publish({ next_after_sequence: 11, changed_command_ids: ['c0'] });
    expect(host.textContent).toContain('failed'); expect(compactReads()).toHaveLength(2);
    await tick(60000); expect(compactReads()).toHaveLength(2);
    expect(requests.every(r => r.method === 'get')).toBe(true);
});

it('a failed identity read is not a repeating timer; recovery notification wakes the original ID without POST', async () => {
    receipts.set('c0', new Error('temporary read outage'));
    await render(<Receipt id="c0" />); expect(compactReads()).toHaveLength(1);
    await tick(60000); expect(compactReads()).toHaveLength(1);
    receipts.set('c0', { ...park, command_id: 'c0', terminal: true, status: 'failed' });
    await publish({ next_after_sequence: 11, changed_command_ids: ['c0'] });
    expect(compactReads()).toHaveLength(2); expect(host.textContent).toContain('failed');
    expect(requests.every(r => r.method === 'get')).toBe(true);
});

it('does not lose a terminal commit arriving while the initial compact identity GET is in flight', async () => {
    let finish!: (receipt: unknown) => void;
    receipts.set('c0', new Promise(resolve => { finish = resolve; }));
    await render(<Receipt id="c0" />); expect(compactReads()).toHaveLength(1);
    await publish({ next_after_sequence: 11, changed_command_ids: ['c0'] });
    receipts.set('c0', { ...park, command_id: 'c0', terminal: true, status: 'stopped' });
    await act(async () => finish({ ...park, command_id: 'c0', terminal: false, status: 'dispatched' }));
    await tick(); expect(host.textContent).toContain('stopped'); expect(compactReads()).toHaveLength(2);
    await tick(60000); expect(compactReads()).toHaveLength(2);
});

it('uncertain admission does one identity lookup then waits for commits, never replays its POST', async () => {
    let action!: ReturnType<typeof useInvokeBioXpDeckActionV2>;
    let found = false;
    function Submitter() { action = useInvokeBioXpDeckActionV2(7, true); return <p>{action.submissions[0]?.state}</p>; }
    const transport = api.defaults.adapter as import('axios').AxiosAdapter;
    api.defaults.adapter = async config => {
        if (config.method === 'post' || config.url?.includes('/requests/')) {
            requests.push({ url: config.url!, method: config.method! });
            if (config.method === 'post') throw new Error('uncertain timeout');
            if (!found) throw { response: { status: 404 } };
            return { data: { ...park, command_id: 'original', action_id: 'oem.deck.move_to_location' }, status: 200, statusText: 'OK', headers: {}, config };
        }
        return transport(config);
    };
    await render(<Submitter />);
    await act(async () => { action.submit({ schema_version: 'bioxp.operator_action_request.v2', action_id: 'oem.deck.move_to_location',
        idempotency_key: 'original-key', expected_connection_generation: 7, expected_ownership_generation: 4,
        expected_board_epoch_by_board: {}, inputs: { target: 'LOC_PARK', camera_offset: false } }); });
    await tick(); expect(host.textContent).toContain('uncertain');
    expect(requests.filter(r => r.url.includes('/requests/'))).toHaveLength(1);
    await tick(60000); expect(requests.filter(r => r.url.includes('/requests/'))).toHaveLength(1);
    found = true; await publish({ next_after_sequence: 12, changed_command_ids: ['original'] });
    expect(host.textContent).toContain('accepted');
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(requests.filter(r => r.url.includes('/requests/')).map(r => r.url)).toEqual([
        '/api/bioxp/operator-controls/v2/requests/original-key', '/api/bioxp/operator-controls/v2/requests/original-key',
    ]);
});

const mapProps = (): BioXpLiveDeckProps => ({ generation: 7, visible: true, connected: true, stale: true,
    dashboard: { ownership_generation: 99, telemetry: { axes: [{ axis: 'x', position_steps: 999999 }] } } as any,
    selection: { station: 'LOC_OC', wells: ['H12'] }, selectedDestination: destinations.action.destination_options.find(d => d.target === 'LOC_TC') as any,
    onMoveToStation: vi.fn(), onMoveToWell: vi.fn(), stationDisabledReason: () => null, wellDisabledReason: null,
    doorControls: null, movementControls: null, commandDetails: null, transferControls: null, cameraControls: <input aria-label="Retained camera" defaultValue="draft" /> });
it('actual per-axis feed moves four guns despite stale catalog; null/partial/read times retain truth and selection/camera', async () => {
    const props = mapProps(); await render(<BioXpLiveDeck {...props} />);
    expect(host.querySelector('[data-gun-module]')).toBeNull();
    const camera = host.querySelector('[aria-label="Retained camera"]');
    await publish({ pose_sequence: 1, pose: { ownership_generation: 4, axes: [{ axis: 'x', position_steps: 50000, observed_at: 0 }] } });
    expect(host.querySelector('[data-gun-module]')).toBeNull();
    await publish({ pose_sequence: 2, pose: { ownership_generation: 4, axes: [{ axis: 'y', position_steps: 10000, observed_at: 2 }] } });
    const guns = () => [...host.querySelectorAll('[data-gun-module]')].map(g => g.getAttribute('transform'));
    expect(guns()).toEqual(gunCenters(50000, 10000).map(g => `translate(${g.point[0]} ${g.point[1]})`));
    expect(host.querySelectorAll('[data-gun-module]')).toHaveLength(4);
    const rail = host.querySelector('[data-testid="vertical-deck-arm"]')!;
    expect(+rail.getAttribute('height')!).toBeGreaterThan(+rail.getAttribute('width')!);
    await publish({ pose_sequence: 3, pose: { ownership_generation: 4, axes: [{ axis: 'x', position_steps: 60000, observed_at: 3 }] } });
    expect(guns()).toEqual(gunCenters(60000, 10000).map(g => `translate(${g.point[0]} ${g.point[1]})`));
    expect(host.querySelector('[data-axis-observed="y"]')?.getAttribute('data-observed-at')).toBe('2');
    expect(host.querySelector('[data-selected-station]')?.getAttribute('data-selected-station')).toBe('LOC_TC');
    await publish({ pose_sequence: 4, pose: null }); const retained = guns();
    await render(<BioXpLiveDeck {...props} visible={false} />); await render(<BioXpLiveDeck {...props} />);
    expect(guns()).toEqual(retained); expect(host.textContent).toContain('Last known position');
    expect(host.querySelector('[aria-label="Retained camera"]')).toBe(camera);
    expect(props.onMoveToStation).not.toHaveBeenCalled(); expect(props.onMoveToWell).not.toHaveBeenCalled();
    await publish({ source_instance_id: 'source-b', ownership_generation: 5, pose: null, reset: true });
    expect(host.querySelector('[data-gun-module]')).toBeNull();
    expect(host.textContent).toContain('Position unavailable');
});
