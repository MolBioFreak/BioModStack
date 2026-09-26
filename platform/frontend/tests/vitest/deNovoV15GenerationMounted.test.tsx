import React, { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_payload: any) => ({ data: {} })) }));
vi.mock('../../src/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../../src/lib/api')>(), submitJob: mocks.submit, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/ModelDocumentationLinks', () => ({ ModelDocumentationLinks: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ workflowRequest }: any) => <output data-execution>{JSON.stringify(workflowRequest)}</output> }));
// These doubles exercise parent snapshot ownership, not source/geometry behavior (covered by child suites).
function Child({ initialValues, onDraftChange, embedded, runDetails, kind, submissionModelId }: any) {
    const [value, setValue] = useState(initialValues.source ?? '');
    useEffect(() => { onDraftChange({ ...initialValues, source: value, enabled: false, amount: 0, selection: null }); }, [value, onDraftChange]);
    return <section data-child={kind} data-embedded={embedded} data-model={submissionModelId}><label>Source<input value={value} onChange={e => setValue(e.target.value)} /></label>{runDetails}<button>Child launch</button></section>;
}
vi.mock('../../src/components/ProteinLocalRedesignTemplate', () => ({ ProteinLocalRedesignTemplate: (props: any) => <Child {...props} kind="redesign" /> }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: (props: any) => <Child {...props} kind="shape" /> }));
import { ProteinModificationTemplate, type DeNovoNavigationState } from '../../src/components/ProteinModificationTemplate';
import { api } from '../../src/lib/api';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <output data-low-level-viewer data-url={props.structureUrl} /> }));

