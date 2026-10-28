import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { api } from '../../src/lib/api';
import { MolecularConstructionWorkbench } from '../../src/components/MolecularConstructionWorkbench';
import { emptyConstructionDraft } from '../../src/lib/molecularConstruction';
import type { ConstructionGraph, MolecularConstructionDraft } from '../../src/lib/molecularConstruction';

const repo = path.resolve('../..');
const python = process.env.BMS_CONSTRUCTION_PYTHON;
if (!python) throw new Error('Set BMS_CONSTRUCTION_PYTHON to the isolated native graph interpreter; native acceptance is not a skipped test.');
function native(method: string, url: string, body?: string) {
    return JSON.parse(execFileSync(python!, [path.join(repo, 'platform/api/tests/molecular_construction_ui_transport.py')], {
        input: JSON.stringify({ method, url, body }), encoding: 'utf8',
        env: { ...process.env, PYTHONPATH: `${repo}:${repo}/platform/api` },
    }).trim());
}
const strand = { id: 's', polymer: 'DNA', copy_ids: ['c'], residues: [{ id: 'r1', base: 'A' }, { id: 'r2', base: 'C' }], five_prime: 'hydroxyl', three_prime: 'hydroxyl' };
const initial = (): MolecularConstructionDraft => ({ ...emptyConstructionDraft(), editor: { graph_provider: 'rdkit', phosphate_state: 'neutral_acid', geometry: 'unpositioned', strands: [structuredClone(strand)] } });
let schemas: unknown;
let graph: ConstructionGraph;
let modification: Record<string, unknown>;
let root: Root;
let host: HTMLDivElement;
let latest: MolecularConstructionDraft;
let calls: Array<{ method: string; url: string; body?: string }> = [];
let intercept: ((url: string, body?: string) => Promise<unknown> | undefined) | undefined;
const originalAdapter = api.defaults.adapter;
beforeAll(() => {
    schemas = native('GET', '/api/molecular-construction/schema').data;
    graph = native('POST', '/api/molecular-construction/graph', JSON.stringify(initial().editor)).data;
    // Native CCD correspondence, not invented component chemistry or canned response.
    modification = JSON.parse(execFileSync(python!, ['-c', `
import json
from biotite.structure.info import get_from_ccd
a = get_from_ccd('chem_comp_atom', '5CM')
names = [str(n) for n, e in zip(a['atom_id'].as_array(str), a['type_symbol'].as_array(str)) if e not in ('H', 'D') and n != 'OP3']
print(json.dumps(dict(id='methyl', kind='base', strand_id='s', copy_id='c', residue_ids=['r2'], ccd_id='5CM', omit_atoms=['OP3'], atom_map={n:dict(residue_id='r2',atom_name=n) for n in names})))
`], { encoding: 'utf8' }));
});
afterEach(async () => { if (root) await act(async () => root.unmount()); host?.remove(); api.defaults.adapter = originalAdapter; intercept = undefined; calls = []; });
async function mount(draft = initial()) {
    latest = structuredClone(draft);
    api.defaults.adapter = async config => {
        const url = config.url!, method = config.method!.toUpperCase();
        const body = typeof config.data === 'string' ? config.data : undefined;
        calls.push({ method, url, body });
        const held = intercept?.(url, body);
        const result = held ? { status: 200, data: await held } : url.endsWith('/schema') ? { status: 200, data: schemas } : native(method, url, body);
        if (result.status >= 400) throw new Error(`HTTP ${result.status}: ${JSON.stringify(result.data)}`);
        return { ...result, config, headers: {}, statusText: String(result.status) };
    };
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<MolecularConstructionWorkbench initialDraft={draft} onDraftChange={value => { latest = value; }} />));
}
async function click(text: string) {
    const button = [...host.querySelectorAll('button')].find(b => b.textContent === text);
    expect(button, text).toBeTruthy();
    await act(async () => button!.click());
}
async function input(label: string, value: string) {
    const element = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(element, label).toBeTruthy();
    await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!;
        setter.call(element, value); element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}

