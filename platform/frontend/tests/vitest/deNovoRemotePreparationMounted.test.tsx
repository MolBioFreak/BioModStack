import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { RemotePreloadPanel } from '../../src/components/dashboard/RemotePreloadPanel';
import { DE_NOVO_PRELOAD_SELECTION } from '../../src/components/dashboard/IndependentProvisionPanel';
import { api, EXECUTION_TARGET_STORAGE_KEY, type ExecutionTarget, type ProvisionSelection } from '../../src/lib/api';

// Only GPU rendering is replaced; shell, all leaves, draft owners and transport are real.
vi.mock('../../src/components/CanonicalMeshPreview', () => ({ default: () => <div /> }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => <div /> }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div /> }));

const family = DE_NOVO_PRELOAD_SELECTION;
const ready: ExecutionTarget = { id: 'vast:123', provider: 'vast', provider_instance_id: '123', name: 'Worker', state: 'ready', active: true, host: 'host', port: 22, username: 'root', remote_root: '/opt/bms', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null };
const other = { ...ready, id: 'vast:456', name: 'Other', provider_instance_id: '456' };
const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const preview = (selection: ProvisionSelection) => ({ selection, artifacts: [], total_bytes: 0, preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'managed_asset_activation' });
const originalAdapter = api.defaults.adapter;
let root: Root;
let host: HTMLDivElement;
let client: QueryClient;
let posts: Array<{ url: string; body: any }>;
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
async function mount(node: React.ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>));
  await act(async () => { await settle(); });
}
function button(text: string, within: ParentNode = host) {
  const found = [...within.querySelectorAll('button')].find(node => node.textContent === text);
  expect(found, text).toBeTruthy(); return found!;
}
async function click(text: string, within: ParentNode = host) {
  await act(async () => { button(text, within).click(); await settle(); });
}
function field(name: string) {
  const direct = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${name}"]`);
  if (direct) return direct;
  const label = [...host.querySelectorAll('label')].find(node => [...node.childNodes].filter(child => child.nodeType === Node.TEXT_NODE).map(child => child.textContent).join('').trim() === name);
  const input = label?.querySelector<HTMLInputElement | HTMLSelectElement>('input,select');
  expect(input, name).toBeTruthy(); return input!;
}
async function edit(name: string, value: string) {
  const input = field(name);
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    await settle();
  });
}
const familyPanel = () => host.querySelector('[aria-label="Independent dependency preparation"]')!;
async function prepareFamily() {
  expect(familyPanel()?.textContent).toContain('All De Novo dependencies');
  const before = posts.length;
  await click('Preview artifact downloads', familyPanel());
  expect(posts[before]).toEqual({ url: '/api/execution-targets/vast%3A123/provision/preview', body: family });
  await click('Start provision', familyPanel());
  expect(posts[before + 1]).toEqual({ url: '/api/execution-targets/vast%3A123/provision', body: { ...family, preview_sha256: 'b'.repeat(64) } });
  expect(posts).toHaveLength(before + 2);
}
beforeEach(() => {
  posts = []; sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, ready.id);
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, options?: RequestInit) => {
    expect(options?.method ?? 'GET').toBe('GET');
    return { ok: true, json: async () => ({ structures: [], cached: [] }) };
  }));
  api.defaults.adapter = async config => {
    const url = String(config.url);
    if (config.method === 'get') {
      if (url === '/api/execution-targets') return response([ready, other]);
      if (url.endsWith('/catalog')) return response([family]);
      if (url.endsWith('/geometries')) return response({ geometries: [] });
      if (url.includes('/sequence-settings/')) return response({ engine: 'proteinmpnn', initial_values: {}, contextual_defaults: {}, params: [] });
      if (url.includes('/system')) return response({ gpus: [], gpu_error: null });
      if (url.endsWith('/runtime-inventory')) return response(null);
      return response([]);
    }
    const body = config.data ? JSON.parse(String(config.data)) : undefined;
    posts.push({ url, body });
    if (!url.includes('/provision')) throw new Error(`Unexpected side effect: ${url}`);
    return response(url.endsWith('/preview') ? preview(body) : ready);
  };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([
  ['rfd3', 'unconditional'], ['disco', 'unconditional'], ['disco', 'dna_conditioned'],
  ['disco', 'rna_conditioned'], ['disco', 'ligand_conditioned'], ['disco', 'custom_json'],
  ['laproteina', 'unconditional'], ['laproteina', 'motif_scaffolding'],
])('prepares incomplete Generate %s / %s without a scientific request', async (generator, design_task) => {
  const changed = vi.fn();
  await mount(<ProteinModificationTemplate onBack={() => {}} onDraftChange={changed} initialValues={{ generator, design_task, job_name: design_task === 'unconditional' ? '' : 'Incomplete draft' }} />);
  expect(posts).toEqual([]);
  expect(host.querySelector('[aria-label="Unsaved workflow provisioning"]')).toBeNull();
  const draft = JSON.stringify(changed.mock.lastCall?.[0]);
  await prepareFamily();
  expect(JSON.stringify(changed.mock.lastCall?.[0])).toBe(draft);
});

