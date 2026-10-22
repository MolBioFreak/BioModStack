import React from 'react';
import { mkdirSync, writeFileSync } from 'node:fs';
import { LigandSelector } from '../../src/components/LigandSelector';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { JobSubmission } from '../../src/components/JobSubmission';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { type ProtenixComponent } from '../../src/components/protenixChemistry';
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
        const data = config.url === '/api/execution-targets' || config.url === '/api/templates' || config.url === '/api/models' ? [] : config.url === '/api/models/boltz2' ? { params: [], modes: [{ id: 'complex', name: 'Complex', params: [] }] } : { providers: {}, state: 'ready' };
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
afterEach(async () => {
    if (process.env.BMS_FRONTEND_EVIDENCE) {
        mkdirSync(process.env.BMS_FRONTEND_EVIDENCE, { recursive: true });
        writeFileSync(`${process.env.BMS_FRONTEND_EVIDENCE}/${expect.getState().currentTestName?.replace(/[^a-zA-Z0-9]+/g, '-').slice(0, 100)}.json`, JSON.stringify({ posts, templates, draft: drafts.at(-1) }, null, 2));
    }
    if (renderer) await close(); api.defaults.adapter = oldAdapter; vi.restoreAllMocks(); });


it('ESMFold2 exact zero-based chemistry, inline MSA and chain conditioning survive inactive save/reopen and Axios', async () => {
    const components = [
        { id: 'A', type: 'protein', sequence: 'ACDE', modifications: [{ position: 0, ccd: 'MSE' }], msa: { sequences: ['ACDE', 'A-DE'] }, count: 2, copy_ids: ['A2', 'A1'] },
        { id: 'D', type: 'dna', sequence: 'AC', modifications: [{ position: 0, ccd: '5CM' }] },
        { id: 'R', type: 'rna', sequence: 'AC', modifications: [{ position: 1, ccd: 'PSU' }] },
        { id: 'L', type: 'ligand', ccd: ['ATP'] },
    ];
    const bonds = [{ chain_id1: 'A2', res_idx1: 0, atom_idx1: 0, chain_id2: 'L', res_idx2: 0, atom_idx2: 3 }];
    const distograms = [{ chain_id: 'A2', distogram: [[0, 4], [4, 0]] }];
    await mount({ pred_method: 'esmfold2', complex_components: components, esmf_use_msa: false, run_frustrampnn: false,
        esmf_covalent_bonds: bonds, esmf_distogram_conditioning: distograms });
    await change('ESMFold2 entity 1 modification 1 position', '1');
    await change('ESMFold2 bond 1 atom_idx2', '0');
    await change('ESMFold2 distogram 1 row 1 column 2', '6');
    await switchPredictor('boltz-2');
    expect(preview().complex_components[0]).not.toHaveProperty('modifications');
    expect(preview()).not.toHaveProperty('esmf_covalent_bonds');
    await click('Add Boltz modification to A'); await change('Boltz entity 1 modification 1 CCD', 'SEP');
    const saved = await save(); await close(); await mount(saved); await switchPredictor('esmfold');
    expect(preview().complex_components[0]).toMatchObject({ modifications: [{ position: 1, ccd: 'MSE' }], msa: components[0].msa, copy_ids: ['A2', 'A1'] });
    expect(preview().complex_components.slice(1)).toMatchObject(components.slice(1));
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params.esmf_covalent_bonds[0]).toMatchObject({ atom_idx1: 0, atom_idx2: 0, res_idx1: 0 });
    expect(posts[0].params.esmf_distogram_conditioning[0].distogram).toEqual([[0, 6], [4, 0]]);
    for (const key of ['covalent_bonds', 'constraint', 'templates', 'constraints', 'esmfold2_component_settings']) expect(posts[0].params).not.toHaveProperty(key);
    await switchPredictor('boltz-2'); expect(preview().complex_components[0].modifications).toEqual([{ position: 1, ccd: 'SEP' }]);
});
it('ESMFold2 native ordered ID lists retain copy identity and explicit null MSA through cold reopen', async () => {
    await mount({ pred_method: 'esmfold2', complex_components: [{ id: ['Z2', 'Z1'], type: 'protein', sequence: 'ACDE', msa: null, modifications: [{ position: 0, ccd: 'MSE' }] }, { id: ['L2', 'L1'], type: 'ligand', ccd: ['ATP'] }], esmf_use_msa: false, run_frustrampnn: false });
    expect(preview().complex_components[0]).toMatchObject({ id: 'Z2', copy_ids: ['Z2', 'Z1'], msa: null });
    expect(preview().complex_components[1]).toMatchObject({ id: 'L2', copy_ids: ['L2', 'L1'], ccd: ['ATP'] });
    await change('ESMFold2 entity 1 copy IDs', 'Z1,Z2');
    const saved = await save(); await close(); await mount(saved); await click('Launch Prediction');
    expect(posts[0].params.complex_components[0]).toMatchObject({ copy_ids: ['Z1', 'Z2'], msa: null, modifications: [{ position: 0, ccd: 'MSE' }] });
});

