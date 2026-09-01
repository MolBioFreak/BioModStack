import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    buildMolecularDynamicsPredictionReturnRoute,
    buildResultsViewerMolecularDynamicsRoute,
} from '../../src/components/gen2StartingStructureState.js';

const apiMocks = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    getLaunchContext: vi.fn(),
    fetchModels: vi.fn(async () => ({ data: [] })),
    fetchTemplates: vi.fn(async () => ({ data: [] as Array<Record<string, unknown>> })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
}));

vi.mock('../../src/lib/projectManager', () => ({
    getLaunchContext: apiMocks.getLaunchContext,
}));

vi.mock('../../src/lib/api', () => ({
    api: { get: apiMocks.get, post: apiMocks.post },
    EXECUTION_TARGET_STORAGE_KEY: 'bms.execution-target-id',
    VAST_DISCOVERY_QUERY_KEY: ['execution-targets', 'providers', 'vast', 'inventory'],
    activateExecutionTarget: vi.fn(),
    completeCurrentLaunchContext: vi.fn(async () => null),
    deactivateExecutionTarget: vi.fn(),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    fetchModels: apiMocks.fetchModels,
    fetchTemplates: apiMocks.fetchTemplates,
    fetchInputPresets: apiMocks.fetchInputPresets,
    fetchFiles: vi.fn(async () => ({ data: { entries: [] } })),
    fetchTemplateById: vi.fn(async () => ({ data: null })),
    refreshVastExecutionTargets: vi.fn(async () => ({ data: { instances: [] } })),
    submitJob: vi.fn(),
    uploadFile: vi.fn(),
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({
    ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }),
}));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/SequenceManager', () => ({ SequenceManager: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: () => null }));
vi.mock('../../src/components/MutagenesisTemplate', () => ({ MutagenesisTemplate: () => null }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/ProteinModificationTemplate', () => ({ ProteinModificationTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({
    StructurePredictionTemplate: ({ sourceSequenceId, mdDraftId }: { sourceSequenceId?: string | null; mdDraftId?: string | null }) => {
        const navigate = useNavigate();
        return (
            <section data-mounted-structure-prediction>
                <h1>Mounted Structure Prediction handoff</h1>
                <p data-source-sequence-id>{sourceSequenceId}</p>
                <button
                    type="button"
                    onClick={() => navigate(buildMolecularDynamicsPredictionReturnRoute(mdDraftId as string, { id: '66666666-6666-4666-8666-666666666666' }))}
                >
                    Complete prediction and return
                </button>
            </section>
        );
    },
}));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-md-starting-structure-viewer /> }));

import { JobSubmission } from '../../src/components/JobSubmission';

const hash = (value: string) => value.repeat(64);
const profile = {
    schema: 'bms.md.chemistry-profile.v1',
    id: 'amber_ff19sb_opc_protein_v1',
    version: '1.0.0',
    profile_sha256: hash('a'),
    display_name: 'AMBER ff19SB + OPC protein',
    family: 'amber',
    assurance: 'validated',
    legacy: false,
    automatic_preparation: true,
    inventory_class: 'selectable',
    availability_explanation: 'Exact managed fixture only.',
    supported_engines: ['gromacs'],
    v1_preparation: { force_field: 'amber99sb-ildn', water_model: 'tip3p' },
    launch_constraints: {
        input_mode: 'structure', structure_sha256: hash('b'), replicas: 1, engine: 'gromacs', force_field: 'ff19SB',
        water_model: 'OPC', timestep_fs: 2, temperature_k: 300, pressure_bar: 1, salt_molar: 0.15, padding_nm: 1,
        max_production_steps: 5000, max_minimization_steps: 50000, max_nvt_steps: 50000, max_npt_steps: 50000,
    },
    scientific_validation: { validated: true, lane: 'short-gpu', version: '1.0.0', scope: { launch_scope: 'short_gpu', system_classes: ['protein'] } },
    states: { installed: true, runtime_validated: true, scientifically_validated: true, operator_enabled: true, asset_probe_success: true, selectable: true },
    explicit_exclusions: [],
    runtime_identity: { runtime_id: 'md-preparation-v1', runtime_version: '1', sif_sha256: hash('f') },
};
const sequenceId = '55555555-5555-4555-8555-555555555555';
const draftId = '44444444-4444-4444-8444-444444444444';
const response = <T,>(data: T) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
    return { promise, resolve };
};

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;

