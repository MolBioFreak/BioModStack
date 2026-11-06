import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { createHash, webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { deNovoAuthoringEnvelope } from '../../src/lib/deNovoAuthoring';
import { api } from '../../src/lib/api';
import { DeNovoNativeGenerationSettings } from '../../src/components/DeNovoNativeGenerationSettings';
import { DeNovoDraftPreparationContext, type DeNovoDraftPreparation } from '../../src/lib/deNovoDocumentCapture';

// Real native form, source rows, Studio iframe/bridge, capture/upload helpers and
// preview/Job Axios serialization. Only transport, WebGL and unrelated peers are
// inert. This is NOT a real Studio parser/browser or server persistence claim.
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/FileBrowser', () => ({ FileBrowser: ({ onSelect }: any) => <button onClick={() => onSelect('inputs/replaced.cif')}>Select replacement fixture</button> }));
const inventory = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import json,sys;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;print(json.dumps(generation_settings('generate')))"], { cwd: resolve('../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
const originalCif = readFileSync(resolve('tests/fixtures/rfd4-saved-conditions.cif'), 'utf8');
const frozenCif = originalCif + '\n# frozen edited snapshot α\n';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const original = { path: 'inputs/original.cif', expected_sha256: hash(originalCif), model_number: 1, output_format: 'native' as const };
const scientific = { input: { kind: 'annotated', slots: ['source'] }, settings: { seed: 0, compress_outputs: false } };
let root: Root | undefined, query: QueryClient, previous: typeof api.defaults.adapter;
let prep: DeNovoDraftPreparation, latest: any, unexpected: string[], calls: Array<{ url: string; body: any }>;
let exports: number, imports: number, failExport: boolean, failUpload: boolean, mismatch: boolean;
let exportReply: (() => Promise<any>) | undefined;
let exportCif: string;
let uploaded: Array<{ path: string; sha256: string; text: string }>;
function receipt(source: any) {
    const edited = String(source.path).includes('annotated_structures');
    return { path: source.path, source_structure: source, original_path: source.path, original_sha256: source.expected_sha256,
        sha256: edited ? uploaded.find(item => item.path === source.path)?.sha256 ?? hash(frozenCif) : hash(originalCif), format: 'cif', native_format: 'cif', native_path: source.path,
        native_sha256: source.expected_sha256, decoded_path: source.path, decoded_sha256: source.expected_sha256,
        stored_encoding: 'plain', selected_path: source.path, selected_sha256: source.expected_sha256,
        source_path: source.path, source_identity: {}, source_positions: [], author_residues: [], model_numbers: [1], model_number: source.model_number ?? null };
}
const readBlob = (blob: Blob): Promise<string> => new Promise((yes, no) => {
    const reader = new FileReader(); reader.onload = () => yes(String(reader.result)); reader.onerror = no; reader.readAsText(blob);
});
const settle = () => act(async () => { await new Promise(yes => setTimeout(yes, 10)); });
async function wait(check: () => void) { await vi.waitFor(async () => { await settle(); check(); }); }
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLElement>('button,summary')].find(n => !n.closest('[hidden]') && n.textContent?.trim() === text);
    expect(node, text).toBeTruthy();
    await act(async () => {
        if (node!.tagName === 'SUMMARY') { const d = node!.parentElement as HTMLDetailsElement; d.open = true; d.dispatchEvent(new Event('toggle')); }
        else node!.click();
    });
    await settle();
}
async function open() {
    const button = document.querySelector<HTMLButtonElement>('[aria-label="Edit source in AtomWorks Studio"]');
    expect(button).toBeTruthy(); await act(async () => button!.click());
    await wait(() => expect(document.querySelector('iframe[title="AtomWorks Studio"]')).not.toBeNull());
}
async function changed() {
    const frame = document.querySelector<HTMLIFrameElement>('iframe[title="AtomWorks Studio"]')!;
    await act(async () => window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, source: frame.contentWindow,
        data: { source: 'atomworks-annotation-studio', type: 'changed', session_id: 'owned' } })));
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); }
async function mount(values: Record<string, unknown> = { rfd4_request: scientific, native_sources: { source: original }, sequence_design: null, sequence_design_null: true }) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter><DeNovoDraftPreparationContext.Provider value={prep}>
        <DeNovoNativeGenerationSettings model="rfd4_proteina" initialValues={values} onDraftChange={value => { latest = value; }} />
    </DeNovoDraftPreparationContext.Provider></MemoryRouter></QueryClientProvider>));
    await wait(() => expect(document.querySelector('[data-rfd4-task-workspace]')).not.toBeNull());
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); previous = api.defaults.adapter; prep = { current: null }; latest = undefined;
    unexpected = []; calls = []; imports = 0; exports = 0; failExport = false; failUpload = false; mismatch = false; uploaded = []; exportReply = undefined; exportCif = frozenCif;
    localStorage.clear(); sessionStorage.clear();
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw Error('No network'); });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
        const url = String(input);
        if (url.startsWith('/api/files/download/')) return { ok: true,
            arrayBuffer: async () => new TextEncoder().encode(mismatch ? 'corrupted bytes' : uploaded.find(item => item.path === decodeURIComponent(url.slice('/api/files/download/'.length)))?.text ?? originalCif).buffer,
            text: async () => originalCif } as Response;
        if (url === '/api/atomworks-studio/api/v1/sessions' && options?.method === 'POST') {
            imports++; expect([originalCif, ...uploaded.map(item => item.text)]).toContain(await readBlob((options.body as FormData).get('file') as File));
            return { ok: true, json: async () => ({ session_id: 'owned', structure: { revision: 0 } }) } as Response;
        }
        if (url === '/api/atomworks-studio/api/v1/sessions/owned/export' && options?.method === 'POST') {
            exports++; if (exportReply) return exportReply();
            return { ok: !failExport, status: failExport ? 502 : 200, json: async () => ({ cif: exportCif, revision: 4, name: 'edited', issues: [{ message: 'advisory only' }] }) } as Response;
        }
        unexpected.push(`fetch ${options?.method ?? 'GET'} ${url}`); throw Error(`Unexpected ${url}`);
    });
    api.defaults.adapter = async config => {
        const url = config.url ?? ''; const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body }); let data: unknown;
        if (url === '/api/models/rfd4_proteina/generation-settings') data = inventory;
        else if (url === '/api/files/materialize-structure') data = receipt(body);
        else if (url === '/api/files/upload-immutable-structure') {
            if (failUpload) throw Error('immutable disk unavailable');
            const form = body as FormData, file = form.get('file') as File, sha256 = String(form.get('sha256'));
            const text = await readBlob(file); const path = `${form.get('path')}/${file.name}`;
            expect(text).toBe(exportCif); expect(hash(text)).toBe(sha256); uploaded.push({ path, sha256, text });
            data = { path, sha256, size: new TextEncoder().encode(text).byteLength, existing: false };
        } else if (url === '/api/jobs/execution-plan/preview') data = { plan: { requested_json: body.params, effective_json: body.params }, blockers: [] };
        else if (url === '/api/jobs') data = { id: 'inert-no-job-created' };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => { await unmount(); api.defaults.adapter = previous; vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected).toEqual([]); });

