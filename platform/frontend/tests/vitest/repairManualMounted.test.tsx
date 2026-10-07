import { RepairManualViewer } from '../../src/components/repairManualViewers';
import { readFileSync, writeFileSync } from 'node:fs';
import { bioXpReceiptStatusText } from '../../src/lib/bioxpEvidencePresentation';
import { repairManualLatestRequest } from '../../src/lib/repairManualFeedback';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import manualProducer from '../fixtures/bioxpManualCatalogProducer.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';

// Transport boundary only: real cockpit, children, queries, mutations and admission.
// Never import a live API implementation. Unknown reads fail closed in this fixture,
// not in the application; fetch/XHR are additionally trapped against regressions.
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let root: Root, host: HTMLDivElement, client: QueryClient;
let catalog: any, actions: any[], connected: boolean, rows: Map<string, any>;
let unexpectedReads: string[];
const tick = async () => act(async () => { await vi.advanceTimersByTimeAsync(5); });
const name = (element: Element) => element.getAttribute('aria-label') ?? element.textContent?.trim();
const button = (label: string, scope: ParentNode = host) => {
    const aliases: Record<string, string[]> = { Connect: ['Connect BMS Link'], 'Open wide': ['Open Wide'], 'Home X + Y together': ['Home X + Y'] };
    const matches = [...scope.querySelectorAll<HTMLButtonElement>('button')].filter(el => [label, ...(aliases[label] ?? [])].includes(name(el) ?? ''));
    expect(matches.length, `button ${label}`).toBeGreaterThan(0);
    return matches[0];
};
const row = (axis: string) => {
    const label = ({ x: 'X Axis', y: 'Y Axis', z: 'Z Axis', g: 'Gripper', door: 'Thermal Door' } as Record<string, string>)[axis];
    const result = host.querySelector(`[data-axis="${axis}"]:not(button)`) ?? [...host.querySelectorAll('article')].find(el => el.querySelector('h3')?.textContent === label);
    expect(result, `${axis} control row`).toBeTruthy();
    return result!;
};
const input = (axis: string, index: number) => row(axis).querySelectorAll<HTMLInputElement>('input[type="number"]')[index];
const jogAxes = ['x', 'y', 'z', 'g'] as const;
const publishedMaximum = { x: 90263, y: 102956, z: 160000, g: 160000 };
const range = (axis: string, kind: 'relative steps' | 'absolute target') => {
    const result = row(axis).querySelector<HTMLInputElement>(`input[type="range"][aria-label="${axis.toUpperCase()} ${kind}"]`);
    expect(result, `${axis} ${kind} slider`).not.toBeNull();
    return result!;
};
const refreshTelemetry = async () => {
    await act(async () => { await client.invalidateQueries(); });
    await tick(); await tick();
};
const setInput = async (element: HTMLInputElement, value: number | '') => {
    expect(element).toBeDefined();
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, String(value));
        element.dispatchEvent(new Event('input', { bubbles: true }));
    });
};
const click = async (element: HTMLElement) => {
    expect(element).toBeDefined();
    if (element instanceof HTMLButtonElement) expect(element.disabled, `${name(element)}: ${element.title}`).toBe(false);
    await act(async () => element.click()); await tick();
};
const mount = async () => {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
    await tick(); await tick();
};
const canonicalIds = [
    'meta.activate_motion', 'meta.recover_motion_non_homing', 'oem.xy.move_absolute', 'oem.xy.home',
    ...['x', 'y', 'z'].flatMap(axis => [`oem.${axis}.move_steps`, `oem.${axis}.move_absolute`, `oem.${axis}.stop`]),
    'oem.x.manual_panel_home', 'oem.y.manual_panel_home', 'oem.z.manual_home', 'oem.z.clear', 'oem.z.diagnostic_home_axis', 'oem.abort_all',
];
// Additional V1 rows are explicit synthetic routing sentinels, not claimed native
// captures. Open/Close use the verbatim native producer IDs and admission below.
const paths = {
    relative: '/motion/oem/manual/relative', absolute: '/motion/oem/manual/absolute', home: '/motion/oem/manual/home',
    stop: '/motion/diagnostics/stop', wide: '/motion/gripper/open_wide',
    settings: '/motion/oem/machine_config', positions: '/motion/oem/position_table',
};
const v1Id = (path: string) => actions.find(action => action.informational_path === path).action_id;
async function expectAction(element: HTMLElement, actionId: string, inputs: Record<string, unknown>, lane: 'v1' | 'v2' = 'v2') {
    const before = vi.mocked(api.post).mock.calls.length;
    await click(element);
    expect(api.post).toHaveBeenCalledTimes(before + 1);
    const [url, body] = vi.mocked(api.post).mock.calls[before];
    expect(url).toBe(`/api/bioxp/operator-controls/${lane === 'v2' ? 'v2/' : ''}actions/${actionId}`);
    expect((body as any).inputs).toEqual(inputs);
    expect(body).toMatchObject({ expected_connection_generation: 7, expected_ownership_generation: catalog.dashboard.ownership_generation });
    expect((body as any).idempotency_key).toEqual(expect.any(String));
}
async function expectInterrupt(element: HTMLElement, axis: 'x' | 'y' | 'z' | 'abort_all') {
    const before = vi.mocked(api.post).mock.calls.length;
    await click(element);
    expect(api.post).toHaveBeenCalledTimes(before + 1);
    const [url, body] = vi.mocked(api.post).mock.calls[before];
    expect(url).toBe(`/api/bioxp/operator-controls/v2/interrupts/oem.${axis === 'abort_all' ? axis : `${axis}.stop`}`);
    expect(body).toEqual({ schema_version: 'bioxp.operator_interrupt_request.v1', expected_connection_generation: 7,
        observed_ownership_generation: catalog.dashboard.ownership_generation, observed_board_epoch_by_board: {},
        idempotency_key: expect.any(String), reason: axis === 'abort_all'
            ? 'BMS operator requested OEM software Abort: cancel waiters only; motors may continue'
            : `BMS operator requested recovered-OEM ${axis === 'y' ? 'addressed ' : ''}${axis.toUpperCase()} STOP` });
}
beforeEach(() => {
    vi.useFakeTimers(); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Live fetch forbidden in inert Robot test'); }));
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { throw new Error('Live XHR forbidden in inert Robot test'); });
    connected = true; rows = new Map(); unexpectedReads = [];
    catalog = structuredClone(metadata.catalog);
    // Explicit telemetry limits, independent of request-schema int32 ceilings.
    catalog.dashboard.telemetry.axes = jogAxes.map(axis => ({ axis, min_steps: 0, max_steps: publishedMaximum[axis], position_steps: 0 }));
    catalog.dashboard.generated_at = Date.now() / 1000;
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.actions = canonicalIds.map(action_id => ({ action_id, enabled: true, disabled_reason: null,
        interrupt: action_id.endsWith('.stop') || action_id === 'oem.abort_all',
        request_schema_version: action_id.endsWith('.stop') || action_id === 'oem.abort_all' ? 'bioxp.operator_interrupt_request.v1' : 'bioxp.operator_action_request.v2',
        response_schema_version: 'bioxp.operator_action_receipt.v2' }));
    actions = structuredClone(manualProducer.referenced);
    for (const [key, path] of Object.entries(paths)) actions.push({ ...actions[0], action_id: `fixture.route.${key}`, informational_path: path,
        enabled: true, disabled_reason: null, safety_class: key === 'stop' ? 'stop' : ['settings', 'positions'].includes(key) ? 'read_only' : 'motion',
        inputs: [] });
    vi.mocked(api.get).mockImplementation(async (url, options) => {
        if (url === '/api/bioxp/status') return { data: { connection: { active: connected, configured: true, reachable: connected, runtime_ready: true, generation: 7 }, mutation_access: { enabled: true } } };
        if (url.endsWith('/catalog')) return { data: catalogWireFixture({ actions, ownership_generation: catalog.dashboard.ownership_generation,
            dashboard: catalog.dashboard.telemetry, canonical: catalog }, options?.params?.view) };
        if (url === '/api/bioxp/service') return { data: { available: true, restart_in_progress: false, unit: 'bioxp-api.service' } };
        if (url.includes('/receipts/')) return { data: rows.get(url.split('/').at(-1)!) };
        if (url.endsWith('/updates')) return new Promise((_resolve, reject) => options?.signal?.addEventListener?.('abort', () => reject(new Error('fixture aborted'))));
        if (url.includes('/history')) return { data: { items: [], next_cursor: null } };
        if (url.endsWith('/calibration-settings')) return { data: { active_motion_positions: [], saved_motion_positions: [], active_positions: [], saved_positions: [], active_loader_adjustments: [], saved_loader_adjustments: [] } };
        if (url.includes('/camera/')) return { data: { active: false, available: false, state: 'unavailable' } };
        if (url.endsWith('/protocols/jobs') || url.endsWith('/methods/runs')) return { data: [] };
        if (['/api/bioxp/methods/examples', '/api/bioxp/methods/library', '/api/bioxp/methods/presets'].includes(url)) return { data: [] };
        // Unrelated tabs deliberately receive unavailable optional services. Their
        // real error presentation must not erase Robot drafts or submit anything.
        if (['/api/bioxp/methods/schema', '/api/bioxp/operator-controls/pipettes/application/status'].includes(url))
            throw new Error('Optional service unavailable in inert navigation fixture');
        unexpectedReads.push(String(url)); throw new Error(`Unmatched inert read ${url}`);
    });
    vi.mocked(api.post).mockImplementation(async (url, body: any) => {
        if (url === '/api/bioxp/connection/connect' || url === '/api/bioxp/connection/disconnect') {
            connected = url.endsWith('/connect'); return { data: { active: connected, generation: 7 } };
        }
        if (url === '/api/bioxp/service/restart') return { data: { restarted: true, unit: 'bioxp-api.service', active_state: 'active', sub_state: 'running', invocation_id: 'inert', pid: 123 } };
        const command_id = `inert-${vi.mocked(api.post).mock.calls.length}`;
        const receipt = { ...park, command_id, action_id: url.split('/').at(-1), status: 'completed', terminal: true,
            canonical_inputs: body?.inputs ?? {}, ownership_generation: catalog.dashboard.ownership_generation };
        rows.set(command_id, receipt); return { data: receipt };
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); host.remove();
    vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
    expect(unexpectedReads).toEqual([]);
});


