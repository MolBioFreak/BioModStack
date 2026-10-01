import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    buildMolecularDynamicsPredictionReturnRoute,
    storeMolecularDynamicsDraft,
} from '../../src/components/gen2StartingStructureState.js';

const apiMocks = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    getLaunchContext: vi.fn(),
    completeCurrentLaunchContext: vi.fn(async (): Promise<string | null> => null),
    fetchModels: vi.fn(async () => ({ data: [] })),
    fetchTemplates: vi.fn(async () => ({ data: [] as Array<Record<string, unknown>> })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
}));

vi.mock('../../src/lib/projectManager', () => ({
    getLaunchContext: apiMocks.getLaunchContext,
}));

vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/lib/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../src/lib/api')>();
    return {
    // Keep the real placement authority; only transport dependencies are mocked.
    prepareExecutionPlacement: actual.prepareExecutionPlacement,
    api: { get: apiMocks.get, post: apiMocks.post },
    EXECUTION_TARGET_STORAGE_KEY: actual.EXECUTION_TARGET_STORAGE_KEY,
    VAST_DISCOVERY_QUERY_KEY: ['execution-targets', 'providers', 'vast', 'inventory'],
    activateExecutionTarget: vi.fn(),
    completeCurrentLaunchContext: apiMocks.completeCurrentLaunchContext,
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
    };
});
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
vi.mock('../../src/components/MolstarViewer', () => ({ default: ({ structureUrl, onLoadStateChange }: { structureUrl?: string; onLoadStateChange?: (state: 'loaded') => void }) => {
    React.useEffect(() => { if (structureUrl) onLoadStateChange?.('loaded'); }, [structureUrl, onLoadStateChange]);
    return <div data-md-starting-structure-viewer />;
} }));

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
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;

beforeEach(() => {
    apiMocks.get.mockReset();
    apiMocks.post.mockReset();
    apiMocks.getLaunchContext.mockReset();
    apiMocks.completeCurrentLaunchContext.mockReset();
    apiMocks.completeCurrentLaunchContext.mockResolvedValue(null);
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
                    <JobSubmission /><RouteReceipt />
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
    await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(container.textContent).toContain(expectedText); });
};

const click = async (label: string) => {
    const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(label));
    expect(button, label).toBeTruthy();
    await act(async () => button?.click());
};

// Real public lazy shell and MD form. Only API transport, unrelated launchers and
// WebGL owner are doubled; these fixtures assert authority routing, not science.
const RouteReceipt = () => { const location = useLocation(); return <output data-route>{location.pathname}{location.search}</output>; };
const context = (id: string, extra = {}) => ({
    schema: 'bms.launch-context.v1', launch_context_id: id,
    project_id: 'project:md', global_experiment_id: 'global:md',
    domain_experiment_id: 'domain:md', return_uri: '/projects/project%3Amd',
    state: 'issued', ...extra,
});
const settle = async (assertion: () => void) => vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assertion();
});

const inspection = {
    schema_version: 'bms.md.starting-structure-inspection.v1',
    source_ref: { kind: 'managed_fixture', id: '1aki-admitted-v1' },
    identity: {
        label: 'RCSB 1AKI — hen egg-white lysozyme',
        format: 'pdb',
        size_bytes: 116397,
        sha256: hash('b'),
        pdb_id: '1AKI',
        producer_job_id: null,
        design_id: null,
    },
    viewer: {
        url: `/api/molecular-dynamics/starting-structures/managed_fixture/1aki-admitted-v1/content?expected_sha256=${hash('b')}`,
        format: 'pdb',
        sha256: hash('b'),
    },
    inspection: {
        model_count: 1,
        chains: ['A'],
        atom_count: 1079,
        hetero_components: ['HOH'],
        parser: { name: 'biopython', version: '1.85' },
    },
    admission: {
        state: 'admitted',
        profile_id: profile.id,
        code: null,
        message: 'Exact starting-structure bytes are admitted.',
    },
};

