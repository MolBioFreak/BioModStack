import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './setup';
import { api } from '../../src/lib/api';
import { NgsDataRecords } from '../../src/components/project-manager/NgsDataRecords';
let root: Root;
let container: HTMLDivElement;
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
it.each(['', 'https://node.example.ts.net'])('Project NGS file links retain encoded paths on the configured server (%s)', async (base) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal('__BMS_API_BASE_URL__', base);
    const oldAdapter = api.defaults.adapter;
    const seen: string[] = [];
    api.defaults.adapter = async config => {
        seen.push(`${config.method} ${config.url}`);
        return { data: { schema: 'bns.ngs-data-record-list.v1', next_cursor: null, items: [{ id: 'transport-fixture', created_at: '2025-01-01T00:00:00Z', author: null, payload: {
            schema: 'bns.ngs-data-record.v1', record_key: 'fixture', title: 'Transport fixture', sample_name: '', data_format: 'fastq', use_case: 'sequence_identity', workflow_stage: 'acquired', data_location: '', notes: '', files: [{ name: 'read one.fastq', path: 'ngs folder/read one.fastq', size: 10 }],
        } }] }, status: 200, statusText: 'OK', headers: {}, config };
    };
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><NgsDataRecords projectId="project fixture" onAdd={vi.fn()} /></QueryClientProvider>));
        await flush();
        expect(container.querySelector('a')?.getAttribute('href')).toBe(`${base}/api/files/download/ngs%20folder/read%20one.fastq`);
        expect(seen).toEqual(['get /api/projects/project%20fixture/ngs-data-records']);
    } finally { api.defaults.adapter = oldAdapter; }
});
