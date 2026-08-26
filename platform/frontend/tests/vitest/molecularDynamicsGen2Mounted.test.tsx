import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.hoisted(() => vi.fn());
const viewerLoadCallbacks = vi.hoisted(() => new Map<string, (state: 'loading' | 'loaded' | 'failed', errorMessage?: string) => void>());
const apiMocks = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    completeCurrentLaunchContext: vi.fn(async () => '/designs/md-job-1'),
}));

vi.mock('react-router-dom', async (importOriginal) => ({
    ...(await importOriginal<typeof import('react-router-dom')>()),
    useNavigate: () => navigate,
}));
vi.mock('../../src/lib/api', () => ({
    api: { get: apiMocks.get, post: apiMocks.post },
    completeCurrentLaunchContext: apiMocks.completeCurrentLaunchContext,
}));
vi.mock('../../src/components/MolstarViewer', () => {
    const MockMolstarViewer = ({ structureUrl, onLoadStateChange }: {
        structureUrl?: string;
        onLoadStateChange?: (state: 'loading' | 'loaded' | 'failed', errorMessage?: string) => void;
    }) => {
        React.useEffect(() => {
            if (!structureUrl || !onLoadStateChange) return;
            viewerLoadCallbacks.set(structureUrl, onLoadStateChange);
            onLoadStateChange('loading');
            return () => { viewerLoadCallbacks.delete(structureUrl); };
        }, [onLoadStateChange, structureUrl]);
        return <div data-md-starting-structure-viewer={structureUrl} />;
    };
    return { default: MockMolstarViewer };
});

import { MolecularDynamicsTemplate } from '../../src/components/MolecularDynamicsTemplate';

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
        input_mode: 'structure',
        structure_sha256: hash('b'),
        replicas: 1,
        engine: 'gromacs',
        force_field: 'ff19SB',
        water_model: 'OPC',
        timestep_fs: 2,
        temperature_k: 300,
        pressure_bar: 1,
        salt_molar: 0.15,
        padding_nm: 1,
        max_production_steps: 5000,
        max_minimization_steps: 50000,
        max_nvt_steps: 50000,
        max_npt_steps: 50000,
    },
    scientific_validation: {
        validated: true,
        lane: 'short-gpu',
        version: '1.0.0',
        scope: { launch_scope: 'short_gpu', system_classes: ['protein'] },
    },
    states: {
        installed: true,
        runtime_validated: true,
        scientifically_validated: true,
        operator_enabled: true,
        asset_probe_success: true,
        selectable: true,
    },
    explicit_exclusions: [],
    runtime_identity: { runtime_id: 'md-preparation-v1', runtime_version: '1', sif_sha256: hash('f') },
};
const profileB = {
    ...profile,
    id: 'amber_ff14sb_tip3p_protein_v1',
    profile_sha256: hash('e'),
    display_name: 'AMBER ff14SB + TIP3P protein',
};
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

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;

const response = <T,>(data: T) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
};
const launchPreviewResponse = (body: Record<string, unknown>, digest = hash('d')) => {
    const launchIntent = body.intent as Record<string, unknown>;
    return response({
    schema_version: 'bms.md.launch-preview.v1',
    source: { source_ref: inspection.source_ref, ...inspection.identity },
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
        execution: { ntmpi: 1, ntomp: 8, gpu_offload: 'full', pin: 'on', placement_authority: 'global_scheduler' },
    },
    warnings: [], blockers: [], preview_digest: digest,
    });
};

beforeEach(() => {
    navigate.mockReset();
    apiMocks.get.mockReset();
    apiMocks.post.mockReset();
    apiMocks.completeCurrentLaunchContext.mockClear();
    viewerLoadCallbacks.clear();
    apiMocks.get.mockImplementation(async (url: string) => {
        if (url === '/api/molecular-dynamics/chemistry-profiles') {
            return response({
                schema: 'bms.md.chemistry-profile-inventory.v1',
                catalog_digest: hash('c'),
                profiles: [profile],
                selectable_profile_ids: [profile.id],
                count: 1,
                bounded: true,
            });
        }
        if (url === '/api/user-sequences') return response([]);
        throw new Error(`unexpected GET ${url}`);
    });
    apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
        if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(inspection);
        if (url === '/api/molecular-dynamics/launch-preview') {
            return response({
                schema_version: 'bms.md.launch-preview.v1',
                source: { source_ref: inspection.source_ref, ...inspection.identity },
                chemistry: {
                    profile_id: profile.id,
                    profile_sha256: profile.profile_sha256,
                    catalog_digest: hash('c'),
                    admitted: true,
                },
                requested_settings: (body.intent as Record<string, unknown>).requested_settings,
                effective_request: {
                    engine: 'gromacs', replicas: 1, random_seed: 20260717,
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
                    execution: { ntmpi: 1, ntomp: 8, gpu_offload: 'full', pin: 'on', placement_authority: 'global_scheduler' },
                },
                warnings: [], blockers: [], preview_digest: hash('d'),
            });
        }
        if (url === '/api/molecular-dynamics/launch') return response({ id: 'md-job-1' });
        throw new Error(`unexpected POST ${url}`);
    });
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

