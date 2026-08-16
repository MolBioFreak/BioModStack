import React, { act, useLayoutEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ngsApiMocks = vi.hoisted(() => ({
    fetchFullJob: vi.fn(),
    fetchJobStages: vi.fn(),
    fetchJobs: vi.fn(),
}));
const alignmentMocks = vi.hoisted(() => ({
    fetchAlignmentSessions: vi.fn(),
    isAlignmentAccessDenied: vi.fn(),
    rotateAlignmentAccess: vi.fn(),
}));
const contextMocks = vi.hoisted(() => ({
    updateQueryParams: vi.fn(),
}));

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    ...ngsApiMocks,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: null,
        globalExperimentId: null,
        stateRevisionId: null,
        selectedDomainExperiment: null,
        availability: { canMutateDomain: false, reason: 'Select an NGS domain experiment.' },
        contextHref: (pathname: string) => pathname,
        updateQueryParams: contextMocks.updateQueryParams,
    }),
}));
vi.mock('../../src/components/ngs/useSequenceQcManifest', () => ({
    useSequenceQcManifest: () => ({ data: null, error: null, isLoading: false }),
}));
vi.mock('../../src/components/useThemeColors', () => ({
    useThemeColors: () => new Proxy({}, { get: () => '#000000' }),
    useThemePlotlyLayout: () => ({}),
}));
vi.mock('../../src/lib/ngsAlignmentSession', () => ({
    ...alignmentMocks,
}));

vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div>Molstar</div> }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({
    ConformationalMappingViewer: () => <div>Conformational mapping</div>,
}));

import { JobDetailPage } from '../../src/components/JobDetailPage';
import { NGSToolkit } from '../../src/components/NGSToolkit';
import {
    isNgsJob,
    ngsJobShouldPoll,
    ngsResultHref,
    ngsToolkitSearchForView,
    ngsToolkitViewFromSearch,
} from '../../src/lib/ngsResultRouting';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

function NgsDestination() {
    const location = useLocation();
    return <div data-testid="ngs-destination">{location.pathname}{location.search}</div>;
}

function SwitchJobButton({ onJob456Layout }: { onJob456Layout?: () => void }) {
    const navigate = useNavigate();
    const location = useLocation();
    const selectedJob = new URLSearchParams(location.search).get('job_id');
    useLayoutEffect(() => {
        if (selectedJob === 'job-456') onJob456Layout?.();
    }, [onJob456Layout, selectedJob]);
    return <button type="button" onClick={() => navigate('/ngs?section=analyses&job_id=job-456')}>Switch job</button>;
}

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
            assertion();
            return;
        } catch {
            await flush();
        }
    }
    assertion();
}

