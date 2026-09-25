import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Plot from 'react-plotly.js';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { CohortAnalytics } from '../../src/components/CohortAnalytics';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
import { api } from '../../src/lib/api';
import type { NativeGenerationRecord } from '../../src/lib/nativeBinderResults';

// Only the renderer/structure engine and HTTP transport are doubled: the dashboard,
// analytics wrapper, query lifecycle, filtering, selection and export code are real.
vi.mock('react-plotly.js', () => ({ default: () => <div data-plot-renderer /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-structure-url={props.structureUrl} /> }));
type Props = React.ComponentProps<typeof NativeBinderGenerationResults>;
const fixture = (): NativeGenerationRecord[] => Array.from({ length: 1205 }, (_, i) => ({
    candidate_key: `native:${i}`, design_id: `design/${i}`, native_input_id: `input-${i}`,
    metrics: { seq_length: i, dsasa: i / 10, precise: 0.1234567890123456, flag: false },
    structures: [{ artifact_id: `artifact-${i}`, primary: true, target_state: 'A', logical_path: `native/${i}.pdb`, download_url: `/api/files/${i}.pdb`, sha256: 'a'.repeat(64) },
        { artifact_id: `alternate-${i}`, target_state: 'B', logical_path: `native/${i}.cif`, download_url: `/api/files/${i}.cif`, sha256: 'b'.repeat(64) }],
}));
const originalAdapter = api.defaults.adapter;
let tree: ReactTestRenderer;
let client: QueryClient;
let requests: any[];
let blobs: Map<string, Blob>;
const changes = vi.fn();
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree.root.findAllByType('button').find(node => text(node) === label)!;
const control = (label: string) => tree.root.findByProps({ 'aria-label': label });
const click = async (label: string) => { await act(async () => button(label).props.onClick()); };
const change = async (label: string, value: string | boolean) => { await act(async () => control(label).props.onChange({ target: typeof value === 'boolean' ? { checked: value } : { value } })); };
const flush = async () => { for (let i = 0; i < 10; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const tableRows = () => control('Candidate data table').findByType('tbody').findAllByType('tr');
const visibleIds = () => tableRows().map(row => text(row.findAllByType('button')[0]));
const cohortIds = () => tree.root.findByType(CohortAnalytics).props.rows.map((row: any) => row.id);
const scatter = () => tree.root.findAllByType(Plot).find(node => node.props.data[0]?.type === 'scatter')!;
function Harness(props: Partial<Props>) {
    const [ids, setIds] = useState(props.selectedDesignIds ?? []);
    return <NativeBinderGenerationResults jobId="cohort-job" {...props} selectedDesignIds={ids} onSelectedDesignIdsChange={next => { changes(next); setIds(next); }} />;
}
function response(config: any, rows: NativeGenerationRecord[]) {
    const { offset, limit } = config.params;
    return { config, status: 200, statusText: 'OK', headers: {}, data: { records: rows.slice(offset, offset + limit), total: rows.length, offset, limit, receipt: { test_only: true }, publication: { source: 'test-only' }, artifacts: [] } };
}
async function mount(rows = fixture(), props: Partial<Props> = {}, transport?: (config: any) => Promise<any>) {
    api.defaults.adapter = async config => {
        requests.push(config);
        if (config.method !== 'get' || !config.url?.endsWith('/generation-results')) throw new Error(`Unexpected request ${config.method} ${config.url}`);
        return transport ? transport(config) : response(config, rows);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><Harness {...props} /></QueryClientProvider>); });
    await flush();
}
async function exported(label: string): Promise<string> {
    const link = tree.root.findAllByType('a').find(node => text(node) === label)!;
    const blob = blobs.get(link.props.href);
    expect(blob).toBeDefined();
    return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob!); });
}
beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    requests = []; blobs = new Map(); changes.mockClear();
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const url = `blob:test-${blobs.size}`; blobs.set(url, blob as Blob); return url; });
});
afterEach(async () => {
    await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals();
    expect(requests.every(config => config.method === 'get' && config.url.endsWith('/generation-results'))).toBe(true);
});

