import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/dashboard/IndependentProvisionPanel', () => ({ WorkflowProvisionPanel: () => null }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({ data: { workflows: { structure_prediction: { default_enabled: false } } } }) }));
vi.mock('../../src/lib/useSystemStatus', () => ({ useSystemStatus: () => ({ data: { data: { gpus: [
    { index: 8, name: 'Local GPU', memory_total_mb: 24000 }, { index: 9, name: 'Local GPU', memory_total_mb: 24000 },
] } }, dataUpdatedAt: Date.now(), isError: false, isLoading: false }) }));
const text = (n: ReactTestInstance): string => n.children.map(c => typeof c === 'string' ? c : text(c)).join('');
const flush = async () => { await act(async () => { await new Promise(r => setTimeout(r, 5)); }); };
const targets = ['one', 'two'].map(id => ({ id: `vast:${id}`, name: id, active: true, state: 'ready', capabilities: { gpu_count: 4, gpu_name: 'RTX 5060 Ti' } }));
const telemetry = (id: string, indices = [0, 1, 2, 3]) => ({ source: 'active_vast', available: true,
    observed_at: new Date().toISOString(), target: targets.find(t => t.id === id), gpus: indices.map(index => ({
        id: `${id}:gpu:${index}`, execution_target_id: id, index, uuid: `GPU-${index}`, name: 'RTX 5060 Ti',
        utilization: 0, memory_used_mb: 0, memory_total_mb: 16384, temperature: 30, power_draw_w: 10,
        controls: { fan: false, power: false },
    })) });
let renderer: ReactTestRenderer;
let client: QueryClient;
let posts: any[];
let reads: string[];
let cacheReads: any[];
let cacheReady = false;
const adapter = api.defaults.adapter;
const fixture = { pred_method: 'fold_cp', sequence: 'MKTIIALSYIFCLVFADYKDDDDA', bcp_size_cp: 4,
    boltz_num_samples: 1, boltz_use_msa: false, run_frustrampnn: false };
