import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { api } from '../../src/lib/api';

// Requires the companion native checkout. No listener, device client or live profile.
it.skipIf(!process.env.BMS_RECOVERY_NATIVE_SRC).each(['action', 'occurrence'])('reloads and retries a held %s through BMS and SQLite', async scope => {
    const cwd = resolve('../api');
    const child = spawn(process.env.BMS_TEST_PYTHON!, ['-u', 'tests/same_run_connected_bridge.py'], { cwd,
        env: { ...process.env, PYTHONPATH: [cwd, resolve('../..'), process.env.BMS_RECOVERY_NATIVE_SRC].join(':') } });
    const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
    let stderr = ''; child.stderr.on('data', d => { stderr += d; });
    const closed = new Promise(resolve => child.on('close', resolve));
    let queue = Promise.resolve();
    function rpc(request: unknown) {
        const result = queue.then(async () => {
            child.stdin.write(JSON.stringify(request) + '\n');
            const next = await lines.next(); if (next.done) throw new Error(stderr);
            return JSON.parse(next.value);
        }); queue = result.then(() => {}, () => {}); return result;
    }
    const original = api.defaults.adapter;
    const id = `protocol-live-${'a'.repeat(64)}`, url = `/api/bioxp/methods/runs/${id}`;
    const seen: any[] = [], errors: unknown[] = [];
    api.defaults.adapter = async config => {
        const path = config.url!, body = config.data ? JSON.parse(config.data) : undefined;
        const req = { url: path, method: config.method, body, params: config.params };
        let data: any = [];
        if (path.includes('/methods/runs')) {
            const res = await rpc(req); seen.push({ ...req, res });
            if (res.status !== 200) { errors.push({ req, res }); throw Error(JSON.stringify(res)); }
            data = res.data;
        } else if (path.endsWith('/updates')) return new Promise(() => {});
        else if (path.endsWith('/catalog')) data = { actions: [] };
        else if (path.endsWith('/schema')) data = { method: {} };
        else if (path.endsWith('/check')) data = { issues: [] };
        else if (config.method !== 'get') { errors.push(req); throw Error(path); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
    const host = document.createElement('div'); document.body.appendChild(host);
    let root = createRoot(host), client = new QueryClient();
    const render = () => act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={77} connected controlsEnabled visible /></QueryClientProvider>));
    async function until(test: () => boolean) {
        for (let i = 0; i < 200 && !test(); i++) { if (errors.length) throw Error(JSON.stringify(errors)); await act(async () => { await new Promise(r => setTimeout(r, 30)); }); }
        expect(test(), JSON.stringify(errors) + host.textContent?.slice(-1700)).toBe(true);
    }
    const button = (name: string) => [...host.querySelectorAll('button')].find(b => b.textContent === name);
    const click = async (name: string) => { await until(() => !!button(name) && !button(name)!.disabled); await act(async () => button(name)!.click()); };
    const field = async (label: string, value: string) => {
        const sel = `[aria-label="${label}"]`; await until(() => { const node = host.querySelector(sel); return !!node && (!(node instanceof HTMLSelectElement) || [...node.options].some(o => o.value === value)); });
        const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(sel)!;
        await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
    };
    try {
        const initial = await rpc({ witness: true }); expect(initial.calls).toEqual(['earlier', 'recipe-1']);
        for (const reload of [false, true]) {
            if (reload) { await act(async () => root.unmount()); client.clear(); root = createRoot(host); client = new QueryClient(); }
            await render(); await until(() => host.textContent!.includes('Attempt 1'));
            // Both discovery and reload must open the failed step without operator clicks.
            await until(() => host.textContent!.includes('Simulated aspirate did not complete') &&
                host.querySelector<HTMLInputElement>('[aria-label="aspiration_delay_ms"]')?.value === String(initial.delays[0]));
            expect(seen.filter(r => r.url.endsWith('/control'))).toHaveLength(0);
        }
        for (const observation of [false, true]) {
            const held = await rpc({ url, method: 'get', params: { observation } });
            expect(held.status).toBe(200); expect(held.data.execution.runtime_state.workflow).toMatchObject({ attempt: 1, gate: 'error_hold', source_occurrence_id: 'occ', native_action_id: 'recipe-1' });
        }
        if (scope === 'occurrence') { await field('Redo scope', scope); await click('Preview decision'); }
        await field('aspiration_delay_ms', '123'); await click('Preview decision');
        await until(() => seen.some(r => r.res.data.proposed_settings?.aspiration_delay_ms === 123));
        const preview = seen.filter(r => r.url.endsWith('/recovery-preview')).at(-1).res.data;
        expect(preview.affected_actions.map((a: any) => a.action_id)).toEqual(scope === 'occurrence' ? ['earlier', 'recipe-1'] : ['recipe-1']);
        expect(preview).toMatchObject({ submitted: false, attempt_results: [{ attempt: 1, ok: false, partial: { amount: null } }], sibling_results: [{ action_id: 'earlier' }] });
        expect(await rpc({ witness: true })).toEqual(initial);
        await click('Submit recovery decision'); await until(() => seen.some(r => r.url.endsWith('/control')));
        const control = seen.filter(r => r.url.endsWith('/control')); expect(control).toHaveLength(1);
        const witness = await rpc({ witness: true }); expect(witness.delays).toEqual([initial.delays[0], 123]);
        expect(witness.calls).toEqual(['earlier', 'recipe-1', ...(scope === 'occurrence' ? [expect.any(String)] : []), expect.any(String), 'next']);
        expect(witness.controls[0]).toMatchObject({ command_id: id, decision: 'retry', native_action_id: 'recipe-1', source_occurrence_id: 'occ', expected_attempt: 1, overrides: { aspiration_delay_ms: 123 }, ...(scope === 'occurrence' ? { scope } : {}) });
        const replay = await rpc(control[0]); expect(replay).toEqual(control[0].res);
        const done = await rpc({ url, method: 'get' }); expect(done.status).toBe(200);
        const state = done.data.execution.runtime_state; expect(done.data.command).toMatchObject({ command_id: id, status: 'completed' });
        expect(state.action_results.filter((r: any) => r.action_id === 'recipe-1')).toMatchObject([{ ok: false, attempt: 1, partial: { amount: null } }, { ok: true, attempt: 2, overrides: { aspiration_delay_ms: 123 } }]);
        expect((await rpc({ witness: true })).calls).toEqual(witness.calls);
        expect(errors).toEqual([]);
    } finally { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = original; child.stdin.end(); expect(await closed, stderr).toBe(0); }
}, 30000);
