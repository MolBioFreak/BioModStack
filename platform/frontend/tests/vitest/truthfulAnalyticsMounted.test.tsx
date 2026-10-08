import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, test, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type Design } from '../../src/lib/api';
import { AnalyticsDashboard } from '../../src/components/AnalyticsDashboard';
import { parseScientificPoint } from '../../src/lib/scientificAnalytics';
import { change, plots, scatter, settled } from './analyticsPlotHarness';
vi.mock('react-plotly.js', () => import('./analyticsPlotHarness'));

const descriptor = (key: string) => ({ metric_id: key, source: 'canonical_artifact', producer_version: 'ui-fixture-v1', derivation_version: 'ui-fixture-v1', scope: 'overall', unit: key === 'plddt_overall' ? 'pLDDT' : key === 'ptm' ? 'dimensionless' : 'angstrom', direction: 'none' });
const row = (id: string, rmsd: number | null) => parseScientificPoint({
    id, name: `Prediction ${id}`, contract_revision: 1, source_job_id: 'j', cohort_key: 'v1:p:j',
    metrics: { plddt_overall: 0, pae_overall: 2, ...(rmsd === null ? {} : { rmsd_overall: rmsd }) },
    metric_states: { plddt_overall: { state: 'ok', value: 0, reason_code: null }, pae_overall: { state: 'ok', value: 2, reason_code: null }, rmsd_overall: rmsd === null ? { state: 'unavailable', value: null, reason_code: 'not_reported' } : { state: 'ok', value: rmsd, reason_code: null } },
    metric_sources: Object.fromEntries(['plddt_overall', 'pae_overall', 'rmsd_overall'].map(key => [key, { artifact_sha256: 'a'.repeat(64), candidate_id: id, document_id: id }])),
    metric_descriptors: Object.fromEntries(['plddt_overall', 'pae_overall', 'rmsd_overall'].map(key => [key, descriptor(key)])),
});
let root: ReturnType<typeof createRoot> | undefined;
let client: QueryClient;
const adapter = api.defaults.adapter;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.innerHTML = ''; api.defaults.adapter = adapter; });
async function mount(points: any[], designs = points, extra = {}, loadedDesignCount?: number) {
    const requests: string[] = [];
    api.defaults.adapter = async config => {
        requests.push(config.url!);
        const data = config.url!.endsWith('/plotly-metrics')
            ? { job_id: 'j', metric_keys: [...new Set(points.flatMap(point => Object.keys(point.metrics)))], points, total: points.length, scientific_cohorts: [], ...extra }
            : { metrics: {}, available_analyses: [], analyses: [] };
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><AnalyticsDashboard designs={designs as Design[]} jobId="j" jobName="Fixture predictions" loadedDesignCount={loadedDesignCount} /></QueryClientProvider>));
    await settled(host);
    return { host, requests };
}

test('canonical observations use real Plotly Lab, histogram and heatmap with zero and missing values intact', async () => {
    const points = [row('missing', null), row('zero', 0), row('five', 5)];
    const { host } = await mount(points);
    expect(host.querySelector('[aria-label="Plotly Lab"]')).not.toBeNull();
    expect(scatter(host).data[0].x).toEqual([0, 0, 0]);
    expect(scatter(host).data[0].y).toEqual([2, 2, 2]);
    expect(plots(host).some(plot => plot.data[0].type === 'histogram')).toBe(true);
    expect(plots(host).some(plot => plot.data[0].type === 'heatmap')).toBe(true);
    expect(host.textContent).toContain('1 of 3 records have unavailable measurements');
    await change(host, '2D Y metric', 'rmsd_overall');
    expect(scatter(host).data[0].y).toEqual([0, 5]);
    expect(scatter(host).data[0].customdata).toEqual(['zero', 'five']);
    expect(host.textContent).toContain('2 plotted · 1 omitted');
    await change(host, 'Plotly Lab palette', 'Cividis');
    expect(scatter(host).data[0].marker.colorscale).toBe('Cividis');
    await change(host, 'Scatter drag mode', 'lasso');
    expect(scatter(host).layout.dragmode).toBe('lasso');
    await change(host, 'Plotly Lab view', '3D');
    expect(plots(host).find(plot => plot.data[0].type === 'scatter3d').data[0].z).toEqual([2, 2]);
    await change(host, 'Plotly Lab view', '2D');
    expect((host.querySelector('[aria-label="2D Y metric"]') as HTMLSelectElement).value).toBe('rmsd_overall');
    await change(host, 'Sort measurements', 'rmsd_overall');
    const detail = host.querySelector('[aria-label="Sort measurements"]')!.closest('details')!;
    expect(detail.hasAttribute('open')).toBe(false);
    const values = [...detail.querySelectorAll('tr')].filter(tr => tr.children[1]?.textContent?.startsWith('rmsd_overall /')).map(tr => tr.children[2].textContent);
    expect(values).toEqual(['0', '5', 'unavailable: not_reported']);
    expect(host.textContent).not.toContain('v1:p:j');
    expect(host.textContent).not.toContain('a'.repeat(64));
    await act(async () => (host.querySelector('[data-plot]') as HTMLElement).click());
    expect(detail.open).toBe(true);
    expect(detail.textContent).toContain('Prediction zero');
    expect(detail.textContent).not.toContain('Prediction missing');
    expect((host.querySelector('[aria-label="2D Y metric"]') as HTMLSelectElement).value).toBe('rmsd_overall');
});

