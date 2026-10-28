import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import blockedPreviewWire from '../fixtures/blocked-preparation-preview.json?raw';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RemotePreloadPanel } from '../../src/components/dashboard/RemotePreloadPanel';
import { WorkflowProvisionPanel } from '../../src/components/dashboard/IndependentProvisionPanel';
import { api, type ExecutionTarget, type ProvisionPreview, type ProvisionSelection } from '../../src/lib/api';

import { MemoryRouter } from 'react-router-dom';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
// Compile the real lazy approval UI before test timing/act scopes begin.
// submitJob still opens and awaits the unmocked approval dialog below.
import '../../src/components/ExecutionPlanApproval';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { ExecutionPolicyControl } from '../../src/components/ExecutionPolicyControl';
import { EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { setDraftExecutionPolicy } from '../../src/lib/executionPolicy';
// These alternative workflows are not mounted in the de-novo request-owner tests.
vi.mock('../../src/components/ProteinLocalRedesignTemplate', () => ({ ProteinLocalRedesignTemplate: () => null }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: () => null }));

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const catalog: ProvisionSelection[] = [{ kind: 'model', model_id: 'protenix' }, { kind: 'image', model_id: 'protenix' }, { kind: 'image', model_id: 'foldcp' }, { kind: 'model', model_id: 'esmfold2' }, { kind: 'workflow_pack', workflow_id: 'structure_prediction' }, { kind: 'workflow_pack', workflow_id: 'antibody_denovo' }, { kind: 'workflow_pack', workflow_id: 'de_novo_design' }];
const compactEmpty = { artifact_summary: { total_count: 0, verified_count: 0, total_bytes: 0, verified_bytes: 0 }, cached_artifact_count: 0 };
const artifacts = [{ name: 'runtime/protenix.sif', sha256: 'a'.repeat(64), size_bytes: 1234 }];
const preview = (selection: ProvisionSelection): ProvisionPreview => ({ selection, artifacts, total_bytes: 1234, preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'download_only' });
const ready: ExecutionTarget = { id: 'vast:123', provider: 'vast', provider_instance_id: '123', name: 'Worker', state: 'ready', active: true, host: 'host', port: 22, username: 'root', remote_root: '/opt/bms', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null };
const adapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let target: ExecutionTarget;
let posts: Array<{ url: string; body: Record<string, string> }>;
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const changed = vi.fn(async () => {});
const render = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[]} onChanged={changed} /></QueryClientProvider>); }); await act(async () => { await settle(); }); await act(async () => { const details = [...container.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent?.includes('Prepare worker')); if (details && !details.open) { details.open = true; details.dispatchEvent(new Event('toggle')); } await settle(); }); };
const button = (text: string) => [...container.querySelectorAll('button')].find(b => b.textContent === text)!;
const click = async (text: string, twice = false) => { await act(async () => { const b = button(text); b.click(); if (twice) b.click(); await settle(); }); };
const disclose = async (label: string) => { await act(async () => {
  const details = [...container.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent?.startsWith(label))!;
  details.open = !details.open; details.dispatchEvent(new Event('toggle')); await settle();
}); };
const select = async (label: string, value: string) => { if (label === 'Provision model' && !container.querySelector('[aria-label="Provision model"]')) await select('Provision scope', 'model'); await act(async () => { const s = container.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!; s.value = value; s.dispatchEvent(new Event('change', { bubbles: true })); await settle(); }); };
beforeEach(() => {
  target = { ...ready }; posts = []; changed.mockClear();
  window.history.replaceState({}, '', '/submit');
  window.sessionStorage.clear(); window.localStorage.clear(); setDraftExecutionPolicy(undefined);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  api.defaults.adapter = async config => {
    if (config.method === 'get' && config.url?.endsWith('/artifacts')) return response({ items: [{ ...artifacts[0], state: 'interrupted' }], total_count: 1, offset: 0, limit: 100, operation_id: target.preload?.operation_id, sequence: target.preload?.sequence });
    if (config.method === 'get') return response(config.url === '/api/models' ? [{ id: 'boltz2', name: 'Boltz-2' }, { id: 'protenix', name: 'Protenix' }, { id: 'esmfold2', name: 'ESMFold2' }, { id: 'foldcp', name: 'FoldCP' }, { id: 'unsupported', name: 'Other registered model' }] : config.url === '/api/templates' ? [{ id: 'molecular_dynamics', name: 'Molecular Dynamics' }] : config.url?.endsWith('/catalog') ? catalog : config.url?.endsWith('/runtime-inventory') ? null : [target]);
    const body = config.data ? JSON.parse(String(config.data)) : undefined;
    posts.push({ url: String(config.url), body });
    if (config.url === '/api/jobs/execution-plan/preview') return response({ schema: 'bms.job.execution-preview.v1', approval_digest: 'd'.repeat(64), admissible: true,
      request: body, plan: { requested_json: body.params, effective_json: body.params, source_identity: { revision: 'a'.repeat(40), tree: 'b'.repeat(40) },
        metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] });
    return response(config.url?.endsWith('/preview') ? preview(body) : target);
  };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = adapter; vi.restoreAllMocks(); });

