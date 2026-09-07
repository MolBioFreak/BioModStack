import React from 'react';
import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import type { Job } from '../../src/lib/api';

const path = process.env.BMS_TEST_ACCOUNTING_WIRE;
if (!path) throw new Error('Run the data-only API publication test with BMS_TEST_ACCOUNTING_WIRE first');
const wire = JSON.parse(readFileSync(path, 'utf8'));
let mounted: ReactTestRenderer | undefined;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); vi.unstubAllGlobals(); });
async function mount(job: Job) {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ structures: [], count: 0 }) })));
    await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
    </QueryClientProvider>); });
}
it('mounts real filter -> SQLite -> job API counts', async () => {
    expect(wire.origin).toBe('synthetic-data-only-filter-sqlite-api');
    await mount(wire.success);
    const output = text(mounted!.root);
    expect(output).toContain('not_selected_by_diversity_budget');
    for (const label of ['Generated: 3', 'Rejected: 1', 'Unevaluable: 1', 'Expected publication: 1', 'Persisted: 1', 'Requested: unknown']) {
        expect(output).toContain(label);
    }
});
it('does not render partial/lost publication as green completion', async () => {
    await mount(wire.failure);
    const alert = mounted!.root.findByProps({ role: 'alert' });
    expect(text(alert)).toContain('candidate_replay_changed');
    expect(text(alert)).toContain('ingestion_failed');
    expect(text(mounted!.root)).not.toContain('Publication validated');
});