const nativeY = JSON.parse(readFileSync(process.env.REPAIR_MANUAL_NATIVE_OUTPUT!, 'utf8'));
const capturedReaderRoot = process.env.REPAIR_MANUAL_READER_FIXTURES!;
const capturedSettings = JSON.parse(readFileSync(`${capturedReaderRoot}/machine-config.json`, 'utf8'));
const capturedTable = JSON.parse(readFileSync(`${capturedReaderRoot}/position-table.json`, 'utf8'));
const interceptReaders = (reader: (url: string) => Promise<any>) => {
    const fallback = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation((url, options) => String(url).includes('/readers/') ? reader(String(url)) : fallback(url, options));
};
describe('Manual repair native projection and inert requests', () => {
    it('exports actual mounted rows for isolated browser geometry (no transport)', async () => {
        catalog.dashboard.y_axis = nativeY.sample;
        await mount();
        expect(host.querySelectorAll('.bx-axis')).toHaveLength(5);
        if (process.env.REPAIR_MANUAL_GEOMETRY_HTML) writeFileSync(process.env.REPAIR_MANUAL_GEOMETRY_HTML,
            '<!doctype html><html><head><style>body{margin:0;font-family:Arial;background:#111827;--text-primary:#f1f5f9;--text-secondary:#cbd5e1;--text-muted:#94a3b8;--bg-primary:#111827;--bg-tertiary:#1e293b;--card-bg:#172033;--border-primary:#475569;--accent-primary:#60a5fa;--error:#ef4444;--warning:#f59e0b;--success:#22c55e}*{box-sizing:border-box}[hidden]{display:none!important}main{padding:16px}</style><style>'
            + readFileSync('src/components/BioXpRobotControls.css','utf8') + '</style></head><body><main>' + host.innerHTML + '</main></body></html>');
        expect(api.post).not.toHaveBeenCalled();
    });
    it('passive full readers remain independent after old Stop; table counts, nulls, values and search match captured response', async () => {
        interceptReaders(async url => ({data: url.endsWith('/settings') ? capturedSettings : capturedTable}));
        await mount();
        await expectInterrupt(button('Stop', row('y')), 'y');
        const posts = vi.mocked(api.post).mock.calls.length;
        await click(button('Show settings'));
        await click(button('Show position table'));
        const settings = host.querySelector('[aria-label="Full settings viewer"]')!;
        const table = host.querySelector('[aria-label="Full position table viewer"]')!;
        expect(settings.textContent).toContain(capturedSettings.source_type);
        expect(settings.textContent).toContain('null');
        expect(table.textContent).toContain(`${capturedTable.rows.length} / ${capturedTable.rows.length} returned rows`);
        expect(table.querySelectorAll('tbody tr')).toHaveLength(capturedTable.rows.length);
        for (const item of capturedTable.rows) expect(table.textContent).toContain(item.location_id);
        await setInput(table.querySelector('input[type=search]')!, 'LOC_MS' as any);
        const expected = capturedTable.rows.filter((item: any) => JSON.stringify(item).includes('LOC_MS'));
        expect(table.querySelectorAll('tbody tr')).toHaveLength(expected.length);
        await click(button('Inspect position row 1', table));
        expect(table.textContent).toContain('26213');
        expect(api.post).toHaveBeenCalledTimes(posts);
        const reads = vi.mocked(api.get).mock.calls.filter(([url]) => String(url).includes('/readers/'));
        expect(reads.map(([url]) => url)).toEqual(['/api/bioxp/operator-controls/readers/settings', '/api/bioxp/operator-controls/readers/position-table']);
        expect(reads.every(([, options]) => options?.params?.expected_connection_generation === 7)).toBe(true);
    });
    it('connection change detaches an old read and preserves last-good identity without starting another read', async () => {
        const deferred: ((value: any) => void)[] = [];
        interceptReaders(() => new Promise(resolve => deferred.push(resolve)));
        const render = async (generation: number) => act(async () => root.render(<RepairManualViewer kind="settings" generation={generation} connected disabled={false} />));
        await render(7); await click(button('Show settings'));
        await act(async () => deferred[0]({data:{marker:'connection7'}}));
        await click(button('Show settings')); await render(8);
        await act(async () => deferred[1]({data:{marker:'late7'}}));
        expect(host.textContent).toContain('Last-good settings · request 1, connection 7');
        expect(host.textContent).toContain('connection7'); expect(host.textContent).not.toContain('late7');
        expect(deferred).toHaveLength(2);
        await click(button('Show settings'));
        await act(async () => deferred[2]({data:{marker:'connection8'}}));
        expect(host.textContent).toContain('connection8');
        expect(host.textContent).not.toContain('connection7');
    });
    it('viewer retains last-good with pending/error and rejects reordered older responses', async () => {
        const deferred: ((value: any) => void)[] = [];
        interceptReaders(() => new Promise(resolve => deferred.push(resolve)));
        await mount();
        await click(button('Show settings'));
        await click(button('Show settings'));
        await act(async () => deferred[1]({data:{marker:'newest',unit:'steps',value:null}}));
        await act(async () => deferred[0]({data:{marker:'older'}}));
        const viewer = host.querySelector('[aria-label="Full settings viewer"]')!;
        expect(viewer.textContent).toContain('newest'); expect(viewer.textContent).not.toContain('older');
        await click(button('Show settings'));
        expect(viewer.textContent).toContain('Last-good'); expect(viewer.textContent).toContain('Reading settings');
        await act(async () => deferred[2]({data:{}}));
        expect(viewer.textContent).toContain('Empty response.');
        interceptReaders(async () => {throw new Error('inert read error');});
        await click(button('Show settings'));
        expect(viewer.textContent).toContain('read failed'); expect(viewer.textContent).toContain('Last-good');
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(['0', '12345', 'None', 'invalid', 'sample'])('renders actual native Y output %s without invented validity', async key => {
        catalog.dashboard.y_axis = nativeY[key];
        await mount();
        const panel = row('y');
        await click(button('Y Axis status and evidence', panel));
        expect(panel.textContent).toContain(key === 'invalid' ? 'Invalid reply' : key === 'sample' ? 'Valid reply' : 'Unknown reply');
        expect(panel.querySelector('.bx-position')!.textContent).toBe(`${nativeY[key].position_steps ?? '—'} steps`);
        expect(panel.textContent).toContain('Board epoch: 8');
        expect(panel.textContent).toContain('Prepared board epoch7');
        expect(panel.textContent).toContain('Authority updated (not sample time)');
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(['x', 'y', 'z'])('%s presets only edit drafts; +/- and absolute preserve original requests', async axis => {
        await mount();
        for (const value of [1000, 5000, 10000, 25000]) {
            await click(button(`${axis.toUpperCase()} jog ${value} steps`, row(axis)));
            expect(input(axis, 0).value).toBe(String(value));
        }
        expect(api.post).not.toHaveBeenCalled();
        await expectAction(button('Move −', row(axis)), `oem.${axis}.move_steps`, { steps: -25000 });
        await expectAction(button('Move +', row(axis)), `oem.${axis}.move_steps`, { steps: 25000 });
        await setInput(input(axis, 1), 23456);
        await expectAction(button('Go absolute', row(axis)), `oem.${axis}.move_absolute`, axis === 'y' ? { target_steps: 23456 } : { position_steps: 23456 });
        await expectInterrupt(button('Stop', row(axis)), axis as 'x' | 'y' | 'z');
    });
    it('relative hints use current position and inner margins, not the raw high limit', async () => {
        catalog.dashboard.y_axis = {...nativeY.sample, position_steps: 50000};
        await mount();
        expect(row('y').textContent).toContain('Useful − / +: 49980 / 52936 steps');
        expect(range('y', 'relative steps').max).toBe('52936');
        await setInput(input('y', 0), 200000);
        expect(input('y', 0).value).toBe('200000');
        expect(range('y', 'relative steps').value).toBe('52936');
        expect(api.post).not.toHaveBeenCalled();
    });
    it('absent telemetry never changes manual admission and drafts survive refresh', async () => {
        catalog.dashboard.telemetry.axes = [];
        catalog.dashboard.y_axis = nativeY.None;
        await mount();
        for (const axis of jogAxes) {
            expect(row(axis).querySelector('input[type=range]')).toBeNull();
            expect(button('Move +', row(axis)).disabled).toBe(false);
        }
        await setInput(input('x', 0), 234567);
        await refreshTelemetry();
        expect(input('x', 0).value).toBe('234567');
        await expectAction(button('Move +', row('x')), 'oem.x.move_steps', {steps:234567});
    });
    it.each(jogAxes)('%s blank relative draft survives refresh without submitting', async axis => {
        await mount(); await setInput(input(axis, 0), ''); await refreshTelemetry();
        expect(input(axis, 0).value).toBe('');
        expect(api.post).not.toHaveBeenCalled();
    });
    it('mounted no-op shows requested, native effective and observed facts separately', async () => {
        await mount();
        vi.mocked(api.post).mockImplementationOnce(async (url, body: any) => {
            const receipt = {...park,command_id:'limit-noop',action_id:'oem.x.move_steps',status:'completed',terminal:true,
                command_sent:false,completion_class:'oem_limit_rejected',canonical_inputs:body.inputs,
                requested_values:{steps:25000},effective_values:{command_sent:false},observed_values:{position_steps:90260}};
            rows.set(receipt.command_id,receipt); return {data:receipt};
        });
        await click(button('X jog 25000 steps', row('x'))); await click(button('Move +',row('x')));
        const latest=host.querySelector('[aria-label="Latest command"]')!;
        expect(latest.textContent).toContain('No movement — existing OEM limit (request completed)');
        expect(latest.textContent).toContain('Requested valuessteps25000');
        expect(latest.textContent).toContain('Native effective valuescommand_sentfalse');
        expect(latest.textContent).toContain('Observed values (receipt, not live)position_steps90260');
    });
    it('completed no-op wording does not change the receipt classification', () => {
        const receipt = {status:'completed',command_sent:false,completion_class:'oem_limit_rejected'};
        expect(bioXpReceiptStatusText(receipt, receipt.status)).toBe('No movement — existing OEM limit (request completed)');
        expect(receipt.status).toBe('completed');
        expect(bioXpReceiptStatusText({...receipt, command_sent:true},'completed')).toBe('completed');
        expect(bioXpReceiptStatusText({...receipt, completion_class:'already_at_target'},'completed')).toBe('completed');
    });
    it('new pending request wins over older Stop even when older completion arrives later', async () => {
        await mount();
        await expectInterrupt(button('Stop', row('y')), 'y');
        let resolve: (value: any) => void = () => {};
        vi.mocked(api.post).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
        await click(button('Move +', row('x')));
        const latest = host.querySelector('[aria-label="Latest command"]')!;
        expect(latest.textContent).toContain('oem.x.move_steps · Submitting');
        expect(latest.textContent).not.toContain('inert-1');
        await act(async () => resolve({data:{...park,command_id:'new-axis',action_id:'oem.x.move_steps',terminal:true,status:'completed'}}));
        await tick();
        expect(latest.textContent).toContain('new-axis');
        expect(latest.textContent).not.toContain('inert-1');
    });
    it('ordering is by request start, not completion or command family', () => {
        const older = {submittedAt:1,status:'success',data:'old-stop'};
        const newer = {submittedAt:2,status:'pending',data:undefined};
        expect(repairManualLatestRequest([older,newer])).toBe(newer);
        newer.status='error'; expect(repairManualLatestRequest([newer,older])).toBe(newer);
    });
});
