import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { deNovoRouteValues, deNovoSavedValues, deNovoSearch } from '../../src/components/deNovoWorkflowRoute';

const mocks = vi.hoisted(() => ({
    submit: vi.fn(async (_body: unknown) => ({ data: { id: 'fixture-not-a-live-job' } })),
    saved: [] as any[],
    create: vi.fn(),
    project: null as any,
    saveProject: vi.fn(),
}));
vi.mock('../../src/lib/api', async original => ({
    ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: [] })),
    fetchTemplates: vi.fn(async () => ({ data: [] })),
    fetchTemplateById: vi.fn(async () => ({ data: null })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })),
    fetchUserTemplates: vi.fn(async () => ({ data: mocks.saved })),
    createUserTemplate: mocks.create,
    submitJob: mocks.submit,
    completeCurrentLaunchContext: vi.fn(async () => null),
}));
vi.mock('../../src/lib/projectManager', async original => ({
    ...await original<typeof import('../../src/lib/projectManager')>(),
    getProjectWorkflowSetup: vi.fn(async () => mocks.project),
    saveProjectWorkflowSetupDraft: mocks.saveProject,
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
// Unrelated workflow editors are not the boundary under test. The de novo parent,
// its source editors, execution controls and template modal remain real.
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';

let root: Root | undefined;
let client: QueryClient;
function RouteProbe() {
    const location = useLocation(); const navigate = useNavigate();
    return <><output data-route>{location.pathname + location.search + location.hash}</output><button onClick={() => navigate(-1)}>History back fixture</button><button onClick={() => navigate(1)}>History forward fixture</button></>;
}
beforeEach(() => {
    mocks.create.mockImplementation(async data => {
        const saved = { ...data, id: 'saved-fixture', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
        mocks.saved = [saved]; return { data: saved };
    });
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined;
    client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear();
    vi.clearAllMocks(); mocks.saved = []; mocks.project = null;
});
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function mount(entry = '/submit?template=protein_modification_experimental', clone?: unknown) {
    if (clone) localStorage.setItem('clonedJobData', JSON.stringify(clone));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><JobSubmission /><RouteProbe /></MemoryRouter></QueryClientProvider>));
    await settle();
}
const route = () => document.querySelector('[data-route]')!.textContent!;
const visible = (e: Element) => !e.closest('[hidden]');
async function click(text: string) {
    const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(e => visible(e) && e.textContent?.trim() === text);
    expect(button, text).toBeTruthy(); await act(async () => button!.click()); await settle();
}
async function input(selector: string, value: string) {
    const field = document.querySelector<HTMLInputElement>(selector); expect(field, selector).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value); field!.dispatchEvent(new Event('input', { bubbles: true })); });
}

it('navigation metadata preserves context, native aliases and false/empty scientific values', () => {
    expect(deNovoRouteValues(new URLSearchParams('template=protein_local_redesign'))).toMatchObject({ modification_mode: 'rfd3_local_redesign' });
    const values = { backend: 'disco', disco_na_sequence: '', dump_trajectories: false, seed: 0 };
    expect(deNovoSavedValues('protein_modification_experimental', 'de_novo_design', values)).toEqual({ ...values, generator: 'disco', modification_mode: 'de_novo_design' });
    expect(deNovoSavedValues('protein_modification_experimental', 'region_redesign', {})).toEqual({ modification_mode: 'region_redesign' });
    const destination = deNovoSearch(new URLSearchParams('model=protein_local_redesign&mode=local_redesign&project_id=keep&extra=stay'), { modification_mode: 'shape_blueprint' });
    expect(Object.fromEntries(destination)).toEqual({ template: 'protein_modification_experimental', modification_mode: 'shape_blueprint', project_id: 'keep', extra: 'stay' });
});

it.each([
    ['protein_modification_experimental', 'de_novo_design', { generator: 'rfd3', min_length: 73, max_length: 99, seed: 0, dump_trajectories: false }, 'rfd3'],
    ['protein_modification_experimental', 'de_novo_design', { backend: 'disco', design_task: 'dna_conditioned', disco_na_sequence: '' }, 'disco'],
    ['protein_cad_experimental', 'de_novo_design', { backend: 'laproteina', design_task: 'unconditional', target_lengths: '125', laproteina_samples_per_length: 3 }, 'laproteina'],
])('Clone %s/%s reaches its actual Generate editor', async (model_id, mode, params, generator) => {
    await mount('/submit?extra=kept#context', { model_id, mode, name: 'Exact saved run', params });
    expect(document.querySelector('[data-bms-de-novo-form]')).not.toBeNull();
    expect(document.body.textContent).not.toContain('Select Model');
    const query = new URLSearchParams(route().split('?')[1].split('#')[0]);
    expect(query.get('template')).toBe('protein_modification_experimental');
    expect(query.get('generator')).toBe(generator);
    expect(query.get('extra')).toBe('kept'); expect(route()).toContain('#context');
    expect([...document.querySelectorAll('input')].some(e => e.value === 'Exact saved run')).toBe(true);
    expect(localStorage.getItem('clonedJobData')).toBeNull();
    expect(mocks.submit).not.toHaveBeenCalled();
});

