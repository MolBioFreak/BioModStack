import React, { useEffect } from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { DashboardTelemetry } from '../../src/components/dashboard/DashboardTelemetry';
import { InfraLiveTelemetry } from '../../src/components/InfraLiveTelemetry';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { MutagenesisTemplate } from '../../src/components/MutagenesisTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { useSystemStatus } from '../../src/lib/useSystemStatus';

const mounts = vi.hoisted(() => ({ provision: vi.fn(), unmount: vi.fn() }));
vi.mock('../../src/components/dashboard/IndependentProvisionPanel', () => ({
    WorkflowProvisionPanel: ({ workflowRequest }: any) => {
        useEffect(() => { mounts.provision(); return () => mounts.unmount(); }, []);
        return <output data-request={workflowRequest} />;
    }, CatalogProvisionPanel: () => null,
}));
vi.mock('../../src/components/telemetryMetricPlot', () => ({ TimeSeriesPlot: () => null }));
vi.mock('../../src/components/RemoteGpuTelemetry', () => ({ RemoteGpuTelemetry: () => <span>remote telemetry</span> }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({ data: { workflows: { structure_prediction: { default_enabled: false } } } }) }));

let root: ReactTestRenderer | undefined;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
let reads: string[];
let estimates: any[];
let targets: any[];
let targetError: boolean;
const worker = { id: 'vast:one', provider: 'vast', provider_instance_id: 'one', name: 'Worker one',
    active: true, state: 'ready', capabilities: { gpu_count: 1 }, pricing: {}, host: 'fixture', port: 22 };
