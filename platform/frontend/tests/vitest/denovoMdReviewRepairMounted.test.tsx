import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { CandidateRoundProgress } from '../../src/components/CandidateRoundProgress';
import { BinderNextRound } from '../../src/components/BinderNextRound';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { MolecularDynamicsNativeSettings } from '../../src/components/MolecularDynamicsNativeSettings';
import { activeInvocationPlan, type InvocationDraftStage } from '../../src/lib/binderInvocationPlan';

// Mounted real consumers/editors and Axios transport. Not a server launch or MD execution.
let tree: ReactTestRenderer | undefined, client: QueryClient;
const adapter = api.defaults.adapter;
const calls: { url: string; method?: string; body: any }[] = [], unexpected: string[] = [];
let initial = true, approved = false, rejectApproval = false;
const digest = 'd'.repeat(64);
const preview = { schema_version: 'bms.md.launch-preview.v2', preview_digest: digest,
    source: { source_ref: { kind: 'candidate', candidate_ref: { kind: 'native_document', job_id: 'producer', artifact_id: 'exact-doc', model: 2 } }, sha256: 'e'.repeat(64) },
    input_identity: { model_number: 2, artifact_id: 'exact-doc' },
    effective_request: { stages: [{ name: 'production', mdp: { nsteps: 0, 'gen-seed': 123, 'custom-native-key': 'retained' } }], replicas: 2 },
    warnings: [{ code: 'native-warning', message: 'Retained warning text' }], blockers: [],
    execution_target_id: 'worker-exact', execution_policy: { remote_result_policy: 'manual' }, execution_plan: { plan_digest: 'plan-exact', target_id: 'worker-exact' } };
const launch = { intent: { name: 'retained-native-launch' }, preview_digest: digest };
const step = () => ({ step_id: 'step/exact', model_id: 'molecular_dynamics', state: approved ? 'queued' : 'review_required', outputs: [],
    request: { name: 'must-not-generic-submit', model_id: 'molecular_dynamics', params: {} }, review: { code: 'md_native_preview_approval_required', preview_digest: digest },
    md_preview: preview, md_launch_request: launch, metadata: { md_preview: preview, md_launch_request: launch } });
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree!.root.findAllByType('button').find(node => text(node) === label)!;
const flush = async () => { for (let n = 0; n < 100; n++) { await act(async () => { await new Promise(r => setTimeout(r, 5)); }); if (!client.isFetching()) return; } throw Error('Queries failed to settle'); };
const click = async (label: string) => { expect(button(label), label).toBeTruthy(); await act(async () => button(label).props.onClick()); await flush(); };
const change = async (label: string, value: string | boolean) => { await act(async () => tree!.root.findByProps({ 'aria-label': label }).props.onChange({ target: typeof value === 'boolean' ? { checked: value } : { value } })); await flush(); };
async function mount(child: React.ReactNode) { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); await act(async () => { tree = create(<QueryClientProvider client={client}>{child}</QueryClientProvider>); }); await flush(); }
beforeEach(() => {
    calls.length = unexpected.length = 0; initial = true; approved = rejectApproval = false; localStorage.clear(); sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => { unexpected.push('fetch'); throw Error('Unexpected fetch'); }));
    vi.stubGlobal('XMLHttpRequest', class { constructor() { unexpected.push('XHR'); throw Error('Unexpected XHR'); } });
    api.defaults.adapter = async config => {
        const url = String(config.url), body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url, method: config.method, body }); let data: any;
        if (url === '/api/binder-continuation/viewed-child/round') data = { job_id: 'resolved-root', round_id: 'initial:resolved-root', state: approved ? 'running' : 'review_required', errors: {}, steps: initial ? { 'step/exact': step() } : {}, selected_rounds: initial ? [] : [{ round_id: 'selected-round', source_job_id: 'viewed-child', root_job_id: 'resolved-root', state: approved ? 'running' : 'review_required', steps: [step()] }] };
        else if (url === '/api/execution-targets') data = [];
        else if (url === '/api/jobs') data = { jobs: [{ id: 'viewed-child', lineage_root_job_id: 'resolved-root' }, { id: 'resolved-root' }], total: 2 };
        else if (url === '/api/jobs/viewed-child') data = { id: 'viewed-child', saved_selection_sets: [] };
        else if (url.endsWith('/md-approval')) { if (rejectApproval) throw Error('stale native digest'); approved = true; data = {}; }
        else if (url === '/api/models/molecular_dynamics') data = { id: 'molecular_dynamics', modes: [{ id: 'simulate' }], params: [] };
        else if (url === '/api/molecular-dynamics/chemistry-profiles') { throw Error('Inventory offline fixture'); }
        else { unexpected.push(`${config.method} ${url}`); throw Error(`Unexpected ${url}`); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { await act(async () => tree?.unmount()); tree = undefined; client?.clear(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); expect(unexpected).toEqual([]); });
