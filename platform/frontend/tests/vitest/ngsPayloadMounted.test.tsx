import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
    api: { post: vi.fn() },
    fetchJobs: vi.fn(),
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
    previewOntNgsJob: vi.fn(),
    fetchOntNgsSettingsContract: vi.fn(),
    fetchExecutionTargets: vi.fn(),
    EXECUTION_TARGET_STORAGE_KEY: "bms.jobLauncher.executionTargetId",
    restorePooledReferenceSet: vi.fn(),
    submitPooledReferenceAssignment: vi.fn(),
}));

vi.mock('../../src/lib/api', () => apiMocks);
const contextMock = vi.hoisted(() => ({
    workspaceId: 'workspace-1',
    globalExperimentId: 'experiment-1',
    stateRevisionId: 'state-1' as string | null,
    selectedDomainExperiment: { domain_experiment_id: 'domain-1' } as { domain_experiment_id: string } | null,
    availability: { canMutateDomain: true, reason: '' },
    contextHref: (path: string) => path,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => contextMock,
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({
    useLiveGpuCatalog: () => ({ gpuOptions: [{ index: 2, label: 'GPU 2' }] }),
}));

import { NanoporeTemplate } from '../../src/components/NanoporeTemplate';
import { buildNanoporeHandoff } from '../../src/lib/nanoporeHandoff';
import { normalizeNanoporeCloneState } from '../../src/lib/nanoporeCloneState';

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    contextMock.selectedDomainExperiment = { domain_experiment_id: 'domain-1' };
    contextMock.stateRevisionId = 'state-1';
    contextMock.availability = { canMutateDomain: true, reason: '' };
    apiMocks.fetchExecutionTargets.mockResolvedValue({ data: [] });
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
    apiMocks.previewOntNgsJob.mockImplementation(async (workflowId, request) => ({ data: {
        schema: 'bms.ont.launch-preview.v1', workflow_id: workflowId,
        requested_settings: request, effective_request: { params: request.params },
        blockers: [], warnings: [], preview_digest: 'c'.repeat(64),
    } }));
    apiMocks.fetchOntNgsSettingsContract.mockResolvedValue({ data: { profile_fixed: {
        wf_clone_basecaller_model: { value: 'dna_r10.4.1_e8.2_400bps_hac@v5.0.0', reason: 'Pinned clone profile.' },
    } } });
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

it('binds final launch to the reviewed preview and independent reference-free context', async () => {
    await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/inputs/pod5', jobName: 'review' });
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    expect(apiMocks.previewOntNgsJob.mock.calls[0][1].experiment_context).toEqual({
        global_domain_experiment_id: 'domain-1', molbio_ngs_state_revision_id: 'state-1',
    });
    expect(container.textContent).toContain('c'.repeat(64));
    await act(async () => buttonWithText('Confirm reviewed launch')!.click()); await flush();
    expect(apiMocks.submitOntNgsJob.mock.calls[0][1].preview_digest).toBe('c'.repeat(64));
});

it('discards a preview when an operator changes a control', async () => {
    await renderTemplate();
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(buttonWithText('Confirm reviewed launch')).not.toBeNull();
    await act(async () => checkboxContaining('Evaluate circular-reference rotations')!.click());
    expect(buttonWithText('Confirm reviewed launch')).toBeNull();
    expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
});

it.each(['pod5', 'bam'])('preserves the full active plasmid QC settings for %s', async (inputSource) => {
    await renderTemplate({ selectedWorkflow: 'plasmidQc', inputSource, jobName: 'qc', pod5Dir: '/inputs/pod5',
        bamPath: '/inputs/reads.bam', runFastqQc: true, ngsReferenceRevisionId: 'reference-revision-1',
        expectedPlasmidSize: 8123, minFastqReadLength: 37, fastqMinimap2Preset: 'map-pb',
        fastqMinimap2AllowSecondary: false, igvTrackWindowBp: 145, igvReportMaxSites: 22, igvReportFlankingBp: 0,
    });
    expect(container.textContent).toContain('IGV track/report tuning');
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(apiMocks.previewOntNgsJob.mock.calls[0][1].params).toMatchObject({
        expected_plasmid_size: 8123, min_fastq_read_length: 37, fastq_minimap2_preset: 'map-pb',
        fastq_minimap2_allow_secondary: false, igv_track_window_bp: 145,
        igv_report_max_sites: 22, igv_report_flanking_bp: 0,
    });
});

it.each(['alias', 'canonical', 'matching', 'conflict'])('preserves basecalling alias parity through native handoff (%s)', async (variant) => {
    const params = {
        pod5_dir: '/inputs/pod5', duplex_pairs: '/inputs/pairs.tsv',
        ...(variant !== 'canonical' ? { basecalling_mode: 'duplex' } : {}),
        ...(variant !== 'alias' ? { dorado_basecall_mode: variant === 'conflict' ? 'simplex' : 'duplex' } : {}),
    };
    await renderTemplate(buildNanoporeHandoff({ name: 'duplex-alias', mode: 'basecall_dna', params }, '').values);
    if (variant === 'conflict') {
        expect(container.textContent).toContain('one exact choice');
        expect(buttonWithText('Review and submit')).toBeNull();
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
        return;
    }
    expect([...container.querySelectorAll('select')].some((select) => select.value === 'duplex')).toBe(true);
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(apiMocks.previewOntNgsJob.mock.calls[0][1].params).toMatchObject({ dorado_basecall_mode: 'duplex', duplex_pairs: '/inputs/pairs.tsv' });
});

it.each([true, false])('preserves BAM modkit intent independently of Dorado modifications (%s)', async (enabled) => {
    await renderTemplate(buildNanoporeHandoff({ name: 'bam-modkit', mode: 'methylation_analysis', params: {
        bam_path: '/inputs/tagged.bam', run_modkit: enabled, modkit_filter_threshold: 0,
        ngs_reference_revision_id: 'reference-revision-1',
    } }, '').values);
    const control = checkboxContaining('Run modkit analysis');
    expect(control).not.toBeNull();
    expect(control?.checked).toBe(enabled);
    expect(buttonWithText('Review and submit')?.disabled).toBe(false);
    await act(async () => buttonWithText('Review and submit')!.click());
    await flush();
    const params = apiMocks.previewOntNgsJob.mock.calls[0][1].params;
    expect(params.run_modkit).toBe(enabled);
    if (enabled) expect(params.modkit_filter_threshold).toBe(0);
    else expect(params).not.toHaveProperty('modkit_filter_threshold');
    expect(params).not.toHaveProperty('modified_bases');
});

it('rejects carried modkit intent without modified-base input rather than disabling it', async () => {
    await renderTemplate(buildNanoporeHandoff({ name: 'invalid-modkit', mode: 'methylation_analysis', params: {
        pod5_dir: '/inputs/pod5', run_modkit: true, modified_bases: 'none',
        ngs_reference_revision_id: 'reference-revision-1',
    } }, '').values);
    expect(buttonWithText('Review and submit')?.disabled).toBe(true);
    expect(container.textContent).toContain('Modkit requires');
    expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
});

it('retains an unsaved generic reference path as an import hint, never a different automatic reference', async () => {
    const values = buildNanoporeHandoff({ name: 'generic-qc', mode: 'plasmid_qc', params: {
        fastq_path: '/inputs/reads.fastq', reference_fasta: '/inputs/original.fasta', min_fastq_read_length: 123,
    } }, '').values;
    apiMocks.importMolBioNgsBrowserReference.mockResolvedValue({ id: 'imported-reference' });
    await renderTemplate(values);
    expect(container.textContent).toContain('/inputs/original.fasta');
    expect(buttonWithText('Review and submit')?.disabled).toBe(true);
    expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    await act(async () => buttonWithText('Import carried reference')!.click()); await flush();
    expect(apiMocks.importMolBioNgsBrowserReference).toHaveBeenCalledWith(expect.objectContaining({
        global_domain_experiment_id: 'domain-1', entry: expect.objectContaining({ source: 'path', path: '/inputs/original.fasta' }),
    }));
});

it.each(['match', 'wrong-revision', 'wrong-digest', 'wrong-sequence'])('restores frozen pooled targets with fresh receipt integrity (%s)', async (receiptCase) => {
    apiMocks.restorePooledReferenceSet.mockResolvedValue({ data: {
        reference_set_id: 'frozen-set', manifest_sha256: 'f'.repeat(64),
        targets: [1, 2].map((n) => ({ target_id: `frozen-${n}`, label: `Frozen ${n}`,
            sequence_id: `seq-${n}`, revision_id: `rev-${n}`, revision_sha256: String(n).repeat(64), indistinguishable_group: 'same-pool' })),
    } });
    apiMocks.issueMolBioNgsReceipt.mockImplementation(async (sequence: string, request: { revision_id: string }) => ({ data: {
        receipt_id: `fresh-${sequence}`, sequence_id: receiptCase === 'wrong-sequence' ? 'other-sequence' : sequence,
        revision_id: receiptCase === 'wrong-revision' ? 'current-revision' : request.revision_id,
        revision_sha256: receiptCase === 'wrong-digest' ? '0'.repeat(64) : sequence.endsWith('1') ? '1'.repeat(64) : '2'.repeat(64),
    } }));
    apiMocks.submitPooledReferenceAssignment.mockResolvedValue({ data: { assignment_job_id: 'new-assignment' } });
    await renderTemplate({ selectedWorkflow: 'pooledAssignment', inputSource: 'fastq', fastqPath: '/data/unsaved.fastq',
        referenceSetManifest: '/inputs/frozen/reference_set.json', pooledAssignmentMinMapq: 37,
        pooledAssignmentMinAlignmentScoreMargin: 1234, jobName: 'unsaved-pool', pinnedGpus: [2] });
    expect(apiMocks.restorePooledReferenceSet).toHaveBeenCalledWith('/inputs/frozen/reference_set.json');
    expect(container.textContent).toContain('rev-1');
    expect(container.querySelector<HTMLSelectElement>('[aria-label="GPU assignment"]')?.value).toBe('2');
    await act(async () => buttonWithText('Submit pooled assignment')!.click());
    await flush();
    expect(apiMocks.issueMolBioNgsReceipt).toHaveBeenCalledWith('seq-1', { revision_id: 'rev-1' });
    if (receiptCase !== 'match') {
        expect(apiMocks.submitPooledReferenceAssignment).not.toHaveBeenCalled();
        expect(container.textContent).toContain('does not match the frozen revision');
        return;
    }
    expect(apiMocks.submitPooledReferenceAssignment).toHaveBeenCalledWith(expect.objectContaining({
        fastq_path: '/data/unsaved.fastq', min_mapq: 37, min_alignment_score_margin: 1234,
        name: 'unsaved-pool', pinned_gpu: 2,
        targets: [1, 2].map((n) => ({ target_id: `frozen-${n}`, label: `Frozen ${n}`,
            indistinguishable_group: 'same-pool', molbio_ngs_receipt_id: `fresh-seq-${n}` })),
    }));
});

it.each([false, true])('recovers the selected original pooled job then restores and submits fresh receipts (denied=%s)', async (denied) => {
    let recovered = false;
    const frozen = { reference_set_id: 'original-set', assignment_job_id: 'original-job', manifest_sha256: 'f'.repeat(64),
        targets: [1, 2].map((n) => ({ target_id: `t${n}`, label: `Target ${n}`, sequence_id: `s${n}`, revision_id: `r${n}`, revision_sha256: String(n).repeat(64) })) };
    apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [{ id: 'original-job', name: 'Original pooled review', status: 'awaiting_input', model_id: 'nanopore', mode: 'pooled_reference_assignment' }], total: 1 } });
    apiMocks.restorePooledReferenceSet.mockImplementation(async () => {
        if (!recovered) throw new Error('Original capability expired');
        return { data: frozen };
    });
    apiMocks.api.post.mockImplementation(async (url: string) => {
        expect(url).toBe('/api/jobs/original-job/alignment-access/rotate');
        if (denied) throw new Error('Application operator authority is required');
        recovered = true;
        return { data: { schema: 'bms.ngs.rotation-success.v1', job_id: 'original-job', rotated: true,
            scheme: 'opaque_job_capability_v1', rotation_count: 1, expires_at: '2030-01-01T00:00:00Z' } };
    });
    apiMocks.issueMolBioNgsReceipt.mockImplementation(async (sequence: string, request: { revision_id: string }) => ({ data: {
        receipt_id: `fresh-${sequence}`, sequence_id: sequence, revision_id: request.revision_id, revision_sha256: sequence.endsWith('1') ? '1'.repeat(64) : '2'.repeat(64),
    } }));
    apiMocks.submitPooledReferenceAssignment.mockResolvedValue({ data: { assignment_job_id: 'new-assignment' } });
    await renderTemplate(buildNanoporeHandoff({ name: 'retry-pool', mode: 'pooled_reference_assignment', params: {
        fastq_path: '/inputs/a.fastq', reference_set_manifest: '/inputs/frozen.json',
    } }, '').values);
    await flush();
    expect(buttonWithText('Submit pooled assignment')?.disabled).toBe(true);
    const selector = container.querySelector<HTMLSelectElement>('[aria-label="Original pooled assignment"]');
    expect(selector).not.toBeNull();
    await act(async () => { selector!.value = 'original-job'; selector!.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => buttonWithText('Recover access and retry restoration')!.click());
    await flush(); await flush();
    if (denied) {
        expect(container.textContent).toContain('Application operator authority');
        expect(buttonWithText('Submit pooled assignment')?.disabled).toBe(true);
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
        return;
    }
    expect(apiMocks.restorePooledReferenceSet).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('s1 / r1');
    await act(async () => buttonWithText('Submit pooled assignment')!.click()); await flush();
    expect(apiMocks.submitPooledReferenceAssignment).toHaveBeenCalledWith(expect.objectContaining({ targets: [
        { target_id: 't1', label: 'Target 1', molbio_ngs_receipt_id: 'fresh-s1' },
        { target_id: 't2', label: 'Target 2', molbio_ngs_receipt_id: 'fresh-s2' },
    ] }));
});

