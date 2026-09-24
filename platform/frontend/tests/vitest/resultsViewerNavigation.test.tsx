import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import { DesignComparePane } from '../../src/components/DesignComparePane';
// GPU canvas is outside this routing test; actual StructureViewerPane remains mounted.
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-test-gpu-canvas /> }));
import { ResultsViewer } from '../../src/components/ResultsViewer';
import BlindPoseSelectedControls from '../../src/components/BlindPoseSelectedControls';
import { ProjectReturnBanner } from '../../src/components/project-manager/ProjectReturnBanner';
import { api } from '../../src/lib/api';

const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const Location = () => <span data-location={useLocation().pathname + useLocation().search} />;
const job = { id: 'parent', name: 'TEST mixed models', model_id: 'boltz2', mode: 'structure_prediction', status: 'completed', params: {}, design_count: 504, created_at: '2026-08-09T00:00:00Z' };
const design = (id: string, model: string) => ({ id, job_id: 'parent', name: id, pdb_path: `${id}.pdb`, provenance: { model_id: model, producer_model_id: model }, created_at: '2026-08-09T00:00:00Z', supported_analyzers: [], viewer_capabilities: ['structure_viewer'], result_contract_source: 'persisted', analysis_contract_id: 'test_structure', artifact_class: 'validated_complex', review_artifact_manifest: { schema: 'bms.review-artifacts.v1', artifacts: { structure: { state: 'ready', path: `${id}.pdb`, sha256: (id === 'z-2' ? 'b' : 'a').repeat(64) } } } });
// Generated TEST rows; transport only is replaced, query serialization and consumers are real.
const rows = [...Array.from({ length: 501 }, (_, i) => design(`a-${i}`, 'boltz2')), ...Array.from({ length: 3 }, (_, i) => design(`z-${i}`, 'protenix'))];
const calls: Array<{ url: string; params: Record<string, any> }> = [];
let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
const original = api.defaults.adapter;

test('ResultsViewer mounts both optional selected actions without a selection', async () => {
    await setup('/designs/parent');
    const actions = renderer!.root.findByType(BlindPoseSelectedControls);
    expect(actions.props.sourceJobId).toBe('parent');
    expect(actions.props.selectedDesignIds).toEqual([]);
    expect(text(actions)).toContain('Blind pose (experimental)');
    expect(text(actions)).toContain('LigandMPNN interface context (experimental)');
    const tableTab = renderer!.root.findAllByType('button').find(button => text(button).includes('Data Table'))!;
    await act(async () => tableTab.props.onClick()); await flush();
    const row = renderer!.root.findAllByType('tr').find(candidate => text(candidate).includes('a-0'))!;
    await act(async () => row.findByType('input').props.onChange({ target: { checked: true } }));
    expect(renderer!.root.findByType(BlindPoseSelectedControls).props.selectedDesignIds).toEqual(['a-0']);
});

test('BC2 native records supplement the mounted Design selection and comparison workbench', async () => {
    const paths: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        paths.push(url);
        if (url.endsWith('/settings')) return { ok: true, json: async () => ({ requested_settings: {}, effective_settings: {} }) };
        if (!url.includes('/bindcraft2-results?')) throw new Error(`Unexpected native request ${url}`);
        return { ok: true, json: async () => ({ schema: 'bindcraft2.native-readback.v1', arm: 'arm-A',
            stage: new URL(url, 'http://test').searchParams.get('stage'), offset: 0, limit: 25, total: 1,
            arms: [{ name: 'arm-A', accounting: {} }], accounting: {}, metadata: null,
            rows: [{ design: 'native-evidence', native_score: 0 }],
        }) };
    }));
    try {
        await setup('/designs/parent', false, [design('bc2-1', 'bindcraft2'), design('bc2-2', 'bindcraft2')], { ...job, model_id: 'bindcraft2', mode: 'campaign', design_count: 2 });
        expect(text(renderer!.root)).toContain('native-evidence');
        expect(paths).toContain('/api/models/bindcraft2/campaign/jobs/parent/settings');
        await act(async () => renderer!.root.findByProps({ 'aria-label': 'Native records' }).props.onChange({ target: { value: 'retained' } }));
        await flush();
        expect(paths.some(path => path.includes('stage=retained'))).toBe(true);
        const button = (label: string) => renderer!.root.findAllByType('button').find(node => text(node).includes(label))!;
        await act(async () => button('Data Table').props.onClick()); await flush();
        const row = renderer!.root.findAllByType('tr').find(node => text(node).includes('bc2-1'))!;
        await act(async () => row.findByType('input').props.onChange({ target: { checked: true } }));
        expect(renderer!.root.findByType(BlindPoseSelectedControls).props.selectedDesignIds).toEqual(['bc2-1']);
        expect(button('Compare Designs')).toBeDefined();
        expect(button('Compare Jobs')).toBeDefined();
        await act(async () => button('Compare Designs').props.onClick()); await flush();
        expect(renderer!.root.findByType(DesignComparePane).props.designs.map((row: { id: string }) => row.id)).toEqual(['bc2-1', 'bc2-2']);
        expect(text(renderer!.root)).toContain('native-evidence');
        await act(async () => button('Data Table').props.onClick()); await flush();
        expect(renderer!.root.findByType(BlindPoseSelectedControls).props.selectedDesignIds).toEqual(['bc2-1']);
        expect(calls.some(call => call.url === '/api/designs' && call.params.job_id === 'parent')).toBe(true);
    } finally { vi.unstubAllGlobals(); }
});

