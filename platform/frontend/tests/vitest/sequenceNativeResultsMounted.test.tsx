import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { nativeSequenceResultKind } from '../../src/components/NativeSequenceResults';

vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: object) => <div data-testid="native-cif-viewer">{JSON.stringify(props)}</div> }));
vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));

// Real Python emitters + model-owned readers, inert scientific kernels only.
// Never execution/scientific acceptance: even the test structure bytes are inert.
const emitted = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', String.raw`
import copy, json, runpy, sys, tempfile, types
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root / 'platform/api'))
from services.caliby_native import normalize_request, read_native_results
from services.ligandmpnn_design import prepare_design_request, read_design_result
runtime = types.ModuleType('caliby_runtime')
runtime.dump_json = lambda p, d: Path(p).write_text(json.dumps(d))
runtime.preflight_caliby_runtime = lambda **k: {'model_name': k.get('packer_model_name') or k['model_name'], 'fixture': True}
class Model:
    sampling_cfg = {}
    def emit(self, sources, out_dir, packing=False):
        path = Path(out_dir); path.mkdir(parents=True)
        result = {'example_id': [], 'out_pdb': []}
        if not packing: result.update(seq=[], input_seq=[], U=[])
        for i, source in enumerate(sources):
            file = path / ('sample_%s.cif' % i); file.write_text('data_INERT_TEST_ONLY\n')
            result['example_id'].append(Path(source).stem); result['out_pdb'].append(str(file))
            if not packing:
                result['seq'].append('AG'); result['input_seq'].append('AA'); result['U'].append(-1.25)
        return result
    def ensemble_sample(self, mapping, out_dir, **kwargs): return self.emit([s[0] for s in mapping.values()], out_dir)
    def sidechain_pack(self, sources, out_dir, **kwargs): return self.emit(sources, out_dir, True)
runtime.load_caliby_model = lambda name: Model(); sys.modules['caliby_runtime'] = runtime
api = types.ModuleType('caliby.api'); api._merge_sampling_cfg = lambda cfg, **kw: kw
sys.modules['caliby.api'] = api
omega = types.ModuleType('omegaconf'); omega.OmegaConf = types.SimpleNamespace(to_container=lambda cfg, **kw: cfg); sys.modules['omegaconf'] = omega
pandas = types.ModuleType('pandas'); pandas.DataFrame = lambda rows: rows; sys.modules['pandas'] = pandas
caliby = runpy.run_path(str(root / 'scripts/run_caliby_experimental.py'))
ligand = runpy.run_path(str(root / 'scripts/run_ligandmpnn_design.py'))
ligand['importlib'].metadata.version = lambda name: 'inert-test'
class Result:
    def __init__(self, options):
        self.input_dict = options
        self.output_dict = {'model_type': 'ligand_mpnn', 'batch_idx': 7, 'design_idx': 11, 'designed_sequence': 'AG', 'sequence_recovery': 0, 'ligand_interface_sequence_recovery': float('nan')}
    def write_structure(self, *, base_path): Path(base_path).with_suffix('.cif').write_text('data_INERT_TEST_ONLY\n')
    def write_fasta(self, *, base_path): Path(base_path).with_suffix('.fa').write_text('>INERT_TEST_ONLY\nAG\n')
class Engine:
    def __init__(self, **kwargs): pass
    def run(self, *, input_dicts, atom_arrays): return [Result(input_dicts[0])]
