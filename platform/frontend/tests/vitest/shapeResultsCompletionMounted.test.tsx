import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { api, type Design, type Job } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { JobSubmission } from '../../src/components/JobSubmission';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { ProteinLocalRedesignTemplate } from '../../src/components/ProteinLocalRedesignTemplate';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { AnalyticsDashboard } from '../../src/components/AnalyticsDashboard';
import { StructureWorkbench } from '../../src/structureViewer/StructureWorkbench';
import { shapeCohort, shapeDocuments, filterShapeCohort, shapeCsv } from '../../src/lib/shapeResultsView';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-canvas-url={props.structureUrl} data-canvas-format={props.format} /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
vi.mock('react-plotly.js', () => ({ default: (props: any) => <div data-plot-points={props.data?.[0]?.x?.length} /> }));
const adapter = api.defaults.adapter;
let mounted: ReactTestRenderer | undefined;
let client: QueryClient;
let failOffset: number | undefined;
let rows: Design[];
const calls: Array<{ url: string; params: any; body: any; context: unknown }> = [];
const blobs: Blob[] = [];
const job = { id: 'shape', name: 'TEST ONLY Shape cohort', model_id: 'protein_modification_experimental', mode: 'shape_blueprint', status: 'completed', params: {}, design_count: 1005, created_at: '2026-09-01T00:00:00Z' } as Job;
const fixture = (index: number) => ({ id: `id-${index}`, job_id: 'shape', name: `fixture-${String(index).padStart(4, '0')}`, review_profile_id: 'shape_blueprint', pdb_path: `/results/primary-${index}.pdb`, supported_analyzers: [], viewer_capabilities: ['structure_viewer'],
    confidence_metrics: { shape_total: index === 1 ? null : index, shape_outside: 0, plddt_overall: 70, post_refold: { status: 'accepted', post_refold: { ca_rmsd_angstrom: 0 } }, validator_evidence: { records: [{ validator: 'boltz2', sample_id: 's0', metrics: { ptm: null } }, { validator: 'protenix_v2', sample_id: 's1', status: 'read_unavailable' }] } },
    provenance: { predictor: 'esmfold2', sequence_engine: 'fampnn', geometry_id: 'geometry' },
    review_artifact_manifest: { artifacts: { structure: { path: `/results/primary-${index}.pdb`, format: 'pdb', sha256: 'a'.repeat(64), state: 'ready' },
        source_backbone: { path: `/results/source-${index}.cif`, format: 'cif', artifact_id: `source-${index}`, sha256: 'b'.repeat(64), state: 'ready', model_number: 7 },
        predictor_sample: { path: `/results/prediction-${index}.cif`, format: 'cif', artifact_id: `sample-${index}`, target_state: 'native-state', sha256: 'c'.repeat(64), state: 'ready', validator: 'protenix_v2', sample_id: 's1' } } },
} as unknown as Design);
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const flush = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const button = (label: string) => mounted!.root.findAllByType('button').find(node => text(node) === label)!;
const field = (label: string) => mounted!.root.findByProps({ 'aria-label': label });
async function click(label: string) { await act(async () => button(label).props.onClick()); await flush(); }
async function change(label: string, value: unknown, checked = false) { await act(async () => field(label).props.onChange({ target: checked ? { checked: value } : { value } })); await flush(); }
function LocationProbe() { const loc = useLocation(); React.useLayoutEffect(() => { window.history.replaceState({}, '', loc.pathname + loc.search); }, [loc.pathname, loc.search]); return <span data-location={loc.pathname + loc.search} />; }
async function mount(route = '/designs/shape') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { mounted = create(<MemoryRouter initialEntries={[route]}><QueryClientProvider client={client}><ThemeProvider><LocationProbe /><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /><Route path="/submit" element={<JobSubmission />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
}
beforeEach(() => {
    sessionStorage.clear(); calls.length = 0; blobs.length = 0; failOffset = undefined;
    rows = Array.from({ length: 1005 }, (_, i) => fixture(i));
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob as Blob); return 'blob:fixture'; });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    api.defaults.adapter = async config => {
        const url = String(config.url), params = config.params ?? {}, body = config.data ? JSON.parse(String(config.data)) : undefined;
        calls.push({ url, params, body, context: config.headers.get('X-BMS-Launch-Context-ID') });
        let data: any = {};
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/shape') data = { ...job, design_count: rows.length };
        else if (url === '/api/designs') {
            if (params.offset === failOffset) throw new Error('fixture later-page read failure');
            data = { designs: rows.slice(params.offset ?? 0, (params.offset ?? 0) + params.limit), total: rows.length, model_counts: {} };
        }
        else if (/\/api\/designs\/id-\d+$/.test(url)) data = rows.find(row => row.id === url.split('/').pop());
        else if (url.endsWith('/workflow-results')) data = { job, composition: { sha256: 'd'.repeat(64) }, tabs: [], source: {}, artifacts: [], counts: { persisted_design_rows: rows.length } };
        else if (url === '/api/launch-contexts/deliberate-context') data = { schema: 'bms.launch-context.v1', launch_context_id: 'deliberate-context', project_id: 'project-fixture', global_experiment_id: 'global-fixture', domain_experiment_id: 'domain-fixture', workflow_id: null, workflow_revision_id: null, pinned_gpu: null, return_uri: '/projects/project-fixture', source_receipt_id: 'receipt-fixture', state: 'issued', issued_at: '2026-09-01T00:00:00Z', expires_at: '2026-10-01T00:00:00Z' };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url === '/api/models') data = [];
        else if (url.includes('/templates')) data = [];
        else if (url.startsWith('/api/models/')) data = { id: url.split('/')[3], modes: [{ id: 'local_redesign', params: [] }], params: [] };
        else if (url.includes('execution-targets')) data = [];
        else if (url.includes('gpu') || url.includes('/status')) data = { gpus: [] };
        else if (url === '/api/files/materialize-structure') {
            const native = body.output_format === 'native';
            data = { source_structure: { ...body, path: 'inputs/exact-native.cif', expected_sha256: body.expected_sha256 },
                path: native ? 'inputs/exact-native.cif' : 'inputs/exact-derived.pdb', sha256: native ? body.expected_sha256 : 'd'.repeat(64), format: native ? 'cif' : 'pdb',
                native_path: 'inputs/exact-native.cif', native_format: 'cif', native_sha256: body.expected_sha256, model_number: 7, model_numbers: [7], author_residues: [], source_identity: body, source_path: 'native/exact.cif' };
        }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => mounted?.unmount()); mounted = undefined; client?.clear(); api.defaults.adapter = adapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); });

