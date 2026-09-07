import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { JobQueuePanel } from '../../src/components/JobQueuePanel';
import { api } from '../../src/lib/api';
import { JobQueueTable } from '../../src/components/dashboard/JobQueueTable';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import { QuickViewer } from '../../src/components/QuickViewer';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });

const defaultAdapter = api.defaults.adapter;
beforeEach(() => { api.defaults.adapter = async () => { throw new Error('Offline test dependency'); }; });
afterEach(() => { document.body.replaceChildren(); api.defaults.adapter = defaultAdapter; });

describe('MD queue integration in the dashboard job queue', () => {
    it.each(['preparing', 'cancelling'])('keeps remote %s visible without claiming execution or offering a duplicate launch', async (phase) => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['queue'], response([{
            id: 'remote-prestart', name: 'Remote bundle preparation', model_id: 'protenix', mode: 'predict',
            queue_status: phase, paused: false, priority: 0, vram_estimate_mb: 5949,
            assigned_gpu: 0, pinned_gpu: null, display_gpu_ids: [],
            execution_target_id: 'vast:123', remote_state: phase,
            created_at: '2026-09-06T12:00:00Z', started_at: null,
        }]));
        client.setQueryData(['system'], response({ gpus: [] }));
        client.setQueryData(['cancelledJobs'], response([]));
        const originalAdapter = api.defaults.adapter;
        const requests: string[] = [];
        api.defaults.adapter = async (config) => {
            requests.push(`${config.method} ${config.url}`);
            if (config.method === 'delete' && config.url === '/api/queue/remote-prestart') return response({ success: true });
            if (config.url === '/api/queue') return response([]);
            throw new Error('Unexpected offline request');
        };
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        try {
            await act(async () => {
                root.render(<MemoryRouter><QueryClientProvider client={client}><JobQueuePanel /></QueryClientProvider></MemoryRouter>);
                await Promise.resolve();
            });
            expect(container.textContent).toContain('Remote bundle preparation');
            expect(container.textContent).toContain(`Vast · ${phase}`);
            expect(container.textContent).toContain('0 run');
            expect([...container.querySelectorAll('h4')].map(node => node.textContent?.trim())).toContain(phase === 'preparing' ? 'Preparing remote jobs' : 'Cancelling remote jobs');
            expect(container.querySelector('button[title="Pause"]')).toBeNull();
            expect(container.querySelector('button[title="Force Launch"]')).toBeNull();
            expect(container.querySelector('button[title="Pin to GPU"]')).toBeNull();
            const cancel = container.querySelector<HTMLButtonElement>('button[title="Cancel"]');
            expect(cancel).not.toBeNull();
            expect(cancel?.disabled).toBe(phase === 'cancelling');
            if (phase === 'preparing') {
                await act(async () => {
                    cancel?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    await new Promise(resolve => setTimeout(resolve, 20));
                });
                expect(requests).toContain('delete /api/queue/remote-prestart');
            }
        } finally {
            await act(async () => root.unmount());
            client.clear();
            api.defaults.adapter = originalAdapter;
        }
    });

    it('shows MD once in the global queue and routes domain controls to MD Operations', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['queue'], response([{
            id: 'md-1', name: 'Membrane equilibration', model_id: 'molecular_dynamics',
            mode: 'simulate', queue_status: 'running', paused: false,
            priority: 0, vram_estimate_mb: 16000, assigned_gpu: 0, pinned_gpu: null,
            display_gpu_ids: [0], scheduler_candidate_gpus: [0], scheduler_ready: true,
            scheduler_blockers: [], created_at: '2026-07-29T11:00:00Z', started_at: '2026-07-29T12:00:00Z',
        }]));
        client.setQueryData(['system'], response({ gpus: [] }));

        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter>
                    <QueryClientProvider client={client}>
                        <JobQueuePanel />
                    </QueryClientProvider>
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.textContent).not.toContain('Molecular Dynamics Queue');
        expect(container.textContent).toContain('Membrane equilibration');
        expect(container.textContent).toContain('MD');
        expect(container.textContent).toContain('MD Operations');
        expect(container.querySelector<HTMLAnchorElement>('a[href="/designs/md-1"]')).toBeTruthy();
        const queueToggle = container.querySelector<HTMLButtonElement>('button[aria-controls="bms-gpu-queue-content"]');
        expect(queueToggle?.getAttribute('aria-expanded')).toBe('true');
        expect(container.textContent).not.toContain('Pause');
        expect(container.textContent).not.toContain('Force');
        expect(client.getQueryState(['md-queue', 25])).toBeUndefined();
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]')).toHaveLength(0);
        expect(container.querySelectorAll('canvas')).toHaveLength(0);

        await act(async () => root.unmount());
        client.clear();
    });

    it('shows remote queue authority without exposing local GPU controls', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['queue'], response([{
            id: 'remote-1', name: 'Remote Protenix', model_id: 'protenix', mode: 'predict',
            queue_status: 'queued', paused: false, priority: 0, vram_estimate_mb: 12000,
            assigned_gpu: null, pinned_gpu: 0, display_gpu_ids: null,
            execution_target_id: 'vast:123', remote_state: 'waiting_remote_gpu',
            scheduler_candidate_gpus: [0], scheduler_ready: true, scheduler_blockers: [],
            created_at: '2026-08-30T12:00:00Z', started_at: null,
        }]));
        client.setQueryData(['system'], response({ gpus: [{ index: 0, name: 'Local GPU' }] }));
        client.setQueryData(['cancelledJobs'], response([]));

        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter>
                    <QueryClientProvider client={client}>
                        <JobQueuePanel />
                    </QueryClientProvider>
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Vast · waiting_remote_gpu');
        expect(container.querySelector('button[title="Pin to GPU"]')).toBeNull();
        expect(container.querySelector('button[title="Force Launch"]')).toBeNull();

        await act(async () => root.unmount());
        client.clear();
    });

    it('marks MD rows in the dashboard table and links to the existing operations/results owner', async () => {
        Object.defineProperty(window, 'matchMedia', {
            configurable: true,
            value: () => ({
                matches: false,
                media: '(max-width: 767px)',
                onchange: null,
                addEventListener: () => undefined,
                removeEventListener: () => undefined,
                addListener: () => undefined,
                removeListener: () => undefined,
                dispatchEvent: () => false,
            }),
        });
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter>
                    <JobQueueTable
                        jobs={[{
                            id: 'md-table-1', name: 'Production replicas', status: 'running',
                            model_id: 'molecular_dynamics', mode: 'molecular_dynamics', params: {},
                            created_at: '2026-07-29T12:00:00Z', design_count: 0, output_dir: null,
                        }]}
                        loading={false}
                        onCancel={() => undefined}
                        onResubmit={() => undefined}
                        onResume={() => undefined}
                        onViewLogs={() => undefined}
                        onViewQuick={() => undefined}
                        quickViewJobId={null}
                    />
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.textContent).toContain('MD');
        expect(container.textContent).toContain('MD Operations');
        expect(container.textContent).not.toContain('Cancel');
        expect(container.textContent).not.toContain('Retry');
        expect(container.textContent).not.toContain('Re-orchestrate');
        expect(container.querySelector<HTMLAnchorElement>('a[href="/designs/md-table-1"]')).toBeTruthy();
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]')).toHaveLength(0);

        await act(async () => root.unmount());
    });

    it('routes completed NGS jobs to the scoped Run Inspector without Quick Viewer', async () => {
        Object.defineProperty(window, 'matchMedia', {
            configurable: true,
            value: () => ({
                matches: false,
                media: '(max-width: 767px)',
                onchange: null,
                addEventListener: () => undefined,
                removeEventListener: () => undefined,
                addListener: () => undefined,
                removeListener: () => undefined,
                dispatchEvent: () => false,
            }),
        });
        let quickViewCalls = 0;
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter initialEntries={['/dashboard?workspace_id=workspace-1&global_experiment_id=global-1&domain_experiment_id=domain-1&state_revision=7']}>
                    <JobQueueTable
                        jobs={[{
                            id: 'ngs-table-1', name: 'Nanopore verification', status: 'completed',
                            model_id: 'nanopore', mode: 'ont_fastq_qc', params: {},
                            created_at: '2026-08-11T09:00:00Z', design_count: 0, output_dir: '/tmp/ngs',
                        }]}
                        loading={false}
                        onCancel={() => undefined}
                        onResubmit={() => undefined}
                        onResume={() => undefined}
                        onViewLogs={() => undefined}
                        onViewQuick={() => { quickViewCalls += 1; }}
                        quickViewJobId={null}
                    />
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        const inspector = container.querySelector<HTMLAnchorElement>(
            'a[href="/ngs?workspace_id=workspace-1&global_experiment_id=global-1&domain_experiment_id=domain-1&state_revision=7&section=analyses&job_id=ngs-table-1"]',
        );
        expect(inspector).toBeTruthy();
        expect(inspector?.textContent).toContain('NGS Run Inspector');
        expect([...container.querySelectorAll('button')].some((button) => button.textContent?.trim() === 'View')).toBe(false);
        expect(quickViewCalls).toBe(0);

        const ngsRow = [...container.querySelectorAll('tr')].find((row) => row.textContent?.includes('Nanopore verification'));
        await act(async () => {
            ngsRow?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await Promise.resolve();
        });
        expect(container.textContent).not.toContain('No structure files');
        expect(container.querySelector('a[href="/designs/ngs-table-1"]')).toBeNull();
        expect(container.textContent).toContain('Open NGS Run Inspector');

        await act(async () => root.unmount());
    });

    it('keeps completed NGS batches on scoped per-run inspector links', async () => {
        Object.defineProperty(window, 'matchMedia', {
            configurable: true,
            value: () => ({ matches: false, media: '(max-width: 767px)', onchange: null,
                addEventListener: () => undefined, removeEventListener: () => undefined,
                addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false }),
        });
        const jobs = ['ngs-batch-1', 'ngs-batch-2'].map((id, index) => ({
            id, name: `ONT batch_run_${index + 1}`, status: 'completed', model_id: 'nanopore', mode: 'ont_fastq_qc',
            params: {}, created_at: '2026-08-11T09:00:00Z', design_count: 0, output_dir: `/tmp/${id}`,
            batch_id: 'ont-batch-1', batch_name: 'ONT batch',
        }));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter initialEntries={['/dashboard?workspace_id=w1&global_experiment_id=g1&domain_experiment_id=d1&state_revision=9']}>
                    <JobQueueTable jobs={jobs} loading={false} onCancel={() => undefined} onResubmit={() => undefined}
                        onResume={() => undefined} onViewLogs={() => undefined} onViewQuick={() => undefined} quickViewJobId={null} />
                </MemoryRouter>,
            );
            await Promise.resolve();
        });
        expect(container.querySelector('a[href^="/results?batch_id="]')).toBeNull();
        const batchRow = [...container.querySelectorAll('tr')].find((row) => row.textContent?.includes('ONT batch'));
        await act(async () => {
            batchRow?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await Promise.resolve();
        });
        for (const job of jobs) {
            expect(container.querySelector(`a[href="/ngs?workspace_id=w1&global_experiment_id=g1&domain_experiment_id=d1&state_revision=9&section=analyses&job_id=${job.id}"]`)).toBeTruthy();
        }
        await act(async () => root.unmount());
    });

    it.each([false, true])('suppresses generic batch results for mixed NGS batches on %s layouts', async (mobileView) => {
        Object.defineProperty(window, 'matchMedia', {
            configurable: true,
            value: () => ({ matches: mobileView, media: '(max-width: 767px)', onchange: null,
                addEventListener: () => undefined, removeEventListener: () => undefined,
                addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false }),
        });
        const jobs = [
            {
                id: 'mixed-ngs-1', name: 'Mixed batch_NGS run', status: 'completed' as const, model_id: 'nanopore', mode: 'ont_fastq_qc',
                params: {}, created_at: '2026-08-11T09:00:00Z', design_count: 0, output_dir: '/tmp/mixed-ngs',
                batch_id: 'mixed-batch-1', batch_name: 'Mixed batch',
            },
            {
                id: 'mixed-protein-1', name: 'Mixed batch_Protein run', status: 'completed' as const, model_id: 'boltz', mode: 'structure_prediction',
                params: {}, created_at: '2026-08-11T09:01:00Z', design_count: 1, output_dir: '/tmp/mixed-protein',
                batch_id: 'mixed-batch-1', batch_name: 'Mixed batch',
            },
        ];
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(
                <MemoryRouter initialEntries={['/dashboard?workspace_id=w1&global_experiment_id=g1&domain_experiment_id=d1&state_revision=9']}>
                    <JobQueueTable jobs={jobs} loading={false} onCancel={() => undefined} onResubmit={() => undefined}
                        onResume={() => undefined} onViewLogs={() => undefined} onViewQuick={() => undefined} quickViewJobId={null} />
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.querySelector('a[href^="/results?batch_id="]')).toBeNull();
        if (mobileView) {
            const toggle = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Mixed batch'));
            await act(async () => {
                toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                await Promise.resolve();
            });
        } else {
            const batchRow = [...container.querySelectorAll('tr')].find((row) => row.textContent?.includes('Mixed batch'));
            await act(async () => {
                batchRow?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                await Promise.resolve();
            });
        }
        expect(container.querySelector('a[href*="job_id=mixed-ngs-1"]')).toBeTruthy();
        await act(async () => root.unmount());
    });

    it('keeps completed NGS jobs out of the generic Quick Viewer', async () => {
        const ngsJob = {
            id: 'ngs-completed-1', name: 'Completed NGS', status: 'completed',
            model_id: 'nanopore', mode: 'ont_fastq_qc', params: {},
            created_at: '2026-08-11T09:00:00Z', design_count: 0, requested_design_count: 0,
            output_dir: '/tmp/ngs',
        };
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['jobs', 'quick-viewer-summary'], response({ jobs: [ngsJob] }));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <MemoryRouter>
                    <QueryClientProvider client={client}>
                        <QuickViewer selectedJobId={ngsJob.id} />
                    </QueryClientProvider>
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.querySelector(`option[value="${ngsJob.id}"]`)).toBeNull();
        expect(container.textContent).not.toContain('No structures found');
        expect(client.getQueryState(['structure-files', ngsJob.id])).toBeUndefined();

        await act(async () => root.unmount());
        client.clear();
    });

    it('keeps completed MD jobs out of generic structure and quick-viewer owners', async () => {
        const mdJob = {
            id: 'md-completed-1', name: 'Completed MD', status: 'completed',
            model_id: 'molecular_dynamics', mode: 'molecular_dynamics', params: {},
            created_at: '2026-07-29T12:00:00Z', design_count: 0, requested_design_count: 0,
            output_dir: '/tmp/md',
        };
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['jobs', 'quick-viewer-summary'], response({ jobs: [mdJob] }));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <MemoryRouter>
                    <QueryClientProvider client={client}>
                        <table><tbody><JobDetailsPanel job={mdJob} onClose={() => undefined} /></tbody></table>
                        <QuickViewer selectedJobId={mdJob.id} />
                    </QueryClientProvider>
                </MemoryRouter>,
            );
            await Promise.resolve();
        });

        expect(container.textContent).toContain('MD Operations');
        expect(container.textContent).toContain('available only in MD Operations');
        expect(container.textContent).not.toContain('No structure files');
        expect(container.querySelector(`option[value="${mdJob.id}"]`)).toBeNull();
        const structureQuery = client.getQueryState(['structure-files', mdJob.id]);
        expect(structureQuery?.fetchStatus).toBe('idle');
        expect(structureQuery?.dataUpdateCount).toBe(0);
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]')).toHaveLength(0);

        await act(async () => root.unmount());
        client.clear();
    });
});
