import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RemotePreloadPanel } from '../../src/components/dashboard/RemotePreloadPanel';
import { WorkflowProvisionPanel } from '../../src/components/dashboard/IndependentProvisionPanel';
import { api, type ExecutionTarget, type ProvisionPreview, type ProvisionSelection } from '../../src/lib/api';

import { MemoryRouter } from 'react-router-dom';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { ExecutionPolicyControl } from '../../src/components/ExecutionPolicyControl';
import { EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { setDraftExecutionPolicy } from '../../src/lib/executionPolicy';
// These alternative workflows are not mounted in the de-novo request-owner tests.
vi.mock('../../src/components/ProteinLocalRedesignTemplate', () => ({ ProteinLocalRedesignTemplate: () => null }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: () => null }));

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const catalog: ProvisionSelection[] = [{ kind: 'model', model_id: 'protenix' }, { kind: 'image', model_id: 'protenix' }, { kind: 'model', model_id: 'esmfold2' }];
const artifacts = [{ name: 'runtime/protenix.sif', sha256: 'a'.repeat(64), size_bytes: 1234 }];
const preview = (selection: ProvisionSelection): ProvisionPreview => ({ selection, artifacts, total_bytes: 1234, preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'managed_asset_activation' });
const ready: ExecutionTarget = { id: 'vast:123', provider: 'vast', provider_instance_id: '123', name: 'Worker', state: 'ready', active: true, host: 'host', port: 22, username: 'root', remote_root: '/opt/bms', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null };
const adapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let target: ExecutionTarget;
let posts: Array<{ url: string; body: Record<string, string> }>;
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const changed = vi.fn(async () => {});
const render = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[]} onChanged={changed} /></QueryClientProvider>); }); await act(async () => { await settle(); }); };
const button = (text: string) => [...container.querySelectorAll('button')].find(b => b.textContent === text)!;
const click = async (text: string, twice = false) => { await act(async () => { const b = button(text); b.click(); if (twice) b.click(); await settle(); }); };
const select = async (label: string, value: string) => { await act(async () => { const s = container.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!; s.value = value; s.dispatchEvent(new Event('change', { bubbles: true })); await settle(); }); };
beforeEach(() => {
  target = { ...ready }; posts = []; changed.mockClear();
  window.history.replaceState({}, '', '/submit');
  window.sessionStorage.clear(); window.localStorage.clear(); setDraftExecutionPolicy(undefined);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  api.defaults.adapter = async config => {
    if (config.method === 'get') return response(config.url?.endsWith('/catalog') ? catalog : config.url?.endsWith('/runtime-inventory') ? null : [target]);
    const body = config.data ? JSON.parse(String(config.data)) : undefined;
    posts.push({ url: String(config.url), body });
    return response(config.url?.endsWith('/preview') ? preview(body) : target);
  };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = adapter; vi.restoreAllMocks(); });

it('binds the unsaved typed workflow settings, invalidates edits, and never submits a Job', async () => {
  let request = { name: 'Unsaved', model_id: 'protenix', mode: 'predict', params: { sequence: 'ACDE', seeds: [7] } };
  const mountWorkflow = async () => { await act(async () => {
    root.render(<QueryClientProvider client={client}><WorkflowProvisionPanel target={target} workflowRequest={request} onChanged={changed} /></QueryClientProvider>);
    await settle();
  }); };
  await mountWorkflow(); expect(posts).toEqual([]);
  await click('Preview artifact downloads');
  expect(posts[0].body).toEqual({ kind: 'workflow', workflow_request: request });
  request = { ...request, params: { ...request.params, seeds: [9] } };
  await mountWorkflow(); expect(button('Start provision').disabled).toBe(true);
  await click('Preview artifact downloads'); await click('Start provision');
  expect(posts.at(-1)?.body).toEqual({ kind: 'workflow', workflow_request: request, preview_sha256: 'b'.repeat(64) });
  expect(posts.every(post => post.url.includes('/provision'))).toBe(true);
});

it('renders exact dependency estimates and blocks mutation on preview blockers', async () => {
  await render(); await select('Provision model', 'protenix');
  vi.spyOn(api, 'post').mockResolvedValueOnce(response({ ...preview(catalog[0]),
    destination: { target_id: ready.id, remote_root: ready.remote_root }, inventory_state: 'stale',
    transfer_bytes: 1234, storage_bytes: 1234, asset_states: [{ ...artifacts[0], state: 'unknown' }], blockers: ['Critical runtime unavailable'] }));
  await click('Preview artifact downloads');
  expect(container.textContent).toContain('Destination: vast:123 · /opt/bms');
  expect(container.textContent).toContain('Transfer upper bound: 1,234 bytes');
  expect(container.textContent).toContain('Critical runtime unavailable');
  expect(button('Start provision').disabled).toBe(true);
});

