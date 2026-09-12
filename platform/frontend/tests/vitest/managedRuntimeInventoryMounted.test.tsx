import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ManagedRuntimeInventoryPanel } from '../../src/components/dashboard/ManagedRuntimeInventoryPanel';
import { api, type ExecutionTarget, type ManagedRuntimeInventory } from '../../src/lib/api';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const ready: ExecutionTarget = { id: 'vast:123', provider: 'vast', provider_instance_id: '123', name: 'Worker', state: 'ready', active: true, host: 'host', port: 22, username: 'root', remote_root: '/opt/bms', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null };
const observation = (): ManagedRuntimeInventory => ({ observed_at: '2026-09-07T15:00:00Z', boot_id: 'boot-identity', state: 'current', scope: 'managed_independent_asset_releases', scientific_ready: false, critical_runtime_ready: false, blockers: ['critical_release_not_verified', 'scientific_readiness_not_checked'], releases: [
  { selection: { kind: 'model', model_id: 'protenix' }, release_sha256: 'a'.repeat(64), source_revision: 'b'.repeat(40), source_tree: 'c'.repeat(40), state: 'partial', artifacts: [{ name: 'weights/model.pt', size_bytes: 1234, sha256: 'd'.repeat(64), state: 'missing' }] },
  { selection: { kind: 'image', model_id: 'esmfold2' }, release_sha256: 'e'.repeat(64), source_revision: 'b'.repeat(40), source_tree: 'c'.repeat(40), state: 'verified', artifacts: [{ name: 'images/esmfold2.sif', size_bytes: 5678, sha256: 'f'.repeat(64), state: 'verified' }] },
] });
let container: HTMLDivElement; let root: Root; let client: QueryClient; let target: ExecutionTarget;
let saved: ManagedRuntimeInventory | null; let requests: Array<{ method?: string; url?: string; data: unknown }>;
const adapter = api.defaults.adapter;
const settle = () => new Promise(resolve => setTimeout(resolve, 25));
const render = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><ManagedRuntimeInventoryPanel target={target} /></QueryClientProvider>); await settle(); }); await act(async () => { await settle(); }); };
const button = (text = 'Refresh installed observation') => [...container.querySelectorAll('button')].find(b => b.textContent === text)!;
const click = async (text?: string) => { await act(async () => { button(text).click(); await settle(); }); await act(async () => { await settle(); }); };
beforeEach(() => {
  saved = observation(); target = { ...ready }; requests = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  api.defaults.adapter = async config => { requests.push({ method: config.method, url: config.url, data: config.data }); return response(saved); };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = adapter; vi.restoreAllMocks(); });