let root: Root;
let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); vi.clearAllMocks(); });
async function render(props: Partial<React.ComponentProps<typeof ProteinModificationTemplate>> = {}) {
    if (!root || !document.querySelector('#host')) {
        const host = document.createElement('div'); host.id = 'host'; document.body.append(host);
        root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    }
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate onBack={() => {}} {...props} /></MemoryRouter></QueryClientProvider>));
}
function control(name: string): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
    const direct = document.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${name}"]`);
    if (direct) return direct;
    const label = [...document.querySelectorAll('label')].find(el => [...el.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() === name);
    const input = label?.querySelector('input, select, textarea'); expect(input, name).toBeTruthy();
    return input as ReturnType<typeof control>;
}
async function edit(name: string, value: string) {
    const input = control(name);
    await act(async () => {
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!.call(input, value);
        input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function click(name: string) {
    const button = [...document.querySelectorAll('button')].find(el => el.textContent === name); expect(button, name).toBeTruthy();
    await act(async () => button!.click());
}
const latest = (spy: ReturnType<typeof vi.fn>) => spy.mock.calls.at(-1)![0];

it('defaults to one RFD3 editor and one run area after scientific inputs', async () => {
    await render({ runDetails: <div data-policy>Execution details</div> });
    expect(control('Engine').value).toBe('rfd3');
    expect([...document.querySelectorAll('nav[aria-label="Design task"] button')].map(el => el.textContent)).toEqual(['Generate', 'Redesign structure', 'Shape']);
    expect([...document.querySelectorAll('nav[aria-label="Generation sections"] button')].map(el => el.textContent)).toEqual(['Design', 'Sampling']);
    expect(document.querySelectorAll('[data-execution]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-policy]')).toHaveLength(1);
    expect([...document.querySelectorAll('h2')].filter(el => el.textContent === 'De Novo Protein Design')).toHaveLength(1);
    expect(document.body.textContent).not.toContain('backup');
    expect(control('Minimum length').compareDocumentPosition(document.querySelector('[data-execution]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await click('Generate candidates');
    expect(mocks.submit).toHaveBeenCalledOnce();
    expect(latest(mocks.submit)).toEqual({ name: 'protein_modification', model_id: 'protein_modification_experimental', mode: 'de_novo_design', params: { generator: 'rfd3', generation_mode: 'unconditional_monomer', min_length: 100, max_length: 200, num_designs: 8, seed: 0, dump_trajectories: false } });
});

it('exposes real conditional engines, retains independent goal drafts, and submits only active scientific values', async () => {
    const draft = vi.fn(); const navigation = vi.fn();
    await render({ onDraftChange: draft, onNavigationChange: navigation });
    await edit('Minimum length', '111');
    await edit('Goal', 'dna_conditioned');
    expect(control('Engine').value).toBe('disco');
    await edit('DNA/RNA sequence', 'ACGT');
    await edit('Inference seed count', '3');
    await edit('Goal', 'rna_conditioned');
    expect(control('DNA/RNA sequence').value).toBe('');
    await edit('DNA/RNA sequence', 'ACGU');
    await edit('Goal', 'dna_conditioned');
    expect(control('DNA/RNA sequence').value).toBe('ACGT');
    expect(control('Inference seed count').value).toBe('3');
    expect(navigation).toHaveBeenCalledTimes(3);
    expect(document.querySelectorAll('[data-execution]')).toHaveLength(1);
    await click('Generate candidates');
    expect(latest(mocks.submit).params).toMatchObject({ generator: 'disco', design_task: 'dna_conditioned', disco_na_sequence: 'ACGT', disco_num_inference_seeds: 3, disco_effort: 'fast' });
    expect(latest(mocks.submit).params).not.toHaveProperty('de_novo_drafts');
    expect(latest(mocks.submit).params).not.toHaveProperty('min_length');
    expect(latest(mocks.submit).params).not.toHaveProperty('laproteina_preset');
    expect(latest(draft).de_novo_drafts['de_novo_design:rfd3:unconditional'].min_length).toBe(111);
    await edit('Goal', 'motif_scaffolding');
    expect(control('Engine').value).toBe('laproteina');
    expect(control('Samples per length').value).toBe('8');
    await edit('Goal', 'ligand_conditioned');
    expect(control('Engine').value).toBe('disco');
    expect(control('Ligand SDF path')).toBeTruthy();
});

it('retains explicit generation clears, zero and false through engine switches and saved reopen', async () => {
    const draft = vi.fn();
    await render({ onDraftChange: draft, initialValues: { generator: 'disco', job_name: 'saved', disco_seeds: '1,2' } });
    await edit('Job name', ''); await edit('Target lengths', ''); await edit('Exact seeds', '');
    await edit('Engine', 'rfd3'); await edit('Seed', '0'); await edit('Engine', 'disco');
    expect(control('Job name').value).toBe(''); expect(control('Target lengths').value).toBe(''); expect(control('Exact seeds').value).toBe('');
    const saved = latest(draft);
    await act(async () => root.unmount()); document.body.replaceChildren();
    await render({ initialValues: saved, onDraftChange: draft });
    expect(control('Engine').value).toBe('disco'); expect(control('Job name').value).toBe(''); expect(control('Target lengths').value).toBe('');
    await edit('Engine', 'rfd3');
    expect(control('Seed').value).toBe('0'); expect((control('Dump trajectories') as HTMLInputElement).checked).toBe(false);
});

it('hydrates saved identity first then follows explicit browser navigation without losing drafts or echoing callbacks', async () => {
    const draft = vi.fn(); const navigation = vi.fn();
    const props = { initialValues: { generator: 'disco', design_task: 'dna_conditioned', disco_na_sequence: 'saved' }, onDraftChange: draft, onNavigationChange: navigation };
    const defaultRoute: DeNovoNavigationState = { modification_mode: 'de_novo_design', generator: 'rfd3', design_task: 'unconditional' };
    await render({ ...props, navigationState: defaultRoute });
    expect(control('DNA/RNA sequence').value).toBe('saved');
    await edit('DNA/RNA sequence', 'edited');
    await render({ ...props, navigationState: { modification_mode: 'shape_blueprint' } });
    expect(document.querySelector('[data-child="shape"]')).toBeTruthy();
    await render({ ...props, navigationState: { modification_mode: 'de_novo_design', generator: 'disco', design_task: 'dna_conditioned' } });
    expect(control('DNA/RNA sequence').value).toBe('edited');
    expect(navigation).not.toHaveBeenCalled();
});

it('mounts only the active child, forwards embedded run details, and preserves cleared child snapshots', async () => {
    const draft = vi.fn(); const navigation = vi.fn();
    await render({ initialValues: { modification_mode: 'region_redesign', source: 'original' }, onDraftChange: draft, onNavigationChange: navigation, runDetails: <div data-policy /> });
    expect(control('Source').value).toBe('original');
    await edit('Source', ''); await click('Shape'); await edit('Source', 'geometry'); await click('Redesign structure');
    expect(control('Source').value).toBe('');
    expect(latest(navigation)).toEqual({ modification_mode: 'region_redesign' });
    expect(document.querySelector('[data-child]')?.getAttribute('data-model')).toBe('protein_modification_experimental');
    expect(document.querySelectorAll('[data-child]')).toHaveLength(1);
    expect(document.querySelector('[data-child]')?.getAttribute('data-embedded')).toBe('true');
    expect(document.querySelectorAll('[data-policy]')).toHaveLength(1);
    expect(latest(draft)).toMatchObject({ source: '', enabled: false, amount: 0, selection: null });
    await click('Shape'); expect(control('Source').value).toBe('geometry');
});

it('keeps validated and native redesign route identities and snapshots distinct', async () => {
    const initialValues = { modification_mode: 'region_redesign', source: 'validated' };
    await render({ initialValues, navigationState: { modification_mode: 'region_redesign' } });
    await render({ initialValues, navigationState: { modification_mode: 'rfd3_local_redesign' } });
    expect(document.querySelector('[data-child]')?.getAttribute('data-model')).toBe('protein_local_redesign');
    expect(control('Source').value).toBe('');
    await edit('Source', 'native');
    await render({ initialValues, navigationState: { modification_mode: 'region_redesign' } });
    expect(document.querySelector('[data-child]')?.getAttribute('data-model')).toBe('protein_modification_experimental');
    expect(control('Source').value).toBe('validated');
});

it('forwards the full retained draft envelope to Template Manager without submitting', async () => {
    const open = vi.fn();
    await render({ onOpenTemplateManager: open });
    await edit('Minimum length', '123'); await edit('Engine', 'laproteina'); await edit('Samples per length', '5');
    await click('Template Manager');
    expect(latest(open)).toMatchObject({ currentModelId: 'protein_modification_experimental', currentMode: 'de_novo_design', currentParams: { generator: 'laproteina', laproteina_samples_per_length: 5, de_novo_drafts: { 'de_novo_design:rfd3:unconditional': { min_length: 123 } } } });
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('does not republish child drafts indefinitely when the external callback identity changes', async () => {
    const draft = vi.fn();
    function Owner() { const [, setDraft] = useState({}); return <ProteinModificationTemplate onBack={() => {}} initialValues={{ modification_mode: 'shape_blueprint' }} onDraftChange={value => { draft(value); setDraft(value); }} />; }
    await render();
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><Owner /></MemoryRouter></QueryClientProvider>));
    expect(draft).toHaveBeenCalledOnce();
});

it('retains the real source/residue workbench through sections, engine switches and JSON saved reopen', async () => {
    const pdb = [1, 2, 3].map(n => `ATOM      1  CA  ALA A${String(n).padStart(4)}       7.000   2.000   3.000  1.00 20.00           C  `).join('\n') + '\nEND\n';
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: [{ id: 'local', name: 'Local structure', path: 'inputs/source.pdb', category: 'Test', description: '' }] });
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { path: 'inputs/exact-motif.pdb' } });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => pdb })));
    const wait = async (ready: () => boolean) => {
        for (let i = 0; i < 100; i++) {
            await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
            if (ready()) return;
        }
        throw new Error('Workbench did not settle');
    };
    try {
        const draft = vi.fn();
        await render({ initialValues: { generator: 'laproteina', design_task: 'motif_scaffolding', laproteina_contig_string: '5/A1/2/A3/5', laproteina_segment_order: 'A', laproteina_checkpoint_dir: '/retained/custom' }, onDraftChange: draft });
        await click('Presets');
        await wait(() => [...document.querySelectorAll('button')].some(e => e.textContent === 'Local structure'));
        await click('Local structure');
        await wait(() => !!document.querySelector('[title="A3 (ALA)"]'));
        for (const n of [1, 3]) await act(async () => (document.querySelector(`[title="A${n} (ALA)"]`) as HTMLButtonElement).click());
        await wait(() => !!document.querySelector('[data-low-level-viewer]'));
        const viewer = document.querySelector('[data-low-level-viewer]');
        await click('Sampling'); await click('Source and regions');
        expect(document.querySelector('[data-low-level-viewer]')).toBe(viewer);
        expect(document.body.textContent).toContain('2 residues selected');
        await click('Use selected residues');
        await wait(() => control('Motif PDB path').value === 'inputs/exact-motif.pdb');
        await edit('Goal', 'unconditional'); await edit('Engine', 'disco'); await edit('Goal', 'motif_scaffolding');
        await wait(() => document.body.textContent?.includes('2 residues selected') === true);
        expect(control('Motif PDB path').value).toBe('inputs/exact-motif.pdb');
        expect([...document.querySelectorAll('summary')].some(e => e.textContent?.includes('Local structure'))).toBe(true);
        const saved = JSON.parse(JSON.stringify(latest(draft)));
        await act(async () => root.unmount()); document.body.replaceChildren();
        await render({ initialValues: saved });
        await wait(() => document.body.textContent?.includes('2 residues selected') === true);
        await click('Generate candidates');
        expect(latest(mocks.submit).params).toMatchObject({ generator: 'laproteina', design_task: 'motif_scaffolding', laproteina_motif_pdb: 'inputs/exact-motif.pdb', laproteina_contig_string: '5/A1/2/A3/5', laproteina_segment_order: 'A', laproteina_checkpoint_dir: '/retained/custom' });
        expect(latest(mocks.submit).params).not.toHaveProperty('laproteina_motif_inspection');
    } finally { get.mockRestore(); post.mockRestore(); vi.unstubAllGlobals(); }
});

it('shares job intent only with untouched engine drafts and preserves explicit native false/paths on replay', async () => {
    const draft = vi.fn();
    await render({ onDraftChange: draft });
    await edit('Job name', 'Shared name'); await edit('Number of designs', '6'); await edit('Engine', 'disco');
    expect(control('Job name').value).toBe('Shared name'); expect(control('Requested design count').value).toBe('6');
    await edit('Job name', ''); await edit('Engine', 'rfd3'); await edit('Job name', 'Changed RFD3'); await edit('Engine', 'disco');
    expect(control('Job name').value).toBe('');
    const saved = JSON.parse(JSON.stringify(latest(draft)));
    saved.de_novo_drafts['de_novo_design:disco:unconditional'].disco_checkpoint_path = '/retained/checkpoint.pt';
    saved.de_novo_drafts['de_novo_design:disco:unconditional'].disco_use_deepspeed_evo_attention = false;
    await act(async () => root.unmount()); document.body.replaceChildren();
    await render({ initialValues: saved }); await edit('Job name', 'Native replay'); await click('Generate candidates');
    expect(latest(mocks.submit).params).toMatchObject({ disco_checkpoint_path: '/retained/checkpoint.pt', disco_use_deepspeed_evo_attention: false });
});