const click = async (label: string) => {
    const button = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes(label));
    expect(button, label).toBeTruthy();
    await act(async () => (button as HTMLButtonElement).click());
};

const mountAdmittedFixture = async () => {
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter><MolecularDynamicsTemplate onBack={() => undefined} /></MemoryRouter>
            </QueryClientProvider>,
        );
    });
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });
    await click('Accepted samples');
    await click('Use verified 1AKI fixture');
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('1,079 atoms')); });
    await act(async () => viewerLoadCallbacks.get(inspection.viewer.url)?.('loaded'));
    await click('Use this structure');
    const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profile.id);
        chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
        await vi.waitFor(() => expect(
            [...container.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes('Preview effective request'))?.disabled,
        ).toBe(false));
    });
};

const mountPredictionCandidateRace = async () => {
    const jobId = '66666666-6666-4666-8666-666666666666';
    const candidateAId = '77777777-7777-4777-8777-777777777771';
    const candidateBId = '77777777-7777-4777-8777-777777777772';
    const candidateAInspection = {
        ...inspection,
        source_ref: { kind: 'design', id: candidateAId },
        identity: { ...inspection.identity, label: 'Candidate A exact bytes', sha256: hash('d'), producer_job_id: jobId, design_id: candidateAId },
        viewer: { ...inspection.viewer, url: `/api/molecular-dynamics/starting-structures/design/${candidateAId}/content?expected_sha256=${hash('d')}`, sha256: hash('d') },
        admission: { state: 'profile_required', profile_id: null, code: 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Select chemistry after Candidate A review.' },
    };
    const candidateBInspection = {
        ...inspection,
        source_ref: { kind: 'design', id: candidateBId },
        identity: { ...inspection.identity, label: 'Candidate B exact bytes', sha256: hash('e'), producer_job_id: jobId, design_id: candidateBId },
        viewer: { ...inspection.viewer, url: `/api/molecular-dynamics/starting-structures/design/${candidateBId}/content?expected_sha256=${hash('e')}`, sha256: hash('e') },
        admission: { state: 'profile_required', profile_id: null, code: 'MD_CHEMISTRY_PROFILE_REQUIRED', message: 'Select chemistry after Candidate B review.' },
    };
    const admittedCandidateBInspection = {
        ...candidateBInspection,
        admission: { state: 'admitted', profile_id: profile.id, code: null, message: 'Candidate B exact bytes are admitted.' },
    };
    const candidateARequest = deferred<ReturnType<typeof response>>();
    const candidateBRequest = deferred<ReturnType<typeof response>>();
    const candidateBProfileRequest = deferred<ReturnType<typeof response>>();

    apiMocks.get.mockImplementation(async (url: string) => {
        if (url === '/api/molecular-dynamics/chemistry-profiles') {
            return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
        }
        if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
            return response({
                schema_version: 'bms.md.prediction-source-candidates.v1',
                job: { id: jobId, name: 'Two prediction candidates', status: 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null },
                candidates: [
                    { source_ref: { kind: 'design', id: candidateAId }, name: 'candidate_A', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 80, ptm: 0.7, iptm: null, confidence: 0.8 }, created_at: null },
                    { source_ref: { kind: 'design', id: candidateBId }, name: 'candidate_B', format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 90, ptm: 0.8, iptm: null, confidence: 0.9 }, created_at: null },
                ],
                next_cursor: null,
            });
        }
        throw new Error(`unexpected GET ${url}`);
    });
    apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
        if (url !== '/api/molecular-dynamics/starting-structures/inspect') throw new Error(`unexpected POST ${url}`);
        const sourceRef = body.source_ref as { id: string };
        if (sourceRef.id === candidateAId) return candidateARequest.promise;
        if (sourceRef.id === candidateBId && body.chemistry_profile_id === profile.id) return candidateBProfileRequest.promise;
        if (sourceRef.id === candidateBId) return candidateBRequest.promise;
        throw new Error(`unexpected candidate ${sourceRef.id}`);
    });

    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter>
                    <MolecularDynamicsTemplate onBack={() => undefined} initialValues={{ source_prediction_job_id: jobId }} />
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('candidate_B')); });

    return {
        candidateAId,
        candidateBId,
        candidateAInspection,
        candidateBInspection,
        admittedCandidateBInspection,
        candidateARequest,
        candidateBRequest,
        candidateBProfileRequest,
    };
};

