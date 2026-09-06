import { createHash } from 'node:crypto';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { api } from '../../src/lib/api';
import { BioXpPipetteControlPanel } from '../../src/components/BioXpPipetteControlPanel';
import { usePlanBioXpPipetteApplication, useReadBioXpPipetteReadback } from '../../src/lib/bioxpClient';
import type { BioXpDirectLiquidLookup, BioXpPipetteReadback, BioXpPipetteApplicationPlan, BioXpPipetteReceipt } from '../../src/lib/bioxpClient';

import pendingExport from '../fixtures/f33_pending_get.json?raw';

const adapter = api.defaults.adapter;
let root: Root;
afterEach(async () => { if (root) await act(async () => root.unmount()); api.defaults.adapter = adapter; vi.restoreAllMocks(); vi.useRealTimers(); });
const cases = [
    [useReadBioXpPipetteReadback, 'readback', '/api/bioxp/operator-controls/pipettes/readback', { include_data: false }],
    [usePlanBioXpPipetteApplication, 'application_plan', '/api/bioxp/operator-controls/pipettes/application/plan', { operation: 'detect_fluid', fluid_class: 'RC' }],
] as const;

// Axios-boundary fixtures, not a claim of real robot/store acceptance.
const truth: BioXpPipetteReceipt['truth'] = {
    semantic_query_response_verified: false, delivery_verified: false, controller_acknowledged: false,
    completion_verified: false, hardware_precondition_verified: false, hardware_postcondition_verified: false,
    physical_effect_verified: false, physical_effect_claim_suppressed: true,
};
function originalResult(kind: BioXpDirectLiquidLookup['request_kind']): BioXpPipetteReadback | BioXpPipetteApplicationPlan {
    const common = { controller_acknowledged: false, completion_verified: false, physical_effect_verified: false,
        receipt_id: 'a'.repeat(32), receipt_truth: truth } as const;
    const channelResult = (channel: 0 | 1 | 2 | 3) => ({ channel, semantic_ok: true, firmware: { ok: true, value: '1.0' },
        status: { ok: true, error_code: 0 }, tip: { ok: true, hardware_truth_level: 'hardware_query', tip_loaded: false },
        pressure: null, data: null });
    if (kind === 'readback') return {
        ...common, ok: true, semantic_ok: true, available: true, channel_count: 4,
        channels_constructed_unconditionally: [0, 1, 2, 3],
        channels: [channelResult(0), channelResult(1), channelResult(2), channelResult(3)],
        include_data: false, live_query_performed: true, truth_source: 'live_hardware_queries', hardware_truth_level: 'hardware_query',
        delivery_verified: false, hardware_postcondition_verified: false,
        oem_source_anchor: 'ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData',
    };
    return { ...common, ok: false, operation: 'detect_fluid', mode: 'plan_only', execution_admitted: false,
        motion_commanded: false, liquid_mutation_commanded: false, state_reconciled: false,
        requested_inputs: { fluid_class: 'RC' }, effective_inputs: null,
        steps: [{ action: 'resolve_fluid_target', mutates: false, owner: 'deck' }],
        dependencies: { deck: { bound: false, authority: null, generation: 7, state: {}, blockers: [] } },
        required_dependencies: ['deck'], missing_dependencies: ['deck'], dependency_blockers: [], dependencies_satisfied: false,
        required_completion_evidence: [], constants: {}, oem_source_anchor: 'ControlLib fluid detection', blocker: 'application_dependencies_unbound' };
}
function lookupFixture(kind: BioXpDirectLiquidLookup['request_kind'], key: string,
    state: BioXpDirectLiquidLookup['lookup_state'] = 'resolved'): BioXpDirectLiquidLookup {
    const plan = kind === 'application_plan';
    const operation = plan ? 'application_plan:detect_fluid' : 'live_readback';
    const status = state === 'pending' ? 'running' : state === 'incomplete' ? 'outcome_unknown' : 'completed';
    return { schema: 'bioxp.direct-liquid.lookup.v1', request_kind: kind, idempotency_key: key, lookup_state: state,
        reason: ({ unknown: 'identity_not_found', pending: 'nonterminal', incomplete: 'outcome_unresolved',
            resolved: null, conflict: 'identity_scope_conflict', unavailable: 'store_unavailable' } as const)[state],
        retry_forbidden: true, live_query_performed: false,
        record: ['unknown', 'conflict', 'unavailable'].includes(state) ? null : {
            command_id: 'b'.repeat(32), pipette_operation_id: 'c'.repeat(32), canonical_request_sha256: 'd'.repeat(64),
            operation, entrypoint_id: plan ? 'legacy.record' : 'direct.liquid.readback', caller_class: plan ? 'legacy' : 'direct_api',
            control_class: plan ? 'pipette_state_command' : 'hardware_query', action_id: 'pipette.' + operation,
            command_status: status, pipette_status: status, outcome: status, failure_code: null,
            ownership_generation: 2, connection_generation: null,
            requested_inputs: plan ? { operation: 'detect_fluid', fluid_class: 'RC', home_z_after: true } : { include_data: false },
            result: state === 'resolved' ? originalResult(kind) : null,
        } };
}

const driftFields = ['body', 'command_id', 'pipette_operation_id', 'canonical_request_sha256', 'receipt_id', 'key', 'kind'] as const;
it.each([false, true])('raw pending null outcome retains owner; missing fails closed (%s)', async (missing) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const raw: BioXpDirectLiquidLookup = JSON.parse(pendingExport);
    const nullableOutcome: NonNullable<BioXpDirectLiquidLookup['record']>['outcome'] = null;
    expect(raw.record!.outcome).toBe(nullableOutcome);
    const requests: InternalAxiosRequestConfig[] = [];
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = JSON.parse(pendingExport);
        if (missing) delete data.record.outcome;
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    const owner = current!.submission;
    expect(current!.identityConflict).toBe(missing);
    expect(current!.lookup).toEqual(missing ? null : raw);
    expect(current!.data).toBeUndefined();
    await act(async () => { await vi.advanceTimersByTimeAsync(499); });
    expect(requests.filter(r => r.method === 'get')).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(requests.filter(r => r.method === 'get')).toHaveLength(missing ? 1 : 2);
    await act(async () => current.mutate({ include_data: true }));
    expect(current!.submission).toBe(owner);
    expect(owner!.request).toEqual({ include_data: false });
    expect(Object.isFrozen(owner!.request)).toBe(true);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    for (const request of requests) {
        expect(request.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(request.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});
it.each(cases)('late POST must agree with receipt already learned by GET: %s', async (hook, kind, _path, body) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') return new Promise(resolve => { complete = () => resolve({
            data: { ...originalResult(kind), receipt_id: 'f'.repeat(32) }, status: 200, statusText: 'OK', headers: {}, config }); });
        return { data: lookupFixture(kind, String(config.headers.get('Idempotency-Key'))), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate(body as never));
    await act(async () => current.refreshRecovery());
    expect(current!.data?.receipt_id).toBe('a'.repeat(32));
    await act(async () => complete());
    expect(current!.identityConflict).toBe(true);
    expect(current!.data).toBeUndefined();
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

