import React from 'react';
import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import { BindCraft2NativeActions } from '../../src/components/BindCraft2JobResults';
import { submitBindCraft2Lifecycle } from '../../src/lib/bindcraft2Lifecycle';
import * as apiModule from '../../src/lib/api';
import * as approvalModule from '../../src/components/ExecutionPlanApproval';

vi.mock('../../src/components/ExecutionTargetPicker', () => ({
  ExecutionTargetPicker: ({ value, onChange }: { value: string | null; onChange: (value: string | null) => void }) =>
    <select aria-label="Action execution target" value={value ?? ''} onChange={event => onChange(event.target.value || null)}><option value="">Local</option><option value="vast:fixture">Remote fixture</option></select>,
}));
import { BindCraft2NativeResults, type BindCraft2NativePage } from '../../src/components/BindCraft2NativeResults';
import type { Job } from '../../src/lib/api';

let mounted: ReactTestRenderer | undefined;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); mounted = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('mounts verified BC2-native pages without a generic Design results link', async () => {
  const paths: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    paths.push(url);
    if (url.endsWith('/settings')) return { ok: true, json: async () => ({
      requested_settings: { save_failed_refolds: false, max_trajectories: 0 },
      effective_settings: { save_failed_refolds: false, max_trajectories: 0 },
      request_sha256: 'request-fixture', effective_sha256: 'effective-fixture', sweep_budget: null,
    }) };
    if (!url.includes('/bindcraft2-results?')) throw new Error(`Unexpected generic fetch: ${url}`);
    const query = new URL(url, 'http://example.test').searchParams;
    return { ok: true, json: async () => ({
      schema: 'bindcraft2.native-readback.v1', arm: 'native_arm', stage: query.get('stage'),
      offset: Number(query.get('offset')), limit: 25, total: 26,
      accounting: { claimed_attempts: 30, generated_rows: 26 },
      arms: [{ name: 'native_arm', accounting: {} }],
      metadata: null,
      rows: [{ design: 'native-1', native_score: 0.91 }],
    }) };
  }));
  const job = { id: 'bc2', model_id: 'bindcraft2', mode: 'native', status: 'completed',
    name: 'Native campaign', output_dir: '/results/bc2', design_count: 0 } as unknown as Job;
  await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
  </QueryClientProvider>); });
  expect(paths).toHaveLength(2);
  expect(paths).toContain('/api/models/bindcraft2/campaign/jobs/bc2/settings');
  expect(paths.find(path => path.includes('/bindcraft2-results?'))).not.toContain('arm=');
  await vi.waitFor(() => expect(text(mounted!.root)).toContain('native-1'));
  expect(text(mounted!.root)).not.toContain('Selection unavailable');
  expect(text(mounted!.root)).not.toContain('Open in Results Viewer');
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'Native records' }).props.onChange({ target: { value: 'attempt' } }));
  await vi.waitFor(() => expect(paths.at(-1)).toContain('stage=attempt'));
  expect(paths.at(-1)).toContain('arm=native_arm');
  await vi.waitFor(() => expect(mounted!.root.findAllByType('button').some(n => text(n) === 'Next')).toBe(true));
  await act(async () => mounted!.root.findAllByType('button').find(n => text(n) === 'Next')!.props.onClick());
  await vi.waitFor(() => expect(paths.at(-1)).toContain('offset=25'));
});

const base: BindCraft2NativePage = {
  schema: 'bindcraft2.native-readback.v1', arm: 'arm-A', stage: 'retained', offset: 0, limit: 25,
  total: 2, accounting: { claimed_attempts: 10, retained_sequences: 2 },
  arms: [{ name: 'arm-A', accounting: {} }, { name: 'arm-B', accounting: {} }], metadata: null,
  rows: [
    { design: 'retained-A', rank: 7, scored_design: 'draw-2', design_id: 'design-exact', values: { i_pTM: '0.9;;0.1' },
      target_readings: { i_pTM: { stateB: '0.9', stateA: null, off: '0.1' } },
      structures: [{ artifact_id: 'state-A', target_state: 'stateA', primary: true, variant: 'native', binder_chains: 'B', target_chains: 'A', download_url: '/api/files/download/bms_results/job/bindcraft2/campaign/stateA.cif' }] },
    { design: 'unassociated', rank: 2, scored_design: null, values: { i_pTM: '' } },
  ],
  artifacts: [{ path: 'arm-A/3_Ranked/!_Ranked.csv', media_type: 'text/csv', bytes: 20, download_url: '/api/files/download/bms_results/job/bindcraft2/campaign/arm-A/3_Ranked/!_Ranked.csv' }],
};

