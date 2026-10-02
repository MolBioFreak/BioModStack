import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
    prepareExecutionPlacement: vi.fn(() => ({ execution_target_id: null, execution_policy: { remote_result_policy: "manual" } })),
    commitMolBioSequenceImport: vi.fn(),
    createMolBioNgsReference: vi.fn(),
    fetchFiles: vi.fn(),
    fetchMolBioNgsReferenceRevision: vi.fn(),
    fetchMolBioNgsSummaries: vi.fn(),
    fetchMolBioNgsStateRevision: vi.fn(),
    fetchMolBioSequenceRevisions: vi.fn(),
    fetchNucleotideSequences: vi.fn(),
    importMolBioNgsBrowserReference: vi.fn(),
    issueMolBioNgsReceipt: vi.fn(),
    previewMolBioSequenceImport: vi.fn(),
    submitOntNgsJob: vi.fn(),
    submitPooledReferenceAssignment: vi.fn(),
}));

vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => <div>Local execution fixture</div> }));
vi.mock('../../src/lib/api', () => apiMocks);
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: 'workspace-1',
        globalExperimentId: 'experiment-1',
        stateRevisionId: 'state-1',
        selectedDomainExperiment: { domain_experiment_id: 'domain-1' },
        availability: { canMutateDomain: true, reason: '' },
        contextHref: (path: string) => path,
    }),
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({
    useLiveGpuCatalog: () => ({ gpuOptions: [{ index: 2, label: 'GPU 2' }] }),
}));

import { normalizeNanoporeCloneState } from '../../src/lib/nanoporeCloneState';

import { NanoporeTemplate } from '../../src/components/NanoporeTemplate';

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchNucleotideSequences.mockResolvedValue({ data: [] });
    const references = [3000, 12000].map((length, index) => ({
        id: `reference-revision-${index + 1}`, reference_id: `reference-${index + 1}`, name: `Reference ${index + 1}`,
        revision_number: 1, canonical_fasta_sha256: 'a'.repeat(64), global_domain_experiment_id: 'domain-1',
        payload: { contigs: [{ length }] },
    }));
    apiMocks.fetchMolBioNgsSummaries.mockResolvedValue({ next_cursor: null, total: 2, items: references });
    apiMocks.fetchMolBioNgsStateRevision.mockResolvedValue({ id: 'state-1', members: references.map((ref) => ({
        role: 'ngs_reference', entity_kind: 'ngs_reference_revision', entity_id: ref.id,
        reopen_destination: { params: { global_domain_experiment_id: 'domain-1', revision_id: ref.id, reference_id: ref.reference_id } },
    })) });
    apiMocks.fetchMolBioNgsReferenceRevision.mockImplementation(async (_reference, revision) => references.find((ref) => ref.id === revision));
    apiMocks.fetchMolBioSequenceRevisions.mockResolvedValue({ data: [] });
    apiMocks.submitOntNgsJob.mockResolvedValue({ data: { id: 'job-1' } });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
});

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

async function renderTemplate(initialValues: Record<string, unknown> = {
    selectedWorkflow: 'constructScreening',
    inputSource: 'fastq',
    jobName: 'construct-run',
    fastqPath: '/data/input.fastq',
    runFastqQc: true,
    runAssembly: false,
    ngsReferenceRevisionId: 'reference-revision-1',
}) {
    await act(async () => {
        root.render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <NanoporeTemplate onBack={vi.fn()} initialValues={initialValues} />
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
    await flush();
    await flush();
}

function checkboxContaining(text: string) {
    return Array.from(container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find((input) => input.parentElement?.textContent?.includes(text)) ?? null;
}

function buttonWithText(text: string) {
    return Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === text) ?? null;
}


function sizeMode() { return container.querySelector<HTMLSelectElement>('[aria-label="Expected plasmid size mode"]')!; }
function sizeInput() { return container.querySelector<HTMLInputElement>('[aria-label="Expected plasmid size (bp)"]')!; }
async function chooseMode(value: string) {
    await act(async () => { sizeMode().value = value; sizeMode().dispatchEvent(new Event('change', { bubbles: true })); });
}
async function editSize(value: string) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(sizeInput(), value);
        sizeInput().dispatchEvent(new Event('input', { bubbles: true }));
    });
}
async function submitSize() {
    expect(buttonWithText('Review and submit')?.disabled).toBe(false);
    await act(async () => buttonWithText('Review and submit')?.click());
    await flush();
    expect(apiMocks.submitOntNgsJob).toHaveBeenCalledTimes(1);
    return apiMocks.submitOntNgsJob.mock.calls[0][1].params.expected_plasmid_size;
}
async function refreshReference(length: number) {
    const index = length === 3000 ? 1 : 2;
    const revisionId = `reference-revision-${index}`;
    const select = Array.from(container.querySelectorAll('select')).find((el) => Array.from(el.options).some((option) => option.value === revisionId))!;
    await act(async () => {
        select.value = revisionId;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        queryClient.setQueryData(['molbio-ngs-selected-reference', 'domain-1', `reference-${index}`, revisionId], {
            id: revisionId, reference_id: `reference-${index}`, global_domain_experiment_id: 'domain-1', payload: { contigs: [{ length }] },
        });
    });
    await flush();
}