async function mount(initialValues: any = fixture, target: string | null = null) {
    posts = []; reads = []; cacheReads = [];
    window.history.replaceState({}, '', '/submit');
    if (target) sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target); else sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    api.defaults.adapter = async config => {
        if (config.url === '/api/msa/provider-cache/inspect') {
            const request = JSON.parse(config.data);
            cacheReads.push(request);
            return { data: { state: request.params.sequence_batch_entries ? 'unresolved' : cacheReady ? 'ready' : 'miss' }, status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.method === 'post') { posts.push(JSON.parse(config.data)); throw new Error('fixture captured; no launch'); }
        let data: any;
        if (config.url === '/api/execution-targets') data = targets;
        else if (config.url === '/api/execution-targets/active/telemetry') {
            reads.push(config.params.execution_target_id); data = telemetry(config.params.execution_target_id);
        } else if (config.url?.includes('msa')) data = { providers: {}, cache_entries: 0 };
        else throw new Error(`Unexpected fixture GET ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => { renderer = create(<MemoryRouter><QueryClientProvider client={client}>
        <StructurePredictionTemplate onBack={() => {}} initialValues={initialValues} />
    </QueryClientProvider></MemoryRouter>); });
    await flush(); await flush();
}
const preview = () => renderer.root.findByType(ExecutionTargetPicker).props.workflowRequest;
async function click(label: string) {
    const button = renderer.root.findAllByType('button').find(n => text(n).includes(label));
    expect(button, label).toBeTruthy();
    await act(async () => button!.props.onClick()); await flush(); await flush();
}
async function sample(data: any) {
    await act(async () => { client.setQueryData(['active-remote-gpu-telemetry', 'vast:one'], { data }); }); await flush();
}
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); client?.clear(); api.defaults.adapter = adapter; sessionStorage.clear(); cacheReady = false; vi.restoreAllMocks(); });

it('admits a saved local MSA provider when Fold-CP MSA is disabled', async () => {
    await mount({ ...fixture, bcp_size_cp: 1, msa_provider: 'local' });
    expect(preview().params.boltz_use_msa).toBe(false);
    await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(cacheReads).toEqual([]);
});

it('refuses local MSA only when Fold-CP actually needs an MSA', async () => {
    await mount({ ...fixture, bcp_size_cp: 1, msa_provider: 'local', boltz_use_msa: true });
    await click('Launch Prediction');
    expect(posts).toHaveLength(0);
    expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/local.*disabled|disabled.*local/i));
});

it('requires cache hits for every protein chain, not only the primary sequence', async () => {

    await mount({ ...fixture, bcp_size_cp: 1, boltz_use_msa: true, msa_cache_only: true, complex_components: [
        { id: 'A', type: 'protein', sequence: fixture.sequence },
        { id: 'B', type: 'protein', sequence: 'AAAAKLL' },
        { id: 'C', type: 'ligand', ccd: 'ATP' },
    ] });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 330)); }); await flush();
    expect(cacheReads[0].params.complex_components).toEqual(expect.arrayContaining([
        expect.objectContaining({ sequence: fixture.sequence }), expect.objectContaining({ sequence: 'AAAAKLL' }),
    ]));
    expect(text(renderer.root)).toContain('Cache: miss');
    await click('Launch Prediction');
    expect(posts).toHaveLength(0);
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('verified native request cache replay'));
});

it('allows cache-only after all protein chains have verified hits', async () => {
    cacheReady = true;
    await mount({ ...fixture, bcp_size_cp: 1, boltz_use_msa: true, msa_cache_only: true, complex_components: [
        { id: 'A', type: 'protein', sequence: fixture.sequence },
        { id: 'B', type: 'protein', sequence: 'AAAAKLL' },
    ] });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 330)); }); await flush();
    await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(posts[0].params.msa_cache_only).toBe(true);
});

it('keeps uncompiled batch cache unresolved rather than certifying the displaced chain', async () => {
    await mount({ ...fixture, bcp_size_cp: 1, boltz_use_msa: true, msa_cache_only: true,
        sequence_batch_input: 'variant1: VVVVVV\nvariant2: LLLLLL', sequence_batch_component_id: 'B',
        complex_components: [
            { id: 'A', type: 'protein', sequence: fixture.sequence },
            { id: 'B', type: 'protein', sequence: 'DISPLACED' },
        ],
    });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 330)); }); await flush();
    expect(cacheReads[0].params.sequence_batch_entries).toEqual(expect.arrayContaining([
        expect.objectContaining({ sequence: 'VVVVVV' }), expect.objectContaining({ sequence: 'LLLLLL' }),
    ]));
    await click('Launch Prediction');
    expect(posts).toHaveLength(0);
});

it('names stale remote telemetry, refuses launch, and refreshes without stale-capacity bypass', async () => {
    await mount(fixture, 'vast:one');
    await sample({ ...telemetry('vast:one'), observed_at: new Date(Date.now() - 60_000).toISOString() });
    expect(preview()).not.toBeNull(); // preloading stays available while telemetry is stale
    expect(text(renderer.root)).toContain('Worker GPU telemetry is stale or unavailable');
    await click('Launch Prediction');
    expect(posts).toHaveLength(0);
    await click('Refresh GPU telemetry');
    expect(reads.filter(id => id === 'vast:one').length).toBeGreaterThan(1);
    expect(preview().params.bcp_gpu_ids).toBe('0,1,2,3');
});

it('actual Structure picker replaces local saved placement and sends four remote devices unchanged in preview/submit', async () => {
    await mount({ ...fixture, pinned_gpus: [8, 9], bcp_gpu_ids: '8,9', lock_gpus: true });
    expect(preview()).not.toBeNull(); // CP4 stays CP4; preloading is not launch admission.
    await click('Vast · one');
    expect(reads).toContain('vast:one');
    expect(text(renderer.root)).not.toContain('Local GPU');
    expect(preview().params).toMatchObject({ bcp_gpu_ids: '0,1,2,3', bcp_size_cp: 4, lock_gpus: false, boltz_num_samples: 1 });
    for (const ordinal of [1, 2, 3, 4]) await click(`RTX 5060 Ti #${ordinal}`);
    expect(preview().params.pinned_gpus).toEqual([0, 1, 2, 3]);
    await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatchObject({ model_id: 'boltz_cp_experimental', execution_target_id: 'vast:one',
        params: { bcp_gpu_ids: '0,1,2,3', bcp_size_cp: 4, pinned_gpus: [0, 1, 2, 3], boltz_num_samples: 1 } });
    await click('Vast · two');
    expect(preview().params.pinned_gpus).toBeUndefined();
    expect(preview().params.bcp_gpu_ids).toBe('0,1,2,3');
    await click('Local');
    expect(text(renderer.root)).toContain('Local GPU');
    expect(preview()).not.toBeNull(); // provisioning is independent of local GPU readiness
});
it.each([undefined, 'vast:two'])('remote initial mount ignores unbound or foreign saved IDs (%s), including coincident indices', async binding => {
    await mount({ ...fixture, execution_target_id: binding, pinned_gpus: [0], bcp_gpu_ids: '0', lock_gpus: true }, 'vast:one');
    expect(preview().params).toMatchObject({ bcp_gpu_ids: '0,1,2,3', bcp_size_cp: 4, lock_gpus: false });
    expect(preview().params.pinned_gpus).toBeUndefined();
});
it('same-target explicit placement remains operator-owned rather than expanded to inventory', async () => {
    await mount({ ...fixture, bcp_size_cp: 1, execution_target_id: 'vast:one', pinned_gpus: [2], bcp_gpu_ids: '2', lock_gpus: true }, 'vast:one');
    expect(preview().params).toMatchObject({ pinned_gpus: [2], bcp_gpu_ids: '2', bcp_size_cp: 1, lock_gpus: true });
});
it.each(['stale', 'unavailable', 'wrong-target', 'missing-device', 'count-only', 'query-error'])('remote %s telemetry cannot use local fallback or reduce CP, and recovers', async fault => {
    await mount(fixture, 'vast:one');
    expect(preview().params.bcp_size_cp).toBe(4);
    const data: any = telemetry('vast:one');
    if (fault === 'stale') data.observed_at = new Date(Date.now() - 60_000).toISOString();
    if (fault === 'unavailable') data.available = false;
    if (fault === 'wrong-target') data.target = targets[1];
    if (fault === 'missing-device') data.gpus.pop();
    if (fault === 'count-only') data.gpus = [];
    if (fault === 'query-error') {
        api.defaults.adapter = async () => { throw new Error('telemetry offline'); };
        await act(async () => { await client.refetchQueries({ queryKey: ['active-remote-gpu-telemetry', 'vast:one'] }); }); await flush();
    } else await sample(data);
    expect(preview()).not.toBeNull(); // still serialize assets while launch is refused
    await click('Launch Prediction'); expect(posts).toHaveLength(0);
    await sample(telemetry('vast:one'));
    expect(preview().params).toMatchObject({ bcp_size_cp: 4, bcp_gpu_ids: '0,1,2,3', boltz_num_samples: 1 });
});
it('catalog device identities, not capacity count, define the four-device preview and submit', async () => {
    await mount(fixture, 'vast:one');
    await sample(telemetry('vast:one', [2, 4, 6, 8]));
    expect(preview().params.bcp_gpu_ids).toBe('2,4,6,8');
    await click('Launch Prediction');
    expect(posts[0].params).toMatchObject({ bcp_gpu_ids: '2,4,6,8', bcp_size_cp: 4 });
});
it('local explicit GPU selection is preserved', async () => {
    await mount({ ...fixture, bcp_size_cp: 1, pinned_gpus: [9], bcp_gpu_ids: '9' });
    expect(preview().params).toMatchObject({ bcp_gpu_ids: '9', bcp_size_cp: 1, pinned_gpus: [9] });
    expect(reads).toEqual([]);
});
it('states why a launch was refused instead of appearing to do nothing', async () => {
    await mount(fixture, 'vast:one');
    await sample(telemetry('vast:one'));
    const passthrough = api.defaults.adapter;
    // The API refuses remote submissions without a matching preview approval; the
    // operator must see that reason rather than a button that silently did nothing.
    api.defaults.adapter = async config => {
        if (config.method === 'post') {
            throw Object.assign(new Error('Request failed with status code 409'), {
                isAxiosError: true,
                response: { status: 409, data: { detail: 'Remote submission requires explicit execution-plan preview approval' } },
            });
        }
        return passthrough(config);
    };
    await click('Launch Prediction');
    expect(text(renderer.root)).toContain('Remote submission requires explicit execution-plan preview approval');
});


it('actual Fold-CP launcher reviews then submits the identical request despite ambient placement drift', async () => {
    await mount(fixture, 'vast:one');
    await sample(telemetry('vast:one'));
    const requests: Array<{ url: string; body: any }> = [];
    const originalAdapter = api.defaults.adapter as (config: any) => Promise<any>;
    api.defaults.adapter = async config => {
        if (config.method !== 'post') return originalAdapter(config);
        const body = JSON.parse(config.data);
        requests.push({ url: config.url!, body });
        const data = config.url === '/api/jobs/execution-plan/preview' ? {
            schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64),
            admissible: true, request: body,
            plan: { requested_json: body.params, effective_json: body.params,
                source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) },
                metadata: { static_components: [{ component_key: 'RunBoltzCPExperimental' }],
                    dynamic_templates: [], external_services: [] } },
            deferred_preparation: [], blockers: [],
        } : { id: 'reviewed-fold-cp' };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    // No target-change event: simulate another form changing the shared default.
    // The selected form and its telemetry still belong to vast:one.
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:two');
    await click('Launch Prediction');
    let approve: HTMLButtonElement | undefined;
    for (let attempt = 0; attempt < 100 && !approve; attempt++) {
        await flush();
        approve = [...document.querySelectorAll('button')].find(
            button => button.textContent === 'Approve and submit');
    }
    expect(approve).toBeTruthy();
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe('/api/jobs/execution-plan/preview');
    expect(requests[0].body.execution_target_id).toBe('vast:one');
    expect(requests[0].body.params).toMatchObject({ bcp_size_cp: 4, bcp_gpu_ids: '0,1,2,3' });
    sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
    await act(async () => { approve!.click(); });
    await flush(); await flush();
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual({ url: '/api/jobs',
        body: { ...requests[0].body, execution_plan_approval: 'a'.repeat(64) } });
}, 15000);