it('empty motif acquisition binds the task before parsing controls, and opens/focuses its existing editor', async () => {
    const sparse = { input: { kind: 'condition_spec', examples: [] }, settings: { seed: 0 } };
    await mount({ native_recipe: 'motif', rfd4_request: sparse, sequence_design: null });
    expect(latest.rfd4_request).toEqual(sparse);
    await click('Choose / import document'); await click('Browse files'); await click('Select replacement fixture');
    await wait(() => expect(latest.rfd4_request.input.examples[0].sources[0].slot).toBe('input_1'));
    const editor = document.querySelector<HTMLButtonElement>('[aria-label="Edit input_1 in AtomWorks Studio"]')!;
    const policy = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Parsing policy')!;
    expect(editor.compareDocumentPosition(policy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect((policy.parentElement as HTMLDetailsElement).open).toBe(false);
    const raw = document.querySelector('[aria-label="Selection expression"]')!;
    expect(raw.closest('details')?.open).toBe(false);
    expect(document.querySelector('[aria-label="Run scope"]')?.textContent).toContain('replaced.cif');
    await act(async () => editor.click());
    await wait(() => expect(document.querySelector('iframe')).not.toBeNull());
    expect(document.activeElement?.getAttribute('aria-label')).toMatch(/^Molecular editor for /);
    expect(latest.rfd4_request.settings).toEqual({ seed: 0 });
    expect(latest.rfd4_request.input.examples[0].sources).toEqual([{ slot: 'input_1' }]);
});

it('Run scope lists only enabled follow-ons in retained order while preview excludes them', async () => {
    const stage = (id: string, model_id: string) => ({ enabled: true, drafts: {}, stage: { id, kind: 'sequence_design', input: { selected: true }, models: [{ invocation_id: id + '-call', model_id, mode: 'design', params: {} }] } });
    const invocation_draft = { enabled: true, stages: [stage('first', 'proteinmpnn'), { ...stage('off', 'caliby_experimental'), enabled: false }, stage('second', 'fampnn')] };
    await mount({ rfd4_request: scientific, native_sources: { source: original }, invocation_draft });
    const summary = document.querySelector('[aria-label="Run scope"]')!.textContent!;
    expect(summary).toContain('1. proteinmpnn · design'); expect(summary).toContain('2. fampnn · design');
    expect(summary).not.toContain('caliby');
    await click('Preview settings');
    await wait(() => expect(calls.some(call => call.url === '/api/jobs/execution-plan/preview')).toBe(true));
    expect(calls.find(call => call.url === '/api/jobs/execution-plan/preview')!.body).not.toHaveProperty('sequence_design');
    expect(latest.invocation_draft).toEqual(invocation_draft);
    expect(latest.native_job_request.sequence_design.round_plan.stages.map((row: any) => row.id)).toEqual(['first', 'second']);
});

it('source-free New chains stays sparse and summarizes no task document', async () => {
    const sparse = { input: { kind: 'unconditional', name: '', chains: [] } };
    await mount({ native_recipe: 'source_free', rfd4_request: sparse, native_sources: { unused: original }, sequence_design: null });
    expect(document.body.textContent).toContain('New chains is source-free');
    expect(document.querySelector('[aria-label="Run scope"]')?.textContent).toContain('Task documents: None selected');
    expect(document.querySelector('[aria-label="Run scope"]')?.textContent).not.toContain('original.cif');
    expect(latest.rfd4_request).toEqual(sparse);
    expect(latest.rfd4_request).not.toHaveProperty('settings');
    expect(document.body.textContent).toContain('Advisory native-generation preview only');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Designs per input"]')?.value).toBe('1');
});

it('one shared editor captures exact bytes for Save and retains original/derived provenance outside science', async () => {
    await mount(); await open(); await open(); expect(imports).toBe(1); expect(document.querySelectorAll('iframe')).toHaveLength(1);
    await changed(); expect(exports).toBe(0);
    await act(async () => { await prep.current!(); });
    expect(exports).toBe(1); expect(uploaded).toHaveLength(1);
    await open(); expect(imports).toBe(1); expect(document.querySelectorAll('iframe')).toHaveLength(1);
    expect(latest.native_job_request.params.rfd4_request).toEqual(scientific);
    expect(latest.native_job_request.params.native_sources.source).toMatchObject({ path: uploaded[0].path, expected_sha256: hash(frozenCif), model_number: 1 });
    expect(latest.molecular_documents.source.original_source).toEqual(original);
    expect(latest.molecular_documents.source.provenance).toMatchObject({ revision: 4, imported_model: 1, exported_models: [1] });
    expect(latest.native_job_request.params).not.toHaveProperty('molecular_documents');
    expect(latest.molecular_documents.source.provenance).not.toHaveProperty('session_id');
    if (process.env.BMS_STUDIO_CAPTURE_WIRE) writeFileSync(process.env.BMS_STUDIO_CAPTURE_WIRE,
        JSON.stringify({ envelope: deNovoAuthoringEnvelope(latest), uploads: uploaded }));
});
it.each([false, true])('cold second edit preserves root only for the exact reopened binding (replacement=%s)', async replacement => {
    await mount(); await open(); await changed();
    expect(document.activeElement?.getAttribute('aria-label')).toMatch(/^Molecular editor for /);
    await act(async () => { await prep.current!(); });
    const saved = JSON.parse(JSON.stringify(latest));
    const parent = saved.native_sources.source;
    expect(saved.molecular_documents.source.original_source).toEqual(original);
    await unmount(); prep = { current: null };
    const reopened = replacement ? { ...original, path: 'inputs/unrelated.cif' } : Object.fromEntries(Object.entries(parent).reverse());
    await mount({ ...saved, native_sources: { source: reopened } });
    expect(document.querySelector('iframe')).toBeNull();
    await open(); await changed(); exportCif = frozenCif + '# cold second edit C\n';
    await act(async () => { await prep.current!(); });
    expect(latest.molecular_documents.source.original_source).toEqual(replacement ? reopened : original);
    expect(latest.molecular_documents.source.provenance.parent_source).toEqual(reopened);
    expect(latest.molecular_documents.source.source.expected_sha256).toBe(hash(exportCif));
    expect(latest.native_job_request.params.rfd4_request).toEqual(scientific);
    expect(latest.native_job_request.params).not.toHaveProperty('molecular_documents');
    expect(imports).toBe(2); expect(exports).toBe(2);
    const twiceSaved = JSON.parse(JSON.stringify(latest));
    await unmount(); prep = { current: null }; await mount(twiceSaved);
    await act(async () => { await prep.current!(); });
    expect(latest.molecular_documents.source).toEqual(twiceSaved.molecular_documents.source);
    expect(exports).toBe(2);
});

it.each(['export', 'upload'])('failed %s retains recovery and does not submit stale source; retry uses frozen bytes', async phase => {
    await mount(); await open(); await changed(); failExport = phase === 'export'; failUpload = phase === 'upload';
    await click('Generate candidates');
    await wait(() => expect(document.body.textContent).toContain(phase === 'export' ? '502' : 'immutable disk unavailable'));
    expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(0); expect(document.querySelector('iframe')).not.toBeNull();
    failExport = false; failUpload = false;
    await click('Generate candidates'); await wait(() => expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(1));
    expect(calls.find(c => c.url === '/api/jobs')!.body.params.native_sources.source.expected_sha256).toBe(hash(frozenCif));
});
it('Preview captures its own source update and a later dirty notification invalidates the result without changing the saved source', async () => {
    await mount(); await open(); await changed(); await click('Preview settings');
    await wait(() => expect(calls.filter(c => c.url === '/api/jobs/execution-plan/preview')).toHaveLength(1));
    await wait(() => expect(document.body.textContent).toContain('Requested and effective native values'));
    const retained = structuredClone(latest.native_sources); await changed();
    expect(latest.native_sources).toEqual(retained); expect(document.body.textContent).toContain('Settings changed. Refresh preview.');
    expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(0);
});
it('a dirty edit during frozen capture cannot submit or clear recovery', async () => {
    await mount(); await open(); let resolve!: (response: Response) => void;
    exportReply = () => new Promise<Response>(yes => { resolve = yes; });
    await click('Generate candidates'); await changed();
    await act(async () => resolve({ ok: true, json: async () => ({ cif: frozenCif, revision: 4, name: 'old', issues: [] }) } as Response));
    await wait(() => expect(document.body.textContent).toContain('document changed during capture'));
    expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(0);
    expect(document.body.textContent).toContain('Uncaptured edits');
});
it('replaced source detaches the old editor without destroying its recoverable edits or blocking manual Generate', async () => {
    await mount(); await open(); await changed();
    await click('Input structures');
    const browse = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label') === 'Browse native file for source')!;
    await act(async () => browse.click()); await click('Select replacement fixture');
    await wait(() => expect(document.body.textContent).toContain('This editor belongs to a replaced source'));
    expect(document.querySelector('iframe')).not.toBeNull();
    await click('Generate candidates'); await wait(() => expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(1));
    expect(exports).toBe(0); expect(calls.find(c => c.url === '/api/jobs')!.body.params.native_sources.source.path).toBe('inputs/replaced.cif');
});
it('source replacement during direct adoption cannot overwrite the newer binding', async () => {
    await mount(); await open(); let resolve!: (response: Response) => void;
    exportReply = () => new Promise<Response>(yes => { resolve = yes; });
    await click('Use edited document for source');
    await click('Input structures');
    const browse = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label') === 'Browse native file for source')!;
    await act(async () => browse.click()); await click('Select replacement fixture');
    await wait(() => expect(latest.native_sources.source.path).toBe('inputs/replaced.cif'));
    await act(async () => resolve({ ok: true, json: async () => ({ cif: frozenCif, revision: 4, name: 'old', issues: [] }) } as Response));
    await wait(() => expect(document.body.textContent).toContain('document changed during capture'));
    expect(latest.native_sources.source.path).toBe('inputs/replaced.cif');
    expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(0);
});

it('cold saved derivative reopens and generates without importing or contacting an editor session', async () => {
    await mount(); await open(); await act(async () => { await prep.current!(); });
    const saved = JSON.parse(JSON.stringify(latest)); await unmount(); const before = exports, imported = imports;
    await mount(saved); await act(async () => { await prep.current!(); }); await click('Generate candidates');
    await wait(() => expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(1));
    expect(exports).toBe(before); expect(imports).toBe(imported); expect(document.querySelector('iframe')).toBeNull();
    expect(latest.molecular_documents).toEqual(saved.molecular_documents);
});
it('source-byte drift refuses only the explicit editor import and leaves the ordinary source request intact', async () => {
    await mount(); mismatch = true;
    const button = document.querySelector<HTMLButtonElement>('[aria-label="Edit source in AtomWorks Studio"]')!;
    await act(async () => button.click()); await wait(() => expect(document.body.textContent).toContain('source bytes changed'));
    expect(imports).toBe(0); expect(latest.native_sources.source).toEqual(original);
    await click('Generate candidates'); await wait(() => expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(1));
});
