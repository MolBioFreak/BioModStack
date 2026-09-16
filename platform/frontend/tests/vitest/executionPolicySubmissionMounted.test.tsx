import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../../src/lib/api', async original => ({
    ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: [{ id: 'fampnn', name: 'FA-MPNN', modes: [{ id: 'design', name: 'Design', params: ['input_pdb'] }], params: [{ name: 'input_pdb', type: 'string', default: 'source.pdb' }] }] })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })), fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: { protein_design: { default_enabled: false } } }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api } from '../../src/lib/api';
import { RESULT_POLICY_DEFAULT_KEY } from '../../src/lib/executionPolicy';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); });
it.each(['manual', 'automatic'] as const)('real shell clone hydration and launch preserve %s outside scientific parameters', async value => {
    localStorage.setItem(RESULT_POLICY_DEFAULT_KEY, value === 'manual' ? 'automatic' : 'manual');
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'clone', model_id: 'fampnn', mode: 'design', params: { input_pdb: 'source.pdb' }, execution_policy: { remote_result_policy: value } }));
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(localStorage.getItem('clonedJobData')).toBeNull();
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Successful remote results"]')?.value).toBe(value);
    await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Launch Experiment')!.click());
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe('/api/jobs');
    expect(post.mock.calls[0][1]).toMatchObject({ execution_policy: { remote_result_policy: value }, params: { input_pdb: 'source.pdb' } });
    expect((post.mock.calls[0][1] as {params: object}).params).not.toHaveProperty('remote_result_policy');
});
