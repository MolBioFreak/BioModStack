import React, { act, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import FrustraMpnnAnalysisControls from '../../src/components/FrustraMpnnAnalysisControls';
import { FrustraMpnnSettingsPanel } from '../../src/components/frustrampnn/FrustraMpnnSettingsPanel';
import {
    CANONICAL_FRUSTRAMPNN_SETTINGS,
    type FrustraMpnnRequestedSettings,
} from '../../src/components/frustrampnn/frustraMpnnSettingsState';
import { api } from '../../src/lib/api';

const inspection = {
    source_models: [1],
    selected_source_model: 1,
    observed_altlocs: [''],
    selected_altloc: '',
    protein_entities: [{
        entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'A', auth_asym_id: 'A', pdb_chain_id: 'A',
    }],
    mapped_residues: [{
        entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'A', auth_asym_id: 'A',
        auth_seq_id: 1, insertion_code: '', sequence_index: 1, wt: 'M',
    }],
};

const mount = async (node: React.ReactNode) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(node));
    return { container, root };
};

const settle = async (milliseconds = 0) => {
    await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    });
};

afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
});

describe('governed FrustraMPNN inspection and launch behavior', () => {
    it('uses the owned source pair, preserves inspected selectors when validation fails, and shows the failure', async () => {
        const originalPost = api.post;
        const calls: string[] = [];
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            calls.push(url);
            if (url.endsWith('/sources/inspect/owned')) return { data: inspection };
            throw new Error('owned validation denied');
        };

        function Harness() {
            const [settings, setSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
            return <FrustraMpnnSettingsPanel
                value={settings}
                onChange={setSettings}
                governedSource={{ kind: 'owned', reference: { job_id: 'job-1', invocation_id: 'invoke-1' } }}
            />;
        }

        try {
            const { container, root } = await mount(<Harness />);
            await settle(250);
            expect(calls).toEqual([
                '/api/frustrampnn/sources/inspect/owned',
                '/api/frustrampnn/settings/validate/owned',
            ]);
            expect(container.querySelector('[role="alert"]')?.textContent).toContain('owned validation denied');
            const selectionMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
            expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_entities"]')?.disabled).toBe(false);
            expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_residues"]')?.disabled).toBe(false);
            await act(async () => root.unmount());
        } finally {
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });

    it('validates the downloaded design PDB as multipart and never queues when validation fails', async () => {
        const originalGet = api.get;
        const originalPost = api.post;
        let queueCalls = 0;
        const postCalls: string[] = [];
        (api as unknown as { get: () => Promise<{ data: unknown }> }).get = async () => ({ data: {} });
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            postCalls.push(url);
            if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
            if (url.endsWith('/settings/validate/upload')) throw new Error('upload validation denied');
            if (url.includes('/jobs/') && url.endsWith('/analyze')) queueCalls += 1;
            return { data: {} };
        };
        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true,
            status: 200,
            arrayBuffer: async () => new TextEncoder().encode('ATOM').buffer,
        })));
        vi.stubGlobal('crypto', {
            subtle: { digest: async () => new Uint8Array(32).buffer },
        });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

        try {
            const { container, root } = await mount(
                <QueryClientProvider client={queryClient}>
                    <FrustraMpnnAnalysisControls
                        parentJobId="parent-1"
                        selectedDesigns={[{ id: 'design-1', name: 'Design 1', pdb_path: 'owned/design-1.pdb' }]}
                        onOpenJob={() => undefined}
                    />
                </QueryClientProvider>,
            );
            await settle(250);
            expect(fetch).toHaveBeenCalledTimes(1);
            expect(postCalls).toEqual([
                '/api/frustrampnn/sources/inspect/upload',
                '/api/frustrampnn/settings/validate/upload',
            ]);
            const selectionMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
            expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_entities"]')?.disabled).toBe(false);
            const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('Analyze 1 selected'));
            expect(button).toBeTruthy();
            await act(async () => button!.click());
            await settle();
            expect(postCalls).toEqual([
                '/api/frustrampnn/sources/inspect/upload',
                '/api/frustrampnn/settings/validate/upload',
                '/api/frustrampnn/sources/inspect/upload',
                '/api/frustrampnn/settings/validate/upload',
            ]);
            expect(queueCalls).toBe(0);
            expect(container.querySelector('[role="alert"]')?.textContent).toContain('upload validation denied');
            await act(async () => root.unmount());
        } finally {
            queryClient.clear();
            (api as unknown as { get: typeof originalGet }).get = originalGet;
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });
});
