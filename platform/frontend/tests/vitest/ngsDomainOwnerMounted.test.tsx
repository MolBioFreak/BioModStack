import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    fetchGlobalWorkspaces: vi.fn(), fetchGlobalExperiments: vi.fn(),
    fetchMolBioNgsProjectExperiments: vi.fn(), fetchMolBioNgsDomainExperiment: vi.fn(),
    getNgsMolBioBinding: vi.fn(),
}));
vi.mock('../../src/lib/api', async (original) => ({ ...(await original<typeof import('../../src/lib/api')>()), ...mocks }));
vi.mock('../../src/lib/projectManager', () => ({ getNgsMolBioBinding: mocks.getNgsMolBioBinding }));
import { GlobalExperimentProvider, useGlobalExperimentContext } from '../../src/components/experiments/GlobalExperimentContext';

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const domain = { domain_experiment_id: 'D', project_id: 'P', global_experiment_id: 'E', global_domain_experiment_revision_id: 'DR' };
function Probe() {
    const context = useGlobalExperimentContext();
    return <><div role="status">{context.workspaceId}/{context.globalExperimentId}/{context.domainExperimentId}: {context.availability.status}</div>
        <p>{context.availability.reason}</p><a href={context.contextHref('/ngs')}>Reopen</a></>;
}
async function settle() {
    for (let index = 0; index < 12; index++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}
beforeEach(() => {
    vi.resetAllMocks();
    mocks.fetchGlobalWorkspaces.mockResolvedValue([{ id: 'P' }]);
    mocks.fetchGlobalExperiments.mockResolvedValue([{ id: 'E', workspace_id: 'P' }]);
    mocks.fetchMolBioNgsProjectExperiments.mockResolvedValue([domain]);
    mocks.fetchMolBioNgsDomainExperiment.mockResolvedValue(domain);
    mocks.getNgsMolBioBinding.mockResolvedValue({ provisioning_state: 'ready', command_state: 'applied', domain_revision_id: 'DR', global_receipt_id: 'receipt', acknowledgement_id: 'ack' });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });
async function mount(search: string) {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/ngs?' + search]}>
        <GlobalExperimentProvider><Probe /></GlobalExperimentProvider>
    </MemoryRouter></QueryClientProvider>));
    await settle();
}
it('resolves omitted parents from the exact Domain owner and preserves historical/auxiliary inputs', async () => {
    await mount('domain_experiment_id=D&section=history&state_revision_id=A&molbio_revision_id=R&dataset_revision_id=DATA&action=clone');
    expect(container.textContent).toContain('P/E/D: available');
    expect(mocks.getNgsMolBioBinding).toHaveBeenCalledWith('P', 'E', 'D', expect.anything());
    const query = new URL(container.querySelector('a')!.href).searchParams;
    expect(query.get('section')).toBe('history');
    expect(query.get('state_revision_id')).toBe('A');
    expect(query.get('molbio_revision_id')).toBe('R');
    expect(query.get('dataset_revision_id')).toBe('DATA');
    expect(query.get('action')).toBe('clone');
});
it('does not repair a conflicting supplied Project by selecting another owner', async () => {
    await mount('workspace_id=foreign&domain_experiment_id=D&section=evidence');
    expect(container.textContent).toContain('owner conflicts');
    expect(container.textContent).not.toContain(': available');
});
it('makes missing/unauthorized Domain ownership explicit without guessing parents', async () => {
    mocks.fetchMolBioNgsDomainExperiment.mockRejectedValue(new Error('Domain owner unavailable'));
    await mount('domain_experiment_id=D&section=history');
    expect(container.textContent).toContain('Domain owner unavailable');
    expect(mocks.getNgsMolBioBinding).not.toHaveBeenCalled();
});
