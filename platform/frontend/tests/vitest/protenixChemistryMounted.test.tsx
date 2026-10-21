import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { JobSubmission } from '../../src/components/JobSubmission';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { orderedProtenixComponents, updateProtenixComponent, type ProtenixComponent } from '../../src/components/protenixChemistry';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/dashboard/IndependentProvisionPanel', () => ({ WorkflowProvisionPanel: () => null }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({ data: { workflows: { structure_prediction: { default_enabled: false } } } }) }));
vi.mock('../../src/lib/useSystemStatus', () => ({ useSystemStatus: () => ({ data: { data: { gpus: [] } }, dataUpdatedAt: Date.now(), isError: false, isLoading: false }) }));
const text = (n: ReactTestInstance): string => n.children.map(c => typeof c === 'string' ? c : text(c)).join('');
let renderer: ReactTestRenderer;
let client: QueryClient;
let drafts: any[] = [];
let templates: any[] = [];
let posts: any[] = [];
const oldAdapter = api.defaults.adapter;
const peptide: ProtenixComponent = { id: 'P', type: 'peptide', sequence: 'YAQGTFTSDYSILLDKKAQAAFIEYLLEGG', count: 2, copy_ids: ['P1', 'P2'],
    modifications: [{ ptmPosition: 2, ptmType: 'CCD_AIB' }, { ptmPosition: 13, ptmType: 'CCD_2ML' }, { ptmPosition: 20, ptmType: 'CCD_AIB' }],
    pairedMsa: '', unpairedMsa: '>query\nYAQGTFTSDYSILLDKKAQAAFIEYLLEGG', pairedMsaPath: 'inputs/paired.a3m', unpairedMsaPath: 'inputs/unpaired.a3m', msa: { precomputed_msa_dir: 'inputs/msa' }, templatesPath: 'inputs/hits.hhr' };