it('blocks pooled submission when frozen restoration fails rather than replacing targets', async () => {
    apiMocks.restorePooledReferenceSet.mockRejectedValue(new Error('Recover original assignment access'));
    await renderTemplate({ selectedWorkflow: 'pooledAssignment', inputSource: 'fastq', fastqPath: '/data/a.fastq', referenceSetManifest: '/inputs/frozen.json' });
    expect(container.textContent).toContain('Recover original assignment access');
    expect(buttonWithText('Submit pooled assignment')?.disabled).toBe(true);
    expect(apiMocks.submitPooledReferenceAssignment).not.toHaveBeenCalled();
});

const auxiliaryPaths = {
    wf_clone_primers: '/data/primers.fasta',
    wf_clone_insert_reference: '/data/insert.fasta',
    wf_clone_host_reference: '/data/host.fasta',
    wf_clone_regions_bedfile: '/data/regions.bed',
};
function cloneValues(workflow: string, params: Record<string, unknown> = {}) {
    return normalizeNanoporeCloneState({ name: 'cloned-run', params: {
        ont_workflow_id: workflow, fastq_path: '/data/reads.fastq',
        global_domain_experiment_id: 'domain-1', molbio_ngs_state_revision_id: 'state-1',
        ngs_reference_revision_id: 'reference-revision-1', ...params,
    } } as never)!;
}