test('mounted selected actions submit exact independent subsets and read native result', async () => {
    const requests: Array<{ url: string; body: any }> = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        requests.push({ url, body: typeof config.data === 'string' ? JSON.parse(config.data) : config.data });
        const data = url.endsWith('/result') ? { records: [{ design_id: 'd-2', classification: 'unclassified', raw_metrics: { native: 0.4 } }] }
            : url.includes('ligandmpnn') ? { job: { id: 'ligand-child' } } : { id: 'blind-child' };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const opened: string[] = [];
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { renderer = create(<BlindPoseSelectedControls sourceJobId="source" sourceModelId="bindcraft2"
        sourceParams={{ bindcraft2_settings: { targets: [{ name: 'target-1' }, { name: 'target-2' }] } }}
        selectedDesignIds={['d-2', 'd-1']} onOpenJob={id => opened.push(id)} />); });
    const control = () => renderer!.root;
    const change = async (label: string, value: string) => {
        await act(async () => control().findByProps({ 'aria-label': label }).props.onChange({ target: { value } }));
    };
    await change('Declared target', 'target-2');
    await change('Blind pose target chains', 'T');
    await change('Binder chains for d-2', 'B,C');
    await change('Binder chains for d-1', 'D');
    await change('Blind pose model variant', 'full');
    await change('Blind pose model ID or path', 'checkpoint');
    await change('Inference loops', '4');
    await change('Diffusion steps', '83');
    await change('Diffusion samples', '2');
    await change('Blind pose seed', '17');
    await act(async () => control().findAllByType('button').find(b => text(b) === 'Run selected blind pose')!.props.onClick());
    expect(requests[0]).toEqual({ url: '/api/blind-pose/selected', body: {
        source_job_id: 'source', target_name: 'target-2', design_ids: ['d-2', 'd-1'],
        binder_chains: { 'd-2': ['B', 'C'], 'd-1': ['D'] }, target_chains: ['T'],
        settings: { model_variant: 'full', model_id_or_path: 'checkpoint', num_loops: 4, num_sampling_steps: 83, num_diffusion_samples: 2, seed: 17 },
    } });
    await act(async () => control().findAllByType('button').find(b => text(b) === 'Open result Job')!.props.onClick());
    expect(opened).toEqual(['blind-child']);
    await change('Fixed binder chain', 'B');
    await change('Target chain', 'T');
    await change('Target patch residue IDs', 'T12,T13A');
    await change('Seed', '21');
    await change('Samples', '3');
    await change('Temperature', '0.5');
    await act(async () => control().findAllByType('button').find(b => text(b) === 'Run selected interface context')!.props.onClick());
    expect(requests[1]).toEqual({ url: '/api/ligandmpnn/interface-context/selected', body: {
        action: 'ligandmpnn_interface_context', source_job_id: 'source', round_id: 'source',
        candidate_ids: ['d-2', 'd-1'], settings: { binder_chain: 'B', target_chain: 'T', target_patch: ['T12', 'T13A'], seed: 21, samples: 3, temperature: 0.5 },
    } });
    await act(async () => renderer!.update(<BlindPoseSelectedControls sourceJobId="blind-child" sourceModelId="esmfold2"
        sourceParams={{}} selectedDesignIds={[]} resultJob={{ id: 'blind-child', model_id: 'esmfold2', mode: 'blind_pose', status: 'completed' }} onOpenJob={() => undefined} />));
    await flush();
    expect(requests[2].url).toBe('/api/blind-pose/blind-child/result');
    expect(text(control())).toContain('unclassified');
    expect(text(control())).toContain('native');
    await act(async () => renderer!.update(<BlindPoseSelectedControls sourceJobId="ligand-child" sourceModelId="ligandmpnn"
        sourceParams={{}} selectedDesignIds={[]} resultJob={{ id: 'ligand-child', model_id: 'ligandmpnn', mode: 'interface_context', status: 'completed' }} onOpenJob={() => undefined} />));
    await flush();
    expect(requests[3].url).toBe('/api/ligandmpnn/interface-context/ligand-child/result');
    expect(text(control())).toContain('unclassified');
});