it.each(cases)('late original POST cannot replace explicit new operation: %s', async (hook, kind, _path, body) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (requests.length === 1) return new Promise(resolve => { complete = () => resolve({
            data: { ...originalResult(kind), receipt_id: 'f'.repeat(32) }, status: 200, statusText: 'OK', headers: {}, config }); });
        return { data: originalResult(kind), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate(body as never));
    const first = current!.submission;
    await act(async () => current.newOperation(body as never));
    expect(current!.data?.receipt_id).toBe('a'.repeat(32));
    const second = current!.submission;
    await act(async () => complete());
    expect(current!.submission).toBe(second);
    expect(current!.retainedHistory).toEqual([first]);
    expect(current!.data?.receipt_id).toBe('a'.repeat(32));
    expect(requests.map(r => r.method)).toEqual(['post', 'post']);
    client.clear();
});

it.each(cases.flatMap(c => driftFields.map(field => [...c, field] as const)))('rejects learned identity drift: %s %s %s %s %s', async (hook, kind, _path, body, field) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let changed = false;
    const requests: InternalAxiosRequestConfig[] = [];
    const uuid = vi.spyOn(crypto, 'randomUUID');
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const value = lookupFixture(kind, String(config.headers.get('Idempotency-Key')));
        if (changed) {
            if (field === 'body') value.record!.requested_inputs = kind === 'readback' ? { include_data: true } : { operation: 'detect_fluid', fluid_class: 'TC', home_z_after: true };
            else if (field === 'receipt_id') value.record!.result!.receipt_id = 'e'.repeat(32);
            else if (field === 'key') value.idempotency_key = 'different:key';
            else if (field === 'kind') value.request_kind = kind === 'readback' ? 'application_plan' : 'readback';
            else value.record![field] = 'e'.repeat(field === 'canonical_request_sha256' ? 64 : 32);
        }
        return { data: value, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate(body as never));
    await act(async () => { await vi.waitFor(() => expect(current.data?.receipt_id).toBe('a'.repeat(32))); });
    const original = current!.submission;
    changed = true;
    await act(async () => current.refreshRecovery());
    expect(current!.identityConflict).toBe(true);
    expect(current!.data).toBeUndefined();
    expect(current!.submission).toBe(original);
    await act(async () => current.refreshRecovery());
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(requests.filter(r => r.method === 'get')).toHaveLength(2);
    expect(uuid).toHaveBeenCalledTimes(1);
    client.clear();
});

it.each(cases)('all lookup states remain GET-only; pending polls at 500ms: %s', async (hook, kind, _path, body) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let state: BioXpDirectLiquidLookup['lookup_state'] = 'pending';
    const requests: InternalAxiosRequestConfig[] = [];
    const uuid = vi.spyOn(crypto, 'randomUUID');
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        return { data: lookupFixture(kind, String(config.headers.get('Idempotency-Key')), state),
            status: state === 'conflict' ? 409 : state === 'unavailable' ? 503 : 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate(body as never));
    expect(current!.lookup?.lookup_state).toBe('pending');
    await act(async () => { await vi.advanceTimersByTimeAsync(499); });
    expect(requests.filter(r => r.method === 'get')).toHaveLength(1);
    state = 'incomplete';
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(current!.lookup?.lookup_state).toBe('incomplete');
    expect(requests.filter(r => r.method === 'get')).toHaveLength(2);
    const query = client.getQueryCache().getAll()[0];
    expect(query.options).toMatchObject({ retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, refetchIntervalInBackground: false });
    for (const next of ['unknown', 'unavailable', 'conflict', 'resolved'] as const) {
        state = next;
        await act(async () => current.refreshRecovery());
        expect(current!.lookup?.lookup_state).toBe(next);
        expect(Boolean(current!.data)).toBe(next === 'resolved');
        const count = requests.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        expect(requests).toHaveLength(count);
    }
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(uuid).toHaveBeenCalledTimes(1);
    client.clear();
});

it.each(cases)('retains default identity before POST and recovers only by GET: %s', async (hook, kind, path, body) => {
    const requests: InternalAxiosRequestConfig[] = [];
    const uuid = vi.spyOn(crypto, 'randomUUID');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const mounted = { result: { current: null as unknown as ReturnType<typeof hook> }, rerender: async ({ generation }: { generation: number }) => {
        await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { generation }))));
    } };
    function Harness({ generation }: { generation: number }) { mounted.result.current = hook(generation); return null; }
    root = createRoot(document.createElement('div'));
    await mounted.rerender({ generation: 77 });
    api.defaults.adapter = async (config) => {
        requests.push(config);
        if (config.method === 'post') {
            expect(mounted.result.current.submission?.idempotencyKey).toBe(config.headers.get('Idempotency-Key'));
            throw new Error('lost response without Axios config');
        }
        return { data: { schema: 'bioxp.direct-liquid.lookup.v1', request_kind: kind,
            idempotency_key: config.headers.get('Idempotency-Key'), lookup_state: 'unknown',
            reason: 'identity_not_found', record: null, retry_forbidden: true, live_query_performed: false },
            status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => mounted.result.current.mutate(body as never));
    await act(async () => { await vi.waitFor(() => expect(mounted.result.current.lookup?.lookup_state).toBe('unknown')); });
    const original = mounted.result.current.submission;
    expect(original).toMatchObject({ requestKind: kind, expectedConnectionGeneration: 77 });
    expect(Object.isFrozen(original)).toBe(true);
    expect(Object.isFrozen(original?.request)).toBe(true);
    await act(async () => { await mounted.result.current.refreshRecovery(); });
    expect(mounted.result.current.submission).toBe(original);
    expect(uuid).toHaveBeenCalledTimes(1);
    expect(requests.filter(r => r.method === 'post').map(r => [r.url, JSON.parse(r.data), r.params]))
        .toEqual([[path, body, { expected_connection_generation: 77 }]]);
    expect(requests.filter(r => r.method === 'get')).toHaveLength(2);
    for (const r of requests.filter(r => r.method === 'get')) {
        expect(r.url).toBe('/api/bioxp/operator-controls/pipettes/requests');
        expect(r.headers.get('Idempotency-Key')).toBe(original?.idempotencyKey);
        expect(r.params).toEqual({ request_kind: kind, expected_connection_generation: 77 });
    }
    await mounted.rerender({ generation: 78 });
    expect(mounted.result.current.detached).toBe(true);
    expect(mounted.result.current.submission).toBe(original);
    await act(async () => { await mounted.result.current.refreshRecovery(); });
    expect(requests).toHaveLength(3);
    await act(async () => mounted.result.current.newOperation(body as never));
    expect(mounted.result.current.retainedHistory).toEqual([original]);
    expect(mounted.result.current.submission?.expectedConnectionGeneration).toBe(78);
    expect(mounted.result.current.submission?.idempotencyKey).not.toBe(original?.idempotencyKey);
    expect(uuid).toHaveBeenCalledTimes(2);
    client.clear();
});

