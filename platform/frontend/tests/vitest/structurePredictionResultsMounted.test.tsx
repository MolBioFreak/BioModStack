import React from 'react';
import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { api } from '../../src/lib/api';
import { AnalyticsDashboard } from '../../src/components/AnalyticsDashboard';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { confidenceProfile, isStandaloneStructurePrediction, StructurePredictionResults } from '../../src/components/StructurePredictionResults';
import { esmfold2Fixture } from '../fixtures/scientificViewerEsmfold2Fixture';
import { nativeAtomFixture, unavailable } from '../fixtures/nativeAtomViewerFixture';

vi.mock('react-plotly.js', () => ({ default: (props: any) => <div data-plot={props} /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-native-url={props.structureUrl} data-native-queries={props.scenePresentation?.colorQueries} /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => null }));
let tree: ReactTestRenderer | undefined;
let client: QueryClient;
const adapter = api.defaults.adapter;
const calls: { url: string; method: string }[] = [];
const text = (n: any): string => typeof n === 'string' ? n : (n.children ?? []).map(text).join('');
const flush = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(r => setTimeout(r, 40)); }); };
const plots = () => tree!.root.findAll(n => n.type === 'div' && !!n.props['data-plot']).map(n => n.props['data-plot']);
const heatmap = () => plots().find(p => p.data[0]?.type === 'heatmap' && p.data[0]?.zmax === 30);
const profilePlot = () => plots().find(p => p.layout.yaxis?.range?.[1] === 100);
const a = nativeAtomFixture();
function other() {
    const b = nativeAtomFixture();
    b.document = { ...b.document, candidateId: 'other', contentSha256: 'b'.repeat(64) };
    for (const payload of [b.pae, b.confidence]) {
        payload.design_id = 'other'; payload.document = b.document;
        for (const axis of [payload.axis, payload.row_axis, payload.column_axis].filter(Boolean)) axis.source_sha256 = b.document.contentSha256;
    }
    b.confidence.values[0] = 0.42;
    b.pae.pae_matrix[0][1] = 27;
    return b;
}
const b = other();
const designs: any[] = [a, b].map((p, i) => ({ id: p.document.candidateId, job_id: 'job', name: `sample ${i}`, pdb_path: `sample-${i}.cif`, scientific_structure_document: p.document, core_protein_scientific_contract: 1, analysis_contract_id: 'structure_prediction_v1', review_artifact_manifest: { schema: 'bms.review-artifacts.v1', artifacts: { structure: { state: 'ready' } } }, review_profile_id: 'structure_prediction_v1', viewer_capabilities: ['structure_viewer'], supported_analyzers: [], provenance: { model_id: 'protenix', producer_model_id: 'protenix' }, confidence_metrics: { plddt: 90 - i, ptm: 0.9, iptm: 0.8 }, plddt_overall: 90 - i, ptm: 0.9 }));
const job: any = { id: 'job', name: 'Prediction fixture', model_id: 'protenix', mode: 'complex', status: 'completed', design_count: 2, params: {}, created_at: '2026-09-01T00:00:00Z' };
function transport({ missing = false, legacy = false, canonical = false, residueConfidence = false, paeRequestFails = false, legacyResidueOnly = false, sampledTokens = false, missingReason = 'full_pae_not_requested' } = {}) {
    calls.length = 0;
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    const metricData = (url: string) => {
        const p = residueConfidence ? esmfold2Fixture(url.includes('/other/') ? 'other' : 'candidate', url.includes('/other/') ? 1 : 0) : url.includes('/other/') ? b : a;
        if (residueConfidence) {
            const expected = url.includes('/other/') ? b.document : a.document;
            p.document = expected; p.confidence.document = expected; p.pae.document = expected;
            p.confidence.axis.source_sha256 = expected.contentSha256;
            p.pae.row_axis.source_sha256 = expected.contentSha256; p.pae.column_axis.source_sha256 = expected.contentSha256;
        }
        if (url.includes('residue-metrics')) return legacy ? { design_id: p.document.candidateId, length: 1, residue_numbers: [1], plddt: [80] } : p.confidence;
        if (url.includes('chain-metrics') && legacyResidueOnly) return {};
        if (url.includes('chain-metrics')) return legacy ? { A: { type: 'protein', length: 2, avg_plddt: 85, plddt: [80,90], residue_numbers: [10,12] } } : unavailable('chain_metrics');
        if (sampledTokens) {
            p.pae.sampled_row_indices = [0,4]; p.pae.sampled_column_indices = [0,4]; p.pae.size = 2;
            p.pae.pae_matrix = [0,4].map(r => [0,4].map(c => p.pae.pae_matrix[r][c]));
        }
        if (url.includes('/pae')) return missing ? unavailable('pae', missingReason) : legacy ? { pae_matrix: [[0,4],[8,0]], size: 2 } : p.pae;
        return null;
    };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => metricData(url) })));
    api.defaults.adapter = async config => {
        const url = String(config.url); calls.push({ url, method: config.method ?? 'get' });
        if (paeRequestFails && url.endsWith('/pae')) throw Error('Full PAE not found');
        let data: any = metricData(url) ?? {};
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/job') data = job;
        else if (url === '/api/designs' || url === '/api/designs/query') data = { designs, total: 2, model_counts: { protenix: 2 } };
        else if (designs.some(d => url === `/api/designs/${d.id}`)) data = designs.find(d => url === `/api/designs/${d.id}`);
        else if (url.endsWith('/plotly-metrics')) {
            const points = canonical ? designs.map(d => ({ id: d.id, name: d.name, contract_revision: 1, source_job_id: 'job', cohort_key: 'v1:p:job', metrics: { ptm: 0.9 }, metric_states: { ptm: { state: 'ok', value: 0.9, reason_code: null } }, metric_sources: { ptm: { artifact_sha256: 'a'.repeat(64), candidate_id: d.id, document_id: 'primary' } }, metric_descriptors: { ptm: { metric_id: 'ptm', source: 'canonical_artifact', producer_version: 'fixture', derivation_version: 'fixture', scope: 'overall', unit: 'dimensionless', direction: 'higher' } } })) : [];
            data = { job_id: 'job', metric_keys: ['ptm'], points, total: points.length, scientific_cohorts: [] };
        }
        else if (url.endsWith('/analyses')) data = { analyses: [], available_analyses: [] };
        else if (url.endsWith('/children') || url.endsWith('/execution-targets')) data = [];
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
}
async function mount(element: React.ReactNode) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}>{element}</QueryClientProvider>); });
    await flush();
}
afterEach(async () => { await act(async () => tree?.unmount()); tree = undefined; client?.clear(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); sessionStorage.clear(); });

