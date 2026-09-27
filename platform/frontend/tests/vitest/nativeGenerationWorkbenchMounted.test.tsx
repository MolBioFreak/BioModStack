import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { BinderPredictionEvidence } from '../../src/components/BinderPredictionEvidence';
import '../../src/components/ExecutionPlanApproval';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
import Plot from 'react-plotly.js';
import { api } from '../../src/lib/api';
import type { NativeGenerationRecord } from '../../src/lib/nativeBinderResults';
import { document as evidenceDocument, fixture as paeFixture } from '../fixtures/scientificViewerFixture';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-shared-workbench data-structure-url={props.structureUrl} /> }));
vi.mock('react-plotly.js', () => ({ default: () => <div data-native-chart /> }));
const primary = { artifact_id: 'primary', primary: true, target_state: 'A', logical_path: 'native/a.pdb', download_url: '/api/files/a.pdb', sha256: 'a'.repeat(64) };
const alternate = { artifact_id: 'alternate', target_state: 'B', logical_path: 'native/b.cif', download_url: '/api/files/b.cif', sha256: 'b'.repeat(64) };
const records = [
    { candidate_key: 'first', design_id: 'd1', metrics: { seq_length: 80, dsasa: 0, missing: null, has_clash: false }, structures: [primary, alternate] },
    { candidate_key: 'second', design_id: 'd2', metrics: { seq_length: 90, dsasa: 12 }, structures: [alternate] },
    { candidate_key: 'historical', metrics: { seq_length: 10, dsasa: null }, structures: [primary] },
];
const original = api.defaults.adapter;
beforeEach(() => { vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))); });
let tree: ReactTestRenderer;
let client: QueryClient;
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (name: string) => tree.root.findAllByType('button').find(node => text(node) === name)!;
async function mount(props: Partial<React.ComponentProps<typeof NativeBinderGenerationResults>> = {}, rows: NativeGenerationRecord[] = records) {
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: { records: rows.slice(config.params.offset, config.params.offset + config.params.limit), total: rows.length, offset: config.params.offset, limit: config.params.limit, receipt: { settings: { enabled: false } }, publication: { source: 'native' }, artifacts: [] } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="job" {...props} /></QueryClientProvider>); });
    await flush();
}
afterEach(async () => { await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = original; vi.unstubAllGlobals(); });

