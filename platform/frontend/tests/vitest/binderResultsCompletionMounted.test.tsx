import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type Job } from '../../src/lib/api';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import BlindPoseSelectedControls from '../../src/components/BlindPoseSelectedControls';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { ParamField } from '../../src/components/ModelParameterField';
import { EpitopeSelector } from '../../src/components/EpitopeSelector';
import { readBinderCandidateDocuments } from '../../src/lib/binderContinuation';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-native-canvas /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-native-canvas data-structure-url={props.structureUrl} /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div data-source-canvas /> }));
vi.mock('react-plotly.js', () => ({ default: (props: any) => <div data-cohort-plot={props.layout?.title?.text ?? ''} /> }));
const original = api.defaults.adapter;
beforeEach(() => vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} })));
let mounted: ReactTestRenderer | undefined;
let client: QueryClient;
const calls: Array<{ url: string; params: any; body: any }> = [];
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const flush = async () => { for (let i = 0; i < 10; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const button = (label: string) => mounted!.root.findAllByType('button').find(node => text(node) === label)!;
const doc = { artifact_id: 'alternate', target_state: 'state B', logical_path: 'native/exact.pdb', download_url: '/api/files/download/exact.pdb' };
const record = { candidate_key: 'producer-key', design_id: 'exact', metrics: { native_zero: 0, missing: null }, structures: [doc] };
const baseJob = { id: 'parent', name: 'TEST generation', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed', params: {}, design_count: 0, created_at: '2026-09-01T00:00:00Z' };
const design = { id: 'exact', job_id: 'parent', name: 'exact', pdb_path: 'primary.pdb', created_at: baseJob.created_at, supported_analyzers: [], viewer_capabilities: ['structure_viewer'], result_contract_source: 'persisted', analysis_contract_id: 'test_structure', artifact_class: 'validated_complex', provenance: { model_id: 'ppiflow', producer_model_id: 'ppiflow' }, review_artifact_manifest: { schema: 'bms.review-artifacts.v1', artifacts: { structure: { state: 'ready', path: 'primary.pdb', sha256: 'a'.repeat(64) } } } };
const fields = [
    { name: 'maturation_repack_enabled', label: 'Repack', type: 'boolean', default: false },
    { name: 'fixed_positions', label: 'Fixed author positions', type: 'string' },
    { name: 'native_file', label: 'Native file', type: 'file' },
    { name: 'native_sequence', label: 'Native sequence', type: 'text', preset_type: 'sequence' },
];
function transport(job = baseJob, zero = false, designFailure = false) {
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url); const params = config.params ?? {}; const body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url, params, body });
        let data: any = [];
        if (url.endsWith('/generation-results')) {
            const nativeRows = zero ? [] : Array.from({ length: 26 }, (_, i) => i === 0 ? record : { ...record, design_id: `extra-${i}`, candidate_key: i === 25 ? 'page-two' : `extra-key-${i}` });
            data = { receipt: { emitted_samples: nativeRows.length, status: zero ? 'zero_yield' : 'complete' }, records: nativeRows.slice(params.offset, params.offset + params.limit), total: nativeRows.length, offset: params.offset, limit: params.limit, publication: { candidates: nativeRows }, artifacts: [{ path: 'samples.jsonl', download_url: '/api/files/download/samples.jsonl' }] };
        }
        else if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url.endsWith('/workflow-results')) data = { job, composition: { sha256: 'c'.repeat(64) }, tabs: [], source: {}, artifacts: [], counts: { persisted_design_rows: zero ? 0 : 1 } };
        else if (url.endsWith('/selection-context')) data = { source_job_id: 'parent', targets: [], candidate_documents: { exact: [doc] } };
        else if (url === '/api/jobs/parent') data = job;
        else if (url === '/api/designs') { if (designFailure) throw new Error('generic Design query offline'); data = { designs: zero ? [] : [design], total: zero ? 0 : 1, model_counts: {} }; }
        else if (url === '/api/designs/exact') data = design;
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.startsWith('/api/models/')) data = { params: fields };
        else if (url.includes('/backbones')) data = { backbones: [] };
        else if (url === '/api/user-sequences') data = [{ id: 'seq', name: 'TEST native sequence', sequence: 'ACDE', sequence_type: 'protein', length: 4, tags: [] }];
        else if (url === '/api/files/browse') data = { entries: [{ path: '/inputs/native.csv', name: 'native.csv', is_directory: false }] };
        else if (url === '/api/binder-continuation/selected') data = { launched_jobs: [{ id: 'child', name: 'native child' }] };
        else if (url === '/api/blind-pose/selected') data = { id: 'diagnostic-child' };
        else if (url === '/api/ligandmpnn/interface-context/selected') data = { job: { id: 'interface-child' } };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
}
function LocationProbe() { const location = useLocation(); return <span data-location={location.pathname + location.search} />; }
async function mount(element: React.ReactNode, route?: string) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { mounted = create(<MemoryRouter initialEntries={[route ?? '/']}><QueryClientProvider client={client}><ThemeProvider><LocationProbe />{element}</ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
}
afterEach(async () => { await act(async () => mounted?.unmount()); mounted = undefined; client?.clear(); api.defaults.adapter = original; vi.unstubAllGlobals(); sessionStorage.clear(); });

it.each([['boltzgen', 'protein_binder'], ['boltzgen', 'nanobody_binder'], ['boltzgen', 'peptide_binder'], ['ppiflow', 'protein_binder'], ['ppiflow', 'antibody_binder'], ['ppiflow', 'nanobody_binder']])('Jobs mounts %s %s zero yield despite generic Design errors', async (model_id, mode) => {
    const job = { ...baseJob, model_id, mode }; transport(job, true, true);
    await mount(<table><tbody><JobDetailsPanel job={job as Job} onClose={() => {}} /></tbody></table>);
    expect(text(mounted!.root)).toContain('published zero-yield result');
    expect(calls.filter(call => call.url.endsWith('/generation-results'))).toEqual([expect.objectContaining({ url: '/api/jobs/parent/generation-results', params: { offset: 0, limit: 1000 } })]);
    expect(mounted!.root.findByType(NativeBinderGenerationResults)).toBeDefined();
});

it('Results mounts zero-yield publication even when its generic query fails', async () => {
    transport(baseJob, true, true);
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>, '/designs/parent');
    expect(text(mounted!.root)).toContain('published zero-yield result');
    expect(text(mounted!.root)).not.toContain('Results could not be loaded');
    expect(button('Overview')).toBeUndefined();
});