results = {}
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    for mode in ('ensemble_design', 'sidechain_pack'):
        reqdir = tmp / mode; reqdir.mkdir()
        states = [{'state_id': 'state-primary', 'path': 'state_000000.cif'}, {'state_id': 'state-conditioning', 'path': 'state_000001.cif'}]
        params = {'ensembles': [{'ensemble_id': 'explicit-group', 'states': states}]} if mode == 'ensemble_design' else {'structures': states[:1]}
        requested = normalize_request(mode, params)
        (reqdir / 'request.json').write_text(json.dumps({'requested': requested, 'effective': requested, 'sources': states}))
        output = tmp / (mode + '-output'); caliby['run'](reqdir, output)
        doc = read_native_results(output)
        assert set(doc['records'][0]) == {'record_id', 'native', 'structure_path', 'source', 'operation'}
        assert set(doc['records'][0]['native']) == ({'example_id', 'out_pdb'} if mode == 'sidechain_pack' else {'example_id', 'out_pdb', 'seq', 'input_seq', 'U'})
        results[mode] = doc
    source = tmp / 'context.cif'; source.write_text('data_INERT_SOURCE\n')
    for mode in ('ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'):
        request = prepare_design_request(mode, {'target_pdb': str(source), 'design_seed': 0, 'temperature': None})
        output = tmp / mode; ligand['execute'](request, source, output, engine_class=Engine)
        doc = read_design_result(output)
        assert set(doc['records'][0]) == {'producer', 'source_sha256', 'native_input', 'native_output', 'artifacts'}
        results[mode] = doc
