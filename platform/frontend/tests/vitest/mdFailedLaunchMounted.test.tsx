import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({
    fetchMDRun: vi.fn(), fetchMDSummary: vi.fn(), fetchMDArtifacts: vi.fn(), fetchMDAnalysis: vi.fn(),
    fetchJobLogs: vi.fn(), reorchestrateMDRun: vi.fn(), deleteFailedMDLaunch: vi.fn(),
    cancelMDRun: vi.fn(), pauseMDRun: vi.fn(), resumeMDRun: vi.fn(), retryMDDynamics: vi.fn(), retryMDAnalysis: vi.fn(),
}));
vi.mock('react-router-dom', async (importOriginal) => ({ ...(await importOriginal<typeof import('react-router-dom')>()), useNavigate: () => navigate }));
vi.mock('../../src/lib/api', () => api);
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-bms-structure-viewer-host /> }));

import MDResultsPane from '../../src/components/MDResultsPane';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const lifecycle = response({
    schema: 'bms.md.run-detail.v1', job_id: 'failed-id', job_status: 'failed', queue_status: 'failed', phase: 'failed', state_version: 7,
    chemistry: { profile_id: 'amber', profile_sha256: 'a'.repeat(64), assurance: 'curated', verification_status: 'verified' }, engine: 'gromacs', replica_count: 0, replica_summary: {}, simulated_time_ps: 0, requested_time_ps: 10, checkpoint_available: false,
    allowed_actions: ['view_logs', 'reorchestrate', 'delete_failed_launch'], action_explanations: { resume_dynamics: 'no checkpoint', retry_dynamics: 'no replica attempt' }, replicas: [], segments: [], checkpoints: [], events: [],
});

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); navigate.mockReset(); });

describe('mounted failed pre-replica MD lifecycle', () => {
    it('keeps lifecycle controls usable when result reads fail, with job-owned logs and safe mutations', async () => {
        api.fetchMDRun.mockResolvedValue(lifecycle);
        api.fetchMDSummary.mockRejectedValue(new Error('404'));
        api.fetchMDArtifacts.mockRejectedValue(new Error('409'));
        api.fetchMDAnalysis.mockRejectedValue(new Error('404'));
        api.fetchJobLogs.mockResolvedValue(response({ nextflow_log: 'launch failure', nextflow_log_source: 'job_output' }));
        api.reorchestrateMDRun.mockResolvedValue(response({ new_job_id: 'new-id' }));
        api.deleteFailedMDLaunch.mockResolvedValue(response({ deleted: true }));
        vi.stubGlobal('confirm', vi.fn(() => true));
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const container = document.createElement('div'); document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => { root.render(<QueryClientProvider client={client}><MDResultsPane jobId="failed-id" /></QueryClientProvider>); });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Dynamics lifecycle')); });
        expect(api.fetchMDSummary).not.toHaveBeenCalled();
        expect(api.fetchMDArtifacts).not.toHaveBeenCalled();
        expect(api.fetchMDAnalysis).not.toHaveBeenCalled();
        expect(container.textContent).toContain('This launch failed before any replica began');
        expect(container.textContent).toContain('Resume dynamics disabled: no checkpoint');
        expect(container.textContent).toContain('Retry dynamics disabled: no replica attempt');
        expect(container.querySelector('[data-bms-structure-viewer-host]')).toBeNull();
        const click = async (label: string) => {
            const button = [...container.querySelectorAll('button')].find((item) => item.textContent === label) as HTMLButtonElement;
            await act(async () => { button.click(); });
        };
        await click('View logs');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('launch failure')); });
        expect(api.fetchJobLogs).toHaveBeenCalledWith('failed-id');
        await click('Re-orchestrate');
        await act(async () => { await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/designs/new-id')); });
        expect(api.reorchestrateMDRun).toHaveBeenCalledWith('failed-id', 7, expect.any(String));
        await click('Delete failed launch');
        await act(async () => { await vi.waitFor(() => expect(api.deleteFailedMDLaunch).toHaveBeenCalledWith('failed-id', 7)); });
        await act(async () => { root.unmount(); });
        client.clear();
    });
});