it('pages and reopens published native metrics with exact native document navigation, without a worker request', async () => {
    transport(); await mount(<NativeBinderGenerationResults jobId="parent" status="completed" />);
    expect(mounted!.root.findByProps({ 'aria-label': 'Sort by native_zero' })).toBeDefined();
    expect(mounted!.root.findAllByType('td').map(text)).toContain('0');
    expect(text(mounted!.root)).toContain('Explicit null');
    expect(mounted!.root.findAllByType('a').map(a => a.props.href)).toContain('/designs/parent?design_id=exact&artifact_id=alternate&target_state=state+B');
    await act(async () => button('Next native records').props.onClick()); await flush();
    expect(text(mounted!.root)).toContain('page-two');
    expect(calls.filter(call => call.url.endsWith('/generation-results'))).toHaveLength(1); // Table pages do not refetch the publication.
    await act(async () => mounted!.unmount()); client.clear();
    await mount(<NativeBinderGenerationResults jobId="parent" status="completed" />);
    expect(text(mounted!.root)).toContain('producer-key');
    expect(calls.every(call => call.url === '/api/jobs/parent/generation-results')).toBe(true);
});

it('exact native URL restores selection and destination, never loads primary document as fallback', async () => {
    transport(); const fetcher = vi.fn(async (_url: string) => ({ ok: false, status: 404 })); vi.stubGlobal('fetch', fetcher);
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>, '/designs/parent?design_id=exact&artifact_id=alternate&target_state=state+B&launch_context_id=destination');
    expect(readBinderCandidateDocuments('parent')).toEqual({ exact: { artifact_id: 'alternate', target_state: 'state B' } });
    const selected = mounted!.root.findByType(BinderSelectedControls);
    expect(selected.props.selectedDesignIds).toContain('exact');
    expect(selected.props.launchContextId).toBe('destination');
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toBe(doc.download_url);
    expect(mounted!.root.findByType(StructureWorkbench).props.alphafoldView).toBe(false);
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/files/download/exact.pdb']);
    await act(async () => button('Run selected operation').props.onClick());
    const request = calls.find(call => call.url === '/api/binder-continuation/selected')!.body;
    expect(request.candidate_documents).toEqual({ exact: { artifact_id: 'alternate', target_state: 'state B' } });
    expect(request.launch_context_id).toBe('destination');
    expect(request.params).not.toHaveProperty('launch_context_id');
    await act(async () => mounted!.unmount()); client.clear();
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>, '/designs/parent?design_id=exact&artifact_id=alternate&target_state=state+B&launch_context_id=destination');
    expect(mounted!.root.findByType(BinderSelectedControls).props.selectedDesignIds).toContain('exact');
    expect(mounted!.root.findByProps({ 'aria-label': 'Document for exact' }).props.value).toBe(JSON.stringify({ artifact_id: 'alternate', target_state: 'state B' }));
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/files/download/exact.pdb', '/api/files/download/exact.pdb']);
    await act(async () => mounted!.root.findByProps({ 'aria-label': 'Document for exact' }).props.onChange({ target: { value: '{}' } })); await flush();
    expect(mounted!.root.findAllByType('span').find(node => node.props['data-location'])?.props['data-location']).toBe('/designs/parent?design_id=exact&launch_context_id=destination');
    expect(readBinderCandidateDocuments('parent')).toEqual({});
});

