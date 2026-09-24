import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ authoring: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: [{ id: 'boltzgen', name: 'BoltzGen', category: 'generative_design', modes: [{ id: 'ligand_binder', name: 'Ligand Binder Generation', params: [] }, { id: 'ntp_binder', name: 'Nucleotide Binder Generation', params: [] }], parameters: [] }] })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })), fetchTemplateById: vi.fn(async () => ({ data: null })),
    fetchModel: vi.fn(async () => ({ data: { id: 'boltzgen', name: 'BoltzGen', modes: [{ id: 'ligand_binder', name: 'Ligand Binder Generation', params: [] }, { id: 'ntp_binder', name: 'Nucleotide Binder Generation', params: [] }], parameters: [] } })),
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
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); mocks.authoring = null; });
async function mount(clone?: unknown) {
    if (clone) localStorage.setItem('clonedJobData', JSON.stringify(clone));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit?template=antibody_denovo']}><JobSubmission /></MemoryRouter></QueryClientProvider>));
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