it.each([false, true])('standalone analytics preserves the real profile in canonical=%s and legacy scalar routes', async canonical => {
    transport({ canonical });
    function Selected() {
        const [id, select] = React.useState(designs[0].id);
        return <AnalyticsDashboard modelId="protenix" jobId="job" designs={designs} selectedDesignId={id} onSelectDesign={select} structure={<div data-document={id} />} />;
    }
    await mount(<Selected />);
    const scalar = tree!.root.findAllByType('details').find(d => text(d.findAllByType('summary')[0]).startsWith('Exploratory scalar'))!;
    await act(async () => scalar.props.onToggle({ currentTarget: { open: true } })); await flush();
    expect(profilePlot().data[0].y).toEqual([90]);
    expect(profilePlot().layout.shapes.map((s: any) => [s.y0,s.y1])).toEqual([[90,100],[70,90],[50,70],[0,50]]);
    expect(profilePlot().layout.hovermode).toBe('x unified');
    expect(profilePlot().config.toImageButtonOptions.format).toBe('svg');
    expect(heatmap().data[0].z).toEqual(a.pae.pae_matrix);
    expect(heatmap().data[0].x[3]).toContain('E:1 GTP / C1');
    await act(async () => tree!.root.findByProps({ 'aria-label': 'Selected prediction' }).props.onChange({ target: { value: 'other' } })); await flush();
    expect(tree!.root.findByProps({ 'data-document': 'other' })).toBeTruthy();
    expect(profilePlot().data[0].y).toEqual([42]);
    expect(heatmap().data[0].z[0][1]).toBe(27);
    await act(async () => tree!.root.findByProps({ 'aria-label': 'Row chain' }).props.onChange({ target: { value: 'E' } }));
    expect(heatmap().data[0].z).toEqual(b.pae.pae_matrix.slice(3));
    expect(calls.filter(c => c.method !== 'get').every(c => c.url.endsWith('/plotly-metrics'))).toBe(true);
    expect(text(tree!.root)).toContain('Exploratory scalar analytics and Plotly Lab');
    if (canonical) expect(tree!.root.findAllByProps({ 'aria-label': 'Plotly Lab' }).length).toBeGreaterThan(0);
});
it('native atom profiles explicitly select protein CA; ligand atoms and DNA CA are not averaged', () => {
    const result = confidenceProfile(a.confidence, designs[0]);
    expect(Object.keys(result.chains)).toEqual(['A']);
    expect(result.chains.A.plddt).toEqual([90]);
    expect(result.description).toContain('no atom averaging');
});
it('not-requested PAE leaves native structure and profile usable, and comparison retains a fixed scale', async () => {
    transport({ missing: true });
    await mount(<AnalyticsDashboard modelId="boltz_cp_experimental" designs={designs} structure={<div data-structure />} />);
    expect(text(tree!.root)).toContain('Full PAE was not requested');
    expect(tree!.root.findByProps({ 'data-structure': true })).toBeTruthy();
    expect(profilePlot().data[0].y).toEqual([90]);
    expect(heatmap()).toBeUndefined();
    expect(calls.every(c => c.method === 'get')).toBe(true);
});
it('an explicit saved output flag explains an absent legacy full PAE without treating unknown flags as false', async () => {
    transport({ paeRequestFails: true });
    await mount(<AnalyticsDashboard modelId="boltz_cp_experimental" fullPaeRequested={false} designs={designs} structure={<div data-structure />} />);
    expect(text(tree!.root)).toContain('Full PAE was not requested for this prediction.');
    expect(profilePlot().data[0].y).toEqual([90]);
    expect(calls.every(c => c.method === 'get')).toBe(true);
});
it('legacy spatial payloads retain residue numbers and asymmetric matrix without inventing chain mapping', async () => {
    transport({ legacy: true });
    const legacy = { ...designs[0], scientific_structure_document: undefined, core_protein_scientific_contract: undefined };
    await mount(<AnalyticsDashboard modelId="boltz2" designs={[legacy]} structure={<div />} />);
    expect(profilePlot().data[0].x).toEqual([10,12]);
    expect(heatmap().data[0].z).toEqual([[0,4],[8,0]]);
    expect(heatmap().layout.xaxis.title.text).toContain('mapping unavailable');
});
it('legacy residue-only confidence is visible without inventing a chain identity', async () => {
    transport({ legacy: true, legacyResidueOnly: true });
    const legacy = { ...designs[0], scientific_structure_document: undefined, core_protein_scientific_contract: undefined };
    await mount(<AnalyticsDashboard modelId="boltz2" designs={[legacy]} structure={<div />} />);
    expect(profilePlot().data[0].x).toEqual([1]);
    expect(profilePlot().data[0].y).toEqual([80]);
    expect(profilePlot().data[0].name).toBe('Legacy profile (chain mapping unavailable)');
});
it('fixed-scale comparison is lazy and every tile keeps its own native values', async () => {
    transport(); await mount(<AnalyticsDashboard modelId="protenix" designs={designs} structure={<div />} />);
    expect(calls.some(c => c.url === '/api/designs/other/pae')).toBe(false);
    const disclosure = tree!.root.findAllByType('details').find(d => text(d.findAllByType('summary')[0]).startsWith('Compare native PAE'))!;
    await act(async () => disclosure.props.onToggle({ currentTarget: { open: true } })); await flush();
    const maps = plots().filter(p => p.data[0]?.type === 'heatmap' && p.data[0].zmax === 30);
    expect(maps).toHaveLength(3);
    expect(maps[2].data[0].z).toEqual(b.pae.pae_matrix);
});
it.each(['esmfold2', 'esmfold2_experimental'])('%s uses native residue confidence, not a scalar token mean', async modelId => {
    transport({ residueConfidence: true });
    await mount(<AnalyticsDashboard modelId={modelId} designs={[designs[0]]} structure={<div />} />);
    expect(profilePlot().data.map((trace: any) => trace.y)).toEqual([[.5],[80],[40]]);
    expect(profilePlot().data[0].x).toEqual(['42A']);
    expect(heatmap().data[0].z).toEqual(esmfold2Fixture().pae.pae_matrix);
    expect(heatmap().data[0].x).toEqual(['Token 0','Token 1','Token 2','Token 3','Token 4']);
    expect(tree!.root.findAllByProps({'aria-label':'Row chain'})).toHaveLength(0);
    expect(text(tree!.root)).toContain('native_token_to_structure_mapping_unavailable');
    expect(text(tree!.root)).toContain('native_output_order');
    expect(text(tree!.root)).toContain('Native collapsed-CIF-residue pLDDT');
    expect(text(tree!.root)).toContain('Not a token-mean summary');
    expect(text(tree!.root)).not.toContain('Native Cα atom pLDDT');
});
it('native token clicks never emit scene selections; collapsed CIF profile selections still do', async () => {
    transport({residueConfidence: true});
    await mount(<MemoryRouter initialEntries={['/designs/job?design_id=candidate&tab=charts']}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></MemoryRouter>);
    await flush();
    const engine = () => tree!.root.findAll(n => n.type === 'div' && !!n.props['data-native-url'])[0];
    const focused = () => engine().props['data-native-queries'].filter((query: any) => query.color?.g === 185);
    expect(focused()).toEqual([]);
    await act(async () => heatmap().onClick({points:[{x:'Token 4',y:'Token 0'}]}));
    expect(focused()).toEqual([]);
    expect(tree!.root.findAllByProps({'aria-label':'Confidence selection'})).toHaveLength(0);
    await act(async () => profilePlot().onClick({points:[{customdata:profilePlot().data[0].customdata[0]}]}));
    expect(focused().length).toBeGreaterThan(0);
    expect(text(tree!.root.findByProps({'aria-label':'Confidence selection'}))).toContain('A:42A ALA');
    const before = focused();
    await act(async () => heatmap().onClick({points:[{x:'Token 1',y:'Token 2'}]}));
    expect(focused()).toEqual(before);
    const disclosure = tree!.root.findAllByType('details').find(d => text(d.findAllByType('summary')[0]).startsWith('Compare native PAE'))!;
    await act(async () => disclosure.props.onToggle({currentTarget:{open:true}})); await flush();
    const maps = plots().filter(p => p.data[0]?.type === 'heatmap' && p.data[0].zmax === 30);
    expect(maps).toHaveLength(3);
    expect(maps[2].data[0].z).toEqual(esmfold2Fixture('other',1).pae.pae_matrix);
    expect(maps[2].data[0].x[4]).toBe('Token 4');
    await act(async () => tree!.root.findByProps({'aria-label':'Selected prediction'}).props.onChange({target:{value:'other'}})); await flush();
    expect(engine().props['data-native-url']).toBe('/api/designs/other/pdb');
    expect(focused()).toEqual([]);
    expect(heatmap().data[0].z).toEqual(esmfold2Fixture('other',1).pae.pae_matrix);
    expect(calls.filter(c=>c.method!=='get').every(c=>/plotly-metrics|designs\/query/.test(c.url))).toBe(true);
});
it.each(['native_pae_not_reported','not_retained_by_producer','missing_or_invalid_esmfold2_native_evidence'])('ESMFold2 %s retains the collapsed confidence chart and structure', async missingReason => {
    transport({residueConfidence:true,missing:true,missingReason});
    await mount(<AnalyticsDashboard modelId="esmfold2" designs={[designs[0]]} structure={<div data-structure />} />);
    expect(heatmap()).toBeUndefined();
    expect(text(tree!.root)).toContain(missingReason);
    expect(profilePlot().data.map((trace: any)=>trace.y)).toEqual([[.5],[80],[40]]);
    expect(tree!.root.findByProps({'data-structure':true})).toBeTruthy();
    expect(calls.every(c=>c.method==='get')).toBe(true);
});
it('sampled token plots retain source indices instead of relabeling as residues or dense positions', async () => {
    transport({residueConfidence:true,sampledTokens:true});
    await mount(<AnalyticsDashboard modelId="esmfold2" designs={[designs[0]]} structure={<div />} />);
    expect(heatmap().data[0].x).toEqual(['Token 0','Token 4']);
    expect(heatmap().data[0].y).toEqual(['Token 0','Token 4']);
    expect(heatmap().data[0].z).toEqual([[0,2],[10,12]]);
    expect(heatmap().layout.xaxis.constrain).toBe('domain');
    expect(heatmap().layout.yaxis.constrain).toBe('domain');
    expect(heatmap().layout.yaxis.scaleanchor).toBe('x');
});
it('scope excludes conformational mapping, ConforNets and historical launchers', () => {
    for (const id of ['conformational_mapping','confornets_experimental','rf3','alphafold2']) expect(isStandaloneStructurePrediction(id)).toBe(false);
    for (const id of ['esmfold2','esmfold2_experimental','boltz_cp_experimental']) expect(isStandaloneStructurePrediction(id)).toBe(true);
});
it.each(['Charts', 'Structure'])('real Results %s routing mounts shared structure and selection changes both', async view => {
    transport();
    await mount(<MemoryRouter initialEntries={['/designs/job?design_id=candidate&tab=charts']}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></MemoryRouter>);
    await flush();
    const charts = tree!.root.findAllByType('button').find(n => text(n).endsWith(view));
    expect(charts).toBeTruthy();
    await act(async () => charts!.props.onClick()); await flush();
    expect(tree!.root.findAllByProps({ 'aria-label': 'Structure prediction confidence' })).toHaveLength(1);
    expect(profilePlot().data[0].y).toEqual([90]);
    const engine = () => tree!.root.findAll(n => n.type === 'div' && !!n.props['data-native-url'])[0];
    expect(engine().props['data-native-url']).toBe('/api/designs/candidate/pdb');
    await act(async () => heatmap().onClick({ points: [{ x: heatmap().data[0].x[3], y: heatmap().data[0].y[4] }] }));
    const focused = engine().props['data-native-queries'].filter((query: any) => query.color?.g === 185);
    expect(focused.map((query: any) => query.labelAtomIds)).toContainEqual(['C1']);
    expect(focused.map((query: any) => query.labelAtomIds)).toContainEqual(['C2']);
    expect(text(tree!.root.findByProps({ 'aria-label': 'Confidence selection' }))).toContain('GTP / C1');
    await act(async () => profilePlot().onClick({ points: [{ customdata: profilePlot().data[0].customdata[0] }] }));
    expect(engine().props['data-native-queries'].filter((query: any) => query.color?.g === 185).map((query: any) => query.labelAtomIds)).toEqual([['CA']]);
    await act(async () => tree!.root.findByProps({ 'aria-label': 'Selected prediction' }).props.onChange({ target: { value: 'other' } })); await flush();
    expect(profilePlot().data[0].y).toEqual([42]);
    expect(tree!.root.findAll(n => n.type === 'div' && n.props['data-native-url'] === '/api/designs/other/pdb').length).toBeGreaterThan(0);
    expect(calls.filter(c => c.method !== 'get').every(c => /plotly-metrics|designs\/query/.test(c.url))).toBe(true);
});