it('real selected workspace browses native files, binds author masks and shares exact choices with independent diagnostics', async () => {
    transport(); const opened = vi.fn();
    const pdb = 'ATOM      1  CA  ALA a  12       0.000   0.000   0.000  1.00 20.00           C  \nEND\n';
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => new TextEncoder().encode(pdb).buffer })));
    await mount(<><BinderSelectedControls sourceJobId="parent" selectedDesignIds={['exact']} onOpenJob={opened} onStartMD={() => {}} launchContextId="destination" /><BlindPoseSelectedControls sourceJobId="parent" sourceModelId="ppiflow" sourceParams={{ launch_context_id: 'wrong-source-destination' }} selectedDesignIds={['exact']} onOpenJob={opened} launchContextId="destination" /></>);
    for (const label of ['Sources', 'Operations', 'Settings', 'Review']) expect(button(label)).toBeDefined();
    const choice = mounted!.root.findByProps({ 'aria-label': 'Document for exact' });
    await act(async () => choice.props.onChange({ target: { value: choice.findAllByType('option')[1].props.value } }));
    await act(async () => button('Inspect exact source exact').props.onClick()); await flush();
    const residues = mounted!.root.findByType(EpitopeSelector);
    expect(residues.props.chains[0].id).toBe('a');
    await act(async () => residues.props.onSelectionChange(new Set(['a12'])));
    await act(async () => button('Use inspected author residues as fixed positions').props.onClick());
    await act(async () => button('Settings').props.onClick());
    const file = mounted!.root.findAllByType(ParamField).find(node => node.props.param.name === 'native_file')!;
    await act(async () => file.findAllByType('button')[0].props.onClick()); await flush();
    await act(async () => button('native.csv').props.onClick());
    await act(async () => button('Choose saved sequence for Native sequence').props.onClick()); await flush();
    await act(async () => button('Use').props.onClick());
    await act(async () => button('Review').props.onClick());
    expect(text(mounted!.root)).toContain('/inputs/native.csv');
    await act(async () => button('Run selected operation').props.onClick());
    expect(calls.find(call => call.url === '/api/binder-continuation/selected')!.body.params).toMatchObject({ fixed_positions: 'a:12', native_file: '/inputs/native.csv', native_sequence: 'ACDE', maturation_repack_enabled: false });
    expect(calls.find(call => call.url === '/api/binder-continuation/selected')!.body.params).not.toHaveProperty('chain_id');
    await act(async () => button('Open child Job').props.onClick()); expect(opened).toHaveBeenCalledWith('child');
    await act(async () => button('Run selected blind pose').props.onClick());
    await act(async () => button('Run selected interface context').props.onClick());
    for (const url of ['/api/blind-pose/selected', '/api/ligandmpnn/interface-context/selected']) {
        const request = calls.find(call => call.url === url)!.body;
        expect(request.launch_context_id).toBe('destination');
        expect(request.candidate_documents).toEqual({ exact: { artifact_id: 'alternate', target_state: 'state B' } });
    }
});

it('keeps chart axes and cohort filters when drilling into a structure and returning', async () => {
    transport(); await mount(<NativeBinderGenerationResults jobId="parent" status="completed" />);
    const field = (label: string) => mounted!.root.findByProps({ 'aria-label': label });
    await act(async () => field('X metric').props.onChange({ target: { value: 'native_zero' } }));
    await act(async () => field('Search candidates').props.onChange({ target: { value: 'producer-key' } }));
    await act(async () => button('producer-key').props.onClick());
    expect(button('Structure').props['aria-selected']).toBe(true);
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toBe(doc.download_url);
    await act(async () => button('Analytics').props.onClick());
    expect(field('X metric').props.value).toBe('native_zero');
    expect(field('Search candidates').props.value).toBe('producer-key');
    expect(mounted!.root.findByProps({ 'aria-label': 'Candidate data table' }).findByType('tbody').findAllByType('tr')).toHaveLength(1);
});

