// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

const mocks = vi.hoisted(() => ({
    preview: vi.fn(), create: vi.fn(), fetchJob: vi.fn(), artifact: vi.fn(), review: vi.fn(),
    fetchReviews: vi.fn(), update: vi.fn(), cancel: vi.fn(), fresh: vi.fn(),
}));
vi.mock('../../src/lib/api', () => ({
    previewOntSignalIdealComparison: mocks.preview,
    createOntSignalIdealComparison: mocks.create,
    fetchOntSignalIdealComparison: mocks.fetchJob,
    fetchOntSignalComparisonArtifact: mocks.artifact,
    createOntSignalComparisonReview: mocks.review,
    fetchOntSignalComparisonReviews: mocks.fetchReviews,
    updateOntSignalViewerSession: mocks.update,
    cancelOntSignalIdealComparison: mocks.cancel,
    createFreshOntSignalIdealComparisonAttempt: mocks.fresh,
}));

import { OntSignalIdealComparison } from '../../src/components/ngs/OntSignalIdealComparison';
import type { OntSignalComparisonJob, OntSignalComparisonPreview } from '../../src/lib/api';

let root: Root;
let container: HTMLDivElement;
const viewer = {
    viewer_session_id: 'viewer-1', dataset_id: 'dataset-1', run_id: 'run-1', observed_generation: 3,
    reference_revision_id: 'reference-7', revision: 4, signal_state: {},
};
const renderParams = {
    strand: 'forward', signal_units: 'pA', scale: 'none', base_shift_source: 'profile', base_shift_value: 0,
    fixed_width: false, base_width: 10, point_size: 0.5, base_limit: 1000, signal_sample_limit: 100000,
    pileup_read_limit: 20, loose_bound: false, show_samples: true, show_base_colours: true,
    remove_signal_outliers: false, managed_bed_artifact_id: null,
};
const preview = {
    viewer_session_id: 'viewer-1', viewer_session_revision: 4, run_id: 'run-1', observed_generation: 3,
    raw_representation_id: 'raw-1', raw_manifest_sha256: 'a'.repeat(64),
    mapping_artifact_id: 'mapping-artifact-1', mapping_artifact_sha256: 'c'.repeat(64),
    mapping_job_id: 'ref-map-1', mapping_profile_id: 'map-profile-1', reference_revision_id: 'reference-7',
    reference_artifact_id: 'ref-artifact-1', reference_fasta_sha256: 'b'.repeat(64),
    reference_topology: 'linear', coordinate_contract: '1-based closed', selected_read_id: 'read-42',
    selected_read_span: { contig: 'chr7', start: 500, end: 560, strand: 'reverse' },
    simulation_orientation: 'reverse', derived_window: { contig: 'chr7', start: 495, end: 565 },
    compatibility_disposition: 'approximate_profile', warnings: ['R10 model is an approximation.'],
    effective_request: { authority: {}, reference_interval: { contig: 'chr7', start: 500, end: 560 }, effective_settings: {
        schema: 'bms.ont-squigulator-ideal-comparison-effective.v1',
        operator_owned: { profile_id: 'dna-r10-min', seed: 7 }, profile_id: 'dna-r10-min',
        profile: { molecule_type: 'dna', flow_cell_generation: 'R10.4.1', device_class: 'MinION',
            pore_model_identity: 'builtin:MODEL_ID_DNA_R10_NUCLEOTIDE', kmer_length: 9,
            digitisation: 8192, sample_rate: 5000, translocation_speed: 400, range: 1536.598389,
            offset_mean: 13.380569389019, offset_standard_deviation: 16.311471649012,
            median_before_mean: 202.15407438804, median_before_standard_deviation: 13.406139241768,
            dwell_mean: 13, dwell_standard_deviation: 4, model_quality_warning: 'Parameters are crude.',
            compatibility_floor: 'approximate_profile' }, workflow_fixed: {},
        compatibility_floor: 'approximate_profile', warnings: ['Parameters are crude.'], upstream: {},
    } }, preview_digest: 'd'.repeat(64),
};
const previewWindowContract: OntSignalComparisonPreview['derived_window'] = preview.derived_window;
const previewWindowContig: string = previewWindowContract.contig;

