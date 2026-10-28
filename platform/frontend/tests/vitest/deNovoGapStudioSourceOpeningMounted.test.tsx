import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type StructureMaterialization, type StructureSourceSelection } from '../../src/lib/api';
import { DeNovoDocumentEditors, DeNovoDocumentEditorPanels, useDeNovoDocumentEditors } from '../../src/components/DeNovoDocumentEditors';
import { DocumentCaptureRegistry } from '../../src/lib/deNovoDocumentCapture';

// Actual opening/capture owner and iframe transport; scientific parsing is
// separately exercised by test_studio_source_materialization_http.py.
const original = { path: 'inputs/ensemble.pdb', expected_sha256: 'a'.repeat(64), model_number: 42 };
const cif = 'data_selected\n# Synthetic selected MODEL 42 receipt transport\n';
const frozen = 'data_exported\n# Synthetic frozen MODEL 1 transport\n';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const receipt = { path: 'inputs/materialized/derived.cif', sha256: hash(cif), format: 'cif',
    original_path: 'inputs/materialized/native.pdb', original_sha256: original.expected_sha256,
    native_path: 'inputs/materialized/native.pdb', native_sha256: original.expected_sha256, native_format: 'pdb',
    selected_path: 'inputs/materialized/model-42.pdb', selected_sha256: 'b'.repeat(64),
    decoded_path: 'inputs/materialized/native.pdb', decoded_sha256: original.expected_sha256,
    source_structure: original, model_number: 42, model_numbers: [7, 42], author_residues: [],
    source_identity: { artifact_id: 'original-artifact', target_state: 'selected-state' }, source_path: original.path } as StructureMaterialization;
let root: Root, previous: typeof api.defaults.adapter, registry: DocumentCaptureRegistry;
let actions: ReturnType<typeof useDeNovoDocumentEditors>, adopted: any[], requests: any[], imports: any[], reads: string[], unexpected: string[];
let materializeReply: (() => Promise<StructureMaterialization>) | undefined;
let importReply: (() => Promise<any>) | undefined;
let exportReply: (() => Promise<any>) | undefined;
function Consumer() { actions = useDeNovoDocumentEditors(); return <DeNovoDocumentEditorPanels />; }
async function render(source: StructureSourceSelection = original) {
    await act(async () => root.render(<DeNovoDocumentEditors sources={{ source }} registry={registry} onAdopt={(_, value) => adopted.push(value)}><Consumer /></DeNovoDocumentEditors>));
}
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
const readBlob = (blob: Blob): Promise<string> => new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob); });
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); previous = api.defaults.adapter;
    registry = new DocumentCaptureRegistry(); adopted = []; requests = []; imports = []; reads = []; unexpected = [];
    materializeReply = undefined; importReply = undefined; exportReply = undefined;
    const node = document.createElement('div'); document.body.append(node); root = createRoot(node);
    api.defaults.adapter = async config => {
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        let data: unknown;
        if (config.url === '/api/files/materialize-structure') {
            requests.push(body);
            data = body.path?.includes('annotated_structures') ? { ...receipt, model_numbers: [1], model_number: null }
                : materializeReply ? await materializeReply() : receipt;
        } else if (config.url === '/api/files/upload-immutable-structure') {
            const form = body as FormData; const file = form.get('file') as File;
            expect(await readBlob(file)).toBe(frozen); expect(form.get('sha256')).toBe(hash(frozen));
            data = { path: `inputs/annotated_structures/${file.name}`, sha256: hash(frozen), size: new TextEncoder().encode(frozen).length, existing: false };
        } else { unexpected.push(String(config.url)); throw Error('Unexpected Axios'); }
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
        const url = String(input);
        if (url.startsWith('/api/files/download/')) { reads.push(url); return { ok: true, arrayBuffer: async () => new TextEncoder().encode(cif).buffer } as Response; }
        if (url === '/api/atomworks-studio/api/v1/sessions') {
            const form = options!.body as FormData;
            imports.push({ filename: (form.get('file') as File).name, text: await readBlob(form.get('file') as File),
                options: Object.fromEntries([...form.entries()].filter(([key]) => key !== 'file')) });
            return { ok: true, json: importReply ?? (async () => ({ session_id: 'owned', structure: { revision: 0 } })) } as Response;
        }
        if (url === '/api/atomworks-studio/api/v1/sessions/owned/export') return { ok: true,
            json: exportReply ?? (async () => ({ cif: frozen, name: 'frozen', revision: 2, issues: [] })) } as Response;
        unexpected.push(url); throw Error('Unexpected fetch');
    });
});
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); api.defaults.adapter = previous; vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected).toEqual([]); });