it('historical unjoined BoltzGen rows retain native evidence without fabricated Design or document links', async () => {
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: {
        receipt: { native_accounting: { retained: 1 } }, records: [{ candidate_key: 'historical', metrics: { native_rank: 0, missing: null } }],
        publication: { schema: 'legacy-core' }, artifacts: [], total: 1, offset: 0, limit: 100,
    } });
    await mount(<NativeBinderGenerationResults jobId="historical-job" status="completed" />);
    expect(text(mounted!.root)).toContain('historical');
    expect(mounted!.root.findByProps({ 'aria-label': 'Sort by native_rank' })).toBeDefined();
    expect(mounted!.root.findAllByType('td').map(text)).toContain('0');
    expect(mounted!.root.findAllByType('a').filter(node => String(node.props.href).startsWith('/designs/') || String(node.props.href).startsWith('/api/files/'))).toHaveLength(0);
    expect(text(mounted!.root)).not.toContain('Selection unavailable');
});

it.each(['ppiflow', 'boltzgen'])('Results %s uses the native workbench and shares all seven selections without generic capability flags', async model_id => {
    const job = { ...baseJob, model_id, design_count: 7 };
    transport(job);
    const fallback = api.defaults.adapter as (config: any) => Promise<any>;
    const rows = Array.from({ length: 7 }, (_, index) => ({
        candidate_key: `source:0/sample:${index}`, design_id: `candidate-${index}`,
        metrics: { seq_length: 50 + index, dsasa: 700 + index, num_ca_ca_clashes: 0 },
        structures: [{ artifact_id: `artifact-${index}`, primary: true, logical_path: `sample-${index}.pdb`, download_url: `/api/files/download/sample-${index}.pdb` }],
    }));
    const designs = rows.map(row => ({ ...design, id: row.design_id, viewer_capabilities: [], result_contract_source: 'unsupported_persisted' }));
    api.defaults.adapter = async config => {
        const url = String(config.url);
        let data: any;
        if (url.endsWith('/generation-results')) data = { receipt: { emitted_samples: 7 }, records: rows, total: 7, offset: 0, limit: 100, publication: {}, artifacts: [] };
        else if (url === '/api/designs') data = { designs: [...designs].reverse(), total: 7, model_counts: {} };
        else if (url.startsWith('/api/designs/candidate-')) data = designs.find(row => url.endsWith(row.id));
        else return fallback(config);
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>, '/designs/parent?launch_context_id=destination');
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toBe(rows[0].structures[0].download_url);
    expect(mounted!.root.findByType(BinderSelectedControls).props.selectedDesignIds).toEqual([]);
    expect(button('Overview')).toBeUndefined();
    expect(text(mounted!.root)).not.toContain('Average pLDDT');
    expect(mounted!.root.findAllByType('details').filter(node => ['Native receipt and accounting', 'Native files and provenance', 'Selected record: complete native readback', 'Selected candidate operations (0)'].includes(text(node.findAllByType('summary')[0]))).every(node => !node.props.open)).toBe(true);
    expect(mounted!.root.findByType(StructureWorkbench).props.workbenchCollapsed).toBe(true);
    const native = () => mounted!.root.findByType(NativeBinderGenerationResults);
    await act(async () => native().props.onInspectDocument(rows[6])); await flush();
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toBe(rows[6].structures[0].download_url);
    expect(mounted!.root.findAllByType('span').find(node => node.props['data-location'])?.props['data-location']).toBe('/designs/parent?design_id=candidate-6&launch_context_id=destination');
    expect(mounted!.root.findByType(BinderSelectedControls).props.selectedDesignIds).toEqual([]);
    await act(async () => native().props.onSelectedDesignIdsChange(rows.map(row => row.design_id))); await flush();
    expect(mounted!.root.findByType(BinderSelectedControls).props.selectedDesignIds).toEqual(rows.map(row => row.design_id));
    expect(mounted!.root.findByType(BlindPoseSelectedControls).props.selectedDesignIds).toEqual(rows.map(row => row.design_id));
    expect(native().props.selectedDesignIds).toHaveLength(7);
    expect(mounted!.root.findByType(BinderSelectedControls).props.launchContextId).toBe('destination');
});
