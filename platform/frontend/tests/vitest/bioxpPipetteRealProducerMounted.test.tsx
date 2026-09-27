import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BioXpWellPipettingPanel } from '../../src/components/BioXpWellPipettingPanel';
import { BioXpPipetteResults } from '../../src/components/BioXpPipetteResults';
import { api } from '../../src/lib/api';

/** Optional cross-repository qualification, never substitute synthetic outcomes.
 * Generate these exports with the robot's connected SQLite/dispatcher suites.
 * Only the BMS connection/job envelope and HTTP transport are fixture-owned.
 */
type ProducerCase = {
    name: string; action_result: any; run?: any; accept_run?: any; restore_run?: any;
};
const load = (path: string | undefined): ProducerCase[] => path
    ? JSON.parse(readFileSync(path, 'utf8')).cases : [];
const calibration = load(process.env.BIOXP_CALIBRATION_PRODUCERS);
const additional = load(process.env.BIOXP_ADDITIONAL_PRODUCERS);
let host: HTMLDivElement, root: Root, client: QueryClient;
const originalAdapter = api.defaults.adapter;
function relay(script: string, payload: unknown) {
    const response = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', [`tests/${script}`], {
        cwd: '../api', encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:tests' },
        input: JSON.stringify(payload), maxBuffer: 8 * 1024 * 1024,
    });
    expect(response.status, response.stderr || response.stdout).toBe(0);
    const output = JSON.parse(response.stdout.trim().split('\n').at(-1)!);
    expect(output.status).toBe(200);
    return output;
}
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function click(text: string) {
    const element = [...host.querySelectorAll('button')].find(button => button.textContent === text);
    expect(element, text).toBeDefined();
    await act(async () => element!.click()); await tick();
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); host.remove();
    api.defaults.adapter = originalAdapter; vi.unstubAllGlobals();
});