it.each(cases.flatMap(c => ['disconnect', 'generation'].map(change => [...c, change] as const)))('late POST cannot bypass detached owner: %s %s %s %s %s', async (hook, _kind, _path, body, change) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: (value: AxiosResponse<unknown>) => void;
    let config!: InternalAxiosRequestConfig;
    const requests: InternalAxiosRequestConfig[] = [];
    let generation = 77;
    function Harness({ connected }: { connected: boolean }) { current = hook(generation, connected); return null; }
    root = createRoot(document.createElement('div'));
    const render = async (connected: boolean) => { await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { connected })))); };
    await render(true);
    expect(current!).not.toHaveProperty('mutateAsync');
    expect(current!).not.toHaveProperty('reset');
    api.defaults.adapter = (c) => { config = c; requests.push(c); return new Promise(resolve => { complete = resolve; }); };
    await act(async () => current.mutate(body as never));
    const original = current!.submission;
    if (change === 'generation') { generation = 78; await render(true); generation = 77; }
    else await render(false);
    await render(true);
    expect(current!.detached).toBe(true);
    await act(async () => complete({ data: { receipt_id: 'a'.repeat(32) }, status: 200, statusText: 'OK', headers: {}, config }));
    expect(current!.data).toBeUndefined();
    expect(current!.submission).toBe(original);
    await act(async () => current.refreshRecovery());
    expect(requests).toHaveLength(1);
    client.clear();
});

it.each(cases.flatMap(c => ['disconnect', 'generation'].map(change => [...c, change] as const)))('late GET stays detached: %s %s %s %s %s', async (hook, kind, _path, body, change) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: () => void;
    let generation = 77;
    function Harness({ connected }: { connected: boolean }) { current = hook(generation, connected); return null; }
    root = createRoot(document.createElement('div'));
    const render = async (connected: boolean) => { await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { connected })))); };
    api.defaults.adapter = async (config) => {
        if (config.method === 'post') throw new Error('lost');
        return new Promise(resolve => { complete = () => resolve({ data: {
            schema: 'bioxp.direct-liquid.lookup.v1', request_kind: kind, idempotency_key: config.headers.get('Idempotency-Key'),
            lookup_state: 'unknown', reason: 'identity_not_found', record: null, retry_forbidden: true, live_query_performed: false,
        }, status: 200, statusText: 'OK', headers: {}, config }); });
    };
    await render(true);
    await act(async () => current.mutate(body as never));
    await act(async () => { await vi.waitFor(() => expect(complete).toBeTypeOf('function')); });
    if (change === 'generation') { generation = 78; await render(true); generation = 77; }
    else await render(false);
    await render(true);
    await act(async () => complete());
    expect(current!.lookup).toBeNull();
    expect(current!.detached).toBe(true);
    client.clear();
});

it.each(cases)('explicit new operation cannot reuse any retained key: %s', async (hook, _kind, _path, body) => {
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = () => new Promise(() => {});
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...body, idempotencyKey: 'retained:key-one' } as never));
    const first = current!.submission;
    await act(async () => { expect(() => current.newOperation({ ...body, idempotencyKey: 'retained:key-one' } as never)).toThrow(); });
    expect(current!.submission).toBe(first);
    await act(async () => current.newOperation({ ...body, idempotencyKey: 'retained:key-two' } as never));
    await act(async () => { expect(() => current.newOperation({ ...body, idempotencyKey: 'retained:key-one' } as never)).toThrow(); });
    expect(current!.retainedHistory).toEqual([first]);
    client.clear();
});

it('panel edit preserves raw pending null-outcome original body', async () => {
    vi.useFakeTimers();
    const raw: BioXpDirectLiquidLookup = JSON.parse(pendingExport);
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = config.url?.endsWith('/requests') ? JSON.parse(pendingExport) : { dependency_blockers: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Read live hardware')!.click());
    expect(container.textContent).toContain('Lookup: pending');
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.textContent).toContain('Lookup: pending');
    const posts = requests.filter(r => r.method === 'post');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].data)).toEqual({ include_data: false });
    const lookups = requests.filter(r => r.url?.endsWith('/requests'));
    expect(lookups).toHaveLength(2);
    for (const request of [...posts, ...lookups]) {
        expect(request.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(request.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});

it.each([['readback', 'Read live hardware'], ['application_plan', 'Build no-motion plan']] as const)('panel form edits cannot rewrite %s recovery', async (kind, label) => {
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    const uuid = vi.spyOn(crypto, 'randomUUID');
    let resolved = false;
    api.defaults.adapter = async (config) => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = config.url?.endsWith('/requests') ? lookupFixture(kind, String(config.headers.get('Idempotency-Key')), resolved ? 'resolved' : 'unknown') : { dependency_blockers: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    const selectValue = async (index: number, value: string) => { await act(async () => {
        const select = container.querySelectorAll('select')[index];
        select.value = value; select.dispatchEvent(new Event('change', { bubbles: true }));
    }); };
    if (kind === 'application_plan') { await selectValue(0, 'detect_fluid'); await selectValue(1, 'RC'); }
    const button = [...container.querySelectorAll('button')].find(b => b.textContent === label)!;
    await act(async () => button.click());
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Lookup: unknown')); });
    const post = requests.find(r => r.method === 'post')!;
    const originalKey = post.headers.get('Idempotency-Key');
    if (kind === 'readback') {
        await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
        expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
    } else { await selectValue(0, 'plunger_down'); expect(container.querySelector('select')!.value).toBe('plunger_down'); }
    resolved = true;
    const refresh = [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!;
    await act(async () => refresh.click());
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('receipt ' + 'a'.repeat(32))); });
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(JSON.parse(post.data)).toEqual(kind === 'readback' ? { include_data: false } : { operation: 'detect_fluid', fluid_class: 'RC' });
    for (const request of requests.filter(r => r.url?.endsWith('/requests'))) {
        expect(request.headers.get('Idempotency-Key')).toBe(originalKey);
        expect(request.data).toBeUndefined();
    }
    expect(uuid).toHaveBeenCalledTimes(1);
    expect(requests.find(r => r.method === 'post')?.params).toEqual({ expected_connection_generation: 77 });
    expect(container.textContent).toContain('New operation');
    client.clear();
});

// Fresh F33/F37 generation. OFFLINE SYNTHETIC release/authority only:
// no deployed attestation, release authorization, or hardware/physical proof.
// Strings cross the Axios response boundary unchanged; IDs are never rebuilt.
const combinedRaw = import.meta.glob<string>('../fixtures/f33_f37_combined/*.json', { query: '?raw', import: 'default', eager: true });
function combinedText(file: string): string { return combinedRaw['../fixtures/f33_f37_combined/' + file]; }
function combinedLookup(name: string): BioXpDirectLiquidLookup { return JSON.parse(combinedText(name + '-get.json')); }
const combinedManifest = JSON.parse(combinedText('manifest.json')) as {
    wire: { file: string; sha256: string; bytes: number; http_status: number; headers: Record<string, string> }[];
    strict_projections: { file: string; sha256: string; bytes: number }[];
};
function combinedResponse(config: InternalAxiosRequestConfig, name: string, suffix = 'get') {
    const file = name + '-' + suffix + '.json';
    const item = combinedManifest.wire.find(i => i.file === file)!;
    return { data: combinedText(file), status: item.http_status, statusText: 'OK', headers: item.headers, config };
}
const combinedCases = ['readback', 'readback-data', 'plan-detect', 'plan-waste', 'plan-up', 'plan-down', 'plan-tip'] as const;

