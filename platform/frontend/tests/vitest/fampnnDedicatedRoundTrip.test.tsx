import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_body: any) => ({ data: {} })), iteration: vi.fn(async (_body: any) => ({ data: {} })), select: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), uploadImmutableFile: vi.fn(async () => ({ data: { path: 'inputs/protein_local_redesign/source.pdb' } })), uploadFile: vi.fn(async () => ({ data: { path: 'source.pdb' } })), submitJob: mocks.submit, fetchExecutionTargets: vi.fn(async () => ({ data: [] })), launchAntibodyIteration: mocks.iteration, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { mocks.select = onSelect; return <output data-saved>{JSON.stringify(currentParams)}</output>; } }));
vi.mock('../../src/components/FrameworkBrowser', () => ({ FrameworkBrowser: () => null }));
vi.mock('../../src/components/TargetAntigenSelector', () => ({ TargetAntigenSelector: () => null }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/Rfd3SourceSelector', () => ({ Rfd3SourceSelector: () => null }));
import { api } from '../../src/lib/api';
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';
import { ProteinLocalRedesignTemplate } from '../../src/components/ProteinLocalRedesignTemplate';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(node: React.ReactNode, refinement = false) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const entry = refinement ? { pathname: '/submit', state: { refinementMode: true, sourceJobId: 'source', selectedDesignIds: ['design'] } } : '/submit';
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>{node}</MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
async function click(label: string) { const button = [...document.querySelectorAll('button')].find(el => el.textContent?.includes(label)); expect(button, label).toBeTruthy(); await act(async () => button!.click()); }
const pdb = 'ATOM      1  CA  ALA A   1      10.000  10.000  10.000  1.00 80.00           C  \nEND\n';
function sourceFetch() {
    Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob([pdb]), text: async () => pdb })));
    URL.createObjectURL = vi.fn(() => 'blob:fixture'); URL.revokeObjectURL = vi.fn();
}
it('local validated launcher sends hydrated scope through its actual submit callback', async () => {
    sourceFetch();
    const overrides = { summary: [], mutation: [{ chain_id: 'A', author_number: 1, insertion_code: '' }] };
    await mount(<ProteinLocalRedesignTemplate onBack={() => {}} submissionModelId="protein_modification_experimental" initialValues={{ input_pdb: 'data/source.pdb', design_chains: 'A', redesign_ranges: 'A1-1', sequence_redesign_ranges: 'A1-1', seq_method: 'fampnn', fampnn_analysis_overrides: overrides }} />);
    await click('Launch RFD3 + Sequence + Validation');
    expect(mocks.submit, document.body.textContent || '').toHaveBeenCalledTimes(1);
    expect(mocks.submit.mock.calls[0][0].fampnn_analysis_overrides).toEqual(overrides);
});
it('antibody de novo clone restores selected sequence stage and sends scope through submitJob', async () => {
    sourceFetch();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ seq_design_fampnn: true, target_pdb: 'data/source.pdb', antigen_chains: 'A', epitope_residues: 'A1', fampnn_analysis_overrides: { mutation: [] } }} />);
    expect(document.querySelector('[aria-label="Override mutation scope"]')).not.toBeNull();
    await click('Launch De Novo Nanobody Pipeline');
    expect(mocks.submit, document.body.textContent || '').toHaveBeenCalledTimes(1);
    expect(mocks.submit.mock.calls[0][0].params.fampnn_analysis_overrides).toEqual({ mutation: [] });
});
it('mounted BoltzGen clone sends its own mode and saved native controls, not RFantibody defaults', async () => {
    sourceFetch();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{
        mode: 'nanobody_binder', target_pdb: 'data/source.pdb', antigen_chains: 'A', epitope_residues: 'A1',
        boltzgen_num_designs: 7, boltzgen_use_framework_template: false,
        boltzgen_scaffold_length: '95-110', boltzgen_cdr_h3_length: '8-12',
        boltzgen_skip_inverse_folding: true, boltzgen_avoid_cysteine: false,
        boltzgen_filter_biased: false, boltzgen_alpha: 0, boltzgen_step_scale: 0,
    }} />);
    await click('Launch BoltzGen Nanobody Batch');
    expect(mocks.submit, document.body.textContent || '').toHaveBeenCalledTimes(1);
    const request = mocks.submit.mock.calls[0][0];
    expect([request.model_id, request.mode]).toEqual(['antibody_denovo', 'nanobody_binder']);
    expect(request.params).toMatchObject({ boltzgen_num_designs: 7,
        boltzgen_use_framework_template: false, boltzgen_scaffold_length: '95-110',
        boltzgen_cdr_h3_length: '8-12', boltzgen_skip_inverse_folding: true,
        boltzgen_avoid_cysteine: false, boltzgen_filter_biased: false,
        boltzgen_alpha: 0, boltzgen_step_scale: 0 });
});
it('mounted seeded PPIFlow clone submits its distinct native route without target substitution', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{
        mode: 'generator_backbone_refine', ppiflow_seed_complex_path: 'data/seed.pdb',
        antibody_chains: 'H,L', antigen_chains: 'T',
    }} />);
    await click('Launch PPIFlow Seeded Batch');
    expect(mocks.submit, document.body.textContent || '').toHaveBeenCalledTimes(1);
    const request = mocks.submit.mock.calls[0][0];
    expect([request.model_id, request.mode]).toEqual(['antibody_denovo', 'generator_backbone_refine']);
    expect(request.params).toMatchObject({ ppiflow_seed_complex_path: 'data/seed.pdb', antibody_chains: 'H,L', antigen_chains: 'T' });
});
it('selected post-round subset is sent intact to iteration rather than a generator resubmission', async () => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[{
        pathname: '/submit', state: { refinementMode: true, sourceJobId: 'round-two',
            selectedDesignIds: ['candidate-2', 'candidate-4'], sourceOutputSourceFilter: 'ppiflow' },
    }]}><AntibodyDenovoTemplate onBack={() => {}} initialValues={{ seq_designer: 'fampnn' }} /></MemoryRouter></QueryClientProvider>));
    await click('Launch Antibody Refinement');
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(mocks.iteration).toHaveBeenCalledTimes(1);
    expect(mocks.iteration.mock.calls[0][0]).toMatchObject({ source_job_id: 'round-two',
        design_ids: ['candidate-2', 'candidate-4'], action: 'ui_refinement' });
});
it('mounted BC2 saved campaign keeps native draft separate and never submits antibody refinement', async () => {
    const settings = { max_trajectories: 11, trajectory_only: false, binder_lengths: [55, 75] };
    const inventory = { upstream_commit: 'pin', fields: {
        max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
        trajectory_only: { native_key: 'trajectory_only', observed_types: ['boolean'], has_native_default: true, native_default: true, status: 'typed' },
    }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
    const discovery = vi.fn(async () => ({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: false, settings: inventory }) }));
    vi.stubGlobal('fetch', discovery);
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ denovo_generator: 'bindcraft2', bindcraft2_settings: settings }} />);
    expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).not.toBeNull();
    expect(document.querySelector('[aria-label="max_trajectories"]')).not.toBeNull();
    expect(document.body.textContent).toContain('Model execution is not enabled.');
    expect(document.body.textContent).not.toContain('Launch BindCraft2 campaign');
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!)).toMatchObject({
        denovo_generator: 'bindcraft2', bindcraft2_settings: settings,
    });
    expect(document.body.textContent).not.toContain('Launch Antibody Refinement');
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(mocks.iteration).not.toHaveBeenCalled();
    expect(discovery).toHaveBeenCalledWith('/api/models/bindcraft2/native-settings', expect.any(Object));
    await act(async () => mocks.select({ name: 'reopened', params: { denovo_generator: 'bindcraft2', bindcraft2_settings: { max_trajectories: 0, trajectory_only: false } } }));
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!).bindcraft2_settings).toEqual({ max_trajectories: 0, trajectory_only: false });
});
it('mounted available BC2 campaign submits its own saved native settings and placement surface', async () => {
    const settings = { max_trajectories: 2, targets: [{ name: 'target', target_path: 'inputs/target.pdb' }], trajectory_only: false };
    const inventory = { upstream_commit: 'pin', fields: {
        max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
    }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) })));
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ denovo_generator: 'bindcraft2', bindcraft2_settings: settings }} />);
    expect(document.querySelector('[aria-label="Execution target"]')).not.toBeNull();
    vi.spyOn(api, 'post').mockResolvedValueOnce({ data: { preview_digest: 'native-preview', effective_settings: {} } });
    await click('Preview native campaign');
    await click('Launch BindCraft2 campaign');
    expect(mocks.submit).toHaveBeenCalledTimes(1);
    expect(mocks.submit.mock.calls[0][0]).toEqual({ name: expect.any(String), model_id: 'bindcraft2', mode: 'campaign',
        params: { bindcraft2_settings: settings, bc2_preview_digest: 'native-preview' } });
    expect(mocks.iteration).not.toHaveBeenCalled();
});
it('BC2 campaign shows a submission refusal without translating it into antibody refinement', async () => {
    mocks.submit.mockRejectedValueOnce({ response: { data: { detail: 'Native campaign unavailable' } } });
    const inventory = { upstream_commit: 'pin', fields: {
        max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
    }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) })));
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ denovo_generator: 'bindcraft2', bindcraft2_settings: { max_trajectories: 2 } }} />);
    vi.spyOn(api, 'post').mockResolvedValueOnce({ data: { preview_digest: 'native-preview', effective_settings: {} } });
    await click('Preview native campaign');
    await click('Launch BindCraft2 campaign');
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Native campaign unavailable');
    expect(mocks.iteration).not.toHaveBeenCalled();
});