describe('real React + Axios + isolated native ASGI molecular construction', () => {
    it('discovers actual closed Pydantic definitions and native choices', async () => {
        await mount();
        const contract = schemas as any;
        expect(contract.recipe.additionalProperties).toBe(false);
        expect(contract.recipe.$defs.Strand.properties.polymer.enum).toEqual(['DNA', 'RNA']);
        expect(contract.complement.$defs.Strand).toBeDefined();
        expect(host.textContent).toContain('Base / sugar / linkage / terminal');
        expect(host.querySelector('[aria-label="Strand 1 polymer"]')!.textContent).toContain('RNA');
        expect(calls.map(c => c.url)).toEqual(['/api/molecular-construction/schema']);
    });
    it('authors blank required drafts durably and does not manufacture defaults or send an implicit graph request', async () => {
        await mount(emptyConstructionDraft());
        await click('Add independently authored strand');
        expect(latest.editor.strands).toEqual([{ id: '', polymer: '', residues: [], copy_ids: [], five_prime: '', three_prime: '' }]);
        expect(latest.editor.graph_provider).toBeUndefined(); expect(latest.editor.phosphate_state).toBeUndefined();
        expect(calls.every(c => c.method === 'GET')).toBe(true);
        const retained = JSON.parse(JSON.stringify(latest));
        expect(retained.editor.strands[0].id).toBe('');
    });
    it('replays exact authored recipe through the actual native graph route and inspects atoms/bonds/stereo', async () => {
        await mount(); await click('Build connected graph');
        const sent = calls.find(c => c.url.endsWith('/graph'))!;
        expect(JSON.parse(sent.body!)).toEqual(initial().editor);
        expect(latest.graph!.recipe.strands).toEqual([{ ...strand, nicks: [] }]);
        expect(latest.graph!.coordinates).toBeNull();
        expect(latest.graph!.atoms.some(a => a.cip === 'R' || a.cip === 'S')).toBe(true);
        expect(latest.graph!.bonds.some(b => b.order === 2)).toBe(true);
        expect(latest.graph!.connected_components).toHaveLength(1);
        expect(host.textContent).toContain(`${latest.graph!.atoms.length} atoms`);
        expect(host.querySelector('iframe, canvas')).toBeNull();
        expect(JSON.stringify(latest)).not.toContain('native_params');
    });
    it('retains empty required drafts without sending, never converts T/U or creates a second strand', async () => {
        await mount();
        await input('Strand 1 sequence', 'AT'); await input('Strand 1 polymer', 'RNA');
        expect((latest.editor.strands as any[])[0].residues.map((r: any) => r.base).join('')).toBe('AT');
        expect((latest.editor.strands as any[])).toHaveLength(1);
        await input('Strand 1 sequence', '');
        expect((latest.editor.strands as any[])[0].residues).toEqual([]);
        expect(calls.every(c => c.method === 'GET')).toBe(true);
        await click('Build connected graph');
        expect(host.textContent).toContain('HTTP 422');
        expect(host.textContent).toContain('model launch is unchanged');
        expect(latest.graph).toBeUndefined();
    });
    it('Undo restores exact retained graph+recipe and unknown companions survive cold JSON reopen', async () => {
        const draft = { ...initial(), graph: { ...graph, future_graph_annotation: { exact: [0, false, null] } }, imported_future: { untouched: true } };
        await mount(JSON.parse(JSON.stringify(draft)));
        await input('Strand 1 sequence', 'AG');
        expect(latest.graph).toBeUndefined();
        expect(latest.undo.at(-1)!.graph).toEqual(draft.graph);
        await click('Undo construction edit');
        expect(latest.editor).toEqual(draft.editor); expect(latest.graph).toEqual(draft.graph);
        expect(latest.imported_future).toEqual({ untouched: true });
        expect(JSON.parse(JSON.stringify(latest)).graph).toEqual(draft.graph);
    });
    it('rejects stale graph response across A→B→Undo A even with identical final recipe', async () => {
        let resolve!: (g: ConstructionGraph) => void;
        intercept = url => url.endsWith('/graph') ? new Promise(r => { resolve = r; }) : undefined;
        await mount(); await click('Build connected graph');
        await input('Strand 1 sequence', 'AG'); await click('Undo construction edit');
        expect(latest.editor).toEqual(initial().editor);
        await act(async () => resolve(graph));
        expect(latest.graph).toBeUndefined();
        expect(host.textContent).toContain('Exact recipe and graph snapshot restored');
    });
    it('explicit complement alone appends a native canonical strand without pairing inference', async () => {
        const draft = initial();
        draft.complement = { source: strand, new_strand_id: 'second', new_copy_ids: ['other'], new_residue_ids: ['b1', 'b2'], five_prime: 'hydroxyl', three_prime: 'phosphate_neutral' };
        await mount(draft); await click('Create explicit complement');
        expect(calls.filter(c => c.url.endsWith('/complement'))).toHaveLength(1);
        const strands = latest.editor.strands as any[];
        expect(strands).toHaveLength(2); expect(strands[1].residues.map((r: any) => r.base).join('')).toBe('GT');
        expect(strands[1].three_prime).toBe('phosphate_neutral'); expect(latest.editor.pairing).toBeUndefined();
        await click('Build connected graph'); expect(latest.graph!.connected_components).toHaveLength(2);
        await click('Undo construction edit'); expect(latest.editor).toEqual(draft.editor);
    });
    it('discards a complement response after unrelated edits and retains unsent complement intent', async () => {
        let resolve!: (g: unknown) => void;
        intercept = url => url.endsWith('/complement') ? new Promise(r => { resolve = r; }) : undefined;
        const draft = initial(); draft.complement = { source: strand, new_strand_id: '' };
        await mount(draft); await click('Create explicit complement'); await input('Strand 1 sequence', 'GG');
        await act(async () => resolve({ ...strand, id: 'late' }));
        expect(latest.editor.strands).toHaveLength(1); expect(latest.complement).toEqual(draft.complement);
    });
    it('authors copy/end choices through primary controls and replays ssRNA without a duplex', async () => {
        await mount();
        await input('Strand 1 polymer', 'RNA'); await input('Strand 1 sequence', 'AU');
        await input('Strand 1 five_prime', 'phosphate_neutral');
        await input('Strand 1 copies[0]', 'named-copy');
        await click('Build connected graph');
        expect(latest.graph!.recipe.strands[0].polymer).toBe('RNA');
        expect(latest.graph!.recipe.strands[0].copy_ids).toEqual(['named-copy']);
        expect(latest.graph!.recipe.strands[0].five_prime).toBe('phosphate_neutral');
        expect(latest.graph!.recipe.strands).toHaveLength(1);
        expect(latest.graph!.connected_components).toHaveLength(1);
    });
    it('edits actual native CCD modification mapping through typed $defs controls and Undo restores exact modified graph', async () => {
        const draft = initial(); draft.editor.replacements = [modification];
        await mount(draft); await click('Build connected graph');
        expect(latest.graph!.atoms.some(a => a.component_id === '5CM' && a.native_atom_name === 'C5A')).toBe(true);
        const modified = structuredClone(latest.graph);
        await input('replacements[0].atom_map.C5A.atom_name', 'methyl-carbon');
        expect(latest.graph).toBeUndefined();
        await click('Build connected graph');
        expect(latest.graph!.atoms.some(a => a.id.endsWith(':methyl-carbon'))).toBe(true);
        expect(latest.graph!.recipe.replacements![0].atom_map.C5A.atom_name).toBe('methyl-carbon');
        await click('Undo construction edit'); expect(latest.graph).toEqual(modified);
    });
    it('schema/graph transport failures are advisory with editable draft retained', async () => {
        intercept = url => url.endsWith('/schema') ? Promise.reject(new Error('offline schema')) : undefined;
        await mount(); expect(host.textContent).toContain('Schema discovery unavailable');
        expect((host.querySelector('button') as HTMLButtonElement).disabled).toBe(false);
        intercept = url => url.endsWith('/graph') ? Promise.reject(new Error('provider missing')) : undefined;
        await click('Build connected graph');
        expect(host.textContent).toContain('provider missing');
        expect(host.textContent).toContain('model launch is unchanged');
        expect(latest.editor).toEqual(initial().editor);
    });
    it('rejects response after workbench unmount without calling parent', async () => {
        let resolve!: (g: ConstructionGraph) => void;
        intercept = url => url.endsWith('/graph') ? new Promise(r => { resolve = r; }) : undefined;
        await mount(); await click('Build connected graph');
        await act(async () => root.unmount());
        await act(async () => resolve(graph));
        expect(latest.graph).toBeUndefined();
    });
    it('preserves explicit copy/end/pair/nick intent and native mismatch table on exact graph replay', async () => {
        const draft = initial();
        draft.editor.strands = [{ ...strand, copy_ids: ['c', 'copy2'], five_prime: 'phosphate_neutral', nicks: [{ after: 'r1', left_three_prime: 'hydroxyl', right_five_prime: 'hydroxyl' }] }];
        draft.editor.pairing = [{ id: 'manual', orientation: 'parallel', pairs: [{ first: { strand_id: 's', copy_id: 'c', residue_id: 'r1' }, second: { strand_id: 's', copy_id: 'copy2', residue_id: 'r2' }, relation: 'mismatch' }] }];
        await mount(draft); await click('Build connected graph');
        expect(latest.graph!.recipe.pairing).toEqual(draft.editor.pairing);
        expect(latest.graph!.recipe.strands).toEqual(draft.editor.strands);
        expect(latest.graph!.connected_components).toHaveLength(4);
    });
});
