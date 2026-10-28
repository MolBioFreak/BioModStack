import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { writeFileSync } from 'node:fs';
import { Rfd3SelectionWorkbench } from '../../src/components/Rfd3SelectionWorkbench';
import { Rfd3AdditionalConditions } from '../../src/components/Rfd3AdditionalConditions';
import { Rfd3PartialNoiseInput } from '../../src/components/Rfd3PartialNoiseInput';
import { Rfd4TaskWorkspace, Rfd4ChainLengths, type Rfd4LengthDraft } from '../../src/components/Rfd4TaskWorkspace';
import { projectRfd3SourceSelection, type Rfd3SourceSelection } from '../../src/lib/rfd3SourceSelection';
const cwd = resolve('../..');
const py = process.env.BMS_TEST_API_PYTHON ?? 'python';
const sourcePath = resolve('tests/fixtures/rfd3-selection-identity.cif');
const ledger = JSON.parse(execFileSync(py, ['-c', "import sys,json;sys.path.insert(0,'platform/api');from services.binder_source_materialization import _cif_ledger;from Bio.PDB.MMCIF2Dict import MMCIF2Dict;print(json.dumps(_cif_ledger(MMCIF2Dict(sys.argv[1]))))", sourcePath], { cwd, encoding: 'utf8' }));
const visual: Rfd3SourceSelection = { inputPath: sourcePath, sourceMapping: 'bms.rfd3.source-map.v1', source: { documentId: sourcePath, model_number: 2, model_numbers: [1, 2], ...ledger }, identities: [{ documentId: sourcePath, modelNumber: 2, labelAsymId: 'X', labelSeqId: 7, authAsymId: 'AA', authSeqId: 42, insertionCode: 'B', labelAtomId: 'CA', authAtomId: 'CAX', atomIndex: 0 }] };
let root: Root | undefined, draft: any;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); });
async function mount(node: React.ReactNode) { const el = document.createElement('div'); document.body.append(el); root = createRoot(el); await act(async () => root!.render(node)); }
async function click(text: string) { const b = [...document.querySelectorAll('button')].find(b => b.textContent === text); expect(b, text).toBeTruthy(); await act(async () => b!.click()); }
async function edit(label: string, value: string) { const n = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(n, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'value')!.set!.call(n, value); n.dispatchEvent(new Event(n.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
function Selection({ initial = {}, onInspect = () => {} }: { initial?: Record<string, unknown>; onInspect?: (v: any) => void }) { const [value, setValue] = useState(initial); draft = value; return <Rfd3SelectionWorkbench value={value} onChange={setValue} visual={visual} onInspect={onInspect} />; }
it('maps real source ledger author/label/MODEL separately, Apply/inspect/Undo do not change other masks', async () => {
    const inspect = vi.fn(); const initial = { contig: 'AA42B,20-30', select_unfixed_sequence: false, select_hotspots: null };
    await mount(<Selection initial={initial} onInspect={inspect} />); expect(draft).toEqual(initial);
    await click('Apply RFD3 selection'); expect(draft).toEqual({ ...initial, select_fixed_atoms: { AA42B: ['CAX'] } });
    await click('Inspect select_fixed_atoms'); expect(inspect).toHaveBeenCalledWith({ field: 'select_fixed_atoms', value: { AA42B: ['CAX'] } });
    const capture = JSON.parse(JSON.stringify(draft));
    const lowered = JSON.parse(execFileSync(py, ['-c', "import json,sys,tempfile;from pathlib import Path;from scripts.rfd3_local_redesign.source_mapping import materialize_source,lower_native;from Bio.PDB.MMCIF2Dict import MMCIF2Dict;d=tempfile.TemporaryDirectory();p=Path(d.name)/'native.cif';m=materialize_source(Path(sys.argv[1]),p,model_serial=2);v=lower_native(json.loads(sys.argv[2]),m,p.name);c=MMCIF2Dict(str(p));print(json.dumps({'native':v,'model':m['model'],'atoms':m['atoms'],'bond':c['_struct_conn.ptnr1_auth_asym_id']}))", sourcePath, JSON.stringify(capture)], { cwd, encoding: 'utf8' }));
    expect(lowered.native).toMatchObject({ contig: 'A1,20-30', select_fixed_atoms: { A1: ['CA'] }, select_unfixed_sequence: false, select_hotspots: null });
    expect(lowered.model).toBe('2'); expect(lowered.bond).toEqual(['A']); expect(lowered.atoms.map((a: any) => a.source.component)).toContain('5MC');
    if (process.env.BMS_TEST_RFD3_SELECTION_WIRE) writeFileSync(process.env.BMS_TEST_RFD3_SELECTION_WIRE, JSON.stringify({ sourcePath, draft: capture, native: lowered.native }));
    await click('Undo RFD3 selection'); expect(draft).toEqual(initial);
    await edit('RFD3 selection destination', 'select_unfixed_sequence'); await click('Apply RFD3 selection'); expect(draft.select_unfixed_sequence).toBe('AA42B'); expect(draft.select_fixed_atoms).toBeUndefined();
});
it.each(['contig', 'select_hotspots', 'select_hbond_donor', 'select_hbond_acceptor', 'select_buried', 'select_exposed', 'select_partially_buried', 'unindex', 'ligand', 'redesign_motif_sidechains'])('explicit native destination %s is isolated and survives serialized reopen', async field => {
    await mount(<Selection initial={{ select_unfixed_sequence: false }} />); await edit('RFD3 selection destination', field); await click('Apply RFD3 selection');
    const expected = ['select_hbond_donor', 'select_hbond_acceptor', 'select_buried', 'select_exposed', 'select_partially_buried'].includes(field) ? { AA42B: ['CAX'] } : 'AA42B';
    expect(draft).toEqual({ select_unfixed_sequence: false, [field]: expected }); const saved = JSON.parse(JSON.stringify(draft));
    await act(async () => root!.unmount()); root = undefined; await mount(<Selection initial={saved} />); expect(draft).toEqual(saved);
});
it.each([
    { inputPath: '/replaced.cif' }, { sourceMapping: undefined },
    { source: { ...visual.source, model_number: null } },
    { identities: [{ ...visual.identities[0], modelNumber: 1 }] },
    { identities: [{ ...visual.identities[0], altLoc: 'A' }] },
    { identities: [{ ...visual.identities[0], operatorInstanceId: 'copy-2' }] },
    { identities: [{ ...visual.identities[0], labelAsymId: 'Y', labelSeqId: 3, authAsymId: 'DNA', authSeqId: 90, insertionCode: '', labelAtomId: 'C1', authAtomId: 'CX', atomIndex: 3 }] },
])('unresolved mapping remains observational case %#', changed => { expect(projectRfd3SourceSelection({ ...visual, ...changed } as Rfd3SourceSelection)).toEqual({ issue: expect.any(String) }); });
it('contig only authors string/null, retains imported unions without stringifying', async () => {
    function Conditions() { const [value, setValue] = useState<Record<string, unknown>>({ contig: { A1: ['CA'] }, select_hbond_donor: true }); draft = value; return <Rfd3AdditionalConditions general definitions={{ contig: { type: 'selection' }, select_hbond_donor: { type: 'selection' } }} value={value} onChange={setValue} />; }
    await mount(<Conditions />); const detail = document.querySelector('details')!; await act(async () => { detail.open = true; detail.dispatchEvent(new Event('toggle')); });
    expect(draft.contig).toEqual({ A1: ['CA'] }); expect(document.body.textContent).toContain('Retained native contig');
    await click('Use null contig'); expect(draft.contig).toBeNull(); await click('Omit contig'); expect(draft).not.toHaveProperty('contig');
    await click('Set contig'); expect(typeof draft.contig === 'string' || draft.contig === null).toBe(true); expect(draft.select_hbond_donor).toBe(true);
});
it('RFD3 advisory slider retains high exact noise, null and cleared text independently', async () => {
    function Noise() { const [value, setValue] = useState<number | '' | null>(23.456789); draft = value; return <Rfd3PartialNoiseInput value={value} onChange={setValue} />; }
    await mount(<Noise />); expect(draft).toBe(23.456789); expect((document.querySelector('[aria-label="Partial noise exact Å"]') as HTMLInputElement).value).toBe('23.456789');
    await edit('Partial noise exact Å', '31.123456'); expect(draft).toBe(31.123456); await edit('Partial noise exact Å', ''); expect(draft).toBe('');
    await click('Use native null partial_t'); expect(draft).toBeNull(); await edit('Partial noise advisory slider', '1'); await edit('Partial noise advisory slider', '0'); expect(draft).toBe(0);
});
it('exact/range lengths retain independent editor drafts, no example default, no UI maximum', async () => {
    let savedDraft: Rfd4LengthDraft | undefined;
    function Length({ initial = { chain_id: 'A' }, retained }: { initial?: Record<string, unknown>; retained?: Rfd4LengthDraft }) {
        const [chain, setChain] = useState(initial); const [length, setLength] = useState(retained); draft = chain; savedDraft = length;
        return <Rfd4ChainLengths chain={chain} index={0} onChange={setChain} draft={length} onDraftChange={setLength} />;
    }
    await mount(<Length />); expect(draft).toEqual({ chain_id: 'A' });
    await edit('Chain 1 min_length', '17'); await edit('Chain 1 max_length', '1200');
    await edit('Chain 1 length mode', 'exact'); expect(draft).toEqual({ chain_id: 'A' });
    await edit('Chain 1 exact length', '1321'); expect(draft).toEqual({ chain_id: 'A', min_length: 1321, max_length: 1321 });
    const saved = JSON.parse(JSON.stringify({ chain: draft, length: savedDraft }));
    await act(async () => root!.unmount()); root = undefined; await mount(<Length initial={saved.chain} retained={saved.length} />);
    await edit('Chain 1 length mode', 'range'); expect(draft).toEqual({ chain_id: 'A', min_length: 17, max_length: 1200 });
    await edit('Chain 1 length mode', 'exact'); expect(draft.min_length).toBe(1321);
    await click('Clear required value Chain 1 exact length'); expect(draft).toEqual({ chain_id: 'A' });
});
it('RFD4 displays model-owned amount defaults without sparse request hydration, including native null seed', async () => {
    const schema = JSON.parse(execFileSync(py, ['-c', "import json;from scripts.lib.rfd4_proteina_contract import parameter_schema;print(json.dumps(parameter_schema('generate')))"], { cwd, encoding: 'utf8', maxBuffer: 16000000 }));
    function Amount() { const [value, setValue] = useState<Record<string, unknown>>({ input: { kind: 'annotated', slots: ['source'] } }); draft = value; return <Rfd4TaskWorkspace request={value} schema={schema} mode="generate" onRecipe={() => {}} onChange={setValue} sourceNames={[]} renderNative={() => null} />; }
    await mount(<Amount />);
    expect((document.querySelector('[aria-label="Designs per input"]') as HTMLInputElement).value).toBe('1');
    expect((document.querySelector('[aria-label="Batch size"]') as HTMLInputElement).value).toBe('1');
    expect(draft.settings).toBeUndefined();
    await click('Use native null for Seed'); expect(draft.settings).toEqual({ seed: null });
    await edit('Seed', '0'); expect(draft.settings).toEqual({ seed: 0 });
    await click('Inherit Seed'); expect(draft.settings).toEqual({});
});
function Workspace({ initial, inspect }: { initial: Record<string, any>; inspect?: (v: any) => void }) { const [value, setValue] = useState(initial); draft = value; return <Rfd4TaskWorkspace request={value} schema={{}} mode="generate" recipe="partial" onRecipe={() => {}} onChange={setValue} sourceNames={[]} renderNative={() => null} onInspectCondition={inspect} />; }
it('RFD4 per-example parse policy is explicit; mixed inherited drafts are not silently repaired', async () => {
    const first = { name: 'mixed', sources: [{ slot: 'a', input_as_annotated_cif: false }, { slot: 'b' }] };
    const initial = { input: { kind: 'condition_spec', global_args: { input_as_annotated_cif: true }, examples: [first, { name: 'untouched', sources: [{ slot: 'c' }] }] } };
    await mount(<Workspace initial={initial} />); expect(draft).toEqual(initial); expect(document.body.textContent).toContain('Mixed source policies retained');
    await click('Preserve annotations for example 1'); expect(draft.input.examples[0].sources.every((s: any) => s.input_as_annotated_cif === true)).toBe(true); expect(draft.input.examples[1]).toEqual(initial.input.examples[1]);
    await click('Inherit policies for example 1'); expect(draft.input.examples[0].sources).toEqual([{ slot: 'a' }, { slot: 'b' }]); expect(draft.settings).toBeUndefined();
});
it('RFD4 inspection carries exact grouped native rows, fraction precision and independent times', async () => {
    const inspect = vi.fn(); const row = { select: 'chain_id == "X" and atom_name == "CA"', group: 'motif.offset' };
    const initial = { input: { kind: 'condition_spec', examples: [{ name: 'motif', sources: [{ slot: 'a.b' }], conditions: { C_IDX: { false: [row] } } }] } };
    await mount(<Workspace initial={initial} inspect={inspect} />); await click('Inspect C_IDX false row 1'); expect(inspect).toHaveBeenCalledWith({ exampleIndex: 0, family: 'C_IDX', value: 'false', rowIndex: 0, ...row }); expect(draft).toEqual(initial);
    await edit('partial_trajectory_fraction_steps', '0.123456789'); await edit('partial_t_backbone', '0'); await edit('partial_t_latent', '0.75');
    expect(draft.settings.generation.args).toEqual({ partial_trajectory_fraction_steps: 0.123456789, partial_t_backbone: 0, partial_t_latent: 0.75 });
    const normalized = JSON.parse(execFileSync(py, ['-c', 'import sys,json;from scripts.lib.rfd4_proteina_contract import normalize_request;print(json.dumps(normalize_request(json.loads(sys.argv[1]))))', JSON.stringify(draft)], { cwd, encoding: 'utf8' }));
    expect(normalized.settings.generation.args).toEqual(draft.settings.generation.args);
    await edit('partial_trajectory_fraction_steps', ''); expect(draft.settings.generation.args.partial_trajectory_fraction_steps).toBe('');
});
