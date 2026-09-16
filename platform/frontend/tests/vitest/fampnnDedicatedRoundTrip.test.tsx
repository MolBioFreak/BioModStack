import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_body: any) => ({ data: {} })), iteration: vi.fn(async (_body: any) => ({ data: {} })), select: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), uploadImmutableFile: vi.fn(async () => ({ data: { path: 'inputs/protein_local_redesign/source.pdb' } })), uploadFile: vi.fn(async () => ({ data: { path: 'source.pdb' } })), submitJob: mocks.submit, launchAntibodyIteration: mocks.iteration, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { mocks.select = onSelect; return <output data-saved>{JSON.stringify(currentParams)}</output>; } }));
vi.mock('../../src/components/FrameworkBrowser', () => ({ FrameworkBrowser: () => null }));
vi.mock('../../src/components/TargetAntigenSelector', () => ({ TargetAntigenSelector: () => null }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/Rfd3SourceSelector', () => ({ Rfd3SourceSelector: () => null }));
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';
import { ProteinLocalRedesignTemplate } from '../../src/components/ProteinLocalRedesignTemplate';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
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
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('summary');
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
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('FA-MPNN');
    await click('Launch RFD3 + Sequence + Validation');
    expect(mocks.submit).not.toHaveBeenCalled();
});
