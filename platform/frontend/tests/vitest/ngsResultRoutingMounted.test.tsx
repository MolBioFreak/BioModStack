import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ngsApiMocks = vi.hoisted(() => ({
    fetchFullJob: vi.fn(),
    fetchJobStages: vi.fn(),
    fetchJobs: vi.fn(),
}));

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    ...ngsApiMocks,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        contextHref: (pathname: string) => pathname,
        updateQueryParams: vi.fn(),
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
    fetchAlignmentSessions: vi.fn(async () => []),
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
    it('maps NGS deep links to the Run Inspector view', () => {
        expect(isNgsJob({ model_id: 'nanopore', mode: 'ont_fastq_qc' })).toBe(true);
        for (const modelId of ['ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation']) {
            expect(isNgsJob({ model_id: modelId, mode: 'completed' })).toBe(true);
        }
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
});
