import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import StructureViewerPane from '../../src/components/StructureViewerPane';
// GPU canvas is outside this routing test; actual StructureViewerPane remains mounted.
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-test-gpu-canvas /> }));
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ProjectReturnBanner } from '../../src/components/project-manager/ProjectReturnBanner';
import { api } from '../../src/lib/api';

const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const Location = () => <span data-location={useLocation().pathname + useLocation().search} />;
const job = { id: 'parent', name: 'TEST mixed models', model_id: 'boltz2', mode: 'structure_prediction', status: 'completed', params: {}, design_count: 504, created_at: '2026-08-09T00:00:00Z' };
const design = (id: string, model: string) => ({ id, job_id: 'parent', name: id, pdb_path: `${id}.pdb`, provenance: { model_id: model }, created_at: '2026-08-09T00:00:00Z', supported_analyzers: [], viewer_capabilities: ['structure_viewer'], result_contract_source: 'persisted', analysis_contract_id: 'test_structure', artifact_class: 'validated_complex', review_artifact_manifest: { schema: 'bms.review-artifacts.v1', artifacts: { structure: { state: 'ready', path: `${id}.pdb`, sha256: (id === 'z-2' ? 'b' : 'a').repeat(64) } } } });
// Generated TEST rows; transport only is replaced, query serialization and consumers are real.
const rows = [...Array.from({ length: 501 }, (_, i) => design(`a-${i}`, 'boltz2')), ...Array.from({ length: 3 }, (_, i) => design(`z-${i}`, 'protenix'))];
const calls: Array<{ url: string; params: Record<string, any> }> = [];
let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
const original = api.defaults.adapter;
const returnUri = '/projects/p/experiments/g/domains/d?workspace=protein&section=results';
const setup = async (entry: string, children = false, suppliedRows = rows, selectedJob = job) => {
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url); const params = config.params ?? {};
        calls.push({ url, params });
        let data: unknown;
        const jobs = children ? [{ ...selectedJob, design_count: 0 }, { ...selectedJob, id: 'child', parent_job_id: 'parent' }] : [selectedJob];
        if (url === '/api/jobs') data = { jobs, total: jobs.length };
        else if (url.endsWith('/workflow-results')) data = { job: selectedJob, composition: { sha256: 'c'.repeat(64) }, tabs: [], source: { artifacts: [] }, artifacts: [], counts: { persisted_design_rows: suppliedRows.length } };
        else if (url.startsWith('/api/jobs/') && !url.includes('/backbones')) data = jobs.find(item => item.id === url.split('/').pop()) ?? job;
        else if (url === '/api/models/frustrampnn/integration') data = { model_id: 'frustrampnn', enabled: false };
        else if (url === '/api/launch-contexts/context') data = { schema: 'bms.launch-context.v1', launch_context_id: 'context', project_id: 'p', global_experiment_id: 'g', domain_experiment_id: 'd', workflow_id: null, workflow_revision_id: null, pinned_gpu: null, return_uri: returnUri, source_receipt_id: 'r', state: 'issued', issued_at: '2026-08-09T00:00:00Z', expires_at: '2026-08-09T00:30:00Z' };
        else if (url === '/api/designs') {
            let selected = params.model_id ? suppliedRows.filter(row => row.provenance.model_id === params.model_id) : suppliedRows;
            if (params.q) selected = selected.filter(row => row.name.includes(params.q));
            data = { designs: selected.slice(params.offset ?? 0, (params.offset ?? 0) + (params.limit ?? 100)), total: selected.length, model_counts: { boltz2: 501, protenix: 3 } };
        } else if (/^\/api\/designs\/[^/]+$/.test(url)) {
            data = suppliedRows.find(row => row.id === url.split('/').pop());
            if (!data) throw new Error('Design not found in requested Job lineage');
        } else if (url.includes('/backbones')) data = { backbones: [] };
        else throw new Error(`Unexpected TEST request ${url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    await act(async () => { renderer = create(<MemoryRouter initialEntries={[entry]}><QueryClientProvider client={client}><ThemeProvider><Location /><ProjectReturnBanner /><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
};
afterEach(async () => { await act(async () => renderer?.unmount()); client?.clear(); api.defaults.adapter = original; sessionStorage.clear(); });

test('mounted off-first-page model scopes on server without a bulk load and survives switching', async () => {
    await setup('/designs/parent?result_model=protenix&launch_context_id=context');
    expect(calls.some(call => call.url === '/api/designs' && call.params.model_id === 'protenix')).toBe(true);
    expect(calls.filter(call => call.url === '/api/designs').every(call => call.params.limit < 500)).toBe(true);
    const modelNav = renderer!.root.findByProps({ 'aria-label': 'Workflow model results' });
    expect(modelNav.findAllByType('button').find(button => text(button) === 'Protenix')?.props['aria-pressed']).toBe(true);
    const link = renderer!.root.findAllByType('a').find(item => item.props['aria-label'] === 'Return to Project context');
    expect(link?.props.href).toBe(returnUri);
    const primary = modelNav.findAllByType('button').find(button => text(button) === 'Structure Prediction')!;
    await act(async () => primary.props.onClick()); await flush();
    const secondary = renderer!.root.findByProps({ 'aria-label': 'Workflow model results' }).findAllByType('button').find(button => text(button) === 'Protenix')!;
    await act(async () => secondary.props.onClick()); await flush();
    expect(renderer!.root.findAllByType('span').find(item => item.props['data-location'])?.props['data-location']).toContain('launch_context_id=context');
});

test('mounted exact off-page Design requests the bound ID with Job ownership and never first-row substitution', async () => {
    await setup('/designs/parent?design_id=z-2&launch_context_id=context');
    const requests = calls.filter(call => /^\/api\/designs\/[^/]+$/.test(call.url));
    expect(requests.map(call => call.url)).toEqual(['/api/designs/z-2']);
    expect(requests[0].params.job_id).toBe('parent');
    expect(text(renderer!.root)).not.toContain('No other candidate has been selected');
    const pane = renderer!.root.findByType(StructureViewerPane);
    expect(pane.props.selectedDesignId).toBe('z-2');
    expect(pane.props.selectedDesign.review_artifact_manifest.artifacts.structure.sha256).toBe('b'.repeat(64));
    expect(pane.props.selectedDesign.provenance.model_id).toBe('protenix');
});

test('mounted missing or foreign exact selection stays unavailable rather than showing first Design', async () => {
    await setup('/designs/parent?design_id=foreign');
    expect(text(renderer!.root)).toContain('Requested Design foreign is unavailable in this Job lineage');
    expect(calls.filter(call => /^\/api\/designs\/[^/]+$/.test(call.url)).map(call => call.url)).toEqual(['/api/designs/foreign']);
});

test('mounted child redirect preserves server Project context and model URL', async () => {
    await setup('/designs/parent?result_model=protenix&launch_context_id=context', true);
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location']);
    expect(location?.props['data-location']).toBe('/designs/child?result_model=protenix&launch_context_id=context');
    expect(renderer!.root.findAllByType('a').find(item => item.props['aria-label'] === 'Return to Project context')?.props.href).toBe(returnUri);
});

test('mounted validator names never collapse distinct candidates or retries', async () => {
    const first = { ...design('first', 'protenix'), name: '1_shared', confidence_metrics: { plddt: 81 }, provenance: { model_id: 'protenix', attempt_id: 'original' } };
    const second = { ...design('second', 'protenix'), name: '2_shared', confidence_metrics: { plddt: 92 }, provenance: { model_id: 'protenix', attempt_id: 'retry' } };
    second.review_artifact_manifest.artifacts.structure.sha256 = 'b'.repeat(64);
    await setup('/designs/parent?result_model=protenix', false, [first, first, second]);
    const tableTab = renderer!.root.findAllByType('button').find(button => text(button).includes('Data Table'));
    expect(tableTab).toBeDefined();
    await act(async () => tableTab!.props.onClick()); await flush();
    const rowText = renderer!.root.findAllByType('tr').map(text);
    expect(rowText.filter(value => value.includes('1_shared'))).toHaveLength(1);
    expect(rowText.filter(value => value.includes('2_shared'))).toHaveLength(1);
});

test('PLR workflow context composes with the exact shared Design structure workbench', async () => {
    await setup('/designs/parent?design_id=z-2', false, rows, { ...job, model_id: 'protein_modification_experimental', mode: 'region_redesign' });
    expect(text(renderer!.root)).toContain('Protein Local Redesign');
    expect(calls.some(call => call.url.endsWith('/workflow-results'))).toBe(true);
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesignId).toBe('z-2');
});
