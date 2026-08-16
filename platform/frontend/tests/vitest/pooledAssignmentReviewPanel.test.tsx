import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const pooled = vi.hoisted(() => ({
    fetchManifest: vi.fn(),
    fetchTargets: vi.fn(),
    release: vi.fn(),
}));

vi.mock('../../src/lib/api', () => ({
    fetchPooledAssignmentManifest: pooled.fetchManifest,
    fetchPooledAssignmentTargets: pooled.fetchTargets,
    releasePooledAssignment: pooled.release,
}));

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
    pooled.fetchManifest.mockReset();
    pooled.fetchTargets.mockReset();
    pooled.release.mockReset();
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
    document.body.replaceChildren();
});

describe('PooledAssignmentReviewPanel', () => {
    it('keeps scientific REVIEW distinct from completed execution and does not release automatically', async () => {
        await renderPanel();
        await waitUntil(() => expect(container.textContent).toContain('Target A'));

        expect(container.querySelector('[data-testid="pooled-assignment-execution-status"]')?.textContent).toContain('completed');
        expect(container.querySelector('[data-testid="pooled-assignment-scientific-status"]')?.textContent).toContain('REVIEW');
        expect(pooled.release).not.toHaveBeenCalled();
        expect(container.querySelectorAll('input[type="checkbox"]')).toHaveLength(2);
        expect(container.querySelector('[aria-label="Explicitly select ambiguous"]')).toBeNull();
        expect(container.querySelector('[aria-label="Explicitly select unclassified"]')).toBeNull();
        expect(container.querySelector('[data-testid="pooled-assignment-review-artifacts"]')).not.toBeNull();
        expect(container.textContent).toContain('sequence-a');
        expect(container.textContent).toContain('revision-a');
        expect(container.textContent).toContain('same-sequence-1');
        expect(container.querySelectorAll('button').length).toBe(1);
        const releaseButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Release selected targets')) as HTMLButtonElement;
        expect(releaseButton.disabled).toBe(true);
    });

    it('sends one atomic explicit release and keeps one idempotency key across a failed retry', async () => {
        await renderPanel();
        await waitUntil(() => expect(container.textContent).toContain('Target B'));

        const checkboxes = Array.from(container.querySelectorAll('input[type="checkbox"]')) as HTMLInputElement[];
        await act(async () => {
            checkboxes[0]?.click();
            checkboxes[1]?.click();
        });

        const workflow = container.querySelector('select') as HTMLSelectElement;
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