beforeEach(() => {
    apiMocks.get.mockReset();
    apiMocks.post.mockReset();
    apiMocks.getLaunchContext.mockReset();
    apiMocks.fetchTemplates.mockReset();
    apiMocks.fetchTemplates.mockResolvedValue({ data: [] });
    apiMocks.get.mockImplementation(async (url: string) => {
        if (url === '/api/molecular-dynamics/chemistry-profiles') {
            return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
        }
        if (url === '/api/user-sequences') return response([]);
        if (url === '/api/molecular-dynamics/prediction-jobs/66666666-6666-4666-8666-666666666666/source-candidates') {
            return response({
                schema_version: 'bms.md.prediction-source-candidates.v1',
                job: { id: '66666666-6666-4666-8666-666666666666', name: 'Round-trip prediction', status: 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null },
                candidates: [],
                next_cursor: null,
            });
        }
        throw new Error(`unexpected GET ${url}`);
    });
    apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
        if (url === '/api/user-sequences') {
            return response({ id: sequenceId, name: body.name, sequence: body.sequence, description: null, length: 6, organism: null, uniprot_id: null, ncbi_id: null, is_preset: false, created_at: '2026-08-26T00:00:00Z', updated_at: null });
        }
        throw new Error(`unexpected POST ${url}`);
    });
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(draftId);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    vi.restoreAllMocks();
    sessionStorage.clear();
    document.body.replaceChildren();
});

const renderShell = async (
    entry = '/submit?template=molecular_dynamics',
    expectedText = 'Molecular Dynamics',
) => {
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={[entry]}>
                    <JobSubmission />
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(expectedText)); });
};

const click = async (label: string) => {
    const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(label));
    expect(button, label).toBeTruthy();
    await act(async () => button?.click());
};

const completeRoundTrip = async () => {
    await click('Open Structure Prediction');
    await act(async () => { await vi.waitFor(() => expect(container.querySelector('[data-mounted-structure-prediction]')).not.toBeNull()); });
    expect(container.querySelector('[data-source-sequence-id]')?.textContent).toBe(sequenceId);
    await click('Complete prediction and return');
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Molecular Dynamics')); });
    expect(container.querySelector('[data-mounted-structure-prediction]')).toBeNull();
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Returned from Structure Prediction')); });
};

