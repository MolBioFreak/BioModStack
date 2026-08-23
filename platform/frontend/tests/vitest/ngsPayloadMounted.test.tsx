import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
    commitMolBioSequenceImport: vi.fn(),
    createMolBioNgsReference: vi.fn(),
    fetchFiles: vi.fn(),
    fetchMolBioNgsReferenceRevisions: vi.fn(),
    fetchMolBioNgsReferences: vi.fn(),
    fetchMolBioNgsStateRevision: vi.fn(),
    fetchMolBioSequenceRevisions: vi.fn(),
    fetchNucleotideSequences: vi.fn(),
    importMolBioNgsBrowserReference: vi.fn(),
    issueMolBioNgsReceipt: vi.fn(),
    previewMolBioSequenceImport: vi.fn(),
    submitOntNgsJob: vi.fn(),
    submitPooledReferenceAssignment: vi.fn(),
}));

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

import { NanoporeTemplate } from '../../src/components/NanoporeTemplate';

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchNucleotideSequences.mockResolvedValue({ data: [] });
    apiMocks.fetchMolBioNgsReferences.mockResolvedValue([{ id: 'reference-1', name: 'Reference one' }]);
    apiMocks.fetchMolBioNgsReferenceRevisions.mockResolvedValue([{
        id: 'reference-revision-1',
        revision_number: 1,
        canonical_fasta_sha256: 'a'.repeat(64),
        molecule_type: 'dna',
        topology: 'circular',
    }]);
    apiMocks.fetchMolBioNgsStateRevision.mockResolvedValue({
        id: 'state-1',
        members: [{ role: 'ngs_reference', entity_kind: 'ngs_reference_revision', entity_id: 'reference-revision-1' }],
    });
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