it('renders real metric columns, native order/ranks, exact state downloads and no invented selectable join', async () => {
  const navigate = vi.fn();
  await act(async () => { mounted = create(<BindCraft2NativeResults page={base} jobId="job" onPage={navigate} />); });
  const rows = mounted!.root.findAllByType('tbody')[0].findAllByType('tr');
  expect(rows.map(row => row.findAllByType('th')[0].children.join(''))).toEqual(['retained-A', 'unassociated']);
  expect(rows.map(row => row.findAllByType('td')[0].children.join(''))).toEqual(['7', '2']);
  expect(text(rows[0])).toContain('stateAUnknown / not emitted');
  expect(mounted!.root.findAllByType('a').filter(a => text(a) === 'Candidate workbench')).toHaveLength(1);
  expect(rows[1].findAllByType('a')).toHaveLength(0);
  expect(mounted!.root.findAllByType('a').map(a => a.props.href)).toContain('/designs/job?design_id=design-exact');
  expect(mounted!.root.findAllByType('a').map(a => a.props.href)).toContain(base.artifacts![0].download_url);
  expect(mounted!.root.findAllByType('pre')).toHaveLength(0);
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'Campaign arm' }).props.onChange({ target: { value: 'arm-B' } }));
  expect(navigate).toHaveBeenLastCalledWith({ arm: 'arm-B', stage: 'retained', offset: 0, limit: 25 });
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'Rows per page' }).props.onChange({ target: { value: '100' } }));
  expect(navigate).toHaveBeenLastCalledWith({ arm: 'arm-A', stage: 'retained', offset: 0, limit: 100 });
});

it('keeps zero yield and missing native settings independently readable and opens existing comparison workbench for Designs', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/settings')
    ? { ok: false }
    : { ok: true, json: async () => ({ ...base, rows: [], total: 0 }) }));
  const job = { id: 'job', model_id: 'bindcraft2', mode: 'campaign', status: 'completed', name: 'Fixture', output_dir: '/results/job', design_count: 1 } as Job;
  await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
  </QueryClientProvider>); });
  await vi.waitFor(() => expect(text(mounted!.root)).toContain('0 records; showing 0–0'));
  expect(text(mounted!.root)).toContain('Native compilation settings are not available');
  expect(mounted!.root.findAllByType('a').some(a => a.props.href === '/designs/job')).toBe(true);
  expect(mounted!.root.findAllByType('button').find(button => text(button) === 'Next')?.props.disabled).toBe(true);
});

it('reads saved requested and effective settings during execution without polling unpublished native results', async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({
    requested_settings: { save_failed_refolds: false, max_trajectories: 0, targets: [] },
    effective_settings: { save_failed_refolds: false, max_trajectories: 0, explicit: null },
    request_sha256: 'request-fixture', effective_sha256: 'effective-fixture', sweep_budget: {},
  }) }));
  vi.stubGlobal('fetch', fetcher);
  const job = { id: 'running', model_id: 'bindcraft2', mode: 'campaign', status: 'running', name: 'Fixture', output_dir: '/results/running', design_count: 0 } as Job;
  await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
  </QueryClientProvider>); });
  await vi.waitFor(() => expect(text(mounted!.root)).toContain('Compiled effective settings'));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledWith('/api/models/bindcraft2/campaign/jobs/running/settings');
  expect(text(mounted!.root)).toContain('save_failed_refoldsfalse');
  expect(text(mounted!.root)).toContain('max_trajectories0');
  expect(text(mounted!.root)).toContain('Empty list');
  expect(text(mounted!.root)).toContain('Explicit null');
  expect(mounted!.root.findAllByType('pre')).toHaveLength(0);
});

