import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RemotePreloadPanel } from '../../src/components/dashboard/RemotePreloadPanel';
import { api, type ExecutionTarget, type ProvisionPreview, type ProvisionSelection } from '../../src/lib/api';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const catalog: ProvisionSelection[] = [{ kind: 'model', model_id: 'protenix' }, { kind: 'image', model_id: 'protenix' }, { kind: 'model', model_id: 'esmfold2' }];
const artifacts = [{ name: 'runtime/protenix.sif', sha256: 'a'.repeat(64), size_bytes: 1234 }];
const preview = (selection: ProvisionSelection): ProvisionPreview => ({ selection, artifacts, total_bytes: 1234, preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'cache_download_only' });
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
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  api.defaults.adapter = async config => {
    if (config.method === 'get') return response(config.url?.endsWith('/catalog') ? catalog : [target]);
    const body = JSON.parse(String(config.data));
    posts.push({ url: String(config.url), body });
    return response(config.url?.endsWith('/preview') ? preview(body) : target);
  };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = adapter; vi.restoreAllMocks(); });

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
  const get = vi.spyOn(api, 'get').mockRejectedValueOnce(new Error('Catalog unavailable'));
  await render();
  expect(container.textContent).toContain('Catalog unavailable');
  expect(button('Start provision').disabled).toBe(true);
  expect(get).toHaveBeenCalledTimes(1);
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