const launchPreviewResponse = (body: Record<string, unknown>, digest = hash('d')) => {
    const launchIntent = body.intent as Record<string, unknown>;
    return response({
    schema_version: 'bms.md.launch-preview.v1',
    execution_plan: null,
    execution_target_id: launchIntent.execution_target_id ?? null,
    execution_policy: launchIntent.execution_policy ?? { remote_result_policy: 'manual' },
    source: { ...inspection.identity, source_ref: launchIntent.source_ref, design_id: (launchIntent.source_ref as { kind: string; id: string }).kind === 'design' ? (launchIntent.source_ref as { id: string }).id : null },
    chemistry: {
        profile_id: launchIntent.chemistry_profile_id,
        profile_sha256: launchIntent.chemistry_profile_sha256,
        catalog_digest: launchIntent.catalog_digest,
        admitted: true,
    },
    requested_settings: launchIntent.requested_settings,
    effective_request: {
        engine: 'gromacs', replicas: 1, random_seed: ((body.intent as Record<string, unknown>).requested_settings as Record<string, unknown>).random_seed,
        preparation: { box_type: 'dodecahedron', padding_nm: 1, salt_molar: 0.15, neutralize: true },
        stages: {
            minimization: { enabled: true, steps: 5000, force_tolerance_kj_mol_nm: 1000 },
            nvt: { enabled: true, steps: 50000, temperature_k: 300 },
            npt: { enabled: true, steps: 50000, temperature_k: 300, pressure_bar: 1 },
            production: {
                enabled: true, steps: 500, timestep_fs: 2, temperature_k: 300, pressure_bar: 1,
                checkpoint_interval_minutes: 15, trajectory_interval_steps: 500, energy_interval_steps: 100,
            },
        },
        execution: { ntmpi: 1, ntomp: (launchIntent.requested_settings as Record<string, unknown>).ntomp, gpu_offload: 'full', pin: 'on', placement_authority: 'global_scheduler' },
    },
    warnings: [], blockers: [], preview_digest: digest,
    });
};

