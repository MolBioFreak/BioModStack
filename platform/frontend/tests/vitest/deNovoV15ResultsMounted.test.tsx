import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type RFD3GenerationReadModel, type RFD3LocalRedesignReadModel } from '../../src/lib/api';
import RFD3GenerationResultsPane from '../../src/components/RFD3GenerationResultsPane';
import RFD3LocalRedesignResultsPane from '../../src/components/RFD3LocalRedesignResultsPane';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';

vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: object) => <div data-testid="viewer">{JSON.stringify(props)}</div> }));
vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));

// Non-persisted rendering fixture, not scientific output or performance timing evidence.
const generation: RFD3GenerationReadModel = {
    schema: 'bms.rfd3.generation.read-model.v1', job_id: 'fixture-job', request: { seed: 0 }, result_manifest_sha256: 'a'.repeat(64),
    counts: { requested: 1003, generated: 1003, accepted: 9 },
    aggregates: Object.fromEntries(['length', 'radius', 'helix', 'strand'].map(key => [key, { min: 1, mean: 2, max: 3 }])) as RFD3GenerationReadModel['aggregates'],
    candidates: Array.from({ length: 1003 }, (_, i) => ({ candidate_id: `fixture-${i}`, status: i < 9 ? 'accepted' : 'generated', length: 100, radius: 12, helix_count: null, strand_count: 0, structure_url: `/api/jobs/fixture-job/rfd3-generation/artifacts/fixture-${i}/candidate_structure` })),
};
const local: RFD3LocalRedesignReadModel = {
    schema: 'bms.rfd3.local-redesign.read-model.v1', job_id: 'fixture-job',
    capabilities: { source_structure: true, candidate_structures: true, native_metadata: true, trajectories: { requested: false, available: false, reason: 'not_requested' } },
    request: { request_id: 'fixture-request', schema_version: 1, request_sha256: 'b'.repeat(64), profile_id: 'fixture-profile', profile_registry_sha256: 'c'.repeat(64), redesign_mode: 'partial_diffusion', sequence_policy: 'preserve', status: 'complete', request: { execution: { num_designs: 1003, seed: 0 } }, request_path_scope: 'basename', provenance_path_scope: 'basename' },
    candidates: generation.candidates.map(c => ({ candidate_id: c.candidate_id, status: 'generated', result_set: 'fixture', stage: 'rfd3', artifact_manifest_sha256: 'd'.repeat(64), metrics: { backbone_rmsd: 0, summary_confidences: null }, metadata: {} })),
    artifacts: generation.candidates.map(c => ({ candidate_id: c.candidate_id, artifact_id: `document-${c.candidate_id}`, role: 'structure', relative_path: `${c.candidate_id}.cif`, sha256: 'e'.repeat(64), bytes: 123, media_type: 'chemical/x-mmcif', metadata: {} })),
};
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const adapter = api.defaults.adapter;
beforeEach(() => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); document.body.replaceChildren(); });
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(b => b.textContent === label)!;
const viewer = () => JSON.parse(container.querySelector('[data-testid="viewer"]')!.textContent!);
const mount = async (element: React.ReactNode) => { await act(async () => root.render(<MemoryRouter initialEntries={['/designs/fixture-job']}><QueryClientProvider client={client}><ThemeProvider>{element}</ThemeProvider></QueryClientProvider></MemoryRouter>)); await flush(); };

