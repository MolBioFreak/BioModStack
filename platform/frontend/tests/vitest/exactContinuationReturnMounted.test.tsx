import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { JobDetailPage } from '../../src/components/JobDetailPage';
import { BinderResultFamily } from '../../src/components/BinderResultFamily';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));
vi.mock('../../src/components/RemoteResultsPrompt', () => ({ RemoteResultsPrompt: () => null }));
vi.mock('../../src/components/RemoteDiagnosticsPrompt', () => ({ RemoteDiagnosticsPrompt: () => null }));
vi.mock('../../src/lib/binderResultFamily', () => ({
    familyReferences: () => ({}), familyRoot: () => 'source',
    fetchBinderResultFamily: async () => [{ id: 'source', name: 'Source', status: 'completed' }],
    fetchBinderCandidateData: async () => ({}),
    buildProjectCandidateData: () => ({ candidates: [{ attempts: [{ id: 'failed', label: 'Monomer', model: 'protenix', status: 'failed', href: '/jobs/failed' }] }] }),
}));
let tree: ReactTestRenderer | undefined;
let client: QueryClient;
const text = (n: any): string => typeof n === 'string' ? n : (n.children ?? []).map(text).join('');
const settle = async () => act(async () => { await new Promise(r => setTimeout(r, 40)); });
afterEach(async () => { if (tree) await act(async () => tree!.unmount()); tree = undefined; client?.clear(); vi.unstubAllGlobals(); });
async function mount(node: React.ReactNode) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}>{node}</QueryClientProvider>); });
    await settle();
}
const back = '/designs/source?native_candidate=candidate2&artifact_id=alternate&target_state=state2&launch_context_id=pm#structure';
it('failed family attempts carry exact source and PM context just like completed attempts', async () => {
    window.history.replaceState({}, '', back);
    await mount(<BinderResultFamily jobId="source" candidate={{ candidate_key: 'candidate2', native_record: { design: 'candidate2' } } as any} candidateLabel="Candidate two" />);
    const href = tree!.root.findAllByType('a').find(a => text(a) === 'Monomer')!.props.href;
    const url = new URL(href, window.location.origin);
    expect(url.pathname).toBe('/jobs/failed'); expect(url.searchParams.get('return_to')).toBe(back);
    expect(url.searchParams.get('return_label')).toBe('Candidate two');
});
it.each([back, '//evil/designs/source'])('cold failed Job return accepts only the existing safe Results contract: %s', async returnTo => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ id: 'failed', name: 'Failed prediction', model_id: 'protenix', mode: 'complex', status: 'failed', created_at: '2026-01-01T00:00:00Z', error_message: 'Original failure remains' }) })));
    await mount(<MemoryRouter initialEntries={['/jobs/failed?' + new URLSearchParams({ return_to: returnTo, return_label: 'Candidate two' })]}><Routes><Route path="/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter>);
    const links = tree!.root.findAllByType('a').filter(a => text(a).includes('Candidate two'));
    expect(links).toHaveLength(returnTo === back ? 1 : 0);
    if (links.length) expect(links[0].props.href).toBe(back);
    expect(text(tree!.root)).toContain('failed');
});
