import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProjectManagerReadModel } from '../../src/lib/projectManager';

const api = vi.hoisted(() => ({ fetchMolBioNgsDomainState: vi.fn(), fetchProjectHub: vi.fn() }));
vi.mock('../../src/lib/api', async (original) => ({ ...(await original<Record<string, unknown>>()), ...api }));
vi.mock('../../src/components/project-manager/DNASequenceProjectWorkspace', () => ({
    DNASequenceProjectWorkspace: ({ model }: { model: { label: string } }) => <div>{model.label}</div>,
}));
import { NativeProjectDataPanel } from '../../src/pages/ProjectManager';

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
const create = vi.fn();
const add = vi.fn();
const select = vi.fn();
const project = { node_key: 'project:p', node_type: 'project', subject_id: 'p', parent_node_key: null, label: 'Example' };
const experiment = { node_key: 'global_experiment:e', node_type: 'global_experiment', subject_id: 'e', parent_node_key: project.node_key, label: 'Experiment' };
function workspace(id: string) { return { node_key: `domain_experiment:${id}`, node_type: 'domain_experiment', subject_id: id, parent_node_key: experiment.node_key, label: `Workspace ${id}` }; }
function summary(nodes: unknown[], selected = project) {
    return { project: { id: 'p', project_scope: 'ngs_molbio_local' }, tree: { nodes }, map: { nodes: [], edges: [], focus_node_key: project.node_key }, selection: { ...selected, relationship: {} }, allowed_actions: ['create_global_experiment'] } as unknown as ProjectManagerReadModel;
}
async function render(value: ProjectManagerReadModel) {
    await act(async () => root.render(<QueryClientProvider client={client}><NativeProjectDataPanel summary={value} stateRevisionId={null} onCreateExperiment={create} onCreateWorkspace={add} onSelectNode={select} /></QueryClientProvider>));
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); }); }
function button(text: string) { return Array.from(host.querySelectorAll('button')).find((element) => element.textContent === text)!; }
beforeEach(() => {
    vi.clearAllMocks();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); client.clear(); host.remove(); });
describe('standalone NGS/MolBio Projects', () => {
    it('does not request a Molecular Tools workspace for a v1 alignment domain', async () => {
        const value = summary([project, experiment, workspace('d')], workspace('d'));
        value.selection.summary = { domain_payload: { schema: 'bms.ngs-molbio-experiment.v1' } };
        await render(value);
        await settle();
        expect(host.textContent).toContain('Workflow results');
        expect(host.textContent).not.toContain('could not be loaded');
        expect(api.fetchMolBioNgsDomainState).not.toHaveBeenCalled();
        expect(api.fetchProjectHub).not.toHaveBeenCalled();
    });
    it('shows sequence intake guidance without requiring generic experiment setup', async () => {
        await render(summary([project]));
        expect(host.textContent).toContain('No DNA sequences attached yet');
        expect(host.textContent).not.toContain('New experiment');
        expect(host.textContent).not.toContain('Global');
        expect(api.fetchProjectHub).not.toHaveBeenCalled();
    });
    it('opens the single exact workspace from the project root', async () => {
        api.fetchMolBioNgsDomainState.mockResolvedValue({ current_state_revision_id: 'state-current' });
        api.fetchProjectHub.mockResolvedValue({ label: 'Project DNA sequences' });
        await render(summary([project, experiment, workspace('d')]));
        await settle(); await settle();
        expect(api.fetchMolBioNgsDomainState).toHaveBeenCalledWith('d');
        expect(api.fetchProjectHub).toHaveBeenCalledWith('p', 'e', 'd', 'state-current', expect.any(AbortSignal));
        expect(host.textContent).toContain('Project DNA sequences');
    });
    it('requires an explicit choice when the project has multiple workspaces', async () => {
        await render(summary([project, experiment, workspace('a'), workspace('b')]));
        expect(host.textContent).toContain('Choose a workspace');
        expect(api.fetchMolBioNgsDomainState).not.toHaveBeenCalled();
        await act(() => button('Open Workspace b').click());
        expect(select).toHaveBeenCalledWith('domain_experiment:b', 'domain_experiment', 'b');
    });
});
