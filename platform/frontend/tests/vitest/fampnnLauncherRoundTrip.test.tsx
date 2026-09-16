import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_body: any) => ({ data: {} })), select: null as any, template: vi.fn(async () => ({ data: null as any })) }));
vi.mock('../../src/lib/api', async (original) => ({
    ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: [{ id: 'fampnn', name: 'FA-MPNN', modes: [{ id: 'design', name: 'Design', params: ['input_pdb'] }], params: [{ name: 'input_pdb', type: 'string', default: 'source.pdb' }] }] })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchTemplateById: mocks.template,
    fetchInputPresets: vi.fn(async () => ({ data: [] })), fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    submitJob: mocks.submit,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: { protein_design: { default_enabled: false } } }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { mocks.select = onSelect; return <output data-template-params>{JSON.stringify(currentParams)}</output>; } }));
import { JobSubmission } from '../../src/components/JobSubmission';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); vi.clearAllMocks(); });
async function mount(params: Record<string, unknown>, route = '/submit') {
    if (route === '/submit') localStorage.setItem('clonedJobData', JSON.stringify({ name: 'FA clone', model_id: 'fampnn', mode: 'design', params }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
}
async function launch() {
    const button = [...document.querySelectorAll('button')].find(el => el.textContent === 'Launch Experiment')!;
    expect(button).toBeTruthy();
    await act(async () => button.click());
}
it.each([{ mutation: [] }, { summary: [], mutation: [{ chain_id: 'H', author_number: 0, insertion_code: 'A' }] }])('manual clone preserves exact overrides in the real submit callback: %j', async overrides => {
    await mount({ input_pdb: 'source.pdb', fampnn_analysis_overrides: overrides, fampnn_analysis_declaration: { protected: true }, fampnn_analysis_policy: { protected: true }, core_protein_contract: 'server-owned' });
    expect(document.querySelector<HTMLInputElement>('[aria-label="Override mutation scope"]')?.checked).toBe(true);
    await act(async () => [...document.querySelectorAll('button')].find(el => el.textContent === 'Template Manager')!.click());
    expect(document.querySelector('[data-template-params]')?.textContent).toContain('fampnn_analysis_overrides');
    await launch();
    expect(mocks.submit).toHaveBeenCalledTimes(1);
    const payload = mocks.submit.mock.calls[0][0] as any;
    expect(payload.fampnn_analysis_overrides).toEqual(overrides);
    expect(payload.params).not.toHaveProperty('fampnn_analysis_declaration');
    expect(payload.params).not.toHaveProperty('fampnn_analysis_policy');
    expect(document.querySelector('[data-template-params]')?.textContent).toContain('fampnn_analysis_overrides');
});
it.each(['monomer', 'binder'])('unadmitted legacy %s template remains blocked without FA-MPNN controls', async mode => {
    mocks.template.mockResolvedValue({ data: { id: 'fa_general', name: 'FA general', stages: [], preset_params: { rfd_mode: mode, seq_method: 'fampnn', fampnn_analysis_declaration: { forbidden: true }, core_protein_scientific_contract: 1 }, user_params: [] } });
    await mount({}, '/submit?template=fa_general');
    expect(document.querySelector('[aria-label="Override mutation scope"]')).toBeNull();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('not an admitted FA-MPNN caller');
    await launch();
    expect(mocks.submit).not.toHaveBeenCalled();
});
it('loading a manual saved template preserves its scopes through model initialization and a source edit', async () => {
    await mount({}, '/submit?unused=1');
    await act(async () => mocks.select({ name: 'saved FA', model_id: 'fampnn', mode: 'design', params: { input_pdb: 'saved.pdb', fampnn_analysis_overrides: { mutation: [] } } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(document.querySelector<HTMLInputElement>('[aria-label="Override mutation scope"]')?.checked).toBe(true);
    const source = [...document.querySelectorAll<HTMLInputElement>('input')].find(el => el.value === 'saved.pdb')!;
    expect(source).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(source, 'changed.pdb'); source.dispatchEvent(new Event('input', { bubbles: true })); });
    await launch();
    expect(mocks.submit.mock.calls.at(-1)?.[0]).toMatchObject({ params: { input_pdb: 'changed.pdb' }, fampnn_analysis_overrides: { mutation: [] } });
});
it('malformed clone stays visible and blocks submission instead of defaulting', async () => {
    await mount({ input_pdb: 'source.pdb', fampnn_analysis_overrides: { mutation: [{ chain_id: 'AB', author_number: 0 }] } });
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('FA-MPNN');
    await launch();
    expect(mocks.submit).not.toHaveBeenCalled();
});