const effectiveSettings = {
    ...preview.effective_request.effective_settings,
    operator_owned: {
        profile_id: 'rna004-prom', seed: 19, scale: 'znorm', point_size: 2,
        fixed_width: true, base_width: 12, base_limit: 900,
        signal_sample_limit: 80000, show_samples: false,
        show_base_colours: false, remove_signal_outliers: true,
    },
    profile_id: 'rna004-prom',
};
const immutableRenderParams = {
    scale: 'medmad', point_size: 3, fixed_width: true, base_width: 14,
    base_limit: 850, signal_sample_limit: 70000, show_samples: false,
    show_base_colours: true, remove_signal_outliers: true,
};
const requestedLifecycleShape: Pick<OntSignalComparisonJob, 'stage_receipts' | 'resource_snapshot' | 'output_manifest'> = {
    stage_receipts: {
        squigulator_producer: null,
        squigualiser_comparison_renderer: null,
        lease_recoveries: null,
    },
    resource_snapshot: { parents: null },
    output_manifest: {
        schema: null,
        parents: null,
        runtime_identities: null,
        stage_receipts: null,
        artifacts: null,
        producer: null,
        renderer: null,
    },
};

function comparisonJob(overrides: Record<string, unknown> = {}) {
    return {
        comparison_job_id: 'comparison-1', state: 'requested', reason_code: 'comparison_requested',
        artifacts: [], ...requestedLifecycleShape, simulation_settings: effectiveSettings,
        render_params: immutableRenderParams, attempt_number: 1, predecessor_job_id: null,
        request_fingerprint: 'e'.repeat(64), viewer_session_id: 'viewer-1', viewer_session_revision: 4,
        run_id: 'run-1', observed_generation: 3, selected_read_id: 'read-42', reference_contig: 'chr7',
        reference_start: 500, reference_end: 560, reference_revision_id: 'reference-7',
        raw_representation_id: 'raw-1', mapping_artifact_id: 'mapping-artifact-1',
        simulation_orientation: 'reverse', sequence_basis: 'managed_reference', generated_read_id: null,
        preview_digest: 'd'.repeat(64), failure_code: null, failure_message: null,
        created_at: '2026-08-27T00:00:00Z', updated_at: '2026-08-27T00:00:00Z', completed_at: null,
        ...overrides,
    };
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; });
    return { promise, resolve, reject };
}

function findButton(label: string) {
    const item = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === label);
    if (!item) throw new Error(`missing button ${label}`);
    return item as HTMLButtonElement;
}
async function settle() { await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); }); }

beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.preview.mockResolvedValue(preview);
    mocks.fetchReviews.mockResolvedValue([]);
    mocks.update.mockResolvedValue(viewer);
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:comparison') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    mocks.create.mockResolvedValue(comparisonJob());
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); vi.useRealTimers(); });

async function render(options: { selectedReadId?: string; onViewerSessionChange?: ReturnType<typeof vi.fn> } = {}) {
    const onViewerSessionChange = options.onViewerSessionChange || vi.fn();
    await act(async () => root.render(<OntSignalIdealComparison datasetId="dataset-1" viewerSession={viewer as never}
        selectedReadId={options.selectedReadId || 'read-42'} contig="chr7" start={500} end={560} mappingJobId="ref-map-1"
        mappingArtifactId="mapping-artifact-1"
        renderParams={renderParams as never} onViewerSessionChange={onViewerSessionChange as never} />));
    await settle();
    return onViewerSessionChange;
}