const fixture = { pred_method: 'protenix', sequence: peptide.sequence, primary_chain_id: 'P', protenix_use_msa: false, run_frustrampnn: false,
    complex_components: [
        { id: 'L', type: 'ligand', ligand_file: 'inputs/ligand.sdf', count: 15, copy_ids: Array.from({ length: 15 }, (_, i) => `L${i + 1}`) },
        peptide,
        { id: 'D', type: 'dna', sequence: 'AC', modifications: [{ basePosition: 1, modificationType: 'CCD_5CM' }] },
        { id: 'R', type: 'rna', sequence: 'AC', modifications: [{ basePosition: 2, modificationType: 'CCD_PSU' }], unpairedMsa: '' },
        { id: 'I', type: 'ion', ion: 'ZN', element: 'ZN', count: 20 },
    ],
    covalent_bonds: [{ left_entity: 2, right_entity: 1, left_position: 1, right_position: 1, left_atom: 'N', right_atom: 4, left_copy: 2, right_copy: 1 },
        { entity1: 2, entity2: 3, position1: 3, position2: 1, atom1: 'SG', atom2: 'P', copy1: 1, copy2: 1 }],
    constraint: { contact: [{ entity1: 2, entity2: 1, copy1: 1, copy2: 1, position1: 3, position2: 1, max_distance: 8 }],
        pocket: { binder_chain: { entity: 2, copy: 1 }, contact_residues: [{ entity: 1, copy: 1, position: 1 }], max_distance: 6 } },
};
async function mount(initialValues: any = fixture, cloneParent = false) {
    drafts = []; templates = []; posts = [];
    api.defaults.adapter = async config => {
        if (config.method === 'post' && config.url !== '/api/msa/provider-cache/inspect') { posts.push(JSON.parse(config.data)); throw new Error('Captured offline, no launch'); }
        const data = config.url === '/api/execution-targets' || config.url === '/api/templates' || config.url === '/api/models' ? [] : config.url === '/api/models/boltz2' ? { params: [] } : { providers: {}, state: 'ready' };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => { renderer = create(<MemoryRouter><QueryClientProvider client={client}>{cloneParent ? <JobSubmission /> : <StructurePredictionTemplate onBack={() => {}}
        initialValues={initialValues} onDraftChange={v => drafts.push(v)} onOpenTemplateManager={v => templates.push(v)} />}</QueryClientProvider></MemoryRouter>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}
const preview = () => renderer.root.findByType(ExecutionTargetPicker).props.workflowRequest.params;
const change = async (label: string, value: string) => act(async () => renderer.root.findByProps({ 'aria-label': label }).props.onChange({ target: { value } }));
const click = async (label: string) => act(async () => renderer.root.findAllByType('button').find(node => text(node).trim() === label)!.props.onClick());
const close = async () => { await act(async () => renderer.unmount()); client.clear(); };
afterEach(async () => { if (renderer) await close(); api.defaults.adapter = oldAdapter; vi.restoreAllMocks(); });

it('preserves counted copies, entity order, peptide chemistry and task records through rename, save, reopen and submission clone', async () => {
    await mount();
    expect(renderer.root.findAllByProps({ 'aria-label': 'Entity 2 msa_path' })).toHaveLength(0);
    expect(renderer.root.findAllByProps({ 'aria-label': 'Entity 2 unpairedMsaPath' })).toHaveLength(1);
    expect(preview().complex_components.map((c: any) => c.id)).toEqual(['L', 'P', 'D', 'R', 'I']);
    expect(preview().complex_components[0]).toMatchObject(fixture.complex_components[0]);
    expect(preview().complex_components[1]).toMatchObject(peptide);
    await change('Entity 2 name', 'harmless rename');
    await change('Entity 1 name', 'managed ligand');
    expect(preview().complex_components[1]).toMatchObject({ ...peptide, name: 'harmless rename' });
    await click('Save Template');
    const saved = JSON.parse(JSON.stringify(templates.at(-1).currentParams));
    expect(saved.covalent_bonds).toEqual(fixture.covalent_bonds);
    expect(saved.constraint).toEqual(fixture.constraint);
    expect(drafts.at(-1).native_job_request.params.complex_components).toEqual(saved.complex_components);
    await close(); await mount(saved);
    expect(preview().complex_components).toEqual(saved.complex_components);
    await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    const cloned = JSON.parse(JSON.stringify(posts[0].params));
    expect(cloned.covalent_bonds).toEqual(fixture.covalent_bonds);
    expect(cloned.constraint).toEqual(fixture.constraint);
    await close(); await mount(cloned);
    expect(preview().complex_components).toEqual(saved.complex_components);
});

it('retains an annotated single primary peptide and edits typed modifications and bonds without rebuilding chemistry', async () => {
    await mount({ ...fixture, complex_components: [peptide], covalent_bonds: [] });
    expect(preview().complex_components).toHaveLength(1);
    expect(preview().complex_components[0]).toMatchObject(peptide);
    await change('Entity 1 modification 2 CCD', 'CCD_NLE');
    await change('Entity 1 count', '16');
    await change('Entity 1 copy IDs (comma separated)', Array.from({ length: 16 }, (_, i) => `P${i + 1}`).join(','));
    await click('Add covalent bond');
    await change('Bond 1 left entity', '1'); await change('Bond 1 right entity', '1');
    await change('Bond 1 left position', '1'); await change('Bond 1 right position', '28');
    await change('Bond 1 left atom', 'N'); await change('Bond 1 right atom', 'C');
    expect(preview().covalent_bonds[0]).toEqual({ left_entity: 1, right_entity: 1, left_position: 1, right_position: 28, left_atom: 'N', right_atom: 'C' });
    expect(preview().complex_components[0].modifications[1]).toEqual({ ptmPosition: 13, ptmType: 'CCD_NLE' });
    expect(preview().complex_components[0].count).toBe(16);
    expect(preview().complex_components[0].copy_ids).toHaveLength(16);
    await click('Save Template'); const saved = JSON.parse(JSON.stringify(templates.at(-1).currentParams));
    await close(); await mount(saved);
    expect(preview().complex_components).toEqual(saved.complex_components);
});

it('keeps chemistry and supplied alignments independent of MSA settings, and invalidates sequence-derived alignments and template hits after sequence change', async () => {
    await mount({ ...fixture, complex_components: [peptide] });
    const msaToggle = renderer.root.findAllByType('select').find(node => node.parent && text(node.parent).includes('Use MSA'));
    expect(msaToggle).toBeTruthy();
    await act(async () => msaToggle!.props.onChange({ target: { value: 'true' } }));
    expect(preview().complex_components[0]).toMatchObject(peptide);
    await act(async () => msaToggle!.props.onChange({ target: { value: 'false' } }));
    await change('Entity 1 unpairedMsa', '>new\nYAQGTFTSDYSILLDKKAQAAFIEYLLEGG');
    expect(preview().complex_components[0].modifications).toEqual(peptide.modifications);
    const sequenceInput = renderer.root.findAllByType('textarea').find(node => node.props.value === peptide.sequence)!;
    await act(async () => sequenceInput.props.onChange({ target: { value: peptide.sequence + 'A' } }));
    expect(preview().complex_components[0].modifications).toEqual(peptide.modifications);
    expect(preview().complex_components[0].copy_ids).toEqual(peptide.copy_ids);
    expect(preview().complex_components[0]).not.toHaveProperty('unpairedMsa');
    expect(preview().complex_components[0]).not.toHaveProperty('templatesPath');
    expect(text(renderer.root)).toContain('Retained modifications and bond/contact/pocket residue positions are not remapped');
    await change('Entity 1 name', 'renamed after sequence edit');
    expect(preview().complex_components[0]).not.toHaveProperty('unpairedMsa');
    await change('Entity 1 unpairedMsa', '');
    expect(preview().complex_components[0].unpairedMsa).toBe('');
    expect(preview().complex_components[0].modifications).toEqual(peptide.modifications);
});

it('does not expose Protenix-only chemistry controls or task fields for Boltz', async () => {
    await mount({ pred_method: 'boltz', sequence: peptide.sequence, boltz_use_msa: false, run_frustrampnn: false, covalent_bonds: fixture.covalent_bonds });
    expect(renderer.root.findAllByProps({ 'aria-label': 'Protenix chemistry' })).toHaveLength(0);
    expect(preview()).not.toHaveProperty('covalent_bonds');
});

it('real JobSubmission clone routes a Protenix source job through the chemistry editor', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'clone chemistry', model_id: 'protenix', mode: 'complex', source_structure: { path: 'inputs/source.cif' }, params: fixture }));
    await mount(undefined, true);
    expect(renderer.root.findByType(StructurePredictionTemplate)).toBeTruthy();
    expect(preview().complex_components[1]).toMatchObject(peptide);
    expect(preview().covalent_bonds).toEqual(fixture.covalent_bonds);
});

