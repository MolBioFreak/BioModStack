import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { RemotePreloadPanel } from '../../src/components/dashboard/RemotePreloadPanel';
import { IndependentProvisionPanel, WorkflowProvisionPanel } from '../../src/components/dashboard/IndependentProvisionPanel';
import { api, type ExecutionTarget, type ArtifactPage, type PreloadArtifactReceipt } from '../../src/lib/api';

const summary = { total_count: 205, verified_count: 137, total_bytes: 9000, verified_bytes: 6000 };
const ready: ExecutionTarget = { id: 'vast:123', provider: 'vast', provider_instance_id: '123', name: 'Worker', state: 'ready', active: true, host: 'host', port: 22, username: 'root', remote_root: '/opt/bms', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null,
  preload: { operation_id: 'op/1', job_id: 'recipe', source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase: 'transferring', artifact: null, message: 'Downloading', started_at: '2026-01-01', updated_at: '2026-01-01', sequence: 4, artifact_summary: summary, cached_artifact_count: 137 } };
const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const adapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let target: ExecutionTarget;
let gets: Array<{ url: string; params: Record<string, unknown> }>;
let posts: Array<{ url: string; body: unknown }>;
let failOffset: number | undefined;
let empty: boolean;
let delayed: (() => Promise<ReturnType<typeof response>>) | undefined;
const settle = () => new Promise(resolve => setTimeout(resolve, 30));
const request = { name: 'Draft', model_id: 'protenix', mode: 'predict', params: { sequence: 'ACDE', seeds: [7], use_template: false } };
const mount = async (surface: 'saved' | 'workflow' | 'independent' = 'saved') => {
  await act(async () => { root.render(<QueryClientProvider client={client}>{surface === 'saved'
    ? <RemotePreloadPanel target={target} jobs={[{ id: 'recipe', model_id: 'protenix' }]} onChanged={() => {}} />
    : surface === 'workflow' ? <WorkflowProvisionPanel target={target} workflowRequest={request} onChanged={() => {}} />
    : <IndependentProvisionPanel target={target} onChanged={() => {}} />}</QueryClientProvider>); await settle(); });
};
const disclose = async (label: string) => { await act(async () => {
  const details = [...container.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent?.startsWith(label))!;
  expect(details).toBeTruthy(); details.open = !details.open; details.dispatchEvent(new Event('toggle')); await settle();
}); };
const click = async (label: string) => { await act(async () => { [...container.querySelectorAll('button')].find(item => item.textContent === label)!.click(); await settle(); }); };
const artifactGets = () => gets.filter(item => item.url.endsWith('/artifacts'));
function page(offset: number, operationId: string): ArtifactPage<PreloadArtifactReceipt> {
  const total = empty ? 0 : 205;
  return { items: Array.from({ length: Math.min(100, Math.max(0, total - offset)) }, (_, i) => ({ name: `${operationId}/file-${offset + i}`, sha256: 'd'.repeat(64), size_bytes: 10, state: 'verified' })), total_count: total, offset, limit: 100, operation_id: operationId, sequence: 4 };
}
beforeEach(() => {
  target = structuredClone(ready); gets = []; posts = []; failOffset = undefined; delayed = undefined; empty = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  api.defaults.adapter = async config => {
    const url = String(config.url);
    if (config.method === 'get') {
      gets.push({ url, params: config.params });
      if (url.endsWith('/artifacts')) {
        if (delayed) return delayed();
        const offset = Number(config.params.offset);
        if (offset === failOffset) throw new Error('Artifact page unavailable');
        return response(page(offset, url.includes('/artifact-inventory/') ? target.artifact_inventory!.operation_id : target.preload!.operation_id));
      }
      return response(url === '/api/models' || url === '/api/templates' || url.endsWith('/catalog') ? [] : null);
    }
    const body = config.data ? JSON.parse(String(config.data)) : undefined;
    posts.push({ url, body });
    return response(url.endsWith('/preview') ? { selection: body, artifacts: [], total_bytes: 0, preview_sha256: 'e'.repeat(64), scientific_ready: false, scope: 'download_only' } : target);
  };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = adapter; });

