import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
import { api } from '../../src/lib/api';
import MDResultsPane from '../../src/components/MDResultsPane';

// Transport-only command qualification. No checkpoint actuator or native process
// is run; server action eligibility and state versions remain authoritative.
const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const jobId = 'project-md-root';
const run = (phase: string, actions: string[]) => ({
    schema: 'bms.md.run-detail.v1', job_id: jobId, job_status: phase,
    queue_status: phase, phase, state_version: 17,
    chemistry: { profile_id: 'amber', profile_sha256: 'a'.repeat(64), assurance: 'curated', verification_status: 'verified' },
    engine: 'gromacs', replica_count: 1, replica_summary: {},
    simulated_time_ps: 0, requested_time_ps: 1, checkpoint_available: phase === 'paused',
    allowed_actions: actions, replicas: [], segments: [], checkpoints: [], events: [],
});
const RouteReceipt = () => { const location = useLocation(); return <output data-route>{location.pathname}</output>; };
let root: Root | undefined;
let client: QueryClient;
let container: HTMLDivElement;
const mount = async (phase: string, actions: string[]) => {
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
        if (url === `/api/molecular-dynamics/runs/${jobId}`) return response(run(phase, actions)) as never;
        throw new Error('Independent result read unavailable in command fixture');
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root!.render(<MemoryRouter initialEntries={[`/designs/${jobId}`]}><QueryClientProvider client={client}><MDResultsPane jobId={jobId} /><RouteReceipt /></QueryClientProvider></MemoryRouter>));
    await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(container.querySelector('[data-bms-md-lifecycle]')).toBeTruthy(); });
};
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined;
    client?.clear(); vi.restoreAllMocks(); document.body.replaceChildren();
});

describe('integrated MD command receiving transport', () => {
    it.each([
        ['running', 'pause', 'Pause', 'pause'],
        ['paused', 'resume_dynamics', 'Resume dynamics', 'resume'],
        ['running', 'cancel', 'Cancel', 'cancel'],
    ])('keeps %s %s on the existing root/state authority despite independent result errors', async (phase, action, label, endpoint) => {
        await mount(phase, [action]);
        const post = vi.spyOn(api, 'post').mockResolvedValue(response(run(phase, [action])) as never);
        const invalidate = vi.spyOn(client, 'invalidateQueries');
        const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === label);
        expect(button).toBeTruthy(); expect(button!.disabled).toBe(false);
        const otherLabels = ['Pause', 'Resume dynamics', 'Cancel'].filter(item => item !== label);
        for (const other of otherLabels) expect([...container.querySelectorAll('button')].some(item => item.textContent === other)).toBe(false);
        await act(async () => button!.click());
        await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(post).toHaveBeenCalledTimes(1); });
        expect(post).toHaveBeenCalledWith(`/api/molecular-dynamics/runs/${jobId}/${endpoint}`, {
            expected_state_version: 17, idempotency_key: expect.stringMatching(new RegExp(`^${action}:${jobId}:run:17:`)),
        });
        expect(invalidate).toHaveBeenCalledWith({ queryKey: ['md-run', jobId] });
        expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jobs'] });
        expect(container.querySelector('[data-route]')?.textContent).toBe(`/designs/${jobId}`);
    });

    it('routes re-orchestration from the exact failed root to the server-returned Job without manufacturing launch context', async () => {
        await mount('failed', ['reorchestrate']);
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        const post = vi.spyOn(api, 'post').mockResolvedValue(response({ new_job_id: 'replacement-md-root' }) as never);
        const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === 'Re-orchestrate')!;
        await act(async () => button.click());
        await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(container.querySelector('[data-route]')?.textContent).toBe('/designs/replacement-md-root'); });
        expect(post).toHaveBeenCalledTimes(1);
        expect(post).toHaveBeenCalledWith(`/api/molecular-dynamics/runs/${jobId}/reorchestrate`, {
            expected_state_version: 17, idempotency_key: expect.stringMatching(/^reorchestrate:project-md-root:17:/),
        });
        expect(container.querySelector('[data-route]')?.textContent).not.toContain('launch_context');
    });
});
