import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Dashboard } from '../../src/components/Dashboard';
import { api, EXECUTION_TARGET_STORAGE_KEY, resumeJob } from '../../src/lib/api';

vi.mock('../../src/components/QuickViewer', () => ({ QuickViewer: () => null }));
vi.mock('../../src/components/JobQueuePanel', () => ({ JobQueuePanel: () => null }));
vi.mock('../../src/components/dashboard/SystemResources', () => ({ GpuSchedulerControls: () => null }));
vi.mock('../../src/components/dashboard/DashboardTelemetry', () => ({ DashboardTelemetry: () => null }));
vi.mock('../../src/components/JobDetailsPanel', () => ({ JobDetailsPanel: () => null }));

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const target = { id: 'vast:remote', name: 'worker50079775', provider: 'vast', state: 'ready', active: true,
    capabilities: { gpu_count: 1, gpu_name: 'RTX 5060 Ti' } };
const summary = { id: 'source', name: 'Source structure', model_id: 'boltz2', mode: 'predict', status: 'failed', created_at: '2026-09-06T00:00:00Z', params: {} };
let jobPatch: Record<string, unknown>;
let paramsPatch: Record<string, unknown>;
let sourceTarget: string | null;
let targets: unknown[];
let failTargets: boolean;
let requests: Record<string, unknown>[];
let hydrated: number;
const originalAdapter = api.defaults.adapter;
let root: ReturnType<typeof createRoot>;
let client: QueryClient;
let container: HTMLDivElement;
const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); };
const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === text)!;
const click = async (text: string) => { expect(button(text), `button ${text}`).toBeTruthy(); await act(async () => button(text).click()); await flush(); };