describe.skipIf(!process.env.BIOXP_CALIBRATION_PRODUCERS)('real robot calibration producer through BMS', () => {
    it('received a nonempty real-producer export', () => expect(calibration.length).toBeGreaterThan(0));
    it.each(calibration)('$name persists critical outcomes through relay and mounted well panel', async item => {
        let job: any = null;
        let currentRun = item.run;
        const requests: any[] = [];
        api.defaults.adapter = async config => {
            const request = config.method === 'post' ? JSON.parse(config.data) : config.params;
            const url = config.url!;
            requests.push({ method: config.method, url, request });
            let data: any;
            if (url.includes('/calibration-settings/runs/')) {
                expect(currentRun, 'real run response must accompany the action').toBeDefined();
                expect(url).toContain(encodeURIComponent(currentRun.run_id));
                if (config.method === 'post') {
                    const next = request.decision === 'accept' ? item.accept_run : item.restore_run;
                    expect(next, 'decision response must be produced by the robot owner').toBeDefined();
                    currentRun = next;
                }
                const response = relay('bioxp_calibration_route_bridge.py', {
                    method: config.method, request, run_id: currentRun.run_id, run_payload: currentRun,
                });
                expect(response.data).toEqual(currentRun);
                expect(response.robot_requests).toEqual([{
                    method: config.method!.toUpperCase(),
                    path: `/motion/oem/calibration_settings/runs/${currentRun.run_id}${config.method === 'post' ? '/decision' : ''}`,
                    body: config.method === 'post' ? { decision: request.decision } : null,
                }]);
                data = response.data;
            } else if (config.method === 'post') {
                expect(url).toBe('/api/bioxp/protocols/submit');
                const response = relay('bioxp_manual_route_bridge.py', { request,
                    status: item.action_result.ok === false ? 'failed' : 'completed',
                    action_results: [item.action_result] });
                expect(response.data.execution.runtime_state.action_results).toEqual([item.action_result]);
                job = response.data; data = job;
            } else {
                expect(url).toContain('/protocols/jobs/');
                expect(job).not.toBeNull(); data = job;
            }
            return { data, status: 200, statusText: 'offline real-producer replay', headers: {}, config };
        };
        await act(async () => root.render(<QueryClientProvider client={client}>
            <BioXpWellPipettingPanel generation={77} connected />
        </QueryClientProvider>));
        await click('OEM calibrate with fluid now');
        const body = item.action_result.pipette_result;
        expect(body, 'export must contain the actual compact persisted result').toBeDefined();
        expect(host.textContent).toContain(body.body_completed ? 'Completed' : 'Incomplete / partial result');
        for (const row of body.measurements) {
            expect(host.textContent).toContain(`${row.plate}: ${row.measured_raw_z}`);
            expect(host.textContent).toContain(row.saved_revision_id);
        }
        if (body.error) expect(host.textContent).toContain(body.error);
        if (body.finalization_error) expect(host.textContent).toContain(body.finalization_error);
        if (body.run_id) {
            expect(host.textContent).toContain(body.run_id);
            expect(host.querySelector('[aria-label="Before and after calibration"]')).not.toBeNull();
            expect(requests.some(row => row.method === 'get' && row.url.includes(body.run_id))).toBe(true);
        }
        for (const decision of ['accept', 'restore'] as const) {
            const value = decision === 'accept' ? item.accept_run : item.restore_run;
            if (!value) continue;
            const start = requests.length;
            await click(decision === 'accept' ? 'Accept calibration' : 'Reject / restore full pre-run calibration');
            expect(requests.slice(start).filter(row => row.url.includes('/calibration-settings/runs/'))).toEqual([
                { method: 'post', url: `/api/bioxp/calibration-settings/runs/${value.run_id}/decision`,
                    request: { expected_connection_generation: 77, decision } },
                { method: 'get', url: `/api/bioxp/calibration-settings/runs/${value.run_id}`,
                    request: { expected_connection_generation: 77 } },
            ]);
            if (item.name === 'no_backup') {
                // OEM resultComparison returns true before the dialog/history/restore
                // branch when no backup exists. Neither POST fabricates consent.
                expect(value).toEqual(item.run);
                expect(value).toMatchObject({ machine_calibrated: false, decision: null,
                    decision_status: 'no_previous_values', comparison_choice: true,
                    comparison_source: 'no_previous_values', outcome: 'accepted_no_previous_values' });
                expect(host.textContent).toContain(`Requested ${decision}; readback decision is not recorded`);
                expect(host.textContent).toContain('Recorded decision: Not decided');
                expect(host.textContent).toContain('no_previous_values');
                expect(host.textContent).not.toContain('Decision read back:');
            } else {
                expect(value.decision).toBe(decision);
                expect(host.textContent).toContain(`Decision read back: ${decision}`);
            }
            expect(host.textContent).toContain(value.active_revision_id ?? 'Baseline / no revision');
            for (const label of ['Accept calibration', 'Reject / restore full pre-run calibration']) {
                expect([...host.querySelectorAll('button')].find(button => button.textContent === label)?.disabled).toBe(false);
            }
        }
        expect(requests.filter(row => row.method === 'post' && row.url === '/api/bioxp/protocols/submit')).toHaveLength(1);
        expect(host.querySelector('pre')).toBeNull();
    }, 60000);
});

describe.skipIf(!process.env.BIOXP_ADDITIONAL_PRODUCERS)('real source/diagnostic compact producer rendering', () => {
    it('received a nonempty real-producer export', () => expect(additional.length).toBeGreaterThan(0));
    it.each(additional)('$name renders unchanged persisted values', async item => {
        await act(async () => root.render(<BioXpPipetteResults value={item.action_result} />));
        const result = item.action_result.pipette_result;
        expect(result).toBeDefined();
        expect(host.textContent).toContain(result.kind);
        if (result.error) expect(host.textContent).toContain(String(result.error));
        if (result.kind === 'source_load_tips') {
            expect(host.textContent).toContain(String(result.requested_pipette));
            expect(host.textContent).toContain(String(result.tip_location));
            expect(host.textContent).toContain(String(result.alignment_published));
        }
        for (const row of result.tests ?? []) {
            expect(host.textContent).toContain(row.label);
            for (const channel of row.channels ?? []) if (channel.display) expect(host.textContent).toContain(channel.display);
        }
        for (const channel of result.channels ?? []) {
            for (const key of ['part_number', 'revision', 'firmware', 'display']) {
                if (channel[key] != null) expect(host.textContent).toContain(String(channel[key]));
            }
        }
        expect(host.querySelector('pre')).toBeNull();
    });
});