it.each(['rfd3_iteration', 'region_redesign', 'shape_blueprint'])('prepares %s with no source or geometry through the actual leaf', async modification_mode => {
  const changed = vi.fn();
  await mount(<ProteinModificationTemplate onBack={() => {}} onDraftChange={changed} initialValues={{ modification_mode }} />);
  expect(posts).toEqual([]);
  expect(host.querySelector('[aria-label="Unsaved workflow provisioning"]')).toBeNull();
  const draft = JSON.stringify(changed.mock.lastCall?.[0]);
  await prepareFamily();
  expect(JSON.stringify(changed.mock.lastCall?.[0])).toBe(draft);
});

it('connects global Workflow → De Novo to the telemetry target, not the stored worker', async () => {
  sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, other.id);
  await mount(<RemotePreloadPanel target={ready} jobs={[]} onChanged={async () => {}} />);
  await act(async () => { const details = [...host.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent?.includes('Prepare worker'))!; details.open = true; details.dispatchEvent(new Event('toggle')); await settle(); });
  await edit('Preparation workflow', family.model_id);
  expect(posts).toEqual([]);
  await prepareFamily();
  expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe(other.id);
});

it.each(['rfd3', 'disco', 'laproteina'])('keeps %s exact requested science separate and unchanged by family preparation', async generator => {
  await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ generator }} />);
  if (generator === 'rfd3') { await edit('Minimum length', '111'); await edit('Seed', '17'); }
  const exact = () => host.querySelector('[aria-label="Unsaved workflow provisioning"]')!;
  await click('Preview artifact downloads', exact());
  const requested = structuredClone(posts[0].body);
  expect(requested.kind).toBe('workflow');
  expect(requested.workflow_request.params.generator).toBe(generator);
  if (generator === 'rfd3') expect(requested.workflow_request.params).toMatchObject({ min_length: 111, seed: 17 });
  await prepareFamily();
  await click('Preview artifact downloads', exact());
  expect(posts.at(-1)?.body).toEqual(requested);
  if (generator === 'rfd3') { expect(field('Minimum length').value).toBe('111'); expect(field('Seed').value).toBe('17'); }
});

it('does not post on task, goal, engine or worker navigation', async () => {
  await mount(<ProteinModificationTemplate onBack={() => {}} />);
  await edit('Engine', 'disco'); await edit('Goal', 'dna_conditioned');
  await click('Redesign structure'); await click('Shape'); await click('Generate');
  await click('Vast · Other');
  expect(posts).toEqual([]);
  expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe(other.id);
});

it('preserves unavailable placement without fallback or provider mutation', async () => {
  client.setQueryData(['execution-targets'], { data: [] });
  await mount(<ExecutionTargetPicker preloadSelection={family} workflowRequest={null} />);
  expect(host.textContent).toContain('Selected worker vast:123 is unavailable');
  expect(button('Local').getAttribute('aria-pressed')).toBe('false');
  expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe(ready.id);
  expect(familyPanel()).toBeNull();
  expect(posts).toEqual([]);
});

it('cannot arm an old worker after a late preview, even after switching away and back', async () => {
  await mount(<ExecutionTargetPicker preloadSelection={family} workflowRequest={null} />);
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await click('Preview artifact downloads', familyPanel());
  await click('Vast · Other'); await click('Vast · Worker');
  await act(async () => { resolve(response(preview(family))); await settle(); });
  expect(button('Start provision', familyPanel()).disabled).toBe(true);
  expect(host.querySelector('[aria-label="Provision preview"]')).toBeNull();
  expect(posts).toEqual([]);
  await prepareFamily();
});