it('renders saved-Job summaries without fetching collapsed details, even after status polling', async () => {
  await mount(); await mount();
  expect(container.textContent).toContain('137 of 205 artifacts complete or cached');
  expect(container.textContent).toContain('6,000 bytes complete or cached of 9,000 bytes declared');
  expect(gets).toEqual([]); expect(posts).toEqual([]);
  expect(container.querySelectorAll('li')).toHaveLength(0);
  expect(container.querySelector('summary')?.textContent).toContain('Prepare worker');
});

it('fetches exactly 100 per page on expansion and replaces rather than accumulates rows', async () => {
  await mount(); await disclose('Artifact progress');
  expect(artifactGets()).toEqual([{ url: '/api/execution-targets/vast%3A123/preload/op%2F1/artifacts', params: { collection: 'progress', offset: 0, limit: 100 } }]);
  expect(container.querySelectorAll('li')).toHaveLength(100);
  await click('Next artifacts');
  expect(artifactGets().at(-1)?.params).toEqual({ collection: 'progress', offset: 100, limit: 100 });
  expect(container.querySelectorAll('li')).toHaveLength(100);
  expect(container.textContent).not.toContain('op/1/file-0');
  await click('Next artifacts');
  expect(container.querySelectorAll('li')).toHaveLength(5);
  expect(container.textContent).toContain('201–205 of 205');
  await click('Previous artifacts'); expect(container.querySelectorAll('li')).toHaveLength(100);
  await disclose('Artifact progress');
  const count = artifactGets().length; await client.invalidateQueries(); await mount();
  expect(artifactGets()).toHaveLength(count); expect(container.querySelectorAll('li')).toHaveLength(0);
});

it('shows page errors with explicit retry, no stale rows, and navigation back', async () => {
  await mount(); await disclose('Artifact progress'); failOffset = 100; await click('Next artifacts');
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('Artifact page unavailable');
  expect(container.querySelectorAll('li')).toHaveLength(0);
  failOffset = undefined; await click('Retry artifact details');
  expect(container.querySelectorAll('li')).toHaveLength(100);
  expect(artifactGets().at(-1)?.params.offset).toBe(100);
  failOffset = 200; await click('Next artifacts'); await click('Previous artifacts');
  expect(container.querySelectorAll('li')).toHaveLength(100);
});

it.each(['operation', 'target'] as const)('discards late detail and resets disclosure/page after switching %s', async change => {
  await mount(); let finish!: (value: ReturnType<typeof response>) => void;
  delayed = () => new Promise(resolve => { finish = resolve; });
  await disclose('Artifact progress');
  if (change === 'operation') target = { ...target, preload: { ...target.preload!, operation_id: 'new-op' } };
  else target = { ...target, id: 'vast:456' };
  await mount(); delayed = undefined;
  await act(async () => { finish(response(page(0, 'op/1'))); await settle(); });
  expect(container.querySelectorAll('li')).toHaveLength(0);
  expect(artifactGets()).toHaveLength(1);
  await disclose('Artifact progress');
  expect(artifactGets().at(-1)?.params.offset).toBe(0);
  expect(artifactGets().at(-1)?.url).toContain(change === 'operation' ? '/new-op/' : '/vast%3A456/');
});

it('paginates cached receipts separately and reports a truthful empty page', async () => {
  await mount(); empty = true; await disclose('Cached preload artifacts');
  expect(artifactGets().at(-1)?.params).toEqual({ collection: 'cached', offset: 0, limit: 100 });
  expect(container.textContent).toContain('No artifacts reported.');
  expect(container.querySelectorAll('li')).toHaveLength(0);
  expect(posts).toEqual([]);
});

it('uses workflow summaries and preserves operation-bound cancellation and exact scientific retry requests', async () => {
  target.preload = { ...target.preload!, selection: { kind: 'workflow', workflow_request: request } };
  await mount('workflow');
  expect(container.textContent).toContain('137 of 205 artifacts complete or cached');
  expect(artifactGets()).toEqual([]);
  await click('Cancel provision');
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision/op%2F1/cancel', body: undefined }]);
  target.preload = { ...target.preload!, phase: 'cancelled' }; await mount('workflow');
  const operation = container.querySelector('[aria-label="Provision operation"]')!;
  await act(async () => { [...operation.querySelectorAll('button')].find(item => item.textContent === 'Preview artifact downloads')!.click(); await settle(); });
  await click('Retry provision with fresh preview');
  expect(posts.slice(1)).toEqual([
    { url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'workflow', workflow_request: request } },
    { url: '/api/execution-targets/vast%3A123/provision/op%2F1/retry', body: { kind: 'workflow', workflow_request: request, preview_sha256: 'e'.repeat(64) } },
  ]);
});

