import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it } from 'vitest';
import { mkdirSync, writeFileSync } from 'node:fs';
import { api } from '../../src/lib/api';
import { analyzeFrustraMpnnDesigns, analyzeFrustraMpnnUpload, reanalyzeFrustraMpnn, type FrustraMpnnLaunchOptions } from '../../src/lib/frustraMpnnApi';
import { FrustraMpnnLaunchControls } from '../../src/components/frustrampnn/FrustraMpnnLaunchControls';
import { CANONICAL_FRUSTRAMPNN_SETTINGS as settings } from '../../src/components/frustrampnn/frustraMpnnSettingsState';
import '../../src/components/ExecutionPlanApproval';

const original = api.defaults.adapter;
let renderer: ReactTestRenderer | undefined;
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let calls: any[] = [];
const receipt = { job_id: 'queued', child_job_id: 'queued', result_job_id: 'queued', name: 'prepared', parent_job_id: null, source_parent_job_id: 'source', trigger: 'design_analyze', status: 'queued', created_at: null, started_at: null, completed_at: null, settings_value_origin: 'operator_request', requested_settings: { ...settings, settings_value_origin: 'operator_request' }, requested_settings_sha256: '1'.repeat(64), candidates: [], results: [] };
const record = (config: any) => { const row = { url: config.url, body: config.data instanceof FormData ? Object.fromEntries(config.data.entries()) : config.data ? JSON.parse(config.data) : undefined, headers: config.headers.toJSON() }; calls.push(row); return row; };
afterEach(async () => {
    if (process.env.BMS_FRONTEND_EVIDENCE) { mkdirSync(process.env.BMS_FRONTEND_EVIDENCE, { recursive: true }); writeFileSync(`${process.env.BMS_FRONTEND_EVIDENCE}/frustra-${expect.getState().currentTestName?.replace(/[^a-zA-Z0-9]+/g, '-').slice(0, 90)}.json`, JSON.stringify(calls, null, 2)); }
    if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; client.clear(); calls = []; api.defaults.adapter = original; window.history.replaceState({}, '', '/'); document.body.innerHTML = '';
});
it('mounted destination and placement controls preserve omitted, explicit local and selected worker through actual Axios', async () => {
    window.history.replaceState({}, '', '/?launch_context_id=destination');
    api.defaults.adapter = async config => {
        if (config.method === 'post') { record(config); throw new Error('offline capture'); }
        return { data: [{ id: 'vast:one', name: 'One', active: true, state: 'ready', capabilities: {} }], status: 200, statusText: 'OK', headers: {}, config };
    };
    function Harness() {
        const [options, setOptions] = useState<FrustraMpnnLaunchOptions>({});
        return <><FrustraMpnnLaunchControls value={options} onChange={setOptions} /><button onClick={() => analyzeFrustraMpnnDesigns('source', { ...options, selections: [{ design_id: 'd', source_sha256: 'a'.repeat(64) }], frustrampnn_settings: settings }).catch(() => {})}>Submit fixture</button></>;
    }
    await act(async () => { renderer = create(<QueryClientProvider client={client}><Harness /></QueryClientProvider>); });
    const click = async (label: string) => act(async () => renderer!.root.findAllByType('button').find(n => n.children.join('') === label)!.props.onClick());
    await click('Submit fixture'); expect(calls[0].body).not.toHaveProperty('execution_target_id'); expect(calls[0].headers).not.toHaveProperty('X-BMS-Launch-Context-ID');
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'FrustraMPNN destination' }).props.onChange({ target: { value: 'destination' } }));
    await click('Submit fixture'); expect(calls[1].body.launch_context_id).toBe('destination'); expect(calls[1].body).not.toHaveProperty('execution_target_id');
    await click('Local'); await click('Submit fixture'); expect(calls[2].body.execution_target_id).toBeNull();
    await click('Vast · One'); await click('Submit fixture'); expect(calls[3].body.execution_target_id).toBe('vast:one');
    await click('Leave placement unchanged'); await click('Submit fixture'); expect(calls[4].body).not.toHaveProperty('execution_target_id');
});
it('upload multipart and reanalysis JSON preserve optional fields, explicit local and idempotency without scientific leakage', async () => {
    api.defaults.adapter = async config => { record(config); return { data: receipt, status: 202, statusText: 'Accepted', headers: {}, config }; };
    const file = new File(['inert'], 'fixture.pdb');
    await analyzeFrustraMpnnUpload(file, settings);
    await analyzeFrustraMpnnUpload(file, settings, undefined, { execution_target_id: null, launch_context_id: 'destination', idempotency_key: 'upload-key' });
    await analyzeFrustraMpnnUpload(file, settings, undefined, { execution_target_id: 'vast:one' });
    await reanalyzeFrustraMpnn('prior', settings, undefined, { execution_target_id: null, idempotency_key: 'retry-key' });
    expect(calls[0].body).not.toHaveProperty('execution_target_id');
    expect(calls[1].body).toMatchObject({ execution_target_id: '', launch_context_id: 'destination', idempotency_key: 'upload-key' });
    expect(calls[2].body).toHaveProperty('execution_target_id', 'vast:one'); expect(calls[2].body).not.toHaveProperty('launch_context_id');
    expect(calls[3].body).toMatchObject({ execution_target_id: null, idempotency_key: 'retry-key', frustrampnn_settings: settings });
    expect(calls[3].body.frustrampnn_settings).not.toHaveProperty('execution_target_id');
});
it('remote prepared reanalysis uses actual mounted approval then authoritative receipt readback without replaying mutation', async () => {
    const prepared = { name: 'prepared', model_id: 'frustrampnn', mode: 'analyze', execution_target_id: 'vast:one', params: { retained_request: 'inputs/request.json' } };
    const preview = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: prepared,
        plan: { requested_json: prepared.params, effective_json: prepared.params, source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
    api.defaults.adapter = async config => {
        record(config);
        if (config.url?.endsWith('/reanalyze')) throw { isAxiosError: true, response: { status: 409, data: { detail: { code: 'remote_prepared_job_review_required', job_request: prepared, response_context: { operation: 'frustrampnn' } } } } };
        const data = config.url === '/api/jobs/execution-plan/preview' ? preview : config.url === '/api/jobs' ? { id: 'queued', name: 'prepared' } : receipt;
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const pending = reanalyzeFrustraMpnn('prior', settings, undefined, { execution_target_id: 'vast:one' });
    const approve = () => [...document.querySelectorAll('button')].find(n => n.textContent === 'Approve and submit');
    for (let i = 0; i < 150 && !approve(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(approve()).toBeTruthy();
    await act(async () => { approve()!.click(); await pending; });
    expect((await pending).child_job_id).toBe('queued');
    expect(calls.filter(c => c.url.endsWith('/reanalyze'))).toHaveLength(1);
    expect(calls.find(c => c.url === '/api/jobs').body).toMatchObject({ ...prepared, execution_plan_approval: preview.approval_digest });
    expect(calls.at(-1).url).toBe('/api/frustrampnn/jobs/queued/receipt');
}, 15000);
