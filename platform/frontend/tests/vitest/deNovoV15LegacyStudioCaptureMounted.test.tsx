import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { createHash, webcrypto } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { deNovoAuthoringEnvelope } from '../../src/lib/deNovoAuthoring';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { DeNovoDraftPreparationContext, type DeNovoDraftPreparation } from '../../src/lib/deNovoDocumentCapture';

// Real parent/legacy serializer, shared editor/registry, Studio bridge and CIF
// custody helper. Transport and expensive visualization only are inert.
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/TargetAntigenSelector', () => ({ TargetAntigenSelector: () => null }));
vi.mock('../../src/lib/bindcraft2StructureInputs', () => ({ createBC2SourceSession: () => ({ read: async () => ({ models: [] }) }) }));
const python = process.env.BMS_TEST_PYTHON ?? 'python';
const inventory = JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'inventory'], { cwd: '../..', encoding: 'utf8' }));
const pdb = 'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \nATOM      2  CA  GLY A   2       4.000   5.000   6.000  1.00 20.00           C  \nEND\n';
const cif = execFileSync(python, ['-c', 'import sys,io;from Bio.PDB import MMCIFIO,PDBParser;w=MMCIFIO();w.set_structure(PDBParser(QUIET=True).get_structure("fixture",io.StringIO(sys.stdin.read())));w.save(sys.stdout)'], { input: pdb, encoding: 'utf8' });
const frozen = cif + '\n# frozen Studio transport fixture\n';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const conditioning = { select_hotspots: null, plddt_enhanced: false, ori_token: [0, 0, 0] };
let root: Root | undefined, query: QueryClient, prep: DeNovoDraftPreparation, latest: any;
let previous: typeof api.defaults.adapter, unexpected: string[], calls: Array<{ url: string; body: any }>;
let exports: number, fail: boolean, checkedFail: boolean, deferred: (() => Promise<any>) | undefined;
let uploads: Array<{ path: string; text: string; sha256: string }>;
const readBlob = (blob: Blob): Promise<string> => new Promise((yes, no) => { const reader = new FileReader(); reader.onload = () => yes(String(reader.result)); reader.onerror = no; reader.readAsText(blob); });
const settle = () => act(async () => { await new Promise(yes => setTimeout(yes, 10)); });
async function wait(check: () => void) { await vi.waitFor(async () => { await settle(); check(); }); }
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLElement>('button,summary')].find(n => !n.closest('[hidden]') && (n.textContent?.trim() === text || n.getAttribute('aria-label') === text));
    expect(node, text).toBeTruthy();
    await act(async () => { if (node!.tagName === 'SUMMARY') { const d = node!.parentElement as HTMLDetailsElement; d.open = true; d.dispatchEvent(new Event('toggle')); } else node!.click(); });
    await settle();
}
async function edit(label: string, value: string) {
    const node = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!; expect(node).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); await settle();
}
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); }
async function mount(generator: string, extra: Record<string, unknown> = {}) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    const values = { generator, modification_mode: 'de_novo_design', min_length: 100, max_length: 100, target_lengths: '100', sequence_design: null, sequence_design_null: true,
        ...(generator === 'rfd3' ? { input_structure: 'inputs/original.cif', source_model: 1, conditioning } : generator === 'laproteina' ? { design_task: 'motif_scaffolding', laproteina_preset: 'motif_idx_aa', laproteina_motif_pdb: 'inputs/original.pdb', laproteina_contig_string: '10/A2/10', laproteina_motif_task_name: 'retained-task', pcad_laproteina_settings_json: { motif_atom_spec: null, num_steps: 0, future_option: false } } : { pcad_disco_jobs_json: [], pcad_disco_jobs_source_path: 'inputs/original/jobs.json' }), ...extra };
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter><DeNovoDraftPreparationContext.Provider value={prep}><ProteinModificationTemplate initialValues={values} onBack={() => {}} onDraftChange={value => { latest = value; }} /></DeNovoDraftPreparationContext.Provider></MemoryRouter></QueryClientProvider>)); await settle();
}
async function open(generator: string) {
    if (generator === 'rfd3') { await click('Input structure (optional)'); await click('Edit input in AtomWorks Studio'); }
    else await click('Edit motif source in Studio');
    await wait(() => expect(document.querySelector('iframe[title="AtomWorks Studio"]')).not.toBeNull());
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); previous = api.defaults.adapter; prep = { current: null }; unexpected = []; calls = []; uploads = []; exports = 0; fail = false; checkedFail = false; deferred = undefined;
    localStorage.clear(); sessionStorage.clear();
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw Error('No network'); });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
        const url = String(input);
        if (url.startsWith('/api/files/download/')) { const text = url.includes('.pdb') ? pdb : url.includes('annotated_structures') ? frozen : cif; return { ok: true, arrayBuffer: async () => new TextEncoder().encode(text).buffer } as Response; }
        if (url === '/api/atomworks-studio/api/v1/sessions' && options?.method === 'POST') return { ok: true, json: async () => ({ session_id: 'owned', structure: { revision: 0 } }) } as Response;
        if (url === '/api/atomworks-studio/api/v1/sessions/owned/export') { exports++; if (deferred) return deferred(); return { ok: !fail, status: fail ? 502 : 200, json: async () => ({ cif: frozen, revision: 4, issues: [] }) } as Response; }
        unexpected.push(url); throw Error(`Unexpected ${url}`);
    });
    api.defaults.adapter = async config => {
        const url = String(config.url), body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data; calls.push({ url, body }); let data: any;
        if (url === '/api/models/protein_modification_experimental') data = { engine_references: {} };
        else if (url.endsWith('/generation-settings')) data = inventory;
        else if (url === '/api/files/materialize-structure') {
            if (body.output_format === 'pdb' && checkedFail) throw Error('No lossless PDB representation for this document');
            const isPdb = body.output_format === 'pdb' || body.path.endsWith('.pdb'); const path = body.output_format === 'pdb' ? 'inputs/checked.pdb' : body.path;
            data = { path, format: isPdb ? 'pdb' : 'cif', sha256: hash(isPdb ? pdb : body.path.includes('annotated_structures') ? frozen : cif), source_structure: body, source_identity: {}, model_number: body.model_number ?? null, model_numbers: [1], source_positions: [], author_residues: [] };
        } else if (url === '/api/files/upload-immutable-structure' || url === '/api/files/upload') {
            if (fail) throw Error('immutable disk unavailable');
            const form = body as FormData, file = form.get('file') as File, text = await readBlob(file), path = `${form.get('path')}/${file.name}`;
            uploads.push({ path, text, sha256: hash(text) }); data = { path, sha256: hash(text), size: new TextEncoder().encode(text).byteLength, existing: false };
        } else if (url === '/api/jobs') data = { id: 'inert' };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { await close(); api.defaults.adapter = previous; vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected).toEqual([]); });
