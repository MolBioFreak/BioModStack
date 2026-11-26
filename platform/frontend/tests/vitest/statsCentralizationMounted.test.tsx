import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CohortAnalytics } from '../../src/components/CohortAnalytics';
import { api } from '../../src/lib/api';
import type { CohortRow } from '../../src/lib/cohortAnalytics';
import { plots } from './analyticsPlotHarness';
vi.mock('react-plotly.js', () => import('./analyticsPlotHarness'));

const rows: CohortRow[] = [
    { id: 'c1', label: 'Candidate 1', values: { seq_length: 80, dsasa: 1200 } },
    { id: 'c2', label: 'Candidate 2', values: { seq_length: 90, dsasa: 1500 } },
    { id: 'c3', label: 'Candidate 3', values: { seq_length: 100, dsasa: 1800 } },
];

let root: ReturnType<typeof createRoot> | undefined;
let client: QueryClient;
let host: HTMLDivElement;
const originalAdapter = api.defaults.adapter;

const flush = async () => {
    for (let i = 0; i < 12; i++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    }
};

beforeEach(() => {
    sessionStorage.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = undefined;
    client?.clear();
    api.defaults.adapter = originalAdapter;
    document.body.innerHTML = '';
});

test('renders stored Toolkit SD and histogram counts, Open in Stats run_id link, stale badge, and Recompute replacement (C25-C27)', async () => {
    const postCalls: Array<{ url: string; data: any }> = [];
    let currentSummary: any = {
        run_id: 'ar-sum-1',
        status: 'completed',
        stale: true,
        params: { histogram_bins: 10, shared_bins: false },
        stats_run: { run_id: 'sr-run-old', tool_id: 'descriptive.summary' },
        result: {
            columns: {
                dsasa: { count: 3, min: 1200, max: 1800, mean: 1500, median: 1500, q1: 1350, q3: 1650, std: 312.345, histogram: { edges: [1200, 1500, 1800], counts: [1, 2] } },
                seq_length: { count: 3, min: 80, max: 100, mean: 90, median: 90, q1: 85, q3: 95, std: 10, histogram: { edges: [80, 90, 100], counts: [2, 1] } },
            },
        },
    };
    let currentCorr: any = {
        run_id: 'ar-corr-1',
        status: 'completed',
        stale: false,
        params: { method: 'pearson', p_adjust_method: 'benjamini_hochberg' },
        stats_run: { run_id: 'sr-corr-1', tool_id: 'inference.correlation_matrix' },
        result: {
            metrics: ['seq_length', 'dsasa'],
            matrix: [[1, 0.99], [0.99, 1]],
            sample_sizes: [[3, 3], [3, 3]],
            method: 'pearson',
            p_adjust_method: 'benjamini_hochberg',
            pairs: [{ var_a: 'seq_length', var_b: 'dsasa', statistic: 0.99, n: 3 }],
        },
    };
    api.defaults.adapter = async config => {
        const url = String(config.url);
        if (config.method === 'get' && url.endsWith('/analyses/job_metric_summary')) {
            return { config, status: 200, statusText: 'OK', headers: {}, data: currentSummary };
        }
        if (config.method === 'get' && url.endsWith('/analyses/job_correlation_matrix')) {
            return { config, status: 200, statusText: 'OK', headers: {}, data: currentCorr };
        }
        if (config.method === 'post' && url.endsWith('/analyses/job_metric_summary')) {
            const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
            postCalls.push({ url, data: body });
            currentSummary = {
                ...currentSummary,
                run_id: 'ar-sum-2',
                stale: false,
                params: body.params,
                stats_run: { run_id: 'sr-run-new', tool_id: 'descriptive.summary' },
                result: {
                    columns: {
                        ...currentSummary.result.columns,
                        dsasa: { count: 3, min: 1200, max: 1800, mean: 1500, median: 1500, q1: 1350, q3: 1650, std: 444.444, histogram: { edges: [1200, 1400, 1600, 1800], counts: [1, 1, 1] } },
                    },
                },
            };
            return { config, status: 200, statusText: 'OK', headers: {}, data: currentSummary };
        }
        throw new Error(`Unexpected ${config.method} ${url}`);
    };

    await act(async () => {
        root!.render(
            <QueryClientProvider client={client}>
                <CohortAnalytics jobId="job-c25" rows={rows} selectedIds={[]} mode="analytics" onInspect={() => {}} onSelect={() => {}} />
            </QueryClientProvider>,
        );
    });
    await flush();

    // C25: stored SD (312.345, not local 300) and stored histogram counts ([1, 2]) render without client-side binning
    expect(host.textContent).toContain('312.345');
    const histPlot = plots(host).find(plot => plot.data[0]?.type === 'bar' && plot.layout?.yaxis?.title?.text === 'Candidate count');
    expect(histPlot?.data[0]?.y).toEqual([1, 2]);
    expect(plots(host).some(plot => plot.data[0]?.type === 'histogram')).toBe(false);

    // C26: Open in Stats link forwards run_id
    const openLinks = [...host.querySelectorAll('a[data-open-in-stats="true"]')].map(a => a.getAttribute('href'));
    expect(openLinks).toContain('/stats?run_id=sr-run-old');
    expect(openLinks).toContain('/stats?run_id=sr-corr-1');

    // C27: stale badge shows, Recompute posts options and replaces displayed values
    expect(host.querySelector('[data-stats-stale="true"]')?.textContent).toBe('Stale');
    const summaryControls = host.querySelector('[data-stats-panel-controls="summary"]')!;
    const recomputeBtn = [...summaryControls.querySelectorAll('button')].find(b => b.textContent === 'Recompute')!;
    await act(async () => { recomputeBtn.click(); });
    const binsSelect = summaryControls.querySelector('select[aria-label="Histogram bins"]') as HTMLSelectElement;
    await act(async () => { binsSelect.value = '20'; binsSelect.dispatchEvent(new Event('change', { bubbles: true })); });
    const runBtn = [...summaryControls.querySelectorAll('button')].find(b => b.textContent === 'Run')!;
    await act(async () => { runBtn.click(); });
    await flush();

    expect(postCalls).toHaveLength(1);
    expect(postCalls[0].data).toMatchObject({ force_refresh: true, params: { include_children: true, histogram_bins: 20, shared_bins: false } });
    expect(host.textContent).toContain('444.444');
    expect(host.querySelector('[data-stats-stale="true"]')).toBeNull();
    const updatedLinks = [...host.querySelectorAll('a[data-open-in-stats="true"]')].map(a => a.getAttribute('href'));
    expect(updatedLinks).toContain('/stats?run_id=sr-run-new');
    const updatedHist = plots(host).find(plot => plot.data[0]?.type === 'bar' && plot.layout?.yaxis?.title?.text === 'Candidate count');
    expect(updatedHist?.data[0]?.y).toEqual([1, 1, 1]);
});

test('shows unavailable state without breaking scatter when Stats Toolkit is unreachable (R10)', async () => {
    api.defaults.adapter = async config => ({
        config, status: 200, statusText: 'OK', headers: {},
        data: { run_id: null, status: 'unavailable', reason: 'stats_toolkit_unavailable', result: null, stats_run: null },
    });
    await act(async () => {
        root!.render(
            <QueryClientProvider client={client}>
                <CohortAnalytics jobId="job-down" rows={rows} selectedIds={[]} mode="analytics" onInspect={() => {}} onSelect={() => {}} />
            </QueryClientProvider>,
        );
    });
    await flush();

    expect(host.querySelector('[data-stats-unavailable="true"]')?.textContent).toBe('Stats unavailable');
    expect(host.textContent).toContain('Distribution histogram unavailable');
    expect(host.textContent).toContain('Correlations unavailable');
    expect(plots(host).some(plot => plot.data[0]?.type === 'scatter')).toBe(true);
});