it('uses operation-bound cancellation and keeps uncertain recovery blocked', async () => {
  target.preload = { operation_id: 'op/1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'recovery_blocked', artifact: artifacts[0].name, message: 'Quiescence unknown',
    started_at: 'now', updated_at: 'now', recovery_required: true, cancel_requested: true, sequence: 4,
    artifact_progress: [{ ...artifacts[0], state: 'interrupted' }] };
  await render();
  expect(container.textContent).toContain('Sequence 4');
  expect(container.textContent).toContain('interrupted');
  expect(button('Retry provision with fresh preview').disabled).toBe(true);
  await click('Recheck cancellation quiescence', true);
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision/op%2F1/cancel', body: undefined }]);
  expect(container.textContent).toContain('Ownership is not released');
});

it('retries a stopped operation only with a fresh preview of its persisted selection', async () => {
  target.preload = { operation_id: 'op1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'cancelled', artifact: null, message: 'Stopped', started_at: 'now', updated_at: 'now' };
  await render();
  const operation = container.querySelector('[aria-label="Provision operation"]')!;
  await act(async () => { [...operation.querySelectorAll('button')].find(b => b.textContent === 'Preview artifact downloads')!.click(); await settle(); });
  await click('Retry provision with fresh preview');
  expect(posts.at(-1)).toEqual({ url: '/api/execution-targets/vast%3A123/provision/op1/retry', body: { ...catalog[0], preview_sha256: 'b'.repeat(64) } });
});

it('mounts in the real worker panel without Jobs; previews exact bytes then starts once with the digest', async () => {
  await render();
  expect(container.textContent).toContain('No saved Job required');
  expect(container.textContent).toContain('Installed artifacts are unknown');
  expect(button('Start provision').disabled).toBe(true);
  await select('Provision model', 'protenix');
  expect(posts).toEqual([]);
  await click('Preview artifact downloads', true);
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'model', model_id: 'protenix' } }]);
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).toContain('1,234 bytes');
  expect(container.textContent).toContain(artifacts[0].name);
  expect(container.textContent).toContain(artifacts[0].sha256);
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).toContain('additional installed copy');
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).toContain('retains prior release generations');
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).toContain('not a missing-byte transfer estimate');
  expect(container.textContent).toContain('b'.repeat(64));
  await click('Start provision', true);
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual({ url: '/api/execution-targets/vast%3A123/provision', body: { kind: 'model', model_id: 'protenix', preview_sha256: 'b'.repeat(64) } });
  expect(changed).toHaveBeenCalledTimes(1);
  expect(button('Start provision').disabled).toBe(true);
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(posts).toHaveLength(2);
});

it('invalidates preview on model, image scope, endpoint and observed source changes', async () => {
  await render(); await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  await select('Provision model', 'esmfold2');
  expect(button('Start provision').disabled).toBe(true);
  await select('Provision scope', 'image'); await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  expect(posts.at(-1)?.body).toEqual({ kind: 'image', model_id: 'protenix' });
  target = { ...target, host: 'replacement' }; await render();
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  expect(button('Start provision').disabled).toBe(true);
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  target = { ...target, preload: { operation_id: 'another', selection: catalog[0], source_revision: 'd'.repeat(40), source_tree: 'e'.repeat(40), request_sha256: 'f'.repeat(64), phase: 'source_download_ready', artifact: null, message: 'Previous cache ready', started_at: '2026-01-01', updated_at: '2026-01-01' } }; await render();
  expect(button('Start provision').disabled).toBe(true);
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(false);
  target = { ...target, preload: { ...target.preload!, source_tree: 'f'.repeat(40) } }; await render();
  expect(button('Start provision').disabled).toBe(true);
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  target = { ...target, id: 'vast:456' }; await render();
  expect(button('Start provision').disabled).toBe(true);
});

it('discards a late preview after selection change and requires explicit preview retry after an API error', async () => {
  await render(); await select('Provision model', 'protenix');
  let resolve!: (value: ReturnType<typeof response>) => void;
  const post = vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  await click('Preview artifact downloads'); await select('Provision model', 'esmfold2');
  await act(async () => { resolve(response(preview(catalog[0]))); await settle(); });
  expect(button('Start provision').disabled).toBe(true);
  post.mockRejectedValueOnce({ isAxiosError: true, response: { data: { detail: 'Managed weights missing' } } });
  await click('Preview artifact downloads');
  expect(container.textContent).toContain('Managed weights missing');
  expect(button('Start provision').disabled).toBe(true);
  post.mockRestore(); await click('Preview artifact downloads');
  const reject = vi.spyOn(api, 'post').mockRejectedValue({ isAxiosError: true, response: { data: { detail: 'Source changed; preview again' } } });
  await click('Start provision', true);
  expect(reject).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain('Source changed; preview again');
  expect(button('Start provision').disabled).toBe(true);
});