const nativeCatalog = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python', ['-c',
  'import asyncio,json; from routers.execution_targets import provision_catalog; print(json.dumps([x.model_dump(mode="json") for x in asyncio.run(provision_catalog())]))'],
  { cwd: resolve('../api'), encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:../..' } }));
const component = (id: string) => {
  const label = nativeCatalog.find((item: any) => item.workflow_id === 'structure_prediction').components.find((item: any) => item.model_id === id).label;
  return [...container.querySelectorAll('label')].find(item => item.textContent === label)!.querySelector<HTMLInputElement>('input')!;
};
const toggleComponent = async (id: string) => { await act(async () => { component(id).click(); await settle(); }); };
const mountNativeCatalog = async () => {
  client.setQueryData(['remote-provision-catalog'], nativeCatalog);
  await render(); await select('Preparation workflow', 'structure_prediction');
};

it('sends exact native catalog checkbox defaults, edits and full-pack through Axios preview and provision', async () => {
  await mountNativeCatalog();
  expect(component('protenix').checked).toBe(true);
  expect(component('frustrampnn').checked).toBe(true);
  expect(component('esmfold2').checked).toBe(false);
  expect(component('boltz2').checked).toBe(false);
  expect(component('boltz_cp_experimental').checked).toBe(false);
  expect(posts).toEqual([]);
  await click('Preview artifact downloads');
  const defaults = { kind: 'workflow_pack', workflow_id: 'structure_prediction', component_ids: ['frustrampnn', 'protenix'] };
  expect(posts[0].body).toEqual(defaults);
  expect(button('Start provision').disabled).toBe(false);
  await toggleComponent('esmfold2');
  expect(button('Start provision').disabled).toBe(true);
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  await click('Preview artifact downloads');
  expect(posts.at(-1)?.body).toEqual({ ...defaults, component_ids: ['esmfold2', 'frustrampnn', 'protenix'] });
  await toggleComponent('esmfold2');
  await click('Prepare selected components', true);
  expect(posts.slice(-2).map(item => item.body)).toEqual([defaults, { ...defaults, preview_sha256: 'b'.repeat(64) }]);
  // The explicit all checkbox retains the historical request, without discovery metadata.
  await act(async () => { [...container.querySelectorAll('label')].find(item => item.textContent === 'All components (full workflow)')!.querySelector<HTMLInputElement>('input')!.click(); await settle(); });
  await click('Prepare entire workflow');
  expect(posts.slice(-2).map(item => item.body)).toEqual([
    { kind: 'workflow_pack', workflow_id: 'structure_prediction' },
    { kind: 'workflow_pack', workflow_id: 'structure_prediction', preview_sha256: 'b'.repeat(64) },
  ]);
  await toggleComponent('esmfold2');
  await click('Preview artifact downloads');
  expect(posts.at(-1)?.body).toEqual({ ...defaults, component_ids: ['boltz2', 'boltz_cp_experimental', 'frustrampnn', 'protenix'] });
  if (process.env.BMS_PROVISION_COMPONENT_WIRE) writeFileSync(process.env.BMS_PROVISION_COMPONENT_WIRE, JSON.stringify(posts.map(item => item.body)));
});

it('keeps an explicitly cleared component choice empty without preview or transmission', async () => {
  await mountNativeCatalog();
  await toggleComponent('protenix'); await toggleComponent('frustrampnn');
  expect(container.textContent).toContain('Select at least one component to prepare.');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  expect(button('Start provision').disabled).toBe(true);
  expect(posts).toEqual([]);
});

it.each(['late edit', 'wrong subset'])('does not start a %s component preview', async mode => {
  await mountNativeCatalog();
  let finish!: (value: ReturnType<typeof response>) => void;
  const post = vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await click('Prepare selected components');
  if (mode === 'late edit') await toggleComponent('esmfold2');
  await act(async () => { finish(response(preview({ kind: 'workflow_pack', workflow_id: 'structure_prediction',
    component_ids: mode === 'late edit' ? ['frustrampnn', 'protenix'] : ['esmfold2'] }))); await settle(); });
  expect(post).toHaveBeenCalledTimes(1);
  expect(posts).toEqual([]);
  expect(button('Start provision').disabled).toBe(true);
});

it('preserves the whole-workflow choice across claim arrival and release without posting', async () => {
  target = { ...target, active_job_ids: ['one', 'two'], progress: null, job_progress: [] };
  await render(); await select('Preparation workflow', 'structure_prediction');
  for (const ids of [['two'], [], ['three']]) {
    target = { ...target, active_job_ids: ids };
    await render();
    expect(container.querySelector<HTMLSelectElement>('[aria-label="Preparation workflow"]')?.value).toBe('structure_prediction');
    expect(button('Prepare entire workflow').disabled).toBe(false);
  }
  expect(posts).toEqual([]);
  await click('Prepare entire workflow');
  expect(posts.map(post => post.url)).toEqual([
    '/api/execution-targets/vast%3A123/provision/preview', '/api/execution-targets/vast%3A123/provision',
  ]);
});

it('completes the explicitly requested one-click preparation when a Job claim arrives before progress', async () => {
  target = { ...target, active_job_ids: [] };
  await render(); await select('Preparation workflow', 'structure_prediction');
  let finish!: (value: ReturnType<typeof response>) => void;
  vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await click('Prepare entire workflow');
  target = { ...target, active_job_ids: ['claimed'], progress: null };
  await render();
  await act(async () => { finish(response(preview({ kind: 'workflow_pack', workflow_id: 'structure_prediction' }))); await settle(); });
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision', body: { kind: 'workflow_pack', workflow_id: 'structure_prediction', preview_sha256: 'b'.repeat(64) } }]);
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Preparation workflow"]')?.value).toBe('structure_prediction');
  expect(button('Prepare entire workflow').disabled).toBe(true);
});

it.each(['model', 'image', 'workflow', 'retry'] as const)('preserves %s previews through running/staging Jobs and starts only on click', async scope => {
  const request = { name: 'Draft', model_id: 'protenix', mode: 'predict', params: { seeds: [7], sequence: 'ACDE' } };
  const selection: ProvisionSelection = scope === 'workflow' ? { kind: 'workflow', workflow_request: request }
    : { kind: scope === 'image' ? 'image' : 'model', model_id: 'protenix' };
  if (scope === 'retry') target.preload = { ...compactEmpty, operation_id: 'stopped', selection,
    source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64),
    phase: 'cancelled', artifact: null, message: 'Stopped', started_at: 'now', updated_at: 'now' };
  const mount = scope === 'workflow' ? async () => {
    await act(async () => { root.render(<QueryClientProvider client={client}><WorkflowProvisionPanel target={target} workflowRequest={request} onChanged={changed} /></QueryClientProvider>); await settle(); });
  } : render;
  // Legacy worker responses have no active_job_ids. Their progress operation also must not bind preparation.
  target = { ...target, progress: { operation_id: 'run-one', job_id: 'one', phase: 'running', artifact: null, message: 'CUDA running', updated_at: 'now' } };
  await mount();
  if (scope === 'model' || scope === 'image') { await select('Provision scope', scope); await select('Provision model', 'protenix'); }
  await click('Preview artifact downloads');
  const start = scope === 'retry' ? 'Retry provision with fresh preview' : 'Start provision';
  expect(button(start).disabled).toBe(false);
  for (const phase of ['staging', 'running'] as const) {
    target = { ...target, progress: { ...target.progress!, operation_id: `run-${phase}`, phase, message: phase } };
    await mount();
    expect(button(start).disabled).toBe(false);
    expect(container.querySelector('[aria-label="Provision preview"]')).not.toBeNull();
  }
  target = { ...target, active_job_ids: ['two', 'three'], job_progress: [target.progress!] };
  await mount();
  target = { ...target, active_job_ids: [] };
  await mount();
  expect(posts).toHaveLength(1);
  expect(button(start).disabled).toBe(false);
  await click(start, true);
  expect(posts).toEqual([
    { url: '/api/execution-targets/vast%3A123/provision/preview', body: selection },
    { url: `/api/execution-targets/vast%3A123/provision${scope === 'retry' ? '/stopped/retry' : ''}`, body: { ...selection, preview_sha256: 'b'.repeat(64) } },
  ]);
});

