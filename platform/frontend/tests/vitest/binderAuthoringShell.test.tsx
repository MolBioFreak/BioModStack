import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ authoring: null as any, project: null as any, saveDraft: vi.fn(), submit: vi.fn(async () => ({ data: {} })) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    submitJob: mocks.submit,
    fetchModels: vi.fn(async () => ({ data: [{ id: 'boltzgen', name: 'BoltzGen', category: 'generative_design', params: [{ name: 'target_pdb', type: 'string', description: 'Target structure', ui_placeholder: 'Target structure', required: false }], modes: [{ id: 'protein_binder', name: 'Protein Binder Generation', params: ['target_pdb'] }, { id: 'ligand_binder', name: 'Ligand Binder Generation', params: [] }, { id: 'ntp_binder', name: 'Nucleotide Binder Generation', params: [] }], parameters: [] }] })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })), fetchTemplateById: vi.fn(async () => ({ data: null })),
    fetchModel: vi.fn(async () => ({ data: { id: 'boltzgen', name: 'BoltzGen', params: [{ name: 'target_pdb', type: 'string', description: 'Target structure', ui_placeholder: 'Target structure', required: false }], modes: [{ id: 'protein_binder', name: 'Protein Binder Generation', params: ['target_pdb'] }, { id: 'ligand_binder', name: 'Ligand Binder Generation', params: [] }, { id: 'ntp_binder', name: 'Nucleotide Binder Generation', params: [] }], parameters: [] } })),
}));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(),
    getProjectWorkflowSetup: vi.fn(async () => mocks.project),
    saveProjectWorkflowSetupDraft: mocks.saveDraft,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: () => null }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/SequenceManager', () => ({ SequenceManager: () => null }));
