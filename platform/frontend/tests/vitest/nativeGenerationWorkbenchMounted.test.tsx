import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
import Plot from 'react-plotly.js';
import { api } from '../../src/lib/api';
import type { NativeGenerationRecord } from '../../src/lib/nativeBinderResults';
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