describe('expected plasmid size mounted authoring', () => {
    it.each([undefined, null, 'hiseq', 'hifi', 'r10.4_sup', 'r10.4_dup', 'ultima'])(
        'reopens consensus preset %s through clone and serialized save, then submits unchanged', async (preset) => {
            const initial = normalizeNanoporeCloneState({ id: 'consensus', name: 'consensus', model_id: 'nanopore', params: {
                fastq_path: '/data/input.fastq', ont_workflow_id: 'ont_construct_screening', run_fastq_qc: true,
                ngs_reference_revision_id: 'reference-revision-1', samtools_consensus_config: preset,
            } } as never)!;
            await renderTemplate(JSON.parse(JSON.stringify(initial)));
            const control = container.querySelector<HTMLSelectElement>('[aria-label="Samtools consensus preset"]')!;
            expect(Array.from(control.options).map((option) => option.value)).toEqual(['', 'hiseq', 'hifi', 'r10.4_sup', 'r10.4_dup', 'ultima']);
            expect(control.value).toBe(preset ?? '');
            await refreshReference(12000);
            expect(control.value).toBe(preset ?? '');
            await submitSize();
            expect(apiMocks.submitOntNgsJob.mock.calls[0][1].params.samtools_consensus_config).toBe(preset ?? null);
        },
    );
    it('lets unknown-input users explicitly choose and clear a consensus preset', async () => {
        await renderTemplate();
        const control = container.querySelector<HTMLSelectElement>('[aria-label="Samtools consensus preset"]')!;
        expect(control.value).toBe('');
        for (const value of ['r10.4_sup', '']) {
            await act(async () => { control.value = value; control.dispatchEvent(new Event('change', { bubbles: true })); });
            expect(control.value).toBe(value);
        }
        await submitSize();
        expect(apiMocks.submitOntNgsJob.mock.calls[0][1].params.samtools_consensus_config).toBeNull();
    });
    it('defaults to Auto, displays resolved reference updates and sends null', async () => {
        await renderTemplate();
        expect(sizeMode().value).toBe('auto');
        await refreshReference(3000);
        expect(container.textContent).toContain('Auto: 3,000 bp');
        await refreshReference(12000);
        expect(container.textContent).toContain('Auto: 12,000 bp');
        expect(await submitSize()).toBeNull();
    });
    it('preserves a multi-keystroke explicit edit through reference refresh and Auto round trip', async () => {
        await renderTemplate();
        await chooseMode('override');
        await editSize('');
        expect(sizeInput().value).toBe('');
        expect(buttonWithText('Review and submit')?.disabled).toBe(true);
        await editSize('7'); await editSize('700'); await editSize('7000');
        await refreshReference(12000);
        expect(sizeInput().value).toBe('7000');
        await chooseMode('auto');
        expect(container.textContent).toContain('Auto: 12,000 bp');
        await chooseMode('override');
        expect(sizeInput().value).toBe('7000');
        expect(await submitSize()).toBe(7000);
    });
    it.each(['pod5', 'bam'])('preserves explicit size on %s-derived QC reads too', async (inputSource) => {
        await renderTemplate({ selectedWorkflow: 'constructScreening', inputSource, pod5Dir: '/data/pod5', bamPath: '/data/reads.bam',
            jobName: 'derived-reads', runFastqQc: true, ngsReferenceRevisionId: 'reference-revision-1', expectedPlasmidSize: 7000 });
        expect(sizeMode().value).toBe('override');
        expect(await submitSize()).toBe(7000);
    });
    it.each([1, 100000000])('preserves existing positive boundary %s', async (size) => {
        await renderTemplate(); await chooseMode('override'); await editSize(String(size));
        expect(await submitSize()).toBe(size);
    });
    it.each([0, -1, 1.5, 100000001])('keeps existing invalid range %s invalid without clamping', async (size) => {
        await renderTemplate(); await chooseMode('override'); await editSize(String(size));
        expect(sizeInput().value).toBe(String(size));
        expect(buttonWithText('Review and submit')?.disabled).toBe(true);
    });
    it.each([
        [{ expected_plasmid_size: 7000 }, 7000],
        [{ expected_plasmid_size: 3000, requested_expected_plasmid_size: null }, 3000],
        [{}, 7000],
    ])('reopens retained execution size explicitly, then permits deliberate Auto: %j', async (params, expected) => {
        const initial = normalizeNanoporeCloneState({ id: 'retained', name: 'retained', model_id: 'nanopore', params: {
            fastq_path: '/data/input.fastq', ont_workflow_id: 'ont_construct_screening', run_fastq_qc: true,
            ngs_reference_revision_id: 'reference-revision-1', ...params,
        } } as never)!;
        await renderTemplate(initial);
        expect(sizeMode().value).toBe('override');
        expect(sizeInput().value).toBe(String(expected));
        await refreshReference(12000);
        expect(sizeInput().value).toBe(String(expected));
        await chooseMode('auto');
        expect(await submitSize()).toBeNull();
    });
    it('reopens an unresolved saved Auto request without turning null into 7000', async () => {
        const initial = normalizeNanoporeCloneState({ id: 'draft', name: 'draft', model_id: 'nanopore', params: {
            fastq_path: '/data/input.fastq', ont_workflow_id: 'ont_construct_screening', run_fastq_qc: true,
            ngs_reference_revision_id: 'reference-revision-1', expected_plasmid_size: null,
        } } as never)!;
        await renderTemplate(initial);
        expect(sizeMode().value).toBe('auto');
        expect(await submitSize()).toBeNull();
    });
});
