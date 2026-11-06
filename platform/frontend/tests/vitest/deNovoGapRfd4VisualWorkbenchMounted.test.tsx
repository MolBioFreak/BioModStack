import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { Rfd4TaskWorkspace } from '../../src/components/Rfd4TaskWorkspace';
import { rfd4SelectedFragments, type Rfd4VisualSelection } from '../../src/components/Rfd4VisualWorkbench';
import type { StructureMaterialization } from '../../src/lib/api';
import type { BioXpOperatorJsonSchema } from '../../src/lib/bioxpClient';
const receipt = { path: 'native.cif', model_number: 1, model_numbers: [1], source_positions: [7, 8, 11].map(n => ({ model_number: 1, label_asym_id: 'X', label_seq_id: n, auth_asym_id: 'A', auth_seq_id: n + 100, pdbx_PDB_ins_code: '', atoms: [{ label_atom_id: 'CA', auth_atom_id: 'CA', model_atom_index: n }] })) } as StructureMaterialization;
const visual: Rfd4VisualSelection = { slot: 'target', receipt, expression: '(chain_id == "X" and res_id == 7)', identities: [7, 8, 11].map(n => ({ documentId: 'native.cif', labelAsymId: 'X', labelSeqId: n, authAsymId: 'A', authSeqId: n + 100, modelNumber: 1 })) };
const schema = { properties: { input: { oneOf: [{ properties: { kind: { const: 'condition_spec' }, examples: { items: { properties: { conditions: { properties: { C_CRD: {}, C_SEQ: {}, C_SEQ_PROB_CYS: {} } } } } } } }] } } } as BioXpOperatorJsonSchema;
let root: Root | undefined;
let current: any;
const clear = vi.fn();
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); clear.mockReset(); });
async function mount(initial: any, selection = visual, sourceChains?: Record<string, string[]>) {
    function Harness() {
        const [request, setRequest] = useState(initial); const [selected, setSelected] = useState<Rfd4VisualSelection | undefined>(selection); current = request;
        return <Rfd4TaskWorkspace request={request} schema={schema} mode="generate" onRecipe={() => {}} onChange={setRequest} visual={selected} sourceNames={['target']} sourceChains={sourceChains} onClearSelection={() => { clear(); setSelected(undefined); }} renderNative={() => null} renderSource={(label, slot) => <div data-source-viewer={slot}>{label}</div>} />;
    }
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<Harness />));
}
async function click(text: string) { const el = [...document.querySelectorAll('button')].find(e => e.textContent === text); expect(el, text).toBeTruthy(); await act(async () => el!.click()); }
async function edit(label: string, value: string) {
    const el = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
const request = (extra: object = {}) => ({ input: { kind: 'condition_spec', examples: [{ name: 'motif', sources: [{ slot: 'target' }], ...extra }] }, settings: { seed: 0, generation: { args: { partial_t_backbone: null } } } });

it('maps exact identities to contiguous native-label spans, not author numbering or expression text', () => {
    expect(rfd4SelectedFragments({ ...visual, expression: 'arbitrary query with X999-999' })).toEqual({ tokens: ['X7-8', 'X11-11'] });
    expect(rfd4SelectedFragments({ slot: 'target', expression: 'chain_id == "A" and res_id == 42' }).tokens).toEqual([]);
    expect(rfd4SelectedFragments({ ...visual, identities: visual.identities!.map(ref => ({ ...ref, labelAsymId: undefined, labelSeqId: undefined })) }).tokens).toEqual(['X7-8', 'X11-11']);
    expect(rfd4SelectedFragments({ ...visual, identities: [{ ...visual.identities![0], documentId: 'other.cif' }] }).tokens).toEqual([]);
    expect(rfd4SelectedFragments({ ...visual, identities: [{ ...visual.identities![0], operatorInstanceId: 'copy' }] }).tokens).toEqual([]);
});
it('deduplicates atom picks into residue fragments without losing native atom-specific conditions', () => {
    const atom = { ...visual.identities![0], labelAtomId: 'CA' };
    expect(rfd4SelectedFragments({ ...visual, identities: [atom, atom] }).tokens).toEqual(['X7-7']);
});
it('renders integrated source and editable colored strip, keeping saved complex tokens untouched', async () => {
    const initial = request({ contig: 'saved complex / token,20-40,/0' }); await mount(initial);
    expect(current).toEqual(initial);
    expect(document.querySelector('.rfd4-design-source [data-source-viewer="target"]')).not.toBeNull();
    expect(document.querySelector('.rfd4-region--native')?.textContent).toContain('saved complex / token');
    expect(document.querySelector('.rfd4-region--generated')?.textContent).toContain('20-40');
    await click('Add selected residues as fragment'); expect(current.input.examples[0].contig).toBe('saved complex / token,20-40,/0,X7-8,X11-11');
    await click('Move region 4 earlier'); expect(current.input.examples[0].contig).toBe('saved complex / token,20-40,X7-8,/0,X11-11');
    await edit('Region 2', '25-50'); await click('Remove region 5');
    expect(current.input.examples[0].contig).toBe('saved complex / token,25-50,X7-8,/0');
    expect(current.settings).toEqual(initial.settings);
    await click('Inherit contig'); expect(current.input.examples[0]).not.toHaveProperty('contig');
});
it('requires explicit assignment, preserves complex conditions and clears only transient selection', async () => {
    const saved = { C_SEQ_PROB_CYS: { '0': [{ select: 'saved(complex, query)', group: 'retain', future: false }] } };
    const initial = request({ conditions: saved }); await mount(initial);
    expect(current).toEqual(initial); expect(document.body.textContent).toContain('3 residues selected');
    await click('Fix coordinates on selection');
    expect(current.input.examples[0].conditions).toEqual({ ...saved, C_CRD: { true: [{ select: visual.expression }] } });
    expect(document.querySelector('.rfd4-condition-sidebar')?.textContent).toContain('Coordinates · true');
    const applied = structuredClone(current); await click('Clear selection');
    expect(clear).toHaveBeenCalledOnce(); expect(current).toEqual(applied); expect(document.body.textContent).toContain('0 residues selected');
    await click('Undo condition edit'); expect(current).toEqual(initial);
    await edit('Selection expression', 'manual(and, or)'); await edit('Native condition value', 'false'); await click('Apply native condition');
    expect(current.input.examples[0].conditions.C_CRD.false).toEqual([{ select: 'manual(and, or)' }]);
    expect(current.input.examples[0].conditions.C_SEQ_PROB_CYS).toEqual(saved.C_SEQ_PROB_CYS);
});
it('keeps ambiguous multi-source adoption observational and manual authoring usable', async () => {
    const initial = request(); initial.input.examples[0].sources.push({ slot: 'second' });
    await mount(initial, visual, { target: ['X'], second: ['X'] });
    await click('Add selected residues as fragment'); expect(current).toEqual(initial);
    await edit('Native contig', 'X7-8,10-20'); expect(current.input.examples[0].contig).toBe('X7-8,10-20');
    await edit('Selection expression', 'manual query'); await edit('Native condition value', 'true'); await click('Apply native condition');
    expect(current.input.examples[0].conditions.C_CRD.true[0].select).toBe('manual query');
});