const specAssociationCases = [
    ['plan-detect', 'fluid_class', 'TC'], ['plan-tip', 'tip_tray', 'different-tray'],
    ['plan-tip', 'tip_well', 'B2'], ['plan-tip', 'tip_type', 99], ['plan-tip', 'tip_location', 3],
    ['plan-tip', 'home_z_after', false], ['plan-up', 'direction', 'down'], ['plan-down', 'direction', 'up'],
    ['plan-tip', 'home_z_after', 1], ['plan-tip', 'tip_type', true], ['plan-tip', 'tip_location', false],
] as const;
it.each(specAssociationCases)('SPEC M1 POST association %s %s', async (name, field, changed) => {
    const raw = combinedLookup(name);
    const client = new QueryClient();
    let current: ReturnType<typeof usePlanBioXpPipetteApplication>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = usePlanBioXpPipetteApplication(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'get') return new Promise(() => {});
        // Explicit negative mutation; never an exact producer export.
        const data = JSON.parse(combinedText(name + '-post.json'));
        expect(data.requested_inputs[field]).not.toBe(changed);
        data.requested_inputs[field] = changed;
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    const owner = current!.submission;
    expect(current!.data).toBeUndefined();
    expect(current!.identityConflict || Boolean(current!.recoveryError) || Boolean(current!.error)).toBe(true);
    await act(async () => current.mutate({ operation: 'plunger_down' }));
    expect(current!.submission).toBe(owner);
    expect(owner!.request).toEqual(raw.record!.requested_inputs);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

const equalityRefreshCases = ['result', 'record'] as const;
it.each(equalityRefreshCases)('SPEC S1 hook %s request-map refresh clears evidence without exception', async location => {
    const raw = combinedLookup('plan-tip');
    const client = new QueryClient();
    let current: ReturnType<typeof usePlanBioXpPipetteApplication>;
    let corrupt = false;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = usePlanBioXpPipetteApplication(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        if (!corrupt) return combinedResponse(config, 'plan-tip');
        // Ordinary negative JSON; ONLY this map value changes, never identity/truth.
        const data = JSON.parse(combinedText('plan-tip-get.json'));
        (location === 'result' ? data.record.result : data.record).requested_inputs.tip_tray = { toString: null };
        return { data: JSON.stringify(data), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody('plan-tip'), idempotencyKey: raw.idempotency_key } as never));
    const owner = current!.submission;
    expect(current!.data).toEqual(raw.record!.result);
    expect(current!.lookup).toEqual(raw);
    corrupt = true;
    await act(async () => current.refreshRecovery());
    expect.soft(current!.recoveryError).toBeNull();
    expect.soft(current!.data).toBeUndefined();
    expect.soft(current!.lookup).toBeNull();
    expect.soft(current!.identityConflict).toBe(true);
    expect.soft(client.getQueryCache().getAll()[0].state.error).toBeNull();
    await act(async () => current.mutate({ operation: 'plunger_down' }));
    expect(current!.submission).toBe(owner);
    expect(owner!.request).toEqual(raw.record!.requested_inputs);
    expect(Object.isFrozen(owner)).toBe(true);
    expect(Object.isFrozen(owner!.request)).toBe(true);
    expect(requests.map(r => r.method)).toEqual(['post', 'get', 'get']);
    expect(JSON.parse(requests[0].data)).toEqual(combinedBody('plan-tip'));
    for (const r of requests) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    // A rejected refresh cannot publish/learn a replacement identity; all IDs above
    // deliberately remain the original IDs so only request-map validation is tested.
    client.clear();
});

it.each(equalityRefreshCases)('SPEC S1 panel %s request-map refresh removes stale plan and receipt', async location => {
    const raw = combinedLookup('plan-tip');
    const request = combinedBody('plan-tip') as Record<string, unknown>;
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    let corrupt = false;
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        if (!config.url?.endsWith('/requests')) return { data: { dependency_blockers: [] }, status: 200, statusText: 'OK', headers: {}, config };
        if (!corrupt) return combinedResponse(config, 'plan-tip');
        const data = JSON.parse(combinedText('plan-tip-get.json'));
        (location === 'result' ? data.record.result : data.record).requested_inputs.tip_tray = { toString: null };
        return { data: JSON.stringify(data), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    await act(async () => {
        const select = container.querySelector('select')!; select.value = 'load_tip';
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    for (const [selector, value] of [['input[placeholder="Tip tray"]', request.tip_tray], ['input[placeholder="Tip well"]', request.tip_well], ['input[type="number"]', request.tip_type]] as const) {
        await act(async () => {
            const input = container.querySelector<HTMLInputElement>(selector)!;
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, String(value));
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Build no-motion plan')!.click());
    expect(container.textContent).toContain('Plan only: load_tip');
    expect(container.textContent).toContain('Lookup: resolved');
    expect(container.textContent).toContain('receipt ' + raw.record!.result!.receipt_id);
    corrupt = true;
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!.click());
    expect.soft(container.textContent).not.toContain('Plan only:');
    expect.soft(container.textContent).not.toContain('Lookup: resolved');
    expect.soft(container.textContent).not.toContain(raw.record!.result!.receipt_id);
    expect.soft(container.textContent).not.toContain(raw.record!.command_id);
    expect.soft(container.textContent).not.toContain(raw.record!.pipette_operation_id);
    expect.soft(container.textContent).toContain('Lookup: identity conflict');
    const query = client.getQueryCache().getAll().find(q => q.queryKey[1] === 'direct-liquid-request' && q.queryKey[3] === 'application_plan')!;
    expect.soft(query.state.error).toBeNull();
    expect(container.textContent).toContain('Request ' + raw.idempotency_key + ' · connection 77');
    const direct = requests.filter(r => r.method === 'post' || r.url?.endsWith('/requests'));
    expect(direct.map(r => r.method)).toEqual(['post', 'get', 'get']);
    expect(JSON.parse(direct[0].data)).toEqual(request);
    expect(uuid).toHaveBeenCalledTimes(1);
    for (const r of direct) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    client.clear();
});

it.each(combinedCases)('SPEC S1 valid %s flat mapping and reordered keys preserve defaults', async name => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        if (config.method === 'post') throw new Error('lost');
        const data = JSON.parse(combinedText(name + '-get.json'));
        const reverse = (v: object) => Object.fromEntries(Object.entries(v).reverse());
        data.record.requested_inputs = reverse(data.record.requested_inputs);
        if (data.record.result.requested_inputs) data.record.result.requested_inputs = reverse(data.record.result.requested_inputs);
        return { data: JSON.stringify(data), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    const body = { ...combinedBody(name) };
    if ('home_z_after' in body) delete body.home_z_after;
    if ('include_data' in body && body.include_data === false) delete body.include_data;
    await act(async () => current.mutate({ ...body, idempotencyKey: raw.idempotency_key } as never));
    expect(current!.lookup).toEqual(raw);
    expect(current!.data).toEqual(raw.record!.result);
    expect(current!.submission!.request).toEqual(raw.record!.requested_inputs);
    expect(current!.identityConflict).toBe(false);
    expect(current!.recoveryError).toBeNull();
    client.clear();
});

it.each(equalityRefreshCases.flatMap(location => [['array', ['tray']], ['object', {}]].map(([fault, value]) => [location, fault, value] as const)))('SPEC S1 %s wrong %s map value rejects', async (location, _fault, value) => {
    const raw = combinedLookup('plan-tip');
    const client = new QueryClient();
    let current: ReturnType<typeof usePlanBioXpPipetteApplication>;
    function Harness() { current = usePlanBioXpPipetteApplication(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        if (config.method === 'post') throw new Error('lost');
        const data = JSON.parse(combinedText('plan-tip-get.json'));
        (location === 'result' ? data.record.result : data.record).requested_inputs.tip_tray = value;
        return { data: JSON.stringify(data), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody('plan-tip'), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.data).toBeUndefined(); expect(current!.lookup).toBeNull();
    expect(current!.identityConflict).toBe(true); expect(current!.recoveryError).toBeNull();
    client.clear();
});

const residualOwnerCases = [['post', 'array'], ['get', 'array'], ['refresh', 'array'], ['refresh', 'object']] as const;
it.each(residualOwnerCases)('SPEC R2 owner %s %s rejects without stale evidence', async (method, fault) => {
    const raw = combinedLookup('plan-detect');
    const client = new QueryClient();
    let current: ReturnType<typeof usePlanBioXpPipetteApplication>;
    let corrupt = method !== 'refresh';
    let completeGet!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = usePlanBioXpPipetteApplication(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (method !== 'post' && config.method === 'post') throw new Error('lost');
        if (method === 'post' && config.method === 'get') return new Promise(resolve => {
            completeGet = () => resolve(combinedResponse(config, 'plan-detect'));
        });
        const result = JSON.parse(combinedText('plan-detect-strict-result.json'));
        if (corrupt) result.steps[0].owner = fault === 'array' ? ['deck'] : { toString: null };
        if (method === 'post') result.receipt_id = 'f'.repeat(32);
        const value = combinedLookup('plan-detect'); value.record!.result = result;
        return { data: JSON.stringify(method === 'post' ? result : value), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody('plan-detect'), idempotencyKey: raw.idempotency_key } as never));
    const owner = current!.submission;
    if (method === 'refresh') {
        expect(current!.data).toEqual(raw.record!.result);
        expect(current!.lookup).toEqual(raw);
        corrupt = true;
        await act(async () => current.refreshRecovery());
    }
    expect(current!.data).toBeUndefined();
    expect(current!.lookup).toBeNull();
    expect(current!.recoveryError).toBeNull(); // malformed JSON must return false, not throw
    if (method === 'post') {
        expect(current!.error).toBeTruthy();
        await act(async () => completeGet());
        // The invalid POST's alternate receipt must not have been learned.
        expect(current!.identityConflict).toBe(false);
        expect(current!.data).toEqual(raw.record!.result);
    } else expect(current!.identityConflict).toBe(true);
    await act(async () => current.mutate({ operation: 'plunger_down' }));
    expect(current!.submission).toBe(owner);
    expect(owner!.request).toEqual(raw.record!.requested_inputs);
    expect(Object.isFrozen(owner!.request)).toBe(true);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(JSON.parse(requests[0].data)).toEqual(combinedBody('plan-detect'));
    for (const r of requests) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    client.clear();
});

it.each(residualOwnerCases)('SPEC R2 panel owner %s %s never displays malformed plan', async (method, fault) => {
    const raw = combinedLookup('plan-detect');
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    let corrupt = method !== 'refresh';
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post' && method !== 'post') throw new Error('lost');
        if (config.method === 'get' && !config.url?.endsWith('/requests')) return { data: { dependency_blockers: [] }, status: 200, statusText: 'OK', headers: {}, config };
        if (config.method === 'get' && method === 'post') return new Promise(() => {});
        const result = JSON.parse(combinedText('plan-detect-strict-result.json'));
        if (corrupt) result.steps[0].owner = fault === 'array' ? ['deck'] : { toString: null };
        const data = combinedLookup('plan-detect'); data.record!.result = result;
        return { data: JSON.stringify(method === 'post' ? result : data), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    const selectValue = async (index: number, value: string) => { await act(async () => {
        const select = container.querySelectorAll('select')[index]; select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }); };
    await selectValue(0, 'detect_fluid'); await selectValue(1, 'RC');
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Build no-motion plan')!.click());
    if (method === 'refresh') {
        expect(container.textContent).toContain('Plan only: detect_fluid');
        corrupt = true;
        await selectValue(0, 'move_to_waste');
        await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!.click());
    }
    expect(container.textContent).not.toContain('Plan only:');
    expect(container.textContent).not.toContain('Lookup: resolved');
    expect(container.textContent).not.toContain('receipt ' + raw.record!.result!.receipt_id);
    expect(container.textContent).toContain(raw.idempotency_key);
    const posts = requests.filter(r => r.method === 'post');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].data)).toEqual(combinedBody('plan-detect'));
    for (const r of requests.filter(r => r.method === 'post' || r.url?.endsWith('/requests'))) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    client.clear();
});

const specResultMutations = ['receipt-only', 'missing-hardware', 'missing-channels', 'wrong-family', 'receipt-truth', 'physical-truth', 'channel-shape'] as const;
it.each(['post', 'get', 'refresh'].flatMap(method => specResultMutations.map(fault => [method, fault] as const)))('SPEC M2 %s rejects %s before publication', async (method, fault) => {
    const raw = combinedLookup('readback');
    const client = new QueryClient();
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    let corrupt = method !== 'refresh';
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (method !== 'post' && config.method === 'post') throw new Error('lost');
        if (method === 'post' && config.method === 'get') return new Promise(() => {});
        const value = combinedLookup('readback');
        let result = JSON.parse(combinedText('readback-strict-result.json'));
        if (corrupt) {
            if (fault === 'receipt-only') result = { receipt_id: result.receipt_id };
            if (fault === 'missing-hardware') delete result.hardware_truth_level;
            if (fault === 'missing-channels') delete result.channels;
            if (fault === 'wrong-family') result = JSON.parse(combinedText('plan-detect-strict-result.json'));
            if (fault === 'receipt-truth') result.receipt_truth.delivery_verified = 'false';
            if (fault === 'physical-truth') result.physical_effect_verified = true;
            if (fault === 'channel-shape') result.channels[0].firmware = null;
        }
        value.record!.result = result;
        return { data: method === 'post' ? result : value, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    const owner = current!.submission;
    if (method === 'refresh') {
        expect(current!.data).toEqual(raw.record!.result);
        corrupt = true;
        await act(async () => current.refreshRecovery());
    }
    expect(current!.data).toBeUndefined();
    expect(current!.lookup).toBeNull();
    expect(current!.identityConflict || Boolean(current!.recoveryError) || Boolean(current!.error)).toBe(true);
    expect(current!.submission).toBe(owner);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

it.each(['pending-result', 'unknown-record', 'missing-record', 'reason', 'status', 'terminal-rows', 'missing-outcome', 'failure-null'].flatMap(fault => ['readback', 'plan-detect'].map(name => [name, fault] as const)))('SPEC M2 lookup consistency %s %s', async (name, fault) => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = JSON.parse(combinedText(name + '-get.json'));
        if (fault === 'pending-result') { data.lookup_state = 'pending'; data.reason = 'nonterminal'; data.record.command_status = data.record.pipette_status = 'running'; }
        if (fault === 'unknown-record') { data.lookup_state = 'unknown'; data.reason = 'identity_not_found'; }
        if (fault === 'missing-record') data.record = null;
        if (fault === 'reason') data.reason = 'nonterminal';
        if (fault === 'terminal-rows') data.record.pipette_status = 'running';
        if (fault === 'missing-outcome') delete data.record.outcome;
        if (fault === 'failure-null') { data.record.command_status = data.record.pipette_status = 'failed'; data.record.result = null; }
        return { data, status: fault === 'status' ? 503 : 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.identityConflict).toBe(fault !== 'failure-null');
    expect(current!.data).toBeUndefined();
    expect(current!.lookup?.lookup_state).toBe(fault === 'failure-null' ? 'resolved' : undefined);
    expect(current!.submission!.request).toEqual(raw.record!.requested_inputs);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

const specRequiredResultFields = ['readback', 'plan-detect'].flatMap(name =>
    Object.keys(JSON.parse(combinedText(name + '-strict-result.json'))).filter(field => field !== 'effective_inputs')
        .flatMap(field => ['post', 'get'].map(method => [name, field, method] as const)));
it.each(specRequiredResultFields)('SPEC M2 required %s %s %s', async (name, field, method) => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (method === 'get' && config.method === 'post') throw new Error('lost');
        if (method === 'post' && config.method === 'get') return new Promise(() => {});
        const data = JSON.parse(combinedText(name + '-strict-result.json'));
        delete data[field];
        const lookup = combinedLookup(name); lookup.record!.result = data;
        return { data: method === 'post' ? data : lookup, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.data).toBeUndefined();
    expect(current!.lookup).toBeNull();
    expect(current!.submission!.request).toEqual(raw.record!.requested_inputs);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

it.each(specAssociationCases.flatMap(row => [false, true].map(mutated => [...row, mutated] as const)))('SPEC M1 panel %s %s changed %s negative %s', async (name, field, changed, mutated) => {
    const raw = combinedLookup(name);
    const request = combinedBody(name) as Record<string, unknown>;
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') {
            const data = JSON.parse(combinedText(name + '-post.json'));
            if (mutated) data.requested_inputs[field] = changed;
            return { data, status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.url?.endsWith('/requests')) return new Promise(() => {});
        return { data: { dependency_blockers: [] }, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    const selectValue = async (index: number, value: string) => { await act(async () => {
        const select = container.querySelectorAll('select')[index]; select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }); };
    await selectValue(0, String(request.operation));
    if (name === 'plan-detect') await selectValue(1, String(request.fluid_class));
    if (name === 'plan-tip') {
        for (const [selector, value] of [['input[placeholder="Tip tray"]', request.tip_tray], ['input[placeholder="Tip well"]', request.tip_well], ['input[type="number"]', request.tip_type]] as const) {
            await act(async () => {
                const input = container.querySelector<HTMLInputElement>(selector)!;
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, String(value));
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }
    }
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Build no-motion plan')!.click());
    const posts = requests.filter(r => r.method === 'post');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].data)).toEqual(request);
    expect(container.textContent!.includes('Plan only:')).toBe(!mutated);
    expect(container.textContent).toContain(raw.idempotency_key);
    if (mutated) expect(container.textContent).toContain('Invalid or mismatched');
    else { expect(container.textContent).toContain('motion commanded false'); expect(container.textContent).toContain('physical effect verified false'); }
    await selectValue(0, 'move_to_waste');
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!.click());
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    for (const r of requests.filter(r => r.method === 'post' || r.url?.endsWith('/requests'))) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});

it.each(['post', 'get', 'refresh'].flatMap(method => ['receipt-only', 'missing-channels', 'physical-truth'].map(fault => [method, fault] as const)))('SPEC M2 panel %s %s cannot render unsafe evidence', async (method, fault) => {
    const raw = combinedLookup('readback');
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    let corrupt = method !== 'refresh';
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post' && method !== 'post') throw new Error('lost');
        if (config.method === 'get' && !config.url?.endsWith('/requests')) return { data: { dependency_blockers: [] }, status: 200, statusText: 'OK', headers: {}, config };
        if (config.method === 'get' && method === 'post') return new Promise(() => {});
        let result = JSON.parse(combinedText('readback-strict-result.json'));
        if (corrupt) {
            if (fault === 'receipt-only') result = { receipt_id: result.receipt_id };
            if (fault === 'missing-channels') delete result.channels;
            if (fault === 'physical-truth') result.physical_effect_verified = true;
        }
        const data = combinedLookup('readback'); data.record!.result = result;
        return { data: method === 'post' ? result : data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Read live hardware')!.click());
    if (method === 'refresh') {
        expect(container.textContent).toContain('receipt ' + raw.record!.result!.receipt_id);
        corrupt = true;
        await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!.click());
    }
    expect(container.textContent).not.toContain('receipt ' + raw.record!.result!.receipt_id);
    expect(container.textContent).not.toContain('Live hardware query ·');
    expect(container.textContent).toContain(raw.idempotency_key);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

it.each(combinedCases)('SPEC M2 public projected %s POST needs no private metadata', async name => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        return { data: combinedText(name + '-strict-result.json'), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.data).toEqual(raw.record!.result);
    expect(requests.map(r => r.method)).toEqual(['post']);
    client.clear();
});

it.each(specAssociationCases)('SPEC M1 rejected %s %s POST reconciles without learning its receipt', async (name, field, changed) => {
    const raw = combinedLookup(name);
    const client = new QueryClient();
    let current: ReturnType<typeof usePlanBioXpPipetteApplication>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = usePlanBioXpPipetteApplication(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'get') return combinedResponse(config, name);
        const data = JSON.parse(combinedText(name + '-post.json'));
        data.requested_inputs[field] = changed;
        data.receipt_id = 'f'.repeat(32);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.error).toBeTruthy();
    expect(current!.identityConflict).toBe(false);
    expect(current!.data).toEqual(raw.record!.result);
    expect(current!.submission!.request).toEqual(raw.record!.requested_inputs);
    expect(requests.map(r => r.method)).toEqual(['post', 'get']);
    for (const r of requests) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});

it('SPEC M3 manual refresh shares deferred pending timer GET', async () => {
    vi.useFakeTimers();
    const raw = combinedLookup('pending');
    const client = new QueryClient();
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    const completes: (() => void)[] = [];
    const requests: InternalAxiosRequestConfig[] = [];
    let gets = 0; let active = 0; let maxActive = 0;
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        gets++;
        if (gets === 1) return combinedResponse(config, 'pending');
        active++; maxActive = Math.max(maxActive, active);
        return new Promise(resolve => { completes.push(() => {
            active--; resolve(combinedResponse(config, 'pending'));
        }); });
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    const owner = current!.submission;
    expect(current!.lookup).toEqual(raw);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(gets).toBe(2);
    await act(async () => { void current.refreshRecovery(); void current.refreshRecovery(); void current.refreshRecovery(); });
    // Resolve every actual outstanding response, newest first, even on the RED path.
    await act(async () => { for (const complete of [...completes].reverse()) complete(); });
    expect(maxActive).toBe(1);
    expect(gets).toBe(2);
    expect(current!.lookup).toEqual(raw);
    expect(current!.submission).toBe(owner);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});
it('fresh 23-artifact fixture set is exact and hash-bound to the synthetic exporter', () => {
    expect(createHash('sha256').update(combinedText('manifest.json')).digest('hex')).toBe('5cf6125b12cdd341bd83fcff4c0b9d2b00086c152b8eb9ae4f6e1680dfec575a');
    expect(combinedManifest.wire).toHaveLength(16);
    expect(combinedManifest.strict_projections).toHaveLength(7);
    const artifacts = [...combinedManifest.wire, ...combinedManifest.strict_projections];
    expect(new Set(artifacts.map(i => i.file)).size).toBe(23);
    for (const item of artifacts) {
        const raw = combinedText(item.file);
        expect(new TextEncoder().encode(raw).byteLength).toBe(item.bytes);
        expect(createHash('sha256').update(raw).digest('hex')).toBe(item.sha256);
    }
    for (const name of combinedCases) expect(JSON.parse(combinedText(name + '-strict-result.json'))).toEqual(combinedLookup(name).record!.result);
});

function combinedBody(name: string) {
    const record = combinedLookup(name).record!;
    const body = { ...record.requested_inputs };
    if ('operation' in body && body.operation !== 'load_tip') delete body.home_z_after;
    return body;
}

it.each(combinedCases)('fresh raw %s preserves full stored identity, result and truth through mounted owner', async name => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost response: offline fixture');
        return combinedResponse(config, name);
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.identityConflict).toBe(false);
    expect(current!.lookup).toEqual(raw);
    expect(current!.data).toEqual(raw.record!.result);
    const original = current!.submission;
    expect(original!.request).toEqual(raw.record!.requested_inputs);
    expect(original!.expectedConnectionGeneration).toBe(77);
    expect(current!.lookup!.record!.connection_generation).toBe(raw.record!.connection_generation);
    expect(current!.data!.receipt_truth).toEqual(raw.record!.result!.receipt_truth);
    expect(current!.data!.physical_effect_verified).toBe(false);
    if (raw.request_kind === 'application_plan') {
        expect(raw.record).toMatchObject({ command_status: 'failed', pipette_status: 'failed', outcome: 'completed', result: { ok: true, mode: 'plan_only', execution_admitted: false, motion_commanded: false, liquid_mutation_commanded: false } });
    } else expect(current!.data).toMatchObject({ hardware_truth_level: 'hardware_query' });
    await act(async () => current.mutate({ include_data: true, operation: 'plunger_down' } as never));
    expect(current!.submission).toBe(original);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    for (const r of requests) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    expect(JSON.parse(requests[0].data)).toEqual(combinedBody(name));
    client.clear();
});

it.each(['readback', 'plan-detect'])('fresh %s retains original history; late raw POST cannot replace new operation', async name => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (requests.length === 1) return new Promise(resolve => { complete = () => resolve(combinedResponse(config, name, 'post')); });
        return new Promise(() => {});
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    const original = current!.submission;
    await act(async () => current.newOperation({ ...combinedBody(name), idempotencyKey: 'offline:explicit-new-operation' } as never));
    const second = current!.submission;
    await act(async () => complete());
    expect(current!.submission).toBe(second);
    expect(current!.retainedHistory).toEqual([original]);
    expect(original!.request).toEqual(raw.record!.requested_inputs);
    expect(current!.data).toBeUndefined();
    expect(requests.map(r => r.method)).toEqual(['post', 'post']);
    client.clear();
});

it.each(['readback', 'plan-detect'].flatMap(name => ['post', 'get'].flatMap(method => ['generation', 'disconnect'].map(change => [name, method, change] as const))))('fresh %s late %s stays detached after %s', async (name, method, change) => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let complete!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness({ generation, connected }: { generation: number; connected: boolean }) { current = hook(generation, connected); return null; }
    root = createRoot(document.createElement('div'));
    const render = async (generation: number, connected: boolean) => { await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { generation, connected })))); };
    api.defaults.adapter = async config => {
        requests.push(config);
        if (method === 'get' && config.method === 'post') throw new Error('lost');
        return new Promise(resolve => { complete = () => resolve(combinedResponse(config, name, method)); });
    };
    await render(77, true);
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    const original = current!.submission;
    await render(change === 'generation' ? 78 : 77, change !== 'disconnect');
    await render(77, true);
    await act(async () => complete());
    expect(current!.detached).toBe(true);
    expect(current!.lookup).toBeNull();
    expect(current!.data).toBeUndefined();
    expect(current!.submission).toBe(original);
    const count = requests.length;
    await act(async () => current.refreshRecovery());
    expect(requests).toHaveLength(count);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});