it('discovers typed native actions and submits explicit filter choices without resetting false or zero', async () => {
  const submit = vi.spyOn(apiModule, 'submitJob').mockResolvedValue({ data: { id: 'action-child' } } as never);
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ settings: { native_actions: {
    resume: { source: 'native', properties: {} },
    rank: { source: 'native', properties: { on: { type: 'array', items: { type: 'string' }, default: [] } } },
    filter: { source: 'native', properties: {
      campaign_relative_path: { type: 'string', control: 'campaign-arm', default: '.' },
      list: { type: 'boolean', default: false }, top: { type: 'integer', default: 20 },
      where: { type: 'array', default: [], items: { type: 'object', required: ['metric', 'comparison', 'value'], properties: {
        metric: { type: 'string' }, comparison: { type: 'string', enum: ['>=', '<=', '='] }, value: { type: 'number' },
      } } },
    } },
    score: { source: 'native', required: ['structure_relative_path'], properties: { structure_relative_path: { type: 'string', control: 'structure' } } },
    campaign_output: { source: 'native', properties: {} }, archive: { source: 'native', properties: {} }, unarchive: { source: 'native', properties: {} },
  } } }) })));
  await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <BindCraft2NativeActions jobId="source-job" page={base} />
  </QueryClientProvider>); });
  await act(async () => mounted!.root.findAllByType('button').find(button => text(button) === 'Native campaign actions')!.props.onClick());
  await vi.waitFor(() => expect(mounted!.root.findByProps({ 'aria-label': 'Native operation' }).findAllByType('option')).toHaveLength(8));
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'Native operation' }).props.onChange({ target: { value: 'filter' } }));
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'campaign_relative_path' }).props.onChange({ target: { value: 'arm-A' } }));
  await act(async () => mounted!.root.findByProps({ 'aria-label': 'top' }).props.onChange({ target: { value: '0' } }));
  await act(async () => mounted!.root.findAllByType('button').find(button => text(button) === 'Add where')!.props.onClick());
  for (const [name, value] of [['where 1 metric', 'i_pTM'], ['where 1 comparison', '>='], ['where 1 value', '0']]) {
    await act(async () => mounted!.root.findByProps({ 'aria-label': name }).props.onChange({ target: { value } }));
  }
  await act(async () => mounted!.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  expect(submit).toHaveBeenCalledWith({ name: 'BindCraft2 filter', model_id: 'bindcraft2', mode: 'filter', execution_target_id: null,
    params: { bc2_source_job_id: 'source-job', bc2_action_options: { campaign_relative_path: 'arm-A', list: false, top: 0,
      where: [{ metric: 'i_pTM', comparison: '>=', value: 0 }] } } }, { launchContext: false });
  expect(mounted!.root.findAllByType('a').map(link => link.props.href)).toContain('/jobs/action-child');
  expect(mounted!.root.findAllByType('textarea')).toHaveLength(0);
});

it('uses the existing prepared remote Job review without re-materializing the source request', async () => {
  const prepared = { model_id: 'bindcraft2', mode: 'resume', execution_target_id: 'vast:fixture',
    params: { bc2_source_job_id: 'source', bc2_action_options: {}, bc2_compilation: '/owned/compilation.json' } };
  const post = vi.spyOn(apiModule.api, 'post').mockRejectedValue({ isAxiosError: true,
    response: { status: 409, data: { detail: { code: 'remote_prepared_job_review_required', job_request: prepared } } } });
  const submit = vi.spyOn(apiModule, 'submitJob').mockResolvedValue({ data: { id: 'remote-child' } } as never);
  expect((await submitBindCraft2Lifecycle('source', 'resume', {}, 'vast:fixture')).id).toBe('remote-child');
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][1]).toMatchObject({ execution_target_id: 'vast:fixture', params: { bc2_source_job_id: 'source', bc2_action_options: {} } });
  expect(submit).toHaveBeenCalledExactlyOnceWith(prepared, { launchContext: false });
});