it.each([
    ['de_novo_design', 'disco', 'dna_conditioned'],
    ['shape_blueprint', undefined, undefined],
    ['region_redesign', undefined, undefined],
])('launcher Back and re-entry keep retained %s route identity', async (modification_mode, generator, design_task) => {
    const query = deNovoSearch(new URLSearchParams('extra=keep'), { modification_mode, generator, design_task });
    await mount(`/submit?${query}`);
    await click('Back');
    await click('Experimental');
    const card = [...document.querySelectorAll('h3')].find(e => e.textContent === 'De Novo Design')?.closest('div.cursor-pointer');
    expect(card).toBeTruthy();
    await act(async () => (card as HTMLElement).click()); await settle();
    const reopened = new URLSearchParams(route().split('?')[1]);
    expect(reopened.get('modification_mode')).toBe(modification_mode);
    expect(reopened.get('generator')).toBe(generator ?? null);
    expect(reopened.get('design_task')).toBe(design_task ?? null);
    expect(reopened.get('extra')).toBe('keep');
    expect(document.querySelector('[data-bms-de-novo-form]')).not.toBeNull();
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('saved template round trip uses the real modal and retains current native values', async () => {
    await mount('/submit', { model_id: 'protein_modification_experimental', mode: 'de_novo_design', name: 'Retained name',
        params: { generator: 'rfd3', min_length: 73, max_length: 99, num_designs: 3, seed: 0, dump_trajectories: false } });
    await click('Template Manager');
    await input('input[placeholder="e.g., My Boltz Config"]', 'UI-only saved fixture');
    await click('Save Template');
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(mocks.saved[0]).toMatchObject({ base_template_id: 'protein_modification_experimental', params: {
        generator: 'rfd3', min_length: 73, max_length: 99, num_designs: 3, seed: 0, dump_trajectories: false,
    } });
    await click('Load');
    expect(document.querySelector('[data-bms-de-novo-form]')).not.toBeNull();
    expect([...document.querySelectorAll('input')].some(e => e.value === '73')).toBe(true);
    expect(route()).toContain('generator=rfd3'); expect(mocks.submit).not.toHaveBeenCalled();
});

it('Project hydration and Save draft retain native settings without launching a Project run', async () => {
    const initial = { generator: 'rfd3', min_length: 73, max_length: 99, num_designs: 3, seed: 0, dump_trajectories: false };
    mocks.project = { project_id: 'project-fixture', setup_context_id: 'setup-fixture', generation: 1, draft: initial,
        project_label: 'Fixture', experiment_label: 'Fixture', workflow_label: 'De Novo Design', state: 'open', return_uri: '/projects/project-fixture', field_errors: {} };
    mocks.saveProject.mockImplementation(async (_project, _setup, request) => ({ ...mocks.project, generation: 2, draft: request.draft }));
    await mount('/submit?template=protein_modification_experimental&project_id=project-fixture&setup_context_id=setup-fixture');
    await click('Save draft');
    expect(mocks.saveProject).toHaveBeenCalledWith('project-fixture', 'setup-fixture', expect.objectContaining({ expected_generation: 1, draft: expect.objectContaining(initial) }));
    expect([...document.querySelectorAll('input')].some(e => e.value === '73')).toBe(true);
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('direct generation model link bypasses the manual catalog and retains native field values through task history', async () => {
    await mount('/submit?model=protein_modification_experimental&mode=de_novo_design&generator=rfd3&extra=kept#context');
    expect(document.querySelector('[data-bms-de-novo-form]')).not.toBeNull();
    expect(document.body.textContent).not.toContain('Select Model');
    const minimum = [...document.querySelectorAll('label')].find(e => e.textContent?.includes('Minimum length'))?.querySelector('input');
    expect(minimum).toBeTruthy();
    minimum!.setAttribute('data-route-length', 'true'); await input('[data-route-length]', '123');
    await click('Shape');
    expect(route()).toContain('modification_mode=shape_blueprint');
    await click('History back fixture');
    expect([...document.querySelectorAll('input')].some(e => e.value === '123')).toBe(true);
    expect(route()).toContain('extra=kept'); expect(route()).toContain('#context');
    expect(mocks.submit).not.toHaveBeenCalled();
});