beforeEach(() => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    sourceTarget = null; targets = [target]; failTargets = false; requests = []; hydrated = 0;
    jobPatch = {}; paramsPatch = {};
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:unrelated-launcher');
    api.defaults.adapter = async (config) => {
        if (config.url === '/api/jobs' || config.url === '/api/jobs/') return response({ jobs: [summary], total: 1 });
        if (config.url === '/api/jobs/source') { hydrated++; return response({ ...summary, ...jobPatch, execution_target_id: sourceTarget,
            params: { structure_launch_variant: 'boltz_cp_experimental', bcp_gpu_ids: '3', pinned_gpus: [3], lock_gpus: true, boltz_sampling_steps: 123, ...paramsPatch } }); }
        if (config.url === '/api/execution-targets') { if (failTargets) throw new Error('inventory offline'); return response(targets); }
        if (config.url === '/api/gpu/status') return response({ gpus: [{ index: 3, name: 'Local A6000' }] });
        if (config.url === '/api/jobs/source/resume') { requests.push(config.data ? JSON.parse(config.data) : {}); return response({ new_job_name: 'retry' }); }
        throw new Error(`Unexpected offline request: ${config.url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); window.sessionStorage.clear(); document.body.replaceChildren(); });
async function open() {
    await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><Dashboard /></QueryClientProvider></MemoryRouter>));
    await flush();
    const opener = [...container.querySelectorAll('button')].find((el) => /Re-orchestrate/.test(`${el.title} ${el.textContent}`));
    expect(opener).toBeTruthy(); await act(async () => opener!.click()); await flush();
    expect(hydrated).toBe(1);
}
it('hydrates Local independently of launcher storage and explicitly submits null', async () => {
    await open(); expect(button('Local')?.getAttribute('aria-pressed')).toBe('true');
    await click('Re-orchestrate Job'); expect(requests[0].execution_target_id).toBeNull();
    expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe('vast:unrelated-launcher');
});
it('inherits the hydrated remote and explicitly preserves it', async () => {
    sourceTarget = target.id; await open();
    expect(button('Vast · worker50079775')?.getAttribute('aria-pressed')).toBe('true');
    expect(container.textContent).toContain('1 × RTX 5060 Ti');
    await click('Re-orchestrate Job'); expect(requests[0].execution_target_id).toBe(target.id);
});
it.each(['missing', 'refresh error'])('keeps an unavailable remote visible and blocks submission: %s', async (kind) => {
    sourceTarget = target.id; if (kind === 'missing') targets = [];
    await open();
    if (kind === 'refresh error') { failTargets = true; await act(async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); }); await flush(); }
    expect(container.textContent).toMatch(/Selected worker.*unavailable/);
    expect(container.textContent).toContain(target.id);
    expect(button('Re-orchestrate Job').disabled).toBe(true); expect(requests).toHaveLength(0);
    await click('Local'); await click('Re-orchestrate Job'); expect(requests[0].execution_target_id).toBeNull();
});
it('switches GPU choices to the remote, clears old pins, and keeps scientific settings', async () => {
    await open(); expect(button('Local A6000')).toBeTruthy();
    const skipMsa = [...container.querySelectorAll('label')].find((label) => label.textContent?.includes('Skip MSA on retry'))!.querySelector('input')!;
    await act(async () => skipMsa.click());
    await click('Vast · worker50079775');
    expect(skipMsa.checked).toBe(true);
    expect(button('Local A6000')).toBeUndefined(); expect(button('RTX 5060 Ti')).toBeTruthy();
    expect(container.textContent).not.toContain('(1 selected)');
    expect(container.textContent).toContain('fresh execution');
    await click('Re-orchestrate Job');
    expect(requests[0].execution_target_id).toBe(target.id);
    expect(requests[0].param_overrides).toMatchObject({ pinned_gpus: null, lock_gpus: false, bcp_gpu_ids: '0', boltz_use_msa: false });
    expect(requests[0].param_overrides).not.toHaveProperty('boltz_sampling_steps');
});
it('explicitly carries automatic Fold-CP allocation between workers with equal GPU ordinals', async () => {
    jobPatch = { model_id: 'boltz_cp_experimental', mode: 'design' };
    paramsPatch = { pinned_gpus: null, lock_gpus: false, bcp_gpu_ids: '0,1,2,3', bcp_size_cp: 4 };
    sourceTarget = 'vast:old';
    targets = [{ ...target, capabilities: { ...target.capabilities, gpu_count: 4 } }];
    await open(); await click('Vast · worker50079775');
    expect(container.textContent).toContain('size_cp 4');
    await click('Re-orchestrate Job');
    expect(requests[0].execution_target_id).toBe(target.id);
    expect(requests[0].param_overrides).toMatchObject({
        pinned_gpus: null, lock_gpus: false, bcp_gpu_ids: '0,1,2,3', bcp_size_cp: 4,
    });
});
it('resume API preserves omission for legacy callers and serializes explicit Local/remote', async () => {
    await resumeJob('source'); await resumeJob('source', undefined, undefined, undefined, null); await resumeJob('source', undefined, undefined, undefined, target.id);
    expect(requests).toEqual([{}, { execution_target_id: null }, { execution_target_id: target.id }]);
});
it.each([{ parent_job_id: 'parent' }, { child_stage: 'fold' }, { awaiting_input: true }, { model_id: 'rfantibody', mode: 'design' }])('does not offer placement changes for domain/child/interactive jobs: %j', async (patch) => {
    jobPatch = patch; sourceTarget = target.id; await open();
    expect(container.querySelector('[aria-label="Execution target"]')).toBeNull();
    expect(container.textContent).toContain('Placement changes are available only for terminal structure root jobs');
    await click('Re-orchestrate Job'); expect(requests[0]).not.toHaveProperty('execution_target_id');
});
it.each(['protenix', 'esmfold2', 'boltz_cp_experimental'])('allows %s roots to replace an unavailable inherited worker', async (model_id) => {
    jobPatch = { model_id, mode: model_id === 'boltz_cp_experimental' ? 'design' : 'predict' }; sourceTarget = 'vast:49684651'; await open();
    expect(container.textContent).toContain('vast:49684651'); expect(button('Re-orchestrate Job').disabled).toBe(true);
    await click('Vast · worker50079775'); await click('Re-orchestrate Job'); expect(requests[0].execution_target_id).toBe(target.id);
});
