import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import { execFileSync } from 'node:child_process';
import { api } from '../../src/lib/api';
import { ResequencePanel } from '../../src/components/ResequencePanel';
import { DeNovoDraftPreparationContext } from '../../src/lib/deNovoDocumentCapture';
import { deNovoSavedValues } from '../../src/components/deNovoWorkflowRoute';
import models from './fixtures/resequenceDesignerModels.json';
import names from '../../src/lib/resequenceValidatorNames.json';

const approval = vi.hoisted(() => ({ accepted: false }));
vi.mock('../../src/components/ExecutionPlanApproval', () => ({ reviewExecutionPlan: vi.fn(async () => approval.accepted) }));
vi.mock('../../src/components/DeNovoNativeSources', () => ({ NativeSourceInput: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ onChange }: any) => <><button onClick={() => onChange(null)}>Place Local</button><button onClick={() => onChange('worker')}>Place worker</button></> }));
let root: Root | undefined, query: QueryClient, latest: any, prep: any;
let handler: ((config: any, body: any) => Promise<any> | any) | undefined;
const calls: Array<{ url: string; body: any }> = [];
const unexpected: string[] = [];
const previous = api.defaults.adapter;
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); };
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = previous; handler = undefined; calls.length = 0; approval.accepted = false; sessionStorage.clear(); expect(unexpected.splice(0)).toEqual([]); });
const q = (label: string) => document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
async function edit(label: string, value: string) { const node = q(label); expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function click(text: string) { const node = [...document.querySelectorAll('button')].find(node => node.textContent === text); expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle(); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const loop = { backbone_input_pdbs: 'retained/basket', seq_method: 'mpnn', structure_validators: ['protenix_v2'] };
const posts = (end: string) => calls.filter(call => call.url.endsWith(end));
async function mount(values: any = loop) {
    api.defaults.adapter = async config => {
        const url = String(config.url), body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body }); let data: any;
        const intercepted = await handler?.(config, body);
        const model = /^\/api\/models\/([^/]+)$/.exec(url);
        if (intercepted !== undefined) data = intercepted;
        else if (model) data = (models as any)[model[1]];
        else if (url.endsWith('/from-structures/prepare')) data = { name: body.name, model_id: 'protein_modification_experimental', mode: 'region_redesign', params: { backbone_input_pdbs: `owned/${calls.length}`, resequence_preparation: { path: `owned/${calls.length}`, token: `token-${calls.length}` } } };
        else if (url.endsWith('/cleanup')) data = { cleaned: true };
        else if (url.endsWith('/execution-plan/preview')) data = { approval_digest: 'a'.repeat(64), admissible: true, request: body };
        else if (url === '/api/jobs') data = { id: 'new-job', name: body.name };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { data, config, status: 200, statusText: 'OK', headers: {} };
    };
    prep = { current: null }; latest = undefined;
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter><DeNovoDraftPreparationContext.Provider value={prep}><ResequencePanel initialValues={values} onDraftChange={draft => { latest = draft; }} /></DeNovoDraftPreparationContext.Provider></MemoryRouter></QueryClientProvider>)); await settle();
}
it('uses the Python model owner mapping rather than guessing internal validator names', () => {
    const actual = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python', ['platform/frontend/scripts/export-resequence-validator-names.py'], { cwd: '../..', encoding: 'utf8' }));
    expect(names).toEqual(actual);
});
it('hydrates effective ESM loop keys and emits exact sparse public settings; cold clear does not resurrect them', async () => {
    await mount(deNovoSavedValues('protein_modification_experimental', 'region_redesign', { ...loop, structure_validators: ['esmfold2', 'boltz2'], esmf_num_loops: 5, esmf_seed: 0, esmf_model_variant: 'full', boltz_seed: 0, boltz_use_potentials: false }));
    expect(q('Validator setting esmfold2 num_loops').value).toBe('5');
    expect(q('Validator setting esmfold2 seed').value).toBe('0');
    await act(async () => { await prep.current(); });
    expect(posts('/prepare')[0].body.validator_settings).toEqual({ esmfold2: { num_loops: 5, seed: 0, model_variant: 'full' }, boltz2: { boltz_seed: 0, boltz_use_potentials: false } });
    await edit('Validator setting esmfold2 num_loops', ''); await edit('Validator setting esmfold2 seed', ''); await edit('Validator setting esmfold2 model_variant', '');
    await edit('Validator setting boltz2 boltz_seed', ''); await edit('Validator setting boltz2 boltz_use_potentials', '');
    const saved = JSON.parse(JSON.stringify(latest)); await close(); await mount(saved);
    expect(q('Validator setting esmfold2 num_loops').value).toBe('');
    await act(async () => { await prep.current(); });
    expect(posts('/prepare').at(-1)!.body.validator_settings).toEqual({});
});
it.each([[undefined, 4], [null, 4], [2, 2]])('hydrates Boltz native fallback with canonical=%s, then clears it without resurrecting the retained alias', async (canonical, expected) => {
    await mount({ ...loop, structure_validators: ['boltz2'], boltz_num_samples: 4, ...(canonical !== undefined ? { boltz_diffusion_samples: canonical } : {}), resequence_job_params: { ...loop, boltz_num_samples: 4 } });
    expect(q('Validator setting boltz2 boltz_diffusion_samples').value).toBe(String(expected));
    await act(async () => { await prep.current(); });
    expect(posts('/prepare')[0].body.validator_settings).toEqual({ boltz2: { boltz_diffusion_samples: expected } });
    await edit('Validator setting boltz2 boltz_diffusion_samples', '');
    const saved = JSON.parse(JSON.stringify(latest)); await close(); await mount(saved);
    await act(async () => { await prep.current(); });
    expect(posts('/prepare').at(-1)!.body.validator_settings).toEqual({});
});
it('distinguishes untouched retained template from explicit clear-all after cold restore', async () => {
    await mount({ ...loop, seq_method: 'ligandmpnn', designer_template: 'retained/template.json', resequence_job_params: { designer_template: 'retained/template.json' } });
    await act(async () => { await prep.current(); });
    expect(posts('/prepare')[0].body).not.toHaveProperty('designer_settings');
    await edit('LigandMPNN mode', 'ligand_aware'); await edit('Designer setting temperature', '0.2'); await edit('Designer setting temperature', '');
    const saved = JSON.parse(JSON.stringify(latest)); await close(); await mount(saved);
    await act(async () => { await prep.current(); });
    expect(posts('/prepare').at(-1)!.body.designer_settings).toEqual({});
});
it('deduplicates concurrent delayed captures and never replaces newer authored science with a late response', async () => {
    const first = deferred<any>();
    handler = (config, body) => config.url.endsWith('/prepare') && body.name === 'Resequence' ? first.promise : undefined;
    await mount(); let a!: Promise<any>, b!: Promise<any>;
    await act(async () => { a = prep.current(); b = prep.current(); });
    expect(posts('/prepare')).toHaveLength(1);
    await edit('Resequence job name', 'replacement'); await act(async () => { await prep.current(); });
    first.resolve({ name: 'Resequence', params: { resequence_preparation: { path: 'old', token: 'old' } } });
    await act(async () => { await Promise.all([a, b]); });
    expect(latest.job_name).toBe('replacement'); expect(latest.native_job_request.name).toBe('replacement');
    expect(latest.resequence_preparations.map((entry: any) => entry.job.name).sort()).toEqual(['Resequence', 'replacement']);
    expect(posts('/prepare')).toHaveLength(2);
});
it('retains uncertain creation staging during concurrent replacement and cold retry', async () => {
    const created = deferred<any>(); let rejectCreate = true;
    handler = async config => {
        if (config.url === '/api/jobs' && rejectCreate) { await created.promise; throw new AxiosError('Network lost'); }
    };
    await mount(); await click('Run resequence and validate');
    const original = posts('/api/jobs')[0].body;
    await edit('Resequence job name', 'replacement'); await act(async () => { await prep.current(); });
    expect(posts('/cleanup')).toHaveLength(0);
    created.resolve(null); await settle();
    expect(posts('/cleanup')).toHaveLength(0);
    expect(latest.resequence_preparations).toHaveLength(2);
    const saved = JSON.parse(JSON.stringify(latest)); await close(); await mount(saved);
    await edit('Resequence job name', 'Resequence'); rejectCreate = false;
    await click('Run resequence and validate');
    expect(posts('/api/jobs').at(-1)!.body.params.resequence_preparation).toEqual(original.params.resequence_preparation);
    expect(posts('/prepare')).toHaveLength(2); expect(posts('/cleanup')).toHaveLength(0);
});
it('cleans explicit review cancellation, but retains a refused or failed cleanup receipt for retry', async () => {
    let cleaned = false;
    handler = config => config.url.endsWith('/cleanup') ? { cleaned } : undefined;
    await mount(); await click('Place worker'); await click('Run resequence and validate');
    expect(posts('/api/jobs')).toHaveLength(0); expect(posts('/cleanup')).toHaveLength(1);
    expect(latest.resequence_prepared.job.params.resequence_preparation).toBeTruthy();
    cleaned = true; await click('Run resequence and validate');
    expect(posts('/prepare')).toHaveLength(1); expect(posts('/cleanup')).toHaveLength(2);
    expect(latest.resequence_prepared).toBeUndefined();
    await click('Run resequence and validate'); expect(posts('/prepare')).toHaveLength(2);
});
it('disposes a superseded direct-run preparation after cancelled review cleanup can be retried', async () => {
    let failures = 1;
    handler = config => { if (config.url.endsWith('/cleanup') && failures-- > 0) throw new AxiosError('cleanup offline'); };
    await mount(); await click('Place worker'); await click('Run resequence and validate');
    await edit('Resequence job name', 'replacement'); await act(async () => { await prep.current(); });
    expect(posts('/cleanup')).toHaveLength(2);
    expect(latest.resequence_preparations).toHaveLength(1);
    expect(latest.resequence_preparations[0].job.name).toBe('replacement');
});