it.each(['components', 'sequences'])('ESMFold2 native %s document clone retains ordered ID lists and task-level conditioning', async key => {
    const bond = { chain_id1: 'B', res_idx1: 0, atom_idx1: 0, chain_id2: 'L', res_idx2: 0, atom_idx2: 1 };
    const distogram = { chain_id: 'B', distogram: [[0]] };
    await mount({ pred_method: 'esmfold2', esmf_use_msa: false, run_frustrampnn: false, complex_components: {
        [key]: [{ id: ['B', 'A'], type: 'protein', sequence: 'ACDE', msa: null, modifications: [{ position: 0, ccd: 'MSE' }] }, { id: 'L', type: 'ligand', ccd: ['ATP'] }],
        covalent_bonds: [bond], distogram_conditioning: [distogram],
    } });
    await change('ESMFold2 entity 1 copy IDs', 'B,A');
    const saved = await save(); await close(); await mount(saved); await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(posts[0].params.complex_components[0]).toMatchObject({ id: 'B', copy_ids: ['B', 'A'], msa: null, modifications: [{ position: 0, ccd: 'MSE' }] });
    expect(posts[0].params).toMatchObject({ esmf_covalent_bonds: [bond], esmf_distogram_conditioning: [distogram] });
});

it('fresh ESMFold2 controls submit explicit null and empty inline MSA without borrowing Boltz numeric positions', async () => {
    await mount({ pred_method: 'boltz', complex_components: [{ id: 'A', type: 'protein', sequence: 'ACDE', modifications: [{ position: 1, ccd: 'SEP' }], msa: 'empty' }], boltz_use_msa: false, esmf_use_msa: false, run_frustrampnn: false });
    await switchPredictor('esmfold'); expect(preview().complex_components[0]).not.toHaveProperty('modifications');
    await click('Add ESMFold2 modification to A'); await change('ESMFold2 entity 1 modification 1 CCD', 'MSE');
    await change('ESMFold2 entity 1 MSA mode', 'none');
    expect(preview().complex_components[0]).toMatchObject({ modifications: [{ position: 0, ccd: 'MSE' }], msa: null });
    await change('ESMFold2 entity 1 MSA mode', 'inline');
    await click('Launch Prediction'); expect(posts[0].params.complex_components[0].msa).toEqual({ sequences: [] });
});
it('ESMFold2 sequence edits clear derived alignments but retain exact positional intent across saved reopen', async () => {
    await mount({ pred_method: 'esmfold2', complex_components: [{ id: 'A', type: 'protein', sequence: 'ACDE', modifications: [{ position: 0, ccd: 'MSE' }], msa: { sequences: ['ACDE'] }, msa_path: 'inputs/old.a3m' }], esmf_use_msa: false, run_frustrampnn: false });
    await act(async () => renderer.root.findAllByType('textarea').find(n => n.props.value === 'ACDE')!.props.onChange({ target: { value: 'ACDF' } }));
    const saved = await save(); await close(); await mount(saved);
    expect(preview().complex_components[0]).not.toHaveProperty('msa');
    expect(preview().complex_components[0]).not.toHaveProperty('msa_path');
    expect(preview().complex_components[0].modifications).toEqual([{ position: 0, ccd: 'MSE' }]);
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
});