it.each(['running', 'staging'] as const)('preloads a saved recipe while %s and retains shared mutation exclusion', async phase => {
  target = { ...target, active_job_ids: ['science'], job_progress: [{ operation_id: 'run', job_id: 'science', phase, artifact: null, message: `Science ${phase}`, updated_at: 'now' }] };
  const mount = async () => { await act(async () => { root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[{ id: 'saved', model_id: 'protenix' }]} onChanged={changed} /></QueryClientProvider>); await settle(); }); };
  await mount(); await disclose('Prepare worker');
  await select('Saved Job recipe', 'saved'); await select('Provision model', 'protenix');
  await click('Preview artifact downloads');
  expect(container.querySelector('[aria-label="Worker activity"]')?.textContent).toContain(`Science ${phase}`);
  let finish!: (value: ReturnType<typeof response>) => void;
  const post = vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await click('Preload selected worker', true);
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith('/api/execution-targets/vast%3A123/preload', { job_id: 'saved' });
  expect(button('Preview artifact downloads').disabled).toBe(true);
  expect(button('Start provision').disabled).toBe(true);
  expect(button('Preloading…').disabled).toBe(true);
  await act(async () => { finish(response(target)); await settle(); });
  expect(changed).toHaveBeenCalledTimes(1);
  expect(button('Start provision').disabled).toBe(false);
});

