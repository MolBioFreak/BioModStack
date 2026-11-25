import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { Rfd4TaskWorkspace, Rfd4ChainLengths, type Rfd4LengthDraft } from '../../src/components/Rfd4TaskWorkspace';
const cwd = resolve('../..');
const py = process.env.BMS_TEST_API_PYTHON ?? 'python';
let root: Root | undefined, draft: any;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); });
async function mount(node: React.ReactNode) { const el = document.createElement('div'); document.body.append(el); root = createRoot(el); await act(async () => root!.render(node)); }
async function click(text: string) { const b = [...document.querySelectorAll('button')].find(b => b.textContent === text); expect(b, text).toBeTruthy(); await act(async () => b!.click()); }
async function edit(label: string, value: string) { const n = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(n, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'value')!.set!.call(n, value); n.dispatchEvent(new Event(n.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
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
