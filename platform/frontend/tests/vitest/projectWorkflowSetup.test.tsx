import React, { act, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
    listProteinProjectCapabilities: vi.fn(),
    createProjectWorkflowSetup: vi.fn(),
    getProjectWorkflowSetup: vi.fn(),
    saveProjectWorkflowSetupDraft: vi.fn(),
    prepareProjectWorkflowSetup: vi.fn(),
    launchDomainRunGroup: vi.fn(),
    deleteProjectWorkflowSetup: vi.fn(),
}));
vi.mock('../../src/lib/projectManager', async (original) => ({ ...(await original<Record<string, unknown>>()), ...api }));

import {
    NewProjectExperimentDialog,
    ProjectTechnicalDetails,
    ProjectWorkflowCard,
    ProjectWorkflowSetupBanner,
    useProjectWorkflowSetup,
} from '../../src/components/project-manager/ProjectWorkflowSetup';

const ready = {
    capability_id: 'protein.structure_prediction.esmfold2', label: 'ESMFold2 structure prediction', state: 'ready',
    adapter_id: 'bms.esmfold2.project-setup.v1', setup_destination: '/submit?template=structure_prediction&pred_method=esmfold2',
    source_requirements: ['protein_sequence_receipt'], follow_up_compatible_capability_ids: ['protein.structure_prediction.esmfold2'],
};
const setup = {
    schema: 'bms.project-workflow-setup.detail.v1', setup_context_id: 'setup-1', project_id: 'project-1',
    global_experiment_id: 'experiment-1', domain_experiment_id: 'domain-1', relationship_kind: 'primary', capability_id: ready.capability_id,
    state: 'open', validation_state: 'ready', setup_destination: ready.setup_destination, return_uri: '/projects/project-1?focus=experiment-1&selected=domain_experiment%3Adomain-1',
    generation: 2, project_label: 'Ubiquitin Project', experiment_label: 'Fold ubiquitin', workflow_label: ready.label,
    draft: { sequence: 'MQLK', recycles: 3 }, field_errors: {}, diagnostics: { preparation_record_id: 'prep-secret', request_sha256: 'a'.repeat(64) },
};
let container: HTMLDivElement; let root: Root; let client: QueryClient;
async function flush() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
async function waitUntil(assertion: () => void) { for (let i = 0; i < 30; i += 1) { try { assertion(); return; } catch { await flush(); } } assertion(); }
function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
async function render(node: React.ReactNode, entry = '/projects/project-1') { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><Location/><Routes><Route path="*" element={node}/></Routes></MemoryRouter></QueryClientProvider>)); }
function HookHarness() {
    const projectSetup = useProjectWorkflowSetup();
    const [settings, setSettings] = useState<Record<string, unknown>>({});
    if (!projectSetup.active) return <p>Standalone launcher</p>;
    if (!projectSetup.setup) return <p>Loading setup</p>;
    return <><ProjectWorkflowSetupBanner setup={projectSetup.setup}/><button onClick={() => { setSettings(projectSetup.settings); void projectSetup.saveDraft(projectSetup.settings); }}>Save draft</button><output>{JSON.stringify(settings)}</output></>;
}
beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.listProteinProjectCapabilities.mockResolvedValue({ schema: 'bms.protein-project-workflow-picker.v1', capabilities: [ready] });
    api.createProjectWorkflowSetup.mockResolvedValue(setup);
    api.getProjectWorkflowSetup.mockResolvedValue(setup);
    api.saveProjectWorkflowSetupDraft.mockResolvedValue({ ...setup, generation: 3 });
    api.deleteProjectWorkflowSetup.mockResolvedValue(undefined);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); document.body.replaceChildren(); });