for (const kind of ['initial', 'selected'] as const) it(`${kind} shows exact retained source/effective settings/warnings/plan before digest approval at resolved root, then reads back`, async () => {
    initial = kind === 'initial';
    await mount(initial ? <CandidateRoundProgress jobId="viewed-child" /> : <BinderNextRound sourceJobId="viewed-child" selectedDesignIds={[]} onOpenJob={() => {}} renderEditor={() => null} />);
    const rendered = text(tree!.root);
    for (const value of ['exact-doc', 'model_number', 'production', 'gen-seed', 'custom-native-key', 'Retained warning text', 'plan-exact', 'worker-exact', digest]) expect(rendered).toContain(value);
    expect(calls.filter(c => c.method === 'post')).toHaveLength(0);
    const reads = calls.filter(c => c.url.endsWith('/round')).length;
    await click('Approve native MD preview');
    expect(calls.filter(c => c.method === 'post')).toEqual([{ url: `/api/binder-continuation/resolved-root/round/${kind === 'initial' ? 'initial%3Aresolved-root' : 'selected-round'}/steps/step%2Fexact/md-approval`, method: 'post', body: { preview_digest: digest } }]);
    expect(calls.filter(c => c.url.endsWith('/round')).length).toBeGreaterThan(reads);
    expect(tree!.root.findAllByType('button').some(n => text(n) === 'Approve native MD preview')).toBe(false);
});
it('failed MD approval retains preview and reports exact receiver error without generic fallback', async () => {
    rejectApproval = true; await mount(<CandidateRoundProgress jobId="viewed-child" />); await click('Approve native MD preview');
    expect(text(tree!.root)).toContain('stale native digest'); expect(text(tree!.root)).toContain('Retained warning text');
    expect(calls.filter(c => c.method === 'post')).toHaveLength(1);
});
const intent = { schema_version: 'bms.md.launch-intent.v2', name: 'saved', input: { kind: 'guided', source_ref: { kind: 'design', id: 'source-exact', model_number: 2 }, expected_source_sha256: 'e'.repeat(64), chemistry_profile_id: 'saved-profile', chemistry_profile_sha256: 'f'.repeat(64), catalog_digest: 'a'.repeat(64), neutralize: false, padding_nm: 0 }, stages: [{ name: 'production', mdp: { 'gen-seed': 0, 'custom-native-key': 'exact' } }], replicas: 2, random_seed: 0, execution: { ntmpi: 1, ntomp: 4, gpu_offload: 'none', pin: 'off' }, execution_target_id: null, windows: [{ id: 'w', mdp: { production: { 'pull-coord1-init': 0 } } }], analysis: { selection: 'name CA' } };
it('fresh invocation can author supplied native systems through typed controls without prediction or launching a Job', async () => {
    retained = [{ enabled: true, drafts: {}, stage: { id: 'fresh-md', kind: 'md', input: { selected: true }, models: [{ model_id: 'molecular_dynamics', mode: 'simulate', invocation_id: 'fresh-call', params: {} }] } }];
    await mount(<Composer />); await click('molecular_dynamics settings');
    await change('MD invocation input kind', 'compiled');
    await change('MD invocation name', 'explicit compiled'); await change('tpr', 'inputs/exact.tpr');
    await change('Replicas per window', '2'); await change('Orchestration seed (not native stochastic seed)', '0'); await change('ntomp', '4');
    const wire: any = activeInvocationPlan(retained);
    expect(wire.stages).toHaveLength(1); expect(wire.stages[0].kind).toBe('md');
    expect(wire.stages[0].models[0].params.intent).toMatchObject({ name: 'explicit compiled', input: { kind: 'compiled', tpr: 'inputs/exact.tpr' }, stages: [], replicas: 2, random_seed: 0 });
    await change('MD invocation input kind', 'prepared');
    await change('coordinates', 'inputs/exact.gro'); await change('topology', 'inputs/exact.top'); await click('Add ordered stage');
    await change('Stage 1 name', 'explicit-production'); await change('nsteps', '0');
    expect((activeInvocationPlan(retained) as any).stages[0].models[0].params.intent).toMatchObject({ input: { kind: 'prepared', coordinates: 'inputs/exact.gro', topology: 'inputs/exact.top' }, stages: [{ name: 'explicit-production', mdp: { nsteps: '0' } }] });
    expect(calls.filter(c => c.method === 'post')).toEqual([]);
});
let retained: InvocationDraftStage[];
function Composer() { const [stages, setStages] = useState(retained); retained = stages; return <BinderInvocationPlan stages={stages} onChange={setStages} />; }
it('real composer reuses complete native settings with invocation-local draft in/out, OFF/restore and independent identical calls', async () => {
    retained = [{ enabled: true, drafts: {}, stage: { id: 'md-stage', kind: 'md', input: { selected: true }, models: ['call-one', 'call-two'].map(invocation_id => ({ model_id: 'molecular_dynamics', mode: 'simulate', invocation_id, execution_target_id: null, params: { intent: structuredClone(intent) } })) } }];
    await mount(<Composer />); await click('molecular_dynamics settings');
    expect(tree!.root.findAllByType(MolecularDynamicsNativeSettings)).toHaveLength(1);
    expect(text(tree!.root)).toContain('Optional analysis'); expect(text(tree!.root)).toContain('Windows: explicit per-stage');
    await change('Replicas per window', '3'); await change('Orchestration seed (not native stochastic seed)', '');
    await change('MD invocation name', 'only-first');
    await click('Back to round'); await change('Enable invocation call-one', false);
    expect(activeInvocationPlan(retained).stages[0].models).toHaveLength(1);
    await change('Enable invocation call-one', true);
    const wire: any = activeInvocationPlan(JSON.parse(JSON.stringify(retained)));
    expect(wire.stages[0].models[0].params.intent).toEqual({ ...intent, name: 'only-first', replicas: 3, random_seed: '' });
    expect(wire.stages[0].models[1].params.intent).toEqual(intent);
    expect(wire.stages[0].models[0].execution_target_id).toBeNull();
    expect(calls.filter(c => c.method === 'post')).toEqual([]);
    await click('molecular_dynamics settings');
    await change('MD invocation input kind', 'compiled');
    let compiled: any = activeInvocationPlan(retained);
    expect(compiled.stages[0].models[0].params.intent.input).toEqual({ kind: 'compiled' });
    expect(compiled.stages[0].models[0].params.intent.stages).toEqual([]);
    expect(text(tree!.root)).toContain('TPR controls the compiled physics');
    await change('MD invocation input kind', 'prepared');
    expect(text(tree!.root)).toContain('Upload topology include tree');
    await change('MD invocation input kind', 'guided');
    expect((activeInvocationPlan(retained) as any).stages[0].models[0].params.intent).toEqual({ ...intent, name: 'only-first', replicas: 3, random_seed: '' });
    await act(async () => tree!.unmount()); tree = undefined; client.clear(); await mount(<Composer />); await click('molecular_dynamics settings');
    expect(tree!.root.findByProps({ 'aria-label': 'Replicas per window' }).props.value).toBe('3');
    expect(tree!.root.findByProps({ 'aria-label': 'Orchestration seed (not native stochastic seed)' }).props.value).toBe('');
});