it('bounds a 1003-candidate generation fixture, selects its exact tail mmCIF and retains selection across pages/details', async () => {
    client.setQueryData(['rfd3-generation', generation.job_id], { data: generation });
    await mount(<RFD3GenerationResultsPane jobId={generation.job_id} />);
    expect(container.querySelectorAll('tbody tr')).toHaveLength(10);
    expect(container.querySelectorAll('[data-testid="viewer"]')).toHaveLength(1);
    expect(container.textContent).toContain('supplied accepted-candidate set may use different criteria');
    expect(container.textContent).toContain('does not establish folding or binding validation');
    await act(async () => button('Last').click());
    expect(container.querySelectorAll('tbody tr')).toHaveLength(3);
    await act(async () => button('fixture-1002').click());
    expect(viewer()).toMatchObject({ structureUrl: generation.candidates[1002].structure_url, label: 'fixture-1002', format: 'cif', artifactJobId: generation.job_id });
    expect(container.querySelector('tbody tr:last-child a')?.getAttribute('href')).toBe(generation.candidates[1002].structure_url);
    const node = container.querySelector('[data-testid="viewer"]');
    await act(async () => button('First').click());
    await act(async () => container.querySelector<HTMLDetailsElement>('details')!.open = true);
    expect(container.querySelector('[data-testid="viewer"]')).toBe(node);
    expect(viewer().label).toBe('fixture-1002');
    expect(container.querySelectorAll('tbody tr')).toHaveLength(10);
});

it('bounds local-redesign selection and renders only selected native metrics ahead of collapsed receipts', async () => {
    client.setQueryData(['rfd3-local-redesign', local.job_id], { data: local });
    await mount(<RFD3LocalRedesignResultsPane jobId={local.job_id} />);
    expect(container.querySelectorAll('button[aria-pressed]')).toHaveLength(10);
    expect(container.querySelectorAll('article')).toHaveLength(1);
    expect(container.querySelector('details')?.open).toBe(false);
    await act(async () => button('Last').click());
    await act(async () => button('fixture-1002').click());
    expect(viewer()).toMatchObject({ structureUrl: '/api/jobs/fixture-job/rfd3-local-redesign/artifacts/document-fixture-1002', label: 'fixture-1002', format: 'cif', artifactJobId: local.job_id });
    expect(container.querySelector('article')?.textContent).toContain('Backbone RMSD0');
    expect(container.querySelector('article')?.textContent).toContain('Summary confidence—');
    expect(container.querySelector('article a')?.getAttribute('href')).toBe(viewer().structureUrl);
    expect(container.querySelectorAll('button[aria-pressed]')).toHaveLength(3);
    const node = container.querySelector('[data-testid="viewer"]');
    await act(async () => { container.querySelector<HTMLDetailsElement>('details')!.open = true; button('First').click(); });
    expect(container.querySelector('[data-testid="viewer"]')).toBe(node);
    expect(viewer().label).toBe('fixture-1002');
});

it.each([
    ['generation', 'published'], ['generation', 'missing'], ['generation', 'zero'],
    ['redesign', 'published'], ['redesign', 'missing'], ['redesign', 'zero'],
] as const)('uses native %s %s evidence in the real ResultsViewer header, never generic zero Design rows', async (kind, state) => {
    const calls: string[] = [];
    const job = { id: 'fixture-job', name: 'TEST native generation', model_id: 'protein_modification_experimental', mode: 'de_novo_design', status: 'completed', params: { generator: 'rfd3' }, design_count: 0, created_at: '2026-01-01T00:00:00Z' };
    if (kind === 'redesign') { job.model_id = 'protein_local_redesign'; job.mode = 'local_redesign'; }
    const endpoint = kind === 'generation' ? '/rfd3-generation' : '/rfd3-local-redesign';
    api.defaults.adapter = async config => {
        const url = String(config.url); calls.push(url);
        let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith(endpoint)) {
            if (state === 'missing') throw new Error('fixture missing native evidence');
            data = kind === 'redesign'
                ? state === 'zero' ? { ...local, candidates: [], artifacts: [] } : local
                : state === 'zero' ? { ...generation, counts: { requested: 1003, generated: 0, accepted: 0 }, candidates: [] } : generation;
        } else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>);
    const noun = kind === 'generation' ? 'generated' : 'published';
    const unknown = kind === 'generation' ? 'Generated count unavailable' : 'Candidate count unavailable';
    expect(container.textContent).toContain(state === 'missing' ? unknown : `${state === 'zero' ? '0' : '1,003'} ${noun} candidates`);
    expect(container.textContent).not.toContain('0 designs');
    expect(calls.filter(url => url.endsWith(endpoint))).toHaveLength(1);
});
