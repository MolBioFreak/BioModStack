import assert from 'node:assert/strict';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, test, vi } from 'vitest';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../src/components/useLiveGpuCatalog', () => ({
    useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }),
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({
    ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({
        data: { workflows: { structure_prediction: { default_enabled: false } } },
        isPending: false, isFetching: false, isError: false,
    }),
}));

import { ProteinLocalRedesignTemplate } from '../../src/components/ProteinLocalRedesignTemplate.js';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate.js';

const clients: QueryClient[] = [];
const renderOwner = async (node: React.ReactNode) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(
            <MemoryRouter><QueryClientProvider client={client}>{node}</QueryClientProvider></MemoryRouter>,
        );
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    return renderer!;
};

afterEach(async () => {
    for (const client of clients.splice(0)) client.clear();
});

test('mounted structure owner hydrates and reports Project ESMFold2 values', async () => {
    const drafts: Array<Record<string, unknown>> = [];
    const renderer = await renderOwner(
        <StructurePredictionTemplate
            onBack={() => undefined}
            initialValues={{
                sequence: 'MQLK', sequence_name: 'target', pred_method: 'esmfold2',
                num_parallel_jobs: 2, run_frustrampnn: false, frustrampnn_requiredness: 'required',
                model_variant: 'full', local_files_only: true,
            }}
            onDraftChange={(draft) => drafts.push(draft)}
        />,
    );
    assert.ok(drafts.length > 0);
    assert.deepEqual(drafts.at(-1), {
        sequence: 'MQLK', sequence_name: 'target', pred_method: 'esmfold2', num_parallel_jobs: 2,
        run_frustrampnn: false, frustrampnn_requiredness: 'required', model_variant: 'full', local_files_only: true,
    });
    await act(async () => renderer.unmount());
});

test('mounted RFD3 owner hydrates typed chain arrays and reports the Project draft', async () => {
    const drafts: Array<Record<string, unknown>> = [];
    const renderer = await renderOwner(
        <ProteinLocalRedesignTemplate
            onBack={() => undefined}
            initialValues={{
                input_structure: 'data/source.pdb', redesign_mode: 'partial_diffusion', design_chains: ['A'],
                context_chains: ['B'], redesign_ranges: 'A:10-20', sequence_policy: 'preserve',
                select_unfixed_sequence: '', insertion_anchor: '', insertion_min_length: 1,
                insertion_max_length: 1, partial_t: 2, ligand: '', num_designs: 4, seed: 7,
                dump_trajectories: false, write_full_json: true, profile_id: 'generic_local_redesign_v1',
            }}
            onDraftChange={(draft) => drafts.push(draft)}
        />,
    );
    assert.ok(drafts.length > 0);
    assert.deepEqual(drafts.at(-1)?.design_chains, ['A']);
    assert.equal(drafts.at(-1)?.num_designs, 4);
    await act(async () => renderer.unmount());
});
