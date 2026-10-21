import React from 'react';
import { writeFileSync, mkdirSync } from 'node:fs';
import { LigandSelector } from '../../src/components/LigandSelector';
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
it('captures an illustrative retatrutide-backbone native request without nonexistent managed paths', async () => {
    const sequence = 'YAQGTFTSDYSILLDKKAQAAFIEYLLEGG PSSGAPPPS'.replace(/ /g, '');
    const component = { id: 'P', type: 'peptide', sequence, count: 1, copy_ids: ['P'],
        modifications: [{ ptmPosition: 2, ptmType: 'CCD_AIB' }, { ptmPosition: 13, ptmType: 'CCD_2ML' }, { ptmPosition: 20, ptmType: 'CCD_AIB' }], pairedMsa: '', unpairedMsa: '' };
    await mount({ pred_method: 'protenix', sequence, primary_chain_id: 'P', protenix_use_msa: false, protenix_use_template: false, run_frustrampnn: false, complex_components: [component], covalent_bonds: [] });
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params.complex_components[0]).toMatchObject(component);
    if (process.env.BMS_FRONTEND_EVIDENCE) { mkdirSync(process.env.BMS_FRONTEND_EVIDENCE, { recursive: true }); writeFileSync(`${process.env.BMS_FRONTEND_EVIDENCE}/illustrative-retatrutide-backbone-request.json`, JSON.stringify(posts[0], null, 2)); }
});

const removeEntity = async (id: string) => act(async () => renderer.root.findByType(LigandSelector).props.setLigands((rows: any[]) => rows.filter(row => row.id !== id)));
const switchPredictor = async (value: string) => act(async () => renderer.root.findAllByType('button').find(node => text(node).toLowerCase().startsWith(value))!.props.onClick());

it('remaps native/legacy bonds and contacts/pocket after removing preceding unreferenced entity, including exact clone transport', async () => {
    const value = { ...fixture, protenix_use_template: true, complex_components: [{ id: 'unused', type: 'ion', ion: 'NA' }, ...fixture.complex_components],
        covalent_bonds: fixture.covalent_bonds.map((b: any) => Object.fromEntries(Object.entries(b).map(([k,v]) => [k, k.includes('entity') ? Number(v) + 1 : v]))),
        constraint: { contact: [{ entity1: 3, entity2: 2, position1: 3, position2: 1 }], pocket: { binder_chain: { entity: 3 }, contact_residues: [{ entity: 2, position: 1 }] } } };
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'remap clone', model_id: 'protenix', mode: 'complex', source_structure: { path: 'inputs/source.cif' }, params: value }));
    await mount(undefined, true);
    await removeEntity('unused');
    expect(preview().covalent_bonds).toEqual(fixture.covalent_bonds);
    expect(preview().constraint).toEqual({ contact: [{ entity1: 2, entity2: 1, position1: 3, position2: 1 }], pocket: { binder_chain: { entity: 2 }, contact_residues: [{ entity: 1, position: 1 }] } });
    await change('Entity 1 ID', 'renamed-ligand'); await change('Entity 2 ID', 'renamed-peptide');
    expect(preview().complex_components.map((c: any) => c.id)).toEqual(['renamed-ligand', 'renamed-peptide', 'D', 'R', 'I']);
    await click('Launch Prediction');
    expect(posts).toHaveLength(1);
    expect(posts[0].params.covalent_bonds).toEqual(fixture.covalent_bonds);
    expect(posts[0].params.protenix_use_template).toBe(true);
    expect(posts[0].params.protenix_target_geometry_mode).toBe('flexible');
    expect(posts[0].params.complex_components[1].templatesPath).toBe(peptide.templatesPath);
    if (process.env.BMS_FRONTEND_EVIDENCE) { mkdirSync(process.env.BMS_FRONTEND_EVIDENCE, { recursive: true }); writeFileSync(`${process.env.BMS_FRONTEND_EVIDENCE}/remapped-clone-request.json`, JSON.stringify(posts[0], null, 2)); }
});

