import React, { act } from 'react';
import fs from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { NativeAnalysis } from '../../src/components/MDNativeAnalysis';

// Explicit synthetic numerical receiving fixtures, not simulated scientific evidence.
// Only the graphics boundary is inert: the actual pane and MD analysis component mount.
vi.mock('react-plotly.js', () => ({ default: (props: unknown) => <div data-plot={JSON.stringify(props)} /> }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: unknown) => <div data-viewer={JSON.stringify(props)} /> }));
import MDResultsPane from '../../src/components/MDResultsPane';
import * as api from '../../src/lib/api';

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const synthetic = (): NativeAnalysis => ({
    schema: 'bms.md.analysis-report-set.v1', job_id: 'synthetic-native', status: 'completed', bounded: true,
    replica_states: [], retry: { active: false, eligible: false, reason: 'fixture' },
    evidence: { status: 'insufficient_evidence', reason: 'Synthetic receiving data, not equilibrium evidence', frames_are_independent_replicates: false },
    ensemble: { statistical_unit: 'replica', frame_pooling: false, completed_replicas: 1, mean_of_replica_mean_rmsd_angstrom: 0, sample_stdev_of_replica_mean_rmsd_angstrom: null, mean_of_replica_final_rmsd_angstrom: 0, sample_stdev_of_replica_final_rmsd_angstrom: null },
    reports: [{ schema: 'bms.md.analysis.v1', status: 'completed', replica: 0, method: 'md_selection_rmsd_v1', selection: 'resname SYN', inputs: { manifest_sha256: null },
        points: [{ replica: 0, source_frame: 0, time_ps: 0, rmsd_angstrom: 0, radius_of_gyration_angstrom: 2 }],
        residue_metrics: [{ segid: 'S', resid: 1, resname: 'SYN', rmsf_angstrom: 0.125, atom_count: 2 }], summary: { count: 1, min: 0, mean: 0, max: 0, final: 0 },
        specialized_analyzers: [{ analyzer_id: 'synthetic_contact', status: 'completed', selection_a: 'resname SYN', selection_b: 'resname OTHER', cutoff_angstrom: 4.5, points: [{ time_ps: 0, source_frame: 0, contact_count: 0, minimum_distance_angstrom: 5.25 }] }],
        pull_coordinates: [{ coordinate: 2, column: 5, label: 'synthetic angle', unit: 'deg', groups: ['A', 'B'], window: 'w0', replica: 0, sample_count: 10000, dimension: 1, time_unit: 'ps', points: [{ time_ps: 0, value: -15 }, { time_ps: 100, value: 30 }], histogram: { edges: [-20, 0, 40], counts: [3000, 7000], sample_count: 10000, normalization: 'count' } }],
    }],
    wham: { method: 'gmx_wham', dimension: 1, scope: 'selected_coordinate_across_windows', status: 'completed', coordinate_unit: 'deg', energy_unit: 'kJ/mol', inputs: [], request: { unit: 'deg', temperature_k: 300, windows: [{ replica: 0, window: 'w0', coordinate: 2, coordinate_count: 2 }, { replica: 1, window: 'w1', coordinate: 2, coordinate_count: 2 }] }, points: [{ coordinate: -5, pmf_kj_mol: 0 }, { coordinate: 5, pmf_kj_mol: 1.75 }], histograms: { columns: [{ replica: 0, window: 'w0', coordinate: 2, coordinate_count: 2 }, { replica: 1, window: 'w1', coordinate: 2, coordinate_count: 2 }], rows: [[-5, 20, 0], [5, 4, 30]] } },
});
async function mount(analysis: NativeAnalysis, windows = true, playback = false) {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Live transport forbidden')));
    for (const key of ['fetchMDRun', 'fetchMDSummary', 'fetchMDAnalysis', 'fetchMDArtifacts'] as const) vi.spyOn(api, key).mockRejectedValue(new Error('Unexpected uncached transport'));
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
    const job = 'synthetic-native';
    client.setQueryData(['md-run', job], response({ phase: 'completed', state_version: 1, engine: 'gromacs', chemistry: { profile_id: 'native' }, simulated_time_ps: 100, requested_time_ps: 100, allowed_actions: [], replicas: [], checkpoints: [] }));
    client.setQueryData(['md-summary', job], response({ status: 'completed', dynamics_state: 'completed', replica_count: 2, artifact_count: 6, replicas: [0, 1].map(replica => ({ replica, ...(windows ? { window_id: `w${replica}`, replicate_index: 0 } : {}), status: 'completed', engine: { name: 'gromacs' }, performance: {} })), trajectory_playback: playback ? { supported: true, replicas: [{ replica: 0 }, { replica: 1 }] } : { supported: false, reason: 'Trajectory output disabled in synthetic fixture' } }));
    vi.mocked(api.fetchMDAnalysis).mockResolvedValue(response(analysis) as Awaited<ReturnType<typeof api.fetchMDAnalysis>>);
    client.setQueryData(['md-artifacts', job], response({ artifacts: playback ? [0, 1].flatMap(replica => [
        { id: `top${replica}`, replica, semantic_role: 'analysis_topology', format: 'gro', atom_order_identity: `order${replica}`, sha256: 'a', content_url: '/inert/top' },
        { id: `traj${replica}`, replica, semantic_role: 'analysis_trajectory', format: 'xtc', atom_order_identity: `order${replica}`, sha256: 'b', content_url: '/inert/traj' },
        { id: `map${replica}`, replica, semantic_role: 'trajectory_frame_map', sha256: 'c', content_url: '/inert/map' },
    ]) : [{ id: 'pullx', name: 'native-pullx.xvg', semantic_role: 'pull_coordinates', content_url: '/inert/pullx' }] }));
    for (const replica of [0, 1]) client.setQueryData(['md-trajectory-frame-map', job, `map${replica}`, 'c'], { frames: [{ display_frame: 0, source_frame: 10 + replica, time_ps: 50 + replica, step: 25000 + replica }, { display_frame: 1, source_frame: 20 + replica, time_ps: 100 + replica, step: 50000 + replica }] });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><MDResultsPane jobId={job} /></QueryClientProvider></MemoryRouter>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(api.fetchMDAnalysis).toHaveBeenCalledWith(job);
}
const plots = () => Array.from(container.querySelectorAll('[data-plot]')).map(element => JSON.parse(element.getAttribute('data-plot')!));
const axis = (text: string) => plots().find(plot => plot.layout.yaxis.title.text === text);
const click = async (text: string) => act(async () => Array.from(container.querySelectorAll('button')).find(button => button.textContent === text)!.click());
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('mounts exact selected-group, contact, native pull and 1D WHAM arrays without rebucketing bounded points', async () => {
    await mount(api.parseMDAnalysisReportSet(synthetic()));
    expect(axis('Selection RMSD (Å)').data[0].y).toEqual([0]);
    expect(axis('Selection RMSF (Å)').data[0].y).toEqual([0.125]);
    expect(axis('Selection RMSF (Å)').data[0].customdata).toEqual([['SYN', 2]]);
    expect(axis('Atom-pair count').data[0].y).toEqual([0]);
    expect(axis('Distance (Å)').data[0].y).toEqual([5.25]);
    expect(axis('synthetic angle (deg)').data[0]).toMatchObject({ x: [0, 100], y: [-15, 30] });
    expect(axis('Samples (count)').data[0]).toMatchObject({ x: [-10, 20], width: [20, 40], y: [3000, 7000], customdata: [[-20, 0], [0, 40]] });
    expect(axis('PMF (kJ/mol)').data[0]).toMatchObject({ x: [-5, 5], y: [0, 1.75] });
    expect(axis('Native histogram count').data.map((trace: { y: number[] }) => trace.y)).toEqual([[20, 4], [0, 30]]);
    expect(container.textContent).toContain('Window w0 · replicate 0 · lane 0');
    expect(container.textContent).toContain('not a joint 2D PMF');
    expect(container.textContent).not.toContain('Completed independent replicas');
    expect(container.querySelector('pre')).toBeNull();
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/inert/pullx');
    expect(fetch).not.toHaveBeenCalled();
});
it('keeps absent structural analysis, failed optional execution and native WHAM failure separate from completed dynamics', async () => {
    const data = synthetic(); data.status = 'partial';
    data.reports = [{ schema: 'bms.md.analysis.v1', replica: 0, status: 'not_applicable', selection: null, reason: 'No selected atoms', inputs: { manifest_sha256: null }, pull_error: { code: 'NATIVE_PULL_FAILED', message: 'native pullx absent' } }];
    data.execution = [{ replica: 0, job_id: 'analysis-child', status: 'failed', error: 'native child exit 1' }];
    data.collection = { status: 'partial_failure', completed_analysis_children: 0, failed_analysis_children: 1, cancelled_analysis_children: 0 };
    data.wham = { ...data.wham!, status: 'failed', error: { code: 'WHAM_FAILED', message: 'native overlap error' }, returncode: 1 };
    await mount(data);
    expect(container.querySelector('[data-bms-md-lifecycle]')?.textContent).toContain('completed');
    expect(container.textContent).toContain('structural analysis: not_applicable');
    expect(container.textContent).toContain('native child exit 1');
    expect(container.textContent).toContain('native overlap error · exit 1');
    expect(container.textContent).toContain('native pullx absent');
    expect(axis('PMF (kJ/mol)')).toBeUndefined();
    expect(container.querySelector('[data-viewer]')).toBeNull();
    expect(container.textContent).toContain('Trajectory output disabled');
});