test('mounted selected action shows server rejection without suppressing experimental operation', async () => {
    api.defaults.adapter = async config => { throw new Error(`Selected route unavailable: ${config.url}`); };
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { renderer = create(<BlindPoseSelectedControls sourceJobId="source" sourceModelId="other" sourceParams={{}}
        selectedDesignIds={['d-1']} onOpenJob={() => undefined} />); });
    await act(async () => renderer!.root.findAllByType('button').find(b => text(b) === 'Run selected interface context')!.props.onClick());
    expect(text(renderer!.root)).toContain('Selected route unavailable: /api/ligandmpnn/interface-context/selected');
    expect(text(renderer!.root)).toContain('Blind pose (experimental)');
});

test('bound native document reads direct PAE without saved-analysis GET or queued-action exposure in ResultsViewer', async()=>{
    const native:any={...design('native','protenix'),core_protein_scientific_contract:1,
        scientific_structure_document:{documentId:'native-doc',candidateId:'native',contentSha256:'a'.repeat(64),sourceKind:'mmcif'},
        supported_analyzers:['pae_matrix']};
    native.review_artifact_manifest.artifacts.aligned_error={state:'ready'};
    const fetcher=vi.fn(async()=>({ok:false}));vi.stubGlobal('fetch',fetcher);
    try {
        await setup('/designs/parent?design_id=native',false,[native]);
        const pane=renderer!.root.findByType(StructureViewerPane);
        expect(pane.props.viewerAnalyses.onRunPaeMatrix).toBeUndefined();
        expect(pane.props.viewerAnalyses.paeMatrixData).toBeNull();
        expect(calls.some(call=>call.url.includes('/analyses/pae_matrix'))).toBe(false);
        expect(fetcher.mock.calls.some(call=>String(call[0]).includes('/pae?max_size=1024'))).toBe(true);
    } finally {vi.unstubAllGlobals();}
});
const returnUri = '/projects/p/experiments/g/domains/d?workspace=protein&section=results';
const setup = async (entry: string, children = false, suppliedRows = rows, selectedJob = job, extraJobs: typeof job[] = []) => {
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url); const params = config.params ?? {};
        calls.push({ url, params });
        let data: unknown;
        const jobs = (children ? [{ ...selectedJob, design_count: 0 }, { ...selectedJob, id: 'child', parent_job_id: 'parent' }] : [selectedJob]).concat(extraJobs);
        if (url === '/api/jobs') data = { jobs, total: jobs.length };
        else if (url.endsWith('/workflow-results')) data = { job: selectedJob, composition: { sha256: 'c'.repeat(64) }, tabs: [], source: { artifacts: [{ artifact_id: 'source', label: 'Source structure', content_url: '/api/jobs/parent/workflow-results/artifacts/source', bytes: 100 }] }, artifacts: [], receipt: { schema_version: 1, validator_summaries: [{ validator: 'esmfold2', state: 'complete' }] }, counts: { persisted_design_rows: suppliedRows.length } };
        else if (url.startsWith('/api/jobs/') && !url.includes('/backbones')) data = jobs.find(item => item.id === url.split('/').pop()) ?? job;
        else if (url === '/api/models/frustrampnn/integration') data = { model_id: 'frustrampnn', enabled: false };
        else if (url === '/api/launch-contexts/context') data = { schema: 'bms.launch-context.v1', launch_context_id: 'context', project_id: 'p', global_experiment_id: 'g', domain_experiment_id: 'd', workflow_id: null, workflow_revision_id: null, pinned_gpu: null, return_uri: returnUri, source_receipt_id: 'r', state: 'issued', issued_at: '2026-08-09T00:00:00Z', expires_at: '2026-08-09T00:30:00Z' };
        else if (url === '/api/designs') {
            const modelOf = (row: typeof suppliedRows[number]) => (row.provenance as Record<string, unknown>).producer_model_id;
            let selected = params.model_id ? suppliedRows.filter(row => modelOf(row) === params.model_id) : suppliedRows;
            if (extraJobs.length) selected = selected.filter(row => row.job_id === params.job_id);
            if (params.q) selected = selected.filter(row => row.name.includes(params.q));
            const counts: Record<string, number> = {};
            for (const row of suppliedRows) { const model = modelOf(row); if (typeof model === 'string' && model) counts[model] = (counts[model] ?? 0) + 1; }
            data = { designs: selected.slice(params.offset ?? 0, (params.offset ?? 0) + (params.limit ?? 100)), total: selected.length, model_counts: counts };
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

test('exact model scope requires canonical producer identity, not upstream model metadata', async () => {
    const unattributed = design('unattributed', 'protenix');
    delete (unattributed.provenance as Record<string, unknown>).producer_model_id;
    await setup('/designs/parent?result_model=protenix&design_id=unattributed', false,
        [design('known', 'protenix'), unattributed]);
    expect(text(renderer!.root)).toContain('Requested Design unattributed is unavailable');
    expect(renderer!.root.findAllByType(StructureViewerPane)).toHaveLength(0);
});

test('mounted child redirect preserves server Project context and model URL', async () => {
    await setup('/designs/parent?result_model=protenix&launch_context_id=context', true);
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location']);
    expect(location?.props['data-location']).toBe('/designs/child?result_model=protenix&launch_context_id=context');
    expect(renderer!.root.findAllByType('a').find(item => item.props['aria-label'] === 'Return to Project context')?.props.href).toBe(returnUri);
});

test('mounted validator names never collapse distinct candidates or retries', async () => {
    const first = { ...design('first', 'protenix'), name: '1_shared', confidence_metrics: { plddt: 81 }, provenance: { model_id: 'protenix', producer_model_id: 'protenix', attempt_id: 'original' } };
    const second = { ...design('second', 'protenix'), name: '2_shared', confidence_metrics: { plddt: 92 }, provenance: { model_id: 'protenix', producer_model_id: 'protenix', attempt_id: 'retry' } };
    second.review_artifact_manifest.artifacts.structure.sha256 = 'b'.repeat(64);
    await setup('/designs/parent?result_model=protenix', false, [first, first, second]);
    const tableTab = renderer!.root.findAllByType('button').find(button => text(button).includes('Data Table'));
    expect(tableTab).toBeDefined();
    await act(async () => tableTab!.props.onClick()); await flush();
    const rowText = renderer!.root.findAllByType('tr').map(text);
    expect(rowText.filter(value => value.includes('1_shared'))).toHaveLength(1);
    expect(rowText.filter(value => value.includes('2_shared'))).toHaveLength(1);
});

test('explicit model switching releases a previously exact Design but preserves Project context', async () => {
    await setup('/designs/parent?design_id=z-2&result_model=protenix&launch_context_id=context');
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesignId).toBe('z-2');
    const nav = renderer!.root.findByProps({ 'aria-label': 'Workflow model results' });
    const primary = nav.findAllByType('button').find(button => text(button) === 'Structure Prediction')!;
    await act(async () => primary.props.onClick()); await flush();
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location'])?.props['data-location'];
    expect(location).not.toContain('design_id=');
    expect(location).toContain('launch_context_id=context');
    const structureTab = renderer!.root.findAllByType('button').find(button => text(button).endsWith('Structure'))!;
    await act(async () => structureTab.props.onClick()); await flush();
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesign.provenance.model_id).toBe('boltz2');
    expect(text(renderer!.root)).not.toContain('No other candidate has been selected');
});