it('removed referenced entities remain visibly unresolved across add/save/reopen until endpoints are repaired', async () => {
    await mount(); await removeEntity('L');
    expect(preview().covalent_bonds[0]).toMatchObject({ left_entity: 1, right_entity: 0 });
    expect(preview().covalent_bonds[1]).toMatchObject({ entity1: 1, entity2: 2 });
    expect(preview().constraint.contact[0].entity2).toBe(0);
    expect(preview().constraint.pocket.contact_residues[0].entity).toBe(0);
    expect(renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ')).toContain('unresolved entity endpoint');
    await act(async () => renderer.root.findByType(LigandSelector).props.setLigands((rows: any[]) => [...rows, { id: 'L', name: 'replacement', type: 'ligand', smiles: 'CCO' }]));
    expect(preview().covalent_bonds[0].right_entity).toBe(0);
    await click('Launch Prediction'); expect(posts).toHaveLength(0);
    await click('Save Template'); const saved = JSON.parse(JSON.stringify(templates.at(-1).currentParams));
    await close(); await mount(saved);
    expect(preview().covalent_bonds[0].right_entity).toBe(0);
    await change('Bond 1 right entity', '5'); await change('Contact 1 entity2', '5'); await change('Pocket residue 1 entity', '5');
    await change('Entity 5 ID', 'new-id');
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
    expect(posts[0].params.covalent_bonds[0].right_entity).toBe(5);
});

it('leaves removed legacy-alias and pocket-binder endpoints unresolved rather than selecting the next molecule', async () => {
    await mount({ ...fixture, constraint: { pocket: { binder_chain: { entity: 3, copy: 1 }, contact_residues: [{ entity: 2, position: 3 }] } } });
    await removeEntity('D');
    expect(preview().covalent_bonds[1]).toMatchObject({ entity1: 2, entity2: 0 });
    expect(preview().constraint.pocket.binder_chain.entity).toBe(0);
    await click('Launch Prediction'); expect(posts).toHaveLength(0);
    await click('Remove bond 2'); await click('Remove pocket');
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
});

it('duplicate IDs do not multiply molecules or edit both rows and require explicit unique IDs', async () => {
    await mount({ ...fixture, complex_components: fixture.complex_components.map((c, i) => i === 2 ? { ...c, id: 'L' } : c) });
    expect(preview().complex_components).toHaveLength(5);
    await click('Launch Prediction'); expect(posts).toHaveLength(0);
    expect(renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ')).toContain('Duplicate component IDs');
    await change('Entity 3 ID', 'DNA');
    expect(preview().complex_components.map((c: any) => c.id)).toEqual(['L', 'P', 'DNA', 'R', 'I']);
    expect(preview().covalent_bonds).toEqual(fixture.covalent_bonds);
    await click('Launch Prediction'); expect(posts).toHaveLength(1);
});

it('switching from another predictor restores full saved native state and independent native template enable', async () => {
    await mount({ ...fixture, pred_method: 'boltz', boltz_use_msa: false, protenix_use_template: true });
    await switchPredictor('protenix');
    expect(preview().complex_components[0]).toMatchObject(fixture.complex_components[0]);
    expect(preview().complex_components[1]).toMatchObject(peptide);
    expect(preview().protenix_use_template).toBe(true);
    expect(preview().protenix_target_geometry_mode).toBe('flexible');
    await click('Save Template'); const saved = JSON.parse(JSON.stringify(templates.at(-1).currentParams));
    expect(saved.protenix_use_template).toBe(true);
    await close(); await mount(saved);
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Use native Protenix templates' }).props.onChange({ target: { checked: false } }));
    expect(preview().protenix_use_template).toBe(false);
    expect(preview().complex_components[1].templatesPath).toBe(peptide.templatesPath);
    await click('Launch Prediction'); expect(posts[0].params.protenix_use_template).toBe(false);
    expect(posts[0].params.complex_components[1].templatesPath).toBe(peptide.templatesPath);
});