test('Protenix-shaped eight predictions plot canonical native pLDDT and pTM, never generic Design values', async () => {
    // Explicit UI-only fixture matching the canonical adapter; not live scientific results.
    const points = Array.from({ length: 8 }, (_, i) => {
        const point = row(`sample-${i}`, null);
        const metrics = { plddt_overall: 72.123456 + i, ptm: i / 10 };
        return parseScientificPoint({ ...point, metrics,
            metric_states: Object.fromEntries(Object.entries(metrics).map(([key, value]) => [key, { state: 'ok', value, reason_code: null }])),
            metric_descriptors: Object.fromEntries(Object.keys(metrics).map(key => [key, descriptor(key)])),
            metric_sources: Object.fromEntries(Object.keys(metrics).map(key => [key, point.metric_sources.plddt_overall])),
        });
    });
    const { host, requests } = await mount(points, points.map(point => ({ ...point, plddt_overall: 999, ptm: 999 })));
    expect(requests).toEqual(['/api/designs/by-job/j/plotly-metrics']);
    expect(scatter(host).data[0].x).toEqual(points.map(point => point.metrics.plddt_overall));
    expect(scatter(host).data[0].y).toEqual(points.map(point => point.metrics.ptm));
    expect(scatter(host).layout.xaxis.title.text).toContain('(pLDDT)');
    expect(scatter(host).layout.yaxis.title.text).toContain('(dimensionless)');
    expect(scatter(host).config.toImageButtonOptions.format).toBe('svg');
    expect(host.textContent).not.toContain('999');
});

test('canonical fractions and incompatible cohorts remain separate and unscaled', async () => {
    const points = [row('fraction', 0), row('percent', 5)];
    points[0].metrics.plddt_overall = 0.73;
    points[0].metric_states.plddt_overall = { state: 'ok', value: 0.73, reason_code: null };
    points[0].metric_descriptors.plddt_overall.unit = 'fraction';
    points[1].cohort_key = 'v1:other:j';
    const { host } = await mount(points);
    const charts = [...host.querySelectorAll('section[aria-label^="Recorded measurements"]')];
    expect(charts).toHaveLength(2);
    expect(scatter(charts[0]).data[0].x).toEqual([0.73]);
    expect(scatter(charts[0]).layout.xaxis.title.text).toContain('(fraction)');
    expect(scatter(charts[1]).data[0].x).toEqual([0]);
});

test('missing publication is a useful empty chart state, not invented confidence or a diagnostic landing table', async () => {
    const point = { ...row('missing', null), metrics: {}, metric_states: {}, metric_descriptors: {}, metric_sources: {}, publication_state: { state: 'unavailable', value: null, reason_code: 'missing_canonical_publication' } };
    const { host } = await mount([point], [{ ...point, plddt_overall: 90 }]);
    expect(host.textContent).toContain('No finite numeric observations');
    expect(plots(host)).toHaveLength(0);
    const reason = [...host.querySelectorAll('td')].find(td => td.textContent === 'missing_canonical_publication');
    expect(reason?.closest('details')?.open).toBe(false);
    expect(host.querySelector('h2')?.textContent).not.toContain('v1:');
});

test('mixed ordinary and canonical results retain separate rich charts without alias fallback', async () => {
    const canonical = row('canonical', 0);
    const legacy = { id: 'legacy', name: 'Ordinary prediction', metrics: { plddt_overall: 88, pae_overall: 5 } };
    const { host } = await mount([canonical, legacy], [canonical, { ...legacy, plddt_overall: 88, pae_overall: 5, analysis_contract_id: 'structure_prediction_v1', viewer_capabilities: ['global_confidence'] }]);
    const native = host.querySelector('[aria-label="Scientific result analytics"]')!;
    expect(scatter(native).data[0].x).toEqual([0]);
    expect(scatter(native).data[0].customdata).toEqual(['canonical']);
    const ordinary = host.querySelector('[aria-label="Historical analytics"]')!;
    expect(ordinary).not.toBeNull();
    expect(plots(ordinary).length).toBeGreaterThan(0);
    expect(native.textContent).not.toContain('Ordinary prediction');
});

test('sampled canonical charts disclose their loaded-result scope', async () => {
    const points = [row('sample', 0)];
    const { host } = await mount(points, points, {}, 8);
    expect(host.textContent).toContain('Charts show 1 of 8 loaded results');
    expect(scatter(host).data[0].customdata).toEqual(['sample']);
});

test('ordinary results retain the existing rich dashboard and configurable Plotly Lab', async () => {
    const designs = [{ id: 'legacy', name: 'Ordinary prediction', plddt_overall: 80, pae_overall: 2, ptm: 0.8,
        viewer_capabilities: ['global_confidence', 'complex_interface_metrics'], analysis_contract_id: 'structure_prediction_v1' }];
    const { host } = await mount([{ id: 'legacy', name: 'Ordinary prediction', metrics: { plddt_overall: 80, pae_overall: 2, ptm: 0.8 } }], designs);
    expect(plots(host).length).toBeGreaterThan(0);
    const button = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('Plotly Lab'));
    expect(button).toBeDefined();
    await act(async () => button!.click());
    expect(host.querySelector('[aria-label="Plotly Lab"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Plotly Lab view"]')).not.toBeNull();
});