describe('mounted NGS settings to submit payload', () => {
    it('submits visible assembly, GPU, reference, and POD5 input settings as one request', async () => {
        await renderTemplate({
            selectedWorkflow: 'constructScreening',
            inputSource: 'pod5',
            jobName: 'construct-pod5-run',
            pod5Dir: '/data/pod5',
            runFastqQc: true,
            runAssembly: false,
            ngsReferenceRevisionId: 'reference-revision-1',
        });
        const assembly = checkboxContaining('Consensus assembly');
        const gpu = container.querySelector<HTMLSelectElement>('[data-testid="ngs-gpu-assignment"]');
        const submit = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Review and submit');

        expect(assembly?.checked).toBe(false);
        expect(gpu?.value).toBe('');
        expect(submit?.disabled).toBe(false);

        await act(async () => {
            assembly?.click();
            if (gpu) {
                gpu.value = '2';
                gpu.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        await flush();

        expect(assembly?.checked).toBe(true);
        expect(gpu?.value).toBe('2');

        const updatedSubmit = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Review and submit');
        expect(updatedSubmit?.disabled).toBe(false);
        await act(async () => updatedSubmit?.click());
        await flush();

        expect(apiMocks.submitOntNgsJob).toHaveBeenCalledTimes(1);
        const [workflowId, request] = apiMocks.submitOntNgsJob.mock.calls[0] as [string, { pinned_gpu: number | null; params: Record<string, unknown> }];
        expect(workflowId).toBe('ont_construct_screening');
        expect(request.pinned_gpu).toBe(2);
        expect(request.params).toMatchObject({
            pod5_dir: '/data/pod5',
            run_assembly: true,
        });
        expect(request.params).not.toHaveProperty('run_multimer_qc');
    });

    it('shows FASTQ as CPU-only and omits a stale GPU pin from the request', async () => {
        await renderTemplate({
            selectedWorkflow: 'constructScreening',
            inputSource: 'fastq',
            jobName: 'fastq-cpu-only',
            fastqPath: '/data/input.fastq',
            runFastqQc: true,
            runAssembly: false,
            pinned_gpu: 2,
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        expect(container.querySelector('[data-testid="ngs-gpu-assignment"]')).toBeNull();
        expect(container.querySelector('[data-testid="ngs-review-gpu"]')?.textContent).toContain('CPU ONLY');

        const fastqQc = checkboxContaining('FASTQ plasmid QC');
        expect(fastqQc?.checked).toBe(true);
        await act(async () => fastqQc?.click());
        expect(fastqQc?.checked).toBe(false);

        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.submitOntNgsJob).toHaveBeenCalledTimes(1);
        const [, request] = apiMocks.submitOntNgsJob.mock.calls[0] as [string, { pinned_gpu: number | null; params: Record<string, unknown> }];
        expect(request.pinned_gpu).toBeNull();
        expect(request.params.run_fastq_qc).toBe(false);
    });

    it('renders the four accessible task-flow sections in mobile order and desktop two-row layout', async () => {
        await renderTemplate({
            selectedWorkflow: 'constructScreening',
            inputSource: 'pod5',
            pod5Dir: '/data/pod5',
            jobName: 'pod5-construct-run',
            runFastqQc: true,
            runAssembly: false,
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        const sections = [
            container.querySelector<HTMLElement>('[data-testid="ngs-job-input-section"]'),
            container.querySelector<HTMLElement>('[data-ngs-section="reference"]'),
            container.querySelector<HTMLElement>('[data-testid="ngs-basecalling-section"]'),
            container.querySelector<HTMLElement>('[data-testid="ngs-analysis-section"]'),
        ];
        expect(sections.every(Boolean)).toBe(true);
        expect(sections.map((section) => section?.querySelector(':scope > h2')?.textContent?.trim())).toEqual([
            '1 · Job and input',
            '2 · Reference / sample',
            '3 · Basecalling and quality',
            '4 · Analysis and advanced controls',
        ]);
        for (const section of sections) {
            const heading = section?.querySelector<HTMLHeadingElement>(':scope > h2');
            expect(heading?.id).toBeTruthy();
            expect(section?.getAttribute('aria-labelledby')).toBe(heading?.id);
            expect(section?.className).not.toContain('xl:col-span-2');
        }
        for (let index = 1; index < sections.length; index += 1) {
            expect(Boolean(sections[index - 1]!.compareDocumentPosition(sections[index]!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
        }
        expect(sections[0]?.parentElement?.className).toContain('xl:grid-cols-2');
    });

    it('keeps pooled reference assignment inside Section 2 without removing any task-flow section', async () => {
        await renderTemplate({
            selectedWorkflow: 'pooledAssignment',
            inputSource: 'fastq',
            jobName: 'pooled-reference-assignment',
            fastqPath: '/data/pooled.fastq',
        });

        const sections = [
            container.querySelector<HTMLElement>('[data-testid="ngs-job-input-section"]'),
            container.querySelector<HTMLElement>('[data-ngs-section="reference"]'),
            container.querySelector<HTMLElement>('[data-testid="ngs-basecalling-section"]'),
            container.querySelector<HTMLElement>('[data-testid="ngs-analysis-section"]'),
        ];
        expect(sections.every(Boolean)).toBe(true);
        const referenceSection = sections[1];
        expect(referenceSection?.querySelector('[data-testid="pooled-reference-assignment-panel"]')).not.toBeNull();
        expect(referenceSection?.textContent).toContain('Pooled FASTQ reference assignment');
    });

    it('keeps Section 3 visible for FASTQ with an already-basecalled state', async () => {
        await renderTemplate();

        const basecalling = container.querySelector<HTMLElement>('[data-testid="ngs-basecalling-section"]');
        expect(basecalling).not.toBeNull();
        expect(basecalling?.textContent).toContain('Already basecalled');
        expect(basecalling?.textContent).toContain('Basecalling is not applicable to FASTQ input.');
    });

    it('renders workflow separately from input, mode, model, GPU, and reference in review', async () => {
        await renderTemplate();

        expect(container.querySelector('[data-testid="ngs-review-bar"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ngs-review-workflow"]')?.textContent).toContain('CONSTRUCT SCREENING');
        expect(container.querySelector('[data-testid="ngs-review-input"]')?.textContent).toContain('INPUT READY');
        expect(container.querySelector('[data-testid="ngs-review-mode"]')?.textContent).toContain('MODE');
        expect(container.querySelector('[data-testid="ngs-review-model"]')?.textContent).toContain('MODEL');
        expect(container.querySelector('[data-testid="ngs-review-gpu"]')?.textContent).toContain('CPU ONLY');
        expect(container.querySelector('[data-testid="ngs-review-reference"]')).not.toBeNull();
        expect(buttonWithText('Validate')).not.toBeNull();
        expect(buttonWithText('Review and submit')).not.toBeNull();
    });

    it('reports every submit blocker through Validate without submitting', async () => {
        await renderTemplate({
            selectedWorkflow: 'modified',
            inputSource: 'pod5',
            jobName: '',
            pod5Dir: '',
            pinnedGpus: [2, 3],
            doradoMolecule: 'rna',
            doradoMode: 'duplex',
            duplexPairs: '',
            barcodeKit: 'SQK-RBK114-96',
            modifiedBases: '5mC_5hmC',
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        await act(async () => buttonWithText('Validate')?.click());
        await flush();

        const alert = Array.from(container.querySelectorAll('[role="alert"]'))
            .map((element) => element.textContent ?? '')
            .join(' ');
        for (const blocker of [
            'Enter a job name.',
            'Select one GPU or Scheduler auto before submitting this NGS job.',
            'Please specify a POD5 data directory.',
            'RNA duplex is unsupported by the locked Dorado runtime.',
            'Duplex basecalling requires a confined read-pairs file.',
            'Barcode classification and duplex cannot be combined in the locked runtime.',
            'Modified-base calling requires DNA HAC simplex.',
        ]) {
            expect(alert).toContain(blocker);
        }
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

    it('exposes pressed input-source state and controls the advanced disclosure', async () => {
        await renderTemplate({
            selectedWorkflow: 'clone',
            inputSource: 'fastq',
            jobName: 'clone-run',
            fastqPath: '/data/input.fastq',
            runFastqQc: false,
            runAssembly: true,
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        const pod5 = buttonWithText('POD5 Raw Reads');
        const bam = buttonWithText('Existing BAM');
        const fastq = buttonWithText('FASTQ Analysis');
        expect(pod5?.getAttribute('aria-pressed')).toBe('false');
        expect(bam?.getAttribute('aria-pressed')).toBe('false');
        expect(fastq?.getAttribute('aria-pressed')).toBe('true');

        const disclosure = buttonWithText('Show advanced controls');
        expect(disclosure?.getAttribute('aria-controls')).toBe('ngs-advanced-controls');
        await act(async () => disclosure?.click());
        expect(container.querySelector('#ngs-advanced-controls')).not.toBeNull();
    });

    it('restores Clone and BAM-QC workflow selections with FASTQ QC off by default', async () => {
        await renderTemplate({
            selectedWorkflow: 'dna',
            inputSource: 'pod5',
            jobName: 'workflow-defaults',
            pod5Dir: '/data/pod5',
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        await act(async () => container.querySelector<HTMLButtonElement>('[data-ngs-workflow-key="clone"]')?.click());
        expect(checkboxContaining('FASTQ plasmid QC')?.checked).toBe(false);

        await act(async () => container.querySelector<HTMLButtonElement>('[data-ngs-workflow-key="bamQc"]')?.click());
        expect(checkboxContaining('FASTQ plasmid QC')?.checked).toBe(false);
    });
    it('reopens persisted effective stage settings in the visible controls', async () => {
        await renderTemplate({
            selectedWorkflow: 'constructScreening',
            inputSource: 'pod5',
            jobName: 'reopened-construct-run',
            pod5Dir: '/data/pod5',
            runFastqQc: false,
            runAssembly: true,
            pinned_gpu: 2,
            ngsReferenceRevisionId: 'reference-revision-1',
        });

        expect(checkboxContaining('Consensus assembly')?.checked).toBe(true);
        expect(container.querySelector<HTMLSelectElement>('[data-testid="ngs-gpu-assignment"]')?.value).toBe('2');
    });
});