const nativeBinding = {
    sequence_id: 'saved-sequence', revision_id: 'saved-revision',
    revision_sha256: 'b'.repeat(64), reference_snapshot_sha256: 'c'.repeat(64),
    receipt_id: 'consumed-receipt', receipt_schema: 'bms.molbio.ngs-receipt.v2',
    binding_source: 'server_consumed_receipt',
};

async function renderNativeClone(params: Record<string, unknown> = {}) {
    apiMocks.fetchMolBioNgsStateRevision.mockResolvedValue({ id: 'state-1', members: [{
        entity_kind: 'molecular_revision', entity_id: 'saved-revision',
        reopen_destination: { params: { sequence_id: 'saved-sequence', revision_id: 'saved-revision' } },
    }] });
    apiMocks.issueMolBioNgsReceipt.mockResolvedValue({ data: { ...nativeBinding, receipt_id: 'fresh-receipt' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ panels: [] }) }));
    await renderTemplate(cloneValues('ont_plasmid_qc', {
        ngs_reference_revision_id: undefined, molbio_revision_binding: nativeBinding,
        molbio_ngs_receipt_id: 'consumed-receipt', ...params,
    }));
}

afterEach(() => vi.unstubAllGlobals());

describe('native-bound clones', () => {
    it('issues a fresh receipt for the saved exact pair, ignoring unrelated URL authority', async () => {
        window.history.replaceState({}, '', '/?molbio_sequence_id=other&molbio_revision_id=current');
        try {
            await renderNativeClone();
            expect(buttonWithText('Review and submit')?.disabled).toBe(false);
            await act(async () => buttonWithText('Review and submit')?.click());
            await flush();
            expect(apiMocks.issueMolBioNgsReceipt).toHaveBeenCalledWith('saved-sequence', { revision_id: 'saved-revision' });
            expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
            const request = apiMocks.previewOntNgsJob.mock.calls[0][1];
            expect(request.params.molbio_ngs_receipt_id).toBe('fresh-receipt');
            expect(request.managed_reference).toBeUndefined();
            expect(request.params).not.toHaveProperty('molbio_revision_binding');
        } finally { window.history.replaceState({}, '', '/'); }
    });

    it.each([undefined, {}, { ...nativeBinding, revision_id: '' }, { ...nativeBinding, revision_sha256: 'bad' }])('refuses missing or malformed binding %j', async (binding) => {
        await renderNativeClone({ molbio_revision_binding: binding });
        expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/cannot reuse/i);
        expect(buttonWithText('Review and submit')).toBeNull();
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
    });

    it('refuses a mismatched saved context', async () => {
        await renderNativeClone({ molbio_ngs_state_revision_id: 'other-state' });
        expect(container.textContent).toMatch(/saved.*context/i);
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
    });

    it('refuses native authority outside the exact state membership', async () => {
        await renderNativeClone();
        queryClient.setQueryData(['molbio-ngs-state-revision', 'domain-1', 'state-1'], { id: 'state-1', members: [] });
        await flush();
        expect(buttonWithText('Review and submit')?.disabled).toBe(true);
        expect(container.textContent).toMatch(/member/i);
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
    });

    it.each(['sequence_id', 'revision_id', 'revision_sha256', 'reference_snapshot_sha256', 'receipt_id', 'unresolvable'])('does not submit when fresh receipt authority fails: %s', async (field) => {
        await renderNativeClone();
        if (field === 'unresolvable') apiMocks.issueMolBioNgsReceipt.mockRejectedValue(new Error('Immutable revision not found'));
        else apiMocks.issueMolBioNgsReceipt.mockResolvedValue({ data: {
            ...nativeBinding, receipt_id: 'fresh-receipt', [field]: field === 'receipt_id' ? 'consumed-receipt' : 'mismatch',
        } });
        expect(buttonWithText('Review and submit')?.disabled).toBe(false);
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.issueMolBioNgsReceipt).toHaveBeenCalledTimes(1);
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });
});