it('prepares the entire workflow from one explicit click using its fresh digest, without a Job', async () => {
  await render();
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  await select('Preparation workflow', 'structure_prediction');
  expect(posts).toEqual([]);
  expect(button('Prepare entire workflow')).toBeTruthy();
  expect(container.querySelector('a[href*="template=structure_prediction"]')).toBeNull();
  await click('Prepare entire workflow', true);
  expect(posts).toEqual([
    { url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'workflow_pack', workflow_id: 'structure_prediction' } },
    { url: '/api/execution-targets/vast%3A123/provision', body: { kind: 'workflow_pack', workflow_id: 'structure_prediction', preview_sha256: 'b'.repeat(64) } },
  ]);
  expect(button('Prepare entire workflow').disabled).toBe(true);
  expect(invalidate).not.toHaveBeenCalled();
  expect(posts.some(post => post.url.endsWith('/runtime-inventory/refresh'))).toBe(false);
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(posts).toHaveLength(2);
});

it('prepares the De Novo whole pack without using the selected request or saved Job', async () => {
  await render();
  await select('Preparation workflow', 'protein_modification_experimental');
  expect(posts).toEqual([]);
  expect(container.textContent).toContain('Optional native oracles, corpora and custom checkpoints');
  await click('Prepare entire workflow', true);
  expect(posts).toEqual([
    { url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'workflow_pack', workflow_id: 'de_novo_design' } },
    { url: '/api/execution-targets/vast%3A123/provision', body: { kind: 'workflow_pack', workflow_id: 'de_novo_design', preview_sha256: 'b'.repeat(64) } },
  ]);
});

it('prepares all de novo binder dependencies in one click, without substituting a configured generator', async () => {
  await render();
  await select('Preparation workflow', 'antibody_denovo');
  expect(container.textContent).toContain('binder generators, sequence designers, validators');
  expect(container.querySelector('a[href*="template=antibody_denovo"]')).toBeNull();
  await click('Prepare entire workflow', true);
  expect(posts).toEqual([
    { url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'workflow_pack', workflow_id: 'antibody_denovo' } },
    { url: '/api/execution-targets/vast%3A123/provision', body: { kind: 'workflow_pack', workflow_id: 'antibody_denovo', preview_sha256: 'b'.repeat(64) } },
  ]);
});

it('keeps whole-workflow missing-asset details visible without starting an incomplete pack', async () => {
  await render(); await select('Preparation workflow', 'structure_prediction');
  const pack = { kind: 'workflow_pack', workflow_id: 'structure_prediction' } as const;
  vi.spyOn(api, 'post').mockResolvedValueOnce(response({ ...preview(pack), blockers: ['host_asset_unavailable: weights/boltz'], estimates_complete: false }));
  await click('Prepare entire workflow');
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(container.querySelector('[role="alert"]')).toBeNull();
  expect(container.textContent).toContain('host_asset_unavailable: weights/boltz');
  expect(container.querySelector('[aria-label="Provision preview"] dl')?.textContent).toContain('To downloadunknown');
  expect(posts).toEqual([]);
});

it.each(['selection', 'worker', 'attachment', 'readiness', 'runtime'])('does not auto-start a late pack preview after changing the %s', async change => {
  await render(); await select('Preparation workflow', 'structure_prediction');
  let finish!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>(resolve => { finish = resolve; });
  const post = vi.spyOn(api, 'post').mockReturnValueOnce(pending);
  await click('Prepare entire workflow');
  if (change === 'selection') await select('Preparation workflow', 'antibody_denovo');
  else {
    if (change === 'worker') target = { ...target, id: 'vast:456', provider_instance_id: '456' };
    if (change === 'attachment') target = { ...target, active: false };
    if (change === 'readiness') target = { ...target, state: 'unavailable' };
    if (change === 'runtime') target = { ...target, capabilities: { critical_runtime_binding: { release_sha256: 'replacement' } } };
    await render();
  }
  await act(async () => { finish(response(preview({ kind: 'workflow_pack', workflow_id: 'structure_prediction' }))); await settle(); });
  expect(post).toHaveBeenCalledTimes(1);
  expect(posts).toEqual([]);
});

it('retries whole-workflow preparation explicitly with a fresh preview after an error', async () => {
  await render(); await select('Preparation workflow', 'structure_prediction');
  vi.spyOn(api, 'post').mockRejectedValueOnce(new Error('Preview failed'));
  await click('Prepare entire workflow');
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('Preview failed');
  expect(posts).toEqual([]);
  await click('Prepare entire workflow');
  expect(posts.map(post => post.url)).toEqual([
    '/api/execution-targets/vast%3A123/provision/preview', '/api/execution-targets/vast%3A123/provision',
  ]);
});