it('opens the published structure immediately with no confidence or viewer-local artifact invention', async () => {
    const selected = vi.fn(); await mount({ onSelectedDesignIdsChange: selected });
    const props = tree.root.findByType(StructureWorkbench).props;
    expect(props).toMatchObject({ mode: 'standard', structureUrl: primary.download_url, structureContentSha256: primary.sha256, alphafoldView: false, showSequenceTrack: true, showMeasurements: true, showM6Workbench: true, hideControls: false });
    expect(props).not.toHaveProperty('artifactId'); expect(props).not.toHaveProperty('plddt'); expect(props).not.toHaveProperty('pae');
    expect(selected).not.toHaveBeenCalled();
    await act(async () => tree.root.findByProps({ 'aria-label': 'All native metric columns' }).props.onChange({ target: { checked: true } }));
    expect(text(tree.root)).toContain('Explicit null'); expect(text(tree.root)).toContain('false');
    expect(tree.root.findAllByType('details').filter(node => ['Native receipt and accounting', 'Native files and provenance', 'Selected record: complete native readback'].includes(text(node.findByType('summary')))).every(node => !node.props.open)).toBe(true);
    expect(props.workbenchCollapsed).toBe(true);
});
it('matches exact artifact AND state and keeps the exact download', async () => {
    await mount({ selectedDesignId: 'd1', artifactId: 'alternate', targetState: 'B' });
    expect(tree.root.findByType(StructureWorkbench).props).toMatchObject({ structureUrl: alternate.download_url, format: 'cif' });
    expect(tree.root.findAllByType('a').find(node => text(node) === 'Download exact native document')?.props.href).toBe(alternate.download_url);
});
it.each([{ selectedDesignId: 'absent' }, { selectedDesignId: 'd1', artifactId: 'alternate', targetState: 'missing' }])('never substitutes another candidate/document for an unavailable request %j', async props => {
    await mount(props); expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0); expect(text(tree.root)).toContain('not substituted');
});
it('separates ordinary inspection, document inspection and parent bulk selection', async () => {
    const inspect = vi.fn(), selected = vi.fn(); await mount({ onInspectDocument: inspect, onSelectedDesignIdsChange: selected, selectedDesignIds: ['other-page'] });
    await act(async () => button('second').props.onClick()); expect(inspect).toHaveBeenLastCalledWith(records[1], undefined); expect(selected).not.toHaveBeenCalled();
    await act(async () => tree.root.findByProps({ 'aria-label': 'Published document' }).props.onChange({ target: { value: '1' } })); expect(inspect).toHaveBeenLastCalledWith(records[0], alternate);
    await act(async () => button('Data table').props.onClick());
    const checkbox = tree.root.findByProps({ 'aria-label': 'Select this page' });
    await act(async () => checkbox.props.onChange({ target: { checked: true } })); expect(selected).toHaveBeenLastCalledWith(['other-page', 'd1', 'd2']);
});
it('supports local inspection for historical records and documents without callbacks', async () => {
    await mount(); await act(async () => button('second').props.onClick()); expect(tree.root.findByType(StructureWorkbench).props.structureUrl).toBe(alternate.download_url);
    await act(async () => button('Data table').props.onClick());
    await act(async () => button('historical').props.onClick()); expect(tree.root.findByType(StructureWorkbench).props.structureUrl).toBe(primary.download_url);
    expect(tree.root.findAllByType('a').filter(node => String(node.props.href).startsWith('/designs/'))).toHaveLength(0);
});
it('plots native zeros, omits missing numeric pairs, and selects the actual row from the chart', async () => {
    await mount(); await act(async () => button('Analytics').props.onClick());
    const chart = tree.root.findAllByType(Plot).find(node => node.props.data[0].type === 'scatter')!;
    expect(chart.props.data[0]).toMatchObject({ x: [80, 90], y: [0, 12], customdata: ['first', 'second'] });
    expect(text(tree.root)).toContain('2 plotted · 1 omitted');
    await act(async () => chart.props.onClick({ points: [{ customdata: 'second' }] })); expect(tree.root.findByType(StructureWorkbench).props.structureUrl).toBe(alternate.download_url);
});
it('makes full record/settings readbacks available only on demand with exact exports', async () => {
    await mount();
    const details = tree.root.findAllByType('details').find(node => text(node).includes('Selected record: complete native readback'))!;
    await act(async () => details.props.onToggle({ currentTarget: { open: true } }));
    expect(text(details)).toContain(primary.sha256);
    expect(text(details)).toContain('false');
    const exported = tree.root.findAllByType('a').find(node => text(node) === 'Export selected record JSON')!;
    expect(JSON.parse(decodeURIComponent(exported.props.href.split(',')[1]))).toEqual(records[0]);
});
it('does not substitute the primary when the explicit document lacks a download', async () => {
    await mount({ selectedDesignId: 'd1', artifactId: 'alternate', targetState: 'B' }, [{ ...records[0], structures: [primary, { ...alternate, download_url: undefined }] }]);
    expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    expect(text(tree.root)).toContain('The Design primary structure is not substituted');
});
it('supports a single published document without a primary flag', async () => {
    await mount({}, [records[1]]);
    expect(tree.root.findByType(StructureWorkbench).props.structureUrl).toBe(alternate.download_url);
});
it('keeps the shared viewer mounted across chart and tool-panel changes', async () => {
    await mount(); const viewer = tree.root.findByType(StructureWorkbench);
    await act(async () => button('Measurements and exports').props.onClick());
    expect(tree.root.findByType(StructureWorkbench)).toBe(viewer);
    expect(viewer.props.workbenchCollapsed).toBe(false);
    await act(async () => button('Analytics').props.onClick());
    expect(tree.root.findByType(StructureWorkbench)).toBe(viewer);
    await act(async () => button('Structure').props.onClick());
    expect(tree.root.findByType(StructureWorkbench)).toBe(viewer);
    expect(viewer.props.hideControls).toBe(false);
});
it('removes only current-page bulk selections and retains pagination', async () => {
    const selected = vi.fn();
    const pagedRows = [...records, ...Array.from({ length: 23 }, (_, i) => ({ candidate_key: `historical-${i}`, metrics: {}, structures: [] }))];
    await mount({ selectedDesignIds: ['other-page', 'd1', 'd2'], onSelectedDesignIdsChange: selected }, pagedRows);
    await act(async () => tree.root.findByProps({ 'aria-label': 'Select this page' }).props.onChange({ target: { checked: false } }));
    expect(selected).toHaveBeenLastCalledWith(['other-page']);
    await act(async () => button('Next native records').props.onClick()); await flush();
    expect(button('Previous native records').props.disabled).toBe(false);
});