it('loads 1205 records through native pages while bounding the table and charting the whole cohort', async () => {
    const rows = fixture(); await mount(rows);
    expect(requests.map(config => config.params)).toEqual([{ offset: 0, limit: 1000 }, { offset: 1000, limit: 1000 }]);
    expect(requests.every(config => config.signal instanceof AbortSignal)).toBe(true);
    expect(tableRows()).toHaveLength(25);
    expect(cohortIds()).toEqual(rows.map(row => row.candidate_key));
    expect(scatter().props.data[0].customdata).toHaveLength(1205);
    expect(scatter().props.data[0].x).toEqual(rows.map(row => (row.metrics as any).seq_length));
    await click('Analytics');
    const stats = tree.root.findAllByType('table').find(table => table.findAllByType('caption').some(caption => text(caption) === 'Descriptive statistics for the full filtered cohort'))!;
    const seq = stats.findByType('tbody').findAllByType('tr').find(row => text(row.findByType('code')) === 'seq_length')!;
    expect(text(seq.findAllByType('td')[0])).toBe('1,205');
    await change('Rows per page', '100'); expect(tableRows()).toHaveLength(100);
    await click('Next native records'); expect(visibleIds()[0]).toBe('native:100');
    expect(cohortIds()).toHaveLength(1205);
});

it('searches, sorts and intersects metric filters across server and table pages without coercion', async () => {
    const rows = fixture();
    rows[1001].metrics = { seq_length: 1001, dsasa: null, flag: false };
    rows[1002].metrics = { seq_length: 1002, flag: false };
    rows[1003].metrics = { seq_length: 1003, dsasa: false };
    rows[1004].metrics = { seq_length: 1004, dsasa: '0' };
    await mount(rows);
    await change('Search candidates', 'input-1204'); expect(visibleIds()).toEqual(['native:1204']);
    await click('Clear filters');
    await act(async () => control('Sort by seq_length').props.onClick());
    expect(visibleIds()[0]).toBe('native:0');
    await act(async () => control('Sort by seq_length').props.onClick());
    expect(visibleIds()[0]).toBe('native:1204');
    await click('Add metric filter'); await change('Filter 1 metric', 'seq_length'); await change('Filter 1 minimum', '1000');
    await click('Add metric filter'); await change('Filter 2 metric', 'dsasa');
    await change('Filter 2 condition', 'null'); expect(visibleIds()).toEqual(['native:1001']);
    await change('Filter 2 condition', 'missing'); expect(visibleIds()).toEqual(['native:1002']);
    await change('Filter 2 condition', 'nonNumeric'); expect(visibleIds()).toEqual(['native:1004', 'native:1003']);
    await change('Filter 2 condition', 'range'); await change('Filter 2 minimum', '100'); await change('Filter 2 maximum', '100.5');
    expect(visibleIds()).toEqual(['native:1005', 'native:1000']);
    await click('Clear filters'); await click('Add metric filter'); await change('Filter 1 metric', 'dsasa');
    await change('Filter 1 minimum', '0'); await change('Filter 1 maximum', '0');
    expect(visibleIds()).toEqual(['native:0']); expect(scatter().props.data[0].y).toEqual([0]);
});

it('keeps missing values last in either sort direction and retains exact precision in cell titles', async () => {
    await mount([
        { candidate_key: 'null', metrics: { score: null } }, { candidate_key: 'absent', metrics: {} },
        { candidate_key: 'zero', metrics: { score: 0 } }, { candidate_key: 'precise', metrics: { score: 0.1234567890123456 } },
    ]);
    await act(async () => control('Sort by score').props.onClick());
    expect(visibleIds()).toEqual(['zero', 'precise', 'null', 'absent']);
    await act(async () => control('Sort by score').props.onClick());
    expect(visibleIds()).toEqual(['precise', 'zero', 'null', 'absent']);
    expect(tableRows()[0].findAllByType('td')[1].props.title).toBe('0.1234567890123456');
});

it('restores an exact requested candidate and alternate document beyond row 1000 without substitution', async () => {
    const rows = fixture(); let pending: any; let finish!: (value: any) => void;
    await mount(rows, { selectedDesignId: 'design/1204', artifactId: 'alternate-1204', targetState: 'B' }, async config => {
        if (config.params.offset === 1000) { pending = config; return new Promise(resolve => { finish = resolve; }); }
        return response(config, rows);
    });
    expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    expect(text(tree.root)).toContain('Loading the requested candidate');
    await act(async () => finish(response(pending, rows))); await flush();
    expect(tree.root.findByType(StructureWorkbench).props).toMatchObject({ structureUrl: '/api/files/1204.cif', structureDocumentId: 'alternate-1204', structureContentSha256: 'b'.repeat(64), format: 'cif' });
    expect(tree.root.findAllByType('a').find(node => text(node) === 'Download exact native document')?.props.href).toBe('/api/files/1204.cif');
    expect(changes).not.toHaveBeenCalled();
});

