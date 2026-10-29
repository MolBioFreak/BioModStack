import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ComplexaTaskWorkspace } from '../../src/components/ComplexaTaskWorkspace';

const tasks = ['protein_binder', 'ligand_binder', 'AME', 'monomer_motif_indexed', 'monomer_motif_unindexed'];
const operations = ['generate', 'design', 'evaluate', 'analysis'];
const schema: any = { properties: { task: { enum: tasks }, operation: { enum: operations }, source: { properties: { target_hotspots: { type: 'array', items: { type: 'string' } } } } } };
const source = { document_id: 'MODEL-2', input_spec: 'A1-90', target_hotspots: ['A42'], contig_string: '10/A5-9/20', segment_order: 'A', SMILES: 'CCO', pdb_id: 'saved', motif_atoms: [{ chain: 'AB', residue: 5, insertion_code: 'A', atoms: ['CA'] }], ligand_atoms: [{ chain: 'L', residue: 8, insertion_code: '', atoms: ['C1'] }], binder_gen_only: false, padding: null, motif_only: false, binder_center: [0, 1, 2], ligand: 'L8', use_bonds_from_file: false };
let tree: ReactTestRenderer;
let current: any;
let emitted = vi.fn();
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
function Harness({ initial, picker = false, controls = schema }: { initial: any; picker?: boolean; controls?: any }) {
    const [request, setRequest] = useState(initial);
    current = request;
    return <ComplexaTaskWorkspace request={request} schema={controls} onChange={r => { emitted(r); setRequest(r); }} onTask={task => setRequest({ ...request, task })} sourceNames={['MODEL-2', 'other']} controlDraft={{}} onControlDraft={() => {}} visual={{ slot: 'MODEL-2', atoms: [{ chain: 'Z', residue: 99, insertion_code: '', atoms: ['N'] }] }} renderNative={(key, s) => <div data-expert={key} data-schema={s} />} renderSource={picker ? (label, value, commit) => <button type="button" data-source={value} onClick={() => commit('other')}>{label}</button> : undefined} />;
}
async function mount(initial: any, picker = false) { await act(async () => { tree = create(<Harness initial={initial} picker={picker} />); }); }
async function change(label: string, value: string) { await act(async () => tree.root.findByProps({ 'aria-label': label }).props.onChange({ target: { value } })); }
async function click(label: string) { await act(async () => tree.root.findAllByType('button').find(n => text(n) === label)!.props.onClick()); }
const has = (label: string) => tree.root.findAllByProps({ 'aria-label': label }).length > 0;
afterEach(async () => { await act(async () => tree?.unmount()); emitted = vi.fn(); });

for (const task of tasks) for (const operation of operations) it(`${task}/${operation}: relevance, edits, switching and cold readback preserve science`, async () => {
    const initial = { task, operation, source: structuredClone(source), reward: null, seed: 0, evaluation_slots: ['candidate-B', 'candidate-A'], args: { retained: false } };
    await mount(initial);
    expect(emitted).not.toHaveBeenCalled();
    const motif = task.startsWith('monomer_motif');
    const evaluates = operation !== 'generate';
    expect(has('Complexa input_spec')).toBe(task === 'protein_binder' || (!motif && evaluates));
    expect(has('Complexa SMILES')).toBe(['ligand_binder', 'AME'].includes(task) || (!motif && evaluates));
    expect(has('Complexa contig_string')).toBe(motif || task === 'AME');
    expect(has('Complexa evaluation slot 1')).toBe(['evaluate', 'analysis'].includes(operation));
    const editKey = motif || task === 'AME' ? 'contig_string' : task === 'ligand_binder' ? 'SMILES' : 'input_spec';
    await change(`Complexa ${editKey}`, 'authored-distinct');
    const expected = { ...initial, source: { ...initial.source, [editKey]: 'authored-distinct' } };
    expect(current).toEqual(expected);
    await change('Complexa task', task === 'protein_binder' ? 'AME' : 'protein_binder');
    await change('Native operation', operation === 'generate' ? 'analysis' : 'generate');
    await change('Complexa task', task);
    await change('Native operation', operation);
    expect(current).toEqual(expected);
    const saved = JSON.stringify(current);
    await act(async () => tree.unmount());
    await mount(JSON.parse(saved));
    expect(current).toEqual(expected);
    expect(tree.root.findByProps({ 'aria-label': `Complexa ${editKey}` }).props.value).toBe('authored-distinct');
    const advanced = tree.root.findAllByType('details').find(n => text(n.findByType('summary')) === 'Advanced source settings')!;
    await act(async () => advanced.props.onToggle({ currentTarget: { open: true } }));
    expect(tree.root.findByProps({ 'data-expert': 'source' }).props['data-schema']).toBe(schema.properties.source);
    expect(current).toEqual(expected);
});