it('rejects the retired cache-only preview scope before enabling provision', async () => {
  await render(); await select('Provision model', 'protenix');
  vi.spyOn(api, 'post').mockResolvedValueOnce(response({ ...preview(catalog[0]), scope: 'cache_download_only' }));
  await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(true);
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
});

it('restores only the persisted last receipt and displays stale/failed status without auto-provision', async () => {
  target = { ...target, artifact_inventory: { operation_id: 'receipt', selection: catalog[0], artifacts, observed_at: '2026-01-01T00:00:00Z', state: 'download_verified', scope: 'last_independent_provision', scientific_ready: false } };
  await render();
  expect(container.textContent).toContain('Cache downloads verified at observation');
  expect(container.textContent).toContain('Not a full installed inventory');
  expect(container.textContent).toContain('not scientific readiness');
  await act(async () => root.render(null)); await render();
  expect(container.textContent).toContain('2026-01-01T00:00:00Z');
  target = { ...target, artifact_inventory: { ...target.artifact_inventory!, state: 'stale' } }; await render();
  expect(container.textContent).toContain('Stale cache observation');
  expect(posts).toEqual([]);
});

it('surfaces catalog failure with explicit retry and no provision side effects', async () => {
  const originalGet = api.get.bind(api);
  const get = vi.spyOn(api, 'get').mockImplementation((url, config) => String(url).endsWith('/catalog')
    ? Promise.reject(new Error('Catalog unavailable')) : originalGet(url, config));
  await render();
  expect(container.textContent).toContain('Catalog unavailable');
  expect(button('Start provision').disabled).toBe(true);
  expect(get.mock.calls.filter(([url]) => String(url).endsWith('/catalog'))).toHaveLength(1);
  get.mockRestore(); await click('Retry catalog');
  await select('Provision model', 'protenix');
  expect(button('Preview artifact downloads').disabled).toBe(false);
  expect(posts).toEqual([]);
});

it('does not restore an old target preview when its request resolves after endpoint replacement', async () => {
  await render(); await select('Provision model', 'protenix');
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  await click('Preview artifact downloads');
  target = { ...target, host_key_sha256: 'd'.repeat(64) }; await render();
  await act(async () => { resolve(response(preview(catalog[0]))); await settle(); });
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  expect(button('Start provision').disabled).toBe(true);
});

it.each(['busy', 'running', 'inactive'])('blocks provisioning when worker is %s', async state => {
  if (state === 'inactive') target = { ...target, active: false };
  if (state === 'running') target = { ...target, progress: { operation_id: 'run', job_id: 'job', phase: 'running', artifact: null, message: 'Running', updated_at: 'now' } };
  if (state === 'busy') target = { ...target, preload: { operation_id: 'op', job_id: 'job', source_revision: 'a'.repeat(40), source_tree: 'a'.repeat(40), request_sha256: 'a'.repeat(64), phase: 'transferring', artifact: null, message: 'Transferring', started_at: 'now', updated_at: 'now' } };
  await render(); await select('Provision model', 'protenix');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  expect(button('Start provision').disabled).toBe(true);
  expect(posts).toEqual([]);
});


it.each(['cancelling', 'recovery_blocked', 'cancelled'] as const)('fences saved-Job preloading during %s with unresolved recovery and labels workflow receipts', async phase => {
  target.preload = { operation_id: 'owned', selection: { kind: 'workflow', workflow_request: { name: 'Draft', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } } },
    source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase, recovery_required: true,
    artifact: null, message: 'Recovery pending', started_at: 'now', updated_at: 'now' };
  await act(async () => { root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[{ id: 'saved', model_id: 'boltz2' }]} onChanged={changed} /></QueryClientProvider>); await settle(); });
  expect(container.querySelector('[aria-label="Preload progress"]')?.textContent).toContain('workflow boltz2 / predict');
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')?.disabled).toBe(true);
  expect(button('Preloading…').disabled).toBe(true);
  expect(posts).toEqual([]);
});