it('Boltz native dialect controls preserve copy order, chemistry, constraints, affinity and seed zero across switch/save/clone', async () => {
    const constraints = [{ bond: { atom1: ['P2', 2, 'CA'], atom2: ['L', 1, 'C1'] } }, { contact: { token1: ['P1', 3], token2: ['L', 'C2'], max_distance: 6, force: false } }, { pocket: { binder: 'L', contacts: [['P1', 4]], max_distance: 8 } }];
    const components = [{ id: 'P', type: 'protein', sequence: 'ACDE', count: 2, copy_ids: ['P1', 'P2'], cyclic: true, modifications: [{ position: 2, ccd: 'CSO' }], msa: 'empty' }, { id: 'L', type: 'ligand', ccd: ['ATP'], name: 'ligand' }];
    await mount({ pred_method: 'boltz', boltz_use_msa: false, protenix_use_msa: false, run_frustrampnn: false, complex_components: components, constraints, properties: [{ affinity: { binder: 'L' } }] });
    await change('Boltz seed', '0'); await change('Boltz entity 1 copy IDs', 'P2,P1'); await change('Boltz entity 1 name', 'display rename');
    await change('Boltz entity 1 modification 1 CCD', 'SEP');
    await switchPredictor('protenix'); const saved = await save(); await close(); await mount(saved); await switchPredictor('boltz-2');
    expect(preview()).toMatchObject({ boltz_seed: 0, constraints, properties: [{ affinity: { binder: 'L' } }] });
    expect(preview().complex_components[0]).toMatchObject({ count: 2, copy_ids: ['P2', 'P1'], cyclic: true, modifications: [{ position: 2, ccd: 'SEP' }], msa: 'empty' });
    await click('Launch Prediction'); expect(posts).toHaveLength(1); const request = posts[0];
    expect(request.params.constraints).toEqual(constraints);
    await close(); localStorage.setItem('clonedJobData', JSON.stringify(request)); await mount(undefined, true);
    await click('Launch Prediction'); expect(posts[0].params).toMatchObject({ boltz_seed: 0, constraints, properties: [{ affinity: { binder: 'L' } }] });
    expect(posts[0].params.complex_components).toEqual(request.params.complex_components);
});
const switchPredictor = async (value: string) => act(async () => renderer.root.findAllByType('button').find(node => text(node).toLowerCase().startsWith(value))!.props.onClick());
const save = async () => { await click('Save Template'); return JSON.parse(JSON.stringify(templates.at(-1).currentParams)); };
it('Boltz rename preserves counted annotated entities through actual save, reopen and HTTP submission', async () => {
    await mount({ ...fixture, pred_method: 'boltz', boltz_use_msa: false });
    const name = renderer.root.findAllByType('input').find(n => n.props.value === 'predicted')!;
    await act(async () => name.props.onChange({ target: { value: 'renamed display' } }));
    const saved = await save();
    expect(saved.complex_components).toHaveLength(5);
    expect(saved.complex_components[1]).toMatchObject({ ...peptide, name: 'renamed display' });
    expect(saved.complex_components[0]).toMatchObject(fixture.complex_components[0]);
    expect(saved.complex_components[4]).toMatchObject({ count: 20, ion: 'ZN', element: 'ZN' });
    await close(); await mount(saved); await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(posts[0].params.complex_components).toEqual(saved.complex_components);
    expect(posts[0].params).not.toHaveProperty('protenix_seeds');
});
it('cross-model delete/add and inactive native settings survive save/reopen without resurrecting molecules', async () => {
    await mount({ ...fixture, boltz_use_msa: false, protenix_seeds: '7,19', protenix_n_sample: 3, protenix_use_template: false });
    await switchPredictor('boltz-2');
    await act(async () => renderer.root.findByType(LigandSelector).props.setLigands((rows: any[]) => [...rows.filter(row => row.id !== 'R'), { id: 'NEW', type: 'protein', name: 'new', sequence: 'ACDE' }]));
    const saved = await save();
    expect(saved).toMatchObject({ protenix_seeds: '7,19', protenix_n_sample: 3, protenix_use_template: false, protenix_use_msa: false, covalent_bonds: fixture.covalent_bonds, constraint: fixture.constraint });
    await close(); await mount(saved); await switchPredictor('protenix');
    expect(preview().complex_components.map((c: any) => c.id)).toEqual(['L', 'P', 'D', 'I', 'NEW']);
    expect(preview()).toMatchObject({ protenix_seeds: '7,19', protenix_n_sample: 3, protenix_use_template: false });
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params.complex_components).toEqual(preview().complex_components);
});
it('sequence replacement in Boltz invalidates alignments/templates and keeps positional intent visibly reviewable on reopen', async () => {
    await mount({ ...fixture, pred_method: 'boltz', boltz_use_msa: false });
    const input = renderer.root.findAllByType('textarea').find(n => n.props.value === peptide.sequence)!;
    await act(async () => input.props.onChange({ target: { value: 'ACDEFGHIK' } }));
    const saved = await save(); const component = saved.complex_components[1];
    for (const key of ['pairedMsa', 'unpairedMsa', 'pairedMsaPath', 'unpairedMsaPath', 'msa', 'templatesPath']) expect(component).not.toHaveProperty(key);
    expect(component.modifications).toEqual(peptide.modifications);
    expect(saved.covalent_bonds).toEqual(fixture.covalent_bonds);
    expect(text(renderer.root)).toContain('Retained modifications and bond/contact/pocket residue positions are not remapped');
    await close(); await mount(saved);
    expect(text(renderer.root)).toContain('Retained modifications and bond/contact/pocket residue positions are not remapped');
    await click('Launch Prediction'); expect(posts).toHaveLength(1); // no new frontend gate
    await click('Keep reviewed positions on this sequence');
    expect(text(renderer.root)).not.toContain('Retained modifications and bond/contact/pocket residue positions are not remapped');
    expect((await save()).complex_components[1].modifications).toEqual(peptide.modifications);
});
it('actual Boltz clone parent retains annotation counts and display edits in captured transport', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'boltz clone', model_id: 'boltz2', mode: 'complex', params: { ...fixture, pred_method: 'boltz', boltz_use_msa: false } }));
    await mount(undefined, true); await click('Launch Prediction');
    expect(posts).toHaveLength(1); expect(posts[0].params.complex_components[1]).toMatchObject(peptide);
    expect(posts[0].params.complex_components[4]).toMatchObject({ count: 20, ion: 'ZN' });
});
it('ensemble and ESMFold2 batch keep native settings through saved inactive selection and captured requests', async () => {
    await mount({ ...fixture, pred_method: 'boltz_protenix', boltz_use_msa: false });
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params.covalent_bonds).toEqual(fixture.covalent_bonds);
    await close();
    await mount({ pred_method: 'esmfold2', sequence: 'ACDE', primary_chain_id: 'B', complex_components: [{ id: 'A', type: 'protein', sequence: 'GGGG' }, { id: 'B', type: 'protein', sequence: 'ACDE' }], esmf_use_msa: false, seed: 0, num_loops: 7, num_sampling_steps: 125, num_diffusion_samples: 3, model_variant: 'full', run_frustrampnn: false, sequence_batch_component_id: 'B', sequence_batch_input: '>variant1\nAAAA\n>variant2\nCCCC' });
    await switchPredictor('boltz-2'); const saved = await save(); await close(); await mount(saved); await switchPredictor('esmfold');
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params).toMatchObject({ seed: 0, num_loops: 7, num_sampling_steps: 125, num_diffusion_samples: 3, sequence_batch_component_id: 'B', sequence_batch_entries: [{ name: 'variant1', sequence: 'AAAA' }, { name: 'variant2', sequence: 'CCCC' }] });
});

