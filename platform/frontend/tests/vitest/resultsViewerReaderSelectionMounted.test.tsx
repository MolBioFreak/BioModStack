import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Job } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';

vi.mock('react-plotly.js', () => ({ default: () => <div data-plot /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div data-molstar /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({
    ConformationalMappingViewer: ({ requestId }: { requestId: string }) => <div data-cm-viewer={requestId}>CM Viewer</div>,
}));
vi.mock('../../src/components/MDResultsPane', () => ({
    default: ({ jobId }: { jobId: string }) => <div data-md-viewer={jobId}>MD Viewer</div>,
}));
vi.mock('../../src/components/BindCraft2JobResults', () => ({
    BindCraft2JobResults: ({ jobId }: { jobId: string }) => <div data-bc2-viewer={jobId}>BC2 Viewer</div>,
}));
vi.mock('../../src/components/NativeSequenceResults', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../src/components/NativeSequenceResults')>();
    return {
        ...actual,
        default: ({ job }: { job: Job }) => <div data-native-seq-viewer={job.id}>Native Seq Viewer</div>,
    };
});
vi.mock('../../src/components/NativeBinderGenerationResults', () => ({
    NativeBinderGenerationResults: ({ jobId }: { jobId: string }) => <div data-native-gen-viewer={jobId}>Native Gen Viewer</div>,
}));
vi.mock('../../src/components/frustrampnn/FrustraMpnnWorkbench', () => ({
    default: ({ job }: { job: Job }) => <div data-frustra-viewer={job.id}>Frustra Viewer</div>,
}));
vi.mock('../../src/components/BinderSelectedControls', () => ({
    default: () => <div data-binder-selected-controls />,
}));
vi.mock('../../src/components/BlindPoseSelectedControls', () => ({
    default: () => <div data-blind-pose-selected-controls />,
}));

let tree: ReactTestRenderer | undefined;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
const requests: Array<{ url: string; params: Record<string, unknown> }> = [];

const flush = async () => {
    for (let i = 0; i < 8; i += 1) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
    }
};

const text = (node: any): string => (
    typeof node === 'string' ? node : (node?.children ?? []).map(text).join('')
);

function installTransport(jobs: Job[], designs: Record<string, unknown>[] = []) {
    requests.length = 0;
    api.defaults.adapter = async (config) => {
        const url = String(config.url ?? '');
        const params = (config.params ?? {}) as Record<string, unknown>;
        requests.push({ url, params });
        let data: unknown = {};
        if (url === '/api/jobs') {
            data = { jobs, total: jobs.length };
        } else if (url.startsWith('/api/jobs/') && !url.includes('/', '/api/jobs/'.length)) {
            const id = url.slice('/api/jobs/'.length);
            data = jobs.find(j => j.id === id) ?? jobs[0];
        } else if (url === '/api/designs') {
            data = { designs, total: designs.length, model_counts: { boltz2: designs.length } };
        } else if (url.startsWith('/api/designs/')) {
            const id = url.slice('/api/designs/'.length);
            data = designs.find(d => d.id === id) ?? designs[0] ?? null;
        } else if (url.endsWith('/generation-results')) {
            data = { records: [], total: 0, offset: 0, limit: 1, receipt: {}, publication: {}, artifacts: [] };
        } else if (url.includes('/integration')) {
            data = { enabled: false };
        }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
}

async function mountRoute(route: string) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => {
        tree = create(
            <MemoryRouter initialEntries={[route]}>
                <QueryClientProvider client={client}>
                    <ThemeProvider>
                        <Routes>
                            <Route path="/designs/:jobId" element={<ResultsViewer />} />
                            <Route path="/designs" element={<ResultsViewer />} />
                        </Routes>
                    </ThemeProvider>
                </QueryClientProvider>
            </MemoryRouter>,
        );
    });
    await flush();
}

beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })));
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});

afterEach(async () => {
    await act(async () => tree?.unmount());
    tree = undefined;
    client?.clear();
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
});

