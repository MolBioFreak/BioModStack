import React from 'react';
import { readFileSync, writeFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { api } from '../../src/lib/api';
import { hasNativeComparison, readNativeComparison, nativeComparisonMetric } from '../../src/lib/nativeBinderComparison';
import { nativeGenerationMetricDetail, nativeGenerationMetricUnit } from '../../src/lib/nativeBinderResults';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div /> }));
vi.mock('react-plotly.js', () => ({ default: () => <div /> }));
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
it('restores RFD3 measurements through its existing bounded reader, retaining zero/null', async () => {
    const original = api.defaults.adapter;
    const job = { id: 'rfd3', name: 'Native RFD3', model_id: 'protein_modification_experimental', mode: 'de_novo_design', params: { generator: 'rfd3' } };
    api.defaults.adapter = async config => {
        expect(config.url).toBe('/api/jobs/rfd3/rfd3-generation');
        expect(config.params).toEqual({ offset: 0, limit: 100 });
        return { config, status: 200, statusText: 'OK', headers: {}, data: { total: 1, candidates: [{ candidate_id: 'one', design_id: 'd', length: 100, radius: 0, helix_count: null, strand_count: 0, status: 'accepted' }] } };
    };
    try {
        expect(hasNativeComparison(job)).toBe(true);
        const set = await readNativeComparison(job);
        expect(set.complete).toBe(true);
        expect(set.records[0].metrics).toEqual({ length: 100, radius: 0, helix_count: null, strand_count: 0 });
        expect(nativeComparisonMetric(set, set.records[0], 'radius').unit).toBe('angstrom');
        expect(nativeGenerationMetricDetail(set.records[0], 'helix_count')).toContain('annotations unavailable');
    } finally { api.defaults.adapter = original; }
});
it.skipIf(!process.env.BMS_RETAINED_RESULTS_WIRE)('mounts real retained publisher/cold-reader page and exports relations with exact continuation', async () => {
    const page = JSON.parse(readFileSync(process.env.BMS_RETAINED_RESULTS_WIRE!, 'utf8'));
    const original = api.defaults.adapter;
    const blobs: Blob[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob as Blob); return `blob:retained-${blobs.length}`; });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let tree: ReactTestRenderer | undefined;
    localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, '', '/bms/designs/job');
    api.defaults.adapter = async config => {
        if (config.url !== '/api/jobs/job') throw Error(`Unexpected request ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: { id: 'job', saved_selection_sets: [] } };
    };
    try {
        await act(async () => { tree = create(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="job" adapter={{ key: 'retained', title: 'Retained native result', label: row => String(row.example_id ?? row.event), fetchPage: async () => page }} /></QueryClientProvider>); });
        for (let i = 0; i < 100 && !tree!.root.findAllByType('a').some(node => text(node) === 'Refine with partial diffusion'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
        const record = page.records.find((r: any) => r.document);
        expect(nativeGenerationMetricUnit(record, 'amino_acid_composition.ala_fraction')).toBe('fraction');
        const link = tree!.root.findAllByType('a').find(node => text(node) === 'Refine with partial diffusion')!;
        const source = JSON.parse(new URL(link.props.href, window.location.origin).searchParams.get('source_structure')!);
        expect(source.document.artifact_id).toBe(record.structures[0].artifact_id);
        const states = page.artifacts.filter((item: any) => item.role === 'sampling_state');
        if (states.length) {
            const select = tree!.root.findByProps({ 'aria-label': 'RFD4 retained state' });
            expect(select.findAllByType('option').length).toBe(states.length + 1);
            expect(text(tree!.root.findByProps({ 'aria-label': 'Retained RFD4 sampling state' }))).toContain('Association with this molecule is not reported');
            await act(async () => select.props.onChange({ target: { value: states[0].artifact_id } }));
            const continuation = tree!.root.findAllByType('a').find(node => text(node) === 'Continue retained RFD4 state')!;
            const state = JSON.parse(new URL(continuation.props.href, window.location.origin).searchParams.get('rfd4_state')!);
            expect(state).toEqual({ path: states[0].source_path, artifact_id: states[0].artifact_id, sha256: states[0].sha256 });
        }
        const exported = await Promise.all(blobs.map(blob => new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); })));
        const payload = exported.map(s => { try { return JSON.parse(s); } catch { return null; } }).find(s => s?.schema === 'bms.native-generation-export.v1' && s.records?.some((row: any) => row.native_record?.relations?.length));
        expect(payload).toBeTruthy();
        expect(payload.records[0].native_record.relations[0].artifact_id).toBe(record.relations[0].artifact_id);
        if (process.env.BMS_RECEIVING_EVIDENCE) writeFileSync(`${process.env.BMS_RECEIVING_EVIDENCE}/retained-mounted-export.json`, JSON.stringify(payload, null, 2));
    } finally { await act(async () => tree?.unmount()); client.clear(); api.defaults.adapter = original; vi.restoreAllMocks(); }
});