it('retains Boltz native modification, cyclic, MSA and CCD-list dialects without Protenix conversion', async () => {
    const components = [{ id: 'A', type: 'protein', name: 'native Boltz', sequence: 'ACG', count: 2,
        copy_ids: ['A1', 'A2'], modifications: [{ position: 2, ccd: 'CSO' }], cyclic: true, msa: 'empty' },
        { id: 'L', type: 'ligand', ccd: ['ATP'], name: 'native CCD list' }];
    await mount({ pred_method: 'boltz', sequence: 'ACG', primary_chain_id: 'A', boltz_use_msa: false, complex_components: components, run_frustrampnn: false });
    const saved = await save();
    expect(saved.complex_components).toEqual(components);
    await close(); await mount(saved); await click('Launch Prediction');
    expect(posts).toHaveLength(1); expect(posts[0].params.complex_components).toEqual(components);
});

it('Boltz native controls retain independent task chemistry and zero seed through inactive save and actual submit', async () => {
    const constraints = [{ bond: { atom1: ['A', 2, 'SG'], atom2: ['B', 2, 'SG'] } },
        { contact: { token1: ['A', 1], token2: ['B', 1], max_distance: 8, force: false } },
        { pocket: { binder: 'L', contacts: [['A', 2]], max_distance: 6, force: true } }];
    const properties = [{ affinity: { binder: 'L' } }];
    await mount({ pred_method: 'boltz', sequence: 'ACG', primary_chain_id: 'A', boltz_use_msa: false, protenix_use_msa: false, run_frustrampnn: false,
        complex_components: [{ id: 'A', type: 'protein', name: 'native', sequence: 'ACG', modifications: [{ position: 2, ccd: 'CSO' }] }, { id: 'B', type: 'protein', sequence: 'ACG' }, { id: 'L', type: 'ligand', ccd: 'ATP' }], constraints, properties });
    await change('Boltz seed', '0'); await change('Boltz entity 1 name', 'renamed');
    await change('Boltz entity 1 count', '17'); await change('Boltz entity 1 modification 1 position', '3');
    await change('Boltz constraint 2 max distance', '9');
    await switchPredictor('protenix'); const saved = await save();
    expect(saved.boltz_seed).toBe(0); expect(saved.constraints[1].contact.max_distance).toBe(9);
    await close(); await mount(saved); await switchPredictor('boltz-2');
    expect(preview().complex_components[0]).toMatchObject({ count: 17, name: 'renamed', modifications: [{ position: 3, ccd: 'CSO' }] });
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params).toMatchObject({ boltz_seed: 0, constraints: saved.constraints, properties });
    expect(posts[0].params).not.toHaveProperty('esmfold2_editor_settings');
    expect(posts[0].params).not.toHaveProperty('sequence_annotation_origins');
});