it('preserves parent/off-view selection through page toggles, matching selection, filters and selected-only', async () => {
    await mount(fixture(), { selectedDesignIds: ['external-design', 'design/1204'] });
    await change('Select this page', true);
    expect(changes).toHaveBeenLastCalledWith(['external-design', 'design/1204', ...Array.from({ length: 25 }, (_, i) => `design/${i}`)]);
    await click('Next native records'); await change('Select this page', true);
    await change('Selected candidates only', true); expect(cohortIds()).toHaveLength(51);
    await click('Next native records'); expect(visibleIds()[0]).toBe('native:25');
    await change('Search candidates', 'native:1204'); expect(visibleIds()).toEqual(['native:1204']);
    await change('Search candidates', ''); expect(cohortIds()).toHaveLength(51);
    await click('Clear filters'); await change('Search candidates', 'input-120');
    await click('Select all matching (6)');
    expect(changes.mock.lastCall![0]).toEqual(expect.arrayContaining(['external-design', 'design/0', 'design/49', 'design/120', 'design/1200', 'design/1204']));
    await change('Select this page', false);
    expect(changes.mock.lastCall![0]).toEqual(['external-design', ...Array.from({ length: 50 }, (_, i) => `design/${i}`)]);
});

it('maps Plotly native identities to canonical Design IDs, ignores unjoined/unknown IDs, and separates inspection', async () => {
    const rows = fixture(); delete rows[1203].design_id;
    await mount(rows, { selectedDesignIds: ['external-design'] });
    await change('Scatter drag mode', 'lasso');
    expect(scatter().props.layout.dragmode).toBe('lasso');
    await act(async () => scatter().props.onSelected({ points: [{ customdata: 'native:1204' }, { customdata: 'native:1204' }, { customdata: 'native:1203' }, { customdata: 'unknown' }, { pointIndex: 1 }] }));
    expect(changes).toHaveBeenLastCalledWith(['external-design', 'design/1204']);
    expect(tree.root.findByType(CohortAnalytics).props.selectedIds).toEqual(['native:1204']);
    const count = changes.mock.calls.length;
    await act(async () => scatter().props.onClick({ points: [{ customdata: 'native:1204' }] }));
    expect(tree.root.findByType(StructureWorkbench).props.structureUrl).toBe('/api/files/1204.pdb');
    expect(button('Structure').props['aria-selected']).toBe(true);
    expect(changes).toHaveBeenCalledTimes(count);
});

it('exports matching, selected and all-loaded scopes as exact native JSON and precision-preserving CSV', async () => {
    const rows = fixture();
    rows[1204].metrics = { seq_length: 1204, dsasa: 0, precise: 0.1234567890123456, flag: false, empty: null,
        formula: '=SUM(A1:A2)', plus: '+cmd', minus: '-cmd', at: '@cmd', tab: '\tcmd', carriage: '\rcmd', quote: 'a,"b"', negative: -2.5 };
    await mount(rows, { selectedDesignIds: ['external-design', 'design/0', 'design/1204'] });
    await change('Search candidates', 'input-1204');
    expect(JSON.parse(await exported('Native JSON'))).toEqual([rows[1204]]);
    const csv = await exported('Metrics CSV');
    expect(csv).toContain('"0.1234567890123456"'); expect(csv).toContain('"false"'); expect(csv).toContain('"Explicit null"');
    for (const value of ['=SUM(A1:A2)', '+cmd', '-cmd', '@cmd', '\tcmd', '\rcmd']) expect(csv).toContain(`"'${value}"`);
    expect(csv).toContain('"a,""b"""'); expect(csv).toContain('"-2.5"'); expect(csv).not.toContain('"\'-2.5"');
    await change('Export scope', 'selected');
    expect(JSON.parse(await exported('Native JSON'))).toEqual([rows[0], rows[1204]]);
    expect((await exported('Metrics CSV')).split('\r\n')).toHaveLength(3);
    await change('Export scope', 'all');
    expect(JSON.parse(await exported('Native JSON'))).toEqual(rows);
    expect((await exported('Metrics CSV')).split('\r\n')).toHaveLength(1206);
    expect((rows[1204].metrics as any).formula).toBe('=SUM(A1:A2)');
});