it('edits native nucleotide, ligand, contact and pocket records without a JSON editor', async () => {
    await mount();
    await change('Entity 3 modification 1 CCD', 'CCD_5MC');
    await change('Entity 4 modification 1 position', '1');
    await change('Contact 1 max_distance', '9.5');
    await change('Pocket binder copy', '2');
    await change('Bond 2 left position', '4');
    await change('Entity 1 smiles', 'CCO');
    expect(preview().complex_components[0]).toMatchObject({ smiles: 'CCO', count: 15 });
    expect(preview().complex_components[0]).not.toHaveProperty('ligand_file');
    expect(preview().complex_components[2].modifications).toEqual([{ basePosition: 1, modificationType: 'CCD_5MC' }]);
    expect(preview().complex_components[3].modifications).toEqual([{ basePosition: 1, modificationType: 'CCD_PSU' }]);
    expect(preview().constraint.contact[0].max_distance).toBe(9.5);
    expect(preview().constraint.pocket.binder_chain.copy).toBe(2);
    expect(preview().covalent_bonds[1]).toMatchObject({ position1: 4 });
    expect(preview().covalent_bonds[1]).not.toHaveProperty('left_position');
});

it('serialization helper clears sequence-derived files and keeps native entity order', () => {
    const updated = updateProtenixComponent(peptide, { sequence: 'AC', name: 'new' });
    expect(updated.modifications).toEqual(peptide.modifications);
    expect(updated).not.toHaveProperty('templatesPath');
    expect(updated).not.toHaveProperty('pairedMsa');
    const ligand: ProtenixComponent = { id: 'L', type: 'ligand', count: 30, ccd: 'ATP' };
    expect(JSON.parse(JSON.stringify(orderedProtenixComponents(['L', 'P'], updated, [ligand])))).toEqual([ligand, updated]);
});
