import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
// Keep the actual parent, NativeSourceInput/Row and materializer transport. Only
// the OS file chooser and expensive viewer/placement leaves are inert.
vi.mock('../../src/components/FileBrowser', () => ({ FileBrowser: ({ onSelect, onCancel }: any) => <div><button onClick={() => onSelect('/retained/two-models.pdb')}>Select retained fixture</button><button onClick={() => onSelect('/retained/replacement.pdb')}>Select replacement fixture</button><button onClick={onCancel}>Cancel file browser</button></div> }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ workflowRequest }: any) => <output data-request>{JSON.stringify(workflowRequest)}</output> }));
vi.mock('../../src/components/ModelDocumentationLinks', () => ({ ModelDocumentationLinks: () => null }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/LaProteinaMotifInput', () => ({ LaProteinaMotifInput: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-viewer /> }));
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
const python = process.env.BMS_TEST_PYTHON ?? 'python3';
const inventory = JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'inventory'], { cwd: '../..', encoding: 'utf8' }));
let root: Root | undefined, client: QueryClient, draft: any;
let requests: { url: string; body: any }[], unexpected: string[];
let pending: { body: any; finish: () => void }[], defer: boolean;
const previous = api.defaults.adapter;
const conditioning = { ori_token: [0, 0, 0], select_hotspots: null, plddt_enhanced: false, symmetry: { id: 'C2' } };
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
const request = () => JSON.parse(document.querySelector('[data-request]')!.textContent!);
beforeEach(() => {
    requests = []; unexpected = []; pending = []; defer = false; localStorage.clear(); sessionStorage.clear();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw Error('No network'); });
    vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => { unexpected.push('XHR'); throw Error('No network'); });
    api.defaults.adapter = async config => {
        const url = String(config.url), body = config.data ? JSON.parse(config.data) : undefined; requests.push({ url, body });
        let data: any;
        if (url.endsWith('/generation-settings')) data = inventory;
        else if (url === '/api/files/materialize-structure') {
            if (defer) await new Promise<void>(resolve => pending.push({ body, finish: resolve }));
            data = { path: body.path, source_path: body.path, source_structure: { ...body, expected_sha256: 'fixture-sha256' }, format: 'pdb', native_format: 'pdb', model_number: body.model_number ?? null, model_numbers: [1, 2], source_positions: [], author_residues: [] };
        } else if (url === '/api/jobs') data = { id: 'inert-job' };
        else { unexpected.push(url); throw Error(`Unexpected HTTP ${url}`); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = previous; vi.restoreAllMocks(); expect(unexpected).toEqual([]); });
async function mount(initial: Record<string, unknown> = {}) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate onBack={() => {}} onDraftChange={value => { draft = value; }} initialValues={{ generator: 'rfd3', conditioning, ...initial }} /></MemoryRouter></QueryClientProvider>));
    await settle();
}
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLElement>('button,summary')].find(node => (node.textContent === text || node.getAttribute('aria-label') === text) && !node.closest('[hidden]'));
    expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle();
}
async function labeledClick(label: string) {
    const node = document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`); expect(node, label).not.toBeNull();
    await act(async () => node!.click()); await settle();
}
async function edit(label: string, value: string) {
    const node = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`); expect(node, label).not.toBeNull();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node!.dispatchEvent(new Event(node!.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle();
}
async function pick() {
    await click('Input structure (optional)'); await labeledClick('Choose structure for RFD3 input structure');
    await click('Browse native file for RFD3 input structure'); await click('Select retained fixture');
}
it.each([undefined, 'bms.rfd3.source-map.v1'])('optional source/MODEL acquisition preserves authored source_mapping=%s through saved cold reopen and Axios', async mapping => {
    await mount(mapping === undefined ? {} : { source_mapping: mapping });
    expect(request().params).not.toHaveProperty('input_structure');
    await pick();
    expect(requests.filter(row => row.url.includes('materialize'))).toEqual([{ url: '/api/files/materialize-structure', body: { path: '/retained/two-models.pdb', output_format: 'native' } }]);
    await labeledClick('Inspect structure for RFD3 input structure');
    await edit('Source MODEL for input', '2');
    expect(requests.filter(row => row.url.includes('materialize')).at(-1)!.body).toMatchObject({ path: '/retained/two-models.pdb', model_number: 2, output_format: 'native' });
    expect(request().params).toMatchObject({ input_structure: '/retained/two-models.pdb', source_model: 2, conditioning });
    expect(Object.hasOwn(request().params, 'source_mapping')).toBe(mapping !== undefined);
    expect(request().params.source_mapping).toBe(mapping);
    const saved = JSON.parse(JSON.stringify(draft)); const exact = request().params;
    await close(); const acquisitions = requests.filter(row => row.url.includes('materialize')).length;
    await mount(saved);
    expect(request().params).toEqual(exact);
    expect(requests.filter(row => row.url.includes('materialize'))).toHaveLength(acquisitions);
    await click('Input structure (optional)');
    expect(document.querySelector<HTMLSelectElement>('[aria-label="RFD3 input structure"]')!.selectedOptions[0].textContent).toContain('MODEL 2');
    const launch = [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent === 'Generate candidates');
    // The real parent submit handler, not a fabricated request builder.
    expect(launch).toBeTruthy(); await act(async () => launch!.click()); await settle();
    expect(requests.find(row => row.url === '/api/jobs')!.body.params).toEqual(exact);
});
it.each(['Clear input structure', 'selector'])('clearing by %s removes source metadata but preserves source-free conditioning', async how => {
    await mount({ input_structure: '/retained/old.pdb', source_model: 2, source_mapping: 'bms.rfd3.source-map.v1' });
    await click('Input structure (optional)');
    if (how === 'selector') await edit('RFD3 input structure', ''); else await click(how);
    const params = request().params;
    for (const key of ['input_structure', 'source_model', 'source_mapping']) expect(params).not.toHaveProperty(key);
    expect(params.conditioning).toEqual(conditioning);
    const saved = JSON.parse(JSON.stringify(draft)); await close(); await mount(saved); expect(request().params).toEqual(params);
    const prepared = JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'replay'], { cwd: '../..', input: JSON.stringify(params), encoding: 'utf8' }));
    expect(prepared.request).not.toHaveProperty('source'); expect(prepared.request.conditioning).toEqual(conditioning);
});
it('cancel discards pending acquisition and replacement wins over stale materialization', async () => {
    await mount(); defer = true; await pick(); expect(pending).toHaveLength(1);
    await click('Cancel'); await act(async () => pending[0].finish()); await settle();
    expect(request().params).not.toHaveProperty('input_structure'); expect(request().params.conditioning).toEqual(conditioning);
    await labeledClick('Choose structure for RFD3 input structure'); await click('Browse native file for RFD3 input structure');
    await click('Select retained fixture'); await click('Select replacement fixture'); expect(pending).toHaveLength(3);
    await act(async () => pending[2].finish()); await settle();
    expect(request().params.input_structure).toBe('/retained/replacement.pdb');
    await act(async () => pending[1].finish()); await settle();
    expect(request().params.input_structure).toBe('/retained/replacement.pdb');
    expect(request().params.conditioning).toEqual(conditioning); expect(request().params).not.toHaveProperty('source_mapping');
});
