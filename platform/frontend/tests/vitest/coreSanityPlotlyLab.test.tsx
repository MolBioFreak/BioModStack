import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, test, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CohortAnalytics } from '../../src/components/CohortAnalytics';
import { AnalyticsDashboard } from '../../src/components/AnalyticsDashboard';
import type { CohortRow } from '../../src/lib/cohortAnalytics';

const plots = vi.hoisted(() => new Map<string, any>());
vi.mock('react-plotly.js', () => ({ default: (props: any) => {
    const key = props.data?.[0]?.type ?? 'empty'; plots.set(key, props);
    return <div data-plot={key} />;
} }));
Object.assign(globalThis, { React, ResizeObserver: class { observe() {} disconnect() {} } });
Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ addEventListener() {}, removeEventListener() {} }) });
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.innerHTML = ''; plots.clear(); });
const rows: CohortRow[] = [
    { id: 'native:a', label: 'Candidate A', values: { raw_x: 0, raw_y: 2, raw_z: 4, raw_color: null } },
    { id: 'native:b', label: 'Candidate B', values: { raw_x: 1, raw_y: 3, raw_z: 5, raw_color: 8 } },
    { id: 'native:c', label: 'Candidate C', values: { raw_x: null, raw_y: 4, raw_z: 6 } },
];
const inspect = vi.fn(), select = vi.fn();
const props = { rows, selectedIds: [], onInspect: inspect, onSelect: select,
    initialMetrics: { x: 'raw_x', y: 'raw_y', distribution: 'raw_y' },
    getMetricLabel: (key: string) => ({ raw_x: 'Length', raw_y: 'Screen score · last update', raw_z: 'Other reading', raw_color: 'Color reading' })[key] ?? key,
    getMetricDescription: () => 'Screen phase, last recorded optimizer update; not a verdict.',
};
async function mount(element: React.ReactNode) { host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); await act(async () => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); }
async function change(label: string, value: string) { await act(async () => { const el = host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement; expect(el).toBeTruthy(); el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); }); }

test('curated dashboard explains readings and histogram renders stored bins with no floating box or secondary axis', async () => {
    const storedCohort = {
        cohort_key: 'v1:test:j', design_ids: ['native:a', 'native:b', 'native:c'], pairs: {},
        metrics: {
            raw_y: {
                observed_count: 3, unavailable_count: 0, invalid_count: 0, reason_code: null,
                descriptor: { metric_id: 'raw_y', source: 'retained_native_artifact' as const, scope: 'overall', unit: 'score', direction: 'higher' as const, producer_version: 'v1', derivation_version: 'v1' },
                statistics: { min: 2, max: 4, avg: 3, median: 3, std_dev: 1, q1: 2.5, q3: 3.5, iqr: 1, histogram_bins: [2, 3, 4], histogram_counts: [1, 2] },
            },
        },
    };
    await mount(<CohortAnalytics {...props} storedCohort={storedCohort} mode="dashboard" />);
    expect(host.textContent).toContain('Each bin counts candidates');
    expect(host.textContent).toContain('last recorded optimizer update');
    expect(host.querySelector('select[aria-label="X metric"]')).toBeNull();
    const histogram = plots.get('bar');
    expect(histogram.data.map((trace: any) => trace.type)).toEqual(['bar']);
    expect(histogram.data[0].y).toEqual([1, 2]);
    expect(histogram.layout.yaxis2).toBeUndefined();
    expect(histogram.layout.xaxis.title.text).toBe('Screen score · last update');
    expect(plots.get('scatter').data[0].hovertemplate).not.toContain('raw_');
});

test('lab reaches 2D and 3D, palette, finite coordinates, missing color and exact callbacks', async () => {
    await mount(<CohortAnalytics {...props} mode="analytics" />);
    expect(host.querySelector('[aria-label="Custom charts"]')).toBeTruthy();
    await change('2D color metric', 'raw_color');
    await change('Custom chart palette', 'Plasma');
    const scatter = plots.get('scatter');
    expect(scatter.data[0].marker.colorscale).toBe('Plasma');
    expect(scatter.data[1].customdata).toEqual(['native:a']);
    expect(scatter.data[1].marker.color).toBe('#94a3b8');
    scatter.onClick({ points: [{ customdata: 'native:a' }] });
    scatter.onSelected({ points: [{ customdata: 'native:b' }, { customdata: 'native:b' }, { customdata: 'foreign' }] });
    expect(inspect).toHaveBeenCalledWith('native:a'); expect(select).toHaveBeenCalledWith(['native:b']);
    await change('Custom chart view', '3D');
    await change('3D Z metric', 'raw_z');
    expect(plots.get('scatter3d').data[0].customdata).toEqual(['native:a', 'native:b']);
    expect(plots.get('scatter3d').config.toImageButtonOptions.format).toBe('svg');
    await change('Custom chart view', '2D');
    await change('2D X metric', 'raw_z');
    await act(async () => root.render(<QueryClientProvider client={client}><CohortAnalytics {...props} rows={rows.filter(row => row.id === 'native:c')} mode="analytics" /></QueryClientProvider>));
    await act(async () => root.render(<QueryClientProvider client={client}><CohortAnalytics {...props} mode="analytics" /></QueryClientProvider>));
    expect((host.querySelector('select[aria-label="2D X metric"]') as HTMLSelectElement).value).toBe('raw_z');
});

test('consumer picker extension receives controlled native keys without translating identity', async () => {
    const picker = vi.fn(({ label, keys, value, onChange, allowEmpty }) => <label>{label}<select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>
        {allowEmpty && <option value="">None</option>}{keys.map((key: string) => <option key={key} value={key}>{props.getMetricLabel(key)}</option>)}
    </select></label>);
    await mount(<CohortAnalytics {...props} mode="analytics" renderMetricPicker={picker} />);
    await change('2D X metric', 'raw_z');
    expect(plots.get('scatter').layout.xaxis.title.text).toBe('Other reading');
    expect(picker.mock.calls.some(([parameters]) => parameters.value === 'raw_z' && parameters.keys.includes('raw_x'))).toBe(true);
});

test('original dashboard explicitly opens the extracted lab without advanced charts', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await mount(<QueryClientProvider client={client}><AnalyticsDashboard designs={[{ id: 'legacy-ui-fixture', name: 'Legacy UI fixture', plddt_overall: 80, pae_overall: 4, iptm: 0.6 } as import('../../src/lib/api').Design]} /></QueryClientProvider>);
    const button = Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Open custom charts');
    expect(button).toBeTruthy();
    await act(async () => button!.click());
    expect(host.querySelector('[aria-label="Custom charts"]')).toBeTruthy();
    expect(host.textContent).toContain('Show Advanced Charts');
    await change('Custom chart view', '3D');
    expect(host.querySelector('select[aria-label="3D Z metric"]')).toBeTruthy();
});
