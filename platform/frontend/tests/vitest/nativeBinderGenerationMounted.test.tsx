import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ models: [] as any[], inventory: {} as Record<string, any>, project: null as any, template: null as any,
    documents: {} as Record<string, string>,
    submit: vi.fn(async (_request: unknown) => ({ data: {} })), save: vi.fn(), upload: vi.fn(async (_directory: string, file: File) => ({ data: { path: `inputs/${file.name}` } })),
    presets: [{ id: 'target', name: 'Fixture target', path: 'inputs/target.pdb', description: 'Non-science parser fixture', category: 'test' }, { id: 'framework', name: 'Fixture framework', path: 'inputs/framework.pdb', description: 'Non-science parser fixture', category: 'test' }],
}));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    submitJob: mocks.submit, uploadFile: mocks.upload,
    materializeExactStructure: vi.fn(async () => { throw new Error('Fixture checked PDB conversion rejected: empty atom-site document'); }),
    materializeStructureTarget: vi.fn(async (source: { path?: string; file?: File }, destination: string) => source.path || (await mocks.upload(destination, source.file!)).data.path),
    fetchModels: vi.fn(async () => ({ data: mocks.models })), fetchTemplates: vi.fn(async () => ({ data: [] })),
    fetchInputPresets: vi.fn(async (type: string) => ({ data: type === 'pdb' ? mocks.presets : [] })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })), fetchTemplateById: vi.fn(async () => ({ data: null })),
    fetchModel: vi.fn(async (id: string) => ({ data: mocks.models.find(model => model.id === id) })),
}));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(),
    getProjectWorkflowSetup: vi.fn(async () => mocks.project), saveProjectWorkflowSetupDraft: mocks.save,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: (props: any) => { mocks.template = props; return null; } }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/MutagenesisTemplate', () => ({ MutagenesisTemplate: () => null }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => <output data-legacy>Legacy authoring</output> }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/ProteinModificationTemplate', () => ({ ProteinModificationTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
// Only the GPU/WebGL runtime is replaced. The actual acquisition, parser, source
// component, sequence selector, full-view toggle and native parent are mounted.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => <output data-viewer={props.structureDocumentId} data-format={props.format}>{props.structureData}</output> }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { ppiflowHotspotsFromSelection } from '../../src/lib/nativeBinderAuthoring';
import { parseBC2Document } from '../../src/lib/bindcraft2StructureInputs';

const pdb = 'ATOM      1  CA  ALA a  42       1.000   2.000   3.000  1.00 20.00           C  \nEND\n';
const framework = 'ATOM      1  CA  GLY H  20       4.000   5.000   6.000  1.00 20.00           C  \nEND\n';
const base = [
    { name: 'target_pdb', type: 'file' }, { name: 'specified_hotspots', type: 'string', default: null },
    { name: 'samples_per_target', type: 'integer', minimum: 1, default: 100 },
    { name: 'self_condition', type: 'boolean', default: true }, { name: 'min_t', type: 'number', default: 0.01 },
];
const protein = [...base, { name: 'target_chain', type: 'string', default: 'R' }, { name: 'binder_chain', type: 'string', default: null }, { name: 'dataset_seed', type: 'integer', default: 123 }];
const antibody = [...base, { name: 'framework_pdb', type: 'file', required: true }, { name: 'antigen_chain', type: 'string' }, { name: 'heavy_chain', type: 'string' }, { name: 'cdr_length', type: 'string', default: 'CDRH1,5-12,CDRH2,4-17,CDRH3,5-26,CDRL1,5-12,CDRL2,3-10,CDRL3,4-13' }];
const boltz = [{ name: 'target_pdb', type: 'file' }, { name: 'target_chains', type: 'string' }, { name: 'target_binding_positions', type: 'string' }, { name: 'scaffold_path', type: 'file' }, { name: 'scaffold_chain', type: 'string' }, { name: 'scaffold_design_ranges', type: 'string' }, { name: 'binder_sequence', type: 'string' }, { name: 'num_designs', type: 'integer', default: 10 }, { name: 'protocol', type: 'string', enum: ['protein-anything', 'peptide-anything'] }, { name: 'alpha', type: 'number', default: 0.1 }];
// Optional exported real server contracts for cross-owner verification. The
// ordinary focused suite remains deterministic without a Python environment.
const exportedInventory = process.env.BMS_NATIVE_GENERATION_INVENTORY ? JSON.parse(readFileSync(process.env.BMS_NATIVE_GENERATION_INVENTORY, 'utf8')) : {};
let root: Root | undefined; let client: QueryClient;
const button = (text: string, within: ParentNode = document) => [...within.querySelectorAll('button')].find(button => button.textContent?.trim() === text)!;
async function click(text: string, within: ParentNode = document) { const target = button(text, within); expect(target, text).toBeTruthy(); await act(async () => target.click()); }
async function edit(label: string, value: string) { const input = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; expect(input, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function mode(value: string) { const select = [...document.querySelectorAll('select')].find(select => [...select.options].some(option => option.value === 'protein_binder'))!; await act(async () => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function mount(model: string, mode: string, params: Record<string, unknown> = {}, project = false) {
    if (!project) localStorage.setItem('clonedJobData', JSON.stringify({ model_id: model, mode, name: 'Native request', params }));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[project ? '/submit?template=antibody_denovo&setup_context_id=setup&project_id=project' : '/submit']}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
beforeEach(() => {
    if (!File.prototype.text) Object.defineProperty(File.prototype, 'text', { configurable: true, value: function(this: File) { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    mocks.documents = {};
    mocks.upload.mockImplementation(async (_directory, file) => { const path = `inputs/${file.name}`; mocks.documents[path] = await file.text(); return { data: { path } }; });
    mocks.inventory = {};
    mocks.models = [
        { id: 'ppiflow', name: 'PPIFlow', params: protein, modes: ['protein_binder', 'antibody_binder', 'nanobody_binder'].map(id => ({ id, name: id, params: (id === 'protein_binder' ? protein : id === 'antibody_binder' ? [...antibody, { name: 'light_chain' }] : antibody).map(p => p.name) })) },
        { id: 'boltzgen', name: 'BoltzGen', params: boltz, modes: ['protein_binder', 'peptide_binder'].map(id => ({ id, name: id, params: boltz.map(p => p.name) })) },
    ];
    for (const model of mocks.models) for (const mode of model.modes) mocks.inventory[`${model.id}:${mode.id}`] = { schema_version: 'fixture', mode: mode.id, parameters: model.id === 'boltzgen' ? boltz : mode.id === 'protein_binder' ? protein : mode.id === 'antibody_binder' ? [...antibody, { name: 'light_chain', type: 'string', default: null }] : antibody, profile: { fixture: 'checkpoint owned' }, native_behavior: ['no native sampling in this test'] };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        const match = /^\/api\/models\/(\w+)\/generation-settings\?mode=(\w+)$/.exec(url);
        if (match) return { ok: true, json: async () => mocks.inventory[`${match[1]}:${match[2]}`] };
        if (url.startsWith('/api/files/download/')) return { ok: true, text: async () => mocks.documents[decodeURIComponent(url.slice('/api/files/download/'.length))] ?? (url.includes('framework') ? framework : pdb) };
        throw new Error(`Unexpected fetch: ${url}`);
    }));
    mocks.project = null;
});
afterEach(async () => { await unmount(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it.each(['protein_binder', 'antibody_binder', 'nanobody_binder'])('mounted PPIFlow %s emits only its native contract and preserves false/zero/null', async modeName => {
    await mount('ppiflow', modeName, { target_pdb: 'inputs/target.pdb', target_chain: 'a', antigen_chain: 'a', framework_pdb: 'inputs/framework.pdb', heavy_chain: 'H', light_chain: 'L', binder_chain: 'B', self_condition: false, min_t: 0, specified_hotspots: null, legacy_seed: 'must-not-submit' });
    expect(document.body.textContent).toContain('PPIFlow initial generation');
    await vi.waitFor(() => expect(document.body.textContent).toContain('Open full viewer'));
    await click('Generation'); await edit('samples_per_target', '2');
    await click('Expert'); expect(document.querySelector<HTMLInputElement>('[aria-label="self_condition"]')!.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>('[aria-label="min_t"]')!.value).toBe('0');
    await click('Launch Experiment');
    const request = mocks.submit.mock.calls[0][0] as any;
    expect(request).toMatchObject({ model_id: 'ppiflow', mode: modeName, params: { target_pdb: 'inputs/target.pdb', samples_per_target: 2, self_condition: false, min_t: 0, specified_hotspots: null } });
    expect(request.params).not.toHaveProperty('legacy_seed');
    expect(request.params).not.toHaveProperty('seed_pdb');
    if (modeName !== 'protein_binder') expect(request.params).toMatchObject({ framework_pdb: 'inputs/framework.pdb', heavy_chain: 'H', antigen_chain: 'a' });
    if (modeName === 'nanobody_binder') expect(request.params).not.toHaveProperty('light_chain');
});

it('actual target and framework preset acquisition remains independent and maps only verified PDB author hotspots', async () => {
    await mount('ppiflow', 'antibody_binder', { antigen_chain: 'a', heavy_chain: 'H', light_chain: 'L' });
    const target = document.querySelector('[aria-label="Antigen source"]')!;
    const scaffold = document.querySelector('[aria-label="Framework source"]')!;
    await click('Presets', target); await settle(); await click('Fixture target', target); await settle();
    await click('Presets', scaffold); await settle(); await click('Fixture framework', scaffold); await settle();
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('inputs/target.pdb');
    expect(document.querySelector<HTMLInputElement>('[aria-label="framework_pdb"]')!.value).toBe('inputs/framework.pdb');
    await click('Inspect source', target); expect(target.textContent).toContain('Close full viewer');
    await click('Close full viewer', target);
    await click('Inspect source', target); expect(target.textContent).toContain('Close full viewer');
    const residue = target.querySelector<HTMLElement>('[title*="ALA"]')!;
    expect(residue).toBeTruthy(); await act(async () => residue.click());
    await click('Use inspected residues as native PPIFlow hotspots', target);
    await click('Design'); expect(document.querySelector<HTMLInputElement>('[aria-label="specified_hotspots"]')!.value).toBe('a42');
    await edit('CDRH1 minimum', '0');
    await click('Launch Experiment');
    expect((mocks.submit.mock.calls[0][0] as any).params).toMatchObject({ target_pdb: 'inputs/target.pdb', framework_pdb: 'inputs/framework.pdb', specified_hotspots: 'a42', cdr_length: 'CDRH1,0-12,CDRH2,4-17,CDRH3,5-26,CDRL1,5-12,CDRL2,3-10,CDRL3,4-13' });
});

it.each(['protein_binder', 'peptide_binder'])('BoltzGen %s submits independent source roles and exact native chain-local masks', async modeName => {
    const values = { target_pdb: 'inputs/target.pdb', target_chains: 'a', target_binding_positions: 'a:2-5', scaffold_path: 'inputs/framework.pdb', scaffold_chain: 'H', scaffold_design_ranges: '2..5,9', binder_sequence: '', alpha: 0, protocol: modeName === 'protein_binder' ? 'protein-anything' : 'peptide-anything' };
    await mount('boltzgen', modeName, values);
    expect(document.body.textContent).toContain('BoltzGen generation');
    expect(document.querySelectorAll('[data-viewer]').length).toBe(2);
    expect(document.body.textContent).toContain('does not provide a verified native position map');
    await click('Launch Experiment');
    expect(mocks.submit).toHaveBeenCalledWith({ name: 'Native request', model_id: 'boltzgen', mode: modeName, params: { ...values, num_designs: 10 } }, { launchContext: false });
});

it('Project save/reopen retains mode drafts, native null, sources and zero without metadata entering submission', async () => {
    const initial = { model_id: 'ppiflow', mode: 'protein_binder', job_name: 'Project native', target_pdb: 'inputs/target.pdb', target_chain: 'a', binder_chain: 'B', self_condition: false, dataset_seed: 0, specified_hotspots: null };
    mocks.project = { project_id: 'project', setup_context_id: 'setup', generation: 1, draft: initial, project_label: 'Project', experiment_label: 'Experiment', workflow_label: 'Binder', state: 'open', return_uri: '/projects/project', field_errors: {} };
    mocks.save.mockImplementation(async (_project: string, _setup: string, request: any) => { mocks.project = { ...mocks.project, generation: 2, draft: request.draft }; return mocks.project; });
    await mount('ppiflow', 'protein_binder', {}, true);
    await mode('nanobody_binder'); await edit('framework_pdb', 'inputs/framework.pdb'); await edit('Native binder job name', 'Nanobody saved');
    await mode('protein_binder'); await click('Save draft'); await settle();
    const saved = mocks.save.mock.calls[0][2].draft;
    expect(saved).toMatchObject(initial);
    expect(saved.binder_native_drafts['ppiflow:nanobody_binder']).toMatchObject({ framework_pdb: 'inputs/framework.pdb', job_name: 'Nanobody saved' });
    await unmount(); await mount('ppiflow', 'protein_binder', {}, true);
    expect(document.querySelector('[data-legacy]')).toBeNull();
    await click('Generation'); expect(document.querySelector<HTMLInputElement>('[aria-label="dataset_seed"]')!.value).toBe('0');
    await mode('nanobody_binder');
    expect(document.querySelector<HTMLInputElement>('[aria-label="framework_pdb"]')!.value).toBe('inputs/framework.pdb');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('Nanobody saved');
    await click('Save draft');
    expect(mocks.save.mock.calls.at(-1)![2].draft.binder_native_drafts['ppiflow:nanobody_binder']).toMatchObject({ framework_pdb: 'inputs/framework.pdb', job_name: 'Nanobody saved' });
    // Full Project prepare/reserve/submit is exercised by the real-parent
    // binderProjectAuthoringCompletionMounted suite, not a bypassed leaf submit.
});

it('UserTemplate save and load use the same native draft and preserve explicit cleared/false/null values', async () => {
    await mount('ppiflow', 'protein_binder', { target_pdb: '', self_condition: false, min_t: 0, specified_hotspots: null });
    await click('Template Manager');
    const props = mocks.template;
    expect(props.currentParams).toMatchObject({ target_pdb: '', self_condition: false, min_t: 0, specified_hotspots: null, native_generation_authoring: true });
    await act(async () => props.onSelect({ model_id: 'ppiflow', mode: 'protein_binder', name: 'Saved', params: props.currentParams }));
    await settle(); await click('Launch Experiment');
    expect((mocks.submit.mock.calls[0][0] as any).params).toMatchObject({ target_pdb: '', self_condition: false, min_t: 0, specified_hotspots: null });
});

it('canonical BoltzGen discovery preserves saved aliases and submits native-only fields absent from the generic catalog', async () => {
    const inventory = mocks.inventory['boltzgen:protein_binder'];
    inventory.parameters = boltz.map(field => ({ ...field, name: field.name === 'target_pdb' ? 'boltzgen_target_pdb_path' : `boltzgen_${field.name}`, aliases: [field.name] }));
    inventory.parameters.push({ name: 'boltzgen_skip_inverse_folding', type: 'boolean', default: true, aliases: ['skip_inverse_folding'] });
    await mount('boltzgen', 'protein_binder', { target_pdb: 'inputs/target.pdb', alpha: 0, skip_inverse_folding: false });
    expect(document.querySelector('[aria-label="Target source"]')).not.toBeNull();
    await click('Native selection'); await edit('alpha', '0.2');
    await click('Launch Experiment');
    const params = (mocks.submit.mock.calls[0][0] as any).params;
    expect(params).toMatchObject({ target_pdb: 'inputs/target.pdb', alpha: 0.2, skip_inverse_folding: false, boltzgen_num_designs: 10 });
    expect(params).not.toHaveProperty('boltzgen_target_pdb_path');
    expect(params).not.toHaveProperty('boltzgen_alpha');
    expect(params).not.toHaveProperty('boltzgen_skip_inverse_folding');
});

it('exact nonprimary PDB conformation is materialized and submitted, never the primary document', async () => {
    const second = pdb.replace('1.000', '9.000');
    mocks.documents['inputs/models.pdb'] = `MODEL        1\n${pdb.replace('END\n', '')}ENDMDL\nMODEL        2\n${second.replace('END\n', '')}ENDMDL\nEND\n`;
    await mount('ppiflow', 'protein_binder', { target_source: { name: 'models.pdb', path: 'inputs/models.pdb', document: { artifact_id: 'exact-artifact', target_state: 'alternate' } }, target_model_number: 2, target_chain: 'a' });
    await settle();
    expect(mocks.upload).toHaveBeenCalledTimes(1);
    const uploaded = mocks.upload.mock.calls[0][1];
    expect(await uploaded.text()).toContain('9.000');
    expect(await uploaded.text()).not.toContain('1.000');
    await click('Launch Experiment');
    const params = (mocks.submit.mock.calls[0][0] as any).params;
    expect(params.target_pdb).toBe('inputs/model-2.pdb');
    expect(params.target_pdb).not.toBe('inputs/models.pdb');
});

it('PPIFlow CIF acquisition retains exact source context without renaming bytes or reusing the old primary PDB', async () => {
    mocks.documents['inputs/source.cif'] = 'data_source\n#\n'; // Empty structural document: transport-only fixture.
    await mount('ppiflow', 'protein_binder', { target_source: { name: 'source.cif', path: 'inputs/source.cif', document: { artifact_id: 'cif-artifact', target_state: 'state-2' } } });
    await settle();
    await vi.waitFor(async () => {
        await settle();
        expect(document.body.textContent).toContain('checked PDB conversion');
    });
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('');
    await click('Template Manager');
    expect(mocks.template.currentParams.target_pdb_source_reference).toMatchObject({ path: 'inputs/source.cif', document: { artifact_id: 'cif-artifact', target_state: 'state-2' } });
    await click('Launch Experiment');
    expect((mocks.submit.mock.calls[0][0] as any).params.target_pdb).toBe('');
    expect(mocks.upload).not.toHaveBeenCalled();
});

it('BoltzGen consumes native CIF without relabeling and preserves source identity on reopen', async () => {
    // Transport fixture: no fabricated coordinates or native inference claims.
    mocks.documents['inputs/source.cif'] = 'data_source\n#\n';
    await mount('boltzgen', 'protein_binder', { target_source: { name: 'native.cif', path: 'inputs/source.cif', document: { artifact_id: 'source-artifact', target_state: 'native-state' } }, target_chains: 'AA', target_binding_positions: 'AA:1' });
    await settle();
    expect(mocks.upload).not.toHaveBeenCalled();
    await click('Template Manager');
    const draft = mocks.template.currentParams;
    expect(draft.target_pdb_source_reference).toMatchObject({ path: 'inputs/source.cif', document: { artifact_id: 'source-artifact', target_state: 'native-state' } });
    await unmount(); await mount('boltzgen', 'protein_binder', draft);
    await click('Template Manager');
    expect(mocks.template.currentParams.target_pdb_source_reference).toEqual(draft.target_pdb_source_reference);
    await click('Launch Experiment');
    expect((mocks.submit.mock.calls[0][0] as any).params).toMatchObject({ target_pdb: 'inputs/source.cif', target_chains: 'AA', target_binding_positions: 'AA:1' });
    expect((mocks.submit.mock.calls[0][0] as any).params).not.toHaveProperty('target_pdb_source_reference');
    expect(mocks.upload).not.toHaveBeenCalled();
});

it('missing exact model retains nonlossy source reference without a primary fallback', async () => {
    await mount('ppiflow', 'protein_binder', { target_source: { name: 'one-model.pdb', path: 'inputs/target.pdb', document: { artifact_id: 'exact-artifact', target_state: 'alternate' } }, target_model_number: 9 });
    expect(document.body.textContent).toContain('Source model 9 is not available');
    await click('Template Manager');
    expect(mocks.template.currentParams).toMatchObject({ target_source: { document: { artifact_id: 'exact-artifact', target_state: 'alternate' } }, target_model_number: 9 });
    expect(mocks.template.currentParams).not.toHaveProperty('target_pdb');
    await click('Launch Experiment');
    expect((mocks.submit.mock.calls[0][0] as any).params).not.toHaveProperty('target_pdb');
    expect(mocks.upload).not.toHaveBeenCalled();
});

it('hotspot conversion refuses unverified document, chain, insertion and conformation mappings without guessing', async () => {
    const doc = await parseBC2Document(pdb, 'target.pdb');
    const ref = { documentId: 'exact', authAsymId: 'a', authSeqId: 42 };
    expect(ppiflowHotspotsFromSelection(doc, 'exact', [ref], 'a', 'protein_binder')).toBe('a42');
    expect(() => ppiflowHotspotsFromSelection(doc, 'other', [ref], 'a', 'protein_binder')).toThrow(/document/);
    expect(() => ppiflowHotspotsFromSelection(doc, 'exact', [ref], 'A', 'protein_binder')).toThrow(/chain/);
    expect(() => ppiflowHotspotsFromSelection({ ...doc, format: 'cif' }, 'exact', [ref], 'a', 'protein_binder')).toThrow(/PDB/);
    const insertion = { ...doc, models: [{ ...doc.models[0], chains: [{ ...doc.models[0].chains[0], residues: [{ ...doc.models[0].chains[0].residues[0], iCode: 'A' }] }] }] };
    expect(() => ppiflowHotspotsFromSelection(insertion, 'exact', [{ ...ref, insertionCode: 'A' }], 'a', 'protein_binder')).toThrow(/insertion/);
    expect(ppiflowHotspotsFromSelection(insertion, 'exact', [{ ...ref, insertionCode: 'A' }], 'a', 'antibody_binder')).toBe('a42A');
});

for (const [identity, inventory] of Object.entries(exportedInventory) as Array<[string, any]>) {
    it(`real exported inventory ${identity}: every applicable setting mounts and exact native defaults submit`, async () => {
        const [model, modeName] = identity.split(':');
        mocks.inventory[identity] = inventory;
        mocks.models = [{ id: model, name: model, params: inventory.parameters, modes: [{ id: modeName, name: modeName, params: inventory.parameters.map((parameter: any) => parameter.name) }] }];
        await mount(model, modeName, { native_generation_authoring: true });
        for (const parameter of inventory.parameters) {
            expect(document.querySelector(`[data-native-setting="${parameter.name}"], [aria-label="${parameter.name}"]`), parameter.name).not.toBeNull();
        }
        await click('Launch Experiment');
        expect(mocks.submit).toHaveBeenCalledWith({ name: 'Native request', model_id: model, mode: modeName, params: Object.fromEntries(inventory.parameters.filter((parameter: any) => Object.hasOwn(parameter, 'default')).map((parameter: any) => [parameter.name, parameter.default])) }, { launchContext: false });
    });
}