it('real Results shell charts the full thousand-row fixture while table pages, filters and selection remain separate', async () => {
    await mount();
    await vi.waitFor(async () => { await flush(); expect(text(mounted!.root)).toContain('Entire cohort · 1005 candidates'); });
    expect(calls.filter(call => call.url === '/api/designs').map(call => call.params.offset)).toEqual([0, 500, 1000]);
    const analytics = () => mounted!.root.findByType(AnalyticsDashboard).props.nativeCohort;
    expect(analytics().rows).toHaveLength(1005);
    expect(field('Shape candidate table').findAllByType('tbody')[0].findAllByType('tr')).toHaveLength(25);
    await click('Select all matching (1005)');
    await click('Next Shape page'); await change('Select Shape page', false, true);
    expect(analytics().selectedIds).toHaveLength(980);
    await change('Search Shape candidates', 'fixture-1004');
    expect(analytics().rows.map((row: any) => row.id)).toEqual(['id-1004']);
    await click('fixture-1004');
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toContain('primary-1004.pdb');
    await click('Back to Shape cohort');
    expect(field('Search Shape candidates').props.value).toBe('fixture-1004');
    expect(analytics().selectedIds).toHaveLength(980);
    await click('Export native JSON (1)');
    const reader = new FileReader(); const result = new Promise<string>(resolve => { reader.onload = () => resolve(String(reader.result)); }); reader.readAsText(blobs.at(-1)!);
    expect(JSON.parse(await result)).toMatchObject({ complete: true, records: [{ id: 'id-1004' }] });
    await change('Shape export scope', 'selected'); await click('Export native JSON (980)');
    const selectedReader = new FileReader(); const selectedResult = new Promise<string>(resolve => { selectedReader.onload = () => resolve(String(selectedReader.result)); }); selectedReader.readAsText(blobs.at(-1)!);
    const exportedIds = JSON.parse(await selectedResult).records.map((row: any) => row.id);
    expect(exportedIds).toHaveLength(980); expect(exportedIds).toContain('id-1004'); expect(exportedIds).not.toContain('id-25');
    await click('Clear Shape filters'); await click('Candidate'); await click('Candidate');
    expect(field('Shape candidate table').findAllByType('tbody')[0].findAllByType('tr')[0].findAllByType('button')[0].children).toEqual(['fixture-1004']);
    expect(calls.filter(call => call.url === '/api/designs')).toHaveLength(3);
});

it('later-page failure retains usable rows, marks partial statistics/export, and retries without changing selection', async () => {
    failOffset = 500; await mount();
    expect(text(mounted!.root)).toContain('Partial read · 500 of 1005 candidates');
    expect(mounted!.root.findByType(AnalyticsDashboard).props.nativeCohort.rows).toHaveLength(500);
    await click('Select all matching (500)'); await click('Export native JSON (500)');
    const reader = new FileReader(); const result = new Promise<string>(resolve => { reader.onload = () => resolve(String(reader.result)); }); reader.readAsText(blobs.at(-1)!);
    expect(JSON.parse(await result)).toMatchObject({ complete: false, loaded: 500, total: 1005 });
    failOffset = undefined; await click('Retry Shape readback');
    expect(text(mounted!.root)).toContain('Entire cohort · 1005 candidates');
    expect(mounted!.root.findByType(AnalyticsDashboard).props.nativeCohort.selectedIds).toHaveLength(500);
});

