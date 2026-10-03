import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { BioXpQuickDashboard } from '../../src/components/BioXpQuickDashboard';
import { BioXpPipetteControlPanel } from '../../src/components/BioXpPipetteControlPanel';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { composeBioXpCatalog, useBioXpOperatorControlCatalog } from '../../src/lib/bioxpClient';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
const path = process.env.BMS_CATALOG_SPLIT_WIRE;
const actual = path ? JSON.parse(readFileSync(path, 'utf8')) : null;
const fixture = () => structuredClone(actual ?? {
    metadata: { catalog_view: 'metadata', metadata_revision: 'v1', actions: [{action_id: 'a', inputs: []}], canonical: {catalog_view: 'metadata', metadata_revision: 'v2', actions: []}},
    assessment: {catalog_view: 'assessment', metadata_revision: 'v1', ownership_generation: 1, dashboard: {}, action_states: [{ enabled: true, disabled_reason: null, provider_available: false, dependencies: [{met: false, reason: 'offline'}] }], action_state_indices: [0], canonical: {catalog_view: 'assessment', metadata_revision: 'v2', dashboard: {}, action_states: [], action_state_indices: []}},
});
const updateFixture = (body: ReturnType<typeof fixture>['assessment']) => {
    const { canonical, ...legacy } = body;
    const wrap = (value: object) => ({catalog_view: 'assessment', assessment_revision: 'fixture-baseline', assessment_base: value, assessment_changes: []});
    return {...wrap(legacy), canonical: wrap(canonical)};
};
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

it.skipIf(!actual)('recomposes every actual captured legacy and canonical action without defaults', () => {
    const data = composeBioXpCatalog(actual.metadata, actual.assessment);
    expect(data.actions).toEqual(actual.full.actions);
    expect(data.canonical.actions).toEqual(actual.full.canonical.actions);
    expect(data.dashboard).toEqual(actual.assessment.dashboard);
    expect(data.canonical.dashboard).toEqual(actual.assessment.canonical.dashboard);
});

it.skipIf(!actual)('renders actual full and compact dashboard/pipette views identically, including false readbacks', () => {
    for (const key of ['dashboard', 'canonical'] as const) {
        const oldDashboard = key === 'dashboard' ? actual.full.dashboard : actual.full.canonical.dashboard.telemetry;
        const newDashboard = key === 'dashboard' ? actual.assessment.dashboard : actual.assessment.canonical.dashboard.telemetry;
        const render = (data: typeof oldDashboard) => {
            const client = new QueryClient();
            const result = renderToStaticMarkup(<QueryClientProvider client={client}>
                <BioXpQuickDashboard connected data={data} isLoading={false} error={null} motionControlsAvailable={true}/>
                <BioXpPipetteControlPanel generation={7} connected pipettes={data.pipettes} freshness={data.snapshot.freshness} actions={actual.full.actions}/>
            </QueryClientProvider>);
            client.clear(); return result;
        };
        expect(render(newDashboard)).toEqual(render(oldDashboard));
    }
});

