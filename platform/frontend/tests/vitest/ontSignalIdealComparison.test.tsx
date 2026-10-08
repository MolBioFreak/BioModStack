// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

const mocks = vi.hoisted(() => ({
    preview: vi.fn(), create: vi.fn(), fetchJob: vi.fn(), artifact: vi.fn(), review: vi.fn(), update: vi.fn(),
}));
vi.mock('../../src/lib/api', () => ({
    previewOntSignalIdealComparison: mocks.preview,
    createOntSignalIdealComparison: mocks.create,
    fetchOntSignalIdealComparison: mocks.fetchJob,
    fetchOntSignalComparisonArtifact: mocks.artifact,
    createOntSignalComparisonReview: mocks.review,
    updateOntSignalViewerSession: mocks.update,
}));

import { OntSignalIdealComparison } from '../../src/components/ngs/OntSignalIdealComparison';

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
    raw_representation_id: 'raw-1', raw_manifest_sha256: 'a'.repeat(64), move_source_id: 'move-1',
    mapping_profile_id: 'map-profile-1', signal_to_read_mapping_job_id: 'read-map-1',
    signal_to_reference_mapping_job_id: 'ref-map-1', reference_revision_id: 'reference-7',
    reference_artifact_id: 'ref-artifact-1', reference_fasta_sha256: 'b'.repeat(64),
    selected_read_id: 'read-42', selected_read_inventory_sha256: 'c'.repeat(64), contig: 'chr7',
    requested_start: 500, requested_end: 560, derived_start: 495, derived_end: 565, orientation: 'reverse',
    profile: { profile_id: 'dna-r10-min', molecule_type: 'dna', pore_family: 'r10', model_id: 'dna-r10',
        sample_rate_hz: 5000, nucleotide_type: 'dna', full_contigs: true, output_format: 'blow5',
        source_profile_id: 'dna-r10-min', approximation: true },
    effective_simulation_settings: { profile_id: 'dna-r10-min', seed: 7 }, effective_render_params: renderParams,
    warnings: [{ code: 'profile_approximation', message: 'R10 model is an approximation.' }], blockers: [],
    compatibility: 'compatible_with_warning', preview_digest: 'd'.repeat(64),
};

function findButton(label: string) {
    const item = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === label);
    if (!item) throw new Error(`missing button ${label}`);
    return item as HTMLButtonElement;
}
async function settle() { await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); }); }

beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.preview.mockResolvedValue(preview);
    mocks.create.mockResolvedValue({ comparison_job_id: 'comparison-1', state: 'requested', reason_code: 'comparison_requested',
        artifacts: [], reviews: [], output_manifest: {}, simulation_settings: {}, render_params: {}, attempt_number: 1,
        predecessor_comparison_job_id: null, request_fingerprint: 'e'.repeat(64), viewer_session_id: 'viewer-1',
        viewer_session_revision: 4, run_id: 'run-1', observed_generation: 3, selected_read_id: 'read-42',
        contig: 'chr7', requested_start: 500, requested_end: 560, reference_revision_id: 'reference-7',
        raw_representation_id: 'raw-1', move_source_id: 'move-1', mapping_profile_id: 'map-profile-1',
        signal_to_read_mapping_job_id: 'read-map-1', signal_to_reference_mapping_job_id: 'ref-map-1',
        stage_receipts: {}, failure_code: null, failure_message: null, cancel_requested_at: null,
        created_at: '2026-08-27T00:00:00Z', updated_at: '2026-08-27T00:00:00Z', completed_at: null });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); vi.useRealTimers(); });

async function render() {
    await act(async () => root.render(<OntSignalIdealComparison datasetId="dataset-1" viewerSession={viewer as never}
        selectedReadId="read-42" contig="chr7" start={500} end={560} mappingJobId="ref-map-1"
        renderParams={renderParams as never} onViewerSessionChange={vi.fn()} />));
    await settle();
}

describe('OntSignalIdealComparison', () => {
    it('requires a current preview digest and invalidates it after an operator edit', async () => {
        await render();
        expect(container.textContent).toContain('Ideal comparison');
        expect(findButton('Generate and compare').disabled).toBe(true);
        await act(async () => { findButton('Preview').click(); await Promise.resolve(); }); await settle();
        expect(container.textContent).toContain('R10 model is an approximation.');
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
            viewer_session_id: 'viewer-1', expected_viewer_session_revision: 4,
            selected_read_id: 'read-42', preview_digest: 'd'.repeat(64),
            simulation_settings: { profile_id: 'dna-r10-min', seed: 7 },
        }));
    });
});