describe('mounted clone round trips', () => {
    it.each(['unknown', 'ont_pooled_reference_assignment'])('visibly refuses unsupported clone %s without exposing a substitute launcher', async (workflow) => {
        await renderTemplate(cloneValues(workflow));
        expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/cannot reuse/i);
        expect(buttonWithText('Review and submit')).toBeNull();
        expect(container.querySelector('[data-testid="pooled-reference-assignment-panel"]')).toBeNull();
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

    it.each(['global_domain_experiment_id', 'molbio_ngs_state_revision_id'])('refuses to rebind saved %s to current context', async (key) => {
        await renderTemplate(cloneValues('ont_plasmid_qc', { [key]: 'different-context' }));
        expect(container.textContent).toMatch(/saved.*context/i);
        expect(buttonWithText('Review and submit')?.disabled ?? true).toBe(true);
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

    it.each([
        { global_domain_experiment_id: undefined }, { molbio_ngs_state_revision_id: undefined },
        { pod5_dir: '/data/pod5' }, { fastq_path: undefined },
    ])('refuses incomplete or ambiguous saved authority: %j', async (params) => {
        await renderTemplate(cloneValues('ont_plasmid_qc', params));
        expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/cannot reuse/i);
        expect(buttonWithText('Review and submit')).toBeNull();
    });

    it('does not replace cloned reference authority with an unrelated molecular URL handoff', async () => {
        window.history.replaceState({}, '', '/?molbio_sequence_id=other-sequence&molbio_revision_id=other-revision');
        try {
            await renderTemplate(cloneValues('ont_plasmid_qc'));
            await act(async () => buttonWithText('Review and submit')?.click());
            await flush();
            expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
            expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
            expect(apiMocks.previewOntNgsJob.mock.calls[0][1].managed_reference.ngs_reference_revision_id).toBe('reference-revision-1');
        } finally {
            window.history.replaceState({}, '', '/');
        }
    });

    it('refuses missing frozen reference authority instead of automatically choosing another reference', async () => {
        await renderTemplate(cloneValues('ont_plasmid_qc', { ngs_reference_revision_id: undefined, reference_fasta: '/old/reference.fasta' }));
        expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/reference/i);
        expect(buttonWithText('Review and submit')).toBeNull();
    });

    it.each([
        ['ont_basecall_dna', 'pod5'], ['ont_basecall_rna', 'pod5'],
        ...['ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation'].flatMap((workflow) => ['pod5', 'bam', 'fastq'].map((input) => [workflow, input])),
        ['ont_methylation_analysis', 'pod5'], ['ont_methylation_analysis', 'bam'], ['ont_fastq_qc', 'fastq'],
    ])('preserves exact %s operation and %s input through clone and submit', async (workflow, input) => {
        const inputKey = input === 'pod5' ? 'pod5_dir' : `${input}_path`;
        const path = `/data/${input}-input`;
        await renderTemplate(cloneValues(workflow, {
            fastq_path: undefined, [inputKey]: path,
            run_assembly: true, ont_molecule_type: workflow === 'ont_basecall_rna' ? 'rna' : 'dna',
            dorado_quality_mode: 'hac', modified_bases: workflow === 'ont_methylation_analysis' ? '5mC_5hmC' : 'none',
            min_qscore: 0, wf_clone_min_quality: 0, wf_clone_expected_identity: 98.25,
        }));
        expect(buttonWithText('Review and submit')?.disabled).toBe(false);
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        const [actualWorkflow, request] = apiMocks.previewOntNgsJob.mock.calls[0];
        expect(actualWorkflow).toBe(workflow);
        expect(request.params[inputKey]).toBe(path);
        expect(request.managed_reference).toEqual({ global_domain_experiment_id: 'domain-1', molbio_ngs_state_revision_id: 'state-1', ngs_reference_revision_id: 'reference-revision-1' });
        if (input === 'pod5') expect(request.params.min_qscore).toBe(0);
        if (workflow === 'wf_clone_validation' || workflow === 'ont_construct_screening') {
            expect(request.params.wf_clone_min_quality).toBe(0);
            expect(request.params.wf_clone_expected_identity).toBe(98.25);
        }
    });

    it.each(['wf_clone_validation', 'ont_construct_screening'])('hydrates auxiliary selectors and submits exact paths for %s', async (workflow) => {
        await renderTemplate(cloneValues(workflow, { run_assembly: true, ...auxiliaryPaths }));
        await act(async () => buttonWithText('Show advanced controls')?.click());
        for (const [key, value] of Object.entries(auxiliaryPaths)) {
            expect(container.querySelector(`[data-testid="${key}"]`)?.textContent).toContain(value);
        }
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob.mock.calls[0]?.[1].params).toMatchObject(auxiliaryPaths);
    });

    it('uses the existing confined file browser to replace each auxiliary value and clears without resubmitting it', async () => {
        await renderTemplate(cloneValues('wf_clone_validation', { run_assembly: true, ...auxiliaryPaths }));
        await act(async () => buttonWithText('Show advanced controls')?.click());
        const replacements: Record<string, string> = {};
        for (const key of Object.keys(auxiliaryPaths)) {
            const path = `data/replaced-${key}.file`;
            replacements[key] = path;
            apiMocks.fetchFiles.mockResolvedValue({ data: { entries: [{ name: `replaced-${key}.file`, path, is_directory: false }] } });
            await act(async () => container.querySelector<HTMLButtonElement>(`[data-testid="${key}"] button`)?.click());
            await flush();
            await flush();
            expect(apiMocks.fetchFiles).toHaveBeenLastCalledWith('data');
            expect(buttonWithText('Select')).not.toBeNull();
            await act(async () => buttonWithText('Select')?.click());
            expect(container.querySelector(`[data-testid="${key}"]`)?.textContent).toContain(path);
        }
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Clear Primers file"]')?.click());
        delete replacements.wf_clone_primers;
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        expect(apiMocks.previewOntNgsJob.mock.calls[0][1].params).toMatchObject(replacements);
        expect(apiMocks.previewOntNgsJob.mock.calls[0][1].params).not.toHaveProperty('wf_clone_primers');
    });

    it.each(['ont_construct_screening', 'ont_plasmid_qc'])('omits inactive auxiliary paths for %s', async (workflow) => {
        await renderTemplate(cloneValues(workflow, { run_assembly: false, ...auxiliaryPaths }));
        await act(async () => buttonWithText('Show advanced controls')?.click());
        for (const key of Object.keys(auxiliaryPaths)) expect(container.querySelector(`[data-testid="${key}"]`)).toBeNull();
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        for (const key of Object.keys(auxiliaryPaths)) expect(apiMocks.previewOntNgsJob.mock.calls[0]?.[1].params).not.toHaveProperty(key);
    });
});