it('polls live state at five seconds, definitions once across Z/draft/remount and anew on reconnect/release', async () => {
    vi.useFakeTimers();
    const wire = fixture();
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    let observed: ReturnType<typeof useBioXpOperatorControlCatalog>;
    function Consumer({generation, z, lifecycle}: {generation:number; z:number; lifecycle:string}) {
        observed = useBioXpOperatorControlCatalog(generation, true, lifecycle, z);
        return <div>{observed.status}{observed.error?.message}{observed.data?.actions.length}</div>;
    }
    let failed = false;
    vi.mocked(api.get).mockImplementation(async (_url, config) => {
        if (failed) throw new Error('offline');
        return {data: structuredClone(config?.params?.view === 'metadata' ? wire.metadata : updateFixture(wire.assessment))};
    });
    const tick = async (ms = 20) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    const render = async (generation=7, z=65000, lifecycle='ready') => { await act(async () => root.render(<QueryClientProvider client={client}><Consumer generation={generation} z={z} lifecycle={lifecycle}/></QueryClientProvider>)); await tick(); };
    const count = (view:string) => vi.mocked(api.get).mock.calls.filter(([,c]) => c?.params?.view === view).length;
    try {
        await render();
        expect(observed!.error?.message).toBeUndefined();
        expect(observed!.isSuccess).toBe(true);
        expect(count('metadata')).toBe(1);
        await tick(60_000);
        expect(count('assessment')).toBe(13);
        expect(count('metadata')).toBe(1);
        await render(7, -2147483648, 'changed');
        expect(vi.mocked(api.get).mock.calls.at(-1)?.[1]?.params).toEqual({view:'assessment', assessment_base:'fixture-baseline', canonical_assessment_base:'fixture-baseline', z_target_steps:-2147483648});
        expect(count('metadata')).toBe(1);
        wire.assessment.action_states[0] = {...wire.assessment.action_states[0], enabled:false, disabled_reason:'provider unavailable', available:false, provider_available:false, dependencies:[{key:'provider_available', met:false, reason:'offline'}], snapshot_freshness:{state:'missing',age_s:null}};
        await tick(5_000);
        expect(observed!.data?.actions[0].enabled).toBe(false);
        expect(observed!.data?.actions[0].disabled_reason).toBe('provider unavailable');
        failed = true; await tick(5_000);
        expect(observed!.isError).toBe(true);
        failed = false; await tick(5_000);
        expect(observed!.error?.message).toBeUndefined();
        expect(observed!.isSuccess).toBe(true);
        await act(async () => root.render(<QueryClientProvider client={client}><div/></QueryClientProvider>)); await tick();
        await render(); expect(count('metadata')).toBe(1);
        await render(8); expect(count('metadata')).toBe(2);
        wire.metadata.metadata_revision = wire.assessment.metadata_revision = 'changed-release';
        await tick(5_000); expect(count('metadata')).toBe(3);
        expect(vi.mocked(api.get).mock.calls.every(([,c])=>['assessment','metadata'].includes(c?.params?.view))).toBe(true);
    } finally { await act(async () => root.unmount()); client.clear(); host.remove(); }
});

it.each(['old-bms', 'old-robot'])('reports incompatible %s once instead of periodically transferring full catalogs', async peer => {
    vi.useFakeTimers(); const client=new QueryClient(); const host=document.createElement('div'); const root=createRoot(host);
    vi.mocked(api.get).mockImplementation(async () => {
        if (peer === 'old-robot') throw Object.assign(new Error('upgrade required'), {response:{status:426}});
        return {data:actual?.full ?? {actions:[]}};
    });
    function Consumer(){const q=useBioXpOperatorControlCatalog(7);return <div>{q.status}{q.error?.message}</div>;}
    try {
        await act(async()=>root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>));
        await act(async()=>{await vi.advanceTimersByTimeAsync(60_000);});
        expect(vi.mocked(api.get).mock.calls).toHaveLength(1);
        expect(host.textContent).toContain('error');
    } finally {await act(async()=>root.unmount());client.clear();host.remove();}
});

it.each(['assessment', 'metadata'])('cancels the in-flight %s read on detach, without a full GET fallback', async phase => {
    vi.useFakeTimers(); const wire=fixture(); const client=new QueryClient();
    const host=document.createElement('div'); const root=createRoot(host); let aborted=false;
    vi.mocked(api.get).mockImplementation(async (_url, config) => {
        if(config?.params?.view !== phase) return {data:updateFixture(wire.assessment)};
        return new Promise((_resolve,reject)=>config?.signal?.addEventListener('abort',()=>{aborted=true; reject(new Error('cancelled'));}));
    });
    function Consumer(){useBioXpOperatorControlCatalog(7);return null;}
    try {
        await act(async()=>root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>));
        await act(async()=>{await vi.advanceTimersByTimeAsync(20);});
        await act(async()=>root.unmount());
        await act(async()=>{await vi.advanceTimersByTimeAsync(20);});
        expect(aborted).toBe(true);
        expect(vi.mocked(api.get).mock.calls.every(([,c])=>c?.params?.view)).toBe(true);
    } finally {client.clear();host.remove();}
});