it.each(['readback', 'plan-detect'].flatMap(name => driftFields.map(field => [name, field] as const)))('fresh %s explicitly mutated counterexample rejects %s drift', async (name, field) => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    let changed = false;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        if (!changed) return combinedResponse(config, name);
        // Explicit negative mutation, NOT an exact positive robot export.
        const value = combinedLookup(name);
        if (field === 'body') value.record!.requested_inputs = { include_data: true };
        else if (field === 'receipt_id') value.record!.result!.receipt_id = 'e'.repeat(32);
        else if (field === 'key') value.idempotency_key = 'different:key';
        else if (field === 'kind') value.request_kind = value.request_kind === 'readback' ? 'application_plan' : 'readback';
        else value.record![field] = 'e'.repeat(field === 'canonical_request_sha256' ? 64 : 32);
        return { data: JSON.stringify(value), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.data).toEqual(raw.record!.result);
    const original = current!.submission;
    changed = true;
    await act(async () => current.refreshRecovery());
    expect(current!.identityConflict).toBe(true);
    expect(current!.data).toBeUndefined();
    expect(current!.submission).toBe(original);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    client.clear();
});
it.each([false, true])('fresh raw pending null outcome retains owner; missing fails closed (%s)', async (missing) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const raw: BioXpDirectLiquidLookup = JSON.parse(combinedText('pending-get.json'));
    const nullableOutcome: NonNullable<BioXpDirectLiquidLookup['record']>['outcome'] = null;
    expect(raw.record!.outcome).toBe(nullableOutcome);
    const requests: InternalAxiosRequestConfig[] = [];
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = JSON.parse(combinedText('pending-get.json'));
        if (missing) delete data.record.outcome;
        return { data: missing ? JSON.stringify(data) : combinedText('pending-get.json'), status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    const owner = current!.submission;
    expect(current!.identityConflict).toBe(missing);
    expect(current!.lookup).toEqual(missing ? null : raw);
    expect(current!.data).toBeUndefined();
    await act(async () => { await vi.advanceTimersByTimeAsync(499); });
    expect(requests.filter(r => r.method === 'get')).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(requests.filter(r => r.method === 'get')).toHaveLength(missing ? 1 : 2);
    await act(async () => current.mutate({ include_data: true }));
    expect(current!.submission).toBe(owner);
    expect(owner!.request).toEqual({ include_data: false });
    expect(Object.isFrozen(owner!.request)).toBe(true);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    for (const request of requests) {
        expect(request.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(request.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});
it('fresh panel edit preserves raw pending null-outcome original body', async () => {
    vi.useFakeTimers();
    const raw: BioXpDirectLiquidLookup = JSON.parse(combinedText('pending-get.json'));
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        const data = config.url?.endsWith('/requests') ? combinedText('pending-get.json') : { dependency_blockers: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Read live hardware')!.click());
    expect(container.textContent).toContain('Lookup: pending');
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.textContent).toContain('Lookup: pending');
    const posts = requests.filter(r => r.method === 'post');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].data)).toEqual({ include_data: false });
    const lookups = requests.filter(r => r.url?.endsWith('/requests'));
    expect(lookups).toHaveLength(2);
    for (const request of [...posts, ...lookups]) {
        expect(request.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(request.params.expected_connection_generation).toBe(77);
    }
    client.clear();
});

it.each([['readback', 'Read live hardware'], ['application_plan', 'Build no-motion plan']] as const)('fresh panel form edits cannot rewrite %s recovery', async (kind, label) => {
    const requests: InternalAxiosRequestConfig[] = [];
    const client = new QueryClient();
    const container = document.createElement('div'); root = createRoot(container);
    const name = kind === 'readback' ? 'readback' : 'plan-detect';
    const raw = combinedLookup(name);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue(raw.idempotency_key as ReturnType<typeof crypto.randomUUID>);
    api.defaults.adapter = async (config) => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        if (config.url?.endsWith('/requests')) return combinedResponse(config, name);
        const data = { dependency_blockers: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(BioXpPipetteControlPanel, { generation: 77 }))));
    const selectValue = async (index: number, value: string) => { await act(async () => {
        const select = container.querySelectorAll('select')[index];
        select.value = value; select.dispatchEvent(new Event('change', { bubbles: true }));
    }); };
    if (kind === 'application_plan') { await selectValue(0, 'detect_fluid'); await selectValue(1, 'RC'); }
    const button = [...container.querySelectorAll('button')].find(b => b.textContent === label)!;
    await act(async () => button.click());
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Lookup: resolved')); });
    const post = requests.find(r => r.method === 'post')!;
    const originalKey = post.headers.get('Idempotency-Key');
    if (kind === 'readback') {
        await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
        expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
    } else { await selectValue(0, 'plunger_down'); expect(container.querySelector('select')!.value).toBe('plunger_down'); }
    const refresh = [...container.querySelectorAll('button')].find(b => b.textContent === 'Refresh stored evidence')!;
    await act(async () => refresh.click());
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('receipt ' + raw.record!.result!.receipt_id)); });
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    expect(JSON.parse(post.data)).toEqual(kind === 'readback' ? { include_data: false } : { operation: 'detect_fluid', fluid_class: 'RC' });
    for (const request of requests.filter(r => r.url?.endsWith('/requests'))) {
        expect(request.headers.get('Idempotency-Key')).toBe(originalKey);
        expect(request.data).toBeUndefined();
    }
    expect(uuid).toHaveBeenCalledTimes(1);
    expect(requests.find(r => r.method === 'post')?.params).toEqual({ expected_connection_generation: 77 });
    expect(container.textContent).toContain('New operation');
    if (kind === 'application_plan') {
        expect(container.textContent).toContain('failed');
        expect(container.textContent).toContain('Plan only: detect_fluid');
        expect(container.textContent).toContain('motion commanded false');
        expect(container.textContent).toContain('physical effect verified false');
    }
    client.clear();
});