const mutation = { mutation: [{ chain_id: 'H', author_number: 0, insertion_code: 'A' }] };
it('antibody refinement preserves mutation-only scope through actual iteration and saved-template callbacks', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ seq_designer: 'fampnn', fampnn_analysis_overrides: mutation }} />, true);
    expect(document.querySelector('[aria-label="Override mutation scope"]')).not.toBeNull();
    expect(document.querySelector('[aria-label="Override summary scope"]')).toBeNull();
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!).fampnn_analysis_overrides).toEqual(mutation);
    await click('Launch Antibody Refinement');
    expect(mocks.iteration).toHaveBeenCalledTimes(1);
    expect(mocks.iteration.mock.calls[0][0].param_overrides.fampnn_analysis_overrides).toEqual(mutation);
});
it('antibody rerun prefill arriving after mount restores FA-MPNN and its exact request', async () => {
    sourceFetch();
    function Harness() { const [values, setValues] = React.useState<any>(); return <><button onClick={() => setValues({ seq_design_fampnn: true, target_pdb: 'data/source.pdb', antigen_chains: 'A', epitope_residues: 'A1', fampnn_analysis_overrides: mutation })}>Load rerun request</button><AntibodyDenovoTemplate onBack={() => {}} initialValues={values} /></>; }
    await mount(<Harness />);
    await click('Load rerun request');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(document.querySelector('[aria-label="Override mutation scope"]')).not.toBeNull();
    await click('Launch De Novo Nanobody Pipeline');
    expect(mocks.submit.mock.calls.at(-1)?.[0].params.fampnn_analysis_overrides).toEqual(mutation);
});
it('antibody de novo saved-template selection restores the FA-MPNN stage and request', async () => {
    sourceFetch();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} />);
    await act(async () => mocks.select({ name: 'saved', params: { seq_designer: 'fampnn', target_pdb: 'data/source.pdb', antigen_chains: 'A', epitope_residues: 'A1', fampnn_analysis_overrides: mutation } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(document.querySelector('[aria-label="Override mutation scope"]')).not.toBeNull();
    await click('Launch De Novo Nanobody Pipeline');
    expect(mocks.submit.mock.calls.at(-1)?.[0].params.fampnn_analysis_overrides).toEqual(mutation);
});
it('antibody saved template reload replaces mutation scope without copying protected declarations', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ seq_designer: 'fampnn', fampnn_analysis_overrides: mutation }} />, true);
    await act(async () => mocks.select({ name: 'saved', params: { seq_designer: 'fampnn', fampnn_analysis_overrides: { mutation: [] }, fampnn_analysis_declaration: { forged: true } } }));
    await click('Launch Antibody Refinement');
    expect(mocks.iteration.mock.calls[0][0].param_overrides.fampnn_analysis_overrides).toEqual({ mutation: [] });
    expect(mocks.iteration.mock.calls[0][0].param_overrides).not.toHaveProperty('fampnn_analysis_declaration');
});
it('antibody saved summary overrides are visibly forbidden and never submitted', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ seq_designer: 'fampnn', fampnn_analysis_overrides: { summary: [], mutation: [] } }} />, true);
    expect([...document.querySelectorAll('[role="alert"]')].map(el => el.textContent).join(' ')).toContain('summary');
    await click('Launch Antibody Refinement');
    expect(mocks.iteration).not.toHaveBeenCalled();
});
it('local redraw rehydrates new prefill scopes and preserves exact draft instead of stale initialization', async () => {
    const drafts = vi.fn();
    function Harness() { const [values, setValues] = React.useState<any>({ seq_method: 'fampnn', fampnn_analysis_overrides: { mutation: [] } }); return <><button onClick={() => setValues({ seq_method: 'fampnn', fampnn_analysis_overrides: mutation })}>Rerun prefill</button><ProteinLocalRedesignTemplate onBack={() => {}} submissionModelId="protein_modification_experimental" initialValues={values} onDraftChange={drafts} /></>; }
    await mount(<Harness />);
    await click('Rerun prefill');
    expect(drafts.mock.calls.at(-1)?.[0].fampnn_analysis_overrides).toEqual(mutation);
});
it('local malformed prefill is visible and fails closed, not a render crash or default', async () => {
    await mount(<ProteinLocalRedesignTemplate onBack={() => {}} submissionModelId="protein_modification_experimental" initialValues={{ seq_method: 'fampnn', fampnn_analysis_overrides: { mutation: 'bad' } }} />);
    expect([...document.querySelectorAll('[role="alert"]')].map(el => el.textContent).join(' ')).toContain('FA-MPNN');
    await click('Launch RFD3 + Sequence + Validation');
    expect(mocks.submit).not.toHaveBeenCalled();
});
