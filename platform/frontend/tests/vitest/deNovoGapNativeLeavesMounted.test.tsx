import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { DiscoJobsInput } from '../../src/components/DiscoJobsInput';
import { ComplexaTaskWorkspace } from '../../src/components/ComplexaTaskWorkspace';
import { LaProteinaFragments } from '../../src/components/LaProteinaFragments';
import { complexaTargetSelection } from '../../src/lib/complexaNativeSelection';
import { parseLaProteinaFragments } from '../../src/lib/laProteinaMotifSelection';
const python = process.env.BMS_TEST_PYTHON ?? 'python3';
const schema = JSON.parse(execFileSync(python, ['-c', 'import json; from scripts.lib.complexa_contract import parameter_schema; print(json.dumps(parameter_schema()))'], { cwd: '../..', encoding: 'utf8' }));
const normalize = (request: any) => JSON.parse(execFileSync(python, ['-c', 'import json,sys; from scripts.lib.complexa_contract import normalize_request; print(json.dumps(normalize_request(json.load(sys.stdin))))'], { cwd: '../..', encoding: 'utf8', input: JSON.stringify(request) }));
let tree: ReactTestRenderer;
let current: any;
let controls: any;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const click = async (label: string) => { await act(async () => tree.root.findAllByType('button').find(n => text(n) === label)!.props.onClick()); };
const change = async (label: string, value: string) => { await act(async () => tree.root.findByProps({ 'aria-label': label }).props.onChange({ target: { value } })); };
function Harness({ initial }: { initial: any }) {
    const [request, setRequest] = useState(initial);
    const [draft, setDraft] = useState({}); current = request; controls = draft;
    return <ComplexaTaskWorkspace request={request} schema={schema} onChange={setRequest} onTask={task => setRequest({ ...request, task })} renderNative={() => null} sourceNames={['checked-PDB']} controlDraft={draft} onControlDraft={setDraft} visual={{ slot: 'checked-PDB', atoms: [{ chain: 'B', residue: 42, insertion_code: '', atoms: ['CA', 'C'] }] }} />;
}
afterEach(async () => { await act(async () => tree?.unmount()); });
for (const [algorithm, label, value, branch, key] of [
    ['best-of-n', 'Replicas (all returned)', '7', 'best_of_n', 'replicas'],
    ['beam-search', 'Branches', '8', 'beam_search', 'n_branch'],
    ['fk-steering', 'Reward temperature', '0.035', 'fk_steering', 'temperature'],
    ['mcts', 'Exploration probability', '0', 'mcts', 'exploration_prob'],
] as const) it(`real schema → mounted ${algorithm} budget → exact native normalization`, async () => {
    const initial = { task: 'AME', task_name: 'exact', reward: null, seed: 0, source: { document_id: 'checked-PDB', ligand_only: false }, search: { algorithm: 'single-pass', mcts: { n_simulations: 9 } } };
    await act(async () => { tree = create(<Harness initial={initial} />); });
    expect(current).toEqual(initial);
    await change('Complexa search strategy', algorithm);
    await change(`Complexa search ${label}`, value);
    const expected = { ...initial, search: { ...initial.search, algorithm, [branch]: { ...(initial.search as any)[branch], [key]: Number(value) } } };
    expect(current).toEqual(expected);
    expect(normalize(current)).toEqual(expected);
    const disclosure = tree.root.findAllByType('details').find(n => text(n.findByType('summary')) === 'Search checkpoints and retention')!;
    await act(async () => disclosure.props.onToggle({ currentTarget: { open: true } }));
    if (algorithm !== 'best-of-n') await change('Complexa search keep_lookahead_samples', 'false');
    const saved = JSON.parse(JSON.stringify(current));
    expect(normalize(saved)).toEqual(saved);
    await change('Complexa search strategy', '');
    expect(current).not.toHaveProperty('search');
    expect(controls.configured_search).toEqual(saved.search);
    await change('Complexa search strategy', algorithm);
    expect(current).toEqual(saved);
    await act(async () => tree.unmount());
    await act(async () => { tree = create(<Harness initial={JSON.parse(JSON.stringify(saved))} />); });
    expect(current).toEqual(saved);
    expect(normalize(current)).toEqual(saved);
});
it('inherit and single-pass remain sparse, target picks are separate explicit actions', async () => {
    const initial = { task: 'protein_binder', task_name: 'selected', source: { document_id: 'checked-PDB', ligand_only: false, target_hotspots: [42, 'A3'] }, reward: null };
    await act(async () => { tree = create(<Harness initial={initial} />); });
    expect(current).toEqual(initial);
    await click('Apply selection to target residues');
    expect(current.source).toEqual({ ...initial.source, input_spec: 'B42-42' });
    await click('Apply selection to target hotspots');
    expect(current.source.target_hotspots).toEqual([42, 'A3', 'B42']);
    await change('Complexa search strategy', 'single-pass');
    expect(current.search).toEqual({ algorithm: 'single-pass' });
    expect(normalize(current)).toEqual(current);
    expect(complexaTargetSelection([{ chain: 'A', residue: 42, insertion_code: 'A', atoms: ['CA'] }])).toHaveProperty('issue');
    expect(complexaTargetSelection([{ chain: 'A', residue: 42, insertion_code: '', atoms: ['N'] }, { chain: 'A', residue: 42, insertion_code: '', atoms: ['CA'] }, { chain: 'B', residue: 7, insertion_code: '', atoms: ['O'] }])).toEqual({ input_spec: 'A42-42, B7-7', target_hotspots: ['A42', 'B7'] });
});
it('explicit strands, copies, modifications and bond pickers reach exact ordered native jobs', async () => {
    function Disco() { const [jobs, setJobs] = useState<any>([{ name: 'strands', retained: false, sequences: [{ dnaSequence: { sequence: 'ACGT', count: 1, future: 0, modifications: [] } }] }]); current = jobs; return <DiscoJobsInput value={jobs} path="" onChange={setJobs} />; }
    await act(async () => { tree = create(<Disco />); });
    await change('Job 1 entity 1 copies', '2');
    expect(current[0].sequences).toHaveLength(1);
    await click('Add RNA strand (5′ → 3′)');
    await change('Job 1 entity 2 sequence', 'ACGU');
    await click('Add modification to entity 1');
    await change('Job 1 entity 1 modification 1 position', '2');
    await change('Job 1 entity 1 modification 1 CCD', 'CCD_5CM');
    await click('Add bond');
    await change('Bond 1 left_entity entity picker', '1');
    await change('Bond 1 right_entity entity picker', '2');
    await change('Bond 1 left_position', '2');
    await change('Bond 1 right_position', '3');
    await change('Bond 1 left_atom', 'P');
    await change('right atom type', 'integer');
    await change('Bond 1 right_copy', '1');
    await click('Move entity 2 up (remap bonds)');
    const expected = [{ name: 'strands', retained: false, sequences: [
        { rnaSequence: { sequence: 'ACGU', count: 1 } },
        { dnaSequence: { sequence: 'ACGT', count: 2, future: 0, modifications: [{ basePosition: 2, modificationType: 'CCD_5CM' }] } }],
        covalent_bonds: [{ left_entity: 2, right_entity: 1, left_position: 2, right_position: 3, left_atom: 'P', right_atom: 0, right_copy: 1 }] }];
    expect(current).toEqual(expected);
    const prepared = JSON.parse(execFileSync(python, ['-c', 'import json,sys; from scripts.lib.disco_contract import build_native_jobs; print(json.dumps(build_native_jobs(json.load(sys.stdin))))'], { cwd: '../..', encoding: 'utf8', input: JSON.stringify(current) }));
    expect(prepared).toEqual(expected);
    await click('Remove entity 2 and its bonds');
    expect(current[0].covalent_bonds).toEqual([]);
    expect(current[0].sequences).toEqual([expected[0].sequences[0]]);
});

it('inline La fragments preserve literal native tokens, blanks, order and manual expressions', async () => {
    function Fragments({ initial }: { initial: string }) { const [contig, setContig] = useState(initial); current = contig; return <LaProteinaFragments contig={contig} onChange={setContig} />; }
    await act(async () => { tree = create(<Fragments initial="010/A25-27/5-9/B40/native expression" />); });
    expect(current).toBe('010/A25-27/5-9/B40/native expression');
    await change('La fragment 3 end', '11');
    await change('La fragment 2 start', '');
    expect(current).toBe('010/A-27/5-11/B40/native expression');
    expect(tree.root.findByProps({ 'aria-label': 'La fragment 2 start' }).props.value).toBe('');
    await click('Move fragment 4 up');
    expect(current).toBe('010/A-27/B40/5-11/native expression');
    const saved = JSON.stringify(current);
    await act(async () => tree.unmount());
    await act(async () => { tree = create(<Fragments initial={JSON.parse(saved)} />); });
    expect(current).toBe('010/A-27/B40/5-11/native expression');
    expect(parseLaProteinaFragments(current).map(f => f.token).join('/')).toBe(current);
});
