import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('react-plotly.js', () => ({ default: ({ data, onClick }: any) => <div data-plot={JSON.stringify(data)}>{onClick && <button data-point onClick={() => onClick({ points: [{ customdata: [0, 20] }] })}>Select point</button>}</div> }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: ({ structureUrl, molecularDynamics, onLoadStateChange }: any) => { React.useEffect(() => { if (structureUrl && onLoadStateChange) onLoadStateChange('loaded'); }, [structureUrl, onLoadStateChange]); return <div data-viewer={structureUrl} data-scene={JSON.stringify(molecularDynamics ?? null)} />; } }));
import * as api from '../../src/lib/api';
import MDResultsPane from '../../src/components/MDResultsPane';
import { RemoteResultsPrompt } from '../../src/components/RemoteResultsPrompt';
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
const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
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

const shaA = 'a'.repeat(64);
const shaB = 'b'.repeat(64);
const frames = [
    { display_frame: 0, source_frame: 0, time_ps: 0, step: 0 },
    { display_frame: 1, source_frame: 10, time_ps: 200, step: 100_000 },
    { display_frame: 2, source_frame: 20, time_ps: 400, step: 200_000 },
    { display_frame: 3, source_frame: 30, time_ps: 600, step: 300_000 },
    { display_frame: 4, source_frame: 40, time_ps: 800, step: 400_000 },
    { display_frame: 5, source_frame: 50, time_ps: 1000, step: 500_000 },
];

const seedPlayback = (client: QueryClient, jobId: string) => {
    client.setQueryData(['md-run', jobId], response({
        schema: 'bms.md.run-detail.v1', job_id: jobId, job_status: 'completed', queue_status: 'completed', phase: 'completed', state_version: 1,
        chemistry: { profile_id: 'amber', profile_sha256: shaA, assurance: 'curated', verification_status: 'verified' }, engine: 'gromacs', replica_count: 1, replica_summary: { completed: 1 }, simulated_time_ps: 1000, requested_time_ps: 1000, checkpoint_available: false, allowed_actions: [], replicas: [], segments: [], checkpoints: [], events: [],
    }));
    client.setQueryData(['md-summary', jobId], response({
        schema: 'bms.md.summary.v1', job_id: jobId, status: 'completed', result_state: 'completed',
        source: 'validated_job_owned_manifests', bounded: true, aggregate_manifest_sha256: shaA,
        replica_count: 1, artifact_count: 3,
        replicas: [{ replica: 0, status: 'completed', engine: { name: 'gromacs' }, performance: {} }],
        analysis_status: 'absent',
        trajectory_playback: { supported: true, reason: 'qualified', replicas: [{ replica: 0, frame_count: 6 }] },
    }));
    client.setQueryData(['md-artifacts', jobId], response({
        schema: 'bms.md.artifact-inventory.v1', job_id: jobId, source: 'validated_job_owned_manifests', bounded: true,
        artifacts: [
            { id: 'top', replica: 0, name: 'topology.gro', bytes: 1, sha256: shaA, semantic_role: 'analysis_topology', atom_order_identity: 'order-0', format: 'gro', content_url: '/top' },
            { id: 'traj', replica: 0, name: 'production.xtc', bytes: 1, sha256: shaB, semantic_role: 'analysis_trajectory', atom_order_identity: 'order-0', format: 'xtc', content_url: '/traj' },
            { id: 'map', replica: 0, name: 'trajectory-frame-map.json', bytes: 1, sha256: shaA, semantic_role: 'trajectory_frame_map', atom_order_identity: 'order-0', format: 'json', content_url: '/map' },
        ],
    }));
    client.setQueryData(['md-analysis', jobId], response({
        schema: 'bms.md.analysis-report-set.v1', job_id: jobId, status: 'absent', bounded: true,
        replica_states: [{ replica: 0, status: 'absent' }], reports: [],
        ensemble: { statistical_unit: 'replica', frame_pooling: false, completed_replicas: 0, mean_of_replica_mean_rmsd_angstrom: null, sample_stdev_of_replica_mean_rmsd_angstrom: null },
        evidence: { status: 'insufficient_evidence', reason: 'fixture', frames_are_independent_replicates: false },
        retry: { eligible: false, active: false, reason: 'not available' },
    }));
    client.setQueryData(['md-trajectory-frame-map', jobId, 'map'], {
        schema: 'bms.md.trajectory-frame-map.v1', job_id: jobId, replica: 0, frame_count: 6, frames,
    });
};