it('keeps inventory details lazy under evidence and uses compact inventory count', async () => {
  target.artifact_inventory = { operation_id: 'receipt', selection: { kind: 'model', model_id: 'protenix' }, observed_at: 'now', artifact_count: 205, state: 'stale', scope: 'last_independent_provision', scientific_ready: false };
  await mount('independent'); expect(artifactGets()).toEqual([]);
  expect(container.textContent).toContain('Cache artifacts (205)');
  await disclose('Evidence'); expect(artifactGets()).toEqual([]);
  await disclose('Cache artifacts');
  expect(artifactGets()).toEqual([{ url: '/api/execution-targets/vast%3A123/artifact-inventory/artifacts', params: { offset: 0, limit: 100 } }]);
  expect(container.querySelectorAll('li')).toHaveLength(100);
  await disclose('Evidence');
  const fetched = artifactGets().length;
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(artifactGets()).toHaveLength(fetched);
  expect(container.querySelectorAll('li')).toHaveLength(0);
  await disclose('Evidence');
  expect(artifactGets().at(-1)?.params.offset).toBe(0);
});

it('renders historical 89,115-record counts directly from compact status, without detail arrays', async () => {
  target.preload = { ...target.preload!, phase: 'source_download_ready', selection: { kind: 'model', model_id: 'protenix' },
    artifact_summary: { total_count: 89115, verified_count: 89115, total_bytes: 123456789, verified_bytes: 123456789 }, cached_artifact_count: 89115 };
  target.artifact_inventory = { operation_id: 'receipt', selection: { kind: 'model', model_id: 'protenix' }, observed_at: 'now', artifact_count: 89115, state: 'download_verified', scope: 'last_independent_provision', scientific_ready: false };
  await mount('independent');
  expect(container.textContent).toContain('89115 of 89115 artifacts complete or cached');
  expect(container.textContent).toContain('123,456,789 bytes complete or cached of 123,456,789 bytes declared');
  expect(container.textContent).toContain('Cached preload artifacts (89,115)');
  expect(container.textContent).toContain('Cache artifacts (89,115)');
  expect(container.textContent).toContain('Downloads complete');
  expect(artifactGets()).toEqual([]);
  expect(container.querySelectorAll('li')).toHaveLength(0);
});

it('binds compact runtime identity faithfully without serializing full legacy manifests', async () => {
  target.preload = null;
  target.capabilities = { critical_runtime_binding: { release_sha256: 'release', paths: { runner: '/first' }, environment: { BMS_CONTAINER_BACKEND: 'udocker' } }, critical_runtime: { release_sha256: 'release', state: 'verified', artifacts: [] } };
  await mount('workflow'); await click('Preview artifact downloads');
  const start = () => [...container.querySelectorAll('button')].find(item => item.textContent === 'Start provision')!;
  expect(start().disabled).toBe(false);
  target = { ...target, capabilities: { ...target.capabilities, critical_runtime: { release_sha256: 'release', state: 'verified', artifacts: [{ name: 'unused full manifest record' }] } } };
  await mount('workflow'); expect(start().disabled).toBe(false);
  target = { ...target, capabilities: { ...target.capabilities, critical_runtime_binding: { release_sha256: 'release', paths: { runner: '/replacement' }, environment: { BMS_CONTAINER_BACKEND: 'udocker' } } } };
  await mount('workflow'); expect(start().disabled).toBe(true);
  expect(posts).toHaveLength(1);
});

it('preserves the saved-Job retry payload and disabled predicates', async () => {
  target.preload = { ...target.preload!, phase: 'failed' }; await mount(); await disclose('Prepare worker');
  await act(async () => { const select = container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')!; select.value = 'recipe'; select.dispatchEvent(new Event('change', { bubbles: true })); await settle(); });
  await click('Retry preload');
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/preload', body: { job_id: 'recipe' } }]);
});
