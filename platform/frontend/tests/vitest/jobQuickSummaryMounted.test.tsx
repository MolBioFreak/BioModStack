import React from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import { api, type Job } from '../../src/lib/api';

let root: Root | undefined;
let host: HTMLDivElement;
let client: QueryClient;
const base = { id: 'summary-job', name: 'Summary job', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed', design_count: 7, created_at: '2026-01-01T00:00:00Z', params: {} } as Job;
const close = vi.fn();
async function mount(job: Job) {
    host = document.createElement('div'); document.body.append(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Summary must not load candidate data'))));
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Summary must not load candidate data'));
    await act(async () => { root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/?launch_context_id=destination%2Fone&design_id=unrelated']}>
        <table><tbody><JobDetailsPanel job={job} onClose={close} /></tbody></table>
    </MemoryRouter></QueryClientProvider>); });
}
afterEach(async () => {
    await act(async () => root?.unmount()); client?.clear(); host?.remove(); root = undefined;
    vi.restoreAllMocks(); vi.unstubAllGlobals(); close.mockClear();
});

const models = ['ppiflow', 'boltzgen', 'bindcraft2', 'esmfold2', 'antibody_denovo', 'docking', 'molecular_dynamics', 'frustrampnn', 'future_model'];
const statuses = ['completed', 'running', 'queued', 'awaiting_input', 'failed', 'cancelled'] as const;
it.each(models.flatMap(model => statuses.map(status => [model, status] as const)))('%s / %s stays a job-only summary without candidate requests', async (model_id, status) => {
    await mount({ ...base, model_id, status });
    const panel = host.querySelector('section[aria-label="Job quick summary"]')!;
    expect(panel.textContent).toContain('Summary job');
    expect(panel.textContent).toContain(status);
    expect(panel.querySelector('canvas, svg, table, select, input, details')).toBeNull();
    expect(panel.textContent).not.toMatch(/Selected candidate|Generation dashboard|Sequence and prediction evidence|Native campaign settings|No structure files|will be available when completed/);
    expect(fetch).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
    expect(panel.querySelector('a[href^="/designs/"]')?.getAttribute('href')).toBe('/designs/summary-job?launch_context_id=destination%2Fone');
});
it('keeps requested and stored quantities distinct and closes through the owner', async () => {
    await mount({ ...base, requested_design_count: 100, design_count: 0 });
    const values = Object.fromEntries([...host.querySelectorAll('dl > div')].map(el => [el.querySelector('dt')!.textContent, el.querySelector('dd')!.textContent]));
    expect(values).toMatchObject({ Requested: '100', 'Stored designs': '0' });
    await act(async () => (host.querySelector('button[aria-label="Close job summary"]') as HTMLButtonElement).click());
    expect(close).toHaveBeenCalledOnce();
});
it('shows only publication totals, preserving zero and unknown without per-candidate details', async () => {
    await mount({ ...base, result_summary: { state: 'validated', partial: false, requested_count: null, generated_count: 1000, rejected_count: 0, failed_count: null, unevaluable_count: 2, expected_publication_count: 998, persisted_count: 998, dispositions: [{ candidate_id: 'private-candidate', disposition: 'rejected', reason_code: 'candidate-reason' }] } as Job['result_summary'] });
    expect(host.textContent).toContain('Generated: 1000');
    expect(host.textContent).toContain('Rejected: 0');
    expect(host.textContent).toContain('Failed candidates: unknown');
    expect(host.textContent).toContain('Persisted: 998');
    expect(host.textContent).not.toMatch(/private-candidate|candidate-reason|Publication validated/);
    expect(fetch).not.toHaveBeenCalled();
});
it('retains partial/failure evidence without declaring successful publication', async () => {
    await mount({ ...base, status: 'failed', result_summary: { state: 'ingestion_failed', partial: true, generated_count: 10, persisted_count: 2, reason: { code: 'missing', message: 'Some results were not imported' } } as Job['result_summary'] });
    expect(host.textContent).toContain('Retained partial results');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Some results were not imported');
    expect(host.textContent).not.toContain('Publication validated');
});
it('reports FrustraMPNN results rather than requested designs', async () => {
    await mount({ ...base, model_id: 'frustrampnn', frustrampnn_result_count: 3, design_count: 0 });
    const values = Object.fromEntries([...host.querySelectorAll('dl > div')].map(el => [el.querySelector('dt')!.textContent, el.querySelector('dd')!.textContent]));
    expect(values['FrustraMPNN results']).toBe('3'); expect(values['Stored designs']).toBeUndefined();
});
it('keeps zero-design native publications reachable in the full Results view', async () => {
    await mount({ ...base, model_id: 'bindcraft2', design_count: 0 });
    expect(host.querySelector('a[href^="/designs/"]')?.textContent).toContain('Open in Results Viewer');
});
it('preserves explicit remote-result retrieval without starting a pull on expansion', async () => {
    await mount({ ...base, status: 'awaiting_input', awaiting_input: true, awaiting_stage: 'remote_results', remote_state: 'results_available', execution_target_id: 'vast:fixture', execution_policy: { remote_result_policy: 'manual' } });
    expect(host.textContent).toContain('Results reported ready on worker');
    expect(host.querySelector('[data-remote-results-job="summary-job"] button')).not.toBeNull();
    expect(fetch).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
});
