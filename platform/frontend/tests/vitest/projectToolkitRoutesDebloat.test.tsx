import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const spies = vi.hoisted(() => ({ workflow: vi.fn(), dataset: vi.fn(), project: vi.fn() }));
vi.mock('../../src/components/Layout', () => ({ Layout: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/runtime/installFeatures', () => ({ useResolvedBmsFeatures: () => ({ features: {}, resolved: true }) }));
vi.mock('../../src/components/MolBioToolkit/indexV2', () => ({ MolBioToolkitV2: () => <div data-testid="molecular-toolkit">Molecular editor</div> }));
vi.mock('../../src/components/NGSToolkit', () => ({ NGSToolkit: () => <div data-testid="ngs-toolkit">NGS launcher</div> }));
vi.mock('../../src/components/molbio-ngs/DomainWorkflowOperator', () => ({ default: (props: object) => { spies.workflow(props); return <div data-testid="workflow-operator">Shared workflow operator</div>; } }));
vi.mock('../../src/components/molbio-ngs/DomainDatasetOperator', () => ({ default: (props: object) => { spies.dataset(props); return <div data-testid="dataset-operator">Dataset builder</div>; } }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    GlobalExperimentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    useGlobalExperimentContext: () => ({
        workspaceId: 'project-1', globalExperimentId: 'global-1', domainExperimentId: 'domain-1', stateRevisionId: 'state-1',
        selectedWorkspace: { name: 'Test Project' }, selectedDomainExperiment: { domain_experiment_id: 'domain-1', local_state_revision_id: 'state-1' },
        availability: { canMutateDomain: true, reason: 'Ready' }, setStateRevisionId: vi.fn(), updateQueryParams: vi.fn(),
        contextHref: (path: string, values: Record<string, string> = {}) => `${path}?${new URLSearchParams({ workspace_id: 'project-1', global_experiment_id: 'global-1', domain_experiment_id: 'domain-1', state_revision_id: 'state-1', ...values })}`,
    }),
}));
vi.mock('../../src/lib/api', async (original) => ({
    ...await original<Record<string, unknown>>(),
    fetchMolBioNgsDomainState: async () => ({ current_state_revision_id: 'state-1', head_generation: 1 }),
    fetchMolBioNgsStateRevision: async () => ({ id: 'state-1', members: [] }),
    fetchProjectHub: spies.project,
}));
import App from '../../src/App';
let node: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(() => {
    vi.clearAllMocks();
    spies.project.mockRejectedValue(new Error('optional project summary unavailable'));
    node = document.createElement('div'); document.body.appendChild(node); root = createRoot(node);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => { act(() => root.unmount()); client.clear(); node.remove(); });
async function open(path: string) {
    await act(async () => { root.render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><App /></QueryClientProvider></MemoryRouter>); });
    await act(async () => { await vi.dynamicImportSettled(); });
    for (let i = 0; i < 100 && node.textContent?.includes('Loading BioModStack'); i++) {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); });
    }
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); });
}
describe('real App Project/Toolkit route boundaries', () => {
    it.each(['/designer', '/designer?section=plasmids&molbio_sequence_id=seq-1', '/designer?section=plasmids&action=add-plasmid'])('leaves %s in the molecular operator without a stacked dashboard', async (path) => {
        await open(path);
        expect(node.querySelector('[data-testid="molecular-toolkit"]')).not.toBeNull();
        expect(node.querySelector('[data-testid="workflow-operator"]')).toBeNull();
        expect(spies.project).not.toHaveBeenCalled();
    });
    it.each(['/ngs', '/ngs?section=sequence-data', '/ngs?section=analyses&job_id=job-1'])('leaves %s in the NGS toolkit', async (path) => {
        await open(path);
        expect(node.querySelector('[data-testid="ngs-toolkit"]')).not.toBeNull();
        expect(spies.project).not.toHaveBeenCalled();
    });
    it.each(['/ngs', '/designer'])('connects %s Plans & Runs, exact group and action to the shared operator', async (base) => {
        await open(`${base}?section=workflow-plans&run_group_id=group-1&action=resubmit`);
        expect(node.querySelector('[data-testid="workflow-operator"]')).not.toBeNull();
        expect(node.querySelector('[data-testid="ngs-toolkit"]')).toBeNull();
        expect(node.querySelector('[data-testid="molecular-toolkit"]')).toBeNull();
        expect(spies.workflow).toHaveBeenCalledWith(expect.objectContaining({ projectId: 'project-1', globalExperimentId: 'global-1', domainExperimentId: 'domain-1', initialRunGroupId: 'group-1' }));
        expect(spies.project).not.toHaveBeenCalled();
    });
    it('carries exact dataset revision selection without either launcher or dashboard', async () => {
        await open('/ngs?section=datasets&dataset_revision_ids=revision-1');
        expect(node.querySelector('[data-testid="dataset-operator"]')).not.toBeNull();
        expect(spies.dataset).toHaveBeenCalledWith(expect.objectContaining({ selectedRevisionIds: ['revision-1'], domainExperimentId: 'domain-1' }));
        expect(spies.project).not.toHaveBeenCalled();
    });
    it('keeps the full Project data view explicit and surfaces only its own error', async () => {
        await open('/designer?section=overview');
        expect(node.querySelector('[data-testid="molecular-toolkit"]')).toBeNull();
        expect(spies.project).toHaveBeenCalled();
        expect(node.querySelector('[role="alert"]')?.textContent).toContain('optional project summary unavailable');
    });
});