it('keeps optional preparation closed on load and polling, retaining a form choice when reopened', async () => {
  const get = vi.spyOn(api, 'get');
  const mount = async () => { await act(async () => {
    root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[]} onChanged={changed} /></QueryClientProvider>);
    await settle();
  }); };
  await mount();
  const details = container.querySelector<HTMLDetailsElement>('[aria-label="Remote preload and activity"] > details')!;
  expect(details.open).toBe(false);
  expect(details.querySelector('summary')?.textContent).toContain('optional dependency downloads');
  await mount();
  expect(details.open).toBe(false);
  expect(container.querySelector('[aria-label="Independent worker provisioning"]')).toBeNull();
  expect(get).not.toHaveBeenCalled();
  await act(async () => { details.querySelector('summary')!.click(); await settle(); });
  await select('Provision model', 'protenix');
  expect(get.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining(['/api/models', '/api/templates', '/api/execution-targets/provision/catalog']));
  await act(async () => { details.querySelector('summary')!.click(); await settle(); });
  await act(async () => { await client.invalidateQueries(); await settle(); });
  expect(details.open).toBe(false);
  await act(async () => { details.querySelector('summary')!.click(); await settle(); });
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision model"]')?.value).toBe('protenix');
  expect(posts).toEqual([]);
});

it('shows persisted worker activity outside closed preparation controls without posting', async () => {
  target = { ...ready, preload: { ...compactEmpty, operation_id: 'op', job_id: 'recipe', source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'transferring', artifact: 'runtime/image', message: 'Downloading runtime',
    started_at: '2026-09-20T23:54:14Z', updated_at: '2026-09-20T23:56:14Z' },
    progress: { operation_id: 'run', job_id: 'job', phase: 'running', artifact: 'inputs/sample', message: 'Staging inputs', updated_at: '2026-09-20T23:56:14Z' } };
  await act(async () => { root.render(<QueryClientProvider client={client}><RemotePreloadPanel target={target} jobs={[]} onChanged={changed} /></QueryClientProvider>); await settle(); });
  const details = container.querySelector<HTMLDetailsElement>('[aria-label="Remote preload and activity"] > details')!;
  expect(details.open).toBe(false);
  expect(details.querySelector('[aria-label="Worker activity"]')).toBeNull();
  expect(details.querySelector('[aria-label="Preload progress"]')).toBeNull();
  expect(container.querySelector('[aria-label="Worker activity"]')?.textContent).toContain('Staging inputs');
  expect(container.querySelector('[aria-label="Preload progress"]')?.textContent).toContain('Downloading runtime');
  expect(posts).toEqual([]);
});

it('keeps attached runtime-unready workers disabled for independent provisioning', async () => {
  target = { ...ready, state: 'unavailable', active: true, last_error: 'Mount namespaces unavailable; use a compatible VM' };
  await render(); await select('Provision model', 'protenix');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  expect(button('Start provision').disabled).toBe(true);
  await click('Preview artifact downloads'); await click('Start provision');
  expect(posts).toEqual([]);
});

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
  expect(container.textContent).toContain('To vast:123 · /opt/bms');
  expect(container.querySelector('[aria-label="Provision preview"] dl')?.textContent).toContain('To download1.2 KiB');
  expect(container.textContent).toContain('Critical runtime unavailable');
  expect(button('Start provision').disabled).toBe(true);
});

it('renders the actual blocked route wire with unknown bytes and no start', async () => {
  // Full response equality is enforced by test_remote_preparation_preview.py.
  const wire: ProvisionPreview = JSON.parse(blockedPreviewWire);
  catalog.push(wire.selection);
  try {
    await render(); await select('Provision model', 'boltz2');
    vi.spyOn(api, 'post').mockResolvedValueOnce(response(wire));
    await click('Preview artifact downloads');
    expect(container.textContent).toContain('host_asset_unavailable');
    expect(container.textContent).toContain('containers/boltz2.sif');
    expect(container.textContent).toContain('weights/boltz');
    const estimates = container.querySelector('[aria-label="Provision preview"] dl')?.textContent;
    expect(estimates).toContain('To downloadunknown');
    expect(estimates).toContain('Disk neededunknown');
    expect(estimates).toContain('Total setunknown');
    expect(container.textContent).toContain('size and SHA256 unknown');
    expect(button('Start provision').disabled).toBe(true);
    await click('Start provision');
    expect(posts).toEqual([]);
  } finally { catalog.pop(); }
});

it('uses operation-bound cancellation and keeps uncertain recovery blocked', async () => {
  target.preload = { ...compactEmpty, operation_id: 'op/1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'recovery_blocked', artifact: artifacts[0].name, message: 'Quiescence unknown',
    started_at: 'now', updated_at: 'now', recovery_required: true, cancel_requested: true, sequence: 4,
    artifact_summary: { total_count: 1, verified_count: 0, total_bytes: 1234, verified_bytes: 0 } };
  await render();
  expect(container.textContent).toContain('Sequence 4');
  expect(container.textContent).not.toContain('interrupted');
  await disclose('Artifact progress');
  expect(posts).toEqual([]);
  expect(container.textContent).toContain('interrupted');
  expect(button('Retry provision with fresh preview').disabled).toBe(true);
  await click('Recheck cancellation quiescence', true);
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision/op%2F1/cancel', body: undefined }]);
  expect(container.textContent).toContain('Ownership is not released');
});

