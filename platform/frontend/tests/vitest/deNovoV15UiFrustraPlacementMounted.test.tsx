import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ onChange }: any) => <><button onClick={() => onChange(null)}>Advisory Local</button><button onClick={() => onChange('worker')}>Advisory worker</button></> }));
vi.mock('../../src/components/ExecutionPlanApproval', () => ({ reviewExecutionPlan: vi.fn(async () => true) }));
let root: ReturnType<typeof createRoot> | undefined; let client: QueryClient;
const previous = api.defaults.adapter;
const unexpected: string[] = [];
afterEach(async () => { if (root) await act(async () => root!.unmount()); client?.clear(); document.body.replaceChildren(); api.defaults.adapter = previous; vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected.splice(0)).toEqual([]); });
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
const button = (text: string) => [...document.querySelectorAll('button')].find(button => button.textContent?.trim().endsWith(text));
async function click(text: string) { await vi.waitFor(async () => { await flush(); expect(button(text), text).toBeTruthy(); }, { timeout: 15000 }); await act(async () => button(text)!.click()); await flush(); }
it('real loop Results sends explicit independent placement/context/settings and reviews every remote advisory child', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw Error('No network'); });
    const calls: Array<{ url: string; body: any }> = [];
    const job = { id: 'loop', name: 'Loop fixture', model_id: 'protein_modification_experimental', mode: 'region_redesign', status: 'awaiting_input', awaiting_input: true, awaiting_stage: 'post_structure_validation', params: { seq_method: 'mpnn' }, design_count: 2, created_at: '2026-01-01T00:00:00Z' };
    const designs = ['first', 'second'].map(name => ({ id: name, job_id: 'loop', name, pdb_path: `loop/${name}.pdb`, length: 100, created_at: '2026-01-01T00:00:00Z' }));
    api.defaults.adapter = async config => {
        const url = String(config.url), body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body }); let data: any;
        if (url === '/api/jobs' && config.method === 'get') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/loop') data = job;
        else if (url === '/api/designs') data = { designs, total: 2, model_counts: {} };
        else if (url === '/api/designs/first') data = designs[0];
        else if (url === '/api/designs/second') data = designs[1];
        else if (url === '/api/designs/by-job/loop/backbone-summary') data = { job_id: 'loop', total: 2, backbones: {} };
        else if (url === '/api/binder-continuation/loop/round') data = { selected_rounds: [] };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        else if (url === '/api/frustrampnn/jobs/loop/analyze-loop-candidates') {
            if (body.execution_target_id) {
                const job_requests = body.names.map((candidate: string) => ({ name: candidate, model_id: 'frustrampnn', mode: 'analysis', execution_target_id: body.execution_target_id, launch_context_id: body.launch_context_id, params: { _frustrampnn_child_v1: { selection: [{ producer_coordinates: { candidate_filename: candidate } }] } } }));
                throw new AxiosError('review required', '409', config, undefined, { config, status: 409, statusText: 'Conflict', headers: {}, data: { detail: { code: 'remote_prepared_job_review_required', job_requests, response_context: { candidates: body.names } } } });
            }
            data = { loop_job_id: 'loop', analyses: body.names.map((candidate: string) => ({ job_id: `local-${candidate}`, candidate })) };
        } else if (url === '/api/jobs/execution-plan/preview') data = { approval_digest: 'a'.repeat(64), admissible: true, request: body };
        else if (url === '/api/jobs' && config.method === 'post') data = { ...body, id: `remote-${body.name}` };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { data, config, status: 200, statusText: 'OK', headers: {} };
    };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    await act(async () => root!.render(<MemoryRouter initialEntries={['/designs/loop?launch_context_id=destination']}><QueryClientProvider client={client}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>));
    await click('Data Table'); await click('Add Visible Rows'); await click('Analyze with FrustraMPNN');
    expect(calls.find(call => call.url.endsWith('analyze-loop-candidates'))!.body).toMatchObject({ names: ['first.pdb', 'second.pdb'], execution_target_id: null, launch_context_id: 'destination', frustrampnn_settings: { schema_version: 2 } });
    await act(async () => (button('Analyze with FrustraMPNN')!.previousElementSibling as HTMLButtonElement).click()); await flush(); await click('Analyze with FrustraMPNN');
    await vi.waitFor(async () => { await flush(); expect(calls.filter(call => call.url === '/api/jobs/execution-plan/preview')).toHaveLength(2); });
    expect(calls.filter(call => call.url === '/api/jobs' && call.body).map(call => call.body.params._frustrampnn_child_v1.selection[0].producer_coordinates.candidate_filename)).toEqual(['first.pdb', 'second.pdb']);
    expect(document.body.textContent).toContain('FrustraMPNN analysis queued for 2 design(s).');
}, 30000);
