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
    it('shows the bounded MD projection and expands operational detail without mounting a viewer', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['queue'], response([{
            id: 'md-replica-child', name: 'Generic duplicate MD child', model_id: 'molecular_dynamics',
            mode: 'molecular_dynamics', queue_status: 'running', paused: false,
        }]));
        client.setQueryData(['system'], response({ gpus: [] }));
        client.setQueryData(['md-queue', 25], response({
            schema: 'bms.md.queue.v1', bounded: true, limit: 25, count: 1,
            runs: [{
                job_id: 'md-1', name: 'Membrane equilibration', job_status: 'running', queue_status: 'running',
                phase: 'replicas_running', state_version: 4, engine: 'gromacs', replica_count: 2,
                replica_summary: { running: 1, queued: 1 }, simulated_time_ps: 25, requested_time_ps: 100,
                checkpoint_available: false, allowed_actions: ['pause', 'cancel'],
                chemistry: { profile_id: 'amber_ff19sb_opc_protein_v1', assurance: 'curated_profile', verification_status: 'verified', requested_scope: 'protein' },
                created_at: '2026-07-29T11:00:00Z', updated_at: '2026-07-29T12:00:00Z',
            }],
        }));
        client.setQueryData(['md-run', 'md-1'], response({
            schema: 'bms.md.run-detail.v1', job_id: 'md-1', job_status: 'running', queue_status: 'running',
            phase: 'replicas_running', state_version: 4,
            chemistry: { profile_id: 'amber_ff19sb_opc_protein_v1', profile_sha256: 'a'.repeat(64), assurance: 'curated_profile', verification_status: 'verified', requested_scope: 'protein' },
            engine: 'gromacs', replica_count: 2, replica_summary: { running: 1, queued: 1 },
            simulated_time_ps: 25, requested_time_ps: 100, checkpoint_available: false,
            allowed_actions: ['pause', 'cancel'],
            replicas: [
                { id: 'replica-0', replica_index: 0, attempt: 0, state: 'running', active: true, engine: 'gromacs', failure: null },
                { id: 'replica-1', replica_index: 1, attempt: 0, state: 'queued', active: true, engine: 'gromacs', failure: null },
            ],
            segments: [], checkpoints: [], events: [],
        }));

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

        expect(container.textContent).toContain('Molecular Dynamics Queue');
        expect(container.textContent).toContain('Membrane equilibration');
        expect(container.textContent).toContain('replicas running');
        expect(container.textContent).toContain('25.00 / 100.00 ps');
        expect(container.textContent).toContain('amber_ff19sb_opc_protein_v1');
        expect(container.textContent).not.toContain('Generic duplicate MD child');
        const queueToggle = container.querySelector<HTMLButtonElement>('button[aria-controls="bms-gpu-queue-content"]');
        expect(queueToggle?.getAttribute('aria-expanded')).toBe('true');

        const details = container.querySelector<HTMLButtonElement>('[data-bms-md-queue-details="md-1"]');
        expect(details).toBeTruthy();
        await act(async () => {
            details!.click();
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Replica 0 · attempt 0 · running');
        expect(container.textContent).toContain('State version 4');
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