const baseJob = (overrides: Partial<Job>): Job => ({
    id: 'job-1',
    name: 'Test job',
    status: 'completed',
    model_id: 'boltz2',
    mode: 'structure_prediction',
    params: {},
    created_at: '2026-11-25T00:00:00Z',
    design_count: 0,
    ...overrides,
});

describe('ResultsViewer reader selection above generic Design workbench', () => {
    const nonDesignCases: Array<{ label: string; job: Job; route: string; marker: string }> = [
        {
            label: 'conformational_mapping',
            job: baseJob({ id: 'cm-1', model_id: 'conformational_mapping', mode: 'protenix_v2_ensemble', conformational_mapping_request_id: 'cm-req-1' }),
            route: '/designs/cm-1',
            marker: 'data-cm-viewer',
        },
        {
            label: 'bindcraft2',
            job: baseJob({ id: 'bc2-1', model_id: 'bindcraft2', mode: 'campaign' }),
            route: '/designs/bc2-1?design_id=bc2-cand-1',
            marker: 'data-bc2-viewer',
        },
        {
            label: 'molecular_dynamics',
            job: baseJob({ id: 'md-1', model_id: 'molecular_dynamics', mode: 'gromacs' }),
            route: '/designs/md-1',
            marker: 'data-md-viewer',
        },
        {
            label: 'frustrampnn',
            job: baseJob({ id: 'frustra-1', model_id: 'frustrampnn', mode: 'analyze' }),
            route: '/designs/frustra-1?result_model=frustrampnn',
            marker: 'data-frustra-viewer',
        },
        {
            label: 'native_binder_generation',
            job: baseJob({ id: 'laproteina-1', model_id: 'laproteina', mode: 'evaluate' }),
            route: '/designs/laproteina-1?design_id=lp-cand-1',
            marker: 'data-native-gen-viewer',
        },
        {
            label: 'native_sequence',
            job: baseJob({ id: 'caliby-1', model_id: 'caliby_experimental', mode: 'ensemble_design' }),
            route: '/designs/caliby-1',
            marker: 'data-native-seq-viewer',
        },
    ];

    for (const testCase of nonDesignCases) {
        it(`does not run generic Design queries for ${testCase.label}`, async () => {
            installTransport([testCase.job]);
            await mountRoute(testCase.route);
            expect(tree!.root.findAllByProps({ [testCase.marker]: testCase.label === 'conformational_mapping' ? 'cm-req-1' : testCase.job.id })).toHaveLength(1);
            const designCalls = requests.filter(req => req.url === '/api/designs' || req.url.startsWith('/api/designs/'));
            expect(designCalls).toEqual([]);
        });
    }

    it('opens a Design result with the same data by direct URL and through its Project (C43)', async () => {
        const job = baseJob({ id: 'boltz-1', name: 'Boltz prediction', model_id: 'boltz2', mode: 'structure_prediction', design_count: 1 });
        const design = {
            id: 'design-1',
            job_id: 'boltz-1',
            name: 'Boltz_candidate_01',
            pdb_path: 'boltz-1/cand1.cif',
            plddt: 91.4,
            iptm: 0.86,
            provenance: { model_id: 'boltz2', producer_model_id: 'boltz2' },
            viewer_capabilities: [],
            supported_analyzers: [],
        };

        installTransport([job], [design]);
        await mountRoute('/designs/boltz-1');
        const directText = text(tree!.root);
        const directDesignCalls = requests.filter(req => req.url === '/api/designs').length;
        expect(directText).toContain('Boltz_candidate_01');
        expect(directDesignCalls).toBeGreaterThanOrEqual(1);

        await act(async () => tree?.unmount());
        tree = undefined;
        client.clear();

        installTransport([job], [design]);
        await mountRoute('/designs/boltz-1?return_to=%2Fprojects%2Fproj-1&launch_context_id=lc-1');
        const projectText = text(tree!.root);
        const projectDesignCalls = requests.filter(req => req.url === '/api/designs').length;
        expect(projectText).toContain('Boltz_candidate_01');
        expect(projectDesignCalls).toBeGreaterThanOrEqual(1);
    });
});
