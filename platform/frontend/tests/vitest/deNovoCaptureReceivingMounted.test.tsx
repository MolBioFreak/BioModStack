import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Real form/serializer and pure model discovery. Only capture transport,
// model execution, optional peers and expensive renderer are inert here.
const nativeInventory = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
    "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;print(json.dumps(generation_settings('generate')))"
], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
const mocks = vi.hoisted(() => ({ capture: vi.fn(), submit: vi.fn(async (_payload: unknown) => ({ data: { id: 'inert' } })) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    fetchNativeGenerationSettings: vi.fn(async () => ({ data: nativeInventory })), submitJob: mocks.submit,
    completeCurrentLaunchContext: vi.fn(async () => null),
}));
vi.mock('../../src/components/DeNovoNativeSources', async () => {
    const React = await import('react');
    const { DocumentCaptureContext } = await import('../../src/lib/deNovoDocumentCapture');
    return { NativeSourceInput: () => null, DeNovoNativeSources: () => {
        const registry = React.useContext(DocumentCaptureContext)!;
        React.useEffect(() => registry.register('source', { generation: () => 0, capture: mocks.capture }), [registry]);
        return <p>Inert export transport registered</p>;
    } };
});
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/DeNovoNativePreview', () => ({ DeNovoNativePreview: () => null }));
import { DeNovoNativeGenerationSettings } from '../../src/components/DeNovoNativeGenerationSettings';
import { DeNovoDraftPreparationContext, type DeNovoDraftPreparation, type CapturedAnnotatedDocument } from '../../src/lib/deNovoDocumentCapture';

let root: Root | undefined;
let query: QueryClient;
let prep: DeNovoDraftPreparation;
let report: ReturnType<typeof vi.fn>;
let unexpected: string[];
let xhr: ReturnType<typeof vi.spyOn>, fetch: ReturnType<typeof vi.spyOn>;
const original = { path: 'inputs/original.cif', expected_sha256: 'a'.repeat(64), model_number: 7 };
const frozen: CapturedAnnotatedDocument = { format: 'cif', size: 123, sha256: 'b'.repeat(64),
    source: { path: 'inputs/annotated_structures/edited.cif', expected_sha256: 'b'.repeat(64), model_number: 7, output_format: 'native' },
    original_source: original, provenance: { revision: 4, recipe: { strands: [], missing: null } } };
const request = { input: { kind: 'annotated', slots: ['source'] }, settings: { seed: 0, compress_outputs: false, search: { checkpoints: null } } };
beforeEach(() => {
    prep = { current: null }; report = vi.fn(); unexpected = [];
    mocks.capture.mockResolvedValue(structuredClone(frozen));
    xhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No network'); });
    fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No network'); });
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined;
    query?.clear(); document.body.replaceChildren(); xhr.mockRestore(); fetch.mockRestore();
    expect(unexpected).toEqual([]); vi.clearAllMocks();
});
async function mount(initialValues: Record<string, unknown> = { rfd4_request: request, native_sources: { source: original }, sequence_design: null, sequence_design_null: true }) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter>
        <DeNovoDraftPreparationContext.Provider value={prep}><DeNovoNativeGenerationSettings model="rfd4_proteina"
            initialValues={initialValues} onDraftChange={report} /></DeNovoDraftPreparationContext.Provider>
    </MemoryRouter></QueryClientProvider>));
}
async function openEditor() {
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Input structures')!;
    expect(summary).toBeTruthy();
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
async function generate() {
    const button = [...document.querySelectorAll('button')].find(node => node.textContent === 'Generate candidates')!;
    expect(button).toBeTruthy(); await act(async () => button.click());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
}

it('prepares Save/Template/Project with the captured source using the native child serializer', async () => {
    await mount(); await openEditor();
    expect(prep.current).not.toBeNull();
    await act(async () => { await prep.current!(); });
    const saved = report.mock.calls.at(-1)![0];
    expect(mocks.capture).toHaveBeenCalledTimes(1);
    expect(saved.native_job_request.params.rfd4_request).toEqual(request);
    expect(saved.native_job_request.params.native_sources.source).toEqual(frozen.source);
    expect(saved.native_sources.source).toEqual(frozen.source);
    expect(saved.molecular_documents.source).toEqual(frozen);
    expect(saved.native_job_request.params).not.toHaveProperty('molecular_documents');
    expect(saved.native_job_request.sequence_design).toBeNull();
    expect(JSON.parse(JSON.stringify(saved))).toEqual(saved);
});

it('Generate awaits the exact same capture and never submits a failed export', async () => {
    await mount(); await openEditor();
    mocks.capture.mockRejectedValueOnce(new Error('Export persistence failed'));
    await generate();
    expect(mocks.submit).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('Export persistence failed');
    await generate();
    expect(mocks.submit).toHaveBeenCalledTimes(1);
    const submitted = mocks.submit.mock.calls[0][0] as any;
    expect(submitted.params.rfd4_request).toEqual(request);
    expect(submitted.params.native_sources.source).toEqual(frozen.source);
    expect(submitted.execution_target_id).toBeNull();
    expect(submitted.params).not.toHaveProperty('provenance');
});

it('cold retained authoring reuses managed bytes without an editor session or another export', async () => {
    await mount({ rfd4_request: request, native_sources: { source: frozen.source }, molecular_documents: { source: frozen }, sequence_design_null: true });
    await act(async () => { await prep.current!(); });
    expect(mocks.capture).not.toHaveBeenCalled();
    const saved = report.mock.calls.at(-1)![0];
    expect(saved.molecular_documents.source).toEqual(frozen);
    expect(saved.native_job_request.params.native_sources.source).toEqual(frozen.source);
    await generate();
    expect(mocks.capture).not.toHaveBeenCalled();
    expect(mocks.submit).toHaveBeenCalledTimes(1);
});
