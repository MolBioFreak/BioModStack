import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Plot from 'react-plotly.js';
import { BindCraft2JobResults } from '../../src/components/BindCraft2JobResults';
import { CohortAnalytics } from '../../src/components/CohortAnalytics';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
import { bc2Record, csvNumber } from '../../src/lib/bindcraft2Results';

vi.mock('react-plotly.js', () => ({ default: () => <div data-plot-renderer /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-structure-url={props.structureUrl} /> }));
let tree: ReactTestRenderer, client: QueryClient;
let reads: URL[], blobs: Map<string, Blob>;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree.root.findAllByType('button').find(node => text(node) === label)!;
const control = (label: string) => tree.root.findByProps({ 'aria-label': label });
const click = async (label: string) => { await act(async () => button(label).props.onClick()); };
const change = async (label: string, value: string | boolean) => { await act(async () => control(label).props.onChange({ target: typeof value === 'boolean' ? { checked: value } : { value } })); };
const flush = async () => { for (let i = 0; i < 14; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const tableRows = () => control('Candidate data table').findByType('tbody').findAllByType('tr');
const fixture = (count: number) => Array.from({ length: count }, (_, i) => ({ design: `exact/design-${i}`, arm: null, stage: 'trajectory',
    terminated: i % 2 ? 'anneal' : 'screen', outcome: null, sequence: null, values: { trajectory: String(i + 1), length: String(100 + i % 10), Timing: 'opaque native timing', hash: 'unchanged' },
    analytics: { duration_seconds: 90 + i, compiled: i === 0, trace_available: true,
        phase_metrics: { screen: { 'human_EGFR.iptm': { first: 0, last: i / 1000, min: 0, max: (i + 1) / 1000, samples: 50 } } } } }));
async function mount(rows = fixture(100), options: { failOffset?: number; missingTrace?: boolean; props?: Record<string, unknown>; traceTotal?: number } = {}) {
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input, 'http://test'); reads.push(url);
        if (url.pathname.endsWith('/trajectory')) {
            expect(init?.signal).toBeInstanceOf(AbortSignal);
            const offset = Number(url.searchParams.get('offset')), total = options.traceTotal ?? 120;
            const trace = Array.from({ length: total }, (_, i) => ({ phase: i < 50 ? 'screen' : 'anneal', round: i < 50 ? i : i - 50, 'human_EGFR.iptm': i / 1000, 'human_EGFR.interface_pae': 1 - i / 2000 }));
            return { ok: true, json: async () => ({ design: url.searchParams.get('design'), arm: null, offset, limit: 1000, total, rows: options.missingTrace ? [] : trace.slice(offset, offset + 1000), available: !options.missingTrace, warnings: [] }) };
        }
        if (!url.pathname.endsWith('/bindcraft2-results')) throw new Error(`Unexpected request ${input}`);
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        const offset = Number(url.searchParams.get('offset')), limit = Number(url.searchParams.get('limit'));
        expect(limit).toBe(100);
        if (offset === options.failOffset) return { ok: false, status: 503 };
        return { ok: true, json: async () => ({ schema: 'bindcraft2.native-readback.v1', arm: url.searchParams.get('arm'), stage: url.searchParams.get('stage'), offset, limit, total: rows.length,
            accounting: { emitted_trajectories: rows.length, scored_draws: 0, retained_sequences: 0 }, arms: [{ name: null }, { name: 'alternate' }], metadata: {}, artifacts: [], rows: rows.slice(offset, offset + limit),
            analytics: { trajectory_count: rows.length, termination_counts: {}, timing_seconds: { samples: rows.length, median: 90, total: 9000 }, complete: true, warnings: [] } }) };
    }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><BindCraft2JobResults jobId="bc2-job" {...options.props} /></QueryClientProvider>); });
    await flush();
}
async function exported() {
    const link = tree.root.findAllByType('a').find(node => text(node) === 'Native JSON')!;
    return new Promise<any[]>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(JSON.parse(String(reader.result))); reader.onerror = reject; reader.readAsText(blobs.get(link.props.href)!); });
}
beforeEach(() => {
    reads = []; blobs = new Map();
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const url = `blob:test-${blobs.size}`; blobs.set(url, blob as Blob); return url; });
});
afterEach(async () => { await act(async () => tree?.unmount()); client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('lands a 100-trajectory zero-yield result on shared plots, not settings or empty downstream workspaces', async () => {
    await mount();
    expect(tree.root.findByType(CohortAnalytics).props.rows).toHaveLength(100);
    expect(tableRows()).toHaveLength(25);
    expect(tree.root.findAllByType(Plot).length).toBeGreaterThan(1);
    expect(text(tree.toJSON())).toContain('0 retained sequences');
    expect(text(tree.toJSON())).not.toContain('Final i_pTM');
    expect(reads).toHaveLength(1);
    expect(tree.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    const summary = tree.root.findAllByType('summary').find(node => text(node) === 'Native campaign settings')!;
    expect(summary.parent?.props.open).not.toBe(true);
    expect(control('Sort by screen · human_EGFR.iptm · last recorded')).toBeTruthy();
});
it('prefers exact recorded iPTM over coldspot and iptm_loss, retaining operator choices', async () => {
    const rows: any[] = fixture(2);
    for (const row of rows) {
        row.analytics.phase_metrics.screen['human_EGFR.iptm_loss'] = { last: 9 };
        row.analytics.phase_metrics.screen['coldspot'] = { last: 99, max: 100 };
        row.analytics.phase_metrics.refine = { 'human_EGFR.iptm': { last: 0.72 }, 'human_EGFR.iptm_loss': { last: 8 } };
    }
    await mount(rows);
    const screen = 'screen · human_EGFR.iptm · last recorded';
    const refine = 'refine · human_EGFR.iptm · last recorded';
    expect(control('X metric').props.value).toBe(screen);
    expect(control('Y metric').props.value).toBe(refine);
    expect(control('Distribution metric').props.value).toBe(refine);
    expect(text(control('Cohort summary'))).toContain('Median Refine · human EGFR.iptm · last recorded');
    expect(text(control('Cohort summary'))).toContain('0.72');
    expect(text(control('Cohort summary'))).not.toContain('coldspot');
    await change('X metric', 'seq_length');
    await change('Distribution metric', screen);
    await change('Search candidates', 'nothing matches');
    await change('Search candidates', '');
    await click('Trajectory 1'); await flush(); await click('Dashboard');
    expect(control('X metric').props.value).toBe('seq_length');
    expect(control('Y metric').props.value).toBe(refine);
    expect(control('Distribution metric').props.value).toBe(screen);
});
it('fills three pages; table, plots, outcomes, cross-page selection and native JSON use one filtered cohort', async () => {
    const rows = fixture(205); await mount(rows);
    expect(reads.map(url => url.searchParams.get('offset'))).toEqual(['0', '100', '200']);
    expect(tree.root.findByType(CohortAnalytics).props.rows).toHaveLength(205);
    await click('Select all matching (205)');
    await click('Next native records'); await change('Select this page', false);
    await change('Export scope', 'selected'); expect(await exported()).toHaveLength(180);
    await change('Search candidates', 'exact/design-204');
    expect(tableRows()).toHaveLength(1);
    expect(tree.root.findByType(CohortAnalytics).props.rows).toHaveLength(1);
    expect(text(control('Native outcomes in view'))).toContain('screen: 1');
    await change('Export scope', 'filtered'); expect(await exported()).toEqual([rows[204]]);
    expect(reads).toHaveLength(3);
});
it('preserves axes/search/selection when a plotted trajectory opens its exact paginated phase trace and returns', async () => {
    await mount(fixture(205), { traceTotal: 1005 });
    await change('Search candidates', 'exact/design-204'); await click('Select all matching (1)');
    await change('Y metric', 'screen · human_EGFR.iptm · peak recorded');
    const scatter = tree.root.findAllByType(Plot).find(node => node.props.data[0]?.type === 'scatter')!;
    await act(async () => scatter.props.onClick({ points: [{ customdata: scatter.props.data[0].customdata[0] }] })); await flush();
    expect(reads.filter(url => url.pathname.endsWith('/trajectory')).map(url => [url.searchParams.get('design'), url.searchParams.get('offset')])).toEqual([['exact/design-204', '0'], ['exact/design-204', '1000']]);
    expect(text(control('Native trajectory detail'))).toContain('1005 of 1005 recorded updates');
    expect(text(control('Native trajectory detail'))).toContain('not the final prediction');
    await click('Dashboard');
    expect(control('Search candidates').props.value).toBe('exact/design-204');
    expect(control('Y metric').props.value).toBe('screen · human_EGFR.iptm · peak recorded');
    expect(control('Select Trajectory 205').props.checked).toBe(true);
});
it('retains earlier pages and labels incomplete read statistics/exports honestly', async () => {
    await mount(fixture(205), { failOffset: 100 });
    expect(tree.root.findByType(CohortAnalytics).props.rows).toHaveLength(100);
    expect(text(tree.toJSON())).toContain('partially loaded');
    expect(text(tree.toJSON())).toContain('100 of 205 loaded');
    expect(await exported()).toHaveLength(100);
});
it('keeps missing traces and unknown numeric observations missing', async () => {
    const rows = fixture(1); rows[0].analytics.trace_available = false;
    await mount(rows); await click('Trajectory 1'); await flush();
    expect(text(control('Native trajectory detail'))).toContain('No recorded loss trace');
    expect(reads).toHaveLength(1);
    expect(csvNumber('')).toBeUndefined(); expect(csvNumber(false)).toBeUndefined(); expect(csvNumber('0')).toBe(0);
    const record = bc2Record({ design: 'x', values: { length: '', score: 'not numeric' } }, { arm: null, stage: 'trajectory' });
    expect(record.design_id).toBeUndefined(); expect(record.structures).toEqual([]);
    expect((record.metrics as any).seq_length).toBeUndefined();
});
it('retained outputs use the full shared workbench and exact alternate document, not invented primary coordinates', async () => {
    const rows: any[] = [{ design: 'accepted', stage: 'retained', design_id: 'real-design', sequence: 'ACDE', values: { i_pTM: '0.81' },
        structures: [{ artifact_id: 'primary', target_state: 'A', logical_path: 'a.pdb', download_url: '/exact/a.pdb', primary: true },
            { artifact_id: 'alternate', target_state: 'B', logical_path: 'b.cif', download_url: '/exact/b.cif' }] }];
    await mount(rows, { props: { selectedDesignId: 'real-design', artifactId: 'alternate', targetState: 'B' } });
    expect(reads[0].searchParams.get('stage')).toBe('retained');
    const viewer = tree.root.findByType(StructureWorkbench);
    expect(viewer.props.structureUrl).toBe('/exact/b.cif'); expect(viewer.props.format).toBe('cif');
    expect(text(control('Native trajectory detail'))).toContain('ACDE');
    expect(reads.some(url => url.pathname.endsWith('/trajectory'))).toBe(false);
});
it('native arm and stage remain distinct transport and selection scopes', async () => {
    await mount(fixture(2)); await click('Select all matching (2)');
    await change('Campaign arm', 'alternate'); await flush();
    expect(reads.at(-1)?.searchParams.get('arm')).toBe('alternate');
    expect(control('Select Trajectory 1').props.checked).toBe(false);
    await change('Native records', 'draw'); await flush();
    expect(reads.at(-1)?.searchParams.get('stage')).toBe('draw');
});