beforeEach(() => {
    vi.useRealTimers();
    ngsApiMocks.fetchFullJob.mockReset();
    ngsApiMocks.fetchJobStages.mockReset();
    ngsApiMocks.fetchJobs.mockReset();
    contextMocks.updateQueryParams.mockReset();
    alignmentMocks.fetchAlignmentSessions.mockReset();
    alignmentMocks.fetchAlignmentSessions.mockResolvedValue([]);
    alignmentMocks.isAlignmentAccessDenied.mockReset();
    alignmentMocks.isAlignmentAccessDenied.mockReturnValue(false);
    alignmentMocks.rotateAlignmentAccess.mockReset();
    alignmentMocks.rotateAlignmentAccess.mockResolvedValue({
        job_id: 'job-123',
        rotated: true,
        scheme: 'opaque_job_capability_v1',
        rotation_count: 1,
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    vi.unstubAllGlobals();
    await act(async () => root.unmount());
    client.clear();
    document.body.replaceChildren();
    vi.useRealTimers();
});

describe('completed NGS result routing', () => {
    it('keeps a clean NGS landing URL on the workflow launcher after jobs load', async () => {
        const completedJob = {
            id: 'job-123',
            name: 'AAZ605 FASTQ QC',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-10T00:00:00Z',
            params: { fastq_files: ['/inputs/AAZ605.fastq'] },
        };
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [completedJob], total: 1 } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs']}>
                        <Routes>
                            <Route path="/ngs" element={<NGSToolkit />} />
                        </Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(ngsApiMocks.fetchJobs).toHaveBeenCalled());
        for (let attempt = 0; attempt < 5; attempt += 1) {
            await flush();
        }

        expect(contextMocks.updateQueryParams).not.toHaveBeenCalled();
        expect(container.textContent).not.toContain('Nanopore Jobs');
    });

    it('maps NGS deep links to the Run Inspector view', () => {
        expect(isNgsJob({ model_id: 'nanopore', mode: 'ont_fastq_qc' })).toBe(true);
        for (const modelId of ['ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation']) {
            expect(isNgsJob({ model_id: modelId, mode: 'completed' })).toBe(true);
        }
        expect(isNgsJob({ model_id: 'mynanopore', mode: 'ont_fastq_qc' })).toBe(false);
        expect(isNgsJob({ model_id: 'unrelated', mode: 'nanopore_methylation' })).toBe(false);
        expect(ngsResultHref('job 123')).toBe('/ngs?section=analyses&job_id=job%20123');
        expect(ngsResultHref(
            'job 123',
            '?workspace_id=ws-1&global_experiment_id=global-1&domain_experiment_id=domain-1',
        )).toBe(
            '/ngs?workspace_id=ws-1&global_experiment_id=global-1&domain_experiment_id=domain-1&section=analyses&job_id=job+123',
        );
        expect(ngsToolkitViewFromSearch('?section=analyses&job_id=job-123')).toBe('runs');
        expect(ngsToolkitViewFromSearch('?section=evidence&job_id=job-123')).toBe('runs');
        expect(ngsToolkitViewFromSearch('?section=instrument')).toBe('instrument');
        expect(ngsToolkitViewFromSearch('?job_id=job-123&launch_context_id=context-1')).toBe('runs');
    });

    it('keeps the URL and toolkit view synchronized', () => {
        const context = '?workspace_id=ws-1&section=analyses&job_id=job-123';
        expect(ngsToolkitSearchForView(context, 'launch')).toBe('?workspace_id=ws-1');
        expect(ngsToolkitSearchForView(context, 'instrument')).toBe('?workspace_id=ws-1&section=instrument');
        expect(ngsToolkitSearchForView(context, 'runs')).toBe(
            '?workspace_id=ws-1&section=analyses&job_id=job-123',
        );
    });

    it('polls full job records until they reach a terminal state', () => {
        expect(ngsJobShouldPoll('queued')).toBe(true);
        expect(ngsJobShouldPoll('running')).toBe(true);
        expect(ngsJobShouldPoll('completed')).toBe(false);
        expect(ngsJobShouldPoll('failed')).toBe(false);
    });

    it('opens the selected inspector and polls an active full job to completion', async () => {
        vi.useFakeTimers();
        const scrollIntoView = vi.fn();
        Object.defineProperty(Element.prototype, 'scrollIntoView', {
            configurable: true,
            value: scrollIntoView,
        });
        const queuedJob = {
            id: 'job-123',
            name: 'AAZ605 FASTQ QC',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'queued',
            created_at: '2026-08-10T00:00:00Z',
            params: { fastq_files: ['/inputs/AAZ605.fastq'] },
        };
        const completedJob = { ...queuedJob, status: 'completed' };
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [queuedJob], total: 1 } });
        ngsApiMocks.fetchFullJob
            .mockResolvedValueOnce(queuedJob)
            .mockResolvedValue(completedJob);
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                        <Routes>
                            <Route path="/ngs" element={<NGSToolkit />} />
                        </Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(0);
        });
        for (let attempt = 0; attempt < 5; attempt += 1) {
            await act(async () => Promise.resolve());
        }

        const inspector = container.querySelector<HTMLElement>('[data-testid="ngs-run-inspector"]');
        expect(inspector).not.toBeNull();
        expect(document.activeElement).toBe(inspector);
        expect(scrollIntoView).toHaveBeenCalled();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(4_000);
        });
        expect(ngsApiMocks.fetchFullJob).toHaveBeenCalledTimes(2);
        expect(container.textContent).toContain('completed');
    });

    it('routes a completed Nanopore job to its NGS Run Inspector instead of requesting structure files', async () => {
        const requested: string[] = [];
        vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
            const url = String(input);
            requested.push(url);
            if (url === '/api/jobs/job-123') {
                return new Response(JSON.stringify({
                    id: 'job-123',
                    name: 'AAZ605 FASTQ QC',
                    model_id: 'nanopore',
                    mode: 'ont_fastq_qc',
                    status: 'completed',
                    created_at: '2026-08-10T00:00:00Z',
                    output_dir: '/results/job-123',
                    params: {},
                }), { status: 200, headers: { 'Content-Type': 'application/json' } });
            }
            if (url.endsWith('/structure-files')) {
                return new Response(JSON.stringify({ structures: [], count: 0 }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                });
            }
            return new Response('not found', { status: 404 });
        }));

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[
                        '/jobs/job-123?workspace_id=ws-1&global_experiment_id=global-1&domain_experiment_id=domain-1',
                    ]}>
                        <Routes>
                            <Route path="/jobs/:jobId" element={<JobDetailPage />} />
                            <Route path="/ngs" element={<NgsDestination />} />
                        </Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });

        await waitUntil(() => {
            expect(container.querySelector('[data-testid="ngs-destination"]')?.textContent)
                .toBe(
                    '/ngs?workspace_id=ws-1&global_experiment_id=global-1&domain_experiment_id=domain-1&section=analyses&job_id=job-123',
                );
        });
        expect(requested).not.toContain('/api/jobs/job-123/structure-files');
    });

    it('restores a denied completed-job capability and retries the selected session query', async () => {
        const completedJob = {
            id: 'job-123',
            name: 'AAZ605 FASTQ QC',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-10T00:00:00Z',
            output_dir: '/results/job-123',
            params: { fastq_path: '/inputs/AAZ605.fastq' },
        };
        const denial = { response: { status: 403 } };
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [completedJob], total: 1 } });
        ngsApiMocks.fetchFullJob.mockResolvedValue(completedJob);
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });
        alignmentMocks.fetchAlignmentSessions
            .mockRejectedValueOnce(denial)
            .mockResolvedValue([]);
        alignmentMocks.isAlignmentAccessDenied.mockImplementation((reason) => reason === denial);
        client.setQueryData(['sequence-qc-manifest', 'job-123'], { schema: 'sequence_qc.manifest.v1' });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                        <Routes>
                            <Route path="/ngs" element={<NGSToolkit />} />
                        </Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await waitUntil(() => {
            expect(container.textContent).toContain('Restore this browser’s access');
        });
        const restore = [...container.querySelectorAll('button')].find(
            (button) => button.textContent === 'Restore this browser’s access',
        );
        expect(restore).toBeTruthy();

        await act(async () => {
            restore?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        await waitUntil(() => {
            expect(alignmentMocks.rotateAlignmentAccess).toHaveBeenCalledWith('job-123');
            expect(alignmentMocks.fetchAlignmentSessions).toHaveBeenCalledTimes(2);
        });
        expect(client.getQueryState(['sequence-qc-manifest', 'job-123'])?.isInvalidated).toBe(true);
    });

    it('drops an old recovery completion after switching the selected job', async () => {
        Object.defineProperty(Element.prototype, 'scrollIntoView', {
            configurable: true,
            value: vi.fn(),
        });
        const job123 = {
            id: 'job-123', name: 'Old FASTQ QC', model_id: 'nanopore', mode: 'ont_fastq_qc',
            status: 'completed', created_at: '2026-08-10T00:00:00Z', output_dir: '/results/job-123', params: {},
        };
        const job456 = {
            ...job123, id: 'job-456', name: 'New FASTQ QC', output_dir: '/results/job-456',
        };
        const denial = { response: { status: 403 } };
        let releaseRotation!: () => void;
        alignmentMocks.rotateAlignmentAccess.mockImplementation(() => new Promise((resolve) => {
            releaseRotation = () => resolve({
                job_id: 'job-123', rotated: true, scheme: 'opaque_job_capability_v1', rotation_count: 1,
            });
        }));
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [job123, job456], total: 2 } });
        ngsApiMocks.fetchFullJob.mockImplementation((jobId: string) => Promise.resolve(jobId === 'job-123' ? job123 : job456));
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });
        alignmentMocks.fetchAlignmentSessions.mockImplementation((jobId: string) => (
            jobId === 'job-123' ? Promise.reject(denial) : Promise.resolve([])
        ));
        alignmentMocks.isAlignmentAccessDenied.mockImplementation((reason) => reason === denial);
        client.setQueryData(['sequence-qc-manifest', 'job-123'], { schema: 'sequence_qc.manifest.v1' });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                        <SwitchJobButton onJob456Layout={() => releaseRotation()} />
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await waitUntil(() => expect(container.textContent).toContain('Restore this browser’s access'));
        const restore = [...container.querySelectorAll('button')].find(
            (button) => button.textContent === 'Restore this browser’s access',
        );
        await act(async () => restore?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
        await waitUntil(() => expect(alignmentMocks.rotateAlignmentAccess).toHaveBeenCalledWith('job-123'));
        const switchJob = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch job');
        await act(async () => {
            switchJob?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await Promise.resolve();
        });
        await waitUntil(() => expect(container.textContent).toContain('New FASTQ QC'));
        await flush();

        expect(alignmentMocks.fetchAlignmentSessions.mock.calls.filter(([jobId]) => jobId === 'job-123')).toHaveLength(1);
        expect(client.getQueryState(['sequence-qc-manifest', 'job-123'])?.isInvalidated).toBe(false);
        expect(container.textContent).not.toContain('Restoring access…');
        expect(container.querySelector('[role="alert"]')?.textContent || '').not.toContain('job-123');
    });

    it('does not offer recovery for non-403 session failures', async () => {
        const completedJob = {
            id: 'job-123', name: 'AAZ605 FASTQ QC', model_id: 'nanopore', mode: 'ont_fastq_qc',
            status: 'completed', created_at: '2026-08-10T00:00:00Z', output_dir: '/results/job-123', params: {},
        };
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [completedJob], total: 1 } });
        ngsApiMocks.fetchFullJob.mockResolvedValue(completedJob);
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });
        alignmentMocks.fetchAlignmentSessions.mockRejectedValue(new Error('manifest failed'));
        alignmentMocks.isAlignmentAccessDenied.mockReturnValue(false);

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await waitUntil(() => expect(alignmentMocks.fetchAlignmentSessions).toHaveBeenCalled());
        expect(container.textContent).not.toContain('Restore this browser’s access');
    });

    it('shows a failed post-rotation session refetch and leaves the manifest query valid', async () => {
        const completedJob = {
            id: 'job-123', name: 'AAZ605 FASTQ QC', model_id: 'nanopore', mode: 'ont_fastq_qc',
            status: 'completed', created_at: '2026-08-10T00:00:00Z', output_dir: '/results/job-123', params: {},
        };
        const denial = { response: { status: 403 } };
        const postRotationFailure = new Error('post-rotation session failed');
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [completedJob], total: 1 } });
        ngsApiMocks.fetchFullJob.mockResolvedValue(completedJob);
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });
        alignmentMocks.fetchAlignmentSessions
            .mockRejectedValueOnce(denial)
            .mockRejectedValueOnce(postRotationFailure);
        alignmentMocks.isAlignmentAccessDenied.mockImplementation((reason) => reason === denial);
        client.setQueryData(['sequence-qc-manifest', 'job-123'], { schema: 'sequence_qc.manifest.v1' });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await waitUntil(() => expect(container.textContent).toContain('Restore this browser’s access'));
        const restore = [...container.querySelectorAll('button')].find(
            (button) => button.textContent === 'Restore this browser’s access',
        );
        await act(async () => restore?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
        await waitUntil(() => expect(container.textContent).toContain('post-rotation session failed'));
        expect(client.getQueryState(['sequence-qc-manifest', 'job-123'])?.isInvalidated).toBe(false);
    });
});
