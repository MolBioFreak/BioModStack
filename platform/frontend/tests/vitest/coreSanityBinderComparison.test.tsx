import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BinderResultComparison } from '../../src/components/BinderResultComparison';
import { api } from '../../src/lib/api';

vi.mock('../../src/components/MetricCharts', () => ({ DesignMultiLineChart: (props: unknown) => <pre data-chart>{JSON.stringify(props)}</pre> }));
vi.mock('../../src/components/CohortAnalytics', () => ({ CohortAnalytics: () => <div>Shared plot boundary</div> }));
const oldAdapter = api.defaults.adapter;
let root: Root | undefined;
let client: QueryClient;
let container: HTMLDivElement;
const requests: Array<{ url?: string; data: unknown; params: unknown }> = [];
const models = ['bindcraft2', 'boltzgen', 'ppiflow', 'rfantibody', 'protenix'];
const point = (job: string) => ({
    id: `${job}-design`, name: 'Same name', contract_revision: 1, source_job_id: job, cohort_key: `v1:${job}:${job}`,
    metrics: job === 'ppiflow' ? {} : { plddt: 0.8 },
    metric_states: { plddt: job === 'ppiflow' ? { state: 'unavailable', value: null, reason_code: 'not_reported' } : { state: 'ok', value: 0.8, reason_code: null } },
    metric_descriptors: { plddt: { metric_id: 'plddt', source: 'canonical_artifact', scope: 'complex', unit: 'fraction', direction: 'higher', producer_version: job, derivation_version: 'native-v1' } },
    metric_sources: { plddt: { artifact_sha256: 'a'.repeat(64), candidate_id: `${job}-candidate`, document_id: `${job}-artifact` } },
});
async function settle() { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function click(text: string) { await act(async () => { const el = [...container.querySelectorAll('button, span')].find(el => el.textContent === text) as HTMLElement; expect(el).toBeTruthy(); el.click(); }); await settle(); }
async function mount(props: React.ComponentProps<typeof BinderResultComparison>) {
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><BinderResultComparison {...props} /></QueryClientProvider>)); await settle();
}
function fixture() {
    requests.length = 0; localStorage.clear();
    api.defaults.adapter = async config => {
        requests.push({ url: config.url, data: config.data ? JSON.parse(config.data) : undefined, params: config.params });
        let data: unknown;
        if (config.url === '/api/jobs') data = { jobs: models.map(id => ({ id, name: id, model_id: id, mode: 'protein_binder', created_at: '2026-01-01' })), total: 5 };
        else if (config.url === '/api/analytics/batch') data = { job_ids: JSON.parse(config.data), metrics_summary: {}, common_metrics: [], scientific_cohorts: [] };
        else if (config.url?.startsWith('/api/analytics/job/')) data = [point(config.url.split('/')[4])];
        else if (config.url?.startsWith('/api/jobs/bc2-')) data = { id: config.url.split('/').pop(), model_id: 'bindcraft2', mode: 'campaign' };
        else if (config.url === '/api/jobs/boltzgen') data = { id: 'boltzgen', model_id: 'boltzgen', mode: 'protein_binder' };
        else if (config.url === '/api/jobs/boltzgen/generation-results') data = { records: [{ candidate_key: 'observation-only', metrics: { native_score: null }, structures: [] }], total: 1, offset: 0, limit: 1000, receipt: {}, publication: {} };
        else if (config.url === '/api/designs') { const job = config.params.job_id; data = { designs: [{ id: `${job}-design`, job_id: job, name: 'Same name', plddt_overall: null }], total: 1 }; }
        else if (config.url?.endsWith('/residue-metrics')) data = { residue_numbers: [10, 12], plddt: [88, null], length: 2 };
        else throw new Error(`Unexpected transport ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); container?.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

it('demand-mounts real batch comparison for all native families and explicit prediction descendants', async () => {
    fixture(); await mount({ jobId: 'bindcraft2', jobIds: models, launchContextId: 'launch' });
    expect(requests).toEqual([]);
    await click('Compare result sets');
    await vi.waitFor(async () => { await settle(); expect(requests.find(r => r.url === '/api/analytics/batch')?.data).toEqual(models); });
    expect(requests.filter(r => r.url?.startsWith('/api/analytics/job/')).every(r => (r.params as {include_children: boolean}).include_children === false)).toBe(true);
    expect(requests.find(r => r.url === '/api/jobs')?.params).toMatchObject({ include_children: true, limit: 500 });
    for (const job of models) expect(container.textContent).toContain(`v1:${job}:${job}`);
    expect(container.textContent).toContain('unavailable: not_reported');
    expect(container.textContent).toContain('a'.repeat(64));
    expect(container.textContent).not.toContain('Success Rate');
    await click('Native observations · boltzgen');
    expect(requests.find(r => r.url === '/api/jobs/boltzgen/generation-results')?.params).toEqual({ offset: 0, limit: 1000 });
    expect(container.textContent).toContain('observation-only');
    expect(requests.some(r => r.url?.includes('observation-only/residue-metrics'))).toBe(false);
    await click('Return to native results');
    expect(container.textContent).not.toContain('Scientific result analytics');
    expect(JSON.parse(localStorage.getItem('binder-result-comparison:launch:bindcraft2')!).jobs).toEqual(models);
});

it('compares two zero-retained BC2 campaigns through their native stage selector', async () => {
    fixture();
    const reads: URL[] = [];
    vi.stubGlobal('fetch', vi.fn(async (raw: string) => {
        const url = new URL(raw, 'http://localhost'); reads.push(url);
        const stage = url.searchParams.get('stage');
        return { ok: true, json: async () => ({ arm: null, stage, offset: 0, limit: 100, total: 1,
            arms: [{ name: null }], metadata: {}, artifacts: [],
            accounting: { emitted_trajectories: 1, scored_draws: 1, retained_sequences: 0 },
            rows: [{ design: 'rejected-native', terminated: 'anneal', outcome: 'rejected', values: { trajectory: '1', i_pTM: '0.2' } }],
        }) };
    }));
    await mount({ jobId: 'bc2-one', jobIds: ['bc2-one', 'bc2-two'] });
    await click('Compare result sets');
    await vi.waitFor(async () => { await settle(); expect(container.textContent).toContain('Native observations · bc2-one'); });
    for (const id of ['bc2-one', 'bc2-two']) await click(`Native observations · ${id}`);
    await vi.waitFor(async () => { await settle(); expect(container.querySelectorAll('select[aria-label="Native records"]')).toHaveLength(2); });
    const selectors = [...container.querySelectorAll<HTMLSelectElement>('select[aria-label="Native records"]')];
    for (const selector of selectors) await act(async () => { selector.value = 'draw'; selector.dispatchEvent(new Event('change', { bubbles: true })); });
    await vi.waitFor(async () => { await settle(); expect(reads.filter(url => url.searchParams.get('stage') === 'draw')).toHaveLength(2); });
    expect(reads.filter(url => url.searchParams.get('stage') === 'trajectory')).toHaveLength(2);
    await vi.waitFor(async () => { await settle(); expect([...container.querySelectorAll('button')].filter(button => button.textContent === 'Trajectory 1')).toHaveLength(2); });
});

it('reuses actual design comparison with exact IDs, explicit descendant scope, missing values and fresh reopen', async () => {
    fixture(); await mount({ jobId: 'boltzgen', jobIds: ['boltzgen', 'protenix'], selectedDesignIds: ['boltzgen-design', 'protenix-design', 'observation-only'] });
    await click('Compare result sets'); await click('Candidate confidence');
    expect(requests.filter(r => r.url === '/api/designs').map(r => r.params)).toEqual([
        { job_id: 'boltzgen', include_children: false, limit: 100, offset: 0, include_summary: false },
        { job_id: 'protenix', include_children: false, limit: 100, offset: 0, include_summary: false },
    ]);
    expect(requests.filter(r => r.url?.endsWith('/residue-metrics')).map(r => r.url)).toEqual(['/api/designs/boltzgen-design/residue-metrics', '/api/designs/protenix-design/residue-metrics']);
    const chart = JSON.parse(container.querySelector('[data-chart]')!.textContent!);
    expect(chart.data).toEqual([{ residue: 10, 'Same name (boltzgen-design)': 88, 'Same name (protenix-design)': 88 }, { residue: 12 }]);
    await act(async () => root!.unmount()); root = undefined; client.clear(); container.remove(); requests.length = 0;
    await mount({ jobId: 'boltzgen' }); expect(requests).toEqual([]);
    await click('Compare result sets');
    expect(container.textContent).toContain('Confidence Overlay');
    expect(requests.filter(r => r.url === '/api/designs').map(r => (r.params as {job_id: string}).job_id)).toEqual(['boltzgen', 'protenix']);
});
