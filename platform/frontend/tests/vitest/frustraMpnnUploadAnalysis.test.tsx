import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../src/lib/api';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from '../../src/components/frustrampnn/frustraMpnnSettingsState';
import FrustraMpnnAnalysisControls from '../../src/components/FrustraMpnnAnalysisControls';

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

const persistedSettings = {
    ...CANONICAL_FRUSTRAMPNN_SETTINGS,
    settings_value_origin: 'operator_request',
};
const effectiveSettings = {
    schema_name: 'frustrampnn_effective_settings',
    schema_version: 1,
    requested_settings: persistedSettings,
    settings_value_origin: 'operator_request',
    resolved_chains: [],
    normalization_policy_id: 'frustrampnn_structure_normalizer',
    normalization_policy_version: 1,
    threshold_policy_id: 'frustrampnn_class_v1',
    threshold_policy_sha256: '1'.repeat(64),
    settings_sha256: '2'.repeat(64),
    capability_inventory_byte_sha256: '3'.repeat(64),
    resolution_identity: {
        source_artifact_sha256: '4'.repeat(64),
        structure_map_schema_name: 'frustrampnn_structure_map',
        structure_map_schema_version: 1,
        structure_map_sha256: '5'.repeat(64),
        normalized_pdb_sha256: '6'.repeat(64),
    },
    value_sources: {
        protein_selection: { mode: 'operator_request', entities: 'operator_request', residues: 'operator_request' },
        source_structure: { selected_model_number: 'operator_request', preferred_altloc: 'operator_request' },
        classification_policy: { mode: 'operator_request', high_max: 'operator_request', minimal_min: 'operator_request' },
    },
    effective_settings_sha256: '7'.repeat(64),
};
const validationPreview = {
    validation_scope: 'preview_only',
    queue_resolution_requirement: 'submission_must_re_resolve_governed_source',
    normalized_requested_settings: persistedSettings,
    effective_settings: effectiveSettings,
    execution_configuration: {
        configuration_id: 'frustrampnn_execution_configuration_v2',
        schema_name: 'frustrampnn_execution_configuration',
        schema_version: 2,
        tool_id: 'frustrampnn',
        tool_version: 'MegaScale',
        effective_settings: effectiveSettings,
        settings_value_origin: 'operator_request',
        requested_settings_sha256: '2'.repeat(64),
        effective_settings_sha256: '7'.repeat(64),
        capability_inventory_byte_sha256: '3'.repeat(64),
        classification_policy_sha256: '8'.repeat(64),
        runtime: {
            sif_name: 'frustrampnn.sif', sif_sha256: '9'.repeat(64), executable_sha256: 'a'.repeat(64),
            checkpoint_id: 'MegaScale', checkpoint_sha256: 'b'.repeat(64), package_version: '1.0',
            source_commit: 'deadbeef', python_version: '3.11', pytorch_version: '2.7', image_version: '1',
        },
        runtime_identity_sha256: 'c'.repeat(64),
        normalization_policy_id: 'frustrampnn_structure_normalizer', normalization_policy_version: 1,
        threshold_policy_id: 'frustrampnn_class_v1', source_artifact_sha256: '4'.repeat(64),
        structure_map_sha256: '5'.repeat(64), normalized_pdb_sha256: '6'.repeat(64),
        configuration_sha256: 'd'.repeat(64),
    },
    hashes: {
        settings_sha256: '2'.repeat(64), effective_settings_sha256: '7'.repeat(64),
        configuration_sha256: 'd'.repeat(64), capability_inventory_byte_sha256: '3'.repeat(64),
        structure_map_sha256: '5'.repeat(64),
    },
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

describe('uploaded FrustraMPNN operator analysis', () => {
    it('renders and opens every scheduler child, including the smaller remainder', async () => {
        const originalGet = api.get;
        const originalPost = api.post;
        const childReceipt = (id: string, structureCount: number) => ({
            job_id: id, child_job_id: id, result_job_id: id,
            name: `FrustraMPNN ${id}`, parent_job_id: 'parent-1', source_parent_job_id: 'parent-1',
            trigger: 'design_analyze', status: 'queued', created_at: '2026-08-27T00:00:00Z',
            started_at: null, completed_at: null, settings_value_origin: 'operator_request',
            requested_settings: persistedSettings, requested_settings_sha256: '2'.repeat(64),
            candidates: [], results: [], structure_count: structureCount,
        });
        const receiptPolls: string[] = [];
        (api as unknown as { get: (url: string) => Promise<{ data: unknown }> }).get = async (url) => {
            const childId = ['child-1', 'child-2'].find((id) => url.includes(`/jobs/${id}/receipt`));
            if (childId) {
                receiptPolls.push(childId);
                const { structure_count: _structureCount, ...receipt } = childReceipt(childId, childId === 'child-1' ? 2 : 1);
                return { data: { ...receipt, status: 'completed', completed_at: '2026-08-27T00:01:00Z' } };
            }
            return { data: {} };
        };
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
            if (url.endsWith('/settings/validate/upload')) return { data: validationPreview };
            if (url.endsWith('/jobs/parent-1/analyze')) return { data: {
                schema_name: 'bms.structure-dataset-fanout.v1', fanout_id: 'f'.repeat(64),
                parent_job_id: 'parent-1', selected_structure_count: 3, structures_per_job: 2,
                replayed: false, child_jobs: [childReceipt('child-1', 2), childReceipt('child-2', 1)],
            } };
            throw new Error(`unexpected launch ${url}`);
        };
        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true, status: 200,
            arrayBuffer: async () => new TextEncoder().encode('ATOM').buffer,
        })));
        vi.stubGlobal('crypto', { subtle: { digest: async () => new Uint8Array(32).buffer } });
        const onOpenJob = vi.fn();
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        try {
            await act(async () => root.render(
                <QueryClientProvider client={queryClient}>
                    <FrustraMpnnAnalysisControls
                        parentJobId="parent-1"
                        selectedDesigns={[
                            { id: 'design-1', name: 'Design 1', pdb_path: 'one.pdb' },
                            { id: 'design-2', name: 'Design 2', pdb_path: 'two.pdb' },
                            { id: 'design-3', name: 'Design 3', pdb_path: 'three.pdb' },
                        ]}
                        onOpenJob={onOpenJob}
                    />
                </QueryClientProvider>,
            ));
            await settle(250);
            const launch = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Analyze 3 selected'))!;
            await act(async () => launch.click());
            await settle(100);
            expect(container.textContent).toContain('2 scheduler children for 3 structures');
            expect(container.textContent).toContain('Child 1/2 · 2 structures');
            expect(container.textContent).toContain('Child 2/2 · 1 structure');
            expect(container.textContent).toContain('child-1');
            expect(container.textContent).toContain('child-2');
            await settle(3100);
            expect(receiptPolls.sort()).toEqual(['child-1', 'child-2']);
            const openButtons = Array.from(container.querySelectorAll('button')).filter((button) => button.textContent?.includes('Open persisted results'));
            expect(openButtons).toHaveLength(2);
            await act(async () => { openButtons[0]!.click(); openButtons[1]!.click(); });
            expect(onOpenJob.mock.calls).toEqual([['child-1'], ['child-2']]);
            await act(async () => root.unmount());
        } finally {
            queryClient.clear();
            (api as unknown as { get: typeof originalGet }).get = originalGet;
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });

    it('inspects the selected PDB before launch, exposes typed settings and requiredness, and blocks a failed preflight', async () => {
        const module = await import('../../src/components/FrustraMpnnUploadAnalysisPanel');
        const Panel = module.default;
        const originalPost = api.post;
        const calls: string[] = [];
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            calls.push(url);
            if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
            if (url.endsWith('/settings/validate/upload')) throw new Error('candidate validation denied');
            throw new Error(`unexpected launch ${url}`);
        };
        const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        try {
            await act(async () => root.render(
                <QueryClientProvider client={queryClient}>
                    <Panel onOpenJob={() => undefined} />
                </QueryClientProvider>,
            ));
            const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
            const file = new File(['ATOM'], 'candidate.pdb', { type: 'chemical/x-pdb' });
            Object.defineProperty(input, 'files', { configurable: true, value: [file] });
            await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
            await settle(250);

            expect(container.textContent).toContain('Required analysis');
            const selectionMode = container.querySelector<HTMLSelectElement>('[data-frustrampnn-selection-mode]');
            expect(selectionMode?.querySelector<HTMLOptionElement>('option[value="selected_entities"]')?.disabled).toBe(false);
            expect(calls).toEqual([
                '/api/frustrampnn/sources/inspect/upload',
                '/api/frustrampnn/settings/validate/upload',
            ]);

            const launch = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Analyze uploaded structure'))!;
            await act(async () => launch.click());
            await settle();
            expect(container.querySelector('[role="alert"]')?.textContent).toContain('candidate validation denied');
            expect(calls.filter((url) => url.endsWith('/jobs/uploads/analyze'))).toHaveLength(0);
            await act(async () => root.unmount());
        } finally {
            queryClient.clear();
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });

    it('binds handoff inspection and launch validation to the newly selected candidate and exposes no raw edit JSON', async () => {
        const module = await import('../../src/components/FrustraMpnnCandidateHandoffPanel');
        const Panel = module.default;
        const originalPost = api.post;
        const calls: string[] = [];
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            calls.push(url);
            if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
            if (url.endsWith('/settings/validate/upload')) throw new Error('handoff validation denied');
            throw new Error(`unexpected launch ${url}`);
        };
        const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        try {
            await act(async () => root.render(
                <QueryClientProvider client={queryClient}>
                    <Panel parentJobId="job-1" parentInvocationId="invoke-1" parentLandscapeSha256={'a'.repeat(64)} />
                </QueryClientProvider>,
            ));
            const textInputs = container.querySelectorAll<HTMLInputElement>('input[type="text"], input:not([type])');
            await act(async () => {
                const candidate = Array.from(textInputs).find((input) => input.placeholder === 'variant-1')!;
                const producer = Array.from(textInputs).find((input) => input.placeholder === 'external-redesign')!;
                const setValue = (input: HTMLInputElement, value: string) => {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
                    setter.call(input, value);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                };
                setValue(candidate, 'candidate-1');
                setValue(producer, 'producer-1');
            });
            const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
            const file = new File(['ATOM'], 'candidate.pdb', { type: 'chemical/x-pdb' });
            Object.defineProperty(input, 'files', { configurable: true, value: [file] });
            await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
            await settle(250);

            expect(container.querySelector('textarea')).toBeNull();
            expect(container.textContent?.toLowerCase()).not.toContain('json');
            expect(calls).toEqual([
                '/api/frustrampnn/sources/inspect/upload',
                '/api/frustrampnn/settings/validate/upload',
            ]);
            const launch = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Queue FrustraMPNN reanalysis'))!;
            await act(async () => launch.click());
            await settle();
            expect(container.querySelector('[role="alert"]')?.textContent).toContain('handoff validation denied');
            expect(calls.filter((url) => url.endsWith('/candidates/handoff'))).toHaveLength(0);
            await act(async () => root.unmount());
        } finally {
            queryClient.clear();
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });

    it('renders the structured backend handoff authority after queueing', async () => {
        const module = await import('../../src/components/FrustraMpnnCandidateHandoffPanel');
        const Panel = module.default;
        const originalPost = api.post;
        const landscapeSha256 = 'd'.repeat(64);
        const receipt = {
            job_id: 'child-job-1', child_job_id: 'child-job-1', result_job_id: 'child-job-1',
            name: 'FrustraMPNN external candidate handoff', parent_job_id: 'job-1', source_parent_job_id: 'job-1',
            trigger: 'external_candidate_handoff', status: 'queued', created_at: '2026-08-09T00:00:00Z',
            started_at: null, completed_at: null, settings_value_origin: 'operator_request',
            requested_settings: persistedSettings, requested_settings_sha256: '2'.repeat(64), candidates: [], results: [],
            handoff: {
                parent_landscape_sha256: landscapeSha256,
                parent_candidate_id: 'parent-candidate-1',
                guidance_id: 'guidance-1',
                producer_id: 'producer-1',
            },
        };
        (api as unknown as { post: (url: string) => Promise<{ data: unknown }> }).post = async (url) => {
            if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
            if (url.endsWith('/settings/validate/upload')) return { data: validationPreview };
            if (url.endsWith('/candidates/handoff')) return { data: receipt };
            throw new Error(`unexpected launch ${url}`);
        };
        const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        try {
            await act(async () => root.render(
                <QueryClientProvider client={queryClient}>
                    <Panel parentJobId="job-1" parentInvocationId="invoke-1" parentLandscapeSha256={landscapeSha256} guidanceId="guidance-1" />
                </QueryClientProvider>,
            ));
            const inputs = Array.from(container.querySelectorAll<HTMLInputElement>('input'));
            const setValue = (input: HTMLInputElement, value: string) => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
                setter.call(input, value);
                input.dispatchEvent(new Event('input', { bubbles: true }));
            };
            await act(async () => {
                setValue(inputs.find((input) => input.placeholder === 'variant-1')!, 'candidate-1');
                setValue(inputs.find((input) => input.placeholder === 'external-redesign')!, 'producer-1');
            });
            const fileInput = inputs.find((input) => input.type === 'file')!;
            Object.defineProperty(fileInput, 'files', {
                configurable: true,
                value: [new File(['ATOM'], 'candidate.pdb', { type: 'chemical/x-pdb' })],
            });
            await act(async () => fileInput.dispatchEvent(new Event('change', { bubbles: true })));
            await settle(250);
            const launch = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Queue FrustraMPNN reanalysis'))!;
            await act(async () => launch.click());
            await settle();

            expect(container.textContent).toContain('Child queued: child-job-1');
            expect(container.textContent).toContain('Parent candidate: parent-candidate-1');
            expect(container.textContent).toContain('Producer: producer-1');
            expect(container.textContent).toContain('Guidance: guidance-1');
            expect(container.textContent).toContain(landscapeSha256);
            await act(async () => root.unmount());
        } finally {
            queryClient.clear();
            (api as unknown as { post: typeof originalPost }).post = originalPost;
        }
    });
});