describe('integrated public MD destination receiving', () => {
    it('preserves MD destination and edited settings through prediction without claiming the predictor stage', async () => {
        apiMocks.getLaunchContext.mockImplementation(async (id: string) => context(id));
        await renderShell('/submit?template=molecular_dynamics&launch_context_id=md-destination');
        await settle(() => expect(container.querySelector('[aria-label="Project launch destination"]')?.textContent).toContain('project:md'));
        const seed = container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]');
        expect(seed).toBeTruthy();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '31');
            seed?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await click('Predict structure from sequence');
        const sequence = container.querySelector<HTMLTextAreaElement>('textarea');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(sequence, 'ACDEFG');
            sequence?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await click('Open Structure Prediction');
        await settle(() => expect(container.querySelector('[data-mounted-structure-prediction]')).not.toBeNull());
        const predictorRoute = container.querySelector('[data-route]')!.textContent!;
        expect(predictorRoute).toContain('source_sequence_id=' + sequenceId);
        expect(predictorRoute).not.toContain('launch_context_id');
        expect(container.querySelector('[aria-label="Project launch destination"]')).toBeNull();
        const callsAtPredictor = apiMocks.getLaunchContext.mock.calls.length;
        await click('Complete prediction and return');
        await settle(() => {
            expect(container.querySelector('[data-route]')?.textContent).toContain('launch_context_id=md-destination');
            expect(container.querySelector('[aria-label="Project launch destination"]')?.textContent).toContain('project:md');
            expect(container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]')?.value).toBe('31');
        });
        expect(apiMocks.getLaunchContext.mock.calls.slice(callsAtPredictor).every(([id]) => id === 'md-destination')).toBe(true);
        expect(apiMocks.completeCurrentLaunchContext).not.toHaveBeenCalled();
    });

    it('keeps a standalone draft standalone on prediction return', async () => {
        storeMolecularDynamicsDraft(sessionStorage, draftId, { form: { randomSeed: 29 } });
        await renderShell(buildMolecularDynamicsPredictionReturnRoute(draftId, { id: '66666666-6666-4666-8666-666666666666' }));
        await settle(() => expect(container.textContent).toContain('Returned from Structure Prediction'));
        expect(container.querySelector('[data-route]')?.textContent).not.toContain('launch_context_id');
        expect(container.querySelector('[aria-label="Project launch destination"]')).toBeNull();
        expect(apiMocks.getLaunchContext).not.toHaveBeenCalled();
    });

    it('honors an explicit destination over a saved destination when reopening the public form', async () => {
        apiMocks.getLaunchContext.mockImplementation(async (id: string) => context(id));
        storeMolecularDynamicsDraft(sessionStorage, draftId, { destinationLaunchContextId: 'saved-destination', form: { randomSeed: 29 } });
        await renderShell(buildMolecularDynamicsPredictionReturnRoute(draftId, { id: '66666666-6666-4666-8666-666666666666' }) + '&launch_context_id=explicit-destination');
        await settle(() => expect(container.querySelector('[aria-label="Project launch destination"]')?.textContent).toContain('project:md'));
        expect(apiMocks.getLaunchContext.mock.calls.map(([id]) => id)).toEqual(['explicit-destination']);
        expect(container.querySelector('[data-route]')?.textContent).toContain('launch_context_id=explicit-destination');
    });

    it('restores destination before existing recovered-Job completion is invoked exactly once', async () => {
        apiMocks.getLaunchContext.mockImplementation(async (id: string) => context(id, { recovery_job_id: 'recovered-md-job' }));
        storeMolecularDynamicsDraft(sessionStorage, draftId, { destinationLaunchContextId: 'recovery-destination' });
        await renderShell(buildMolecularDynamicsPredictionReturnRoute(draftId, { id: '66666666-6666-4666-8666-666666666666' }));
        await settle(() => expect(apiMocks.completeCurrentLaunchContext).toHaveBeenCalledTimes(1));
        expect(apiMocks.completeCurrentLaunchContext).toHaveBeenCalledWith({ id: 'recovered-md-job' });
        expect(container.querySelector('[data-route]')?.textContent).toContain('launch_context_id=recovery-destination');
        expect(apiMocks.getLaunchContext.mock.calls.map(([id]) => id)).toEqual(['recovery-destination']);
    });
    it('binds restored destination, exact Design and saved settings to public preview and launch', async () => {
        const designId = '77777777-7777-4777-8777-777777777777';
        const predictionId = '66666666-6666-4666-8666-666666666666';
        apiMocks.completeCurrentLaunchContext.mockResolvedValue('/projects/project%3Amd');
        apiMocks.getLaunchContext.mockImplementation(async (id: string) => context(id));
        storeMolecularDynamicsDraft(sessionStorage, draftId, {
            destinationLaunchContextId: 'launch-destination', form: { randomSeed: 37, ntomp: 65 },
            selectedProfileId: profile.id, selectedProfileDigest: profile.profile_sha256,
        });
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url.endsWith('/inspect')) return response({
                ...inspection, source_ref: { kind: 'design', id: designId },
                identity: { ...inspection.identity, label: 'Exact returned Design', design_id: designId, producer_job_id: predictionId },
                viewer: { ...inspection.viewer, url: `/design/${designId}` },
                admission: body.chemistry_profile_id === profile.id
                    ? inspection.admission
                    : { state: 'profile_required', profile_id: null, code: 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Select chemistry for this exact Design.' },
            });
            if (url.endsWith('/launch-preview')) return launchPreviewResponse(body);
            if (url.endsWith('/launch')) return response({ id: 'public-md-job' });
            throw new Error(`unexpected POST ${url}`);
        });
        await renderShell(buildMolecularDynamicsPredictionReturnRoute(draftId, { id: predictionId }) + '&source_design_id=' + designId);
        await settle(() => expect(container.textContent).toContain('Exact returned Design'));
        await click('Use this structure');
        const chemistry = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistry, profile.id);
            chemistry.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await settle(() => expect([...container.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Preview')?.disabled).toBe(false));
        await click('Preview');
        await settle(() => expect(container.textContent).toContain('Effective request digest'));
        await click('Launch');
        await settle(() => expect(apiMocks.completeCurrentLaunchContext).toHaveBeenCalledWith({ id: 'public-md-job' }));
        const previewBody = apiMocks.post.mock.calls.find(([url]) => url.endsWith('/launch-preview'))![1];
        const launchBody = apiMocks.post.mock.calls.find(([url]) => url.endsWith('/launch'))![1];
        expect(previewBody.intent).toMatchObject({
            source_ref: { kind: 'design', id: designId }, expected_source_sha256: hash('b'),
            launch_context_id: 'launch-destination', execution_target_id: null,
            requested_settings: { random_seed: 37, ntomp: 65 },
        });
        expect(launchBody.intent).toEqual(previewBody.intent);
        expect(launchBody.preview_digest).toBe(hash('d'));
        expect(container.querySelector('[data-route]')?.textContent).toBe('/projects/project%3Amd');
    });

});