it('plain count controls show native schema references without authoring sparse values, and keep unknown inheritance explicit', async () => {
    const native = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import json,sys;sys.path.insert(0,'platform/api');from services.complexa import discovery;print(json.dumps(discovery()['schema']))"], { cwd: resolve('../..'), encoding: 'utf8' }));
    const initial = { task: 'protein_binder', operation: 'generate', source: {} };
    await act(async () => { tree = create(<Harness initial={initial} controls={native} />); });
    expect(tree.root.findByProps({ 'aria-label': 'Repeats per sample' }).props.value).toBe('1');
    expect(tree.root.findByProps({ 'aria-label': 'Recycles' }).props.value).toBe('0');
    expect(text(tree.root)).toContain('Schema reference; not an override');
    expect(emitted).not.toHaveBeenCalled(); expect(current).toEqual(initial);
    await change('Repeats per sample', ''); expect(current.nrepeat_per_sample).toBe('');
    await click('Inherit Repeats per sample'); expect(current).toEqual(initial);
    await act(async () => tree.unmount()); await mount(JSON.parse(JSON.stringify(current)));
    expect(text(tree.root)).toContain('Inherited; value not reported');
    expect(tree.root.findByProps({ 'aria-label': 'Repeats per sample' }).props.value).toBe('');
    expect(current).toEqual(initial);
});

it('AME atom roles are independent and visual selection requires explicit apply', async () => {
    await mount({ task: 'AME', operation: 'generate', source: structuredClone(source) });
    expect(current.source).toEqual(source);
    expect(emitted).not.toHaveBeenCalled();
    await change('motif_atoms 1 chain', 'M');
    expect(current.source.ligand_atoms).toEqual(source.ligand_atoms);
    await click('Apply selection to ligand atoms');
    expect(current.source.motif_atoms[0].chain).toBe('M');
    expect(current.source.ligand_atoms).toEqual([...source.ligand_atoms, { chain: 'Z', residue: 99, insertion_code: '', atoms: ['N'] }]);
});
it('candidate ordering and source picker commits preserve independent context', async () => {
    await mount({ task: 'protein_binder', operation: 'evaluate', source: structuredClone(source), evaluation_slots: ['B', 'A'] }, true);
    expect(current.source.document_id).toBe('MODEL-2');
    expect(emitted).not.toHaveBeenCalled();
    await click('Candidate 1');
    await click('Source structure');
    expect(current.evaluation_slots).toEqual(['other', 'A']);
    expect(current.source).toEqual({ ...source, document_id: 'other' });
});
it('numeric hotspot warning appears only for retained numeric values', async () => {
    await mount({ task: 'protein_binder', source: structuredClone(source) });
    expect(text(tree.root)).not.toContain('Legacy numeric');
    await act(async () => tree.unmount());
    await mount({ task: 'protein_binder', source: { ...source, target_hotspots: [42] } });
    expect(text(tree.root)).toContain('Legacy numeric');
    expect(current.source.target_hotspots).toEqual([42]);
    expect(emitted).not.toHaveBeenCalled();
});