it('retries a stopped operation only with a fresh preview of its persisted selection', async () => {
  target.preload = { ...compactEmpty, operation_id: 'op1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'cancelled', artifact: null, message: 'Stopped', started_at: 'now', updated_at: 'now' };
  await render();
  const operation = container.querySelector('[aria-label="Provision operation"]')!;
  await act(async () => { [...operation.querySelectorAll('button')].find(b => b.textContent === 'Preview artifact downloads')!.click(); await settle(); });
  await click('Retry provision with fresh preview');
  expect(posts.at(-1)).toEqual({ url: '/api/execution-targets/vast%3A123/provision/op1/retry', body: { ...catalog[0], preview_sha256: 'b'.repeat(64) } });
});

it('mounts in the real worker panel without Jobs; previews exact bytes then starts once with the digest', async () => {
  await render();
  expect(container.querySelector('[title*="It does not launch a job, run inference or establish scientific readiness"]')).not.toBeNull();
  expect(container.textContent).toContain('Installed artifacts are unknown');
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')?.value).toBe('workflow');
  expect(button('Start provision')).toBeUndefined();
  await select('Provision model', 'protenix');
  expect(button('Start provision').disabled).toBe(true);
  expect(posts).toEqual([]);
  await click('Preview artifact downloads', true);
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision/preview', body: { kind: 'model', model_id: 'protenix' } }]);
  expect(container.textContent).not.toContain(artifacts[0].name);
  await disclose('Cache artifacts');
  expect(posts).toHaveLength(1);
  expect(container.querySelector('[aria-label="Provision preview"] dl')?.textContent).toContain('Total set1.2 KiB');
  expect(container.textContent).toContain(artifacts[0].name);
  expect(container.textContent).toContain(artifacts[0].sha256);
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).not.toContain('additional installed copy');
  expect(container.querySelector('[aria-label="Provision preview"]')?.textContent).not.toContain('retains prior release generations');
  expect(container.querySelector('[aria-label="Provision preview"] dl')?.getAttribute('title')).toContain('Reuses cached assets and downloads missing files');
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
  expect(button('Start provision')).toBeUndefined();
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  target = { ...target, preload: { ...compactEmpty, operation_id: 'another', selection: catalog[0], source_revision: 'd'.repeat(40), source_tree: 'e'.repeat(40), request_sha256: 'f'.repeat(64), phase: 'source_download_ready', artifact: null, message: 'Previous cache ready', started_at: '2026-01-01', updated_at: '2026-01-01' } }; await render();
  expect(button('Start provision')).toBeUndefined();
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(false);
  target = { ...target, preload: { ...target.preload!, source_tree: 'f'.repeat(40) } }; await render();
  expect(button('Start provision')).toBeUndefined();
  await select('Provision model', 'protenix'); await click('Preview artifact downloads');
  target = { ...target, id: 'vast:456' }; await render();
  expect(button('Start provision')).toBeUndefined();
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

it.each(['download_only', 'managed_asset_activation'] as const)('accepts %s preview wire compatibility without promising activation', async scope => {
  await render(); await select('Provision model', 'protenix');
  vi.spyOn(api, 'post').mockResolvedValueOnce(response(JSON.parse(JSON.stringify({ ...preview(catalog[0]), scope }))));
  await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(false);
  expect(container.querySelector('[aria-label="Provision preview"] dl')?.getAttribute('title')).toContain('not a free-space check');
  await click('Start provision');
  expect(posts).toEqual([{ url: '/api/execution-targets/vast%3A123/provision', body: { ...catalog[0], preview_sha256: 'b'.repeat(64) } }]);
});

it('rejects unknown preview scopes before enabling provision', async () => {
  await render(); await select('Provision model', 'protenix');
  vi.spyOn(api, 'post').mockResolvedValueOnce(response({ ...preview(catalog[0]), scope: 'cache_download_only' }));
  await click('Preview artifact downloads');
  expect(button('Start provision').disabled).toBe(true);
  expect(container.querySelector('[aria-label="Provision preview"]')).toBeNull();
});

it('restores only the persisted last receipt and displays stale/failed status without auto-provision', async () => {
  target = { ...target, artifact_inventory: { operation_id: 'receipt', selection: catalog[0], artifact_count: artifacts.length, observed_at: '2026-01-01T00:00:00Z', state: 'download_verified', scope: 'last_independent_provision', scientific_ready: false } };
  await render();
  expect(container.textContent).toContain('Cache downloads verified at observation');
  expect(container.querySelector('[title*="Cache downloads only, and separate from the installed inventory below"]')?.getAttribute('title')).toContain('Not scientific readiness or runtime activation');
  await act(async () => root.render(null)); await render();
  expect(container.textContent).toContain('2026-01-01T00:00:00Z');
  target = { ...target, artifact_inventory: { ...target.artifact_inventory!, state: 'stale' } }; await render();
  expect(container.textContent).toContain('Historical cache observation');
  expect(posts).toEqual([]);
});

it('surfaces catalog failure with explicit retry and no provision side effects', async () => {
  const originalGet = api.get.bind(api);
  const get = vi.spyOn(api, 'get').mockImplementation((url, config) => String(url).endsWith('/catalog')
    ? Promise.reject(new Error('Catalog unavailable')) : originalGet(url, config));
  await render();
  expect(container.textContent).toContain('Catalog unavailable');
  expect(button('Start provision')).toBeUndefined();
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
  expect(button('Start provision')).toBeUndefined();
});

it.each(['busy', 'inactive'])('blocks provisioning when worker is %s', async state => {
  if (state === 'inactive') target = { ...target, active: false };
  if (state === 'busy') target = { ...target, preload: { ...compactEmpty, operation_id: 'op', job_id: 'job', source_revision: 'a'.repeat(40), source_tree: 'a'.repeat(40), request_sha256: 'a'.repeat(64), phase: 'transferring', artifact: null, message: 'Transferring', started_at: 'now', updated_at: 'now' } };
  await render(); await select('Provision model', 'protenix');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  expect(button('Start provision').disabled).toBe(true);
  expect(posts).toEqual([]);
});


it.each(['cancelling', 'recovery_blocked', 'cancelled'] as const)('fences saved-Job preloading during %s with unresolved recovery and labels workflow receipts', async phase => {
  target.preload = { ...compactEmpty, operation_id: 'owned', selection: { kind: 'workflow', workflow_request: { name: 'Draft', model_id: 'boltz2', mode: 'predict', params: { sequence: 'ACDE' } } },
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
  target.preload = { ...compactEmpty, operation_id: 'old', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase: 'source_download_ready', artifact: null, message: '', started_at: 'now', updated_at: 'now' };
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
  const launch = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === 'Generate candidates')!;
  expect(launch).toBeTruthy();
  await act(async () => { launch.click(); await settle(); });
  expect(posts.find(post => post.url === '/api/jobs')?.body).toBeUndefined();
  for (let attempt = 0; attempt < 50 && ![...document.querySelectorAll('button')].some(item => item.textContent === 'Approve and submit'); attempt++) {
    await act(async () => { await settle(); });
  }
  await act(async () => { [...document.querySelectorAll('button')].find(item => item.textContent === 'Approve and submit')!.click(); await settle(); });
  expect(posts.find(post => post.url === '/api/jobs')?.body).toEqual({ ...approved.workflow_request, execution_plan_approval: 'd'.repeat(64) });
});

it.each(['start', 'retry', 'cancel'] as const)('refreshes the target once after provision %s', async action => {
  if (action !== 'start') target.preload = {
    ...compactEmpty,
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

it('shows FoldCP image-only scope without implying its weights are prepared', async () => {
  await render();
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')?.value).toBe('workflow');
  await select('Provision scope', 'model');
  // Model scope lists only models with reviewed managed assets.
  expect([...container.querySelectorAll<HTMLOptionElement>('[aria-label="Provision model"] option')].map(o => o.value)).not.toContain('foldcp');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  await select('Provision scope', 'image');
  await select('Provision model', 'foldcp');
  expect(container.textContent).toContain('Container image only');
  expect(container.textContent).toContain('Weights are not downloaded.');
  expect(button('Preview artifact downloads').disabled).toBe(false);
  expect(posts).toEqual([]);
});

it('discovers the shared launcher workflows and full model registry without granting unsupported preparation', async () => {
  await render();
  expect(container.querySelector<HTMLSelectElement>('[aria-label="Provision scope"]')?.value).toBe('workflow');
  await select('Provision scope', 'model');
  const offered = [...container.querySelectorAll<HTMLOptionElement>('[aria-label="Provision model"] option')].map(o => o.value);
  expect(offered).toEqual(['', 'esmfold2', 'protenix']);
  expect(offered).not.toContain('unsupported');
  expect(button('Preview artifact downloads').disabled).toBe(true);
  await select('Provision scope', 'workflow');
  expect(container.textContent).toContain('De Novo Binder Design');
  expect(container.textContent).toContain('De Novo Design');
  expect(container.textContent).toContain('Molecular Dynamics');
  await select('Preparation workflow', 'molecular_dynamics');
  expect(container.querySelector('a')?.getAttribute('href')).toBe('/submit?template=molecular_dynamics');
  expect(container.textContent).toContain('prepares its dependencies from its launch form');
  expect(container.textContent).toContain('Preview artifact downloads');
  expect(container.textContent).toContain("Match a saved Job's dependencies");
  expect(container.textContent).not.toContain('No saved Job required');
  expect(posts).toEqual([]);
});

it('lazily discloses exact dependency files without hiding blockers or posting', async () => {
  await render(); await select('Provision model', 'protenix');
  vi.spyOn(api, 'post').mockResolvedValueOnce(response({ ...preview(catalog[0]),
    asset_states: [{ ...artifacts[0], state: 'unknown' }], blockers: ['Keep visible'] }));
  await click('Preview artifact downloads');
  expect(container.textContent).not.toContain(artifacts[0].name);
  expect(container.textContent).toContain('Keep visible');
  await disclose('Exact dependency states');
  expect(container.textContent).toContain(artifacts[0].name);
  await disclose('Exact dependency states');
  expect(container.textContent).not.toContain(artifacts[0].name);
  expect(posts).toEqual([]); // Preview uses the explicitly mocked post; disclosures never call it.
  expect(api.post).toHaveBeenCalledTimes(1);
});

it('leads with the running preparation, its verified bytes and the stop control', async () => {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-09-20T23:56:14Z'));
  target.preload = { ...compactEmpty, operation_id: 'live1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'transferring', artifact: 'weights/protenix.bin', message: 'Downloading artifact from Hugging Face',
    started_at: '2026-09-20T23:54:14Z', updated_at: '2026-09-20T23:56:14Z', sequence: 3,
    artifact_summary: { total_count: 2, verified_count: 1, total_bytes: 3234, verified_bytes: 1234 } };
  await render();
  expect(container.textContent).toContain('Downloading assets');
  expect(container.textContent).toContain('1 of 2 artifacts complete or cached');
  expect(container.textContent).toContain('1.2 KiB of 3.2 KiB');
  expect(container.querySelector('[title*="Transfer progress"], [title*="live transfer bytes and rate are not reported"]')).not.toBeNull();
  expect(container.textContent).toContain('2m 0s elapsed');
  clock.mockReturnValue(Date.parse('2026-09-20T23:57:14Z'));
  await render();
  expect(container.textContent).toContain('3m 0s elapsed');
  expect(container.textContent).not.toContain('MB/s average');
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
  const running = container.querySelector('[aria-label="Provision operation"]')!;
  const scope = container.querySelector('[aria-label="Provision scope"]')!;
  expect(running.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(button('Cancel provision')).toBeTruthy();
  expect(posts).toEqual([]);
  target = { ...target, preload: { ...target.preload!, phase: 'source_download_ready' } };
  await render();
  expect(container.textContent).toContain('2m 0s elapsed');
});

it('states the preparation caveat once and keeps the technical evidence collapsed', async () => {
  await render();
  // The caveat is kept once, as hover help, not repeated in body copy.
  expect(container.textContent).not.toContain('establish scientific readiness');
  expect([...container.querySelectorAll('[title*="establish scientific readiness"]')]).toHaveLength(1);
  const evidence = [...container.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent?.startsWith('What is already on this worker'))!;
  expect(evidence.open).toBe(false);
});

it('pairs a stale installed observation with the control that refreshes it', async () => {
  const originalGet = api.get.bind(api);
  vi.spyOn(api, 'get').mockImplementation((url, config) => String(url).endsWith('/runtime-inventory/summary')
    ? Promise.resolve(response({ scope: 'managed_independent_asset_releases', state: 'stale', observed_at: '2026-09-20T00:00:00Z',
        boot_id: 'boot', critical_runtime_ready: true, scientific_ready: false, blockers: [], releases: [] }))
    : originalGet(url, config));
  target.preload = { ...compactEmpty, operation_id: 'done1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'verifying', artifact: null, message: 'Artifact cache identities verified',
    started_at: '2026-09-20T23:54:14Z', updated_at: '2026-09-20T23:56:14Z', artifact_summary: { total_count: 1, verified_count: 1, total_bytes: 1234, verified_bytes: 1234 } };
  await render();
  await disclose('What is already on this worker');
  const stale = [...container.querySelectorAll('p')].find(item => item.textContent?.includes('Stale observation'))!;
  expect(stale).toBeTruthy();
  expect(stale.nextElementSibling?.textContent).toContain('Refresh installed observation');
});

it('does not refresh or invalidate installed inventory as downloads settle or complete', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  target.preload = { ...compactEmpty, operation_id: 'settle1', selection: catalog[0], source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40),
    request_sha256: 'c'.repeat(64), phase: 'transferring', artifact: artifacts[0].name, message: 'Downloading artifact from Hugging Face',
    started_at: '2026-09-20T23:54:14Z', updated_at: '2026-09-20T23:56:14Z',
    artifact_summary: { total_count: 1, verified_count: 0, total_bytes: 1234, verified_bytes: 0 } };
  await render();
  changed.mockClear();
  target = { ...target, preload: { ...target.preload!, phase: 'verifying', message: 'Artifact cache identities verified',
    artifact_summary: { total_count: 1, verified_count: 1, total_bytes: 1234, verified_bytes: 1234 } } };
  await render();
  expect(changed).not.toHaveBeenCalled();
  target = { ...target, preload: { ...target.preload!, phase: 'source_download_ready' } };
  await render(); await render();
  expect(container.textContent).toContain('Downloads complete');
  expect(invalidate).not.toHaveBeenCalled();
  expect(changed).not.toHaveBeenCalled();
  // Saved GET projections may remount; the explicit audit POST never runs.
  expect(posts.some(post => post.url.endsWith('/runtime-inventory/refresh'))).toBe(false);
  expect(posts).toEqual([]);
});
