import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { request as httpRequest } from 'node:http';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { api } from '../../src/lib/api';

// Real loopback Methods library/compiler HTTP and SQLite. Only original native
// observation/recovery transport is inert; captures pass through the real ASGI projector.
const origin = process.env.RECEIVING_ORIGIN;
let host: HTMLDivElement, root: Root, client: QueryClient, previous: typeof api.defaults.adapter;
let requests: any[], inFlight = 0, original: any, report: any, recover: (body: any) => any;
async function wire(method: string, path: string, body?: unknown) {
    const result: any = await new Promise((resolve, reject) => {
        const req = httpRequest(origin + path, { method: method.toUpperCase(), agent: false, headers: { 'Content-Type': 'application/json' } }, res => {
            let text = ''; res.on('data', b => text += b); res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(text) }));
        }); req.on('error', reject); if (body !== undefined) req.write(JSON.stringify(body)); req.end();
    });
    expect(result.status, JSON.stringify(result.data)).toBeLessThan(400); return result.data;
}
async function settle() {
    for (let i = 0; i < 300; i++) {
        await act(async () => { await new Promise(r => setTimeout(r, 25)); });
        if (!inFlight) { await act(async () => { await new Promise(r => setTimeout(r, 25)); }); if (!inFlight) return; }
    }
    throw new Error('Recovery HTTP did not settle');
}
async function mount() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); root = createRoot(host);
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={9} connected controlsEnabled={false} /></QueryClientProvider>)); await settle();
}
async function click(name: string) {
    const el = [...host.querySelectorAll('button')].find(b => b.textContent === name)!;
    expect(el, name).toBeTruthy(); expect(el.disabled, name).toBe(false);
    await act(async () => el.click()); await settle();
}
async function input(name: string, value: string) {
    const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${name}"]`)!; expect(el, name).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle();
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; inFlight = 0;
    host = document.createElement('div'); document.body.append(host); previous = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const url = config.url!, method = config.method!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        let data: any; inFlight++;
        try {
            if (url.endsWith('/runs') && method === 'get') data = { rows: [original] };
            else if (url.includes(`/runs/${original.job_id}`)) {
                if (url.endsWith('/recovery-draft') && method === 'post') data = recover(body);
                else { expect(method).toBe('get'); data = url.endsWith('/report') ? report : original; }
            } else {
                expect(/\/(control|review|quick-runs)$/.test(url)).toBe(false);
                expect(method === 'post' && /\/runs$/.test(url)).toBe(false);
                const query = new URLSearchParams(Object.entries(config.params ?? {}).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString();
                data = await wire(method, url + (query ? '?' + query : ''), body);
            }
        } finally { inFlight--; }
        requests.push({ method, url, body, response: data });
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
});
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); host.remove(); api.defaults.adapter = previous; vi.unstubAllGlobals(); });
async function journey(name: string, expected: any, editAssumptions: boolean, compile: boolean) {
    const unchanged = JSON.stringify(original);
    await mount(); await click('Runs'); await click(`${original.job_id} · ${original.status}`); await click('Load method report');
    const occurrence = report.method_snapshot.compilation.provenance[0];
    await input('Exact recovery occurrence', occurrence.occurrence_id);
    if (occurrence.native_action_ids?.length) await input('Recovery native action', occurrence.native_action_ids[0]);
    if (editAssumptions) {
        await input('Recovery initial assumptions type', 'object');
        await input('Recovery initial assumptions new field', 'custody'); await click('Add Recovery initial assumptions field');
        await input('Recovery initial assumptions.custody type', 'null');
    }
    await click('Create recovery draft'); await input('Method name', name); await click('Save');
    const accepted = requests.find(r => r.method === 'post' && r.url.endsWith('/library'))?.response;
    expect(accepted, host.textContent ?? '').toBeTruthy();
    const exact = await wire('get', `/api/bioxp/methods/library/${accepted.id}/revisions/${accepted.revision}`);
    expect(exact.method.editor_state.run_inputs).toEqual(expected);
    expect(exact.method.editor_state.recovery_linkage).toMatchObject({ original_job_id: original.job_id, submitted: false });
    await act(async () => root.unmount()); client.clear(); localStorage.clear(); await mount();
    expect(host.querySelector(`[aria-label="Library entry"] option[value="${accepted.id}"]`)).toBeTruthy();
    await input('Library entry', accepted.id); await click('Open'); await click('Save');
    const reopened = await wire('get', `/api/bioxp/methods/library/${accepted.id}`);
    expect(reopened.revision).toBe(2); expect(reopened.method).toEqual(exact.method);
    if (compile) {
        await click('Compile'); const compiled = requests.filter(r => r.url.endsWith('/compile')).at(-1)!;
        expect(compiled.body.method).toEqual(exact.method);
        expect(compiled.body.bindings).toEqual(expected.bindings); expect(compiled.body.dependencies).toEqual(expected.dependencies);
        expect(compiled.body.initial_state).toEqual(expected.initial_state);
        expect(compiled.response.document, JSON.stringify(compiled.response.issues)).toBeTruthy();
    }
    expect(JSON.stringify(original)).toBe(unchanged);
    expect(requests.filter(r => r.method !== 'get' && /\/(control|review|runs|quick-runs)$/.test(r.url))).toHaveLength(0);
    if (process.env.RECEIVING_OUTPUT) writeFileSync(`${process.env.RECEIVING_OUTPUT}.${name.replace(/[^a-zA-Z0-9-]/g, '-')}.json`, JSON.stringify({ exact, reopened, requests, scope: 'Real library/compiler HTTP; inert native observation transport; no execution' }, null, 2));
}
it.runIf(!!origin && !!process.env.BIOXP_NATIVE_BMS_SNAPSHOTS).each([
    ['bms-46-aspirate.json.gz', 'result'], ['bms-46-dispense.json.gz', 'result'],
    ['bms-0-error-hold.json.gz', 'held'], ['bms-0-error-hold.json.gz', 'result'],
])('native %s/%s recovery Save exact HTTP GET cold Open Save compile', async (filename, state) => {
    original = JSON.parse(gunzipSync(readFileSync(`${process.env.BIOXP_NATIVE_BMS_SNAPSHOTS}/${filename}`)).toString())[state];
    const project = (path: string, body?: unknown) => {
        const r = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/fixtures/bioxpNativeMethodProjection.py'], { encoding: 'utf8', input: JSON.stringify({ filename, state, path, body }), timeout: 30000 });
        expect(r.status, r.stderr).toBe(0); return JSON.parse(r.stdout);
    };
    report = project('report'); recover = body => project('recovery-draft', body);
    expect(report.snapshot_available).toBe(true);
    await journey(`Recovery-native-${filename}-${state}`, { bindings: report.method_snapshot.bindings, dependencies: report.method_snapshot.dependencies, initial_state: { custody: null } }, true, true);
}, 90000);
it.runIf(!!origin).each([
    ['omitted', {}], ['null', { bindings: null, dependencies: null, initial_state: null }],
    ['empty', { bindings: {}, dependencies: {}, initial_state: {} }],
    ['raw', { bindings: { amount: '003.7500', unknown: null }, dependencies: { liquid_classes: [], unknown: null }, initial_state: { custody: null } }],
])('inert raw %s recovery preserves exact presence through actual HTTP persistence', async (kind, inputs) => {
    original = { job_id: `protocol-live-${'a'.repeat(64)}`, status: 'completed' };
    const method = { schema: 'bms.bioxp-method.v1', name: 'Raw recovery fixture', steps: [{ type: 'action', step_id: 'note', action: 'note', inputs: { message: 'Inert persistence regression' } }], editor_state: { run_inputs: { bindings: { stale: true }, dependencies: { stale: true }, initial_state: { stale: true } } } };
    report = { job_id: original.job_id, method_snapshot: { method, compilation: { provenance: [{ occurrence_id: 'note', step_id: 'note', path: '/steps/0', native_action_ids: [] }] } } };
    recover = body => ({ method, ...inputs as object, recovery: { original_job_id: original.job_id, occurrence: body.occurrence, automatic_setup: [], excluded_actions: [], submitted: false } });
    await journey(`Recovery-raw-${kind}`, inputs, false, false);
}, 60000);
