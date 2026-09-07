import React from 'react';
import { readFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, test, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { JobDetailsPanel } from '../../src/components/JobDetailsPanel';
import type { Job } from '../../src/lib/api';
let mounted: ReactTestRenderer | undefined;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
afterEach(async () => { if (mounted) await act(async () => mounted!.unmount()); vi.unstubAllGlobals(); });
test.each(['esmfold2','openmm'])('mounted %s job details show persisted ASGI receipt, not raw JSON', async (model) => {
    const wire = JSON.parse(readFileSync((model === 'esmfold2' ? process.env.BMS_WP06_WIRE : process.env.BMS_WP06_OPENMM_WIRE)!, 'utf8'));
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ok:true, json:async () =>
        url.endsWith('/execution-settings') ? wire : {structures:[], count:0}})));
    const job = {id:'receipt-job', name:'Receipt job', model_id:model === 'esmfold2' ? 'esmfold2' : 'antibody_denovo', status:'completed', mode:'predict'} as Job;
    await act(async () => { mounted = create(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
        <MemoryRouter><table><tbody><JobDetailsPanel job={job} onClose={() => {}} /></tbody></table></MemoryRouter>
    </QueryClientProvider>); });
    await vi.waitFor(() => expect(text(mounted!.root)).toContain('Effective execution settings'));
    const booleanKey = model === 'esmfold2' ? 'msa_remove_insertions' : 'cdr_only';
    expect(text(mounted!.root.findByProps({'data-setting-key':booleanKey}))).toContain('falsefalseRequest');
    if (model === 'esmfold2') expect(text(mounted!.root.findByProps({'data-setting-key':'seed'}))).toContain('00Request');
    expect(text(mounted!.root)).toContain(wire.receipts[0].sources[0].sha256);
    expect(text(mounted!.root)).not.toContain('argv');
});
