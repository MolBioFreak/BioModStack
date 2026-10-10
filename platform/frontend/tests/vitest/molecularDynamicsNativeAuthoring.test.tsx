import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-real-viewer-boundary /> }));
import { api, parseMDAnalysisReportSet } from '../../src/lib/api';
import { MolecularDynamicsTemplate } from '../../src/components/MolecularDynamicsTemplate';
import { hydrateMolecularDynamicsNative, serializeMolecularDynamicsNativeDraft, parseNativeMdp, renderNativeMdp, parseMolecularDynamicsNativePreview, type MolecularDynamicsNativeIntent } from '../../src/components/molecularDynamicsUiState';

const fixture = (): MolecularDynamicsNativeIntent => ({ schema_version: 'bms.md.launch-intent.v2', name: 'native_test',
    input: { kind: 'prepared', coordinates: 'inputs/native.gro', topology: 'inputs/native.top' },
    replicas: 3, random_seed: 20260717, execution: { ntmpi: 1, ntomp: 2, gpu_offload: 'auto', pin: 'off' },
    stages: [{ name: 'production', mdp: { integrator: 'md', dt: '0.001', nsteps: 0, 'gen-seed': 0, 'ld-seed': -1, 'native-unknown': '1e-07', 'pull-ncoords': 2, 'pull-coord1-init': 0, 'pull-coord1-k': 100, 'pull-coord2-init': 1, 'pull-coord2-k': 200 } }],
    analysis: { selection: null, wham: null }, windows: [{ id: 'w0', mdp: { production: { 'pull-coord1-init': 0, 'pull-coord2-k': 0 } } }], execution_target_id: null, execution_policy: { remote_result_policy: 'manual' },
});
let root: Root; let host: HTMLDivElement; let query: QueryClient;
let calls: Array<{ url: string; body: unknown }>; let files: Array<{ name: string; path: string; is_directory: boolean; size_bytes: number }>;
const settle = async () => { await act(async () => { await new Promise(r => setTimeout(r, 20)); }); };
const button = (name: string) => Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent === name)!;
async function click(name: string) { expect(button(name), name).toBeTruthy(); await act(async () => button(name).click()); await settle(); }
async function change(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
async function mount(initialValues?: Record<string, unknown>) { await act(async () => root.render(<QueryClientProvider client={query}><MemoryRouter><MolecularDynamicsTemplate onBack={() => {}} initialValues={initialValues} /></MemoryRouter></QueryClientProvider>)); await settle(); }
beforeEach(() => {
    calls = []; files = []; sessionStorage.clear(); localStorage.clear();
    query = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    api.defaults.adapter = async config => {
        const url = config.url!; const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body }); let data: unknown;
        if (url === '/api/molecular-dynamics/chemistry-profiles') data = { schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: 'a'.repeat(64), profiles: [], selectable_profile_ids: [], count: 0, bounded: true };
        else if (url === '/api/execution-targets') data = [{ id: 'vast:123', name: 'Worker', active: true, state: 'ready', capabilities: {} }];
        else if (url === '/api/files/browse') data = { entries: files, path: '/' };
        else if (url === '/api/files/upload') { const file = (body as FormData).get('file') as File; const path = `${(body as FormData).get('path')}/${file.name}`; files.push({ name: file.name, path, size_bytes: file.size, is_directory: false }); data = { filename: file.name, path, size: file.size }; }
        else if (url === '/api/molecular-dynamics/launch-preview') { const intent = body.intent; data = { schema_version: 'bms.md.launch-preview.v2', input_identity: {}, source: null, effective_request: { engine: 'gromacs', stages: intent.stages, windows: intent.windows }, warnings: [], blockers: [], preview_digest: 'b'.repeat(64), execution_target_id: intent.execution_target_id, execution_policy: intent.execution_policy }; }
        else if (url === '/api/molecular-dynamics/launch') data = { id: 'inert-md-job', status: 'queued' };
        else throw new Error(`Unstubbed inert transport ${url}`);
        return { data, status: 200, statusText: 'OK', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove(); });

it('preserves native document identity, explicit false/zero/null and unknown entries through JSON reopen', () => {
    const intent = fixture(); intent.input = { kind: 'guided', neutralize: false, salt_molar: 0 };
    expect(hydrateMolecularDynamicsNative(JSON.parse(JSON.stringify({ intent })))).toEqual(intent);
    expect(hydrateMolecularDynamicsNative({ intent: { ...intent, execution_target_id: 'vast:old' }, execution_target_id: null })?.execution_target_id).toBeNull();
    const mdp = parseNativeMdp('; comment\nnsteps = 0\nnative-unknown = 1e-07\ngen-vel = no');
    expect(parseNativeMdp(renderNativeMdp(mdp))).toEqual(mdp);
    const saved = { schema: 'bms.md.job.v3', input: { coordinates: 'inputs/a.gro', topology: 'inputs/a.top' }, stages: intent.stages, execution: { ...intent.execution, gpu_id: '0' }, replicas: 2, random_seed: 12, analysis: { selection: null }, windows: intent.windows };
    const reopened = hydrateMolecularDynamicsNative({ md_job_config: saved });
    expect(reopened?.stages).toEqual(intent.stages); expect(reopened?.execution).not.toHaveProperty('gpu_id'); expect(reopened?.analysis?.selection).toBeNull();
});
it('validates native preview placement and keeps stage/window values without projection', () => {
    const intent = fixture(); const preview = { schema_version: 'bms.md.launch-preview.v2', input_identity: {}, source: null, effective_request: { stages: intent.stages, windows: intent.windows }, execution_target_id: null, execution_policy: intent.execution_policy, warnings: [], blockers: [], preview_digest: 'c'.repeat(64) };
    expect(parseMolecularDynamicsNativePreview(preview, intent)).toBe(preview);
    expect(() => parseMolecularDynamicsNativePreview({ ...preview, execution_target_id: 'vast:other' }, intent)).toThrow(/target/);
});
it('keeps not-applicable selection, pull/WHAM arrays and execution errors at the real API parser', () => {
    const pull = { coordinate: 2, column: 3, label: 'distance', unit: 'nm', groups: ['A', 'B'], window: 'w0', replica: 0, dimension: 1, time_unit: 'ps', points: [{ time_ps: 0, value: 0 }], histogram: { edges: [0, 1], counts: [1], sample_count: 1, normalization: 'count' } };
    const value = { schema: 'bms.md.analysis-report-set.v1', job_id: 'job', bounded: true, status: 'partial', replica_states: [{ replica: 0, status: 'not_applicable' }], reports: [{ schema: 'bms.md.analysis.v1', status: 'not_applicable', method: 'md_selection_rmsd_v1', selection: null, reason: 'disabled', inputs: { manifest_sha256: null }, pull_coordinates: [pull], pull_error: { code: 'test', message: 'native error' } }], wham: { status: 'completed', dimension: 1, method: 'gmx_wham', scope: 'selected_coordinate_across_windows', coordinate_unit: 'nm', energy_unit: 'kJ/mol', inputs: [], request: {}, points: [{ coordinate: 0, pmf_kj_mol: 0 }], histograms: { columns: [], rows: [[0, 2]] } }, collection: { status: 'partial' }, execution: [{ replica: 0, job_id: 'child', status: 'failed', error: 'native failure' }] };
    expect(parseMDAnalysisReportSet(value)).toBe(value);
    expect(() => parseMDAnalysisReportSet({ ...value, wham: { ...value.wham, dimension: 2 } })).toThrow();
});
it('uses mounted native controls, managed upload/browser and typed preview/launch transport without replacing native seeds', async () => {
    await mount({ intent: fixture(), name: 'native_test' });
    expect(host.querySelector('[aria-label="Protocol"]')).toHaveProperty('value', 'prepared');
    await change('Native MDP', renderNativeMdp({ ...fixture().stages[0].mdp, 'unknown-imported': 'keep-me' })); await click('Apply MDP');
    await change('nsteps', '250'); await change('pull-coord2-k', '0');
    await click('Browse / upload coordinates');
    const input = host.querySelector<HTMLInputElement>('[aria-label="Upload coordinates"]')!;
    await act(async () => { Object.defineProperty(input, 'files', { configurable: true, value: [new File(['native bytes'], 'uploaded.gro')] }); input.dispatchEvent(new Event('change', { bubbles: true })); });
    await settle(); await settle();
    const row = Array.from(host.querySelectorAll<HTMLElement>('[role="button"]')).find(e => e.textContent?.includes('uploaded.gro'))!;
    expect(row).toBeTruthy(); await act(async () => row.click());
    await click('Preview');
    const preview = calls.find(c => c.url.endsWith('/launch-preview'))!.body as { intent: MolecularDynamicsNativeIntent };
    expect(preview.intent.input.coordinates).toBe('inputs/uploaded.gro');
    expect(preview.intent.stages[0].mdp).toMatchObject({ nsteps: '250', 'pull-coord2-k': '0', 'gen-seed': '0', 'ld-seed': '-1', 'native-unknown': '1e-07', 'unknown-imported': 'keep-me' });
    expect(preview.intent.analysis).toEqual({ selection: null, wham: null });
    expect(preview.intent.windows).toEqual(fixture().windows);
    await click('Launch');
    expect(calls.find(c => c.url.endsWith('/launch'))!.body).toMatchObject({ intent: preview.intent, preview_digest: 'b'.repeat(64) });
});
it('starts native authoring inside the original workspace and keeps ordered stages out of compiled TPR requests', async () => {
    await mount();
    expect(host.textContent).toContain('RCSB');
    await change('Protocol', 'prepared'); await click('Add ordered stage'); await change('integrator', 'steep'); await change('nsteps', '0');
    await click('Add ordered stage');
    await change('Stage 2 name', 'production');
    await change('Protocol', 'compiled'); await change('tpr', 'inputs/precompiled.tpr');
    expect(host.textContent).toContain('MDP editing is not applied');
    await click('Preview');
    const body = calls.find(c => c.url.endsWith('/launch-preview'))!.body as { intent: MolecularDynamicsNativeIntent };
    expect(body.intent.input).toEqual({ kind: 'compiled', tpr: 'inputs/precompiled.tpr' }); expect(body.intent.stages).toEqual([]);
    await change('Protocol', 'prepared');
    expect(host.querySelector('[aria-label="Stage 2 name"]')).toHaveProperty('value', 'production');
    expect(host.querySelector('[aria-label="nsteps"]')).toHaveProperty('value', '0');
});
it('edits ordered stages, window centers and optional analysis through controls, then reopens the exact submitted document', async () => {
    await mount({ intent: fixture() });
    await change('Stage 1 name', 'bias');
    await change('w0 bias pull-coord1-init', '0.25');
    await click('Add contact'); await change('Contact 1 name', 'surface'); await change('Contact 1 selection_a', 'resname SUR'); await change('Contact 1 selection_b', 'nucleic'); await change('Contact 1 cutoff_angstrom', '4.5');
    await click('Add pull trace'); await change('Trace 1 coordinate', '2'); await change('Trace 1 column', '3'); await change('Trace 1 label', 'height'); await change('Trace 1 unit', 'nm'); await change('Trace 1 groups', 'Surface DNA'); await change('Trace 1 window', 'w0');
    const wham = Array.from(host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find(el => el.parentElement?.textContent?.includes('Native 1D WHAM'))!;
    await act(async () => wham.click()); await change('WHAM unit', 'nm'); await change('WHAM temperature_k', '300'); await change('WHAM begin_ps', '0'); await click('Add WHAM window');
    await change('WHAM window 1 replica', '0'); await change('WHAM window 1 window', 'w0'); await change('WHAM window 1 coordinate', '2'); await change('WHAM window 1 coordinate_count', '2');
    await click('Preview');
    const submitted = (calls.filter(c => c.url.endsWith('/launch-preview')).at(-1)!.body as { intent: MolecularDynamicsNativeIntent }).intent;
    expect(submitted.windows?.[0].mdp?.bias).toEqual({ 'pull-coord1-init': '0.25', 'pull-coord2-k': 0 });
    expect(submitted.analysis?.contacts?.[0]).toEqual({ name: 'surface', selection_a: 'resname SUR', selection_b: 'nucleic', cutoff_angstrom: 4.5 });
    expect(submitted.analysis?.pull_coordinates?.[0]).toMatchObject({ coordinate: 2, column: 3, groups: ['Surface', 'DNA'], window: 'w0' });
    expect(submitted.analysis?.wham).toMatchObject({ temperature_k: 300, begin_ps: 0, windows: [{ replica: 0, coordinate: 2, coordinate_count: 2, window: 'w0' }] });
    await act(async () => root.unmount()); root = createRoot(host); await mount(JSON.parse(JSON.stringify({ intent: submitted })));
    await click('Preview');
    expect((calls.filter(c => c.url.endsWith('/launch-preview')).at(-1)!.body as { intent: unknown }).intent).toEqual(submitted);
});
it('uploads a real topology directory selection through managed transport preserving relative include paths', async () => {
    await mount({ intent: fixture() });
    const top = new File(['#include "ff/atoms.itp"'], 'system.top');
    const include = new File(['[ atomtypes ]'], 'atoms.itp');
    Object.defineProperty(top, 'webkitRelativePath', { value: 'system/system.top' });
    Object.defineProperty(include, 'webkitRelativePath', { value: 'system/ff/atoms.itp' });
    const input = host.querySelector<HTMLInputElement>('[aria-label="Upload topology include tree"]')!;
    await act(async () => { Object.defineProperty(input, 'files', { value: [top, include] }); input.dispatchEvent(new Event('change', { bubbles: true })); });
    await settle();
    const uploads = calls.filter(c => c.url === '/api/files/upload').map(c => c.body as FormData);
    expect(uploads).toHaveLength(2);
    expect(String(uploads[1].get('path'))).toBe(`${uploads[0].get('path')}/ff`);
    expect((uploads[1].get('file') as File).name).toBe('atoms.itp');
    const pick = Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.startsWith('Use topology '))!;
    expect(pick).toBeTruthy(); await act(async () => pick.click()); await click('Preview');
    const submitted = (calls.find(c => c.url.endsWith('/launch-preview'))!.body as { intent: MolecularDynamicsNativeIntent }).intent;
    expect(submitted.input.topology).toBe(`${uploads[0].get('path')}/system.top`);
});
it('uses actual placement controls and preserves explicit Local on reopen', async () => {
    sessionStorage.setItem('bms.jobLauncher.executionTargetId', 'vast:123');
    await mount({ intent: fixture() });
    expect(button('Local').getAttribute('aria-pressed')).toBe('true');
    await click('Vast · Worker'); await click('Preview');
    expect((calls.filter(c => c.url.endsWith('/launch-preview')).at(-1)!.body as { intent: MolecularDynamicsNativeIntent }).intent.execution_target_id).toBe('vast:123');
    await click('Local'); expect(button('Launch').disabled).toBe(true); await click('Preview');
    expect((calls.filter(c => c.url.endsWith('/launch-preview')).at(-1)!.body as { intent: MolecularDynamicsNativeIntent }).intent.execution_target_id).toBeNull();
});
it('keeps cleared numeric draft text distinct from explicit zero and does not fabricate native values', async () => {
    await mount({ intent: fixture() }); await change('Replicas per window', '');
    await change('Protocol', 'compiled'); await change('Protocol', 'prepared');
    expect(host.querySelector('[aria-label="Replicas per window"]')).toHaveProperty('value', '');
    const draft = { ...fixture(), replicas: '', random_seed: '0' };
    expect(serializeMolecularDynamicsNativeDraft(draft).replicas).toBe('');
    expect(serializeMolecularDynamicsNativeDraft(draft).random_seed).toBe(0);
    expect(hydrateMolecularDynamicsNative(JSON.parse(JSON.stringify({ md_form: { native_intent: draft } })))?.replicas).toBe('');
});
