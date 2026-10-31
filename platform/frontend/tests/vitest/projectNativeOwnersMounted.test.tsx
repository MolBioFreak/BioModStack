import assert from 'node:assert/strict';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, test, vi } from 'vitest';
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

import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate.js';

const clients: QueryClient[] = [];
// These owner tests are offline; never let incidental readiness reads use XHR.
import { api } from '../../src/lib/api';
const originalAdapter = api.defaults.adapter;
beforeEach(() => { api.defaults.adapter = async () => { throw new Error('offline owner fixture'); }; });
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
    api.defaults.adapter = originalAdapter;
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
    const draft = drafts.at(-1)!;
    const expected = {
        sequence: 'MQLK', sequence_name: 'target', pred_method: 'esmfold2', num_parallel_jobs: 2,
        run_frustrampnn: false, frustrampnn_requiredness: 'required', model_variant: 'full', local_files_only: true,
        esmf_use_msa: false, quality_preset: 'custom', num_loops: 3, num_sampling_steps: 50, num_diffusion_samples: 1,
    };
    // The common draft also includes launcher/provider metadata; assert the full
    // effective native settings instead of an obsolete exact sparse projection.
    for (const [key, value] of Object.entries(expected)) assert.deepEqual(draft[key], value);
    await act(async () => renderer.unmount());
});

