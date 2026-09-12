import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseOntFastqQcResult } from '../../src/lib/ontFastqQcResult';
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
    bindAlignmentSessionsToResultAuthority: vi.fn((sessions: unknown[]) => sessions),
    describeNgsError: vi.fn((_reason: unknown, fallback: string) => fallback),
    disposeAlignmentAccess: vi.fn(),
    fetchAlignmentSessions: vi.fn(),
    fetchAlignmentPresentation: vi.fn(),
    createAlignmentLocusSlice: vi.fn(),
    isAlignmentAccessDenied: vi.fn(),
    rotateAlignmentAccess: vi.fn(),
}));
const igvMocks = vi.hoisted(() => ({ createBrowser: vi.fn(), removeBrowser: vi.fn() }));
vi.mock('igv', () => ({ default: { ...igvMocks, version: () => '3.7.3' } }));

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
vi.mock('../../src/lib/ngsAlignmentSession', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/ngsAlignmentSession')>()),
    ...alignmentMocks,
}));

vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div>Molstar</div> }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({
    ConformationalMappingViewer: () => <div>Conformational mapping</div>,
}));

import { api } from '../../src/lib/api';
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
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Unrelated API read blocked by mounted test'));
    ngsApiMocks.fetchFullJob.mockReset();
    ngsApiMocks.fetchJobStages.mockReset();
    ngsApiMocks.fetchJobs.mockReset();
    contextMocks.updateQueryParams.mockReset();
    alignmentMocks.fetchAlignmentSessions.mockReset();
    alignmentMocks.fetchAlignmentSessions.mockResolvedValue([]);
    alignmentMocks.disposeAlignmentAccess.mockReset();
    alignmentMocks.disposeAlignmentAccess.mockResolvedValue(undefined);
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
    vi.restoreAllMocks();
    document.body.replaceChildren();
    vi.useRealTimers();
});