it.each([null, 'explicit-destination'])('Jobs lifecycle reaches real submission with independent destination %s', async destination => {
  const original = apiModule.api.defaults.adapter;
  const requests: Array<{ body: any; contextHeader: unknown }> = [];
  const originalLocation = window.location.pathname + window.location.search;
  window.history.replaceState(null, '', `/?launch_context_id=${destination ?? 'unrelated-ambient-context'}`);
  const actions = process.env.BMS_BC2_ACTION_INVENTORY
    ? JSON.parse(readFileSync(process.env.BMS_BC2_ACTION_INVENTORY, 'utf8')) : { resume: { properties: {} } };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.endsWith('/native-settings') ? { settings: { native_actions: actions } }
      : url.endsWith('/settings') ? { requested_settings: {}, effective_settings: {} } : base })));
  apiModule.api.defaults.adapter = async config => {
    expect(config.url).toBe('/api/jobs');
    requests.push({ body: JSON.parse(config.data), contextHeader: config.headers.get('X-BMS-Launch-Context-ID') });
    return { data: { id: 'destination-child' }, status: 200, statusText: 'OK', headers: {}, config };
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    const job = { id: 'source-job', model_id: 'bindcraft2', mode: 'campaign', status: 'completed', name: 'Source',
      design_count: 0, params: { launch_context_id: 'not-the-destination' }, launch_context_id: 'source-project-context' } as unknown as Job;
    await act(async () => { mounted = create(<QueryClientProvider client={client}><MemoryRouter initialEntries={[
      `/jobs/source-job${destination ? `?launch_context_id=${destination}` : ''}`,
    ]}><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter></QueryClientProvider>); });
    await act(async () => mounted!.root.findAllByType('button').find(button => text(button) === 'Native campaign actions')!.props.onClick());
    await vi.waitFor(() => expect(mounted!.root.findByProps({ 'aria-label': 'Native operation' }).findAllByType('option').length).toBeGreaterThan(1));
    await act(async () => mounted!.root.findByProps({ 'aria-label': 'Native operation' }).props.onChange({ target: { value: 'resume' } }));
    await act(async () => mounted!.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    expect(requests).toHaveLength(1);
    expect(requests[0].body.params).toEqual({ bc2_source_job_id: 'source-job', bc2_action_options: {} });
    expect(requests[0].body.launch_context_id).toBe(destination ?? undefined);
    expect(requests[0].contextHeader).toBe(destination ?? undefined);
  } finally { apiModule.api.defaults.adapter = original; client.clear(); window.history.replaceState(null, '', originalLocation); }
});

it('real lifecycle preparation and approval retain the server child destination, not the source or input context', async () => {
  const original = apiModule.api.defaults.adapter;
  const requests: Array<{ url: string; body: any; skip: unknown }> = [];
  const prepared = { model_id: 'bindcraft2', mode: 'resume', execution_target_id: 'vast:fixture', launch_context_id: 'reserved-child-context',
    params: { bc2_source_job_id: 'source', bc2_action_options: {}, bc2_compilation: '/owned/compilation.json' } };
  vi.spyOn(approvalModule, 'reviewExecutionPlan').mockResolvedValue(true);
  apiModule.api.defaults.adapter = async config => {
    const body = JSON.parse(config.data); const url = String(config.url);
    requests.push({ url, body, skip: config.headers.get('X-BMS-Skip-Launch-Context') });
    if (requests.length === 1) throw { isAxiosError: true, response: { status: 409, data: {
      detail: { code: 'remote_prepared_job_review_required', job_request: prepared },
    } } };
    return { data: url.endsWith('/preview') ? { approval_digest: 'a'.repeat(64), request: prepared } : { id: 'remote-child' },
      status: 200, statusText: 'OK', headers: {}, config };
  };
  try {
    expect((await submitBindCraft2Lifecycle('source', 'resume', {}, 'vast:fixture', 'explicit-destination')).id).toBe('remote-child');
    expect(requests.map(request => request.url)).toEqual(['/api/jobs', '/api/jobs/execution-plan/preview', '/api/jobs']);
    expect(requests[0].body.launch_context_id).toBe('explicit-destination');
    expect(requests[1].body).toEqual(apiModule.prepareExecutionPlacement(prepared));
    expect(requests[2].body).toEqual({ ...apiModule.prepareExecutionPlacement(prepared), execution_plan_approval: 'a'.repeat(64) });
    expect(requests.every(request => request.skip === undefined)).toBe(true);
  } finally { apiModule.api.defaults.adapter = original; }
});