it('does not reuse a native/original display receipt; imports exact converted bytes and captures separate ancestry', async () => {
    await render();
    const known = { ...receipt, path: original.path, format: 'pdb', sha256: original.expected_sha256 } as StructureMaterialization;
    await act(async () => { await actions!.open('source', original, known); });
    expect(requests).toEqual([{ ...original, output_format: 'cif' }]);
    expect(reads).toEqual(['/api/files/download/inputs%2Fmaterialized%2Fderived.cif']);
    expect(imports).toEqual([{ filename: 'source.cif', text: cif, options: { parse_preset: 'minimal', assembly: 'asym', model: '1',
        advanced_options: JSON.stringify({ load_standard_annotations: true, altloc: 'first' }) } }]);
    expect(document.querySelector('iframe')).not.toBeNull();
    const button = [...document.querySelectorAll('button')].find(node => node.textContent === 'Use edited document for source')!;
    await act(async () => button.click()); await vi.waitFor(async () => { await settle(); expect(adopted).toHaveLength(1); });
    expect(adopted[0].original_source).toEqual(original);
    expect(adopted[0].source.model_number).toBe(1);
    expect(adopted[0].provenance).toMatchObject({ imported_model: 42, imported_models: [7, 42], exported_models: [1],
        imported_source: receipt.source_identity, imported_original: { path: receipt.original_path, sha256: receipt.original_sha256 },
        imported_document: { path: receipt.path, sha256: receipt.sha256, format: 'cif' }, import_options: imports[0].options });
    await render(adopted[0].source); await act(async () => { await actions!.open('source', adopted[0].source); });
    expect(imports).toHaveLength(1); // callback remains compatible after adoption
});

it.each(['materialization', 'import'])('A→B→A while %s is pending never attaches a stale session', async phase => {
    let resolve!: (value: any) => void;
    if (phase === 'materialization') materializeReply = () => new Promise(yes => { resolve = yes; });
    else importReply = () => new Promise(yes => { resolve = yes; });
    await render(); let pending!: Promise<void>;
    await act(async () => { pending = actions!.open('source', original); });
    await vi.waitFor(async () => { await settle(); expect(resolve).toBeTypeOf('function'); });
    await render({ ...original, model_number: 7 }); await render(original);
    await act(async () => { resolve(phase === 'materialization' ? receipt : { session_id: 'stale', structure: { revision: 0 } }); await pending; });
    expect(document.querySelector('iframe')).toBeNull(); expect(adopted).toEqual([]);
    expect(imports).toHaveLength(phase === 'materialization' ? 0 : 1);
});

it('A→B→A during capture retains recovery and never adopts stale bytes', async () => {
    await render(); await act(async () => { await actions!.open('source', original); });
    let resolve!: (value: any) => void; exportReply = () => new Promise(yes => { resolve = yes; });
    const button = [...document.querySelectorAll('button')].find(node => node.textContent === 'Use edited document for source')!;
    await act(async () => button.click());
    await render({ ...original, model_number: 7 }); await render(original);
    await act(async () => resolve({ cif: frozen, name: 'stale', revision: 2, issues: [] }));
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain('document changed during capture'); });
    expect(adopted).toEqual([]); expect(document.querySelector('iframe')).not.toBeNull();
});

it('an unselected ensemble does not import a first frame or mutate the ordinary source', async () => {
    materializeReply = async () => ({ ...receipt, model_number: null });
    const source = { path: original.path }; await render(source);
    await act(async () => { await actions!.open('source', source); });
    expect(imports).toEqual([]); expect(adopted).toEqual([]);
    expect(document.body.textContent).toContain('Choose the source MODEL');
});
