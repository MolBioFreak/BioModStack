import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { api } from '../../src/lib/api';
import { CandidateRoundProgress } from '../../src/components/CandidateRoundProgress';
// Preload the actual review owner; only HTTP transport is replaced below.
import '../../src/components/ExecutionPlanApproval';

let root: Root;
let host: HTMLDivElement;
let client: QueryClient;
const original = api.defaults.adapter;
let progress: any;
let calls: Array<{ url: string; method: string; body: any; headers: any }>;
let unavailable: boolean;
const retained = {
    name: 'fixture-sequence-child', model_id: 'caliby_experimental', mode: 'ensemble_design',
    execution_target_id: 'fixture:worker', launch_context_id: 'retained-destination',
    binder_round_step: { root_job_id: 'source', step_id: 'step' },
    params: { num_seqs_per_pdb: 2, verbose: false, gaussian_n_conformers: 0,
        ensembles: [{ ensemble_id: 'source-design', states: [{ state_id: 'native', path: '/fixture/retained/source.cif' }] }] },
};
const step = { state: 'completed', job_id: 'native-child',
    metadata: { stage: 'sequence_design', source_design_id: 'source-design', target_state: null } };
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); };
async function until(predicate: () => boolean) {
    for (let i = 0; i < 50 && !predicate(); i++) await settle();
    expect(predicate()).toBe(true);
}
function button(label: string, within: ParentNode = host) {
    const found = [...within.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.trim() === label);
    expect(found, label).toBeTruthy(); return found!;
}
async function click(label: string, within: ParentNode = host) {
    await act(async () => button(label, within).click()); await settle();
}
async function mount(kind: 'sequence' | 'binder' = 'sequence') {
    await act(async () => root.render(<QueryClientProvider client={client}><CandidateRoundProgress jobId="source" kind={kind} /></QueryClientProvider>));
    await until(() => Boolean(host.querySelector('section')) || host.textContent!.includes('progress unavailable'));
}
beforeEach(() => {
    unavailable = false; calls = [];
    progress = { job_id: 'source', state: 'completed', errors: {}, steps: { step } };
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    localStorage.clear(); sessionStorage.clear();
    api.defaults.adapter = async config => {
        const url = String(config.url), method = config.method ?? 'get';
        const body = config.data ? JSON.parse(String(config.data)) : undefined;
        calls.push({ url, method, body, headers: config.headers });
        if (unavailable && method === 'get') throw Error('fixture read unavailable');
        let data: any = progress;
        if (url.endsWith('/retry')) progress = data = { ...progress, state: 'running' };
        else if (url.endsWith('/execution-plan/preview')) data = {
            schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
            request: body, plan: { requested_json: body.params, effective_json: body.params,
                source_identity: { revision: 'fixture-source', tree: 'fixture-tree' },
                metadata: { static_components: [], dynamic_templates: [], external_services: [] } },
            deferred_preparation: [], blockers: [],
        };
        else if (url === '/api/jobs' && method === 'post') {
            data = { id: 'native-child', launch_context_id: 'retained-destination' };
            progress = { ...progress, state: 'running', steps: { step: { ...step, state: 'queued' } } };
        }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); host.remove();
    api.defaults.adapter = original; document.body.replaceChildren();
    localStorage.clear(); sessionStorage.clear();
});

it('general progress opens the native child results without a legacy Design or binder target', async () => {
    await mount();
    expect(host.querySelector('[aria-label="Sequence design progress"]')).not.toBeNull();
    expect(host.textContent).toContain('Sequence design: completed');
    expect(host.textContent).toContain('source-design');
    expect(host.textContent).not.toContain('target');
    expect(host.querySelector('a[href="/designs/native-child"]')?.textContent).toBe('Open results');
    expect(calls.every(call => call.method === 'get')).toBe(true);
});

it('generation-only readback neither submits nor offers a retry', async () => {
    progress = { ...progress, state: 'generation_only', steps: {} };
    await mount();
    expect(host.textContent).toContain('generation_only');
    expect(host.querySelector('button')).toBeNull();
    expect(calls.every(call => call.method === 'get')).toBe(true);
});

it('a failed general follow-on preserves source evidence and retries through the existing owner', async () => {
    progress = { ...progress, state: 'completed_with_errors',
        steps: { step: { ...step, state: 'failed', error: 'fixture native failure' } } };
    await mount();
    expect(host.textContent).toContain('source-design');
    expect(host.textContent).toContain('fixture native failure');
    expect(host.querySelector('a[href="/designs/native-child"]')).not.toBeNull();
    await click('Retry sequence design');
    expect(calls.filter(call => call.method === 'post')).toEqual([
        expect.objectContaining({ url: '/api/binder-continuation/source/round/retry', body: {} }),
    ]);
    expect(host.textContent).toContain('Sequence design: running');
});

it('binder progress keeps its target and existing retry action after shared extraction', async () => {
    progress = { ...progress, state: 'needs_retry', steps: { step: { ...step,
        metadata: { ...step.metadata, target_state: 'independent-target' } } } };
    await mount('binder');
    expect(host.querySelector('[aria-label="Binder round progress"]')).not.toBeNull();
    expect(host.textContent).toContain('target independent-target');
    await click('Retry binder round');
    expect(calls.filter(call => call.method === 'post')[0].url).toBe('/api/binder-continuation/source/round/retry');
});

it('prepared general child uses the real approval helper and retains its exact source and destination', async () => {
    progress = { ...progress, state: 'review_required', steps: { step: {
        ...step, job_id: undefined, state: 'review_required', request: structuredClone(retained), review: {} } } };
    await mount(); await click('Review prepared remote step step');
    await until(() => [...document.querySelectorAll('button')].some(node => node.textContent === 'Approve and submit'));
    expect(calls.filter(call => call.url === '/api/jobs')).toEqual([]);
    expect(calls.find(call => call.url.endsWith('/execution-plan/preview'))?.body).toMatchObject(retained);
    await click('Approve and submit', document);
    await until(() => calls.some(call => call.url === '/api/jobs'));
    const body = calls.find(call => call.url === '/api/jobs')!.body;
    expect(body).toMatchObject({ ...retained, execution_plan_approval: 'a'.repeat(64) });
    expect(body.params).not.toHaveProperty('launch_context_id');
    expect(retained).not.toHaveProperty('execution_plan_approval');
    expect(host.textContent).toContain('queued');
});

it('read failure stays observational and can be refreshed without posting', async () => {
    unavailable = true; await mount();
    expect(host.textContent).toContain('Round progress unavailable');
    expect(calls.every(call => call.method === 'get')).toBe(true);
    unavailable = false; await click('Refresh round progress');
    await until(() => host.textContent!.includes('Sequence design: completed'));
    expect(calls.every(call => call.method === 'get')).toBe(true);
});