describe('Project workflow setup', () => {
    it('lists only server-ready workflows and creates a primary setup before navigating to its native destination', async () => {
        await render(<NewProjectExperimentDialog projectId="project-1" open onClose={() => undefined}/>);
        await waitUntil(() => expect(container.textContent).toContain(ready.label));

        const inputs = container.querySelectorAll<HTMLInputElement>('input');
        await act(async () => {
            const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            set?.call(inputs[0], 'Fold ubiquitin'); inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
            set?.call(inputs[1], 'Predict a stable structure'); inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
            container.querySelector<HTMLInputElement>(`input[value="${ready.capability_id}"]`)?.click();
        });
        const create = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Continue to setup');
        await act(async () => create?.click());
        await waitUntil(() => expect(api.createProjectWorkflowSetup).toHaveBeenCalledTimes(1));
        expect(api.createProjectWorkflowSetup).toHaveBeenCalledWith('project-1', {
            schema: 'bms.project-workflow-setup.create.v1', relationship_kind: 'primary', global_experiment_id: null,
            experiment: { name: 'Fold ubiquitin', objective: 'Predict a stable structure' },
            domain_kind: 'protein_in_silico', capability_id: ready.capability_id,
        });
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('/submit?template=structure_prediction'));
        expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('setup_context_id=setup-1');
    });

    it('creates a follow-up under the existing global experiment', async () => {
        await render(<NewProjectExperimentDialog projectId="project-1" globalExperimentId="experiment-1" open onClose={() => undefined}/>);
        await waitUntil(() => expect(container.textContent).toContain(ready.label));
        const radio = container.querySelector<HTMLInputElement>(`input[value="${ready.capability_id}"]`);
        await act(async () => radio?.click());
        const create = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Continue to setup');
        await act(async () => create?.click());
        await waitUntil(() => expect(api.createProjectWorkflowSetup).toHaveBeenCalled());
        expect(api.createProjectWorkflowSetup).toHaveBeenCalledWith('project-1', {
            schema: 'bms.project-workflow-setup.create.v1', relationship_kind: 'follow_up', global_experiment_id: 'experiment-1',
            domain_kind: 'protein_in_silico', capability_id: ready.capability_id,
        });
    });

    it('shows incomplete setup actions and deletes only the Project-owned draft', async () => {
        await render(<ProjectWorkflowCard projectId="project-1" setup={setup}/>);
        expect(container.textContent).toContain('Setup incomplete');
        expect(container.textContent).toContain('Resume setup');
        const remove = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Delete draft');
        await act(async () => remove?.click());
        await waitUntil(() => expect(api.deleteProjectWorkflowSetup).toHaveBeenCalledWith('project-1', 'setup-1'));
    });

    it('hydrates and saves exact typed settings without defaults or a Job submission', async () => {
        await render(<HookHarness/>, '/submit?setup_context_id=setup-1&project_id=project-1');
        await waitUntil(() => expect(container.textContent).toContain('Ubiquitin Project'));
        const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save draft');
        await act(async () => save?.click());
        await waitUntil(() => expect(api.saveProjectWorkflowSetupDraft).toHaveBeenCalled());
        expect(api.saveProjectWorkflowSetupDraft).toHaveBeenCalledWith('project-1', 'setup-1', { expected_generation: 2, draft: setup.draft });
        expect(container.textContent).toContain('"recycles":3');
    });

    it('leaves standalone launchers unchanged without setup_context_id', async () => {
        await render(<HookHarness/>, '/submit?template=structure_prediction');
        expect(container.textContent).toContain('Standalone launcher');
        expect(api.getProjectWorkflowSetup).not.toHaveBeenCalled();
    });

    it('saves current values before preparation and navigates with immutable launch authority', async () => {
        function PrepareHarness() {
            const projectSetup = useProjectWorkflowSetup();
            if (!projectSetup.setup) return <p>Loading setup</p>;
            return <button onClick={() => void projectSetup.startRun({ sequence: 'EDITED' })}>Start run</button>;
        }
        api.prepareProjectWorkflowSetup.mockResolvedValue({ ...setup, generation: 3, preparation_id: 'prep-1', launch_context_id: 'launch-1' });
        api.launchDomainRunGroup.mockResolvedValue({ run_group_id: 'run-group-1' });
        await render(<PrepareHarness/>, '/submit?setup_context_id=setup-1&project_id=project-1');
        await waitUntil(() => expect(container.textContent).toContain('Start run'));
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Start run')?.click());
        await waitUntil(() => expect(api.saveProjectWorkflowSetupDraft).toHaveBeenCalledWith('project-1', 'setup-1', { expected_generation: 2, draft: { sequence: 'EDITED' } }));
        expect(api.prepareProjectWorkflowSetup).toHaveBeenCalledWith('project-1', 'setup-1', 3);
        expect(api.launchDomainRunGroup).toHaveBeenCalledWith('project-1', 'experiment-1', 'domain-1', [{ preparation_id: 'prep-1', launch_context_id: 'launch-1' }]);
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('launch_context_id=launch-1'));
        expect(container.querySelector('[data-testid="location"]')?.textContent).not.toContain('setup_context_id');
        expect(container.querySelector('[data-testid="location"]')?.textContent).not.toContain('project_id');
    });

    it('starts managed Project work without fabricating a typed launch context', async () => {
        function ManagedHarness() {
            const projectSetup = useProjectWorkflowSetup();
            if (!projectSetup.setup) return <p>Loading setup</p>;
            return <button onClick={() => void projectSetup.startRun({ backend: 'confornets', request: {} })}>Start managed run</button>;
        }
        api.prepareProjectWorkflowSetup.mockResolvedValue({ ...setup, generation: 3, preparation_id: 'prep-managed' });
        api.launchDomainRunGroup.mockResolvedValue({ run_group_id: 'run-group-managed' });
        await render(<ManagedHarness/>, '/submit?setup_context_id=setup-1&project_id=project-1');
        await waitUntil(() => expect(container.textContent).toContain('Start managed run'));
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Start managed run')?.click());
        await waitUntil(() => expect(api.launchDomainRunGroup).toHaveBeenCalledWith(
            'project-1', 'experiment-1', 'domain-1', [{ preparation_id: 'prep-managed', launch_context_id: null }],
        ));
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('/projects/project-1'));
    });

    it('keeps diagnostics absent until Technical details is disclosed', async () => {
        await render(<ProjectTechnicalDetails setup={setup}/>);
        expect(container.textContent).not.toContain('prep-secret');
        const details = container.querySelector('details');
        await act(async () => { details?.setAttribute('open', ''); details?.dispatchEvent(new Event('toggle', { bubbles: true })); });
        expect(container.textContent).toContain('prep-secret');
    });
});