print(json.dumps(results, allow_nan=False))
`, resolve('../..')], { encoding: 'utf8' }));

let root: Root, container: HTMLDivElement, client: QueryClient;
const adapter = api.defaults.adapter;
beforeEach(() => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); document.body.replaceChildren(); });
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const calls: string[] = [];
async function mount(mode: string, generic: 'empty' | 'error' = 'empty', options: { zero?: boolean; structuresOff?: boolean; nativeError?: boolean; topOnly?: boolean; prefixed?: boolean; missingSource?: boolean } = {}) {
    const caliby = ['ensemble_design', 'sidechain_pack'].includes(mode);
    const job = { id: 'fixture-job', name: 'INERT native results', model_id: caliby ? 'caliby_experimental' : 'ligandmpnn', mode, status: 'completed', params: {}, design_count: 0, created_at: '2026-01-01T00:00:00Z' };
    const doc = structuredClone(emitted[mode]);
    // Decoration matches the existing publication reader, not a path-to-URL guess by the UI.
    const directory = caliby ? 'caliby_native' : 'ligandmpnn_design';
    const file = (path: string) => ({ path, relative_path: options.prefixed ? `${directory}/${path}` : path, artifact_id: `owned-${path}`, download_url: `/api/files/download/jobs/fixture-job/${directory}/${path}`, stream_url: `/api/files/stream/jobs/fixture-job/${directory}/${path}` });
    doc.artifacts = [file(caliby ? 'caliby_results.json' : 'manifest.json')];
    if (caliby) doc.records.forEach((row: any) => { row.structure = file(row.structure_path); });
    else {
        doc.schema = doc.contract;
        doc.records.forEach((row: any) => { row.artifacts = row.artifacts.map((a: any) => ({ ...a, ...file(a.path) })); });
    }
    if (options.zero) doc.records = [];
    if (options.structuresOff) { doc.request.write_structures = false; doc.records.forEach((r: any) => { r.artifacts = r.artifacts.filter((a: any) => a.kind !== 'structure'); }); }
    if (options.topOnly) doc.records.forEach((row: any) => {
        if (caliby) { doc.artifacts.push(row.structure); delete row.structure; }
        else row.artifacts.forEach((a: any) => { doc.artifacts.push(file(a.path)); delete a.download_url; delete a.stream_url; });
    });
    if (options.missingSource && caliby) doc.records.forEach((row: any) => { row.source = null; });
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url); calls.push(url); let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith('/caliby-native-results') || url.endsWith('/ligandmpnn-design-results')) {
            if (options.nativeError) throw new Error('native publication unavailable'); data = doc;
        } else if (url === '/api/designs') { if (generic === 'error') throw new Error('generic Designs unavailable'); data = { designs: [], total: 0, model_counts: {} }; }
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => root.render(<MemoryRouter initialEntries={['/designs/fixture-job']}><QueryClientProvider client={client}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>)); await flush();
    return doc;
}

it.each(['ensemble_design', 'sidechain_pack', 'ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'])('reopens emitted %s records/settings without Designs and passes exact governed CIF to the shared viewer', async mode => {
    const doc = await mount(mode);
    expect(container.textContent).toContain('1 native records');
    expect(container.textContent).not.toContain('0 designs');
    const article = container.querySelector('article')!;
    if (mode === 'ensemble_design') { expect(article.textContent).toContain('explicit-group'); expect(article.textContent).toContain('state-conditioning'); expect(article.textContent).toContain('-1.25'); }
    else if (mode === 'sidechain_pack') { expect(article.textContent).not.toContain('input_seq'); expect(article.querySelector('dt')?.textContent).toBe('state_id'); }
    else { expect(article.textContent).toContain('batch 7'); expect(article.textContent).toContain('design 11'); expect(article.textContent).toContain('ligand_interface_sequence_recoveryNot reported'); expect(article.textContent).toContain('sequence_recovery0'); expect(container.textContent).toContain('temperatureNot reported'); }
    const button = Array.from(article.querySelectorAll('button')).find(b => b.textContent?.startsWith('View CIF'))!;
    await act(async () => button.click());
    const expected = doc.records[0].structure?.download_url ?? doc.records[0].artifacts.find((a: any) => a.kind === 'structure').download_url;
    expect(JSON.parse(container.querySelector('[data-testid="native-cif-viewer"]')!.textContent!)).toMatchObject({ structureUrl: expected.replace('/download/', '/stream/'), format: 'cif', artifactJobId: 'fixture-job' });
    expect(article.querySelector('a')?.getAttribute('href')).toBe(expected);
    expect(container.querySelector('a[download]')).not.toBeNull();
});
it.each(['ensemble_design', 'ligand_aware'])('shows %s independently of a failed generic Design query', async mode => {
    await mount(mode, 'error'); expect(calls).toContain('/api/designs'); expect(container.textContent).toContain('1 native records'); expect(container.textContent).not.toContain('Results could not be loaded');
});
it.each(['ensemble_design', 'sidechain_pack', 'dna_aware'])('retains settings/files for valid zero-record %s output', async mode => {
    await mount(mode, 'error', { zero: true }); expect(container.textContent).toContain('0 native records'); expect(container.textContent).toContain('No native records were emitted'); expect(container.querySelector('a[download]')).not.toBeNull(); expect(container.querySelector('article')).toBeNull();
});
it('retains LigandMPNN sequence and missing measurements when structure writing is off', async () => {
    await mount('metal_aware', 'empty', { structuresOff: true }); expect(container.textContent).toContain('Structure writing was off'); expect(container.textContent).toContain('designed_sequenceAG'); expect(container.textContent).toContain('Not reported'); expect(container.textContent).not.toContain('View CIF'); expect(container.querySelector('article a')?.textContent).toContain('.fa');
});
it('reports native read failure without substituting empty generic Designs', async () => {
    await mount('sidechain_pack', 'empty', { nativeError: true }); expect(container.textContent).toContain('native publication unavailable'); expect(container.textContent).not.toContain('0 native records'); expect(container.textContent).not.toContain('0 designs');
});
it.each([['ensemble_design', false], ['sidechain_pack', true], ['ligand_aware', false], ['dna_aware', true]] as const)('resolves %s top-level governed handles with explicit prefix=%s', async (mode, prefixed) => {
    const doc = await mount(mode, 'error', { topOnly: true, prefixed, missingSource: true });
    const article = container.querySelector('article')!;
    const button = Array.from(article.querySelectorAll('button')).find(b => b.textContent?.startsWith('View CIF'))!;
    expect(button).toBeDefined(); await act(async () => button.click());
    expect(JSON.parse(container.querySelector('[data-testid="native-cif-viewer"]')!.textContent!).structureUrl).toBe(doc.artifacts.find((a: any) => a.relative_path.endsWith('.cif')).stream_url);
    expect(container.textContent).not.toContain('Selection unavailable');
});
it('routes only the exact restored and ordinary modes, leaving historical and diagnostic routes unchanged', () => {
    for (const [model_id, mode] of [['caliby_experimental', 'design'], ['caliby_binder', 'design'], ['ligandmpnn', 'interface_context'], ['fampnn', 'design'], ['proteinmpnn', 'ensemble_design']]) expect(nativeSequenceResultKind({ model_id, mode })).toBeNull();
});
