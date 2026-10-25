import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { deNovoRouteValues, isDeNovoModel } from '../../src/components/deNovoWorkflowRoute';
import { api } from '../../src/lib/api';

it('mounts every native metric and emits an ordinary separate Job request', async () => {
    const schema = process.env.BMS_LA_SCHEMA ? JSON.parse(readFileSync(process.env.BMS_LA_SCHEMA, 'utf8')) : { parameter_schema: JSON.parse(execFileSync('python3', ['-c', `import sys,json; sys.path.insert(0, ${JSON.stringify(resolve('../../scripts'))}); from lib.laproteina_contract import evaluation_schema; print(json.dumps(evaluation_schema()))`], { encoding: 'utf8' })) };
    const prior = api.defaults.adapter;
    const unexpected: string[] = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        const data = url.includes('/models/laproteina/generation-settings') ? schema
            : url.includes('execution-targets') ? [] : url.includes('resources') ? {} : null;
        if (data === null) unexpected.push(url);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    let latest: Record<string, any> = {};
    const values = { laproteina_evaluation_request: { samples_slot: 'samples', config_slot: 'config', job_id: 0, ncpus: 1 },
        native_sources: { samples: { kind: 'file', path: 'inputs/native' }, config: { kind: 'file', path: 'inputs/original.yaml' } } };
    const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div'); document.body.append(container); const root = createRoot(container);
    const onDraftChange = (draft: Record<string, unknown>) => { latest = draft; };
    try {
        await act(async () => root.render(<MemoryRouter><QueryClientProvider client={query}><ProteinModificationTemplate onBack={() => {}} initialValues={{ ...values, ...deNovoRouteValues(new URLSearchParams('model=laproteina&mode=evaluate')) }} onDraftChange={onDraftChange} /></QueryClientProvider></MemoryRouter>));
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
        const fields = schema.parameter_schema.properties.metrics.properties;
        for (const [key, field] of Object.entries(fields) as [string, any][]) {
            expect(container.querySelector(`[aria-label="${field.type === 'boolean' ? key : `Override ${key}`}"]`), key).not.toBeNull();
        }
        const disabled = container.querySelector<HTMLSelectElement>('[aria-label="compute_designability"]')!;
        await act(async () => { disabled.value = 'false'; disabled.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => container.querySelector<HTMLInputElement>('[aria-label="Override motif_rmsd_modes"]')!.click());
        const body = latest.native_job_request;
        expect(isDeNovoModel('laproteina')).toBe(true);
        expect(body.model_id).toBe('laproteina'); expect(body.mode).toBe('evaluate');
        expect(body.params.laproteina_evaluation_request.metrics).toEqual({ compute_designability: false, motif_rmsd_modes: [] });
        expect(body.params.laproteina_evaluation_request.job_id).toBe(0);
        expect(body.params.laproteina_evaluation_request).not.toHaveProperty('source_directory');
        expect(container.querySelector('textarea')).toBeNull();
        if (process.env.BMS_LA_CAPTURE) writeFileSync(process.env.BMS_LA_CAPTURE, JSON.stringify(body, null, 2));
        expect(unexpected).toEqual([]);
    } finally {
        await act(async () => root.unmount()); query.clear(); api.defaults.adapter = prior; container.remove(); vi.restoreAllMocks();
    }
});
