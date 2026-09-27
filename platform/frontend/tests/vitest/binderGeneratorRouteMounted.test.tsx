import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_body: any) => ({ data: {} })), iteration: vi.fn(async (_body: any) => ({ data: {} })), select: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), fetchModelById: vi.fn(async (id: string) => ({ data: { id, params: [] } })), fetchInputPresets: vi.fn(async () => ({ data: [] })), listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })), uploadImmutableFile: vi.fn(async () => ({ data: { path: 'inputs/protein_local_redesign/source.pdb' } })), uploadFile: vi.fn(async () => ({ data: { path: 'source.pdb' } })), submitJob: mocks.submit, fetchExecutionTargets: vi.fn(async () => ({ data: [] })), launchAntibodyIteration: mocks.iteration, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { mocks.select = onSelect; return <output data-saved>{JSON.stringify(currentParams)}</output>; } }));
vi.mock('../../src/components/FrameworkBrowser', () => ({ FrameworkBrowser: () => null }));

vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/Rfd3SourceSelector', () => ({ Rfd3SourceSelector: () => null }));
import { AntibodyDenovoTemplate, type AntibodyDenovoTemplateProps } from '../../src/components/AntibodyDenovoTemplate';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function RouteIdentity() { const location = useLocation(); return <output data-route>{location.pathname + location.search + location.hash}</output>; }
async function mount(entry: string, props: Partial<AntibodyDenovoTemplateProps> = {}) {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true,
        settings: { fields: {}, presets: {}, registered_metrics: { filters: {}, losses: {} }, paratope_conformations: [] } }) })));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><RouteIdentity /><AntibodyDenovoTemplate onBack={() => {}} {...props} /></MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
const route = () => document.querySelector('[data-route]')!.textContent!;
const draft = () => JSON.parse(document.querySelector('[data-saved]')!.textContent!);
const chooserDetails = () => [...document.querySelectorAll('summary')].find(el => el.textContent === 'Change generation engine')!.parentElement as HTMLDetailsElement;
async function choose(label: string) {
    const buttons = [...document.querySelectorAll<HTMLButtonElement>('button')].filter(el => !el.closest('[hidden]'));
    const button = buttons.find(el => el.textContent === label); expect(button, label).toBeTruthy();
    await act(async () => button!.click());
}
async function remount(entry: string) { await act(async () => root.unmount()); client.clear(); document.body.replaceChildren(); await mount(entry); }
it('RF to BC2 selection persists engine identity on reopen without losing Project query or hash', async () => {
    await mount('/submit?template=antibody_denovo&project_id=project-1&setup=setup-2&launch_context=ctx&extra=keep#sources');
    expect(draft().denovo_generator).toBe('rfantibody');
    await act(async () => chooserDetails().querySelector('summary')!.click());
    expect(chooserDetails().open).toBe(true);
    await choose('BindCraft2 campaign');
    const destination = route(); const params = new URLSearchParams(destination.split('?')[1].split('#')[0]);
    expect(params.get('engine')).toBe('bindcraft2');
    for (const [key, value] of Object.entries({ project_id: 'project-1', setup: 'setup-2', launch_context: 'ctx', extra: 'keep' })) expect(params.get(key)).toBe(value);
    expect(destination.endsWith('#sources')).toBe(true);
    expect(draft().bindcraft2_settings).toEqual({});
    await remount(destination);
    expect(draft().denovo_generator).toBe('bindcraft2');
    expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).not.toBeNull();
    expect(mocks.submit).not.toHaveBeenCalled();
});
it('BC2 to RF selection reopens RF and leaves requested BC2 settings untouched', async () => {
    const settings = { targets: [], relax_steps: 0, trajectory_only: false, binder_name: null };
    await mount('/submit?template=antibody_denovo&engine=bindcraft2', { initialValues: { model_id: 'bindcraft2', mode: 'campaign', bindcraft2_settings: settings } });
    const select = [...document.querySelectorAll<HTMLSelectElement>('[aria-label="Binder format / objective"]')].find(el => !el.closest('[hidden]'))!;
    await act(async () => { select.value = 'antibody'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await choose('RFantibody Stack');
    expect(draft().bindcraft2_settings).toEqual(settings);
    const destination = route(); expect(destination).toContain('engine=rfantibody');
    await remount(destination); expect(draft().denovo_generator).toBe('rfantibody');
});
it.each([
    { initialValues: { model_id: 'bindcraft2', mode: 'campaign', bindcraft2_settings: { relax_steps: 0 } } },
    { initialDraft: { model_id: 'bindcraft2', mode: 'campaign', bindcraft2_settings: { relax_steps: 0 } } },
])('saved/clone/Project native identity takes precedence over URL: %j', async props => {
    await mount('/submit?template=antibody_denovo&engine=rfantibody', props);
    expect(draft().denovo_generator).toBe('bindcraft2'); expect(draft().bindcraft2_settings).toEqual({ relax_steps: 0 });
});
it('unknown URL selector preserves the warning rather than substituting RF', async () => {
    await mount('/submit?template=antibody_denovo&engine=unknown-engine');
    expect(document.body.textContent).toContain('Saved generator is not available here');
    expect(document.querySelector('[aria-label="Binder modality and generation engine"] button[aria-pressed="true"]')).toBeNull();
});
it('unknown saved selector is not replaced by a known URL engine', async () => {
    await mount('/submit?template=antibody_denovo&engine=rfantibody', { initialDraft: { denovo_generator: 'unknown-engine' } });
    expect(document.body.textContent).toContain('Saved generator is not available here');
    expect(draft().denovo_generator).toBe('unknown-engine');
});
it('explicit chooser reopening changes presentation only and remains dismissible', async () => {
    await mount('/submit?template=antibody_denovo', { initialEngineChooserOpen: true });
    expect(chooserDetails().open).toBe(true); const before = draft();
    await act(async () => chooserDetails().querySelector('summary')!.click());
    expect(chooserDetails().open).toBe(false); expect(draft()).toEqual(before);
    expect(route()).toBe('/submit?template=antibody_denovo'); expect(mocks.submit).not.toHaveBeenCalled();
});
