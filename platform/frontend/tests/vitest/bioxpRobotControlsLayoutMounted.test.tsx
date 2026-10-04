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

describe('Robot controls layout transport parity (inert)', () => {
    it.each(['unarmed', 'armed', 'referenced'] as const)('native producer %s Open/Close admission and titles survive the layout', async phase => {
        actions = [...structuredClone(manualProducer[phase]), ...actions.filter(action => action.action_id.startsWith('fixture.'))];
        await mount();
        for (const axis of ['g', 'door']) for (const label of ['Open', 'Close']) {
            const descriptor = actions.find(action => action.informational_path === `/motion/${axis === 'g' ? 'gripper' : 'thermal_door'}/${label.toLowerCase()}`);
            const control = button(label, row(axis));
            expect(control.disabled).toBe(!descriptor.enabled);
            expect(control.title).toBe(descriptor.enabled ? 'Robot control' : descriptor.disabled_reason);
            if (control.disabled) await act(async () => control.click());
        }
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(['dispatched', 'ambiguous', 'failed'])('XY %s outcome stays outside closed details and survives draft changes without resubmitting', async status => {
        await mount();
        vi.mocked(api.post).mockImplementationOnce(async (url, body: any) => {
            const receipt = { ...park, command_id: 'inert-outcome', action_id: url.split('/').at(-1), status,
                terminal: status !== 'dispatched', completion_class: status === 'failed' ? 'failed' : null,
                canonical_inputs: body.inputs, error: status === 'failed' ? { code: 'fixture_failure', message: 'Inert controller failure' } : null };
            rows.set(receipt.command_id, receipt); return { data: receipt };
        });
        await click(button('Move X + Y together'));
        const expected = status === 'failed' ? 'XY command failed' : `XY command pending · ${status}`;
        const notice = [...host.querySelectorAll('[role="status"], [role="alert"]')].find(el => el.textContent?.includes(expected));
        expect(notice, expected).toBeDefined();
        expect(notice!.closest('details:not([open])')).toBeNull();
        await setInput(input('z', 0), 4321);
        expect(host.textContent).toContain(expected);
        expect(api.post).toHaveBeenCalledTimes(1);
    });
    it.each(['x', 'y', 'z'])('%s relative signs, Home, absolute and Stop preserve native requests', async axis => {
        await mount();
        await setInput(input(axis, 0), 1234); await setInput(input(axis, 1), 23456);
        await expectAction(button('Move −', row(axis)), `oem.${axis}.move_steps`, { steps: -1234 });
        await expectAction(button('Move +', row(axis)), `oem.${axis}.move_steps`, { steps: 1234 });
        await expectAction(button('Home', row(axis)), `oem.${axis}.${axis === 'z' ? 'manual_home' : 'manual_panel_home'}`, {});
        await expectAction(button('Go absolute', row(axis)), `oem.${axis}.move_absolute`, axis === 'y' ? { target_steps: 23456 } : { position_steps: 23456 });
        await expectInterrupt(button('Stop', row(axis)), axis as 'x' | 'y' | 'z');
        expect(api.post).toHaveBeenCalledTimes(5);
    });
    it('gripper relative, Home, absolute, Open/Close/Wide and Stop retain V1 route inputs', async () => {
        await mount(); await setInput(input('g', 0), 1234); await setInput(input('g', 1), 23456);
        for (const [label, steps] of [['Move −', -1234], ['Move +', 1234]] as const)
            await expectAction(button(label, row('g')), v1Id(paths.relative), { axis: 'g', steps }, 'v1');
        await expectAction(button('Home', row('g')), v1Id(paths.home), { axis: 'g' }, 'v1');
        await expectAction(button('Go absolute', row('g')), v1Id(paths.absolute), { axis: 'g', position_steps: 23456 }, 'v1');
        for (const [label, path] of [['Open', '/motion/gripper/open'], ['Close', '/motion/gripper/close'], ['Open wide', paths.wide]])
            await expectAction(button(label, row('g')), v1Id(path), {}, 'v1');
        await expectAction(button('Stop', row('g')), v1Id(paths.stop), { axis: 'g' }, 'v1');
        expect(api.post).toHaveBeenCalledTimes(8);
    });
    it('thermal door Home/Open/Close/Stop preserves addressed routes without jog inputs', async () => {
        await mount(); expect(row('door').querySelectorAll('input')).toHaveLength(0);
        await expectAction(button('Home', row('door')), v1Id(paths.home), { axis: 'door' }, 'v1');
        for (const label of ['Open', 'Close']) await expectAction(button(label, row('door')), v1Id(`/motion/thermal_door/${label.toLowerCase()}`), {}, 'v1');
        await expectAction(button('Stop', row('door')), v1Id(paths.stop), { axis: 'door' }, 'v1');
    });
    it('combined XY shares X/Y drafts and keeps Z Clear distinct from switch-search Home', async () => {
        await mount(); await setInput(input('x', 1), 32100); await setInput(input('y', 1), 43210);
        expect((host.querySelector('[aria-label="Combined X target (steps)"]') as HTMLInputElement).value).toBe('32100');
        expect((host.querySelector('[aria-label="Combined Y target (steps)"]') as HTMLInputElement).value).toBe('43210');
        await expectAction(button('Move X + Y together'), 'oem.xy.move_absolute', { x: 32100, y: 43210 });
        await expectAction(button('Home X + Y together'), 'oem.xy.home', {});
        await expectAction(button('Z Clear (automatic position)'), 'oem.z.clear', {});
        await expectAction(button('Z switch-search recovery home'), 'oem.z.diagnostic_home_axis', {});
    });
    it('controller enable/recover, unique header Stops and Software Abort keep separate native lanes', async () => {
        await mount();
        await expectAction(button('Enable controllers'), 'meta.activate_motion', {});
        await expectAction(button('Recover controllers (no homing)'), 'meta.recover_motion_non_homing', {});
        for (const axis of ['x', 'y', 'z'] as const) {
            expect([...host.querySelectorAll('[aria-label="Stop controls"] button')].filter(el => name(el) === `Stop ${axis.toUpperCase()}`)).toHaveLength(1);
            await expectInterrupt(button(`Stop ${axis.toUpperCase()}`), axis);
        }
        expect([...host.querySelectorAll('button')].filter(el => name(el) === 'Stop gripper')).toHaveLength(1);
        await expectAction(button('Stop gripper'), v1Id(paths.stop), { axis: 'g' }, 'v1');
        await expectInterrupt(button('Software Abort (cancel waiters)'), 'abort_all');
        expect([...host.querySelectorAll('button')].filter(el => name(el) === 'Software Abort (cancel waiters)')).toHaveLength(1);
    });
    it('disconnect/connect/restart send exactly the existing service requests', async () => {
        await mount(); await click(button('Disconnect'));
        expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/api/bioxp/connection/disconnect']);
        await click(button('Connect'));
        expect(vi.mocked(api.post).mock.calls[1]).toEqual(['/api/bioxp/connection/connect']);
        const restart = host.querySelector('[aria-label="Robot service restart"] button') as HTMLButtonElement;
        await click(restart);
        expect(vi.mocked(api.post).mock.calls[2]).toEqual(['/api/bioxp/service/restart', {}, { timeout: 75000 }]);
        expect(api.post).toHaveBeenCalledTimes(3);
    });
    it('settings and position-table actions stay explicit rather than firing when Tools opens', async () => {
        await mount();
        const tools = [...host.querySelectorAll('details')].find(el => el.querySelector('summary')?.textContent?.includes('Tools'));
        if (tools) await act(async () => { tools.open = true; tools.dispatchEvent(new Event('toggle')); });
        expect(api.post).not.toHaveBeenCalled();
        await expectAction(button('Show axis settings'), v1Id(paths.settings), {}, 'v1');
        await expectAction(button('Show position table'), v1Id(paths.positions), {}, 'v1');
    });
    it('all five rows, disclosures and all tabs retain drafts without a picker or presets', async () => {
        await mount();
        for (const axis of ['x', 'y', 'z', 'g']) { await setInput(input(axis, 0), 1234); await setInput(input(axis, 1), 23456); }
        expect(host.querySelector('button[data-axis], button[aria-label^="Select "][aria-label$=" axis"]')).toBeNull();
        expect(host.querySelector('[aria-label$="step presets"]')).toBeNull();
        for (const axis of ['x', 'y', 'z', 'g', 'door']) {
            expect(row(axis).closest('[hidden], [aria-hidden="true"]')).toBeNull();
            const disclosure = row(axis).querySelector<HTMLButtonElement>('button[aria-expanded][aria-controls]');
            expect(disclosure, `${axis} evidence disclosure`).not.toBeNull();
            expect(disclosure!.getAttribute('aria-expanded')).toBe('false');
            await click(disclosure!); expect(disclosure!.getAttribute('aria-expanded')).toBe('true');
            await click(disclosure!); expect(disclosure!.getAttribute('aria-expanded')).toBe('false');
        }
        for (const tab of ['pipettes', 'workflows', 'live-deck', 'robot']) await click(host.querySelector(`#control-tab-${tab}`)!);
        for (const axis of jogAxes) {
            expect(input(axis, 0).value).toBe('1234'); expect(input(axis, 1).value).toBe('23456');
            expect(range(axis, 'relative steps').value).toBe('1234'); expect(range(axis, 'absolute target').value).toBe('23456');
        }
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(jogAxes)('%s sliders share exact numeric drafts and submit only through existing move actions', async axis => {
        await mount();
        const relative = range(axis, 'relative steps'), absolute = range(axis, 'absolute target');
        expect(relative.max).toBe(String(publishedMaximum[axis]));
        expect(absolute.max).toBe(String(publishedMaximum[axis]));
        expect(relative.min).toBe(axis === 'y' ? '0' : '1');
        expect(Number(absolute.min)).toBe(Math.max(0, Number(input(axis, 1).min)));
        expect(input(axis, 1).labels?.[0].textContent).toContain('Absolute target (steps)');
        await setInput(relative, 1234); await setInput(absolute, 23456);
        expect(input(axis, 0).value).toBe('1234'); expect(input(axis, 1).value).toBe('23456');
        expect(api.post).not.toHaveBeenCalled();
        await expectAction(button('Move −', row(axis)), axis === 'g' ? v1Id(paths.relative) : `oem.${axis}.move_steps`,
            axis === 'g' ? { axis, steps: -1234 } : { steps: -1234 }, axis === 'g' ? 'v1' : 'v2');
        await expectAction(button('Move +', row(axis)), axis === 'g' ? v1Id(paths.relative) : `oem.${axis}.move_steps`,
            axis === 'g' ? { axis, steps: 1234 } : { steps: 1234 }, axis === 'g' ? 'v1' : 'v2');
        await expectAction(button('Go absolute', row(axis)), axis === 'g' ? v1Id(paths.absolute) : `oem.${axis}.move_absolute`,
            axis === 'g' ? { axis, position_steps: 23456 } : axis === 'y' ? { target_steps: 23456 } : { position_steps: 23456 }, axis === 'g' ? 'v1' : 'v2');
        await setInput(input(axis, 0), 4321); await setInput(input(axis, 1), 34567);
        expect(range(axis, 'relative steps').value).toBe('4321'); expect(range(axis, 'absolute target').value).toBe('34567');
        expect(api.post).toHaveBeenCalledTimes(3);
    });
    it.each(jogAxes)('%s changing telemetry bounds and clearing an absolute entry never rewrites the draft', async axis => {
        await mount();
        await setInput(input(axis, 0), 54321); await setInput(input(axis, 1), 65432);
        const numericBounds = [input(axis, 0).min, input(axis, 0).max, input(axis, 1).min, input(axis, 1).max];
        const telemetry = catalog.dashboard.telemetry.axes.find((item: any) => item.axis === axis);
        telemetry.max_steps = 40000; telemetry.min_steps = 500;
        await refreshTelemetry();
        expect(range(axis, 'relative steps').max).toBe('40000');
        expect(range(axis, 'absolute target').max).toBe('40000');
        expect(Number(range(axis, 'absolute target').min)).toBe(Math.max(500, Number(input(axis, 1).min)));
        expect(range(axis, 'relative steps').min).toBe(axis === 'y' ? '0' : '1');
        expect(input(axis, 0).value).toBe('54321'); expect(input(axis, 1).value).toBe('65432');
        expect([input(axis, 0).min, input(axis, 0).max, input(axis, 1).min, input(axis, 1).max]).toEqual(numericBounds);
        await setInput(input(axis, 1), '');
        telemetry.max_steps = publishedMaximum[axis]; await refreshTelemetry();
        expect(input(axis, 1).value).toBe(''); expect(input(axis, 0).value).toBe('54321');
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(jogAxes)('%s missing telemetry bounds leave exact entry and native moves available', async axis => {
        catalog.dashboard.telemetry.axes = [];
        // Remove specialized telemetry too: a numeric schema ceiling is not a travel limit.
        if (catalog.dashboard.telemetry[`${axis}_axis`]?.status) {
            delete catalog.dashboard.telemetry[`${axis}_axis`].status.max_steps;
        }
        await mount();
        for (const slider of row(axis).querySelectorAll<HTMLInputElement>('input[type="range"]')) {
            expect(slider.disabled).toBe(true);
            expect(Number(slider.max || 100)).toBeLessThan(2147483647);
        }
        await setInput(input(axis, 0), 1234); await setInput(input(axis, 1), 23456);
        expect(api.post).not.toHaveBeenCalled();
        await expectAction(button('Move +', row(axis)), axis === 'g' ? v1Id(paths.relative) : `oem.${axis}.move_steps`,
            axis === 'g' ? { axis, steps: 1234 } : { steps: 1234 }, axis === 'g' ? 'v1' : 'v2');
        await expectAction(button('Go absolute', row(axis)), axis === 'g' ? v1Id(paths.absolute) : `oem.${axis}.move_absolute`,
            axis === 'g' ? { axis, position_steps: 23456 } : axis === 'y' ? { target_steps: 23456 } : { position_steps: 23456 }, axis === 'g' ? 'v1' : 'v2');
    });
});