describe('OntSignalIdealComparison', () => {
    it('requires a current preview digest and invalidates it after an operator edit', async () => {
        await render();
        expect(container.textContent).toContain('Ideal comparison');
        expect(findButton('Generate and compare').disabled).toBe(true);
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        expect(container.textContent).toContain('R10 model is an approximation.');
        expect(container.textContent).toContain('5000 Hz');
        expect(container.textContent).toContain('builtin:MODEL_ID_DNA_R10_NUCLEOTIDE');
        expect(findButton('Generate and compare').disabled).toBe(false);
        const seed = container.querySelector<HTMLInputElement>('[aria-label="Simulation seed"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '8');
            seed.dispatchEvent(new Event('input', { bubbles: true })); seed.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(findButton('Generate and compare').disabled).toBe(true);
    });

    it('submits only the exact preview digest and current typed settings', async () => {
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({
            viewer_session_id: 'viewer-1', expected_viewer_revision: 4,
            mapping_artifact_id: 'mapping-artifact-1',
            selected_read_id: 'read-42', preview_digest: 'd'.repeat(64),
            simulation_settings: { profile_id: 'dna-r10-min', seed: 7 },
            render_params: {
                scale: 'none', point_size: 0.5, fixed_width: false, base_width: 10,
                base_limit: 1000, signal_sample_limit: 100000, show_samples: true,
                show_base_colours: true, remove_signal_outliers: false,
            },
        }));
    });

    it('clears stale busy state on an identity generation change and disables editable settings while busy', async () => {
        const pendingPreview = deferred<typeof preview>();
        mocks.preview.mockReturnValueOnce(pendingPreview.promise);
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); });
        for (const label of ['Simulation profile', 'Simulation seed', 'Comparison scale',
            'Comparison point size', 'Comparison fixed width', 'Comparison base width',
            'Comparison base limit', 'Comparison signal sample limit', 'Comparison show samples',
            'Comparison show base colours', 'Comparison remove outliers']) {
            expect((container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement).disabled).toBe(true);
        }
        expect(findButton('Preview').disabled).toBe(true);

        await render({ selectedReadId: 'read-99' });

        expect(findButton('Preview').disabled).toBe(false);
        expect((container.querySelector('[aria-label="Simulation profile"]') as HTMLSelectElement).disabled).toBe(false);
        pendingPreview.resolve(preview);
        await settle();
        expect(container.textContent).not.toContain('R10 model is an approximation.');
    });

    it('does not let an older running poll replace a newer ready response', async () => {
        vi.useFakeTimers();
        const older = deferred<ReturnType<typeof comparisonJob>>();
        const newer = deferred<ReturnType<typeof comparisonJob>>();
        mocks.fetchJob.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); });
        await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); });
        await act(async () => {
            newer.resolve(comparisonJob({ state: 'ready', reason_code: 'ideal_comparison_ready' }));
            await Promise.resolve();
            older.resolve(comparisonJob({ state: 'running', reason_code: 'worker_claimed' }));
            for (let i = 0; i < 5; i += 1) await Promise.resolve();
        });
        expect(container.textContent).toContain('ready: ideal_comparison_ready');
        expect(container.textContent).not.toContain('running: worker_claimed');
    });

    it('does not let an older rejected poll publish after a newer success', async () => {
        vi.useFakeTimers();
        const older = deferred<ReturnType<typeof comparisonJob>>();
        const newer = deferred<ReturnType<typeof comparisonJob>>();
        mocks.fetchJob.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        await act(async () => { vi.advanceTimersByTime(3000); await Promise.resolve(); });
        await act(async () => {
            newer.resolve(comparisonJob({ state: 'running', reason_code: 'current_worker_claimed' }));
            await Promise.resolve();
            older.reject(new Error('stale polling failure'));
            for (let i = 0; i < 5; i += 1) await Promise.resolve();
        });
        expect(container.textContent).toContain('running: current_worker_claimed');
        expect(container.textContent).not.toContain('stale polling failure');
    });

    it('clears a current polling error after a later successful poll', async () => {
        vi.useFakeTimers();
        mocks.fetchJob.mockRejectedValueOnce(new Error('temporary polling failure'))
            .mockResolvedValueOnce(comparisonJob({ state: 'running', reason_code: 'poll_recovered' }));
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); }); await settle();
        expect(container.textContent).toContain('temporary polling failure');
        await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); }); await settle();
        expect(container.textContent).toContain('running: poll_recovered');
        expect(container.textContent).not.toContain('temporary polling failure');
    });

    it('aborts a stale viewer-session mutation after identity changes and cannot publish it', async () => {
        const pendingUpdate = deferred<typeof viewer>();
        const onViewerSessionChange = await render();
        mocks.update.mockReturnValueOnce(pendingUpdate.promise);
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();

        expect(mocks.update).toHaveBeenCalledOnce();
        const mutationSignal = mocks.update.mock.calls[0][2] as AbortSignal;
        expect(mutationSignal.aborted).toBe(false);

        await render({ selectedReadId: 'read-99', onViewerSessionChange });

        expect(mutationSignal.aborted).toBe(true);
        pendingUpdate.resolve({ ...viewer, revision: 5 });
        await settle();
        expect(onViewerSessionChange).not.toHaveBeenCalled();
        expect(findButton('Preview').disabled).toBe(false);
    });

    it('persists comparison settings only from the returned immutable job', async () => {
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();

        expect(mocks.update).toHaveBeenCalledWith('viewer-1', expect.objectContaining({
            signal_state: expect.objectContaining({
                comparison_settings: {
                    simulation_settings: { profile_id: 'rna004-prom', seed: 19 },
                    render_params: immutableRenderParams,
                },
            }),
        }), expect.any(AbortSignal));
    });

    it('exposes every supported profile and comparison render control', async () => {
        await render();
        expect(container.querySelector('option[value="rna004-prom"]')).not.toBeNull();
        for (const label of ['Comparison scale', 'Comparison point size', 'Comparison fixed width',
            'Comparison base width', 'Comparison base limit', 'Comparison signal sample limit',
            'Comparison show samples', 'Comparison show base colours', 'Comparison remove outliers']) {
            expect(container.querySelector(`[aria-label="${label}"]`)).not.toBeNull();
        }
        const pointSize = container.querySelector<HTMLSelectElement>('[aria-label="Comparison point size"]')!;
        expect(pointSize.tagName).toBe('SELECT');
        expect(Array.from(pointSize.options, (option) => Number(option.value))).toEqual([
            0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
        ]);
        expect(pointSize.querySelector('option[value="1.5"]')).toBeNull();
    });

    it('restores persisted immutable job settings and fixed profile disclosure on reopen', async () => {
        const persistedSettings = {
            ...preview.effective_request.effective_settings,
            operator_owned: { ...preview.effective_request.effective_settings.operator_owned,
                profile_id: 'rna004-prom', seed: 19, scale: 'znorm', point_size: 2,
                fixed_width: true, base_width: 12, base_limit: 900,
                signal_sample_limit: 80000, show_samples: false,
                show_base_colours: false, remove_signal_outliers: true },
            profile_id: 'rna004-prom',
            profile: { ...preview.effective_request.effective_settings.profile,
                molecule_type: 'rna', device_class: 'PromethION', model_quality_warning: 'Persisted warning.' },
            warnings: ['Persisted warning.'],
        };
        mocks.fetchJob.mockResolvedValue({
            comparison_job_id: 'comparison-persisted', state: 'ready', reason_code: 'ideal_comparison_ready',
            artifacts: [], simulation_settings: persistedSettings,
            render_params: persistedSettings.operator_owned, attempt_number: 1,
            predecessor_job_id: null, request_fingerprint: 'e'.repeat(64), viewer_session_id: 'viewer-1',
            viewer_session_revision: 4, run_id: 'run-1', observed_generation: 3,
            selected_read_id: 'read-42', reference_contig: 'chr7', reference_start: 500,
            reference_end: 560, reference_revision_id: 'reference-7', raw_representation_id: 'raw-1',
            mapping_artifact_id: 'mapping-artifact-1', simulation_orientation: 'reverse',
            sequence_basis: 'managed_reference', generated_read_id: 'sim-1', preview_digest: 'd'.repeat(64),
            resource_snapshot: {}, stage_receipts: {}, output_manifest: {}, failure_code: null,
            failure_message: null, created_at: '2026-08-27T00:00:00Z', updated_at: '2026-08-27T00:00:00Z',
            completed_at: '2026-08-27T00:01:00Z',
        });
        const reopened = { ...viewer, signal_state: { comparison_job_id: 'comparison-persisted' } };
        await act(async () => root.render(<OntSignalIdealComparison datasetId="dataset-1" viewerSession={reopened as never}
            selectedReadId="read-42" contig="chr7" start={500} end={560} mappingJobId="ref-map-1"
            mappingArtifactId="mapping-artifact-1" renderParams={renderParams as never} onViewerSessionChange={vi.fn()} />));
        await settle();
        expect((container.querySelector('[aria-label="Simulation profile"]') as HTMLSelectElement).value).toBe('rna004-prom');
        expect((container.querySelector('[aria-label="Simulation seed"]') as HTMLInputElement).value).toBe('19');
        expect(container.textContent).toContain('Persisted warning.');
        expect(container.textContent).toContain('PromethION');
    });

    it('accepts and renders the governed artifact with exact permanent runtime labels', async () => {
        mocks.create.mockResolvedValueOnce(comparisonJob({
            state: 'ready', reason_code: 'ideal_comparison_ready', generated_read_id: 'sim-1',
            artifacts: [{ artifact_id: 'html-1', kind: 'comparison_html' }],
            completed_at: '2026-08-27T00:01:00Z',
        }));
        mocks.artifact.mockResolvedValue(new Blob([
            '<!doctype html><html><head></head><body>REAL · INSTRUMENT ACQUIRED · read-42 SIMULATED IDEAL · SQUIGULATOR 0.5.0 · reference</body></html>',
        ], { type: 'text/html' }));
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        expect(mocks.artifact).toHaveBeenCalledWith('comparison-1', 'html-1');
        expect(container.querySelector('iframe')?.getAttribute('src')).toBe('blob:comparison');
        expect(container.querySelector('[role="alert"]')).toBeNull();
        const seed = container.querySelector<HTMLInputElement>('[aria-label="Simulation seed"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '8');
            seed.dispatchEvent(new Event('input', { bubbles: true })); seed.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:comparison');
    });

    it('ignores a stale cancellation completion after comparison identity changes', async () => {
        let resolveCancel!: (value: unknown) => void;
        mocks.cancel.mockReturnValue(new Promise((resolve) => { resolveCancel = resolve; }));
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Cancel comparison').click(); await Promise.resolve(); });
        const seed = container.querySelector<HTMLInputElement>('[aria-label="Simulation seed"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(seed, '8');
            seed.dispatchEvent(new Event('input', { bubbles: true })); seed.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { resolveCancel({ comparison_job_id: 'stale-job', state: 'cancelled' }); await Promise.resolve(); });
        await settle();
        expect(container.textContent).not.toContain('stale-job');
        expect(container.textContent).not.toContain('comparison-1 · attempt');
    });

    it('submits the governed manual review vocabulary and interval', async () => {
        mocks.create.mockResolvedValueOnce(comparisonJob({
            state: 'ready', reason_code: 'ideal_comparison_ready', generated_read_id: 'sim-1',
            completed_at: '2026-08-27T00:01:00Z',
        }));
        mocks.review.mockResolvedValue({ review_id: 'review-1', comparison_job_id: 'comparison-1',
            predecessor_review_id: null, review_question: 'Does the real trace visually agree with the ideal expectation?',
            required_outcome: 'approve', note: 'Matches.', reviewed_start: 500, reviewed_end: 560,
            comparison_html_artifact_id: 'html-1', comparison_html_sha256: 'f'.repeat(64),
            comparison_request_fingerprint: 'e'.repeat(64), reviewer_identity: 'operator', created_at: '2026-08-27T00:02:00Z' });
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        const note = container.querySelector<HTMLTextAreaElement>('[aria-label="Review note"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(note, 'Matches.');
            note.dispatchEvent(new Event('input', { bubbles: true })); note.dispatchEvent(new Event('change', { bubbles: true }));
        });
        const outcome = container.querySelector<HTMLSelectElement>('[aria-label="Review required outcome"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(outcome, 'approve');
            outcome.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { findButton('Record review revision').click(); await Promise.resolve(); }); await settle();
        expect(mocks.review).toHaveBeenCalledWith('comparison-1', {
            predecessor_review_id: null,
            review_question: 'Does the real trace visually agree with the ideal expectation?',
            required_outcome: 'approve', note: 'Matches.', reviewed_start: 500, reviewed_end: 560,
        });
        expect(mocks.update).toHaveBeenLastCalledWith('viewer-1', expect.objectContaining({
            signal_state: expect.objectContaining({
                comparison_review_id: 'review-1',
                comparison_settings: {
                    simulation_settings: { profile_id: 'rna004-prom', seed: 19 },
                    render_params: immutableRenderParams,
                },
            }),
        }), expect.any(AbortSignal));
    });

    it('disables ready manual-review controls while a review revision is being recorded', async () => {
        mocks.create.mockResolvedValueOnce(comparisonJob({
            state: 'ready', reason_code: 'ideal_comparison_ready', generated_read_id: 'sim-1',
            completed_at: '2026-08-27T00:01:00Z',
        }));
        mocks.review.mockReturnValueOnce(new Promise(() => undefined));
        await render();
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        await act(async () => { findButton('Generate and compare').click(); await Promise.resolve(); }); await settle();
        const note = container.querySelector<HTMLTextAreaElement>('[aria-label="Review note"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(note, 'Reviewed trace');
            note.dispatchEvent(new Event('input', { bubbles: true })); note.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => { findButton('Record review revision').click(); await Promise.resolve(); });
        expect(container.querySelector<HTMLSelectElement>('[aria-label="Review required outcome"]')!.disabled).toBe(true);
        expect(note.disabled).toBe(true);
    });
});
