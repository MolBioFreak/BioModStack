import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { Job, RFD3GenerationReadModel } from '../../src/lib/api';

vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: object) => <div data-testid="viewer">{JSON.stringify(props)}</div> }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));
vi.mock('../../src/components/RemoteResultsPrompt', () => ({ RemoteResultsPrompt: () => null }));
vi.mock('../../src/components/RemoteDiagnosticsPrompt', () => ({ RemoteDiagnosticsPrompt: () => null }));
import RFD3GenerationResultsPane from '../../src/components/RFD3GenerationResultsPane';
import JobDetailPage from '../../src/components/JobDetailPage';
import { isRFD3GenerationResultJob } from '../../src/components/rfd3GenerationResultsView';

const job = (generator: string, mode = 'de_novo_design', model = 'protein_modification_experimental') => ({ id: 'native-job', name: 'Native generation', status: 'completed', model_id: model, mode, params: { generator }, created_at: '2026-01-01T00:00:00Z', design_count: 2, output_dir: null }) as Job;
const result: RFD3GenerationReadModel = {
    schema: 'bms.rfd3.generation.read-model.v1', job_id: 'native-job', request: {}, result_manifest_sha256: 'a'.repeat(64),
    counts: { requested: 2, generated: 2, accepted: 1 },
    aggregates: Object.fromEntries(['length', 'radius', 'helix', 'strand'].map((key) => [key, { min: 1, mean: 2, max: 3 }])) as RFD3GenerationReadModel['aggregates'],
    candidates: ['candidate-A', 'candidate-B'].map((candidate_id) => ({ candidate_id, status: 'generated', length: 100, radius: 12, helix_count: null, strand_count: null, structure_url: `/api/jobs/native-job/rfd3-generation/artifacts/${candidate_id}/candidate_structure` })),
};
afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

it('discriminates generator identity without hijacking iteration, Shape, or unrelated jobs', () => {
    expect(isRFD3GenerationResultJob(job('rfd3'))).toBe(true);
    for (const generator of ['disco', 'laproteina', '', 'unknown']) expect(isRFD3GenerationResultJob(job(generator))).toBe(false);
    expect(isRFD3GenerationResultJob(job('rfd3', 'local_redesign', 'protein_local_redesign'))).toBe(false);
    expect(isRFD3GenerationResultJob(job('rfd3', 'shape_conditioned'))).toBe(false);
    expect(isRFD3GenerationResultJob(undefined)).toBe(false);
});

it('selects the exact native mmCIF in shared Molstar and retains candidate downloads and metrics', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(['rfd3-generation', result.job_id], { data: result });
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(<QueryClientProvider client={client}><RFD3GenerationResultsPane jobId={result.job_id} /></QueryClientProvider>));
    const viewer = () => JSON.parse(container.querySelector('[data-testid="viewer"]')!.textContent!);
    expect(viewer()).toMatchObject({ structureUrl: result.candidates[0].structure_url, format: 'cif', artifactJobId: result.job_id });
    await act(async () => container.querySelectorAll<HTMLButtonElement>('tbody button')[1].click());
    expect(viewer()).toMatchObject({ structureUrl: result.candidates[1].structure_url, label: 'candidate-B', format: 'cif' });
    expect(container.querySelectorAll('tbody button')[1].getAttribute('aria-pressed')).toBe('true');
    expect(Array.from(container.querySelectorAll('a[download]')).map((link) => link.getAttribute('href'))).toEqual(result.candidates.map((candidate) => candidate.structure_url));
    expect(container.textContent).toContain('12.00');
    expect(container.textContent).toContain('—');
    await act(async () => client.setQueryData(['rfd3-generation', result.job_id], { data: { ...result, job_id: 'wrong-job' } }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    expect(container.querySelector('[data-testid="viewer"]')).toBeNull();
    await act(async () => root.unmount()); client.clear();
});

it.each(['rfd3', 'disco', 'laproteina'])('routes %s details only to its legitimate consumer', async (generator) => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ structures: [], count: 0 }) }); vi.stubGlobal('fetch', fetcher);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(['job', 'native-job'], job(generator));
    const Destination = () => { const location = useLocation(); return <div data-testid="destination">{location.pathname}{location.search}</div>; };
    const container = document.createElement('div'); document.body.appendChild(container); const root = createRoot(container);
    await act(async () => root.render(<MemoryRouter initialEntries={['/jobs/native-job?project=owned']}><QueryClientProvider client={client}><Routes><Route path="/jobs/:jobId" element={<JobDetailPage />} /><Route path="/designs/:jobId" element={<Destination />} /></Routes></QueryClientProvider></MemoryRouter>));
    if (generator === 'rfd3') {
        expect(container.textContent).toBe('/designs/native-job?project=owned');
        expect(fetcher).not.toHaveBeenCalled();
    } else {
        expect(container.querySelector('[data-testid="destination"]')).toBeNull();
        expect(fetcher).toHaveBeenCalledWith('/api/jobs/native-job/structure-files');
    }
    await act(async () => root.unmount()); client.clear();
});
