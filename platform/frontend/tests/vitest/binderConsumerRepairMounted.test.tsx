import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { CandidateRoundProgress } from '../../src/components/CandidateRoundProgress';
import { BinderPredictionEvidence } from '../../src/components/BinderPredictionEvidence';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { BinderResultComparison } from '../../src/components/BinderResultComparison';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';

// Transport fixtures only: no publication, inference or scientific values invented.
vi.mock('react-plotly.js', () => ({ default: () => <div /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
let tree: ReactTestRenderer, client: QueryClient;
const original = api.defaults.adapter;
let calls: any[], total: number, family: any[], returnedRoot: string, hold: boolean;
let held: (() => void)[];
let job: any;
const design = (i: number) => ({ id: `design-${i}`, job_id: 'producer', name: `retained-${i}`, stage_family: 'rfantibody', provenance: { model_id: 'rfantibody' }, viewer_capabilities: ['antibody_backbone_metrics'], supported_analyzers: [], pdb_path: null });
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const flush = async () => { for (let i = 0; i < 30; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
async function mount(element: React.ReactNode = <Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<MemoryRouter initialEntries={['/designs/parent']}><QueryClientProvider client={client}><ThemeProvider>{element}</ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
}
async function click(label: string) {
    const button = tree.root.findAllByType('button').find(n => text(n).includes(label));
    expect(button, label).toBeTruthy();
    await act(async () => button!.props.onClick()); await flush();
}
beforeEach(() => {
    calls = []; total = 9; returnedRoot = 'root'; hold = false; held = [];
    job = { id: 'parent', name: 'Transport fixture', model_id: 'template_antibody_denovo', mode: 'antibody_denovo_pipeline', status: 'completed', params: {}, design_count: total, created_at: '2026-09-01T00:00:00Z', lineage_root_job_id: 'root' };
    family = [job, { ...job, id: 'producer' }, { ...job, id: 'root', lineage_root_job_id: null }];
    localStorage.clear(); sessionStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })));
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    api.defaults.adapter = async config => {
        calls.push(config); const url = String(config.url), p = config.params ?? {}; let data: any = [];
        if (config.method !== 'get') throw Error(`Unexpected mutation ${url}`);
        if (url === '/api/jobs') data = { jobs: family, total: family.length };
        else if (url === '/api/jobs/parent') data = job;
        else if (url === '/api/designs') {
            if (hold) await new Promise<void>(resolve => held.push(resolve));
            data = { designs: Array.from({ length: Math.min(p.limit ?? 100, total - (p.offset ?? 0)) }, (_, i) => design(i + (p.offset ?? 0))), total, model_counts: { rfantibody: total } };
        } else if (/\/api\/designs\/design-\d+$/.test(url)) data = design(Number(url.split('-').at(-1)));
        else if (url.endsWith('/binder-evidence')) data = { schema_version: 1, job_id: url.split('/')[4], offset: p.offset, limit: 100, total: p.source_design_id ? 1 : 0, records: p.source_design_id ? [{ source_design_id: p.source_design_id, candidate_key: null, sequences: [] }] : [] };
        else if (url.endsWith('/round')) data = { job_id: returnedRoot, state: 'not_requested', steps: {}, errors: {} };
        else if (url.startsWith('/api/models/')) data = { params: [] };
        else if (url.includes('/integration')) data = { enabled: false };
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { held.forEach(r => r()); await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = original; vi.unstubAllGlobals(); });

it('cold Results preserves independent comparison choices, but an explicit table clear still clears them', async () => {
    const key = 'binder-result-comparison::parent';
    const saved = { jobs: ['parent'], designs: ['design-1'], native: [{ job_id: 'producer', artifact_id: 'exact-native' }], documents: { 'design-1': { artifact_id: 'exact-design', target_state: 'native-state' } }, view: 'designs' };
    localStorage.setItem(key, JSON.stringify(saved));
    await mount();
    const props = tree.root.findByType(BinderResultComparison).props;
    expect(props.selectedDesignIds).toBeUndefined();
    expect(props.selectedNativeSources).toBeUndefined();
    expect(props.candidateDocuments).toBeUndefined();
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual(saved);
    // Exercise the actual Results selection receiver, not the leaf comparison alone.
    await act(async () => tree.root.findByType(BinderSelectedControls).props.onClearSelection()); await flush();
    expect(tree.root.findByType(BinderResultComparison).props.selectedDesignIds).toEqual([]);
    expect(JSON.parse(localStorage.getItem(key)!)).toMatchObject({ designs: [], native: [], documents: {} });
    await act(async () => tree.unmount()); client.clear();
    await mount();
    expect(JSON.parse(localStorage.getItem(key)!)).toMatchObject({ designs: [], native: [], documents: {} });
});

it('legacy full-loop caller carries the exact Results return into the existing refinement editor route', async () => {
    localStorage.setItem('bms:selected-designs:parent', JSON.stringify(['design-1']));
    function Receiver() { const location = useLocation(); return <pre data-refinement-receiver>{location.pathname + location.search}</pre>; }
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /><Route path="/submit" element={<Receiver />} /></Routes>);
    await click('Legacy full-loop refinement');
    const route = new URL(text(tree.root.findByProps({ 'data-refinement-receiver': true })), 'http://fixture');
    expect(route.searchParams.get('template')).toBe('antibody_denovo');
    expect(route.searchParams.get('refinement')).toBe('1');
    expect(route.searchParams.get('return_to')).toBe('/designs/parent');
});

it('actual parent binds selected child Design evidence to its producer and never emits an empty source ID', async () => {
    await mount(); await click('Data Table');
    const evidence = calls.filter(c => c.url.endsWith('/binder-evidence'));
    expect(evidence.some(c => c.url === '/api/designs/by-job/producer/binder-evidence' && c.params.source_design_id === 'design-0')).toBe(true);
    expect(evidence.some(c => c.params.source_design_id === '')).toBe(false);
    expect(evidence.some(c => c.url === '/api/designs/by-job/parent/binder-evidence' && c.params.source_design_id)).toBe(false);
    expect(text(tree.root)).toContain('Source Design: design-0');
});
it('empty source selection remains the paged candidate reader, without a 422-producing parameter', async () => {
    returnedRoot = 'parent'; await mount(<BinderPredictionEvidence jobId="parent" sourceDesignId="" />);
    expect(calls.find(c => c.url.endsWith('/binder-evidence')).params).toEqual({ offset: 0, limit: 100 });
    expect(tree.root.findByProps({ 'aria-label': 'Evidence candidate' })).toBeTruthy();
});
it.each(['lineage_root_job_id', 'parent_job_id'])('accepts the endpoint resolved root bound by native %s context and displays its identity', async field => {
    family = [{ ...job, lineage_root_job_id: null, [field]: 'root' }, family[2]];
    await mount(<CandidateRoundProgress jobId="parent" />);
    expect(text(tree.root)).toContain('Round: not_requested');
    expect(tree.root.findAllByType('a').some(n => n.props.href === '/designs/root')).toBe(true);
});
it.each(['foreign', 'foreign-present', 'cycle', 'missing-source', 'missing-root'])('rejects unrelated or incomplete %s root context', async scenario => {
    if (scenario === 'foreign') returnedRoot = 'foreign';
    else if (scenario === 'foreign-present') { returnedRoot = 'foreign'; family.push({ ...family[2], id: 'foreign' }); }
    else if (scenario === 'cycle') family[2].parent_job_id = 'producer';
    else family = family.filter(j => j.id !== (scenario === 'missing-source' ? 'parent' : 'root'));
    await mount(<CandidateRoundProgress jobId="parent" />);
    expect(text(tree.root)).toContain('Round progress unavailable');
});
it('same-job round reads need no extra family lookup', async () => {
    returnedRoot = 'parent'; await mount(<CandidateRoundProgress jobId="parent" />);
    expect(text(tree.root)).toContain('Round: not_requested');
    expect(calls.some(c => c.url === '/api/jobs')).toBe(false);
});
it('large cohort defaults to bounded All, pages real rows, and preserves an explicit client source filter', async () => {
    total = 8850; job = { ...job, status: 'awaiting_input', awaiting_stage: 'rfantibody', current_stage: 'rfantibody' };
    await mount(); await click('Data Table');
    const pages = () => calls.filter(c => c.url === '/api/designs');
    expect(pages().every(c => c.params.limit <= 500)).toBe(true);
    expect(pages().at(-1).params.limit).toBe(100);
    expect(tree.root.findAllByType('tbody').flatMap(n => n.findAllByType('tr')).length).toBeGreaterThanOrEqual(100);
    await click('→');
    expect(pages().at(-1).params).toMatchObject({ limit: 100, offset: 100 });
    expect(text(tree.root)).toContain('retained-100');
    await click('RFantibody');
    expect(text(tree.root)).toContain('500');
    expect(pages().at(-1).params.limit).toBe(500);
    expect(tree.root.findAllByType('tbody').flatMap(n => n.findAllByType('tr'))).toHaveLength(0);
});
it('nonnative name input stays mounted throughout an unresolved query rather than losing typing after j', async () => {
    await mount(); await click('Data Table');
    const input = tree.root.findByProps({ placeholder: 'Filter by name...' });
    hold = true;
    await act(async () => input.props.onChange({ target: { value: 'j' } })); await flush();
    expect(text(tree.root)).toContain('Loading matching results');
    expect(tree.root.findByProps({ placeholder: 'Filter by name...' })).toBe(input);
    await act(async () => input.props.onChange({ target: { value: 'job0_job0_rfantibody_child_1_seq_2_model_0' } })); await flush();
    expect(tree.root.findByProps({ placeholder: 'Filter by name...' })).toBe(input);
    expect(calls.filter(c => c.url === '/api/designs').at(-1).params.q).toBe('job0_job0_rfantibody_child_1_seq_2_model_0');
});
it('bounded overview guidance does not advertise a nonexistent All page size', async () => {
    total = 230; await mount();
    expect(text(tree.root)).toContain('choose a larger page (up to 500)');
    expect(text(tree.root)).not.toContain('Use `All`');
});
it('historical non-RFA empty view uses generic retained-results guidance', async () => {
    total = 0; job = { ...job, model_id: 'boltzgen', mode: 'ligand_binder' }; await mount();
    expect(text(tree.root)).toContain('No persisted design rows or review-summary metadata');
    expect(text(tree.root)).not.toContain('RFA review rows');
});
