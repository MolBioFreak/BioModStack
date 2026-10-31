import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type RFD3GenerationReadModel } from '../../src/lib/api';
import RFD3GenerationResultsPane from '../../src/components/RFD3GenerationResultsPane';
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
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const adapter = api.defaults.adapter;
let unexpected: string[];
beforeEach(() => {
    unexpected = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No live network'); });
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No live network'); });
    api.defaults.adapter = async config => { unexpected.push(`${config.method} ${config.url}`); throw new Error('Missing inert HTTP fixture'); };
    window.history.replaceState({}, '', '/designs/fixture-job');
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); api.defaults.adapter = adapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); expect(unexpected).toEqual([]); });
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
// The Results workspace and its native header read arrive through lazy chunks; wait on the clock, not on a tick count.
const until = async (done: () => boolean, ms = 110000) => { const end = Date.now() + ms; while (!done() && Date.now() < end) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); }); };
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
    await act(async () => button('Candidate 1003').click());
    expect(viewer()).toMatchObject({ structureUrl: generation.candidates[1002].structure_url, label: 'Candidate 1003', format: 'cif', artifactJobId: generation.job_id });
    expect(container.querySelector<HTMLInputElement>('[aria-label="Exact candidate identifier"]')?.value).toBe('fixture-1002');
    expect(container.querySelector('tbody tr:last-child a')?.getAttribute('href')).toBe(generation.candidates[1002].structure_url);
    const node = container.querySelector('[data-testid="viewer"]');
    await act(async () => button('First').click());
    await act(async () => container.querySelector<HTMLDetailsElement>('details')!.open = true);
    expect(container.querySelector('[data-testid="viewer"]')).toBe(node);
    expect(viewer().label).toBe('Candidate 1003');
    expect(container.querySelectorAll('tbody tr')).toHaveLength(10);
});

it.each([
    ['generation', 'published'], ['generation', 'missing'], ['generation', 'zero'],
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
    const expected = state === 'missing' ? unknown : `${state === 'zero' ? '0' : '1,003'} ${noun} candidates`;
    await until(() => !!container.textContent?.includes(expected));
    expect(container.textContent).toContain(expected);
    expect(container.textContent).not.toContain('0 designs');
    expect(calls.filter(url => url.endsWith(endpoint))).toHaveLength(1);
}, 120000);

it.each(['rfd4_proteina', 'complexa'] as const)('names the published %s record count in the real ResultsViewer header, not zero Design rows', async model => {
    const job = { id: 'fixture-job', name: 'TEST native publication', model_id: model, mode: 'generate', status: 'completed', params: {}, design_count: 0,
        provenance: { native_generation_publication: { contract: `${model}_v1` } }, created_at: '2026-01-01T00:00:00Z' };
    const totals: number[] = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith('/generation-results')) {
            totals.push(Number((config.params as { limit?: number } | undefined)?.limit));
            data = { records: [], total: 17, offset: 0, limit: 1, publication: { contract: `${model}_v1` }, receipt: {}, model_id: model, owner_model_id: model };
        } else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>);
    await until(() => !!container.textContent?.includes('17 generated candidates'));
    expect(container.textContent).toContain('17 generated candidates');
    expect(container.textContent).not.toContain('0 designs');
    expect(totals).toContain(1);   // the header reads only the total, one record
}, 120000);

it('counts generated molecules, not reward and timing rows, in the published native header', async () => {
    const job = { id: 'fixture-job', name: 'TEST native publication', model_id: 'complexa', mode: 'generate', status: 'completed', params: {}, design_count: 0,
        provenance: { native_generation_publication: { contract: 'complexa_v1' } }, created_at: '2026-01-01T00:00:00Z' };
    api.defaults.adapter = async config => {
        const url = String(config.url);
        let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith('/generation-results')) data = { records: [], total: 5, offset: 0, limit: 1, publication: { contract: 'complexa_v1' }, receipt: {},
            record_counts: { total: 5, observations: 3, molecular_records: 2, populations: { generation: 2, observation: 3 } }, model_id: 'complexa', owner_model_id: 'complexa' };
        else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>);
    await until(() => !!container.textContent?.includes('2 generated candidates'));
    expect(container.textContent).toContain('2 generated candidates');
    expect(container.textContent).not.toContain('5 generated candidates');
}, 120000);

it.each(['generation'] as const)('retains the inspected %s page and exact continuation identity on reopen', async kind => {
    const source = { job_id: 'fixture-job', design_id: 'persisted-design', output_format: 'native' as const };
    const model = generation;
    client.setQueryData(['rfd3-generation', model.job_id], { data: { ...model, candidates: model.candidates.map(candidate => ({ ...candidate, source_structure: candidate.candidate_id === 'fixture-1002' ? source : undefined })) } });
    const pane = <RFD3GenerationResultsPane jobId={model.job_id} />;
    await mount(pane);
    await act(async () => button('Last').click());
    await act(async () => button(kind === 'generation' ? 'Candidate 1003' : 'fixture-1002').click());
    for (const label of ['Redesign', 'Design sequence', 'Predict structure']) {
        const link = [...container.querySelectorAll('a')].find(item => item.textContent === label)!;
        const query = new URL(link.href).searchParams;
        expect(JSON.parse(query.get('source_structure')!)).toEqual(source);
        expect(link.href).not.toContain('binder-continuation');
    }
    await act(async () => root.render(null));
    await mount(pane);
    expect(container.textContent).toContain('Page 101 / 101');
    expect(viewer().label).toBe('Candidate 1003');
});