let mounted: Root | undefined;
let client: QueryClient;
let container: HTMLDivElement;
const mount = async (element: React.ReactNode, queries = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })) => {
    client = queries; container = document.createElement('div'); document.body.appendChild(container); mounted = createRoot(container);
    await act(async () => mounted!.render(<MemoryRouter><QueryClientProvider client={client}>{element}</QueryClientProvider></MemoryRouter>));
};
const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === label)!;
const finalArtifact = { id: 'final', replica: 0, name: 'final.pdb', format: 'pdb', semantic_role: 'representative_structure', content_url: '/final', sha256: shaA };
const prepared = (id: string) => {
    const queries = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } }); seedPlayback(queries, id);
    const inv = queries.getQueryData<any>(['md-artifacts', id]);
    queries.setQueryData(['md-artifacts', id], response({ ...inv.data, artifacts: [...inv.data.artifacts, finalArtifact] })); return queries;
};
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); mounted = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); document.body.replaceChildren(); sessionStorage.clear(); });
describe('MD trim receiving behavior', () => {
    it('defers heavy playback and preserves byte identity on frame selection', async () => {
        const queries = prepared('demand'); const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ frames }) })); vi.stubGlobal('fetch', fetcher);
        await mount(<MDResultsPane jobId="demand" />, queries);
        expect(fetcher).not.toHaveBeenCalled(); expect(container.querySelector('[data-viewer]')?.getAttribute('data-viewer')).toBe('/final'); expect(container.querySelector('[data-viewer]')?.getAttribute('data-scene')).toBe('null');
        await act(async () => button('Load trajectory playback').click()); await settle();
        expect(fetcher).toHaveBeenCalledTimes(1); expect(container.querySelector('[data-viewer]')?.getAttribute('data-scene')).toContain('trajectoryArtifactId');
        await act(async () => container.querySelector<HTMLButtonElement>('[data-bms-md-display-frame="2"]')!.click());
        expect(fetcher).toHaveBeenCalledTimes(1); expect(container.querySelector('[data-viewer]')?.getAttribute('data-scene')).toContain('"sourceFrame":20');
        const inv = queries.getQueryData<any>(['md-artifacts', 'demand']);
        await act(async () => queries.setQueryData(['md-artifacts', 'demand'], response({ ...inv.data, artifacts: inv.data.artifacts.map((item: any) => item.id === 'map' ? { ...item, sha256: shaB } : item) }))); await settle(); expect(fetcher).toHaveBeenCalledTimes(2);
        seedPlayback(queries, 'other-job');
        await act(async () => mounted!.render(<MemoryRouter><QueryClientProvider client={queries}><MDResultsPane jobId="other-job" /></QueryClientProvider></MemoryRouter>)); await settle(); expect(fetcher).toHaveBeenCalledTimes(2); expect(button('Load trajectory playback')).toBeTruthy();
    });
    it('isolates a failed playback read, preserves the final structure, and permits explicit recovery', async () => {
        const queries = prepared('playback-error'); const fetcher = vi.fn().mockResolvedValueOnce({ ok: false, status: 422 }).mockResolvedValue({ ok: true, json: async () => ({ frames }) }); vi.stubGlobal('fetch', fetcher);
        await mount(<MDResultsPane jobId="playback-error" />, queries); await act(async () => button('Load trajectory playback').click()); await settle();
        expect(container.querySelector('[data-viewer]')?.getAttribute('data-viewer')).toBe('/final'); expect(container.querySelector('[data-viewer]')?.getAttribute('data-scene')).toBe('null'); expect(container.textContent).toContain('HTTP 422');
        await act(async () => button('Retry playback read').click()); await settle(); expect(fetcher).toHaveBeenCalledTimes(2); expect(container.querySelector('[data-viewer]')?.getAttribute('data-scene')).toContain('trajectoryArtifactId');
    });
    it('keeps analysis, exports and final structure despite failed summary/playback read', async () => {
        const queries = prepared('independent'); queries.removeQueries({ queryKey: ['md-summary', 'independent'] }); vi.spyOn(api, 'fetchMDSummary').mockRejectedValue(new Error('corrupt playback map'));
        const report = queries.getQueryData<any>(['md-analysis', 'independent']);
        queries.setQueryData(['md-analysis', 'independent'], response({ ...report.data, status: 'completed', reports: [{ replica: 0, status: 'completed', inputs: {}, points: [{ replica: 0, source_frame: 20, time_ps: 400, rmsd_angstrom: 2, radius_of_gyration_angstrom: 4 }], block_statistics: [{ block: 0, count: 1, mean_rmsd_angstrom: 2, mean_radius_of_gyration_angstrom: 4 }], specialized_analyzers: [{ analyzer_id: 'protein_interface_contacts_v1', status: 'completed', cutoff_angstrom: 4.5, definition: 'heavy-atom inter-segment contacts; first protein segment versus remaining protein segments', selection_a: 'protein segment 0', selection_b: 'protein segments 1..n', points: [] }, { analyzer_id: 'protein_nucleic_contacts_v1', status: 'not_applicable', reason: 'protein and nucleic selections are both required' }] }] }));
        await mount(<MDResultsPane jobId="independent" />, queries); await settle(); await settle();
        expect(container.querySelector('[data-viewer]')?.getAttribute('data-viewer')).toBe('/final'); expect(container.querySelector('a[href="/final"]')).toBeTruthy(); expect(container.querySelector('[data-plot]')?.getAttribute('data-plot')).toContain('2'); expect(container.textContent).toContain('Some result reads failed'); expect(container.textContent).toContain('mean_rmsd_angstrom'); expect(container.textContent).toContain('Cutoff 4.5 Å'); expect(container.textContent).toContain('protein and nucleic selections are both required');
    });
    it.each(['absent', 'partial', 'error'])('stops terminal inactive %s polling, preserves explicit reads', async status => {
        vi.useFakeTimers(); const queries = prepared('poll'); const data = queries.getQueryData<any>(['md-analysis', 'poll']); queries.removeQueries({ queryKey: ['md-analysis', 'poll'] });
        const read = vi.spyOn(api, 'fetchMDAnalysis'); status === 'error' ? read.mockRejectedValue(new Error('failed')) : read.mockResolvedValue(response({ ...data.data, status }) as any);
        await mount(<MDResultsPane jobId="poll" />, queries); await act(async () => { await vi.advanceTimersByTimeAsync(50); });
        const count = read.mock.calls.length; await act(async () => { await vi.advanceTimersByTimeAsync(20_000); }); expect(read).toHaveBeenCalledTimes(count);
        await act(async () => { await queries.refetchQueries({ queryKey: ['md-analysis', 'poll'] }); await vi.advanceTimersByTimeAsync(50); }); expect(read).toHaveBeenCalledTimes(count + 1);
    });
    it('continues active terminal analysis retry, settles and refreshes publication', async () => {
        vi.useFakeTimers(); const queries = prepared('retry'); const data = queries.getQueryData<any>(['md-analysis', 'retry']); queries.setQueryData(['md-analysis', 'retry'], response({ ...data.data, retry: { ...data.data.retry, active: true } }));
        const read = vi.spyOn(api, 'fetchMDAnalysis').mockResolvedValue(response({ ...data.data, status: 'completed' }) as any);
        const inventory = vi.spyOn(api, 'fetchMDArtifacts').mockResolvedValue(queries.getQueryData<any>(['md-artifacts', 'retry'])); vi.spyOn(api, 'fetchMDSummary').mockResolvedValue(queries.getQueryData<any>(['md-summary', 'retry']));
        await mount(<MDResultsPane jobId="retry" />, queries); await act(async () => { await vi.advanceTimersByTimeAsync(5_100); }); expect(read).toHaveBeenCalledTimes(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(20_000); }); expect(read).toHaveBeenCalledTimes(1); expect(inventory).toHaveBeenCalled();
    });
    it('invalidates MD families after late remote import without remount or focus', async () => {
        const queries = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
        const families = ['md-run', 'md-summary', 'md-artifacts', 'md-analysis', 'md-trajectory-frame-map']; for (const family of families) queries.setQueryData([family, 'remote'], { stale: true });
        const job = { id: 'remote', execution_target_id: 'vast:123', status: 'awaiting_input', awaiting_input: true, awaiting_stage: 'remote_results', remote_state: 'results_available' };
        await mount(<RemoteResultsPrompt job={job} />, queries);
        await act(async () => mounted!.render(<MemoryRouter><QueryClientProvider client={queries}><RemoteResultsPrompt job={{ ...job, status: 'completed', awaiting_input: false }} /></QueryClientProvider></MemoryRouter>));
        for (const family of families) expect(queries.getQueryState([family, 'remote'])?.isInvalidated).toBe(true);
    });
    it('refreshes mounted missing MD results after an operator pull imports native publication', async () => {
        const queries = prepared('remote-pane'); const full = prepared('remote-pane');
        queries.setQueryData(['md-artifacts', 'remote-pane'], response({ schema: 'bms.md.artifact-inventory.v1', artifacts: [] }));
        queries.removeQueries({ queryKey: ['md-summary', 'remote-pane'] });
        let imported = false;
        vi.spyOn(api, 'fetchMDRun').mockResolvedValue(full.getQueryData<any>(['md-run', 'remote-pane']));
        vi.spyOn(api, 'fetchMDSummary').mockImplementation(async () => { if (!imported) throw new Error('not published'); return full.getQueryData<any>(['md-summary', 'remote-pane']); });
        vi.spyOn(api, 'fetchMDArtifacts').mockImplementation(async () => full.getQueryData<any>(['md-artifacts', 'remote-pane']));
        vi.spyOn(api, 'fetchMDAnalysis').mockResolvedValue(full.getQueryData<any>(['md-analysis', 'remote-pane']));
        const pull = vi.spyOn(api, 'pullRemoteJobResults').mockImplementation(async () => { imported = true; return response({}) as any; });
        await mount(<><MDResultsPane jobId="remote-pane" /><RemoteResultsPrompt job={{ id: 'remote-pane', execution_target_id: 'vast:123', status: 'awaiting_input', awaiting_input: true, awaiting_stage: 'remote_results', remote_state: 'results_available' }} /></>, queries); await settle(); await settle();
        expect(container.querySelector('[data-viewer]')).toBeNull(); await act(async () => button('Pull results').click()); await settle(); await settle();
        expect(pull).toHaveBeenCalledTimes(1); expect(container.querySelector('[data-viewer]')?.getAttribute('data-viewer')).toBe('/final'); expect(container.textContent).not.toContain('Summary: not published'); full.clear();
    });
    it('refreshes bounded result readers at native lifecycle publication rather than polling inventory', async () => {
        vi.useFakeTimers(); const queries = prepared('publication'); const full = prepared('publication');
        const run = full.getQueryData<any>(['md-run', 'publication']); queries.setQueryData(['md-run', 'publication'], response({ ...run.data, phase: 'analyzing', job_status: 'running' }));
        queries.setQueryData(['md-artifacts', 'publication'], response({ schema: 'bms.md.artifact-inventory.v1', artifacts: [] }));
        vi.spyOn(api, 'fetchMDRun').mockResolvedValue(response({ ...run.data, state_version: 2 }) as any);
        const inventory = vi.spyOn(api, 'fetchMDArtifacts').mockResolvedValue(full.getQueryData<any>(['md-artifacts', 'publication']));
        vi.spyOn(api, 'fetchMDSummary').mockResolvedValue(full.getQueryData<any>(['md-summary', 'publication'])); vi.spyOn(api, 'fetchMDAnalysis').mockResolvedValue(full.getQueryData<any>(['md-analysis', 'publication']));
        await mount(<MDResultsPane jobId="publication" />, queries); expect(inventory).not.toHaveBeenCalled();
        await act(async () => { await vi.advanceTimersByTimeAsync(5_100); }); expect(container.querySelector('[data-viewer]')?.getAttribute('data-viewer')).toBe('/final'); const count = inventory.mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(20_000); }); expect(inventory).toHaveBeenCalledTimes(count); full.clear();
    });
    it('pages prediction candidates, refreshes only active/open reads and aborts on closing the lane', async () => {
        vi.useFakeTimers(); const jobId = '11111111-1111-4111-8111-111111111111';
        let active = true; let hold = false; let signal: AbortSignal | undefined;
        const candidate = (id: string, name: string) => ({ source_ref: { kind: 'design', id }, name, format: 'pdb', eligible: true, blocker_code: null, metrics: { plddt: 80, ptm: null, iptm: null, confidence: null }, created_at: null });
        const get = vi.spyOn(api.api, 'get').mockImplementation(async (url: any, config: any) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true }) as any;
            if (url.includes('/source-candidates')) {
                signal = config.signal; if (hold) return new Promise(() => {});
                return response({ schema_version: 'bms.md.prediction-source-candidates.v1', job: { id: jobId, name: 'Prediction', status: active ? 'running' : 'completed', model_id: 'boltz2', mode: 'predict', created_at: null, started_at: null, completed_at: null, failure: null }, candidates: config.params.cursor ? [candidate('33333333-3333-4333-8333-333333333333', 'Second page Design')] : Array.from({ length: 24 }, (_, index) => candidate(`${String(index + 2).padStart(8, '0')}-2222-4222-8222-222222222222`, index === 0 ? 'First page Design' : `Page-one Design ${index}`)), next_cursor: config.params.cursor ? null : 'next' }) as any;
            }
            return response([]) as any;
        }); vi.spyOn(api, 'fetchExecutionTargets').mockResolvedValue(response([]) as any);
        await mount(<MolecularDynamicsTemplate onBack={() => {}} initialValues={{ source_prediction_job_id: jobId }} />);
        await act(async () => { await vi.advanceTimersByTimeAsync(100); }); expect(container.textContent).toContain('First page Design');
        await act(async () => button('Next candidates').click()); await act(async () => { await vi.advanceTimersByTimeAsync(100); }); expect(container.textContent).toContain('Second page Design');
        expect(get.mock.calls.some(([, config]: any) => config?.params?.cursor === 'next')).toBe(true);
        const before = get.mock.calls.filter(([url]: any) => url.includes('/source-candidates')).length; active = false;
        await act(async () => { await vi.advanceTimersByTimeAsync(5_100); }); expect(get.mock.calls.filter(([url]: any) => url.includes('/source-candidates')).length).toBe(before + 1);
        await act(async () => { await vi.advanceTimersByTimeAsync(10_000); }); expect(get.mock.calls.filter(([url]: any) => url.includes('/source-candidates')).length).toBe(before + 1);
        hold = true; await act(async () => button('Refresh candidates').click()); expect(signal?.aborted).toBe(false);
        const source = container.querySelector<HTMLSelectElement>('[aria-label="Existing source"]')!;
        await act(async () => { source.value = 'server_file'; source.dispatchEvent(new Event('change', { bubbles: true })); }); expect(signal?.aborted).toBe(true);
    });
    it('pages governed files and saved sequences without preloading catalogs', async () => {
        const saved = (n: number) => ({ id: `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`, name: `Sequence ${n}`, sequence: 'AAAA', description: null, length: 4, organism: null, uniprot_id: null, ncbi_id: null, is_preset: false, created_at: '2026-01-01', updated_at: null });
        const get = vi.spyOn(api.api, 'get').mockImplementation(async (url: any, config: any) => {
            if (url === '/api/molecular-dynamics/chemistry-profiles') return response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true }) as any;
            if (url === '/api/user-sequences') return response(config.params.offset ? [saved(25)] : Array.from({ length: 24 }, (_, i) => saved(i + 1))) as any;
            if (url.endsWith('/server-files')) return response({ items: config.params.cursor ? [{ id: 'file25', label: 'File 25', bytes: 1, format: 'pdb' }] : Array.from({ length: 24 }, (_, i) => ({ id: `file${i}`, label: `File ${i}`, bytes: 1, format: 'pdb' })), count: config.params.cursor ? 1 : 24, next_cursor: config.params.cursor ? null : 'next' }) as any;
            return response([]) as any;
        }); vi.spyOn(api, 'fetchExecutionTargets').mockResolvedValue(response([]) as any);
        await mount(<MolecularDynamicsTemplate onBack={() => {}} />); await settle();
        expect(get.mock.calls.some(([url]: any) => url === '/api/user-sequences' || url.endsWith('/server-files'))).toBe(false);
        await act(async () => button('Your Runs').click()); await act(async () => button('Use saved sequence').click()); await settle();
        await act(async () => button('Next sequences').click()); await settle(); expect(container.textContent).toContain('Sequence 25');
        const source = container.querySelector<HTMLSelectElement>('[aria-label="Existing source"]')!; await act(async () => { source.value = 'server_file'; source.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
        await act(async () => button('Next files').click()); await settle(); expect(container.textContent).toContain('File 25');
        expect(get.mock.calls.some(([, config]: any) => config?.params?.offset === 24 && config?.signal instanceof AbortSignal)).toBe(true);
        expect(get.mock.calls.some(([, config]: any) => config?.params?.cursor === 'next' && config?.signal instanceof AbortSignal)).toBe(true);
    });
    it('preserves real picker placement, context, policy and preview binding while source viewer stays mounted', async () => {
        vi.spyOn(api.api, 'get').mockImplementation(async (url: any) => url === '/api/molecular-dynamics/chemistry-profiles' ? response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true }) as any : response([]) as any);
        vi.spyOn(api, 'fetchExecutionTargets').mockResolvedValue(response([{ id: 'vast:123', name: 'Worker', active: true, state: 'ready', capabilities: {} }]) as any);
        vi.spyOn(api, 'completeCurrentLaunchContext').mockResolvedValue('/');
        const post = vi.spyOn(api.api, 'post').mockImplementation(async (url: any, body: any) => {
            if (url.endsWith('/inspect')) return response(inspection) as any;
            if (url.endsWith('/launch-preview')) return launchPreviewResponse(body) as any;
            if (url.endsWith('/launch')) return response({ id: 'md-job' }) as any;
            throw new Error(`Unexpected POST ${url}`);
        });
        const context = '33333333-3333-4333-8333-333333333333';
        window.history.replaceState({}, '', '/submit?template=molecular_dynamics');
        await mount(<MolecularDynamicsTemplate onBack={() => {}} launchContextId={context} />); await settle();
        expect(button('Preview').disabled).toBe(true); await act(async () => button('Accepted samples').click()); await act(async () => button('Use verified 1AKI fixture').click()); await settle();
        expect(button('Preview').disabled).toBe(true); await act(async () => button('Use this structure').click());
        const select = container.querySelector<HTMLSelectElement>('[data-md-chemistry-profile]')!;
        await act(async () => { select.value = profile.id; select.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
        const viewer = container.querySelector('[data-viewer]'); await act(async () => button('Vast · Worker').click()); await act(async () => button('Preview').click()); await settle();
        const previewCall = post.mock.calls.find(([url]: any) => url.endsWith('/launch-preview'))!;
        expect((previewCall[1] as any).intent).toMatchObject({ execution_target_id: 'vast:123', execution_policy: { remote_result_policy: 'manual' }, launch_context_id: context });
        expect(button('Launch').disabled).toBe(false); await act(async () => button('Local').click()); expect(button('Launch').disabled).toBe(true);
        await act(async () => button('Preview').click()); await settle(); await act(async () => button('Launch').click()); await settle();
        const launch = post.mock.calls.find(([url]: any) => url.endsWith('/launch'))!;
        expect((launch[1] as any).intent).toMatchObject({ execution_target_id: null, execution_policy: { remote_result_policy: 'manual' }, launch_context_id: context });
        expect((launch[1] as any).preview_digest).toBe(hash('d')); expect(container.querySelector('[data-viewer]')).toBe(viewer);
    });
    it('retains saved OFF conflict until deliberate correction and mounts all settings', async () => {
        vi.spyOn(api.api, 'get').mockImplementation(async (url: any) => url === '/api/molecular-dynamics/chemistry-profiles' ? response({ schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: hash('c'), profiles: [profile], selectable_profile_ids: [profile.id], count: 1, bounded: true }) as any : response([]) as any);
        vi.spyOn(api, 'fetchExecutionTargets').mockResolvedValue(response([]) as any);
        await mount(<MolecularDynamicsTemplate onBack={() => {}} initialValues={{ md_form: { neutralize: false, randomSeed: 99, ntomp: 65 }, intent: { chemistry_profile_id: profile.id, chemistry_profile_sha256: profile.profile_sha256 } }} />); await settle();
        const neutralize = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find(input => input.closest('label')?.textContent?.includes('Neutralize'))!;
        expect(neutralize.disabled).toBe(true); expect(neutralize.checked).toBe(false); expect(container.textContent).toContain('Saved OFF conflicts'); expect(container.querySelector('[data-md-setting="random_seed"]')?.getAttribute('value')).toBe('99'); expect(container.querySelectorAll('input[type="number"]').length).toBe(15);
        await act(async () => button('Use profile neutralization').click()); expect(neutralize.checked).toBe(true); expect(container.textContent).not.toContain('Saved OFF conflicts');
    });
});