// Inert Boltz wire shape: producer identifiers are not the public Design/document IDs.
function boltzScalarWire() {
    const source = { candidate_id: 'native-sample', document_id: 'native-sample/model_0.pdb', artifact_sha256: 'a'.repeat(64) };
    const records = [
        { metric_key: 'ptm', value: 0.7550473809242249, unit: 'dimensionless', scope: 'overall' },
        { metric_key: 'complex_plddt', value: 0.7823242545127869, unit: 'fraction', scope: 'complex' },
    ].map(r => ({ ...r, state: 'ok', reason_code: null, source, producer_version: '7ebf1be087d4d61a02234c878402838bf3712d8b', derivation_version: 'boltz-native-scalar-v1', direction: 'higher_is_better' }));
    const design = { ...designs[0], plddt_overall: null, ptm: null, iptm: null,
        confidence_metrics: { core_protein_scientific_contract: 1, core_protein_scientific: {
            schema_name: 'boltz_scientific_design', schema_version: 1, design_id: designs[0].id,
            candidate_id: source.candidate_id, document_id: source.document_id,
            identity: { vector_unit: 'fraction', source_axis: 'boltz_native_tokens' },
            artifacts: { metrics: { path: 'confidence.json', sha256: source.artifact_sha256 }, structure: { path: 'model_0.pdb', sha256: a.document.contentSha256 } }, metrics: records,
        } } };
    const point: any = { id: design.id, name: design.name, contract_revision: 1, source_job_id: design.job_id, cohort_key: `v1:fixture:${design.job_id}`, publication_state: null,
        metrics: Object.fromEntries(records.map(r => [r.metric_key, r.value])),
        metric_states: Object.fromEntries(records.map(r => [r.metric_key, { state: r.state, value: r.value, reason_code: r.reason_code }])),
        metric_sources: Object.fromEntries(records.map(r => [r.metric_key, r.source])),
        metric_descriptors: Object.fromEntries(records.map(r => [r.metric_key, { metric_id: r.metric_key, source: 'canonical_artifact', scope: r.scope, unit: r.unit, direction: 'higher', producer_version: r.producer_version, derivation_version: r.derivation_version }])),
    };
    return { design, points: [point] };
}
function scalarTransport(points: unknown, fail = false) {
    transport();
    const rest = api.defaults.adapter as any;
    api.defaults.adapter = async config => {
        if (String(config.url).startsWith('/api/analytics/job/')) {
            calls.push({ url: String(config.url), method: config.method ?? 'get' });
            expect(config.params).toEqual({ include_children: false });
            if (fail) throw new Error('offline');
            return { config, data: points, status: 200, statusText: 'OK', headers: {} };
        }
        return rest(config);
    };
}
it('saved samples share a source-verified job read but never share selected scalars', async () => {
    const { design, points } = boltzScalarWire();
    const other = { ...design, id: 'other', name: 'other sample', scientific_structure_document: b.document };
    const second = structuredClone(points[0]); second.id = 'other'; second.name = other.name;
    second.metrics.ptm = 0.125; second.metric_states.ptm.value = 0.125;
    second.metrics.complex_plddt = 0.42; second.metric_states.complex_plddt.value = 0.42;
    scalarTransport([...points, second]);
    await mount(<StructurePredictionResults designs={[design, other]} />);
    expect(savedCells().map(row => row.slice(1).map(text))).toEqual([['78.23', '0.7550', '—'], ['42.00', '0.1250', '—']]);
    await act(async () => tree!.root.findByProps({ 'aria-label': 'Selected prediction' }).props.onChange({ target: { value: 'other' } })); await flush();
    expect(summary().findAllByType('dd').map(text)).toEqual(['0.1250', '42.00']);
    expect(profilePlot().data[0].y).toEqual([42]);
    expect(calls.filter(c => c.url.startsWith('/api/analytics/job/'))).toHaveLength(1);
});
const summary = () => tree!.root.findByProps({ 'aria-label': 'Native confidence summary' });
const savedCells = () => tree!.root.findAllByType('tr').filter(n => n.props['aria-selected'] !== undefined).map(row => row.findAllByType('td'));
it('native Boltz null-column wire renders canonical summary and saved row without inventing iPTM', async () => {
    // Optional retained real files exercise exactly the same mounted consumer, not a generated response.
    const wire = process.env.BMS_NATIVE_SCALAR_DESIGN && process.env.BMS_NATIVE_SCALAR_POINTS
        ? { design: JSON.parse(readFileSync(process.env.BMS_NATIVE_SCALAR_DESIGN, 'utf8')), points: JSON.parse(readFileSync(process.env.BMS_NATIVE_SCALAR_POINTS, 'utf8')) }
        : boltzScalarWire();
    expect(wire.design.plddt_overall).toBeNull(); expect(wire.design.ptm).toBeNull(); expect(wire.design.iptm).toBeNull();
    expect(wire.design.confidence_metrics.core_protein_scientific.metrics.map((r: any) => r.metric_key)).toEqual(['ptm', 'complex_plddt']);
    scalarTransport(wire.points);
    await mount(<StructurePredictionResults modelId="boltz2" designs={[wire.design]} structure={<div data-structure />} />);
    expect(summary().findAllByType('dd').map(text)).toEqual(['0.7550', '78.23']);
    expect(text(summary())).toContain('Complex pLDDT (0–100)');
    expect(text(summary())).not.toContain('iPTM');
    expect(savedCells()[0].slice(1).map(text)).toEqual(['78.23', '0.7550', '—']);
    expect(savedCells()[0][1].props.title).toContain('0.7823242545127869 fraction');
    expect(summary().findAllByType('dd')[0].props.title).toContain('0.7550473809242249');
    expect(calls.every(c => c.method === 'get')).toBe(true);
});
it.each([
    ['esmfold2', 'fraction', 'model_token_mean', 0.7823242545127869, 'Native token-mean'],
    ['esmfold2_experimental', 'fraction', 'model_token_mean', 0.7823242545127869, 'Native token-mean'],
    ['protenix', 'percent', 'model_atom_mean', 78.23242545127869, 'Native atom-mean'],
])('%s scalar scope and units remain distinct from the profile', async (model, unit, scope, value, label) => {
    if (model === 'protenix' && process.env.BMS_PROTENIX_SCALAR_DESIGNS && process.env.BMS_PROTENIX_SCALAR_POINTS) {
        const saved = JSON.parse(readFileSync(process.env.BMS_PROTENIX_SCALAR_DESIGNS, 'utf8')).designs;
        scalarTransport(JSON.parse(readFileSync(process.env.BMS_PROTENIX_SCALAR_POINTS, 'utf8')));
        await mount(<StructurePredictionResults modelId="protenix" designs={saved} />);
        expect(text(summary())).toContain('Native atom-mean pLDDT (0–100)');
        expect(savedCells()[0].slice(1).map(text)).toEqual(['62.03', '0.6251', '0.0000']);
        expect(savedCells()[0][1].props.title).toContain('62.0338020324707 percent');
        return;
    }
    const { design, points } = boltzScalarWire(); const point = points[0];
    point.metrics = { plddt: value, ptm: 0 };
    point.metric_states = { plddt: { state: 'ok', value, reason_code: null }, ptm: { state: 'ok', value: 0, reason_code: null } };
    point.metric_descriptors = { plddt: { ...point.metric_descriptors.complex_plddt, metric_id: 'plddt', unit, scope }, ptm: point.metric_descriptors.ptm };
    point.metric_sources = { plddt: point.metric_sources.complex_plddt, ptm: point.metric_sources.ptm };
    scalarTransport(points);
    await mount(<StructurePredictionResults modelId={String(model)} designs={[design]} />);
    expect(text(summary())).toContain(`${label} pLDDT (0–100)`);
    expect(savedCells()[0].slice(1).map(text)).toEqual(['78.23', '0.0000', '—']);
    expect(savedCells()[0][1].props.title).toContain(`${value} ${unit}`);
    expect(profilePlot().data[0].y).toEqual([90]);
});
it.each(['unavailable', 'invalid', 'wrong-design', 'wrong-job', 'wrong-document', 'request-failed', 'malformed', 'publication'])('native %s never falls back to contradictory legacy scores', async mode => {
    const { design, points } = boltzScalarWire();
    design.plddt_overall = 99; design.ptm = 0.99; design.iptm = 0.99;
    const point = points[0];
    if (mode === 'unavailable' || mode === 'invalid') {
        for (const key of Object.keys(point.metric_states)) point.metric_states[key] = { state: mode, value: null, reason_code: 'native_not_reported' };
        point.metrics = {};
    } else if (mode === 'wrong-design') point.id = 'other';
    else if (mode === 'wrong-job') { point.source_job_id = 'other-job'; point.cohort_key = 'v1:fixture:other-job'; }
    else if (mode === 'wrong-document') design.scientific_structure_document = { ...design.scientific_structure_document, candidateId: 'other' };
    else if (mode === 'malformed') point.metrics.ptm = 'not numeric';
    else if (mode === 'publication') point.publication_state = { state: 'invalid', value: null, reason_code: 'invalid_canonical_publication' };
    scalarTransport(points, mode === 'request-failed');
    await mount(<StructurePredictionResults designs={[design]} structure={<div data-structure />} />);
    expect(savedCells()[0].slice(1).map(text)).toEqual(['—', '—', '—']);
    expect(text(summary())).not.toContain('0.9900');
    expect(tree!.root.findByProps({ 'data-structure': true })).toBeTruthy();
    if (mode !== 'wrong-document') expect(profilePlot().data[0].y).toEqual([90]);
});
it.each(['boltz_cp_experimental', 'protenix'])('%s retained legacy scores remain available without canonical requests', async modelId => {
    transport({ legacy: true });
    const design = { ...designs[0], scientific_structure_document: undefined, core_protein_scientific_contract: undefined,
        confidence_metrics: { complex_plddt: 0.7823242545127869, ptm: 0.7550473809242249 }, plddt_overall: null, ptm: null, iptm: null };
    await mount(<StructurePredictionResults modelId={modelId} designs={[design]} />);
    expect(savedCells()[0].slice(1).map(text)).toEqual(['78.23', '0.7550', '—']);
    expect(calls.some(c => c.url.startsWith('/api/analytics/'))).toBe(false);
    expect(profilePlot().data[0].y).toEqual([80,90]);
});
