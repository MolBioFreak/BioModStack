import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), navigate: vi.fn() }));
vi.mock('react-router-dom', async original => ({ ...await original<typeof import('react-router-dom')>(), useNavigate: () => mocks.navigate }));
vi.mock('../../platform/frontend/src/lib/api', async original => ({
    ...await original<typeof import('../../platform/frontend/src/lib/api')>(),
    api: { get: mocks.get, post: mocks.post }, fetchExecutionTargets: async () => ({ data: [] }),
}));
vi.mock('../../platform/frontend/src/components/MolstarViewer', () => ({ default: () => null }));
import { MolecularDynamicsTemplate } from '../../platform/frontend/src/components/MolecularDynamicsTemplate';
import { buildMolecularDynamicsHandoffInitialValues, parseMolecularDynamicsHandoffRoute } from '../../platform/frontend/src/components/gen2StartingStructureState';

it('mounted standalone sender saves and reads exact durable draft before navigation', async () => {
    const sequenceId = '55555555-5555-4555-8555-555555555555';
    const operationId = '66666666-6666-4666-8666-666666666666';
    let stored: any;
    const record = { id: sequenceId, name:'Exact', sequence:'ACDEFG', length:6, description:null, organism:null, uniprot_id:null, ncbi_id:null, is_preset:false, created_at:'2026-10-23T00:00:00', updated_at:null };
    mocks.get.mockImplementation(async (url: string) => {
        if (url === '/api/user-sequences') return { data: [record] };
        if (url === `/api/user-sequences/${sequenceId}`) return { data: record };
        if (url === `/api/molecular-dynamics/prediction-detours/${operationId}`) return { data: stored };
        if (url === '/api/molecular-dynamics/chemistry-profiles') return { data: { schema:'bms.md.chemistry-profile-inventory.v1', catalog_digest:'a'.repeat(64), profiles:[], selectable_profile_ids:[], count:0, bounded:true } };
        throw Error(`Unexpected GET ${url}`);
    });
    mocks.post.mockImplementation(async (url: string, payload: any) => {
        expect(url).toBe('/api/molecular-dynamics/prediction-detours');
        stored = structuredClone(payload); return { data: { id: operationId } };
    });
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container); const client = new QueryClient({ defaultOptions: { queries: { retry:false } } });
    const click = async (text: string) => {
        const button = [...container.querySelectorAll('button')].find(b => b.textContent?.includes(text));
        expect(button, text).toBeTruthy(); await act(async () => { button!.click(); });
    };
    try {
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter><MolecularDynamicsTemplate onBack={() => {}} initialValues={{ md_return_to:'/designs/parent', complex_components:[{ type:'protein',sequence:'ACDEFG',copies:2 },{ type:'ligand',smiles:'O' }] }} /></MemoryRouter></QueryClientProvider>); });
        await click('Predict structure from sequence');
        await click('Use saved sequence');
        const select = container.querySelector<HTMLSelectElement>('[data-md-saved-sequence]'); expect(select).toBeTruthy();
        await act(async () => { await vi.waitFor(() => expect(select!.querySelector(`option[value="${sequenceId}"]`)).toBeTruthy()); });
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select, sequenceId); select!.dispatchEvent(new Event('change',{ bubbles:true })); });
        await click('Open Structure Prediction');
        expect(stored, container.textContent ?? '').toBeTruthy();
        expect(stored.composition.sequence).toBe('ACDEFG');
        expect(stored.composition.complex_components).toHaveLength(2);
        expect(stored.return_to).toBe('/designs/parent');
        expect(stored.md_draft.form.native_intent).toBeUndefined();
        expect(mocks.navigate).toHaveBeenCalledTimes(1);
        const route = mocks.navigate.mock.calls[0][0];
        expect(route).toContain(`md_operation_id=${operationId}`);
        expect(mocks.get.mock.calls.some(([url]) => url.endsWith(`/prediction-detours/${operationId}`))).toBe(true);
        sessionStorage.clear();
        const returned = parseMolecularDynamicsHandoffRoute(`?template=molecular_dynamics&md_draft_id=77777777-7777-4777-8777-777777777777&md_operation_id=${operationId}`);
        const coldValues = buildMolecularDynamicsHandoffInitialValues(returned, stored.md_draft);
        expect(coldValues.md_return_to).toBe('/designs/parent');
        expect(coldValues.md_sequence_snapshot).toEqual(record);
        const selectedSnapshot = structuredClone(stored);
        record.sequence = 'VVVVVV'; // Mutable saved sequence changed after durable capture.
        mocks.navigate.mockClear();
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter><MolecularDynamicsTemplate key="cold" onBack={() => {}} initialValues={coldValues} /></MemoryRouter></QueryClientProvider>); });
        const getCount = mocks.get.mock.calls.filter(([url]) => url === `/api/user-sequences/${sequenceId}`).length;
        await click('Open Structure Prediction');
        expect(mocks.navigate).toHaveBeenCalledTimes(1);
        expect(stored.composition).toEqual(selectedSnapshot.composition);
        expect(stored.md_draft.sequenceSnapshot.sequence).toBe('ACDEFG');
        expect(mocks.get.mock.calls.filter(([url]) => url === `/api/user-sequences/${sequenceId}`).length).toBe(getCount);
        expect(() => parseMolecularDynamicsHandoffRoute('?template=molecular_dynamics&md_operation_id=bad')).toThrow();
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it('exact redesign updates only the existing selected primary and retains copies and chemistry', async () => {
    const { hydrateMdPredictionComposition } = await import('../../platform/frontend/src/components/StructurePredictionTemplate');
    const saved = { primary_chain_id:'B', md_exact_sequence:'ACDEFG', sequence:'ACDEFG', complex_components:[
        { id:'A', type:'protein', sequence:'GGGG', copies:2 },
        { id:'B', type:'protein', sequence:'OLDSEQ', copies:3 },
        { id:'L', type:'ligand', smiles:'O', copies:1 },
        { id:'I', type:'ion', element:'NA', copies:2 },
    ] };
    const result = hydrateMdPredictionComposition(saved);
    expect(result.complex_components).toEqual([saved.complex_components[0], { ...saved.complex_components[1], sequence:'ACDEFG' }, saved.complex_components[2], saved.complex_components[3]]);
    expect(saved.complex_components[1].sequence).toBe('OLDSEQ');
    expect(result.source_structure).toBeUndefined();
});

it('cold draft restores independent placement; predicted document supersedes old source on success', () => {
    const saved = { form:{ native_intent:{ schema_version:'bms.md.launch-intent.v2', stages:[], random_seed:0 } }, source_ref:{ kind:'design', id:'11111111-1111-4111-8111-111111111111' }, execution_target_id:null, execution_policy:{ remote_result_policy:'manual' }, destinationLaunchContextId:'destination', return_to:'/designs/source' };
    const cancel = buildMolecularDynamicsHandoffInitialValues(parseMolecularDynamicsHandoffRoute('?template=molecular_dynamics'), saved);
    expect(cancel.source_ref).toEqual(saved.source_ref); expect(cancel.execution_target_id).toBeNull();
    expect(cancel.md_destination_launch_context_id).toBe('destination'); expect((cancel.md_form as any).native_intent.random_seed).toBe(0);
    const success = buildMolecularDynamicsHandoffInitialValues(parseMolecularDynamicsHandoffRoute('?template=molecular_dynamics&source_prediction_job_id=22222222-2222-4222-8222-222222222222'), saved);
    expect(success.source_ref).toBeUndefined(); expect(success.source_prediction_job_id).toBe('22222222-2222-4222-8222-222222222222');
});