describe('mounted NGS settings to submit payload', () => {
    it.each([
        { selectedWorkflow: 'dna' },
        { selectedWorkflow: 'rna', doradoMolecule: 'rna' },
        { selectedWorkflow: 'dna', doradoMode: 'duplex', duplexPairs: '/data/pairs.tsv' },
        { selectedWorkflow: 'dna', barcodeKit: 'SQK-RBK114-96' },
    ])('launches POD5-only basecalling without inventing reference authority: %j', async (settings) => {
        await renderTemplate({ ...settings, inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'basecall-only' });
        expect(buttonWithText('Review and submit')?.disabled).toBe(false);
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        const [workflow, request] = apiMocks.previewOntNgsJob.mock.calls[0];
        expect(workflow).toBe(settings.selectedWorkflow === 'rna' ? 'ont_basecall_rna' : 'ont_basecall_dna');
        expect(request.params.pod5_dir).toBe('/data/pod5');
        expect(request).not.toHaveProperty('managed_reference');
        expect(request.params).not.toHaveProperty('molbio_ngs_receipt_id');
        expect(request.params).not.toHaveProperty('reference_fasta');
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
    });

    it('does not carry an automatically chosen required reference into basecalling', async () => {
        await renderTemplate({ selectedWorkflow: 'constructScreening', inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'switch-to-basecalling' });
        await act(async () => container.querySelector<HTMLButtonElement>('[data-ngs-workflow-key="dna"]')?.click());
        await flush();
        expect(buttonWithText('Review and submit')?.disabled).toBe(false);
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        expect(apiMocks.previewOntNgsJob.mock.calls[0][1]).not.toHaveProperty('managed_reference');
    });

    it.each(['empty', 'unavailable'])('launches basecalling with an %s reference library', async (library) => {
        if (library === 'empty') apiMocks.fetchMolBioNgsReferences.mockResolvedValue([]);
        else apiMocks.fetchMolBioNgsReferences.mockRejectedValue(new Error('offline'));
        await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'no-reference' });
        expect(buttonWithText('Review and submit')?.disabled).toBe(false);
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        expect(apiMocks.previewOntNgsJob.mock.calls[0][1]).not.toHaveProperty('managed_reference');
    });

    it.each(['domain', 'state', 'permission'])('retains %s ownership gating for reference-free basecalling', async (missing) => {
        if (missing === 'domain') contextMock.selectedDomainExperiment = null;
        if (missing === 'state') contextMock.stateRevisionId = null;
        if (missing === 'permission') contextMock.availability = { canMutateDomain: false, reason: 'Read only' };
        await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'no-reference' });
        expect(buttonWithText('Review and submit')?.disabled).toBe(true);
        expect(apiMocks.issueMolBioNgsReceipt).not.toHaveBeenCalled();
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

    it.each([true, false])('validates explicit optional basecalling reference state membership: %s', async (member) => {
        if (!member) apiMocks.fetchMolBioNgsStateRevision.mockResolvedValue({ id: 'state-1', members: [] });
        await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'optional-reference', ngsReferenceRevisionId: 'reference-revision-1' });
        expect(buttonWithText('Review and submit')?.disabled).toBe(!member);
        if (member) {
            await act(async () => buttonWithText('Review and submit')?.click());
            await flush();
            expect(apiMocks.previewOntNgsJob.mock.calls[0][1].managed_reference.ngs_reference_revision_id).toBe('reference-revision-1');
        } else expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

    it('locks named FASTQ QC on even when reopened with a stale false value', async () => {
        await renderTemplate({ selectedWorkflow: 'fastqQc', inputSource: 'fastq', fastqPath: '/data/reads.fastq', jobName: 'qc', runFastqQc: false, ngsReferenceRevisionId: 'reference-revision-1' });
        const qc = checkboxContaining('FASTQ plasmid QC');
        expect(qc?.checked).toBe(true);
        expect(qc?.disabled).toBe(true);
        await act(async () => qc?.click());
        await act(async () => buttonWithText('Review and submit')?.click());
        await flush();
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        const [workflow, request] = apiMocks.previewOntNgsJob.mock.calls[0];
        expect(workflow).toBe('ont_fastq_qc');
        expect(request.params.run_fastq_qc).toBe(true);
        expect(request.managed_reference.ngs_reference_revision_id).toBe('reference-revision-1');
    });

    it('blocks named FASTQ QC without reference authority', async () => {
        apiMocks.fetchMolBioNgsReferences.mockResolvedValue([]);
        await renderTemplate({ selectedWorkflow: 'fastqQc', inputSource: 'fastq', fastqPath: '/data/reads.fastq', jobName: 'qc' });
        expect(buttonWithText('Review and submit')?.disabled).toBe(true);
        expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
    });

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

        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        const [workflowId, request] = apiMocks.previewOntNgsJob.mock.calls[0] as [string, { pinned_gpu: number | null; params: Record<string, unknown> }];
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
        expect(apiMocks.previewOntNgsJob).toHaveBeenCalledTimes(1);
        const [, request] = apiMocks.previewOntNgsJob.mock.calls[0] as [string, { pinned_gpu: number | null; params: Record<string, unknown> }];
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


it('binds the selected remote target to preview and never replaces an unavailable selection with Local', async () => {
    await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/inputs/pod5',
        jobName: 'remote', execution_target_id: 'vast:123' });
    expect(container.textContent).toContain('Selected worker vast:123 is unavailable');
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(apiMocks.previewOntNgsJob.mock.calls[0][1].execution_target_id).toBe('vast:123');
    await act(async () => buttonWithText('Confirm reviewed launch')!.click()); await flush();
    expect(apiMocks.submitOntNgsJob.mock.calls[0][1].execution_target_id).toBe('vast:123');
});

it('changing execution target discards the previous launch preview', async () => {
    await renderTemplate({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/inputs/pod5',
        jobName: 'remote', execution_target_id: 'vast:123' });
    await act(async () => buttonWithText('Review and submit')!.click()); await flush();
    expect(buttonWithText('Confirm reviewed launch')).not.toBeNull();
    await act(async () => buttonWithText('Local')!.click()); await flush();
    expect(buttonWithText('Confirm reviewed launch')).toBeNull();
    expect(apiMocks.submitOntNgsJob).not.toHaveBeenCalled();
});
