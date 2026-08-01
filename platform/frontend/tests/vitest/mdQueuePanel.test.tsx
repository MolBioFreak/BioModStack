import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { JobQueuePanel } from '../../src/components/JobQueuePanel';
import { JobQueueTable } from '../../src/components/dashboard/JobQueueTable';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import { QuickViewer } from '../../src/components/QuickViewer';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });

afterEach(() => document.body.replaceChildren());

describe('MD queue integration in the dashboard job queue', () => {
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