it('does not resurrect an approved workflow preview after a cancellation/recovery transition', async () => {
  const request = { name: 'Draft', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } };
  const mount = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><WorkflowProvisionPanel target={target} workflowRequest={request} onChanged={changed} /></QueryClientProvider>); await settle(); }); };
  target.preload = { operation_id: 'old', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase: 'source_download_ready', artifact: null, message: '', started_at: 'now', updated_at: 'now' };
  await mount(); await click('Preview artifact downloads'); expect(button('Start provision').disabled).toBe(false);
  target = { ...target, preload: { ...target.preload!, phase: 'cancelling', recovery_required: true } }; await mount();
  target = { ...target, preload: { ...target.preload!, phase: 'cancelled', recovery_required: false } }; await mount();
  expect(button('Start provision').disabled).toBe(true);
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  expect(posts).toHaveLength(1);
});

it('the real de-novo typed form provisions the same unsaved request as launch and invalidates scientific edits', async () => {
  window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target.id);
  await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate onBack={() => {}} /></MemoryRouter></QueryClientProvider>); await settle(); });
  await act(async () => { await settle(); });
  expect(posts).toEqual([]);
  await click('Preview artifact downloads');
  const original = posts.at(-1)!.body as unknown as { workflow_request: { model_id: string; mode: string; params: Record<string, unknown> } };
  expect(original.workflow_request).toMatchObject({ model_id: 'protein_modification_experimental', mode: 'de_novo_design', params: { generator: 'rfd3', generation_mode: 'unconditional_monomer', min_length: 100, max_length: 200, num_designs: 8, seed: 0, dump_trajectories: false } });
  const input = [...container.querySelectorAll('label')].find(label => label.textContent?.startsWith('Minimum length'))!.querySelector('input')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '120'); input.dispatchEvent(new Event('input', { bubbles: true })); await settle(); });
  expect(button('Start provision').disabled).toBe(true);
  await click('Preview artifact downloads');
  const approved = posts.at(-1)!.body as unknown as { workflow_request: Record<string, unknown> };
  expect(approved.workflow_request).toMatchObject({ params: { min_length: 120 } });
  expect(posts.every(post => post.url.endsWith('/provision/preview'))).toBe(true);
  const launch = [...container.querySelectorAll('button')].find(item => item.textContent?.includes('Launch') && item.textContent?.includes('RFD3'))!;
  expect(launch).toBeTruthy();
  await act(async () => { launch.click(); await settle(); });
  expect(posts.find(post => post.url === '/api/jobs')?.body).toEqual(approved.workflow_request);
});

it.each(['start', 'retry', 'cancel'] as const)('refreshes the target once after provision %s', async action => {
  if (action !== 'start') target.preload = {
    operation_id: 'owned', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: action === 'cancel' ? 'transferring' : 'cancelled',
    artifact: null, message: '', started_at: 'now', updated_at: 'now',
  };
  window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target.id);
  const request = { name: 'Draft', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } };
  await act(async () => { root.render(<QueryClientProvider client={client}><ExecutionTargetPicker workflowRequest={request} /></QueryClientProvider>); await settle(); });
  await act(async () => { await settle(); });
  if (action === 'start') await click('Preview artifact downloads');
  if (action === 'retry') await act(async () => {
    const operation = container.querySelector('[aria-label="Provision operation"]')!;
    [...operation.querySelectorAll('button')].find(item => item.textContent === 'Preview artifact downloads')!.click();
    await settle();
  });
  const get = vi.spyOn(api, 'get');
  await click(action === 'start' ? 'Start provision' : action === 'retry' ? 'Retry provision with fresh preview' : 'Cancel provision');
  await act(async () => { await settle(); });
  expect(get.mock.calls.filter(([url]) => url === '/api/execution-targets')).toHaveLength(1);
  expect(posts.at(-1)?.url).toBe(action === 'start' ? '/api/execution-targets/vast%3A123/provision'
    : `/api/execution-targets/vast%3A123/provision/owned/${action}`);
});

it('invalidates the picker preview immediately on execution-policy and target changes without implicit provisioning', async () => {
  window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target.id);
  const request = { name: 'Draft', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } };
  await act(async () => { root.render(<QueryClientProvider client={client}><ExecutionTargetPicker workflowRequest={request} /><ExecutionPolicyControl /></QueryClientProvider>); await settle(); });
  await act(async () => { await settle(); });
  await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(false);
  await select('Successful remote results', 'automatic');
  expect(button('Start provision').disabled).toBe(true);
  await click('Preview artifact downloads');
  expect(posts.at(-1)?.body).toMatchObject({ workflow_request: { execution_policy: { remote_result_policy: 'automatic' } } });
  await click('Local');
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  await click('Vast · Worker'); expect(button('Start provision').disabled).toBe(true);
  expect(posts).toHaveLength(2);
});