describe('mounted JobSubmission same-route MD handoff ownership', () => {
    it('shows exactly De Novo Design and Molecular Dynamics as Experimental workflows', async () => {
        apiMocks.fetchTemplates.mockResolvedValueOnce({
            data: [
                { id: 'protein_cad_experimental', name: 'Protein CAD Experimental', experimental: true, stages: [] },
                { id: 'protein_local_redesign', name: 'RFD3 Local Redesign', experimental: true, stages: [] },
                { id: 'confornets_experimental', name: 'ConforNets Experimental', experimental: true, stages: [] },
                { id: 'molecular_dynamics', name: 'Molecular Dynamics', experimental: true, stages: [] },
            ],
        });
        await renderShell('/submit', 'New Experiment');
        await click('Experimental');
        await act(async () => {
            await vi.waitFor(() => expect(container.textContent).toContain('Choose active alpha:'));
        });

        const cards = container.querySelector('.grid.grid-cols-2.gap-3');
        expect(cards?.children).toHaveLength(2);
        expect(cards?.textContent).toContain('De Novo Design');
        expect(cards?.textContent).toContain('Molecular Dynamics');
        expect(cards?.textContent).not.toContain('Protein CAD Experimental');
        expect(cards?.textContent).not.toContain('RFD3 Local Redesign');
        expect(cards?.textContent).not.toContain('ConforNets Experimental');
    });

    it('proves the ResultsViewer Design in the supplied completed prediction Job before exact inspection', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designId = '77777777-7777-4777-8777-777777777777';
        const exactInspection = {
            schema_version: 'bms.md.starting-structure-inspection.v1',
            source_ref: { kind: 'design', id: designId },
            identity: { label: 'ResultsViewer selected Design', format: 'pdb', size_bytes: 100, sha256: hash('b'), pdb_id: null, producer_job_id: jobId, design_id: designId },
            viewer: { url: `/api/molecular-dynamics/starting-structures/design/${designId}/content?expected_sha256=${hash('b')}`, format: 'pdb', sha256: hash('b') },
            inspection: { model_count: 1, chains: ['A'], atom_count: 10, hetero_components: [], parser: { name: 'biopython', version: '1.85' } },
            admission: { state: 'profile_required', profile_id: null, code: 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Select chemistry for the exact Design.' },
        };
        const inspectRequest = deferred<ReturnType<typeof response>>();
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1',
                    job: { id: jobId, name: 'ResultsViewer prediction Job', status: 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null },
                    candidates: [{ source_ref: { kind: 'design', id: designId }, name: 'selected-design', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 90, ptm: 0.8, iptm: null, confidence: 0.9 }, created_at: null }],
                    next_cursor: null,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                expect(body).toEqual({ source_ref: { kind: 'design', id: designId }, chemistry_profile_id: null });
                return inspectRequest.promise;
            }
            throw new Error(`unexpected POST ${url}`);
        });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[buildResultsViewerMolecularDynamicsRoute(jobId, designId)]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => {
            await vi.waitFor(() => expect(apiMocks.post.mock.calls, JSON.stringify(apiMocks.post.mock.calls)).toContainEqual([
                '/api/molecular-dynamics/starting-structures/inspect',
                { source_ref: { kind: 'design', id: designId }, chemistry_profile_id: null },
            ]));
        });
        await act(async () => inspectRequest.resolve(response(exactInspection)));
        await act(async () => {
            await vi.waitFor(() => expect(container.textContent, JSON.stringify(apiMocks.post.mock.calls)).toContain('ResultsViewer selected Design'));
        });

        expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: designId }, chemistry_profile_id: null },
        );
        expect(apiMocks.get.mock.calls).toContainEqual([
            `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`,
            { params: { limit: 24 } },
        ]);
        expect(container.textContent).toContain('selected-design');
        expect([...container.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')]
            .find((button) => button.textContent?.includes('selected-design'))?.getAttribute('aria-pressed')).toBe('true');
    });

    it('fails closed when the supplied Design is absent from the supplied prediction Job', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const routedDesignId = '77777777-7777-4777-8777-777777777777';
        const foreignDesignId = '88888888-8888-4888-8888-888888888888';
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1',
                    job: { id: jobId, name: 'Supplied prediction Job', status: 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null },
                    candidates: [{ source_ref: { kind: 'design', id: foreignDesignId }, name: 'must-not-select', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 80, ptm: 0.7, iptm: null, confidence: 0.8 }, created_at: null }],
                    next_cursor: null,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[buildResultsViewerMolecularDynamicsRoute(jobId, routedDesignId)]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull()); });
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('does not belong to the supplied prediction Job');
        expect(container.textContent).not.toContain('must-not-select');
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/molecular-dynamics/starting-structures/inspect')).toBe(false);
    });

    it('fails closed when the supplied Job projection names an unsupported producer', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designId = '77777777-7777-4777-8777-777777777777';
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1',
                    job: { id: jobId, name: 'Unsupported producer Job', status: 'completed', model_id: 'molecular_dynamics', mode: 'simulate', created_at: null, started_at: null, completed_at: null, failure: null },
                    candidates: [{ source_ref: { kind: 'design', id: designId }, name: 'must-not-inspect', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: null, ptm: null, iptm: null, confidence: null }, created_at: null }],
                    next_cursor: null,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[buildResultsViewerMolecularDynamicsRoute(jobId, designId)]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull()); });
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('accepted Structure Prediction producer');
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/molecular-dynamics/starting-structures/inspect')).toBe(false);
    });

    it('uses bounded cursor pagination to prove and inspect the exact later-page Design', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const firstDesignId = '77777777-7777-4777-8777-777777777771';
        const routedDesignId = '77777777-7777-4777-8777-777777777772';
        const job = { id: jobId, name: 'Paged prediction Job', status: 'completed', model_id: 'protenix', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null };
        apiMocks.get.mockImplementation(async (url: string, config?: { params?: { cursor?: string } }) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                if (config?.params?.cursor === 'cursor-page-2') {
                    return response({
                        schema_version: 'bms.md.prediction-source-candidates.v1', job,
                        candidates: [{ source_ref: { kind: 'design', id: routedDesignId }, name: 'exact-later-page-design', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 92, ptm: 0.85, iptm: null, confidence: 0.91 }, created_at: null }],
                        next_cursor: null,
                    });
                }
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1', job,
                    candidates: [{ source_ref: { kind: 'design', id: firstDesignId }, name: 'first-page-design', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 80, ptm: 0.7, iptm: null, confidence: 0.8 }, created_at: null }],
                    next_cursor: 'cursor-page-2',
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                expect(body).toEqual({ source_ref: { kind: 'design', id: routedDesignId }, chemistry_profile_id: null });
                return response({
                    schema_version: 'bms.md.starting-structure-inspection.v1',
                    source_ref: { kind: 'design', id: routedDesignId },
                    identity: { label: 'Exact later-page Design bytes', format: 'pdb', size_bytes: 100, sha256: hash('b'), pdb_id: null, producer_job_id: jobId, design_id: routedDesignId },
                    viewer: { url: `/api/molecular-dynamics/starting-structures/design/${routedDesignId}/content?expected_sha256=${hash('b')}`, format: 'pdb', sha256: hash('b') },
                    inspection: { model_count: 1, chains: ['A'], atom_count: 10, hetero_components: [], parser: { name: 'biopython', version: '1.85' } },
                    admission: { state: 'profile_required', profile_id: null, code: 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Select chemistry for the exact later-page Design.' },
                });
            }
            throw new Error(`unexpected POST ${url}`);
        });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[buildResultsViewerMolecularDynamicsRoute(jobId, routedDesignId)]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Exact later-page Design bytes')); });
        expect(apiMocks.get.mock.calls).toContainEqual([
            `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`,
            { params: { limit: 24, cursor: 'cursor-page-2' } },
        ]);
        expect(container.textContent).toContain('exact-later-page-design');
        expect(container.textContent).not.toContain('first-page-design');
        expect(apiMocks.post).toHaveBeenCalledTimes(1);
    });

    it('rejects an ambiguous duplicated ResultsViewer Design identity before inspection', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designA = '77777777-7777-4777-8777-777777777771';
        const designB = '77777777-7777-4777-8777-777777777772';
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[`/submit?template=molecular_dynamics&source_prediction_job_id=${jobId}&source_design_id=${designA}&source_design_id=${designB}`]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });

        expect(container.querySelector('[role="alert"]')?.textContent).toContain('duplicate source_design_id');
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/molecular-dynamics/starting-structures/inspect')).toBe(false);
    });

     it('remounts a deferred prepared Project MD route with the exact pinned intent and chemistry identity', async () => {
        const contextRequest = deferred<Record<string, unknown>>();
        const sourceDesignId = '77777777-7777-4777-8777-777777777777';
        const requestedSettings = {
            replicas: 1,
            random_seed: 23,
            padding_nm: 1,
            salt_molar: 0.15,
            neutralize: false,
            temperature_k: 300,
            pressure_bar: 1,
            timestep_fs: 2,
            minimization_steps: 42000,
            nvt_ps: 80,
            npt_ps: 90,
            production_ns: 0.002,
            trajectory_interval_ps: 0.5,
            energy_interval_ps: 0.1,
            checkpoint_interval_minutes: 11,
            ntomp: 6,
        };
        apiMocks.getLaunchContext.mockReturnValue(contextRequest.promise);
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                return response({
                    schema_version: 'bms.md.starting-structure-inspection.v1',
                    source_ref: { kind: 'design', id: sourceDesignId },
                    identity: { label: 'Prepared Project Design', format: 'pdb', size_bytes: 100, sha256: hash('b'), pdb_id: null, producer_job_id: null, design_id: sourceDesignId },
                    viewer: { url: `/api/molecular-dynamics/starting-structures/design/${sourceDesignId}/content?expected_sha256=${hash('b')}`, format: 'pdb', sha256: hash('b') },
                    inspection: { model_count: 1, chains: ['A'], atom_count: 10, hetero_components: [], parser: { name: 'biopython', version: '1.85' } },
                    admission: { state: body.chemistry_profile_id === profile.id ? 'admitted' : 'profile_required', profile_id: body.chemistry_profile_id, code: body.chemistry_profile_id === profile.id ? null : 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Prepared Project admission.' },
                });
            }
            throw new Error(`unexpected POST ${url}`);
        });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[`/submit?template=molecular_dynamics&launch_context_id=launch-context-md&source_design_id=${sourceDesignId}`]}>
                        <JobSubmission />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        expect(container.textContent).toContain('Resolving immutable Project launch authority…');
        expect(container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]')).toBeNull();

        await act(async () => contextRequest.resolve({
            schema: 'bms.launch-context.v2',
            launch_context_id: 'launch-context-md',
            project_id: 'project:md',
            global_experiment_id: 'global:md',
            domain_experiment_id: 'domain:md',
            workflow_id: 'workflow:md',
            workflow_revision_id: 'revision:md',
            preparation_id: 'preparation:md',
            pinned_gpu: null,
            return_uri: '/projects/project%3Amd',
            source_receipt_id: 'receipt:md',
            state: 'issued',
            pinned_scheduler: {
                name: 'Prepared MD exact',
                model_id: 'molecular_dynamics',
                mode: 'simulate',
                params: {
                    schema_version: 'bms.md.launch-intent.v1',
                    name: 'Prepared MD exact',
                    source_ref: { kind: 'design', id: sourceDesignId },
                    expected_source_sha256: hash('b'),
                    chemistry_profile_id: profile.id,
                    chemistry_profile_sha256: profile.profile_sha256,
                    catalog_digest: hash('c'),
                    requested_settings: requestedSettings,
                },
            },
            issued_at: '2026-08-26T00:00:00Z',
            expires_at: '2026-08-26T01:00:00Z',
        }));
        await act(async () => {
            await vi.waitFor(() => expect(container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]')?.value).toBe('23'));
        });

        expect(container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]')?.value).toBe(profile.id);
        expect([...container.querySelectorAll<HTMLInputElement>('input')].find((input) => input.value === 'Prepared MD exact')).toBeTruthy();
        expect(apiMocks.post.mock.calls).toContainEqual([
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: sourceDesignId }, chemistry_profile_id: profile.id },
        ]);
        const valuesByLabel = Object.fromEntries([...container.querySelectorAll('label')].map((label) => [
            label.textContent?.replace(/\s+/g, ' ').trim(),
            label.querySelector<HTMLInputElement>('input[type="number"]')?.value,
        ]));
        expect(Object.entries(valuesByLabel)).toEqual(expect.arrayContaining([
            [expect.stringContaining('Minimization'), '42000'],
            [expect.stringContaining('NVT equilibration'), '80'],
            [expect.stringContaining('NPT equilibration'), '90'],
            [expect.stringContaining('Production per replica'), '0.002'],
            [expect.stringContaining('Trajectory interval'), '0.5'],
            [expect.stringContaining('Energy/log interval'), '0.1'],
            [expect.stringContaining('Checkpoint interval'), '11'],
            [expect.stringContaining('CPU threads per replica'), '6'],
        ]));
        expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false);
    });

    it('round-trips a newly persisted UserSequence through Structure Prediction back to MD', async () => {
        await renderShell();
        await click('Predict structure from sequence');
        const name = [...container.querySelectorAll<HTMLInputElement>('input')].find((item) => item.value === 'MD candidate');
        const sequence = container.querySelector<HTMLTextAreaElement>('textarea');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(name, 'Candidate A');
            name?.dispatchEvent(new Event('input', { bubbles: true }));
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(sequence, 'ACDEFG');
            sequence?.dispatchEvent(new Event('input', { bubbles: true }));
        });

        await completeRoundTrip();
        expect(apiMocks.post).toHaveBeenCalledWith('/api/user-sequences', { name: 'Candidate A', sequence: 'ACDEFG' });
    });

    it('round-trips an existing saved UserSequence without creating a duplicate', async () => {
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === '/api/user-sequences') {
                return response([{ id: sequenceId, name: 'Saved candidate', sequence: 'ACDEFG', description: null, length: 6, organism: null, uniprot_id: null, ncbi_id: null, is_preset: false, created_at: '2026-08-26T00:00:00Z', updated_at: null }]);
            }
            if (url === '/api/molecular-dynamics/prediction-jobs/66666666-6666-4666-8666-666666666666/source-candidates') {
                return response({ schema_version: 'bms.md.prediction-source-candidates.v1', job: { id: '66666666-6666-4666-8666-666666666666', name: 'Round-trip prediction', status: 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null }, candidates: [], next_cursor: null });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        await renderShell();
        await click('Predict structure from sequence');
        await click('Use saved sequence');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Saved candidate')); });
        const savedSelect = container.querySelector<HTMLSelectElement>('[data-md-saved-sequence]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(savedSelect, sequenceId);
            savedSelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });

        await completeRoundTrip();
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/user-sequences')).toBe(false);
    });
});
