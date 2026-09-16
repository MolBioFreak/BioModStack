import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { HistoryPanel } from '../../src/components/MolBioToolkit/panels/HistoryPanel';
import { PCRPanel } from '../../src/components/MolBioToolkit/panels/PCRPanel';
const mocks = vi.hoisted(() => ({ select: vi.fn() }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: mocks.select, contextHref: () => '/' }) }));
vi.mock('../../src/components/MolBioToolkit/PrimerTmSettingsPanel', () => ({ PrimerTmSettingsPanel: () => null }));
let host: HTMLDivElement, root: Root;
const originalAdapter = api.defaults.adapter;
const seq = { name: 'editable', sequence: 'ACGTACGT', circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const noop = () => {};
const settings = { algorithm: 'wallace', salt_correction: 'none' } as any;
function deferred<T = any>() { let resolve!: (x: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function summary(sequenceId: string, n: number) { return { id: `${sequenceId}-r${n}`, revision_id: `${sequenceId}-r${n}`, sequence_id: sequenceId, revision_number: n, change_kind: 'sequence_edit', content_sha256: 'a'.repeat(64), content_length: 8, topology: 'linear', created_at: '2026-09-11T00:00:00Z', created_by: null, is_current: n === 0 }; }
function pcrSummary(experiment: string, n: number) { return { id: `${experiment}-r${n}`, experiment_id: experiment, revision_number: n, review_state: 'draft', created_at: '2026-09-11T00:00:00Z' }; }
function exact(experiment: string, n: number) { return { ...pcrSummary(experiment, n), relation: 'historical', template_snapshot: { sequence: 'EXACT-TEMPLATE' }, product_snapshot: { sequence: 'EXACT-PRODUCT' }, reopen_destination: { surface: 'molbio-pcr-experiment-revision', params: { experiment_id: experiment, revision_id: `${experiment}-r${n}` } } }; }
function http(handler: (config: any) => any) {
 const requests = vi.fn(async (config: any) => ({ data: await handler(config), status: 200, statusText: 'OK', headers: {}, config }));
 api.defaults.adapter = requests;
 return requests;
}
async function render(node: React.ReactNode) { await act(async () => root.render(node)); }
async function click(label: string) { const button = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(button, label).toBeDefined(); await act(async () => button!.click()); }
function history(id: string | null) { return <HistoryPanel sequenceData={seq} selectedSequenceId={id} historyJournal={[{ id: 'local', label: 'Local undo', summary: 'Local edit only', timestamp: 0 } as any]} workspaces={[]} activeWorkspaceId="local" onActivateWorkspace={noop} revisionHref={(s,r) => `/molbio?molbio_sequence_id=${s}&molbio_revision_id=${r}`} />; }
let navigate: ReturnType<typeof useNavigate>;
function PcrHarness() { navigate = useNavigate(); return <PCRPanel sequenceData={seq} sequenceId="editable" onHighlight={noop} tmOptions={null} tmSettings={settings} onTmSettingsChange={noop} />; }
async function pcr(experiment='e', revision=151) { await render(<MemoryRouter initialEntries={[`/?pcr_experiment_id=${experiment}&pcr_revision_id=${experiment}-r${revision}`]}><PcrHarness /></MemoryRouter>); }
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); mocks.select.mockReset(); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); api.defaults.adapter = originalAdapter; });
it('molecular navigation pages scalar summaries beyond 100 without fetching any snapshot', async () => {
 const requests = http(config => { expect(config.url).toBe('/api/molbio/sequences/a/revisions'); expect(config.params.limit).toBe(50); const offset = config.params.offset; return Array.from({ length: offset < 100 ? 50 : 1 }, (_, i) => summary('a', offset + i)); });
 await render(history('a'));
 expect(host.querySelectorAll('article')).toHaveLength(50);
 await click('Load more revisions'); await click('Load more revisions');
 expect(host.querySelectorAll('article')).toHaveLength(101);
 expect(requests.mock.calls.map(([c]) => c.params.offset)).toEqual([0,50,100]);
 expect(host.querySelector('a[href*="a-r100"]')).not.toBeNull();
 expect(host.textContent).toContain('sequence_edit'); expect(host.textContent).toContain('Local undo');
 expect(host.textContent).not.toContain('Load more revisions');
});
it('molecular continuation rejects late preceding-sequence responses and restarts ABA at zero', async () => {
 const pending = deferred();
 const requests = http(config => config.url.includes('/a/') && config.params.offset === 50 ? pending.promise : Array.from({ length: 50 }, (_, i) => summary(config.url.includes('/a/') ? 'a' : 'b', i)));
 await render(history('a')); await click('Load more revisions'); await render(history('b'));
 await act(async () => pending.resolve([summary('a', 999)]));
 expect(host.textContent).not.toContain('a-r999'); expect(host.textContent).toContain('b-r0');
 await render(history('a'));
 expect(requests.mock.calls.at(-1)?.[0].params.offset).toBe(0);
});
it('molecular wrong-identity summaries fail without presenting exact navigation', async () => {
 http(() => [summary('wrong', 1)]); await render(history('a'));
 expect(host.textContent).toContain('different sequence'); expect(host.querySelectorAll('article')).toHaveLength(0);
});
it('selected PCR exact revision beyond page one remains usable when history fails', async () => {
 const requests = http(config => { if (config.url.endsWith('/revisions/e-r151')) return exact('e',151); expect(config.params).toMatchObject({ summary: true, limit: 50, offset: 0 }); throw new Error('history unavailable'); });
 await pcr(); expect(host.textContent).toContain('EXACT-PRODUCT'); expect(host.textContent).toContain('Exact revision remains available');
 expect(host.textContent).not.toContain('Unable to load exact PCR revision authority'); expect(requests).toHaveBeenCalledTimes(2);
});
it('PCR pages scalar navigation and selects an exact ID without hydrating history snapshots', async () => {
 const requests = http(config => {
  if (config.url.includes('/revisions/')) return exact('e',151);
  expect(config.url).toBe('/api/molbio/pcr-experiments/e'); expect(config.params.summary).toBe(true); expect(config.params.limit).toBe(50);
  const offset = config.params.offset;
  return { id: 'e', revisions: Array.from({ length: offset < 100 ? 50 : 1 }, (_, i) => pcrSummary('e',offset+i)), current_revision_id: 'e-r0', has_more: offset < 100, next_offset: offset < 100 ? offset+50 : null };
 });
 await pcr(); await click('Load more PCR revisions'); await click('Load more PCR revisions');
 expect(host.textContent).toContain('101 revisions loaded'); expect(host.textContent).toContain('e-r100');
 const button = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('e-r100'))!; await act(async () => button.click());
 expect(mocks.select).toHaveBeenLastCalledWith({ pcr_experiment_id: 'e', pcr_revision_id: 'e-r100' });
 expect(requests.mock.calls.filter(([c]) => c.url.includes('/revisions/'))).toHaveLength(1);
 expect(requests.mock.calls.filter(([c]) => c.params?.summary).map(([c]) => c.params.offset)).toEqual([0,50,100]);
});
it('PCR rejects stale exact detail and summary pages after experiment switch', async () => {
 const oldDetail = deferred(), oldHistory = deferred();
 http(config => config.url.includes('/e/') || config.url.endsWith('/e') ? (config.url.includes('/revisions/') ? oldDetail.promise : oldHistory.promise) : config.url.includes('/revisions/') ? exact('new',1) : { id:'new', revisions:[pcrSummary('new',1)], current_revision_id:'new-r1', has_more:false,next_offset:null });
 await pcr(); await act(async () => navigate('/?pcr_experiment_id=new&pcr_revision_id=new-r1'));
 await act(async () => { oldDetail.resolve(exact('e',151)); oldHistory.resolve({ revisions:[pcrSummary('e',999)], current_revision_id:'e-r999',has_more:false,next_offset:null }); });
 expect(host.textContent).toContain('new-r1'); expect(host.textContent).not.toContain('e-r151'); expect(host.textContent).not.toContain('e-r999');
});