it('hydrates exact alternate CIF before inspection, preserves runtime on tool expansion, and never substitutes an unavailable selector', async () => {
    rows = [fixture(0)]; await mount('/designs/shape?design_id=id-0&shape_document=source_backbone');
    const viewer = () => mounted!.root.findByType(StructureWorkbench);
    const instance = viewer();
    expect(instance.props).toMatchObject({ format: 'cif', structureUrl: expect.stringContaining('source-0.cif'), hideControls: false, alphafoldView: false });
    expect(mounted!.root.findByType(AnalyticsDashboard).props.nativeCohort.selectedIds).toEqual([]);
    await click('Measurements and exports'); expect(viewer()).toBe(instance); expect(viewer().props.workbenchCollapsed).toBe(false);
    await change('Shape native document', 'predictor_sample');
    expect(viewer().props.structureUrl).toContain('prediction-0.cif');
    await change('Shape native document', 'not-published');
    expect(mounted!.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    expect(text(mounted!.root)).toContain('primary structure is not substituted');
});

it.each(['standalone', 'context'])('selected native document reaches the mounted receiver and deliberate %s destination through materialization transport', async destination => {
    rows = [fixture(0)]; await mount('/designs/shape?design_id=id-0&shape_document=source_backbone&launch_context_id=deliberate-context');
    await change('Shape continuation destination', destination);
    await click('Redesign selected structure');
    expect(calls.filter(call => call.url === '/api/files/materialize-structure').map(call => call.body)).toEqual([
        { job_id: 'shape', design_id: 'id-0', document: { artifact_id: 'source-0' }, expected_sha256: 'b'.repeat(64), model_number: 7, output_format: 'native' },
        { path: 'inputs/exact-native.cif', expected_sha256: 'b'.repeat(64), model_number: 7, output_format: 'pdb' },
    ]);
    expect(calls.filter(call => call.url === '/api/files/materialize-structure').map(call => call.context)).toEqual(destination === 'context' ? ['deliberate-context', 'deliberate-context'] : [undefined, undefined]);
    expect(mounted!.root.findByType(ProteinModificationTemplate).props.initialValues).toMatchObject({ input_structure: 'inputs/exact-derived.pdb', source_structure: { design_id: 'id-0', document: { artifact_id: 'source-0' } }, _source_native_prepared: { format: 'cif' } });
    expect(mounted!.root.findByType(ProteinLocalRedesignTemplate).props.initialValues).toMatchObject({ input_structure: 'inputs/exact-derived.pdb' });
    expect(calls.some(call => call.url === '/api/jobs' && call.body)).toBe(false);
});

it('explicit artifact links never transiently mount primary bytes and primary reset is deliberate', async () => {
    rows = [fixture(0)]; await mount('/designs/shape?design_id=id-0&artifact_id=sample-0&target_state=native-state');
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toContain('prediction-0.cif');
    await change('Shape native document', 'structure');
    expect(mounted!.root.findByType(StructureWorkbench).props.structureUrl).toContain('primary-0.pdb');
    expect(mounted!.root.findByProps({ 'data-location': '/designs/shape?design_id=id-0&shape_document=structure' })).toBeDefined();
});

it('zero-yield readback stays usable without manufacturing metrics or a structure', async () => {
    rows = []; await mount();
    expect(text(mounted!.root)).toContain('No persisted Shape candidates were published');
    expect(mounted!.root.findAllByType(StructureWorkbench)).toHaveLength(0);
    expect(mounted!.root.findByType(AnalyticsDashboard).props.nativeCohort.rows).toEqual([]);
});

it('metric projection preserves stage, native sample identities, zeros, nulls and exact CSV values', () => {
    const cohort = shapeCohort([fixture(0), fixture(1)]);
    expect(cohort[0].values['primary (esmfold2).shape_total']).toBe(0);
    expect(cohort[0].values['post-refold.post_refold.ca_rmsd_angstrom']).toBe(0);
    expect(cohort[0].values['prediction boltz2 [s0].metrics.ptm']).toBeNull();
    expect(cohort[0].values['prediction protenix_v2 [s1].status']).toBe('read_unavailable');
    expect(filterShapeCohort(cohort, '', { key: 'primary (esmfold2).shape_total', kind: 'null', min: '', max: '' }).map(row => row.id)).toEqual(['id-1']);
    expect(shapeCsv(cohort, ['primary (esmfold2).shape_total'])).toContain('"id-0","fixture-0000","0"');
    expect(shapeDocuments(fixture(0))[1].source).toMatchObject({ document: { artifact_id: 'source-0' }, model_number: 7 });
});