it('retains the first 1000 usable records after a later-page error and retries the failed page', async () => {
    const rows = fixture(); let fail = true;
    await mount(rows, {}, async config => { if (config.params.offset === 1000 && fail) throw new Error('test-only later page failure'); return response(config, rows); });
    expect(text(tree.root)).toContain('partially loaded'); expect(cohortIds()).toHaveLength(1000); expect(tableRows()).toHaveLength(25);
    expect(JSON.parse(await exported('Native JSON'))).toEqual(rows.slice(0, 1000));
    await change('Search candidates', 'input-999'); expect(visibleIds()).toEqual(['native:999']);
    fail = false; await click('Retry native readback'); await flush();
    expect(text(tree.root)).not.toContain('partially loaded');
    await click('Clear filters'); expect(cohortIds()).toHaveLength(1205);
    expect(requests.map(config => config.params.offset)).toEqual([0, 1000, 1000]);
});

it('does not let a delayed previous-job page populate the new job', async () => {
    const old = fixture(); let finish!: (value: any) => void; let pending: any;
    const fresh = [{ candidate_key: 'new-job-only', metrics: { seq_length: 7, dsasa: 0 }, structures: [] }];
    await mount(old, {}, async config => {
        if (config.url.includes('/cohort-job/') && config.params.offset === 1000) { pending = config; return new Promise(resolve => { finish = resolve; }); }
        return response(config, config.url.includes('/new-job/') ? fresh : old);
    });
    expect(cohortIds()).toHaveLength(1000);
    await act(async () => tree.update(<QueryClientProvider client={client}><Harness jobId="new-job" /></QueryClientProvider>)); await flush();
    expect(pending.signal.aborted).toBe(true); expect(cohortIds()).toEqual(['new-job-only']);
    await act(async () => finish(response(pending, old))); await flush();
    expect(cohortIds()).toEqual(['new-job-only']); expect(JSON.parse(await exported('Native JSON'))).toEqual(fresh);
    expect(text(tree.root)).not.toContain('native:1000');
});

it('retains published zero-yield readbacks without invented candidates or charts', async () => {
    await mount([]); expect(text(tree.root)).toContain('No native candidate records were emitted');
    expect(tree.root.findAllByType(CohortAnalytics)).toHaveLength(0); expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    expect(requests).toHaveLength(1);
    const receipt = tree.root.findAllByType('details').find(node => text(node.findByType('summary')) === 'Native receipt and accounting')!;
    await act(async () => receipt.props.onToggle({ currentTarget: { open: true } }));
    expect(text(receipt)).toContain('test_only');
});

it('recovers an initial read failure only through GET retry without inventing an empty publication', async () => {
    const rows = fixture(); let fail = true;
    await mount(rows, {}, async config => { if (fail) throw new Error('test-only initial failure'); return response(config, rows); });
    expect(text(tree.root)).toContain('Native publication is not available');
    expect(text(tree.root)).not.toContain('zero-yield');
    expect(tree.root.findAllByType(CohortAnalytics)).toHaveLength(0);
    expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    fail = false; await click('Retry native readback'); await flush();
    expect(cohortIds()).toHaveLength(1205);
    expect(requests.map(config => config.params.offset)).toEqual([0, 0, 1000]);
    expect(changes).not.toHaveBeenCalled();
});