describe('preview-independent mounted locus loading', () => {
    it('keeps every NGS destination in a bounded wrapping navigation group', async () => {
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [], total: 0 } });
        await act(async () => root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={['/ngs?section=analyses']}>
                    <NGSToolkit />
                </MemoryRouter>
            </QueryClientProvider>,
        ));
        const nav = container.querySelector('nav[aria-label="NGS navigation"]') as HTMLElement;
        expect(nav).toBeTruthy();
        expect(nav.classList.contains('flex-wrap')).toBe(true);
        expect(nav.classList.contains('max-w-full')).toBe(true);
        expect(nav.parentElement?.classList.contains('flex-wrap')).toBe(true);
        for (const label of ['Data Analysis', 'Instrument setup', 'Runs', 'Mol Bio Toolkit']) {
            expect([...nav.querySelectorAll('button')].some((button) => button.textContent === label)).toBe(true);
        }
    });

    it.each(['rejected', 'pending'] as const)('loads a detailed locus when the oversized BAM preview is %s', async (previewState) => {
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const hash = 'a'.repeat(64);
        const artifact = (role: string, size = 1024) => ({
            artifact_id: role, url: `/api/jobs/job-123/artifacts/${role}`, sha256: hash,
            source_manifest_sha256: hash, size_bytes: size, mime_type: 'application/octet-stream', range_capable: true,
        });
        const session = {
            schema: 'bms.ngs.alignment-session.v1', job_id: 'job-123', session_id: 'session-123',
            mode: 'primary', ready: true, unavailable_reason: null,
            reads_url: '/api/jobs/job-123/alignment-sessions/session-123/reads',
            sequence_qc_manifest_sha256: hash, verification_manifest_sha256: hash,
            artifact_set_sha256: hash, alignment_pair_sha256: hash,
            reference: { contig: 'plasmid', length_bp: 1000, topology: 'circular',
                normalized_sequence_sha256: hash, fasta_sha256: hash, fai_sha256: hash },
            artifacts: { alignment: artifact('alignment', 67_000_000), alignment_index: artifact('alignment_index'),
                reference: artifact('reference'), reference_index: artifact('reference_index') },
        };
        const result = parseOntFastqQcResult(JSON.parse(readFileSync(resolve(process.cwd(),
            '../api/tests/fixtures/ont_fastq_qc_result_retry3_v1.json'), 'utf8').replaceAll(
            '31f02bd5-830f-4558-aa78-3873c515de68', 'job-123')), 'job-123');
        client.setQueryData(['ont-fastq-qc-result', 'job-123', 'completed', 'ont_fastq_qc'], result);
        const job = { id: 'job-123', name: 'Oversized FASTQ QC', model_id: 'nanopore', mode: 'ont_fastq_qc',
            status: 'completed', created_at: '2026-08-10T00:00:00Z', output_dir: '/results/job-123',
            params: { workflow_id: 'ont_fastq_qc', fastq_path: '/inputs/reads.fastq' } };
        ngsApiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [job], total: 1 } });
        ngsApiMocks.fetchFullJob.mockResolvedValue(job);
        ngsApiMocks.fetchJobStages.mockResolvedValue({ data: { stages: [] } });
        alignmentMocks.fetchAlignmentSessions.mockResolvedValue([session]);
        alignmentMocks.fetchAlignmentPresentation.mockReset();
        alignmentMocks.createAlignmentLocusSlice.mockReset();
        let resolvePreview!: (value: unknown) => void;
        const preview = new Promise((resolve) => { resolvePreview = resolve; });
        alignmentMocks.fetchAlignmentPresentation.mockImplementation(() => previewState === 'rejected'
            ? Promise.reject(new Error('preview unavailable')) : preview);
        let resolveSlice!: (value: unknown) => void;
        alignmentMocks.createAlignmentLocusSlice.mockImplementation(() => new Promise((resolve) => { resolveSlice = resolve; }));
        type Track = { type?: string; name?: string; url?: string; id?: string };
        const tracks: Track[] = [];
        const browser = {
            // Deliberately never emit locuschange: initialization must seed the authoritative locus.
            on: vi.fn(), off: vi.fn(), search: vi.fn().mockResolvedValue(undefined), trackViews: [],
            findTracks: vi.fn((predicate: (track: Track) => boolean) => tracks.filter(predicate)),
            removeTrack: vi.fn((track: Track) => { tracks.splice(tracks.indexOf(track), 1); }),
            loadTrack: vi.fn(async (config: Track) => { const track = { ...config }; tracks.push(track); return track; }),
        };
        igvMocks.createBrowser.mockReset();
        igvMocks.createBrowser.mockResolvedValue(browser);
        vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
            if (String(input) === session.artifacts.reference.url) return new Response(`>plasmid\n${'A'.repeat(1000)}\n`);
            throw new Error(`Unexpected mounted-test network request: ${String(input)}`);
        }));
        await act(async () => root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=job-123']}>
                    <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                </MemoryRouter>
            </QueryClientProvider>,
        ));
        const button = (label: string) => [...document.querySelectorAll('button')].find((entry) => entry.textContent === label);
        await waitUntil(() => expect(button('Open local IGV')?.disabled).toBe(false));
        await act(async () => button('Open local IGV')!.click());
        await waitUntil(() => expect(igvMocks.createBrowser).toHaveBeenCalledTimes(1));
        await waitUntil(() => expect(alignmentMocks.fetchAlignmentPresentation).toHaveBeenCalled());
        for (let index = 0; index < 5; index += 1) await flush();
        expect(alignmentMocks.fetchAlignmentPresentation).toHaveBeenCalledTimes(1);
        expect(browser.loadTrack).not.toHaveBeenCalled();
        await waitUntil(() => expect(button('Load locus reads')?.disabled).toBe(false));
        await act(async () => button('Load locus reads')!.click());
        expect(alignmentMocks.createAlignmentLocusSlice).toHaveBeenCalledWith('job-123', 'session-123', {
            contig: 'plasmid', start: 1, end: 1000,
        });
        const slice = {
            schema: 'bms.ngs.alignment-locus-slice.v1', job_id: 'job-123', session_id: 'session-123',
            slice_id: hash, state: 'ready', contig: 'plasmid', start_1based: 1, end_1based: 1000,
            overlapping_read_count: 7000, selected_read_count: 5000, selected_record_count: 5100, capped: true,
            policy: { id: 'bounded-full-source-locus-slice', version: 1, max_reads: 5000,
                max_records: 20_000, max_bytes: 67_108_864, max_span_bp: 1_000_000, max_seconds: 60 },
            bam: { ...artifact('locus-bam'), kind: 'alignment_locus_slice' },
            index: { ...artifact('locus-bai'), kind: 'alignment_locus_slice_index' },
            manifest: { ...artifact('locus-manifest'), kind: 'alignment_locus_slice_manifest' },
        };
        if (previewState === 'pending') {
            await act(async () => resolvePreview({
                schema: 'bms.ngs.alignment-presentation.v1', job_id: 'job-123', session_id: 'session-123',
                mode: 'primary', state: 'ready', source: { primary_read_count: 10000, alignment_size_bytes: 67_000_000,
                    package_manifest_sha256: hash, alignment_sha256: hash, alignment_index_sha256: hash,
                    alignment_index_size_bytes: 1024, alignment_record_count: 10500 },
                policy: { id: 'primary-read-presentation-v3', version: 3, target_reads: 2000,
                    max_preview_bytes: 67_108_864, max_coverage_bins: 10000, max_seconds: 120 },
                preview: { kind: 'primary_read_preview', selected_read_count: 2000, selected_record_count: 2000,
                    selected_read_set_sha256: hash, forward_count: 1000, reverse_count: 1000,
                    bam: { ...artifact('preview-bam'), kind: 'alignment_preview' },
                    index: { ...artifact('preview-bai'), kind: 'alignment_preview_index' } },
                coverage: { kind: 'full_source_primary_coverage', bin_width_bp: 10, primary_read_count: 10000,
                    artifact: { ...artifact('preview-coverage'), kind: 'full_source_primary_coverage', mime_type: 'text/plain' } },
                manifest: { ...artifact('preview-manifest'), kind: 'alignment_presentation_manifest', mime_type: 'application/json' },
            }));
            for (let index = 0; index < 5; index += 1) await flush();
            expect(browser.loadTrack).not.toHaveBeenCalled();
        }
        await act(async () => resolveSlice(slice));
        await waitUntil(() => expect(tracks.some((track) => track.url === slice.bam.url)).toBe(true));
        await waitUntil(() => expect(document.querySelector('.ngs-alignment-presentation-status')?.textContent).toBe('Locus reads · 5,000 of 7,000 reads'));
        for (let index = 0; index < 5; index += 1) await flush();
        expect(alignmentMocks.fetchAlignmentPresentation).toHaveBeenCalledTimes(1);
        expect(tracks.filter((track) => track.type === 'alignment').map((track) => track.url)).toEqual([slice.bam.url]);
        expect(browser.loadTrack.mock.calls.every(([config]) => config.url === slice.bam.url)).toBe(true);
        expect(document.querySelector('.ngs-alignment-presentation-status')?.textContent).toBe('Locus reads · 5,000 of 7,000 reads');
    });
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
        expect(client.getQueryState(['sequence-qc-manifest', 'job-123'])).toBeUndefined();
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