const text = (node: ReactTestInstance): string => node.children.map(c => typeof c === 'string' ? c : text(c)).join('');
async function tick(ms = 1) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function render(children: React.ReactNode) {
    await act(async () => {
        const tree = <MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter>;
        if (root) root.update(tree); else root = create(tree);
    });
    await tick();
}
async function click(label: string) {
    const button = root!.root.findAllByType('button').find(n => text(n).trim().startsWith(label));
    expect(button, label).toBeTruthy();
    await act(async () => button!.props.onClick());
    await tick();
}
const count = (path: string) => reads.filter(p => p === path).length;
beforeEach(() => {
    vi.useFakeTimers(); focusManager.setFocused(true);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    reads = []; estimates = []; targets = [worker]; targetError = false; mounts.provision.mockClear(); mounts.unmount.mockClear();
    window.history.replaceState({}, '', '/submit'); sessionStorage.clear(); localStorage.clear();
    api.defaults.adapter = async config => {
        if (config.method === 'post' && config.url === '/api/jobs/boltz-api/estimate') {
            estimates.push(JSON.parse(config.data)); throw new Error('Inert estimate captured');
        }
        if (config.method !== 'get') throw new Error(`Forbidden fixture write ${config.url}`);
        reads.push(config.url!);
        let data: any;
        if (config.url === '/api/execution-targets') {
            if (targetError) throw new Error('inventory offline');
            data = targets;
        } else if (config.url === '/api/gpu/status') data = { timestamp: new Date().toISOString(), gpus: [{ index: 0, name: 'Fresh local GPU', memory_total_mb: 24000, utilization: 0, memory_used_mb: 0, reserved_memory_mb: 0, power_draw_w: 10, power_limit_w: 100, temperature: 30, processes: [] }], cpu: { name: 'CPU', utilization: 0, frequency_current_mhz: 1000 }, ram: { used_gb: 1, available_gb: 7, utilization: 12.5, swap_percent: 0 } };
        else if (config.url === '/api/execution-targets/active/telemetry') data = { available: true, observed_at: new Date().toISOString(), target: worker,
            gpus: [{ index: 0, name: 'Fresh remote GPU', memory_total_mb: 24000, execution_target_id: worker.id }] };
        else if (config.url === '/api/telemetry/chart-history') data = { ...config.params, generated_at_ms: Date.now(), next_cursor_ms: null, points: [] };
        else if (config.url === '/api/jobs/boltz-api/status') data = { available: true, cli_available: true, credential_configured: true, model: 'boltz-2.1', message: 'Provider ready' };
        else if (config.url?.includes('msa')) data = { providers: {}, cache_entries: 0 };
        else if (['/api/gpu/power-control', '/api/gpu/fan-control', '/api/scheduler/config'].includes(config.url!)) data = {};
        else throw new Error(`Unexpected fixture GET ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined; client.clear();
    api.defaults.adapter = originalAdapter; focusManager.setFocused(undefined); vi.useRealTimers(); vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear();
});

it('Dashboard local telemetry consumes one inventory observer and preserves fresh status, failure and recovery', async () => {
    await render(<DashboardTelemetry />);
    expect(client.getQueryCache().find({ queryKey: ['execution-targets'] })!.getObserversCount()).toBe(1);
    expect(count('/api/execution-targets')).toBe(1);
    await tick(5100);
    expect(count('/api/execution-targets')).toBe(2);
    expect(count('/api/gpu/status')).toBe(6);
    expect(text(root!.root)).toContain('Fresh local GPU');
    targetError = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); }); await tick();
    expect(text(root!.root)).toContain('Vast inventory unavailable');
    expect(root!.root.findAllByProps({ role: 'tab' })).toHaveLength(1);
    targetError = false; targets = [{ ...worker, name: 'Recovered worker' }];
    await act(async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); }); await tick();
    expect(text(root!.root)).toContain('Recovered worker');
    expect(client.getQueryCache().find({ queryKey: ['execution-targets'] })!.getObserversCount()).toBe(1);
});

it('standalone telemetry retains its own five-second inventory polling and native status cadence', async () => {
    await render(<InfraLiveTelemetry />);
    expect(count('/api/execution-targets')).toBe(1);
    await tick(10100);
    expect(count('/api/execution-targets')).toBe(3);
    expect(count('/api/gpu/status')).toBe(11);
});

function StatusConsumer() {
    const query = useSystemStatus();
    return <span>{query.data?.data.timestamp}</span>;
}
it.each([null, 'vast:one'])('provider-only prediction removes local/remote GPU polling and aging timer, then restores native freshness (%s)', async target => {
    if (target) sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target);
    const draft = vi.fn();
    const intervals = vi.spyOn(globalThis, 'setInterval');
    await render(<StructurePredictionTemplate onBack={() => {}} initialValues={{ pred_method: 'boltz_api', sequence: 'ACDE', boltz_use_msa: false, boltz_num_samples: 2, run_frustrampnn: false }} onDraftChange={draft} />);
    expect(count('/api/jobs/boltz-api/status')).toBe(1);
    expect(count('/api/gpu/status')).toBe(0);
    expect(count('/api/execution-targets/active/telemetry')).toBe(0);
    expect(intervals.mock.calls.filter(call => call[1] === 1000)).toHaveLength(0);
    await click('Estimate API cost');
    expect(estimates).toEqual([{ name: 'structure_prediction', client_request_id: expect.any(String), model: 'boltz-2.1', sequence: 'ACDE', primary_chain_id: 'A', complex_components: [], num_samples: 2, use_msa: false }]);
    const before = draft.mock.calls.length;
    await tick(12100);
    expect(count('/api/gpu/status')).toBe(0);
    expect(count('/api/execution-targets/active/telemetry')).toBe(0);
    expect(draft).toHaveBeenCalledTimes(before);
    expect(draft.mock.lastCall![0]).toMatchObject({ pred_method: 'boltz_api', sequence: 'ACDE', boltz_use_msa: false, boltz_num_samples: 2, run_frustrampnn: false });
    await click('Boltz');
    expect(intervals.mock.calls.filter(call => call[1] === 1000)).toHaveLength(1);
    expect(count('/api/gpu/status')).toBe(1);
    expect(count('/api/execution-targets/active/telemetry')).toBe(target ? 1 : 0);
    expect(text(root!.root)).toContain(target ? 'Fresh remote GPU' : 'Fresh local GPU');
    await tick(5100);
    expect(count('/api/gpu/status')).toBe(2);
    await click('Boltz API');
    const stopped = count('/api/gpu/status');
    const remoteStopped = count('/api/execution-targets/active/telemetry');
    await tick(12100);
    expect(count('/api/gpu/status')).toBe(stopped);
    expect(count('/api/execution-targets/active/telemetry')).toBe(remoteStopped);
});

it('provider-only prediction does not stop another mounted native status consumer', async () => {
    await render(<><StructurePredictionTemplate onBack={() => {}} initialValues={{ pred_method: 'boltz_api', run_frustrampnn: false }} /><StatusConsumer /></>);
    expect(count('/api/gpu/status')).toBe(1);
    await tick(10100);
    expect(count('/api/gpu/status')).toBe(3);
});

it('Mutagenesis edits update the real picker request without remounting or losing selected placement', async () => {
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, worker.id);
    await render(<MutagenesisTemplate onBack={() => {}} onSubmit={() => {}} />);
    const picker = root!.root.findByType(ExecutionTargetPicker);
    const textarea = root!.root.findAllByType('textarea')[0];
    await act(async () => textarea.props.onChange({ target: { value: 'ACDEFGHIK' } })); await tick();
    const region = root!.root.findAllByType('input').find(n => n.props.placeholder === 'e.g., 10-20, 45-50')!;
    await act(async () => region.props.onChange({ target: { value: '1-9' } })); await tick();
    const generate = root!.root.findAllByType('button').find(n => text(n).includes('Generate Preview'))!;
    expect(generate).toBeTruthy();
    await act(async () => generate.props.onClick()); await tick();
    const firstRequest = root!.root.findByType(ExecutionTargetPicker).props.workflowRequest;
    expect(firstRequest).toBeTruthy();
    expect(mounts.provision).toHaveBeenCalledTimes(1);
    const number = root!.root.findAllByType('input').find(n => n.props.type === 'number' && n.props.value === 20)!;
    await act(async () => number.props.onChange({ target: { value: '3' } })); await tick();
    expect(root!.root.findByType(ExecutionTargetPicker) === picker).toBe(true);
    expect(root!.root.findByType(ExecutionTargetPicker).props.workflowRequest).toBeNull();
    await act(async () => generate.props.onClick()); await tick();
    expect(root!.root.findByType(ExecutionTargetPicker).props.workflowRequest).not.toEqual(firstRequest);
    expect(root!.root.findAllByType('button').find(n => text(n).trim() === 'Vast · Worker one')!.props['aria-pressed']).toBe(true);
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe(worker.id);
    expect(count('/api/execution-targets')).toBe(1);
    expect(client.getQueryCache().find({ queryKey: ['execution-targets'] })!.getObserversCount()).toBe(1);
});