it('accepts the actual critical-runtime boolean and compatibility shape, without promoting stale readiness', async () => {
  saved!.critical_runtime_ready = true;
  saved!.blockers = ['scientific_readiness_not_checked'];
  saved!.releases.push({ selection: { kind: 'critical_runtime', model_id: 'worker' },
    release_sha256: '1'.repeat(64), source_revision: '2'.repeat(40), source_tree: '3'.repeat(40),
    state: 'verified', artifacts: [], critical: { compatible: true, requirements: { os: 'Linux' }, observed: { os: 'Linux' } } });
  await render();
  expect(container.textContent).toContain('Critical runtime ready: true');
  expect(container.textContent).toContain('critical_runtime · worker');
  expect(container.textContent).toContain('os: required Linux · observed Linux');
  expect(container.textContent).not.toContain('Unsupported managed inventory observation');
  saved = { ...saved!, state: 'stale' };
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(container.textContent).toContain('Critical runtime ready: unknown (no fresh evidence)');
  expect(container.textContent).toContain('Last observed critical readiness: true');
  expect(requests.every(request => request.method === 'get')).toBe(true);
});
it.each(['passed', 'failed'] as const)('retains scoped native %s evidence across polling without promoting scientific readiness', async outcome => {
  saved!.critical_runtime_ready = true;
  saved!.releases[0].native_readiness = {
    state: outcome === 'passed' ? 'unverified' : 'blocked', scope: 'native_runtime_preflight_only',
    authority: 'test-native-authority', blockers: outcome === 'failed' ? ['native_runtime_preflight_failed'] : [],
    missing_authorities: ['complete_selected_model_native_probe_coverage'],
    probe: { authority: 'scripts/check_rfantibody_runtime.py:run_preflight', outcome, gpu_id: 0,
      observed_at: saved!.observed_at, release_sha256: 'a'.repeat(64), image_sha256: 'd'.repeat(64),
      script_sha256: 'e'.repeat(64), source_revision: 'b'.repeat(40), source_tree: 'c'.repeat(40),
      boot_id: saved!.boot_id },
  };
  await render();
  expect(container.textContent).toContain(`Last recorded native preflight: ${outcome} · GPU 0`);
  expect(container.textContent).toContain('Scientific ready: false');
  expect(container.textContent).toContain('complete_selected_model_native_probe_coverage');
  for (const state of ['current', 'stale'] as const) {
    saved = { ...saved!, state };
    await act(async () => { await client.invalidateQueries(); await settle(); });
    expect(container.textContent).toContain(`Last recorded native preflight: ${outcome}`);
    expect(container.textContent).toContain('weights/model.pt');
    expect(container.textContent).toContain('Scientific ready: false');
  }
  expect(container.textContent).toContain('(historical; not current readiness)');
  expect(requests.every(request => request.method === 'get')).toBe(true);
});
it('GETs saved cumulative image/model evidence without POST, keeping freshness distinct from asset state/readiness', async () => {
  await render();
  expect(requests).toEqual([{ method: 'get', url: '/api/execution-targets/vast%3A123/runtime-inventory', data: undefined }]);
  for (const text of ['Fresh observation (current)', 'model · protenix · Release state: partial', 'image · esmfold2 · Release state: verified', 'Artifact state: missing', '2026-09-07T15:00:00Z', 'Critical runtime ready: false', 'Scientific ready: false', '1,234 bytes', 'd'.repeat(64), 'boot-identity']) expect(container.textContent).toContain(text);
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(requests.every(r => r.method === 'get')).toBe(true);
});
it('explicit refresh sends one bodyless POST despite double click, then reloads saved evidence', async () => {
  saved!.state = 'stale'; await render(); expect(container.textContent).toContain('Stale observation');
  saved = observation();
  await act(async () => { const b = button(); b.click(); b.click(); await settle(); });
  await act(async () => { await settle(); });
  expect(requests.filter(r => r.method === 'post')).toEqual([{ method: 'post', url: '/api/execution-targets/vast%3A123/runtime-inventory/refresh', data: undefined }]);
  expect(container.textContent).toContain('Fresh observation (current)');
});
it('retains prior evidence but not freshness after refresh failure; recovery requires an explicit click', async () => {
  await render(); saved!.state = 'stale';
  const post = vi.spyOn(api, 'post').mockRejectedValueOnce({ isAxiosError: true, response: { data: { detail: 'Worker observation failed' } } });
  await click();
  expect(post).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain('Worker observation failed');
  expect(container.textContent).toContain('Stale observation');
  expect(container.textContent).toContain('weights/model.pt');
  expect(container.textContent).not.toContain('Fresh observation (current)');
  post.mockRestore(); saved = observation(); await click();
  expect(container.textContent).toContain('Fresh observation (current)');
});
it.each(['missing', 'corrupt', 'incompatible', 'unverified', 'partial', 'verified'] as const)('renders %s release state without changing readiness', async state => {
  saved!.releases[0].state = state; await render();
  expect(container.textContent).toContain(`Release state: ${state}`);
  expect(container.textContent).toContain('Critical runtime ready: false · Scientific ready: false');
});
it.each([null, { ...observation(), releases: [] }])('does not promote empty or absent inventory to readiness', async data => {
  saved = data; await render();
  expect(container.textContent).toContain(data ? 'No tracked releases' : 'No saved managed observation');
  expect(requests.every(r => r.method === 'get')).toBe(true);
});
it('does not display another endpoint observation while its new saved GET is pending', async () => {
  await render();
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.spyOn(api, 'get').mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  target = { ...target, host: 'replacement' }; await render();
  expect(container.textContent).not.toContain('weights/model.pt');
  await act(async () => { resolve(response(null)); await settle(); });
  expect(container.textContent).toContain('No saved managed observation');
});
it('GET errors allow saved-only retry, without SSH refresh', async () => {
  vi.spyOn(api, 'get').mockRejectedValueOnce(new Error('Saved read unavailable'));
  await render(); expect(container.textContent).toContain('Saved read unavailable');
  await click('Reload saved observation');
  expect(container.textContent).toContain('weights/model.pt');
  expect(requests.every(r => r.method === 'get')).toBe(true);
});
it('rejects observations that claim scientific readiness or use another scope', async () => {
  api.defaults.adapter = async () => response({ ...observation(), scientific_ready: true });
  await render();
  expect(container.textContent).toContain('Unsupported managed inventory observation');
  expect(container.textContent).not.toContain('Fresh observation (current)');
  expect(container.querySelector('[aria-label="Managed releases"]')).toBeNull();
});
it('a late refresh cannot overwrite the replacement endpoint view', async () => {
  await render();
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  await click();
  saved = null; target = { ...target, host: 'replacement' }; await render();
  await act(async () => { resolve(response(observation())); await settle(); });
  expect(container.textContent).not.toContain('weights/model.pt');
  expect(container.textContent).toContain('No saved managed observation');
});
it.each(['inactive', 'busy', 'running'])('disables refresh for %s workers without mutation', async state => {
  if (state === 'inactive') target.active = false;
  if (state === 'busy') target.state = 'discovered';
  if (state === 'running') target.progress = { operation_id: 'run', job_id: 'job', phase: 'running', artifact: null, message: 'Running', updated_at: 'now' };
  await render(); expect(button().disabled).toBe(true); await click();
  expect(requests.every(r => r.method === 'get')).toBe(true);
});

it.each(['cancelling', 'recovery_blocked', 'cancelled'] as const)('does not promote stored critical readiness while %s has unresolved transport ownership', async phase => {
  saved!.critical_runtime_ready = true;
  saved!.releases.push({ selection: { kind: 'workflow', model_id: '1'.repeat(64) }, release_sha256: '2'.repeat(64),
    source_revision: '3'.repeat(40), source_tree: '4'.repeat(40), state: 'verified', artifacts: [],
    bounded_readiness: 'verified_assets_and_critical_runtime', readiness_scope: 'asset_integrity_and_critical_compatibility_only' });
  await render(); expect(container.textContent).toContain('Critical runtime ready: true');
  target = { ...target, preload: { operation_id: 'recovering', selection: { kind: 'model', model_id: 'boltz2' },
    source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase,
    recovery_required: true, artifact: null, message: 'Transport quiescence unknown', started_at: 'now', updated_at: 'now' } };
  await render();
  expect(container.textContent).toContain('Critical runtime ready: unknown (no fresh evidence)');
  expect(container.textContent).toContain('Bounded readiness: stale');
  expect(button().disabled).toBe(true);
  expect(requests.every(request => request.method === 'get')).toBe(true);
});
