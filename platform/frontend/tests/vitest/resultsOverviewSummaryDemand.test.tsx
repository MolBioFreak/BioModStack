import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-test-gpu-canvas /> }));
import StructureViewerPane from '../../src/components/StructureViewerPane';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { api } from '../../src/lib/api';
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let i = 0; i < 15; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const job = { id: 'parent', name: 'TEST summaries', model_id: 'protenix', mode: 'structure_prediction', status: 'completed', params: {}, design_count: 1, created_at: '2026-08-09T00:00:00Z' };
const design = { id: 'exact-candidate', job_id: 'parent', name: 'exact-candidate', pdb_path: 'exact.pdb', provenance: { model_id: 'protenix', producer_model_id: 'protenix' }, created_at: job.created_at,
    supported_analyzers: ['chain_metrics', 'structure_summary', 'contact_map', 'ipsae_interface', 'pae_matrix', 'antibody_annotation_pack'], viewer_capabilities: ['structure_viewer'], result_contract_source: 'persisted', analysis_contract_id: 'test_structure', artifact_class: 'validated_complex',
    review_artifact_manifest: { schema: 'bms.review-artifacts.v1', artifacts: { aligned_error: { state: 'ready', path: 'exact.pae.json' }, structure: { state: 'ready', path: 'exact.pdb', sha256: 'a'.repeat(64) } } } };