describe('mounted Molecular Dynamics Gen 2 launcher', () => {
    it('asks for a first chemistry selection without falsely calling the profile stale', async () => {
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter><MolecularDynamicsTemplate onBack={() => undefined} /></MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });

        expect(container.textContent).toContain('Select a chemistry profile after promoting a starting structure.');
        expect(container.textContent).not.toContain('The selected chemistry profile is stale or no longer present in the deployed catalog.');
    });

    it('still rejects a previously selected profile that is absent from the deployed catalog', async () => {
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate
                            onBack={() => undefined}
                            initialValues={{
                                intent: {
                                    chemistry_profile_id: 'retired_profile_v1',
                                    chemistry_profile_sha256: hash('e'),
                                },
                            }}
                        />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });

        expect(container.textContent).toContain('The selected chemistry profile is stale or no longer present in the deployed catalog.');
    });

    it('still rejects a changed digest for a profile that remains in the deployed catalog', async () => {
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate
                            onBack={() => undefined}
                            initialValues={{
                                intent: {
                                    chemistry_profile_id: profile.id,
                                    chemistry_profile_sha256: hash('e'),
                                },
                            }}
                        />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });

        expect(container.textContent).toContain('The saved chemistry profile digest is stale; explicitly reselect the profile before launch.');
    });

    it('keeps a recursively invalid chemistry inventory out of the launcher', async () => {
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({
                    schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'),
                    profiles: [{ ...profile, states: { ...profile.states, runtime_path: '/private/runtime.sif' } }],
                    selectable_profile_ids: [profile.id], count: 1, bounded: true,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter><MolecularDynamicsTemplate onBack={() => undefined} /></MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('unavailable; launch is blocked')); });
        expect(container.textContent).not.toContain(profile.display_name);
    });

    it('inspects and promotes exact coordinates before chemistry selection and profile admission', async () => {
        const profileRequiredInspection = {
            ...inspection,
            admission: {
                state: 'profile_required',
                profile_id: null,
                code: 'MD_CHEMISTRY_PROFILE_REQUIRED',
                message: 'Select a chemistry profile after reviewing these coordinates.',
            },
        };
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                return response(body.chemistry_profile_id === null ? profileRequiredInspection : inspection);
            }
            throw new Error(`unexpected POST ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter><MolecularDynamicsTemplate onBack={() => undefined} /></MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });
        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        expect(chemistrySelect?.value).toBe('');
        expect(chemistrySelect?.disabled).toBe(true);
        await click('Accepted samples');
        const fixtureButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent?.includes('Use verified 1AKI fixture'));
        expect(fixtureButton?.disabled).toBe(false);
        await act(async () => fixtureButton?.click());
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('1,079 atoms')); });
        expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: inspection.source_ref, chemistry_profile_id: null },
        );
        expect(container.textContent).toContain('Select a chemistry profile after reviewing these coordinates.');
        const promoteButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent === 'Use this structure');
        await act(async () => viewerLoadCallbacks.get(profileRequiredInspection.viewer.url)?.('loaded'));
        expect(promoteButton?.disabled).toBe(false);
        await act(async () => promoteButton?.click());
        expect(chemistrySelect?.disabled).toBe(false);
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profile.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: inspection.source_ref, chemistry_profile_id: profile.id },
        )); });
        expect(container.textContent).toContain('Exact starting-structure bytes are admitted');
    });

    it('persists a new sequence before navigating through the UUID-only Structure Prediction handoff', async () => {
        const draftId = '44444444-4444-4444-8444-444444444444';
        const sequenceId = '55555555-5555-4555-8555-555555555555';
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(draftId);
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/user-sequences') {
                expect(body).toEqual({ name: 'Candidate A', sequence: 'ACDEFG' });
                return response({
                    id: sequenceId,
                    name: 'Candidate A',
                    sequence: 'ACDEFG',
                    description: null,
                    length: 6,
                    organism: null,
                    uniprot_id: null,
                    ncbi_id: null,
                    is_preset: false,
                    created_at: '2026-08-26T00:00:00Z',
                    updated_at: null,
                });
            }
            throw new Error(`unexpected POST ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate onBack={() => undefined} />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });
        expect([...container.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent)).toEqual([
            'RCSB',
            'Your Runs',
            'Upload',
            'Accepted samples',
        ]);
        expect(container.textContent).toContain('Starting structure');
        expect(container.textContent).toContain('Chemistry');
        expect(container.textContent).toContain('Protocol and output');
        expect(container.textContent).toContain('Review and launch');
        expect(container.textContent).toContain('Predict structure from sequence');
        expect(container.querySelector('[data-bms-md-launcher="gen2"]')?.className).not.toContain('max-w-7xl');
        await click('Your Runs');
        const name = [...container.querySelectorAll<HTMLInputElement>('input')].find((item) => item.value === 'MD candidate');
        const sequence = container.querySelector<HTMLTextAreaElement>('textarea');
        expect(name).toBeTruthy();
        expect(sequence).toBeTruthy();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(name, 'Candidate A');
            name?.dispatchEvent(new Event('input', { bubbles: true }));
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(sequence, 'ACDEFG');
            sequence?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await click('Open Structure Prediction');
        await act(async () => {
            await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(
                `/submit?template=structure_prediction&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}`,
            ));
        });
        const stored = JSON.parse(sessionStorage.getItem(`bms.md.gen2.draft.v1:${draftId}`) ?? 'null');
        expect(stored).toMatchObject({ form: { jobName: 'molecular_dynamics' } });
        expect(JSON.stringify(stored)).not.toContain('ACDEFG');
    });

    it('uses an existing durable UserSequence ID without creating a duplicate', async () => {
        const draftId = '44444444-4444-4444-8444-444444444444';
        const sequenceId = '55555555-5555-4555-8555-555555555555';
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(draftId);
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({
                    schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile],
                    selectable_profile_ids: [profile.id], count: 1, bounded: true,
                });
            }
            if (url === '/api/user-sequences') {
                return response([{
                    id: sequenceId, name: 'Saved candidate', sequence: 'ACDEFG', description: null, length: 6,
                    organism: null, uniprot_id: null, ncbi_id: null, is_preset: false,
                    created_at: '2026-08-26T00:00:00Z', updated_at: null,
                }]);
            }
            throw new Error(`unexpected GET ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter><MolecularDynamicsTemplate onBack={() => undefined} /></MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });
        await click('Your Runs');
        await click('Use saved sequence');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Saved candidate')); });
        const savedSelect = container.querySelector<HTMLSelectElement>('[data-md-saved-sequence]');
        expect(savedSelect).not.toBeNull();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(savedSelect, sequenceId);
            savedSelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await click('Open Structure Prediction');
        await act(async () => {
            await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(
                `/submit?template=structure_prediction&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}`,
            ));
        });
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/user-sequences')).toBe(false);
    });

    it('reconstructs the returned Structure Prediction Job and requires explicit Design promotion', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designId = '77777777-7777-4777-8777-777777777777';
        const designInspection = {
            ...inspection,
            source_ref: { kind: 'design', id: designId },
            identity: { ...inspection.identity, label: 'model_01 exact bytes', producer_job_id: jobId, design_id: designId },
            viewer: { ...inspection.viewer, url: `/api/molecular-dynamics/starting-structures/design/${designId}/content?expected_sha256=${inspection.identity.sha256}` },
        };
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                expect(body).toMatchObject({ source_ref: { kind: 'design', id: designId } });
                return response(designInspection);
            }
            throw new Error(`unexpected POST ${url}`);
        });
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1',
                    job: {
                        id: jobId,
                        name: 'Candidate A structure prediction',
                        status: 'completed',
                        model_id: 'boltz2',
                        mode: 'predict',
                        created_at: '2026-08-26T00:00:00Z',
                        started_at: '2026-08-26T00:00:01Z',
                        completed_at: '2026-08-26T00:00:02Z',
                        failure: null,
                    },
                    candidates: [{
                        source_ref: { kind: 'design', id: designId },
                        name: 'model_01', format: 'pdb', eligible: true, blocker_code: null,
                        metrics: { plddt: 91, ptm: 0.8, iptm: null, confidence: 0.9 },
                        created_at: '2026-08-26T00:00:00Z',
                    }],
                    next_cursor: null,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate onBack={() => undefined} initialValues={{ source_prediction_job_id: jobId }} />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('model_01')); });
        expect(container.textContent).toContain('Returned from Structure Prediction');
        expect(container.textContent).toContain('Candidate A structure prediction');
        expect(container.textContent).toContain('Run another prediction');
        const yourRuns = [...container.querySelectorAll('[role="tab"]')].find((tab) => tab.textContent === 'Your Runs');
        expect(yourRuns?.getAttribute('aria-selected')).toBe('true');
        expect(container.textContent).not.toContain('Use selected Design for MD');
        expect(apiMocks.get.mock.calls.some(([url]) => String(url).includes('/api/jobs'))).toBe(false);
        expect(apiMocks.get.mock.calls.some(([url]) => String(url).includes('/api/designs'))).toBe(false);
        const candidateCard = [...container.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')]
            .find((button) => button.textContent?.includes('model_01'));
        expect(candidateCard).toBeTruthy();
        await act(async () => candidateCard?.click());
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: designId }, chemistry_profile_id: null },
        )); });
        expect(container.textContent).toContain('model_01 exact bytes');
        const promote = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent === 'Use selected Design for MD');
        expect(promote?.disabled).toBe(true);
        await act(async () => viewerLoadCallbacks.get(designInspection.viewer.url)?.('loaded'));
        expect(promote?.disabled).toBe(false);
        await act(async () => promote?.click());
        expect(container.textContent).toContain('Starting structure promoted to chemistry');
    });

    it('keeps Candidate B inspected and promotable when Candidate A succeeds late', async () => {
        const race = await mountPredictionCandidateRace();
        await click('candidate_A');
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: race.candidateAId }, chemistry_profile_id: null },
        )); });
        await click('candidate_B');
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: race.candidateBId }, chemistry_profile_id: null },
        )); });

        await act(async () => race.candidateBRequest.resolve(response(race.candidateBInspection)));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Candidate B exact bytes')); });
        await act(async () => viewerLoadCallbacks.get(race.candidateBInspection.viewer.url)?.('loaded'));
        const promote = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent === 'Use selected Design for MD');
        expect(promote?.disabled).toBe(false);
        await act(async () => promote?.click());

        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profile.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: { kind: 'design', id: race.candidateBId }, chemistry_profile_id: profile.id },
        )); });

        await act(async () => race.candidateARequest.resolve(response(race.candidateAInspection)));
        expect(container.textContent).toContain('Inspecting immutable starting-structure bytes…');
        expect(container.textContent).toContain('Candidate B exact bytes');
        expect(container.textContent).not.toContain('Candidate A exact bytes');

        await act(async () => race.candidateBProfileRequest.resolve(response(race.admittedCandidateBInspection)));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Candidate B exact bytes are admitted.')); });
        const selectedB = [...container.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')]
            .find((button) => button.textContent?.includes('candidate_B'));
        expect(selectedB?.getAttribute('aria-pressed')).toBe('true');
        expect(promote?.disabled).toBe(false);
        expect(container.querySelector('[role="alert"]')).toBeNull();
    });

    it('keeps Candidate B selected and inspected without publishing Candidate A late error', async () => {
        const race = await mountPredictionCandidateRace();
        await click('candidate_A');
        await click('candidate_B');
        await act(async () => race.candidateBRequest.resolve(response(race.candidateBInspection)));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Candidate B exact bytes')); });
        await act(async () => viewerLoadCallbacks.get(race.candidateBInspection.viewer.url)?.('loaded'));

        await act(async () => race.candidateARequest.reject(new Error('Candidate A late inspection failed.')));
        const selectedB = [...container.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')]
            .find((button) => button.textContent?.includes('candidate_B'));
        const promote = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent === 'Use selected Design for MD');
        expect(selectedB?.getAttribute('aria-pressed')).toBe('true');
        expect(container.textContent).toContain('Candidate B exact bytes');
        expect(container.textContent).not.toContain('Candidate A late inspection failed.');
        expect(promote?.disabled).toBe(false);
        expect(container.querySelector('[role="alert"]')).toBeNull();
    });

    it('keeps a recursively invalid prediction projection out of the MD source selector', async () => {
        const jobId = '66666666-6666-4666-8666-666666666666';
        const designId = '77777777-7777-4777-8777-777777777777';
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true });
            }
            if (url === `/api/molecular-dynamics/prediction-jobs/${jobId}/source-candidates`) {
                return response({
                    schema_version: 'bms.md.prediction-source-candidates.v1',
                    job: {
                        id: jobId, name: 'Invalid private projection', status: 'completed', model_id: 'boltz2', mode: 'predict',
                        created_at: null, started_at: null, completed_at: null, failure: null,
                    },
                    candidates: [{
                        source_ref: { kind: 'design', id: designId }, name: 'must-not-render', format: 'pdb', eligible: true,
                        blocker_code: null, metrics: { plddt: 91, ptm: null, iptm: null, confidence: null, pdb_path: '/private/model.pdb' }, created_at: null,
                    }],
                    next_cursor: null,
                });
            }
            throw new Error(`unexpected GET ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate onBack={() => undefined} initialValues={{ source_prediction_job_id: jobId }} />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull()); });
        expect(container.textContent).not.toContain('must-not-render');
        expect(container.textContent).toContain('Invalid prediction source candidates response');
    });

    it('blocks old profile admission while same-source profile B reinspection is pending and after it fails', async () => {
        const profileBInspection = deferred<ReturnType<typeof response>>();
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile, profileB], selectable_profile_ids: [profile.id, profileB.id], count: 2, bounded: true });
            }
            if (url === '/api/user-sequences') return response([]);
            throw new Error(`unexpected GET ${url}`);
        });
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url !== '/api/molecular-dynamics/starting-structures/inspect') throw new Error(`unexpected POST ${url}`);
            if (body.chemistry_profile_id === profileB.id) return profileBInspection.promise;
            return response(inspection);
        });
        await mountAdmittedFixture();
        const previewButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Preview effective request'));
        expect(previewButton?.disabled).toBe(false);

        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profileB.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: inspection.source_ref, chemistry_profile_id: profileB.id },
        )); });
        expect(previewButton?.disabled).toBe(true);
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();
        expect(container.textContent).toContain(inspection.identity.label);

        await act(async () => profileBInspection.reject(new Error('Profile B exact-byte admission failed.')));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Profile B exact-byte admission failed.')); });
        expect(previewButton?.disabled).toBe(true);
        expect(chemistrySelect?.value).toBe(profileB.id);
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();
    });

    it('keeps the current-SHA scene and promotion while profile B reinspection becomes authoritative', async () => {
        const profileBInspection = deferred<ReturnType<typeof response>>();
        const admittedProfileB = {
            ...inspection,
            admission: { ...inspection.admission, profile_id: profileB.id, message: 'Profile B exact bytes are admitted.' },
        };
        apiMocks.get.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') {
                return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile, profileB], selectable_profile_ids: [profile.id, profileB.id], count: 2, bounded: true });
            }
            if (url === '/api/user-sequences') return response([]);
            throw new Error(`unexpected GET ${url}`);
        });
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url !== '/api/molecular-dynamics/starting-structures/inspect') throw new Error(`unexpected POST ${url}`);
            if (body.chemistry_profile_id === profileB.id) return profileBInspection.promise;
            return response(inspection);
        });
        await mountAdmittedFixture();
        const previewButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Preview effective request'));
        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profileB.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(previewButton?.disabled).toBe(true);
        expect(container.textContent).toContain('Starting structure promoted to chemistry.');
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();

        await act(async () => profileBInspection.resolve(response(admittedProfileB)));
        await act(async () => { await vi.waitFor(() => expect(previewButton?.disabled).toBe(false)); });
        expect(container.textContent).toContain('Profile B exact bytes are admitted.');
        expect(container.textContent).toContain('Starting structure promoted to chemistry.');
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();
    });

    it('requires successful same-ID new-digest admission through pending failure and retry success', async () => {
        const replacedProfile = {
            ...profile,
            version: '1.0.1',
            profile_sha256: hash('e'),
            display_name: 'AMBER ff19SB + OPC protein replacement',
        };
        const firstDigestBInspection = deferred<ReturnType<typeof response>>();
        const secondDigestBInspection = deferred<ReturnType<typeof response>>();
        let profiledInspectionCount = 0;
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url !== '/api/molecular-dynamics/starting-structures/inspect') throw new Error(`unexpected POST ${url}`);
            if (body.chemistry_profile_id !== profile.id) return response(inspection);
            profiledInspectionCount += 1;
            if (profiledInspectionCount === 1) return response(inspection);
            return profiledInspectionCount === 2 ? firstDigestBInspection.promise : secondDigestBInspection.promise;
        });
        await mountAdmittedFixture();
        const previewButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Preview effective request'));
        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        expect(previewButton?.disabled).toBe(false);

        await act(async () => {
            client.setQueryData(['molecular-dynamics', 'chemistry-profiles'], {
                schema: 'bms.md.chemistry-profile-inventory.v1',
                catalog_digest: hash('c'),
                profiles: [replacedProfile],
                selectable_profile_ids: [replacedProfile.id],
                count: 1,
                bounded: true,
            });
        });
        await act(async () => { await vi.waitFor(() => expect(chemistrySelect?.value).toBe('')); });
        expect(previewButton?.disabled).toBe(true);

        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, replacedProfile.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(profiledInspectionCount).toBe(2)); });
        expect(previewButton?.disabled).toBe(true);
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();
        expect(container.textContent).toContain('Starting structure promoted to chemistry.');

        await act(async () => firstDigestBInspection.reject(new Error('Replacement digest admission failed.')));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Replacement digest admission failed.')); });
        expect(previewButton?.disabled).toBe(true);
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();

        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, replacedProfile.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(profiledInspectionCount).toBe(3)); });
        expect(previewButton?.disabled).toBe(true);
        await act(async () => secondDigestBInspection.resolve(response({
            ...inspection,
            admission: { ...inspection.admission, message: 'Replacement digest exact bytes are admitted.' },
        })));
        await act(async () => { await vi.waitFor(() => expect(previewButton?.disabled).toBe(false)); });
        expect(container.textContent).toContain('Replacement digest exact bytes are admitted.');
    });

     it('does not publish an in-flight preview after the request intent changes and accepts the next owned preview', async () => {
        const previewA = deferred<ReturnType<typeof response>>();
        const previewB = deferred<ReturnType<typeof response>>();
        const previewBodies: Record<string, unknown>[] = [];
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(inspection);
            if (url === '/api/molecular-dynamics/launch-preview') {
                previewBodies.push(body);
                return previewBodies.length === 1 ? previewA.promise : previewB.promise;
            }
            throw new Error(`unexpected POST ${url}`);
        });
        await mountAdmittedFixture();

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(1)); });
        const seed = container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '19');
            seed?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const previewButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Preview effective request') || item.textContent?.includes('Compiling preview'));
        const launchButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Launch typed MD job'));
        expect(previewButton?.disabled).toBe(false);
        expect(launchButton?.disabled).toBe(true);

        await act(async () => previewA.resolve(launchPreviewResponse(previewBodies[0], hash('d'))));
        expect(container.textContent).not.toContain(hash('d'));
        expect(launchButton?.disabled).toBe(true);

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(2)); });
        await act(async () => previewB.resolve(launchPreviewResponse(previewBodies[1], hash('e'))));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(hash('e'))); });
        expect(launchButton?.disabled).toBe(false);
    });

    it('does not publish an error or stale finally state from an invalidated preview request', async () => {
        const previewRequest = deferred<ReturnType<typeof response>>();
        apiMocks.post.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(inspection);
            if (url === '/api/molecular-dynamics/launch-preview') return previewRequest.promise;
            throw new Error(`unexpected POST ${url}`);
        });
        await mountAdmittedFixture();
        await click('Preview effective request');
        const seed = container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '31');
            seed?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => previewRequest.reject(new Error('stale preview A failed')));

        expect(container.textContent).not.toContain('stale preview A failed');
        expect(container.querySelector('[role="alert"]')).toBeNull();
        expect([...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Preview effective request'))?.disabled).toBe(false);
        expect([...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Launch typed MD job'))?.disabled).toBe(true);
    });

    it('invalidates a valid preview when only the hydrated catalog digest changes and accepts a fresh preview', async () => {
        const previewBodies: Record<string, unknown>[] = [];
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(inspection);
            if (url === '/api/molecular-dynamics/launch-preview') {
                previewBodies.push(body);
                return launchPreviewResponse(body, previewBodies.length === 1 ? hash('d') : hash('e'));
            }
            if (url === '/api/molecular-dynamics/launch') return response({ id: 'must-not-launch-stale' });
            throw new Error(`unexpected POST ${url}`);
        });
        await mountAdmittedFixture();
        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(hash('d'))); });
        const launchButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Launch typed MD job'));
        expect(launchButton?.disabled).toBe(false);

        await act(async () => {
            client.setQueryData(['molecular-dynamics', 'chemistry-profiles'], {
                schema: 'bms.md.chemistry-profile-inventory.v1',
                catalog_digest: hash('1'),
                profiles: [profile],
                selectable_profile_ids: [profile.id],
                count: 1,
                bounded: true,
            });
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).not.toContain(hash('d'))); });
        expect(launchButton?.disabled).toBe(true);
        await act(async () => launchButton?.click());
        expect(apiMocks.post.mock.calls.some(([url]) => url === '/api/molecular-dynamics/launch')).toBe(false);

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(hash('e'))); });
        expect(previewBodies[1]).toMatchObject({ intent: { catalog_digest: hash('1') } });
        expect(launchButton?.disabled).toBe(false);
    });

    it('rejects stale preview success after a same-ID profile digest refetch and accepts digest B only after reinspection', async () => {
        const replacement = { ...profile, version: '1.0.1', profile_sha256: hash('e') };
        const previewA = deferred<ReturnType<typeof response>>();
        const previewB = deferred<ReturnType<typeof response>>();
        const previewBodies: Record<string, unknown>[] = [];
        let profiledInspectionCount = 0;
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') {
                if (body.chemistry_profile_id === profile.id) profiledInspectionCount += 1;
                return response(inspection);
            }
            if (url === '/api/molecular-dynamics/launch-preview') {
                previewBodies.push(body);
                return previewBodies.length === 1 ? previewA.promise : previewB.promise;
            }
            throw new Error(`unexpected POST ${url}`);
        });
        await mountAdmittedFixture();
        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(1)); });

        await act(async () => {
            client.setQueryData(['molecular-dynamics', 'chemistry-profiles'], {
                schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [replacement],
                selectable_profile_ids: [replacement.id], count: 1, bounded: true,
            });
        });
        const launchButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Launch typed MD job'));
        await act(async () => previewA.resolve(launchPreviewResponse(previewBodies[0], hash('d'))));
        expect(container.textContent).not.toContain(hash('d'));
        expect(launchButton?.disabled).toBe(true);

        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, replacement.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(profiledInspectionCount).toBe(2)); });
        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(2)); });
        await act(async () => previewB.resolve(launchPreviewResponse(previewBodies[1], hash('f'))));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(hash('f'))); });
        expect(previewBodies[1]).toMatchObject({ intent: { chemistry_profile_id: profile.id, chemistry_profile_sha256: hash('e') } });
        expect(launchButton?.disabled).toBe(false);
    });

    it('suppresses a stale preview error after catalog refetch and keeps launch blocked until a fresh preview succeeds', async () => {
        const previewA = deferred<ReturnType<typeof response>>();
        const previewB = deferred<ReturnType<typeof response>>();
        const previewBodies: Record<string, unknown>[] = [];
        apiMocks.post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(inspection);
            if (url === '/api/molecular-dynamics/launch-preview') {
                previewBodies.push(body);
                return previewBodies.length === 1 ? previewA.promise : previewB.promise;
            }
            throw new Error(`unexpected POST ${url}`);
        });
        await mountAdmittedFixture();
        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(1)); });
        await act(async () => {
            client.setQueryData(['molecular-dynamics', 'chemistry-profiles'], {
                schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('1'), profiles: [profile],
                selectable_profile_ids: [profile.id], count: 1, bounded: true,
            });
        });
        await act(async () => previewA.reject(new Error('stale catalog preview failed')));
        expect(container.textContent).not.toContain('stale catalog preview failed');
        const launchButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((item) => item.textContent?.includes('Launch typed MD job'));
        expect(launchButton?.disabled).toBe(true);

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(previewBodies).toHaveLength(2)); });
        await act(async () => previewB.resolve(launchPreviewResponse(previewBodies[1], hash('e'))));
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(hash('e'))); });
        expect(launchButton?.disabled).toBe(false);
    });

     it('inspects an opaque quick-start source, previews the closed request, invalidates it on change, and launches the exact digest', async () => {
        const openPrediction = vi.fn();
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate
                            onBack={() => undefined}
                            launchContextId="launch-context-1"
                            onOpenStructurePrediction={openPrediction}
                        />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain(profile.display_name)); });

        expect(container.textContent).toContain('Choose a starting structure');
        expect(container.textContent).toContain('RCSB');
        expect(container.textContent).toContain('Your Runs');
        expect(container.textContent).toContain('Accepted samples');
        expect(container.textContent).not.toContain('Or server path');

        await click('Accepted samples');
        await click('Use verified 1AKI fixture');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('1,079 atoms')); });
        expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: inspection.source_ref, chemistry_profile_id: null },
        );
        expect(container.querySelector('[data-md-starting-structure-viewer]')).not.toBeNull();
        expect(container.textContent).toContain('Exact starting-structure bytes are admitted');

        const previewButton = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes('Preview effective request')) as HTMLButtonElement;
        const promoteButton = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes('Use this structure')) as HTMLButtonElement;
        expect(promoteButton).toBeTruthy();
        expect(promoteButton.disabled).toBe(true);
        expect(previewButton.disabled).toBe(true);
        expect(container.textContent).toContain('Mol* is loading the inspected structure');

        await act(async () => viewerLoadCallbacks.get(inspection.viewer.url)?.('loaded'));
        expect(promoteButton.disabled).toBe(false);
        expect(previewButton.disabled).toBe(true);
        await click('Use this structure');
        expect(previewButton.disabled).toBe(true);
        const chemistrySelect = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(chemistrySelect, profile.id);
            chemistrySelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { await vi.waitFor(() => expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: inspection.source_ref, chemistry_profile_id: profile.id },
        )); });
        expect(previewButton.disabled).toBe(false);

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Effective request digest')); });
        const previewCall = apiMocks.post.mock.calls.find(([url]) => url === '/api/molecular-dynamics/launch-preview');
        expect(previewCall?.[1]).toMatchObject({
            schema_version: 'bms.md.launch-preview-request.v1',
            intent: {
                schema_version: 'bms.md.launch-intent.v1',
                source_ref: inspection.source_ref,
                expected_source_sha256: inspection.identity.sha256,
                chemistry_profile_id: profile.id,
                chemistry_profile_sha256: profile.profile_sha256,
                catalog_digest: hash('c'),
                launch_context_id: 'launch-context-1',
                requested_settings: { neutralize: true, replicas: 1, timestep_fs: 2 },
            },
        });

        const seed = container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]');
        expect(seed).not.toBeNull();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '19');
            seed?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        expect(container.textContent).not.toContain('Effective request digest');

        await click('Preview effective request');
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Effective request digest')); });
        await click('Launch typed MD job');
        await act(async () => { await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/designs/md-job-1')); });
        const launchCall = apiMocks.post.mock.calls.find(([url]) => url === '/api/molecular-dynamics/launch');
        expect(launchCall?.[1]).toMatchObject({
            schema_version: 'bms.md.launch-request.v1',
            preview_digest: hash('d'),
            intent: { requested_settings: { random_seed: 19 } },
        });
    });

    it('reopens a Project-owned Design source in the matching source lane and re-inspects exact bytes', async () => {
        const designRef = { kind: 'design', id: '22222222-2222-4222-8222-222222222222' };
        const designInspection = {
            ...inspection,
            source_ref: designRef,
            identity: {
                ...inspection.identity,
                label: 'Project Design candidate',
                producer_job_id: '33333333-3333-4333-8333-333333333333',
                design_id: designRef.id,
            },
        };
        apiMocks.post.mockImplementation(async (url: string) => {
            if (url === '/api/molecular-dynamics/starting-structures/inspect') return response(designInspection);
            throw new Error(`unexpected POST ${url}`);
        });
        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter>
                        <MolecularDynamicsTemplate
                            onBack={() => undefined}
                            launchContextId="launch-context-2"
                            initialValues={{
                                name: 'project_md',
                                intent: {
                                    source_ref: designRef,
                                    chemistry_profile_id: profile.id,
                                    chemistry_profile_sha256: profile.profile_sha256,
                                    requested_settings: {
                                        replicas: 4,
                                        random_seed: 23,
                                        padding_nm: 2,
                                        salt_molar: 0.3,
                                        neutralize: true,
                                        temperature_k: 310,
                                        pressure_bar: 2,
                                        timestep_fs: 4,
                                        minimization_steps: 50000,
                                        nvt_ps: 100,
                                        npt_ps: 100,
                                        production_ns: 0.001,
                                        trajectory_interval_ps: 1,
                                        energy_interval_ps: 0.2,
                                        checkpoint_interval_minutes: 15,
                                        ntomp: 8,
                                    },
                                },
                            }}
                        />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await act(async () => {
            await vi.waitFor(() => {
                expect(apiMocks.post.mock.calls, JSON.stringify(apiMocks.post.mock.calls)).toContainEqual([
                    '/api/molecular-dynamics/starting-structures/inspect',
                    { source_ref: designRef, chemistry_profile_id: profile.id },
                ]);
            });
        });
        await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain('Project Design candidate')); });
        const designTab = [...container.querySelectorAll('[role="tab"]')].find((item) => item.textContent === 'Your Runs');
        expect(designTab?.getAttribute('aria-selected')).toBe('true');
        const replicaControl = [...container.querySelectorAll('label')]
            .find((item) => item.textContent?.includes('Independent replicas'))
            ?.querySelector<HTMLInputElement>('input[type="number"]');
        expect(replicaControl?.value).toBe('1');
        expect(replicaControl?.disabled).toBe(true);
        expect(container.querySelector<HTMLInputElement>('[data-md-setting="random_seed"]')?.value).toBe('23');
        expect(apiMocks.post).toHaveBeenCalledWith(
            '/api/molecular-dynamics/starting-structures/inspect',
            { source_ref: designRef, chemistry_profile_id: profile.id },
        );
    });
});