it.each(combinedCases)('fresh raw %s POST reaches mounted hook without receipt/source rewriting', async name => {
    const raw = combinedLookup(name);
    const hook = raw.request_kind === 'readback' ? useReadBioXpPipetteReadback : usePlanBioXpPipetteApplication;
    const client = new QueryClient();
    let current: ReturnType<typeof hook>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = hook(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => { requests.push(config); return combinedResponse(config, name, 'post'); };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ ...combinedBody(name), idempotencyKey: raw.idempotency_key } as never));
    expect(current!.data).toEqual(JSON.parse(combinedText(name + '-post.json')));
    expect(current!.data!.receipt_id).toBe(raw.record!.result!.receipt_id);
    expect(current!.data!.receipt_truth).toEqual(raw.record!.result!.receipt_truth);
    expect(current!.data!.physical_effect_verified).toBe(false);
    expect(requests.map(r => r.method)).toEqual(['post']);
    expect(requests[0].params.expected_connection_generation).toBe(77);
    expect(requests[0].headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
    expect(JSON.parse(requests[0].data)).toEqual(combinedBody(name));
    client.clear();
});

it('fresh raw pending poll cannot collide with an in-flight GET or resubmit', async () => {
    vi.useFakeTimers();
    const raw = combinedLookup('pending');
    const client = new QueryClient();
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    let complete!: () => void;
    const requests: InternalAxiosRequestConfig[] = [];
    let gets = 0;
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        gets++;
        if (gets === 1) return combinedResponse(config, 'pending');
        return new Promise(resolve => { complete = () => resolve(combinedResponse(config, 'pending')); });
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    expect(current!.lookup).toEqual(raw);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(gets).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(gets).toBe(2);
    await act(async () => current.mutate({ include_data: true }));
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    await act(async () => complete());
    expect(current!.lookup).toEqual(raw);
    client.clear();
});