const detail: Record<string, any> = {
    ipsae_interface: { ipsae: 0.75, ipsae_chain_pair: 'A:B', pair_scores: [{ native_metric: 0.375 }] },
    pae_matrix: { size: 2, matrix: [[0, 1], [1, 0]] },
    antibody_annotation_pack: { cdr_lengths: { H1: 2 }, humanness_score: 0.85, antibody_type: 'nanobody', cdrs: { H1: 'AA' } },
    chain_metrics: { A: { type: 'protein', length: 2, residue_numbers: [12, 13], native_positions: [{ author_chain_id: 'A', author_residue_number: 12, insertion_code: 'B', document_id: 'exact-document' }], metric: 0.375 } },
    structure_summary: { residue_count: 2, chain_ids: ['A'], gyration_radius: 1.5, secondary_structure: 'HH' },
    contact_map: { size: 2, distance_matrix: [[0, 1], [1, 0]], residue_numbers: [12, 13], chain_ids: ['A', 'A'] },
};
const summaries: Record<string, any> = { ipsae_interface: { ipsae: 0.75, ipsae_chain_pair: 'A:B' }, chain_metrics: { chain_count: 1 }, structure_summary: { residue_count: 2, chain_count: 1 }, contact_map: { size: 2 } };
const calls: Array<{ url: string; params: Record<string, any> }> = [];
let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
const original = api.defaults.adapter;
const setup = async (status = 'completed', historical = false, failFull = false, absentPair = false) => {
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url); const params = config.params ?? {}; calls.push({ url, params });
        let data: any;
        if (url.includes('/analyses/')) {
            const type = url.split('/').pop()!;
            if (failFull && params.include_result !== false) throw new Error('TEST full artifact unreadable');
            data = { run_id: `exact-${type}`, analysis_type: type, subject_kind: 'design', subject_id: design.id, params: type === 'contact_map' ? { max_size: 300 } : {}, status,
                summary: historical ? null : absentPair && type === 'ipsae_interface' ? { ipsae: 0.75 } : summaries[type], result: status === 'completed' && params.include_result !== false ? detail[type] : null, artifacts: { result_json: `exact/${type}.json` }, error_message: status === 'failed' ? 'TEST scientific failure' : null };
        } else if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/parent') data = job;
        else if (url === '/api/models/frustrampnn/integration') data = { model_id: 'frustrampnn', enabled: false };
        else if (url === '/api/designs') data = { designs: [design], total: 1, model_counts: { protenix: 1 } };
        else if (url === `/api/designs/${design.id}`) data = design;
        else if (url.includes('/backbones')) data = { backbones: [] };
        else throw new Error(`Unexpected TEST request ${url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    await act(async () => { renderer = create(<MemoryRouter initialEntries={['/designs/parent']}><QueryClientProvider client={client}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await vi.waitFor(async () => { await flush(); expect(calls.some(call => call.url.includes('/analyses/'))).toBe(true); }, { timeout: 10000 });
    await flush();
    await act(async () => button('Analyses').props.onClick());
    return (nextStatus: string) => { status = nextStatus; };
};
const button = (label: string) => renderer!.root.findAllByType('button').find(node => label === 'Structure' ? text(node) === '3DStructure' : text(node).includes(label))!;
afterEach(async () => { await act(async () => renderer?.unmount()); client?.clear(); api.defaults.adapter = original; sessionStorage.clear(); });
test('mounted default Overview uses persisted counts; actual Structure demand fetches complete identity-bound detail', async () => {
    await setup();
    console.info('OVERVIEW_REQUEST_TRACE', JSON.stringify(calls.filter(call => call.url.includes('/analyses/'))));
    expect(text(renderer!.root)).toContain('1 chains cached');
    expect(text(renderer!.root)).toContain('0.75 • A:B');
    expect(text(renderer!.root)).toContain('2 residues • 1 chains');
    expect(text(renderer!.root)).toContain('2 × 2 matrix');
    expect(calls.filter(call => call.url.includes('/analyses/')).map(call => [call.url.split('/').pop(), call.params.include_result]).sort()).toEqual([['antibody_annotation_pack', undefined], ['chain_metrics', false], ['contact_map', false], ['ipsae_interface', false], ['pae_matrix', undefined], ['structure_summary', false]]);
    expect(renderer!.root.findAllByType(StructureViewerPane)).toHaveLength(0);
    await act(async () => button('Structure').props.onClick());
    await vi.waitFor(async () => { await flush(); expect(renderer!.root.findAllByType(StructureViewerPane)).toHaveLength(1); }, { timeout: 10000 });
    const pane = renderer!.root.findByType(StructureViewerPane);
    expect(pane.props.viewerAnalyses.chainMetrics).toEqual(detail.chain_metrics);
    expect(pane.props.viewerAnalyses.chainMetricsRun.run_id).toBe('exact-chain_metrics');
    expect(pane.props.viewerAnalyses.structureAnalysis).toEqual(detail.structure_summary);
    expect(pane.props.viewerAnalyses.contactMap).toEqual(detail.contact_map);
    expect(pane.props.viewerAnalyses.ipsaeInterface).toEqual(detail.ipsae_interface);
    expect(pane.props.viewerAnalyses.paeMatrixData).toEqual(detail.pae_matrix);
    expect(calls.filter(call => call.url.includes('/analyses/') && call.params.include_result !== false)).toHaveLength(6);
    expect(client.getQueryData(['design-analysis', 'chain_metrics', design.id, 'summary'])).toMatchObject({ result: null });
    expect(client.getQueryData(['design-analysis', 'chain_metrics', design.id])).toMatchObject({ result: detail.chain_metrics });
});
test('historical absent summaries preserve the existing full card consumer', async () => {
    await setup('completed', true);
    console.info('OVERVIEW_REQUEST_TRACE', JSON.stringify(calls.filter(call => call.url.includes('/analyses/'))));
    expect(text(renderer!.root)).toContain('1 chains cached');
    expect(text(renderer!.root)).toContain('0.75 • A:B');
    expect(calls.filter(call => call.url.includes('/analyses/') && call.params.include_result !== false)).toHaveLength(6);
});
test.each(['missing', 'queued', 'running', 'failed'])('Overview %s remains truthful without full reads', async status => {
    await setup(status);
    expect(calls.filter(call => /analyses\/(chain_metrics|structure_summary|contact_map|ipsae_interface)$/.test(call.url)).every(call => call.params.include_result === false)).toBe(true);
    expect(text(renderer!.root)).not.toContain('1 chains cached');
    if (status === 'failed') expect(text(renderer!.root)).toContain('TEST scientific failure');
    expect(client.getQueryData(['design-analysis', 'chain_metrics', design.id, 'summary'])).toMatchObject({ status });
});
test('failed demanded full read does not reuse summary as scientific detail', async () => {
    await setup('completed', false, true);
    await act(async () => button('Structure').props.onClick());
    await vi.waitFor(async () => { await flush(); expect(renderer!.root.findAllByType(StructureViewerPane)).toHaveLength(1); }, { timeout: 10000 });
    const pane = renderer!.root.findByType(StructureViewerPane);
    expect(pane.props.viewerAnalyses.chainMetrics).toBeNull();
    expect(String(client.getQueryState(['design-analysis', 'chain_metrics', design.id])?.error)).toContain('TEST full artifact unreadable');
});

test('queued summary polls automatically and stops at terminal completion without full-result requests', async () => {
    const setStatus = await setup('queued');
    setStatus('completed');
    await vi.waitFor(async () => { await flush(); expect(text(renderer!.root)).toContain('1 chains cached'); }, { timeout: 4000 });
    const chainReads = () => calls.filter(call => call.url.endsWith('/analyses/chain_metrics'));
    expect(chainReads().length).toBeGreaterThan(1);
    expect(chainReads().every(call => call.params.include_result === false)).toBe(true);
    const stoppedAt = chainReads().length;
    await new Promise(resolve => setTimeout(resolve, 1700));
    await flush();
    expect(chainReads()).toHaveLength(stoppedAt);
}, 10000);

test('partial ipSAE summary cannot erase the persisted displayed chain pair', async () => {
    await setup('completed', false, false, true);
    expect(text(renderer!.root)).toContain('0.75 • A:B');
    expect(calls.filter(call => call.url.endsWith('/analyses/ipsae_interface') && call.params.include_result !== false)).toHaveLength(1);
    expect(calls.filter(call => call.url.endsWith('/analyses/chain_metrics') && call.params.include_result !== false)).toHaveLength(0);
});