test('explicit candidate selection replaces a previously exact Design in the reopen URL', async () => {
    await setup('/designs/parent?design_id=z-2&result_model=protenix&launch_context_id=context');
    const tableTab = renderer!.root.findAllByType('button').find(button => text(button).includes('Data Table'))!;
    await act(async () => tableTab.props.onClick()); await flush();
    const target = renderer!.root.findAllByType('tr').find(row => text(row).includes('z-0'))!;
    await act(async () => target.props.onClick()); await flush();
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location'])?.props['data-location'];
    expect(location).toContain('design_id=z-0');
    expect(location).toContain('launch_context_id=context');
});

test('explicit Job switching clears only Job-local selection and keeps the Project return context', async () => {
    const other = { ...job, id: 'other', name: 'TEST other result', status: 'failed', design_count: 0 };
    await setup('/designs/parent?design_id=z-2&result_model=protenix&candidate_id=old-candidate&invocation_id=old-invocation&launch_context_id=context', false, rows, job, [other]);
    const menu = renderer!.root.findAllByType('button').find(button => text(button).includes(job.name))!;
    await act(async () => menu.props.onClick()); await flush();
    const option = renderer!.root.findAllByType('button').find(button => text(button).includes(other.name))!;
    await act(async () => option.props.onClick()); await flush();
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location'])?.props['data-location'];
    expect(location).toBe('/designs/other?launch_context_id=context');
    expect(text(renderer!.root)).not.toContain('Requested Design');
});

