import React from 'react';
import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { api } from '../../src/lib/api';
import { prepareDeNovoContinuation, type DeNovoDestination } from '../../src/lib/deNovoContinuation';
import { sourceResultReturn } from '../../src/lib/sourceResultReturn';
import { MemoryRouter } from 'react-router-dom';
import { DeNovoNativeGenerationSettings } from '../../src/components/DeNovoNativeGenerationSettings';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div /> }));
vi.mock('react-plotly.js', () => ({ default: () => <div /> }));
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
it.skipIf(!process.env.BMS_RESULTS_POPULATION_WIRE)('registered producer page renders event counts and exact sequences, emits native mounted continuation', async () => {
    const page = JSON.parse(readFileSync(process.env.BMS_RESULTS_POPULATION_WIRE!, 'utf8'));
    const schemas = JSON.parse(readFileSync(process.env.BMS_RESULTS_POPULATION_WIRE! + '.schemas.json', 'utf8'));
    const original = api.defaults.adapter;
    const calls: any[] = [];
    let tree: ReactTestRenderer | undefined;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    localStorage.clear(); sessionStorage.clear();
    window.history.replaceState({}, '', '/bms/designs/job?model_number=2');
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test');
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url: config.url, body });
        let data;
        if (config.url === '/api/jobs/job') data = { id: 'job', saved_selection_sets: [] };
        else if (config.url === '/api/models/rfd4_proteina/generation-settings') data = { parameter_schema: schemas[config.params.mode] };
        else if (config.method === 'post' && config.url?.includes('materializ')) data = {
            path: 'retained/annotated.cif', format: 'cif', sha256: 'a'.repeat(64), model_number: 2,
            model_numbers: [2], source_structure: body, native_format: 'cif', author_residues: [],
        };
        else throw Error(`Unexpected ${config.method} ${config.url}`);
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
    try {
        await act(async () => { tree = create(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="job" adapter={{ key: 'population-wire', title: 'Native output', label: row => String(row.example_id ?? row.event), fetchPage: async () => page }} /></QueryClientProvider>); });
        for (let i = 0; i < 100 && !text(tree!.root).includes('A[MSE]'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
        expect(text(tree!.root)).toContain('Publication records');
        expect(text(tree!.root)).toContain('Observations');
        expect(text(tree!.root)).toContain('A[MSE]');
        expect(text(tree!.root)).toContain('B:12A · MSE');
        expect(text(tree!.root)).toContain('ranking intermediate');
        const retained = page.artifacts.find((item: any) => item.role === 'sampling_state');
        await act(async () => tree!.root.findByProps({ 'aria-label': 'RFD4 retained state' }).props.onChange({ target: { value: retained.artifact_id } }));
        await act(async () => tree!.root.findByProps({ 'aria-label': 'RFD4 continuation seed' }).props.onChange({ target: { value: '0' } }));
        const stateLink = tree!.root.findAllByType('a').find(node => text(node) === 'Continue retained RFD4 state')!;
        const stateRoute = new URL(stateLink.props.href, window.location.origin);
        expect(JSON.parse(stateRoute.searchParams.get('rfd4_state')!)).toMatchObject({ path: retained.source_path, artifact_id: retained.artifact_id, sha256: retained.sha256, seed: 0 });
        for (const [label, destination] of [['Refine with partial diffusion', 'rfd4_partial'], ['Native inverse folding', 'rfd4_inverse'], ['Evaluate with RFD4', 'rfd4_evaluate']]) {
            const anchor = tree!.root.findAllByType('a').find(node => text(node) === label)!;
            const route = new URL(anchor.props.href, window.location.origin);
            expect(route.pathname).toBe('/bms/submit');
            const source = JSON.parse(route.searchParams.get('source_structure')!);
            expect(source.model_number).toBe(2);
            expect(source.document.artifact_id).toBe(page.records[0].structures[0].artifact_id);
            const values = await prepareDeNovoContinuation(source, destination as DeNovoDestination);
            expect(values).toMatchObject({ generator: 'rfd4_proteina', rfd4_request: { input: { kind: 'annotated', slots: ['selected'] } } });
            let editor: ReactTestRenderer | undefined;
            let draft: any;
            try {
                await act(async () => { editor = create(<MemoryRouter><QueryClientProvider client={client}><DeNovoNativeGenerationSettings model="rfd4_proteina" mode={destination === 'rfd4_evaluate' ? 'evaluate' : 'generate'} initialValues={values} onDraftChange={value => { draft = value; }} /></QueryClientProvider></MemoryRouter>); });
                for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
                expect(draft.native_job_request.model_id).toBe('rfd4_proteina');
                expect(draft.native_job_request.params.rfd4_request).toEqual((values as any).rfd4_request);
                expect(draft.native_job_request.params.native_sources.selected.model_number).toBe(2);
            } finally { await act(async () => editor?.unmount()); }
            expect(sourceResultReturn(route.searchParams.get('return_to'))).toContain('/designs/job?');
        }
        expect(calls.filter(call => call.body).every(call => call.body.output_format === 'native' && call.body.model_number === 2)).toBe(true);
        const source = { job_id: 'job', document: { artifact_id: page.records[0].structures[0].artifact_id }, model_number: 2 };
        const state = await prepareDeNovoContinuation(source, 'rfd4_state', { path: 'retained/state.pt', seed: 0 });
        expect(state).toMatchObject({ native_sources: { resume: { kind: 'file', path: 'retained/state.pt' } }, rfd4_request: { resume_slot: 'resume', settings: { seed: 0 } } });
    } finally {
        await act(async () => tree?.unmount()); client.clear(); api.defaults.adapter = original; vi.restoreAllMocks();
    }
});
