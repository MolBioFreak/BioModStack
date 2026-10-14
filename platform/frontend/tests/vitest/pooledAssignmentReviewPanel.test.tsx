import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const pooled = vi.hoisted(() => ({
    fetchManifest: vi.fn(),
    fetchTargets: vi.fn(),
    release: vi.fn(),
    prepare: vi.fn(),
    executionTargets: vi.fn(async () => ({ data: [] as unknown[] })),
}));

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/lib/api')>()),
    fetchExecutionTargets: pooled.executionTargets,
    preparePooledAssignmentRelease: pooled.prepare,
    fetchPooledAssignmentManifest: pooled.fetchManifest,
    fetchPooledAssignmentTargets: pooled.fetchTargets,
    releasePooledAssignment: pooled.release,
}));

import { api } from '../../src/lib/api';
const originalAdapter = api.defaults.adapter;

import { PooledAssignmentReviewPanel } from '../../src/components/ngs/PooledAssignmentReviewPanel';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

const manifest = {
    assignment_job_id: 'assignment-job-001',
    reference_set_id: 'reference-set-001',
    manifest_id: 'manifest-001',
    manifest_sha256: 'a'.repeat(64),
    scientific_status: 'REVIEW' as const,
    execution_status: 'completed',
};

const targets = [
    {
        target_id: 'target-a',
        label: 'Target A',
        sequence_id: 'sequence-a',
        revision_id: 'revision-a',
        revision_sha256: 'b'.repeat(64),
        indistinguishable_group: null,
    },
    {
        target_id: 'target-b',
        label: 'Target B',
        sequence_id: 'sequence-b',
        revision_id: 'revision-b',
        revision_sha256: 'c'.repeat(64),
        indistinguishable_group: 'same-sequence-1',
    },
    {
        target_id: 'ambiguous',
        label: 'Ambiguous assignment',
        sequence_id: 'sequence-ambiguous',
        revision_id: 'revision-ambiguous',
        revision_sha256: 'd'.repeat(64),
        disposition: 'ambiguous',
    },
    {
        target_id: 'unclassified',
        label: 'Unclassified reads',
        sequence_id: 'sequence-unclassified',
        revision_id: 'revision-unclassified',
        revision_sha256: 'e'.repeat(64),
        disposition: 'unclassified',
    },
];

const releaseResponse = {
    release_id: 'release-001',
    assignment_job_id: 'assignment-job-001',
    reference_set_id: 'reference-set-001',
    child_job_ids: ['child-job-a', 'child-job-b'],
};

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
            assertion();
            return;
        } catch {
            await flush();
        }
    }
    assertion();
}

async function renderPanel() {
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <PooledAssignmentReviewPanel
                    jobId="assignment-job-001"
                    jobStatus="completed"
                    mode="pooled_reference_assignment"
                    ontWorkflowId="ont_pooled_reference_assignment"
                    stageOutputs={{ pooled_reference_assignment: [
                        'bms_results/assignment-job-001/assignment_summary.json',
                        'bms_results/assignment-job-001/per_read_assignment.tsv',
                        'bms_results/assignment-job-001/intended_pool.igv_session.json',
                    ] }}
                    files={{ assignment_summary: 'bms_results/assignment-job-001/assignment_summary.json' }}
                    results={{ per_read_assignment: 'bms_results/assignment-job-001/per_read_assignment.tsv' }}
                />
            </QueryClientProvider>,
        );
    });
}