it('keeps sequence, prediction sample, native PAE and persisted directional evidence distinct without changing Mol* or bulk selection', async () => {
    const selected = vi.fn(), requests: any[] = [];
    const run = { run_id: 'ipsae-run', analysis_type: 'ipsae_interface', status: 'completed', params: { pae_cutoff: 10 }, result: { pae_cutoff: 10, dist_cutoff: 15, pair_scores: [{ chain_1: 'X', chain_2: 'Y', ipsae_d0res_asym: 0 }, { chain_1: 'Y', chain_2: 'X', ipsae_d0res_asym: null }] } };
    const sample = { job_id: 'prediction-job', design_id: evidenceDocument.candidateId, name: 'native sample', model_id: 'protenix', status: 'completed', target_state: 'open', binder_chains: ['source-A'], target_chains: ['source-B'], design_url: '/api/designs/prediction', pae_url: '/api/designs/prediction/pae', chain_metrics_url: null, analyses: [run, { run_id: 'pose', analysis_type: 'binder_pose_comparison', status: 'completed', params: {}, result: { binder_fitted_ca_rmsd: 0, target_fitted_binder_ca_rmsd: 4.2, target_fit_ca_rmsd: null, reference_design_id: 'd1', counts: { binder: { matched: 12 } } } }], ipsae: [run] };
    const failed = { ...sample, job_id: 'failed-job', design_id: null, name: null, status: 'failed', target_state: 'closed', design_url: null, pae_url: null, analyses: [], ipsae: [] };
    const sequences = [{ job_id: 'designer', design_id: 'seq-1', name: 'sequence one', model_id: 'fampnn', status: 'completed', native_identity: { sequence_key: 'native-1' }, predictions: [sample, failed] }, { job_id: 'designer', design_id: 'seq-2', name: 'sequence two', model_id: 'fampnn', status: 'completed', native_identity: { sequence_key: 'native-2' }, predictions: [] }];
    const record = { source_design_id: 'd1', candidate_key: 'first', sequences };
    api.defaults.adapter = async config => {
        requests.push(config);
        let data: unknown;
        if (config.url?.endsWith('/generation-results')) data = { records, total: records.length, offset: 0, limit: 1000, receipt: {}, publication: {} };
        else if (config.url?.endsWith('/round')) data = { job_id: 'job', state: 'not_requested', steps: {}, errors: {} };
        else if (config.url?.endsWith('/binder-evidence')) data = { schema_version: 1, job_id: 'job', records: [record], total: 1, offset: 0, limit: 100 };
        else if (config.url === '/api/designs/prediction') data = { id: evidenceDocument.candidateId, scientific_structure_document: evidenceDocument };
        else if (config.url === '/api/designs/prediction/pae') data = paeFixture();
        else throw Error(`Unexpected request ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="job" onSelectedDesignIdsChange={selected} selectedDesignIds={['other-page']} /></QueryClientProvider>); });
    await flush();
    const viewer = tree.root.findByType(StructureWorkbench);
    const choose = async (label: string, value: string) => { await act(async () => tree.root.findByProps({ 'aria-label': label }).props.onChange({ target: { value } })); await flush(); };
    expect(tree.root.findByProps({ 'aria-label': 'Evidence sequence' }).props.value).toBe('');
    expect(tree.root.findAllByProps({ 'aria-label': 'Evidence prediction' })).toHaveLength(0);
    await choose('Evidence sequence', JSON.stringify(['designer', 'seq-1']));
    expect(tree.root.findByProps({ 'aria-label': 'Evidence prediction' }).props.value).toBe('');
    await choose('Evidence prediction', JSON.stringify([sample.job_id, sample.design_id, sample.target_state]));
    expect(tree.root.findAllByType(Plot).find(node => node.props.data[0].type === 'heatmap')?.props.data[0].z).toEqual(paeFixture().pae_matrix);
    const pairTable = tree.root.findByProps({ 'aria-label': 'Persisted ipsae_interface' });
    expect(pairTable.findAllByType('td').map(text)).toEqual(['X', 'Y', '0', 'Y', 'X', 'Unmeasured']);
    expect(text(pairTable)).toContain('PAE cutoff: 10');
    expect(tree.root.findByProps({ 'aria-label': 'Persisted binder_pose_comparison' }).findAllByType('dl')[0].findAllByType('dd').map(text)).toEqual(['0', '4.2', 'Unmeasured', 'd1']);
    expect(tree.root.findByType(StructureWorkbench)).toBe(viewer);
    expect(selected).not.toHaveBeenCalled();
    await choose('Evidence prediction', JSON.stringify([failed.job_id, null, 'closed']));
    expect(text(tree.root)).toContain('Prediction not published · failed · target closed');
    expect(tree.root.findAllByType(Plot).filter(node => node.props.data[0].type === 'heatmap')).toHaveLength(0);
    await choose('Evidence sequence', JSON.stringify(['designer', 'seq-2']));
    expect(text(tree.root)).toContain('Prediction evidence: Unmeasured');
    const exported = tree.root.findAllByType('a').find(node => text(node) === 'Export candidate evidence JSON')!;
    expect(JSON.parse(decodeURIComponent(exported.props.href.split(',')[1]))).toEqual(record);
    expect(requests.every(config => config.method === 'get')).toBe(true);
});

it('reads round errors without submitting, retries explicitly and reuses the retained remote request through the real approval dialog', async () => {
    const requests: any[] = [];
    const retained = { model_id: 'protenix', mode: 'predict', execution_target_id: 'fixture-remote', params: { complex_components: [{ id: 'binder', sequence: 'TEST' }] }, binder_round_step: { root_job_id: 'job', step_id: 'step' }, launch_context_id: 'retained-destination' };
    let state = 'needs_retry';
    api.defaults.adapter = async config => {
        requests.push(config);
        let data: unknown;
        if (config.url?.endsWith('/binder-evidence')) data = { schema_version: 1, job_id: 'job', offset: 0, limit: 100, total: 0, records: [] };
        else if (config.url?.endsWith('/round/retry')) { state = 'review_required'; data = {}; }
        else if (config.url?.endsWith('/round')) data = { job_id: 'job', state, errors: state === 'needs_retry' ? { d1: 'source mapping unavailable' } : {}, steps: state === 'needs_retry' ? {} : { step: { state, metadata: { stage: 'prediction', source_design_id: 'd1', target_state: 'open' }, request: retained } } };
        else if (config.url === '/api/jobs/execution-plan/preview') data = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: retained, plan: { requested_json: retained.params, effective_json: retained.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
        else if (config.url === '/api/jobs') { state = 'queued'; data = { id: 'new-child' }; }
        else throw Error(`Unexpected ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><BinderPredictionEvidence jobId="job" /></QueryClientProvider>); });
    await flush();
    expect(text(tree.root)).toContain('source mapping unavailable');
    expect(requests.every(config => config.method === 'get')).toBe(true);
    await act(async () => button('Retry binder round').props.onClick()); await flush();
    expect(text(tree.root)).toContain('Round: review_required');
    await act(async () => button('Review prepared remote step step').props.onClick()); await flush();
    expect(requests.filter(config => config.url === '/api/jobs')).toHaveLength(0);
    if (!document.body.textContent?.includes('Approve and submit')) throw Error(`UI ${text(tree.root)}; DOM ${document.body.textContent}; calls ${requests.map(c => c.url).join(',')}`);
    const approve = Array.from(document.querySelectorAll('button')).find(el => el.textContent === 'Approve and submit');
    expect(approve).toBeDefined();
    await act(async () => approve!.click()); await flush();
    const posted = JSON.parse(requests.find(config => config.url === '/api/jobs').data);
    expect(posted).toMatchObject({ ...retained, execution_plan_approval: 'a'.repeat(64) });
    expect(text(tree.root)).toContain('Round: queued');
    expect(requests.at(-1).method).toBe('get');
});