it.each(['rfd3', 'laproteina'])('%s captures Save twice, Generate and cold prepare with native representations', async generator => {
    await mount(generator); await open(generator);
    await act(async () => { await prep.current!(); });
    expect(exports).toBe(1);
    const first = structuredClone(latest);
    const params = first.native_job_request.params;
    if (generator === 'rfd3') {
        expect(params).toMatchObject({ input_structure: uploads[0].path, source_model: 1, conditioning });
        expect(params).not.toHaveProperty('native_sources');
    } else {
        expect(params.pcad_laproteina_settings_json).toMatchObject({ motif_pdb: uploads[1].path, motif_atom_spec: null, num_steps: 0, future_option: false });
        expect(uploads[1].text).toContain('GLY A   2'); expect(uploads[1].text).not.toContain('ALA A   1');
        expect(params.laproteina_motif_task_name).toBe('retained-task');
    }
    expect(params).not.toHaveProperty('molecular_documents');
    await act(async () => { await prep.current!(); }); expect(exports).toBe(2);
    await click('Generate candidates'); expect(exports).toBe(3);
    expect(calls.filter(call => call.url === '/api/jobs').at(-1)!.body.params).toEqual(latest.native_job_request.params);
    if (process.env.BMS_LEGACY_CAPTURE_DIR) writeFileSync(resolve(process.env.BMS_LEGACY_CAPTURE_DIR, `${generator}.json`), JSON.stringify({ envelope: deNovoAuthoringEnvelope(latest), uploads, pdb, cif: frozen }));
    const saved = structuredClone(latest); await close(); await mount(generator, saved);
    await act(async () => { await prep.current!(); }); expect(exports).toBe(3);
    expect(latest.native_job_request).toEqual(saved.native_job_request);
    expect(latest.molecular_documents).toEqual(saved.molecular_documents);
});
it.each(['rfd3', 'laproteina'])('%s failure retains editor and prevents stale Generate; retry recovers', async generator => {
    await mount(generator); await open(generator); fail = true;
    await click('Generate candidates'); expect(calls.filter(call => call.url === '/api/jobs')).toEqual([]);
    expect(document.querySelector('iframe')).not.toBeNull(); fail = false;
    await click('Generate candidates'); expect(calls.filter(call => call.url === '/api/jobs')).toHaveLength(1);
});
it('La source A→B→A during export is fenced and leaves the manual source intact', async () => {
    await mount('laproteina'); await open('laproteina'); let finish!: (value: any) => void;
    deferred = () => new Promise(resolve => { finish = resolve; });
    let result!: Promise<unknown>; await act(async () => { result = prep.current!().catch(error => error); });
    await edit('Motif PDB path', 'inputs/replaced.pdb'); await edit('Motif PDB path', 'inputs/original.pdb');
    await act(async () => { finish({ ok: true, json: async () => ({ cif: frozen, revision: 4 }) }); await result; });
    expect(await result).toBeInstanceOf(Error); expect(latest.native_job_request.params.laproteina_motif_pdb).toBe('inputs/original.pdb');
});
it('RFD3 source replacement during export cannot overwrite a cleared native input', async () => {
    await mount('rfd3'); await open('rfd3'); let finish!: (value: any) => void;
    deferred = () => new Promise(resolve => { finish = resolve; });
    let result!: Promise<unknown>; await act(async () => { result = prep.current!().catch(error => error); });
    await click('Clear input structure');
    await act(async () => { finish({ ok: true, json: async () => ({ cif: frozen, revision: 4 }) }); await result; });
    expect(await result).toBeInstanceOf(Error);
    expect(latest.native_job_request.params).not.toHaveProperty('input_structure');
    expect(latest.native_job_request.params.conditioning).toEqual(conditioning);
    expect(document.body.textContent).toContain('replaced source');
});
it('La unsupported checked projection retains recovery without changing manual/named task requests', async () => {
    await mount('laproteina'); await open('laproteina'); const before = structuredClone(latest.native_job_request);
    checkedFail = true; await click('Generate candidates');
    expect(calls.filter(call => call.url === '/api/jobs')).toEqual([]);
    expect(latest.native_job_request).toEqual(before);
    expect(document.body.textContent).toContain('No lossless PDB representation');
    await click('Close editor'); await click('Generate candidates');
    expect(calls.filter(call => call.url === '/api/jobs').at(-1)!.body.params).toEqual(before.params);
});
it('capture serializes the latest native contig authored while Studio export is pending', async () => {
    await mount('laproteina'); await open('laproteina'); let finish!: (value: any) => void;
    deferred = () => new Promise(resolve => { finish = resolve; });
    let result!: Promise<unknown>; await act(async () => { result = prep.current!(); });
    await edit('Motif contig', '5/A1/5');
    await act(async () => { finish({ ok: true, json: async () => ({ cif: frozen, revision: 4 }) }); await result; });
    expect(latest.native_job_request.params.pcad_laproteina_settings_json.contig_string).toBe('5/A1/5');
    expect(uploads.at(-1)!.text).toContain('ALA A   1'); expect(uploads.at(-1)!.text).not.toContain('GLY A   2');
});
it.each(['rfd3', 'laproteina'])('%s untouched source-free/manual prepare does not require Studio', async generator => {
    await mount(generator, generator === 'rfd3' ? { input_structure: undefined, source_model: undefined } : { laproteina_motif_pdb: '', laproteina_motif_task_name: 'retained-task' });
    const before = structuredClone(latest.native_job_request);
    await act(async () => { await prep.current!(); }); await click('Generate candidates');
    expect(exports).toBe(0); expect(uploads).toEqual([]);
    expect(calls.filter(call => call.url === '/api/jobs').at(-1)!.body.params).toEqual(before.params);
});
it('DISCO explicit unknown fields, integer bonds and FILE_ owner are not projected from a structure', async () => {
    const jobs = [{ name: 'explicit', future: null, sequences: [{ ligand: { ligand: 'FILE_ligand.pdb', count: 1 }, extra: false }], covalent_bonds: [{ left_entity: 1, left_position: 1, left_atom: 0, right_entity: 1, right_position: 1, right_atom: 'C1' }] }];
    await mount('disco', { pcad_disco_jobs_json: jobs });
    await act(async () => { await prep.current!(); });
    expect(latest.native_job_request.params.pcad_disco_jobs_json).toEqual(jobs);
    expect(latest.native_job_request.params.pcad_disco_jobs_source_path).toBe('inputs/original/jobs.json');
    expect(exports).toBe(0);
});
it('DISCO explicit empty jobs and original FILE_ root survive prepare without capture or structural translation', async () => {
    await mount('disco'); const before = structuredClone(latest.native_job_request);
    await act(async () => { await prep.current!(); });
    expect(latest.native_job_request).toEqual(before); expect(exports).toBe(0);
    expect(before.params.pcad_disco_jobs_json).toEqual([]);
    expect(before.params.pcad_disco_jobs_source_path).toBe('inputs/original/jobs.json');
});
