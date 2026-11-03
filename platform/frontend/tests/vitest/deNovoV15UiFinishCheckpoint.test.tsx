import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError } from 'axios';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { CheckpointReview } from '../../src/components/CheckpointReview';
const approval = vi.hoisted(() => ({ accepted: true }));
vi.mock('../../src/components/ExecutionPlanApproval', () => ({ reviewExecutionPlan: vi.fn(async () => approval.accepted) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ onChange }: any) => <><button onClick={() => onChange(null)}>Place Local</button><button onClick={() => onChange('new-worker')}>Place worker</button></> }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: ({ structureUrl }: any) => <div data-url={structureUrl} /> }));
let root: Root | undefined, query: QueryClient;
const previous = api.defaults.adapter;
const calls: Array<{ url: string; body: any; params: any }> = [], unexpected: string[] = [];
const checkpoint = { checkpoint_id: 'checkpoint', checkpoint_sha256: 'a'.repeat(64) };
const job: any = { id: 'old-job', name: 'stage', status: 'awaiting_input', execution_target_id: 'old-worker', execution_policy: { remote_result_policy: 'manual' }, awaiting_payload: { component_checkpoint: checkpoint } };
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); api.defaults.adapter = previous; calls.length = 0; approval.accepted = true; expect(unexpected.splice(0)).toEqual([]); });
async function click(text: string) { const node = [...document.querySelectorAll('button')].find(node => node.textContent === text); expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle(); }
async function mount() {
    api.defaults.adapter = async config => {
        const url = String(config.url), body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body, params: config.params }); let data: any;
        if (url.endsWith('/structure-files')) data = { ...checkpoint, structures: [{ relative_path: 'stage/candidate.cif', path: 'retained/candidate.cif', name: 'candidate', type: 'cif', schema: 'native', sha256: 'b'.repeat(64) }], artifacts: [] };
        else if (url.endsWith('/resume')) {
            if (body.execution_target_id === 'new-worker') {
                const request = { name: 'successor', model_id: 'protein_modification_experimental', mode: 'region_redesign', execution_target_id: 'new-worker', execution_policy: body.execution_policy, launch_context_id: body.launch_context_id, params: { retained_checkpoint: checkpoint.checkpoint_id, selected_artifacts: body.checkpoint_decision.selected_artifacts } };
                throw new AxiosError('Review required', '409', config, undefined, { data: { detail: { code: 'remote_prepared_job_review_required', job_request: request, response_context: { original_job_id: 'old-job' } } }, config, status: 409, statusText: 'Conflict', headers: {} });
            }
            data = { job_id: body.execution_target_id === null ? 'local-successor' : 'old-job', state: 'queued' };
        } else if (url.endsWith('/execution-plan/preview')) data = { approval_digest: 'c'.repeat(64), admissible: true, request: body };
        else if (url === '/api/jobs') data = { id: 'remote-successor', name: body.name };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { data, config, status: 200, statusText: 'OK', headers: {} };
    };
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter><CheckpointReview job={job} launchContextId="context" /></MemoryRouter></QueryClientProvider>)); await settle();
    await act(async () => document.querySelector<HTMLInputElement>('input[type=checkbox]')!.click()); await settle();
}
it('preserves same-target default and exact checkpoint artifact identity without placement injection', async () => {
    await mount(); await click('Continue selected stage outputs');
    expect(calls.find(call => call.url.endsWith('/structure-files'))!.params).toEqual({ checkpoint_sha256: checkpoint.checkpoint_sha256 });
    expect(calls.find(call => call.url.endsWith('/resume'))!.body).toEqual({ ...checkpoint, checkpoint_decision: { selected_artifacts: ['stage/candidate.cif'] } });
});
it('sends explicit Local with saved policy/context independently of the previous worker', async () => {
    await mount(); await click('Place Local'); await click('Continue selected stage outputs');
    expect(calls.find(call => call.url.endsWith('/resume'))!.body).toEqual({ ...checkpoint, checkpoint_decision: { selected_artifacts: ['stage/candidate.cif'] }, execution_target_id: null, execution_policy: { remote_result_policy: 'manual' }, launch_context_id: 'context' });
    expect([...document.querySelectorAll('a')].find(a => a.textContent === 'Open continued Job')?.getAttribute('href')).toBe('/designs/local-successor');
});
it('uses standard prepared review for a new worker; cancellation submits nothing and retry preserves exact selection', async () => {
    await mount(); await click('Place worker'); approval.accepted = false; await click('Continue selected stage outputs');
    expect(calls.filter(call => call.url === '/api/jobs')).toHaveLength(0);
    expect(document.body.textContent).toContain('Execution-plan approval cancelled');
    approval.accepted = true; await click('Continue selected stage outputs');
    const resumes = calls.filter(call => call.url.endsWith('/resume'));
    expect(resumes[1].body).toEqual(resumes[0].body);
    const submitted = calls.find(call => call.url === '/api/jobs')!.body;
    expect(submitted).toMatchObject({ execution_target_id: 'new-worker', launch_context_id: 'context', execution_policy: { remote_result_policy: 'manual' }, execution_plan_approval: 'c'.repeat(64), params: { retained_checkpoint: 'checkpoint', selected_artifacts: ['stage/candidate.cif'] } });
    expect([...document.querySelectorAll('a')].find(a => a.textContent === 'Open continued Job')?.getAttribute('href')).toBe('/designs/remote-successor');
});
it('resetting placement restores omission rather than silently choosing Local', async () => {
    await mount(); await click('Place Local'); await click('Keep current execution target'); await click('Continue selected stage outputs');
    expect(calls.find(call => call.url.endsWith('/resume'))!.body).not.toHaveProperty('execution_target_id');
});