const plrModels = [['rfd3', 'RFD3', 8], ['fampnn', 'FA-MPNN', 8], ['esmfold2', 'ESMFold2', 8], ['protenix_v2', 'Protenix V2', 40]] as const;
const plrRows = plrModels.flatMap(([model, , count]) => Array.from({ length: count }, (_, index) => ({
    ...design(`${model}-${index}`, 'protein_modification_experimental'),
    provenance: { model_id: 'protein_modification_experimental', producer_model_id: model },
})));

test.each(plrModels)('PLR %s opens the exact producer-scoped Design without conflating workflow provenance', async (model, label, count) => {
    await setup(`/designs/parent?design_id=${model}-0&result_model=${model}&launch_context_id=context`, false, plrRows, { ...job, model_id: 'protein_modification_experimental', mode: 'region_redesign', design_count: 64 });
    const nav = renderer!.root.findByProps({ 'aria-label': 'Workflow model results' });
    expect(nav.findAllByType('button').map(text)).toEqual(expect.arrayContaining(['All results', ...plrModels.map(([, modelLabel]) => modelLabel)]));
    expect(nav.findAllByType('button').find(button => text(button) === label)?.props['aria-pressed']).toBe(true);
    expect(calls.filter(call => call.url === '/api/designs').every(call => call.params.model_id === model)).toBe(true);
    const pane = renderer!.root.findByType(StructureViewerPane);
    expect(pane.props.selectedDesignId).toBe(`${model}-0`);
    expect(pane.props.selectedDesign.provenance.model_id).toBe('protein_modification_experimental');
    expect(pane.props.selectedDesign.provenance.producer_model_id).toBe(model);
    expect(pane.props.designs).toHaveLength(count);
    expect(pane.props.designs.every((row: typeof plrRows[number]) => row.provenance.producer_model_id === model)).toBe(true);
    expect(text(renderer!.root)).not.toContain('No other candidate has been selected');
    expect(calls.some(call => call.url.endsWith('/workflow-results'))).toBe(false);
    const otherLabel = model === 'rfd3' ? 'FA-MPNN' : 'RFD3';
    await act(async () => nav.findAllByType('button').find(button => text(button) === otherLabel)!.props.onClick()); await flush();
    const location = renderer!.root.findAllByType('span').find(item => item.props['data-location'])?.props['data-location'];
    expect(location).toContain('launch_context_id=context');
    expect(location).not.toContain('design_id=');
    const structureTab = renderer!.root.findAllByType('button').find(button => text(button).endsWith('Structure'))!;
    await act(async () => structureTab.props.onClick()); await flush();
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesign.provenance.producer_model_id).toBe(model === 'rfd3' ? 'fampnn' : 'rfd3');
});

test('PLR workflow context composes with the exact shared Design structure workbench', async () => {
    await setup('/designs/parent?design_id=z-2', false, rows, { ...job, model_id: 'protein_modification_experimental', mode: 'region_redesign' });
    expect(text(renderer!.root)).toContain('Protein Local Redesign');
    expect(calls.some(call => call.url.endsWith('/workflow-results'))).toBe(false);
    expect(text(renderer!.root)).not.toContain('Source and validator receipt');
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesignId).toBe('z-2');
});

test('PLR native files load only on disclosure and never dump validator receipts into the workbench', async () => {
    await setup('/designs/parent?design_id=z-2', false, rows, { ...job, model_id: 'protein_modification_experimental', mode: 'region_redesign' });
    expect(calls.filter(call => call.url.endsWith('/workflow-results'))).toHaveLength(0);
    const inventory = renderer!.root.findAllByType('details').find(node => node.findAllByType('summary').some(summary => text(summary) === 'Protein Local Redesign files'))!;
    expect(inventory).toBeDefined();
    await act(async () => inventory.props.onToggle({ currentTarget: { open: true } }));
    await flush();
    expect(calls.filter(call => call.url.endsWith('/workflow-results'))).toHaveLength(1);
    expect(renderer!.root.findAllByType('a').some(link => link.props.href === '/api/jobs/parent/workflow-results/artifacts/source')).toBe(true);
    expect(text(renderer!.root)).not.toContain('validator_summaries');
    expect(text(renderer!.root)).not.toContain('schema_version');
    expect(text(renderer!.root)).not.toContain('Source and validator receipt');
    expect(renderer!.root.findByType(StructureViewerPane).props.selectedDesignId).toBe('z-2');
});