it('projects BoltzGen verified native scalars without turning disposition criteria or unavailable values into scores', async () => {
    const metric = (key: string, state: string, value: unknown, unit: string, reason_code: string | null = null) =>
        ({ metric_key: key, state, value, unit, reason_code });
    // Authorized test-only shape: filter_summary dispositions joined to scalar_block's native_metrics.
    const rows: NativeGenerationRecord[] = [
        { candidate_key: 'selected-cif', design_id: 'boltz-design-1', candidate_id: 'selected-cif', disposition: 'selected',
            criteria: [{ criterion: 'design_ptm', disposition: 'passed', evidence: { value: 0.8 } }],
            native_metrics: { schema_version: 1, producer: 'boltzgen', design_id: 'boltz-design-1', metrics: {
                design_ptm: metric('design_ptm', 'ok', 0.8, 'fraction'),
                affinity_probability: metric('affinity_probability', 'ok', 0, 'fraction'),
                filter_rmsd: metric('filter_rmsd', 'ok', 1.25, 'angstrom'),
            } }, structures: [{ artifact_id: 'native-cif', target_state: null, primary: true, logical_path: 'selected-cif.cif', download_url: '/api/files/selected-cif.cif', sha256: 'c'.repeat(64) }] },
        { candidate_key: 'selected-missing', candidate_id: 'selected-missing', disposition: 'selected',
            criteria: [],
            native_metrics: { schema_version: 1, producer: 'boltzgen', metrics: {
                design_ptm: metric('design_ptm', 'unavailable', null, 'fraction', 'missing_native_metric'),
                affinity_probability: metric('affinity_probability', 'invalid', null, 'fraction', 'nonfinite'),
                filter_rmsd: metric('filter_rmsd', 'ok', 2.5, 'angstrom'),
            } }, structures: [] },
        { candidate_key: 'rejected-cif', candidate_id: 'rejected-cif', disposition: 'rejected', criterion: 'design_ptm', reason_code: 'below_threshold',
            criteria: [{ criterion: 'design_ptm', disposition: 'rejected_threshold', evidence: { value: 0.2 } }], structures: [] },
    ];
    await mount(rows);
    const projected = tree.root.findByType(CohortAnalytics).props.rows;
    expect(projected.map((row: { values: Record<string, unknown> }) => row.values)).toEqual([
        { design_ptm: 0.8, affinity_probability: 0, filter_rmsd: 1.25 },
        { design_ptm: undefined, affinity_probability: undefined, filter_rmsd: 2.5 },
        {},
    ]);
    expect(Object.keys(projected[0].values)).toEqual(['design_ptm', 'affinity_probability', 'filter_rmsd']);
    expect(scatter().props.data[0].customdata).toEqual(['selected-cif']);
    expect(scatter().props.data[0].x).toEqual([0.8]);
    expect(scatter().props.data[0].y).toEqual([0]);
    expect(text(control('Candidate data table'))).toContain('Filter rmsd (angstrom)');
    expect(tableRows()[0].findAllByType('td')[2].props.title).toBe('0 · fraction');
    expect(tableRows()[1].findAllByType('td')[1].props.title).toBe('Not reported · fraction · unavailable · missing_native_metric');
    expect(tableRows()[1].findAllByType('td')[2].props.title).toBe('Not reported · fraction · invalid · nonfinite');
    expect(text(tableRows()[1])).not.toContain('0.2');
    expect((await exported('Metrics CSV')).split('\r\n')[0]).toContain('"design_ptm","affinity_probability","filter_rmsd"');
    expect(await exported('Metrics CSV')).toContain('"Not reported"');
    expect(JSON.parse(await exported('Native JSON'))).toEqual(rows);
    await click('selected-cif');
    expect(tree.root.findByType(StructureWorkbench).props).toMatchObject({ structureUrl: '/api/files/selected-cif.cif', structureDocumentId: 'native-cif', structureContentSha256: 'c'.repeat(64), format: 'cif' });
    expect(text(control('Candidate structure inspector'))).toContain('Filter rmsd (angstrom)');
    expect(changes).not.toHaveBeenCalled();
});

it('keeps PPIFlow flat metrics unchanged even when a non-BoltzGen native block is present', async () => {
    await mount([{ candidate_key: 'ppi', metrics: { ppiflow_clash_count_ca: 0, seq_length: 31 }, native_metrics: { producer: 'ppiflow', metrics: { invented: { state: 'ok', value: 100 } } } }]);
    expect(tree.root.findByType(CohortAnalytics).props.rows[0].values).toEqual({ ppiflow_clash_count_ca: 0, seq_length: 31 });
    expect(text(control('Candidate data table'))).not.toContain('invented');
});

it('keeps no-metric historical records inspectable and exportable with no invented numeric observations', async () => {
    const rows = [{ candidate_key: 'historical-only', metrics: {}, structures: [] }]; await mount(rows);
    expect(visibleIds()).toEqual(['historical-only']); expect(text(tree.root)).toContain('No finite numeric observations');
    expect(tree.root.findAllByType(Plot)).toHaveLength(0);
    expect(JSON.parse(await exported('Native JSON'))).toEqual(rows);
    await click('historical-only'); expect(text(tree.root)).toContain('No downloadable native structure was published');
    expect(changes).not.toHaveBeenCalled();
});
