// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DataViewerLanding } from '../../src/components/DataViewerLanding';
import { api, type Job } from '../../src/lib/api';

// Rendering fixture for the recent-jobs list; not scientific output.
const job = (over: Partial<Job>) => ({
    id: 'job', name: 'fixture', model_id: 'rfd4_proteina', mode: 'generate', status: 'completed', design_count: 0,
    created_at: new Date().toISOString(), params: {}, provenance: null, ...over,
}) as unknown as Job;

let root: Root; let container: HTMLDivElement;
beforeEach(() => {
    api.defaults.adapter = async config => ({ data: [], status: 200, statusText: 'OK', headers: {}, config });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

const mount = (jobs: Job[]) => act(async () => root.render(
    <QueryClientProvider client={new QueryClient()}>
        <DataViewerLanding jobs={jobs} jobsLoading={false} onBrowseJobs={() => {}} onSelectJob={() => {}} onImportComplete={() => {}} />
    </QueryClientProvider>));

it('does not claim zero designs for a registered native generation, and keeps counts for Design-bearing jobs', async () => {
    await mount([
        job({ id: 'a', name: 'native RFD4', model_id: 'rfd4_proteina', mode: 'generate', design_count: 0 }),
        job({ id: 'b', name: 'native La', model_id: 'protein_modification_experimental', mode: 'de_novo_design',
              params: { generator: 'laproteina' }, provenance: { native_generation_publication: { contract: 'laproteina_v1' } } as never }),
        job({ id: 'c', name: 'ordinary', model_id: 'proteinmpnn', mode: 'design', design_count: 12 }),
        job({ id: 'd', name: 'ordinary empty', model_id: 'proteinmpnn', mode: 'design', design_count: 0 }),
    ]);
    const text = container.textContent ?? '';
    expect(text).toContain('rfd4_proteina • native results');
    expect(text).toContain('protein_modification_experimental • native results');
    expect(text).toContain('proteinmpnn • 12 designs');
    expect(text).toContain('proteinmpnn • 0 designs');
    expect(text).not.toContain('rfd4_proteina • 0 designs');
});