vi.mock('../../src/components/MutagenesisTemplate', () => ({ MutagenesisTemplate: () => null }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: (props: any) => { mocks.authoring = props; return <output data-authoring>{JSON.stringify(props.initialValues)}</output>; } }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/ProteinModificationTemplate', () => ({ ProteinModificationTemplate: () => <output data-rfd3>Native RFD3 authoring</output> }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); mocks.authoring = null; mocks.project = null; });
async function mount(clone?: unknown, project = false) {
    if (clone) localStorage.setItem('clonedJobData', JSON.stringify(clone));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/submit?template=antibody_denovo${project ? '&setup_context_id=setup&project_id=project' : ''}`]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
it('BC2 cloned Job enters its dedicated authoring with exact native route and settings', async () => {
    const settings = { max_trajectories: 3, trajectory_only: false, losses: {}, binder_lengths: [] };
    await mount({ model_id: 'bindcraft2', mode: 'campaign', name: 'saved campaign', params: { bindcraft2_settings: settings, bc2_preview_digest: 'historical-digest' } });
    expect(mocks.authoring.initialValues).toMatchObject({ model_id: 'bindcraft2', mode: 'campaign', denovo_generator: 'bindcraft2', bindcraft2_settings: settings });
});
it('generic BoltzGen clone retains its ligand mode instead of entering VHH authoring', async () => {
    await mount({ model_id: 'boltzgen', mode: 'ligand_binder', name: 'saved ligand request', params: { num_designs: 7 } });
    expect(document.querySelector('[data-authoring]')).toBeNull();
    expect(document.body.textContent).toContain('Ligand Binder Generation');
});
it('binder chooser RFD3 callback enters the existing native launcher', async () => {
    await mount();
    expect(mocks.authoring).not.toBeNull();
    await act(async () => mocks.authoring.onOpenNativeRoute({ templateId: 'protein_modification_experimental' }));
    expect(document.querySelector('[data-rfd3]')).not.toBeNull();
});
it('binder chooser ligand callback enters model configuration without antibody coercion', async () => {
    await mount();
    await act(async () => mocks.authoring.onOpenNativeRoute({ modelId: 'boltzgen', mode: 'ligand_binder' }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(document.querySelector('[data-authoring]')).toBeNull();
    expect(document.body.textContent).toContain('Ligand Binder Generation');
});


it('Project binder draft hydrates, reports edits, and saves through the real JobSubmission banner', async () => {
    const initial = { denovo_generator: 'rfantibody', structure_validator: 'esmfold2', run_structure_validation: false };
    mocks.project = { project_id: 'project', setup_context_id: 'setup', generation: 1, draft: initial,
        project_label: 'Project', experiment_label: 'Experiment', workflow_label: 'De Novo Binder Design',
        state: 'open', return_uri: '/projects/project', field_errors: {} };
    mocks.saveDraft.mockImplementation(async (_project, _setup, request) => ({ ...mocks.project, generation: 2, draft: request.draft }));
    await mount(undefined, true);
    expect(mocks.authoring.initialValues).toEqual(initial);
    expect(typeof mocks.authoring.onDraftChange).toBe('function');
    const edited = { ...initial, job_name: 'saved from native authoring', target_source: { type: 'preset', path: 'inputs/target.pdb' }, selected_chain: 'a', selected_residues: ['a42A'], native_values: { zero: 0, off: false, empty: [], nullable: null } };
    await act(async () => mocks.authoring.onDraftChange(edited));
    const save = [...document.querySelectorAll('button')].find(button => button.textContent === 'Save draft')!;
    await act(async () => save.click());
    expect(mocks.saveDraft).toHaveBeenCalledWith('project', 'setup', { expected_generation: 1, draft: { ...edited, binder_native_drafts: {} } });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    // A save acknowledgment must not rehydrate and overwrite in-flight editor changes.
    expect(mocks.authoring.initialValues).toEqual(initial);
});


it.each([undefined, { target_pdb: '' }])('native handoff inherits only untouched fields before defaults: %j', async initialDraft => {
    await mount();
    await act(async () => mocks.authoring.onOpenNativeRoute({ modelId: 'boltzgen', mode: 'protein_binder', initialDraft,
        sources: { target: { path: 'inputs/materialized-target.cif', name: 'Saved target', chain: 'a', residues: ['a42A'] } } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(document.querySelector('[data-authoring]')).toBeNull();
    expect(document.body.textContent).toContain('Protein Binder Generation');
    const target = document.querySelector<HTMLInputElement>('input[placeholder="Target structure"]');
    expect(target).not.toBeNull();
    expect(target!.value).toBe(initialDraft ? '' : 'inputs/materialized-target.cif');
});


it('an unadvertised PPIFlow initial mode cannot fall through to a legacy request', async () => {
    await mount();
    await act(async () => mocks.authoring.onOpenNativeRoute({ modelId: 'ppiflow', mode: 'protein_binder', initialDraft: { job_name: 'initial generation' } }));
    const launch = [...document.querySelectorAll('button')].find(button => button.textContent === 'Launch Experiment')!;
    expect(launch.disabled).toBe(true);
    expect(launch.title).toContain('not advertised');
    await act(async () => launch.click());
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('advertised native generation submits only its model-mode fields, preserving explicit empty values', async () => {
    await mount();
    await act(async () => mocks.authoring.onOpenNativeRoute({ modelId: 'boltzgen', mode: 'protein_binder', initialDraft: { job_name: 'native generation', target_pdb: '', incompatible_legacy_setting: true } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    const launch = [...document.querySelectorAll('button')].find(button => button.textContent === 'Launch Experiment')!;
    expect(launch.disabled).toBe(false);
    await act(async () => launch.click());
    expect(mocks.submit).toHaveBeenCalledWith({ name: 'native generation', model_id: 'boltzgen', mode: 'protein_binder', params: { target_pdb: '' } }, { launchContext: false });
});


it('native mode navigation restores the destination draft including deliberately cleared source fields', async () => {
    await mount();
    await act(async () => mocks.authoring.onOpenNativeRoute({ modelId: 'boltzgen', mode: 'protein_binder', initialDraft: { job_name: 'protein draft', target_pdb: 'inputs/target.pdb' } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    const target = document.querySelector<HTMLInputElement>('input[placeholder="Target structure"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(target, ''); target.dispatchEvent(new Event('input', { bubbles: true })); });
    const mode = [...document.querySelectorAll('select')].find(select => [...select.options].some(option => option.value === 'protein_binder'))!;
    await act(async () => { mode.value = 'ligand_binder'; mode.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => { mode.value = 'protein_binder'; mode.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(document.querySelector<HTMLInputElement>('input[placeholder="Target structure"]')!.value).toBe('');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('protein draft');
});