it('fresh raw unknown GET remains stored absence, never automatic POST permission', async () => {
    const raw = combinedLookup('unknown');
    const client = new QueryClient();
    let current: ReturnType<typeof useReadBioXpPipetteReadback>;
    const requests: InternalAxiosRequestConfig[] = [];
    function Harness() { current = useReadBioXpPipetteReadback(77); return null; }
    root = createRoot(document.createElement('div'));
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method === 'post') throw new Error('lost');
        return combinedResponse(config, 'unknown');
    };
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Harness))));
    await act(async () => current.mutate({ include_data: false, idempotencyKey: raw.idempotency_key }));
    expect(current!.lookup).toEqual(raw);
    expect(current!.identityConflict).toBe(false);
    expect(current!.data).toBeUndefined();
    const original = current!.submission;
    await act(async () => current.mutate({ include_data: true }));
    await act(async () => current.refreshRecovery());
    expect(current!.submission).toBe(original);
    expect(requests.filter(r => r.method === 'post')).toHaveLength(1);
    for (const r of requests) {
        expect(r.headers.get('Idempotency-Key')).toBe(raw.idempotency_key);
        expect(r.params.expected_connection_generation).toBe(77);
        if (r.method === 'get') expect(r.data).toBeUndefined();
    }
    client.clear();
});
