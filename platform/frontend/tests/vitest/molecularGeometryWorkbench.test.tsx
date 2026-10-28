import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { api } from '../../src/lib/api';
import { MolecularConstructionWorkbench } from '../../src/components/MolecularConstructionWorkbench';
import { emptyConstructionDraft } from '../../src/lib/molecularConstruction';
import type { MolecularConstructionDraft, MolecularGeometryResult } from '../../src/lib/molecularConstruction';
const repo = path.resolve('../..');
const python = process.env.BMS_CONSTRUCTION_PYTHON;
if (!python || !process.env.BMS_MOLECULAR_GEOMETRY_BUNDLE) throw new Error('Explicit isolated interpreter and real geometry bundle required');
function native(method: string, url: string, body?: string) {
    return JSON.parse(execFileSync(python!, [path.join(repo, 'platform/api/tests/molecular_geometry_ui_transport.py')], { input: JSON.stringify({ method, url, body }), encoding: 'utf8', env: { ...process.env, PYTHONPATH: `${repo}:${repo}/platform/api` } }));
}
const choices = { operation: 'create_new_idealized_geometry', provider: 'ambertools24_fd_helix', form: 'abdna', topology: 'canonical_antiparallel_duplex', hydrogen_policy: 'heavy_atom_coordinates_graph_implicit_hydrogens' } as const;
const strand = (id: string, base: string) => ({ id, polymer: 'DNA', copy_ids: ['copy'], residues: [{ id: 'r1', base }], five_prime: 'hydroxyl', three_prime: 'hydroxyl' });
const editor = { graph_provider: 'rdkit', phosphate_state: 'neutral_acid', geometry: 'unpositioned', strands: [strand('first', 'A'), strand('second', 'T')], pairing: [{ id: 'duplex', orientation: 'antiparallel', pairs: [{ first: { strand_id: 'first', copy_id: 'copy', residue_id: 'r1' }, second: { strand_id: 'second', copy_id: 'copy', residue_id: 'r1' }, relation: 'canonical' }] }] };
const initial = (): MolecularConstructionDraft => ({ ...emptyConstructionDraft(), editor: structuredClone(editor), imported_source: { coordinate_text: 'retained original bytes', future: [null, 0, false] } });
let graphSchema: unknown, geometrySchema: any, real: MolecularGeometryResult;
let root: Root, host: HTMLDivElement, latest: MolecularConstructionDraft;
let positioned: MolecularGeometryResult[] = [];
let calls: Array<{ method: string; url: string; body?: string }> = [];
let intercept: ((url: string) => Promise<unknown> | undefined) | undefined;
const original = api.defaults.adapter;
beforeAll(() => {
    graphSchema = native('GET', '/api/molecular-construction/schema').data;
    geometrySchema = native('GET', '/api/molecular-geometry/schema').data;
    const response = native('POST', '/api/molecular-geometry/construct', JSON.stringify({ ...choices, construction: editor }));
    expect(response.status).toBe(200); real = response.data;
});
afterEach(async () => { if (root) await act(async () => root.unmount()); host?.remove(); api.defaults.adapter = original; calls = []; positioned = []; intercept = undefined; });
async function mount(draft = initial()) {
    latest = structuredClone(draft);
    api.defaults.adapter = async config => {
        const url = config.url!, method = config.method!.toUpperCase(), body = typeof config.data === 'string' ? config.data : undefined;
        calls.push({ method, url, body });
        const held = intercept?.(url);
        const result = held ? { status: 200, data: await held } : url.endsWith('/schema') ? { status: 200, data: url.includes('geometry') ? geometrySchema : graphSchema } : native(method, url, body);
        if (result.status >= 400) throw new Error(`HTTP ${result.status}: ${JSON.stringify(result.data)}`);
        return { ...result, config, headers: {}, statusText: String(result.status) };
    };
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<MolecularConstructionWorkbench initialDraft={draft} onDraftChange={d => { latest = d; }} onPositioned={r => positioned.push(r)} />));
}
async function click(text: string) { const b = [...host.querySelectorAll('button')].find(b => b.textContent === text); expect(b).toBeTruthy(); await act(async () => b!.click()); }
async function input(label: string, value: string) {
    const e = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement;
    expect(e).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(e, value); e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
async function configure() {
    await click('Configure optional geometry');
    for (const [key, label] of Object.entries({ operation: 'Geometry operation', provider: 'Geometry provider', form: 'Helix form', topology: 'Geometry topology', hydrogen_policy: 'Hydrogen policy' })) await input(label, choices[key as keyof typeof choices]);
}
it('discovers actual choices without defaults, requests, complements or source mutations', async () => {
    await mount(); await click('Configure optional geometry');
    expect(geometrySchema.request.additionalProperties).toBe(false);
    expect(geometrySchema.request.$defs.MolecularConstructionRecipe).toBeDefined();
    expect([...host.querySelectorAll('[aria-label="Optional idealized geometry"] select')].every(s => (s as HTMLSelectElement).value === '')).toBe(true);
    expect([...host.querySelectorAll('[aria-label="Helix form"] option')].slice(1).map(o => o.getAttribute('value'))).toEqual(geometrySchema.request.properties.form.enum);
    expect(calls.every(c => c.method === 'GET')).toBe(true); expect(latest.geometry).toBeUndefined();
    for (const text of ['No ssDNA/ssRNA folding', 'mismatches', 'modifications', 'Imported geometry is unchanged', 'not adopt a source']) expect(host.textContent).toContain(text);
});
it('posts exact explicit request to native route and retains exact bytes/hash/map and callback', async () => {
    await mount(); await configure(); expect(calls.every(c => c.method === 'GET')).toBe(true);
    await click('Create new idealized geometry');
    expect(JSON.parse(calls.find(c => c.url.endsWith('/construct'))!.body!)).toEqual({ ...choices, construction: editor });
    expect(latest.geometry!.request).toEqual({ ...choices, construction: editor });
    expect(latest.geometry!.result).toEqual(real); expect(positioned).toEqual([real]);
    expect(createHash('sha256').update(latest.geometry!.result!.coordinate_text, 'ascii').digest('hex')).toBe(real.coordinate_sha256);
    expect(latest.imported_source).toEqual(initial().imported_source); expect(latest.editor).toEqual(editor);
});
it('cold JSON reopen and Undo retain unknown state and exact request/result without callback', async () => {
    const draft = { ...initial(), geometry: { choices, request: { ...choices, construction: editor }, result: { ...real, future_result: [0, false, null] }, future_geometry: { exact: true } } };
    await mount(JSON.parse(JSON.stringify(draft)));
    expect(latest).toEqual(draft); expect(positioned).toEqual([]); expect(calls.every(c => c.method === 'GET')).toBe(true);
    expect(host.textContent).toContain(real.coordinate_sha256);
    await input('Helix form', 'adna'); expect(latest.geometry!.result).toBeUndefined(); expect(latest.undo.at(-1)!.geometry).toEqual(draft.geometry);
    await click('Undo construction edit'); expect(latest.geometry).toEqual(draft.geometry); expect(positioned).toEqual([]);
    await input('Strand 1 sequence', 'G'); expect(latest.geometry!.result).toBeUndefined();
    await click('Undo construction edit'); expect(latest.geometry).toEqual(draft.geometry); expect(latest.editor).toEqual(editor);
});
it('sends incomplete choices unchanged and reports native validation without defaults or duplex repair', async () => {
    const draft = initial(); draft.editor.strands = [strand('first', 'A')]; draft.editor.pairing = [];
    await mount(draft); await click('Configure optional geometry'); await click('Create new idealized geometry');
    expect(JSON.parse(calls.find(c => c.url.endsWith('/construct'))!.body!)).toEqual({ construction: draft.editor });
    expect(host.textContent).toContain('HTTP 422'); expect(host.textContent).toContain('model launch are unchanged');
    expect(positioned).toEqual([]); expect(latest.editor).toEqual(draft.editor);
    await click('Build connected graph'); expect(latest.graph).toBeDefined();
});
it('runtime and discovery errors are advisory with graph and imported geometry retained', async () => {
    intercept = url => url.includes('geometry') ? Promise.reject(new Error('optional runtime unavailable 503')) : undefined;
    await mount({ ...initial(), geometry: { choices } }); expect(host.textContent).toContain('Geometry schema unavailable');
    await click('Create new idealized geometry'); expect(host.textContent).toContain('Geometry unavailable');
    expect(latest.geometry!.request).toEqual({ ...choices, construction: editor }); expect(positioned).toEqual([]);
    await click('Build connected graph'); expect(latest.graph).toBeDefined(); expect(latest.imported_source).toEqual(initial().imported_source);
});
it('rejects delayed A→B→Undo A despite identical choices', async () => {
    let resolve!: (r: unknown) => void;
    intercept = url => url.endsWith('/construct') ? new Promise(r => { resolve = r; }) : undefined;
    await mount({ ...initial(), geometry: { choices } }); await click('Create new idealized geometry');
    await input('Helix form', 'adna'); await click('Undo construction edit');
    await act(async () => resolve(real)); expect(latest.geometry!.result).toBeUndefined(); expect(positioned).toEqual([]);
    expect(latest.geometry!.choices).toEqual(choices);
});
it('rejects old-response-last after recipe edits and a newer successful request', async () => {
    let resolve!: (r: unknown) => void;
    intercept = url => url.endsWith('/construct') ? new Promise(r => { resolve = r; }) : undefined;
    await mount({ ...initial(), geometry: { choices } }); await click('Create new idealized geometry');
    await input('Strand 1 sequence', 'G'); await click('Undo construction edit'); intercept = undefined;
    await click('Create new idealized geometry'); expect(positioned).toEqual([real]);
    await act(async () => resolve(real)); expect(positioned).toEqual([real]); expect(latest.geometry!.result).toEqual(real);
});
it('discards delayed native result on unmount', async () => {
    let resolve!: (r: unknown) => void;
    intercept = url => url.endsWith('/construct') ? new Promise(r => { resolve = r; }) : undefined;
    await mount({ ...initial(), geometry: { choices } }); await click('Create new idealized geometry');
    await act(async () => root.unmount()); await act(async () => resolve(real)); expect(positioned).toEqual([]); expect(latest.geometry!.result).toBeUndefined();
});