beforeEach(() => {
    api.defaults.adapter = async config => {
        if (config.url !== '/api/jobs/assignment-job-001/ngs-artifacts') throw Error(`Unexpected request: ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: { job_id: 'assignment-job-001', artifacts:
            ['assignment_summary.json', 'per_read_assignment.tsv', 'intended_pool.igv_session.json'].map((filename, i) => ({
                filename, artifact_id: String(i), state: 'present', source: 'pooled_assignment', kind: 'report', size_bytes: 10,
                url: `/api/jobs/assignment-job-001/ngs-artifacts/${i}`,
            })) } };
    };
    pooled.fetchManifest.mockReset();
    pooled.fetchTargets.mockReset();
    pooled.release.mockReset();
    pooled.prepare.mockReset();
    pooled.executionTargets.mockResolvedValue({ data: [] });
    pooled.fetchManifest.mockResolvedValue({ data: manifest });
    pooled.fetchTargets.mockResolvedValue({ data: { assignment_job_id: manifest.assignment_job_id, reference_set_id: manifest.reference_set_id, targets } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(client, 'invalidateQueries');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    api.defaults.adapter = originalAdapter;
    document.body.replaceChildren();
});

describe('PooledAssignmentReviewPanel', () => {
    it('reviews both prepared targets once and reuses the retained release after a failed commit', async () => {
        pooled.executionTargets.mockResolvedValue({ data: [{ id: 'worker-b', name: 'Independent worker', active: true, state: 'ready', capabilities: {} }] });
        const plan = (digest: string) => ({ schema: 'bms.job.execution-preview.v1', approval_digest: digest.repeat(64), admissible: true,
            request: { model_id: 'nanopore', mode: 'plasmid_qc', execution_target_id: 'worker-b' },
            plan: { requested_json: {}, effective_json: {}, source_identity: { revision: 'source', tree: 'tree' },
                metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] });
        pooled.prepare.mockImplementation(async (_job, request) => ({ data: { request: { ...request, release_id: 'retained-release' }, request_fingerprint: 'f'.repeat(64), previews: { 'target-a': plan('a'), 'target-b': plan('b') } } }));
        pooled.release.mockRejectedValueOnce(new Error('retryable commit failure')).mockResolvedValueOnce({ data: releaseResponse });
        await renderPanel();
        await waitUntil(() => expect(container.textContent).toContain('Independent worker'));
        const button = (text: string) => [...document.querySelectorAll('button')].find(b => b.textContent?.includes(text))!;
        await act(async () => {
            button('Independent worker').click();
            container.querySelector<HTMLInputElement>('[aria-label="Explicitly select target-a"]')!.click();
            container.querySelector<HTMLInputElement>('[aria-label="Explicitly select target-b"]')!.click();
        });
        await act(async () => button('Release selected targets').click());
        await waitUntil(() => expect(document.body.textContent).toContain('Review remote execution plan'));
        expect(document.body.textContent).toContain('target-a');
        expect(document.body.textContent).toContain('target-b');
        expect(pooled.release).not.toHaveBeenCalled();
        await act(async () => button('Approve and submit').click());
        await waitUntil(() => expect(container.textContent).toContain('retryable commit failure'));
        await act(async () => button('Release selected targets').click());
        await waitUntil(() => expect(document.body.textContent).toContain('Review remote execution plan'));
        await act(async () => button('Approve and submit').click());
        await waitUntil(() => expect(pooled.release).toHaveBeenCalledTimes(2));
        expect(pooled.prepare).toHaveBeenCalledTimes(1);
        expect(pooled.release.mock.calls[1]).toEqual(pooled.release.mock.calls[0]);
        expect(pooled.release.mock.calls[0][1]).toMatchObject({ execution_target_id: 'worker-b', release_id: 'retained-release', execution_plan_approvals: { 'target-a': 'a'.repeat(64), 'target-b': 'b'.repeat(64) } });
    });
    it('keeps scientific REVIEW distinct from completed execution and does not release automatically', async () => {
        await renderPanel();
        await waitUntil(() => expect(container.textContent).toContain('Target A'));

        expect(container.querySelector('[data-testid="pooled-assignment-execution-status"]')?.textContent).toContain('completed');
        expect(container.querySelector('[data-testid="pooled-assignment-scientific-status"]')?.textContent).toContain('REVIEW');
        expect(pooled.release).not.toHaveBeenCalled();
        expect(container.querySelectorAll('input[aria-label^="Explicitly select"]')).toHaveLength(2);
        expect(container.querySelector('[aria-label="Explicitly select ambiguous"]')).toBeNull();
        expect(container.querySelector('[aria-label="Explicitly select unclassified"]')).toBeNull();
        await waitUntil(() => expect(container.querySelector('[data-testid="pooled-assignment-review-artifacts"]')).not.toBeNull());
        expect(container.querySelector('a[href^="/api/files/"]')).toBeNull();
        expect(container.querySelectorAll('a[href^="/api/jobs/assignment-job-001/ngs-artifacts/"]')).toHaveLength(3);
        expect(container.textContent).toContain('sequence-a');
        expect(container.textContent).toContain('revision-a');
        expect(container.textContent).toContain('same-sequence-1');
        expect(container.querySelector('[aria-label="Execution target"]')).not.toBeNull();
        const releaseButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Release selected targets')) as HTMLButtonElement;
        expect(releaseButton.disabled).toBe(true);
    });

    it('sends one atomic explicit release and keeps one idempotency key across a failed retry', async () => {
        await renderPanel();
        await waitUntil(() => expect(container.textContent).toContain('Target B'));

        const checkboxes = Array.from(container.querySelectorAll('input[aria-label^="Explicitly select"]')) as HTMLInputElement[];
        await act(async () => {
            checkboxes[0]?.click();
            checkboxes[1]?.click();
        });

        const workflow = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="ont_construct_screening"]'))!;
        await act(async () => {
            workflow.value = 'ont_construct_screening';
            workflow.dispatchEvent(new Event('change', { bubbles: true }));
        });

        pooled.release.mockRejectedValueOnce(new Error('temporary release failure'));
        pooled.release.mockResolvedValueOnce({ data: releaseResponse });
        const releaseButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Release selected targets')) as HTMLButtonElement;
        expect(releaseButton.disabled).toBe(false);

        await act(async () => releaseButton.click());
        await flush();
        expect(pooled.release).toHaveBeenCalledTimes(1);
        const firstCall = pooled.release.mock.calls[0];
        expect(firstCall[0]).toBe('assignment-job-001');
        expect(firstCall[1].idempotency_key).toEqual(expect.any(String));
        expect(firstCall[1].idempotency_key).toMatch(/\S/);
        expect(firstCall[1]).toMatchObject({
            target_workflow: 'ont_construct_screening',
            target_ids: ['target-a', 'target-b'],
        });
        expect(firstCall[1]).not.toHaveProperty('name_prefix');
        expect(firstCall[1]).not.toHaveProperty('pinned_gpu');

        await act(async () => releaseButton.click());
        await flush();
        expect(pooled.release).toHaveBeenCalledTimes(2);
        const secondCall = pooled.release.mock.calls[1];
        expect(secondCall[1].idempotency_key).toBe(firstCall[1].idempotency_key);
        expect(secondCall[1].target_ids).toEqual(['target-a', 'target-b']);
        expect(container.textContent).toContain('child-job-a');
        expect(container.textContent).toContain('child-job-b');
        expect(client.invalidateQueries).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['jobs'] }));
    });
});