it('selects actual trajectory-only window lanes and keeps the displayed frame caption exact', async () => {
    const data = synthetic(); data.reports = []; data.wham = null; data.status = 'absent';
    await mount(data, true, true);
    const select = container.querySelector<HTMLSelectElement>('select[aria-label="Trajectory or structure lane"]')!;
    expect(Array.from(select.options).map(option => option.textContent)).toEqual(['Window w0 · replicate 0 · lane 0', 'Window w1 · replicate 0 · lane 1']);
    await click('Load trajectory playback');
    await act(async () => { select.value = '1'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('1');
    const viewer = JSON.parse(container.querySelector('[data-viewer]')!.getAttribute('data-viewer')!);
    expect(viewer.molecularDynamics.activeReplica).toBe(1);
    expect(viewer.molecularDynamics.playback.selectedFrame).toEqual({ replica: 1, displayFrame: 1, sourceFrame: 21, timePs: 101, step: 50001 });
    expect(viewer.label).toBe('Window w1 · replicate 0 · lane 1 · source frame 21 · 101 ps · step 50001');
    expect(container.textContent).toContain('Display frame 1 · source 21 / 101 ps / step 50001');
    expect(container.textContent).toContain('No WHAM result published.');
    expect(fetch).not.toHaveBeenCalled();
});
it('retains legacy protein RMSF, comparison and exact zero values', async () => {
    const data = synthetic(); data.wham = null;
    data.reports[0] = { ...data.reports[0], method: 'md_backbone_rmsd_v1', selection: 'protein and backbone', pull_coordinates: [], residue_metrics: [{ segid: 'A', resid: 1, resname: 'ALA', backbone_rmsf_angstrom: 0, backbone_atom_count: 4 }] };
    await mount(data, false);
    expect(axis('Backbone RMSD (Å)').data[0].y).toEqual([0]);
    expect(axis('Backbone RMSF (Å)').data[0].y).toEqual([0]);
    expect(axis('Final backbone RMSD (Å)').data[0].y).toEqual([0]);
    expect(container.textContent).toContain('Completed independent replicas: 1');
});

it.each([undefined, null, 'w0'])('preserves optional pull window %s and nullable lane metadata', async window => {
    const data = synthetic();
    const pull = data.reports[0].pull_coordinates![0];
    if (window === undefined) delete pull.window; else pull.window = window;
    data.reports[0].window_id = null; data.reports[0].replicate_index = null;
    delete pull.sample_count;
    const before = JSON.stringify(data);
    expect(api.parseMDAnalysisReportSet(data)).toBe(data);
    expect(JSON.stringify(data)).toBe(before);
    await mount(data);
    expect(axis('synthetic angle (deg)').data[0].y).toEqual([-15, 30]);
    expect(container.textContent).toContain('unavailable native samples');
});

it('shows publication/collection errors without advertising TRR playback', async () => {
    const data = synthetic();
    data.collection = { status: 'failed', completed_analysis_children: 0, failed_analysis_children: 1, cancelled_analysis_children: null, collection_errors: [{ code: 'MD_ANALYSIS_REPORT_INVALID', message: 'Native child sidecar malformed' }] };
    data.execution = [{ replica: 0, job_id: 'failed-child', status: 'failed', error: { code: 'NATIVE_EXIT', message: 'Native analyzer exit 2' } }];
    await mount(api.parseMDAnalysisReportSet(data));
    await act(async () => {
        client.setQueryData(['md-summary', 'synthetic-native'], response({ status: 'completed', dynamics_state: 'completed', replicas: [{ replica: 0, window_id: null, replicate_index: null, engine: { name: 'gromacs' }, performance: {}, publication_errors: { representative_structure: 'editconf native failure' } }], trajectory_playback: { supported: false, reason: 'MD playback requires one XTC trajectory, topology, and frame map per replica', error: { code: 'MD_TRAJECTORY_PLAYBACK_MANIFEST_INVALID', message: 'MD playback requires one XTC trajectory, topology, and frame map per replica' } } }));
    });
    await vi.waitFor(() => expect(container.textContent).toContain('editconf native failure'));
    expect(container.textContent).toContain('Native child sidecar malformed');
    expect(container.textContent).toContain('Native analyzer exit 2');
    expect(container.textContent).toContain('requires one XTC trajectory');
    expect(container.textContent).not.toContain('Load trajectory playback');
    expect(container.querySelector('pre')).toBeNull();
    expect(container.querySelector('[data-bms-md-lifecycle]')?.textContent).toContain('completed');
});

// Opt-in replay of retained real GROMACS/MDAnalysis/WHAM route output.
// No replacement numerical fixture or graphics-engine acceptance is implied.
if (process.env.BMS_MD_REAL_WIRES) it.each(['composed', 'composed-null'])('receives real routed native result: %s', async label => {
    const dir = `${process.env.BMS_MD_REAL_WIRES}/${label}`;
    const load = (name: string) => JSON.parse(fs.readFileSync(`${dir}/${name}-response.json`, 'utf8'));
    const raw = load('analysis'); const before = JSON.stringify(raw);
    const parsed = api.parseMDAnalysisReportSet(raw);
    expect(parsed).toBe(raw); expect(JSON.stringify(parsed)).toBe(before);
    expect(Object.hasOwn(parsed.reports[0].pull_coordinates![0], 'window')).toBe(label === 'composed-null');
    await mount(parsed);
    const artifacts: api.MDArtifact[] = load('artifacts').artifacts;
    await act(async () => {
        client.setQueryData(['md-summary', 'synthetic-native'], response(load('summary')));
        client.setQueryData(['md-artifacts', 'synthetic-native'], response(load('artifacts')));
        for (const lane of [0, 1]) {
            const artifact = artifacts.find(a => a.replica === lane && a.semantic_role === 'trajectory_frame_map')!;
            const frames = JSON.parse(fs.readFileSync(`${dir}/parent/replicas/replica_${lane}/analysis/trajectory-frame-map.json`, 'utf8'));
            client.setQueryData(['md-trajectory-frame-map', 'synthetic-native', artifact.id, artifact.sha256], frames);
        }
    });
    await vi.waitFor(() => expect(container.textContent).toContain('Load trajectory playback'));
    const report = parsed.reports[0]; const pull = report.pull_coordinates![0];
    expect(axis('Distance (nm)').data[0].y).toEqual(pull.points.map(p => p.value));
    expect(axis('Atom-pair count').data[0].y).toEqual(report.specialized_analyzers!.find(c => c.analyzer_id === 'pair')!.points!.map(p => p.contact_count));
    expect(axis('Samples (count)').data[0].y).toEqual(pull.histogram.counts);
    expect(axis('PMF (kJ/mol)').data[0].y).toEqual(parsed.wham!.points!.map(p => p.pmf_kj_mol));
    await click('Load trajectory playback');
    expect(container.querySelector('[data-viewer]')).not.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
});
