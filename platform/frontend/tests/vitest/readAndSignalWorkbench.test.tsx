import React, { act, type ComponentProps } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

const apiMocks = vi.hoisted(() => ({
    cancelCalibration: vi.fn(),
    cancelMapping: vi.fn(),
    cancelView: vi.fn(),
    createCalibration: vi.fn(),
    createMapping: vi.fn(),
    createProfile: vi.fn(),
    createView: vi.fn(),
    createViewerSession: vi.fn(),
    fetchCalibration: vi.fn(),
    fetchCapabilities: vi.fn(),
    fetchMapping: vi.fn(),
    fetchMoveSources: vi.fn(),
    fetchProfiles: vi.fn(),
    fetchRawWaveform: vi.fn(),
    fetchView: vi.fn(),
    fetchViewArtifact: vi.fn(),
    fetchFullJob: vi.fn(),
    fetchJobStages: vi.fn(),
    fetchJobs: vi.fn(),
    fetchRawSignalCapabilities: vi.fn(),
    fetchViewerSession: vi.fn(),
    fetchPooledAssignment: vi.fn(),
    apiGet: vi.fn(),
    requestRawWaveform: vi.fn(),
    updateViewerSession: vi.fn(),
}));

const alignmentMocks = vi.hoisted(() => ({
    fetchRead: vi.fn(),
    fetchReads: vi.fn(),
    fetchSessions: vi.fn(),
    isAccessDenied: vi.fn(),
    rotateAccess: vi.fn(),
}));

const contextMocks = vi.hoisted(() => ({
    contextHref: vi.fn(),
    updateQueryParams: vi.fn(),
}));

const igvMocks = vi.hoisted(() => ({
    createBrowser: vi.fn(),
    removeBrowser: vi.fn(),
    setDefaults: vi.fn(),
}));

const renderMocks = vi.hoisted(() => ({
    rawInspectorRender: vi.fn(),
    suspendedAlignmentSessionId: null as string | null,
    suspension: null as Promise<void> | null,
}));

vi.mock('../../src/lib/api', () => ({
    DEFAULT_ONT_SIGNAL_RENDER_PARAMS: {
        strand: 'forward', signal_units: 'pA', scale: 'none', base_shift_source: 'profile', base_shift_value: 0,
        fixed_width: false, base_width: 10, point_size: 0.5, base_limit: 1000, signal_sample_limit: 100_000,
        pileup_read_limit: 20, loose_bound: false, show_samples: true, show_base_colours: true,
        remove_signal_outliers: false, managed_bed_artifact_id: null,
    },
    api: { get: apiMocks.apiGet },
    cancelOntSignalCalibration: apiMocks.cancelCalibration,
    cancelOntSignalMapping: apiMocks.cancelMapping,
    cancelOntSignalView: apiMocks.cancelView,
    createOntSignalCalibration: apiMocks.createCalibration,
    createOntSignalMapping: apiMocks.createMapping,
    createOntSignalMappingProfile: apiMocks.createProfile,
    createOntSignalView: apiMocks.createView,
    createOntSignalViewerSession: apiMocks.createViewerSession,
    fetchOntMoveSources: apiMocks.fetchMoveSources,
    fetchOntSignalCalibration: apiMocks.fetchCalibration,
    fetchOntSignalMapping: apiMocks.fetchMapping,
    fetchOntSignalMappingProfiles: apiMocks.fetchProfiles,
    fetchOntRawSignalWaveform: apiMocks.fetchRawWaveform,
    fetchOntSignalView: apiMocks.fetchView,
    fetchOntSignalViewArtifact: apiMocks.fetchViewArtifact,
    fetchOntSignalWorkbenchCapabilities: apiMocks.fetchCapabilities,
    fetchFullJob: apiMocks.fetchFullJob,
    fetchJobStages: apiMocks.fetchJobStages,
    fetchJobs: apiMocks.fetchJobs,
    fetchOntRawSignalCapabilities: apiMocks.fetchRawSignalCapabilities,
    fetchOntSignalViewerSession: apiMocks.fetchViewerSession,
    fetchPooledAssignmentManifest: apiMocks.fetchPooledAssignment,
    requestOntRawSignalWaveform: apiMocks.requestRawWaveform,
    updateOntSignalViewerSession: apiMocks.updateViewerSession,
}));

vi.mock('../../src/lib/ngsAlignmentSession', () => ({
    fetchAlignmentRead: alignmentMocks.fetchRead,
    fetchAlignmentReads: alignmentMocks.fetchReads,
    fetchAlignmentSessions: alignmentMocks.fetchSessions,
    isAlignmentAccessDenied: alignmentMocks.isAccessDenied,
    rotateAlignmentAccess: alignmentMocks.rotateAccess,
}));

vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: null,
        globalExperimentId: null,
        domainExperimentId: null,
        stateRevisionId: null,
        selectedDomainExperiment: null,
        availability: { canMutateDomain: false, reason: 'No domain selected.' },
        contextHref: contextMocks.contextHref,
        updateQueryParams: contextMocks.updateQueryParams,
    }),
}));
vi.mock('../../src/components/ngs/useSequenceQcManifest', () => ({
    useSequenceQcManifest: () => ({ status: 'idle', data: null, error: null, isLoading: false }),
}));
vi.mock('../../src/components/useThemeColors', () => ({
    useThemeColors: () => new Proxy({}, { get: () => '#000000' }),
    useThemePlotlyLayout: () => ({}),
}));
vi.mock('react-plotly.js', () => ({ default: () => <div data-testid="plotly-stub" /> }));
vi.mock('../../src/components/NanoporeTemplate', () => ({ NanoporeTemplate: () => <div>Nanopore launcher</div> }));
vi.mock('../../src/components/ngs/OntInstrumentPanel', () => ({ OntInstrumentPanel: () => <div>ONT instrument</div> }));
vi.mock('../../src/components/ngs/RawReadInspector', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../src/components/ngs/RawReadInspector')>()),
    RawReadInspector: ({ sessionId, onOpenRawSignal }: {
        sessionId: string;
        onOpenRawSignal?: (read: {
            read_id: string;
            length: number;
            contig: string;
            start_1based: number;
        }) => void;
    }) => {
        renderMocks.rawInspectorRender(sessionId);
        if (sessionId === renderMocks.suspendedAlignmentSessionId && renderMocks.suspension) {
            throw renderMocks.suspension;
        }
        return (
            <div>
                Raw read inspector
                <button type="button" onClick={() => onOpenRawSignal?.({
                    read_id: 'read-from-inspector',
                    length: 21,
                    contig: 'chr7',
                    start_1based: 510,
                })}>Open raw signal for read</button>
            </div>
        );
    },
}));
vi.mock('../../src/components/ngs/BarcodeUnitsPanel', () => ({ BarcodeUnitsPanel: () => <div>Barcode units</div> }));
vi.mock('../../src/components/ngs/PooledAssignmentReviewPanel', () => ({ PooledAssignmentReviewPanel: () => <div>Pooled assignment</div> }));
vi.mock('../../src/components/ngs/SequenceQcManifestPanel', () => ({
    SequenceQcManifestPanel: ({ onNavigateLocus }: { onNavigateLocus?: (start: number, end: number, source: string) => void }) => (
        <div>
            Sequence QC
            <button type="button" onClick={() => onNavigateLocus?.(101, 120, 'test locus A')}>Queue locus A</button>
            <button type="button" onClick={() => onNavigateLocus?.(201, 220, 'test locus B')}>Queue locus B</button>
        </div>
    ),
}));
vi.mock('../../src/components/molbio-ngs/ExperimentReferenceLinks', () => ({ default: () => <div>References</div> }));
vi.mock('igv', () => ({
    default: {
        createBrowser: igvMocks.createBrowser,
        removeBrowser: igvMocks.removeBrowser,
        setDefaults: igvMocks.setDefaults,
        version: () => '3.7.3',
    },
}));

import { ReadAndSignalWorkbench } from '../../src/components/ngs/ReadAndSignalWorkbench';
import { NGSToolkit } from '../../src/components/NGSToolkit';
import type {
    OntMoveTableSource,
    OntSignalCalibrationJob,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSignalRenderParams,
    OntSignalViewerSession,
    OntSignalViewJob,
    OntSignalWorkbenchCapabilities,
} from '../../src/lib/api';
import type { AlignmentRead, AlignmentSession } from '../../src/lib/ngsAlignmentSession';

const renderParams: OntSignalRenderParams = {
    strand: 'forward',
    signal_units: 'pA',
    scale: 'none',
    base_shift_source: 'profile',
    base_shift_value: 0,
    fixed_width: false,
    base_width: 10,
    point_size: 0.5,
    base_limit: 1000,
    signal_sample_limit: 100_000,
    pileup_read_limit: 20,
    loose_bound: false,
    show_samples: true,
    show_base_colours: true,
    remove_signal_outliers: false,
    managed_bed_artifact_id: null,
};

const alignmentSession: AlignmentSession = {
    session_id: 'alignment-session-1',
    job_id: 'alignment-job-1',
    mode: 'primary',
    reference_contig: 'chr7',
    ready: true,
    unavailable_reason: null,
    reads_url: '/api/jobs/alignment-job-1/reads',
    artifacts: {},
};

const moveSource: OntMoveTableSource = {
    move_source_id: 'moves-dorado-v4.3.0',
    run_id: 'run-1',
    observed_generation: 3,
    raw_representation_id: 'blow5-indexed-1',
    artifact_id: 'move-bam-1',
    artifact_sha256: 'a'.repeat(64),
    artifact_size_bytes: 8192,
    bam_header_sha256: 'b'.repeat(64),
    record_count: 10,
    unique_read_count: 10,
    tag_counts: { mv: 10, ts: 10, ns: 10 },
    basecall_model_id: 'dna_r10.4.1_e8.2_400bps_sup@v4.3.0',
    molecule_type: 'dna',
    source_job_id: 'basecall-job-1',
    external_registration_receipt_id: null,
    source_runtime_identity: { image: 'dorado@sha256:fixture' },
    read_inventory_sha256: 'c'.repeat(64),
    state: 'ready',
    reason_code: 'move_source_validated',
    validation_receipt: { exact_read_set: true },
    created_at: '2026-08-16T12:00:00Z',
    validated_at: '2026-08-16T12:01:00Z',
};

function capabilities(overrides: Partial<OntSignalWorkbenchCapabilities['resolved']> = {}): OntSignalWorkbenchCapabilities {
    return {
        run_id: 'run-1',
        observed_generation: 3,
        resolved: {
            raw_representation_id: 'blow5-indexed-1',
            move_source_id: moveSource.move_source_id,
            mapping_profile_id: null,
            calibration_job_id: null,
            calibration_artifact_id: null,
            signal_to_read_mapping_job_id: null,
            signal_to_reference_mapping_job_id: null,
            ...overrides,
        },
        modes: {
            igv: { state: 'independent', reason_code: 'alignment_session_ready' },
            raw_waveform: { state: 'ready', reason_code: 'indexed_blow5_ready' },
            signal_to_read: { state: 'preparable', reason_code: 'approved_mapping_profile_required' },
            signal_to_reference: { state: 'unavailable', reason_code: 'signal_to_read_mapping_required' },
            signal_pileup: { state: 'unavailable', reason_code: 'validated_realign_mapping_required' },
        },
    };
}

const calibrationArtifact = {
    calibration_artifact_id: 'calibration-artifact-1',
    raw_representation_id: 'blow5-indexed-1',
    move_source_id: moveSource.move_source_id,
    basecall_model_id: moveSource.basecall_model_id as string,
    sample_selection: {
        method: 'digest_ordered_intersection',
        requested_count: 100,
        selected_count: 10,
        intersection_count: 10,
        read_ids: ['read-1', 'read-2'],
        selection_sha256: 'd'.repeat(64),
    },
    recommended_kmer_length: 1,
    recommended_signal_move_offset: 2,
    score_evidence: [{ offset: 2, score: 0.99 }],
    runtime_identity: { image: 'squigualiser@sha256:fixture' },
    parent_sha256s: { blow5: 'e'.repeat(64), move_bam: moveSource.artifact_sha256 },
    artifact_sha256: 'f'.repeat(64),
    created_at: '2026-08-16T12:03:00Z',
};

function calibrationJob(state: 'requested' | 'ready', withArtifact = false): OntSignalCalibrationJob {
    return {
        calibration_job_id: 'calibration-job-1',
        run_id: 'run-1',
        observed_generation: 3,
        raw_representation_id: 'blow5-indexed-1',
        move_source_id: moveSource.move_source_id,
        sample_count: 100,
        request_fingerprint: 'calibration-request-1',
        state,
        reason_code: state === 'ready' ? 'calibration_evidence_ready' : 'calibration_requested',
        attempt: 1,
        resource_snapshot: {},
        stage_receipts: {},
        failure_code: null,
        failure_message: null,
        artifact: withArtifact ? calibrationArtifact : null,
        created_at: '2026-08-16T12:02:00Z',
        updated_at: '2026-08-16T12:03:00Z',
        completed_at: state === 'ready' ? '2026-08-16T12:03:00Z' : null,
    };
}

const approvedProfile: OntSignalMappingProfile = {
    mapping_profile_id: 'profile-dorado-v4.3.0-exact',
    name: 'Calibrated dna_r10.4.1_e8.2_400bps_sup@v4.3.0',
    molecule_type: 'dna',
    basecall_model_id: moveSource.basecall_model_id as string,
    kmer_length: 1,
    signal_move_offset: 2,
    base_shift_value: 0,
    parameter_source: 'approved_calibration',
    calibration_artifact_id: calibrationArtifact.calibration_artifact_id,
    primary_alignment_policy: 'primary_only',
    minimum_mapq: 0,
    include_supplementary: false,
    read_set_selection: 'immutable_full_set',
    approval_receipt: { approved: true },
    approved_at: '2026-08-16T12:04:00Z',
    approved_by: null,
};

function readyMapping(mappingJobId = 'mapping-read-1'): OntSignalMappingJob {
    return {
        mapping_job_id: mappingJobId,
        mode: 'signal_to_read',
        run_id: 'run-1',
        observed_generation: 3,
        raw_representation_id: 'blow5-indexed-1',
        move_source_id: moveSource.move_source_id,
        mapping_profile_id: approvedProfile.mapping_profile_id,
        reference_revision_id: null,
        alignment_job_id: null,
        alignment_session_id: null,
        parent_mapping_job_id: null,
        request_fingerprint: '3'.repeat(64),
        state: 'ready',
        reason_code: 'validated_reform_mapping_ready',
        attempt: 1,
        resource_snapshot: {},
        stage_receipts: {},
        failure_code: null,
        failure_message: null,
        artifacts: [{
            mapping_artifact_id: 'reform-paf-1',
            mapping_job_id: mappingJobId,
            kind: 'reform_paf',
            sha256: '1'.repeat(64),
            size_bytes: 4096,
            media_type: 'text/plain',
            parent_identities: {},
            runtime_identity: {},
            validation_receipt: { exact_read_set: true },
            created_at: '2026-08-16T12:05:00Z',
        }],
        created_at: '2026-08-16T12:04:00Z',
        updated_at: '2026-08-16T12:05:00Z',
        completed_at: '2026-08-16T12:05:00Z',
    };
}

function readyReferenceMapping(): OntSignalMappingJob {
    const base = readyMapping('mapping-reference-1');
    return {
        ...base,
        mode: 'signal_to_reference',
        reference_revision_id: 'reference-revision-7',
        alignment_job_id: 'alignment-job-1',
        alignment_session_id: alignmentSession.session_id,
        parent_mapping_job_id: 'mapping-read-1',
        artifacts: [{
            ...base.artifacts[0],
            mapping_artifact_id: 'realign-paf-1',
            mapping_job_id: 'mapping-reference-1',
            kind: 'realign_paf',
        }],
    };
}

function readyView(viewJobId = 'view-ready-1', artifactId = 'bounded-html-1'): OntSignalViewJob {
    return {
        view_job_id: viewJobId,
        mapping_artifact_id: 'realign-paf-1',
        mode: 'reference',
        read_id: 'read-42',
        reference_region: { contig: 'chr7', start: 500, end: 560 },
        render_params: { ...renderParams, strand: 'reverse', scale: 'medmad' },
        request_fingerprint: '4'.repeat(64),
        state: 'ready',
        reason_code: 'bounded_signal_view_ready',
        output_manifest: {
            schema: 'bms.ont-signal-view-manifest.v1',
            artifacts: [{
                artifact_id: artifactId,
                sha256: '2'.repeat(64),
                size_bytes: 2048,
                media_type: 'text/html',
                url: `/api/ont/signal-workbench/views/${viewJobId}/artifacts/${artifactId}`,
            }],
        },
        render_receipt: { bounded: true, network_policy: 'none' },
        failure_code: null,
        failure_message: null,
        created_at: '2026-08-16T12:06:00Z',
        updated_at: '2026-08-16T12:07:00Z',
        completed_at: '2026-08-16T12:07:00Z',
    };
}

function viewerSession(overrides: Partial<OntSignalViewerSession> = {}): OntSignalViewerSession {
    return {
        viewer_session_id: 'viewer-session-1',
        dataset_id: 'dataset-1',
        run_id: 'run-1',
        observed_generation: 3,
        alignment_job_id: 'alignment-job-1',
        alignment_session_id: alignmentSession.session_id,
        reference_revision_id: 'reference-revision-7',
        raw_representation_id: 'blow5-indexed-1',
        move_source_id: moveSource.move_source_id,
        mapping_profile_id: null,
        contig: 'chr7',
        locus_start: 401,
        locus_end: 460,
        selected_read_id: null,
        igv_state: {},
        signal_state: {},
        revision: 7,
        created_at: '2026-08-16T12:00:00Z',
        updated_at: '2026-08-16T12:00:00Z',
        reopen_url: '/ngs?viewer_session_id=viewer-session-1',
        ...overrides,
    };
}

const selectedRead: AlignmentRead = {
    read_id: 'read-42',
    length: 21,
    mean_quality: 19.2,
    contig: 'chr7',
    start_1based: 510,
    strand: '-',
    mapq: 60,
    cigar: '21M',
    flags: 16,
    unmapped: false,
};

const rawWaveform = {
    lookup_id: 'waveform-run-1-generation-3-read-42',
    run_id: 'run-1',
    observed_generation: 3,
    representation_id: 'blow5-indexed-1',
    read_id: 'read-42',
    state: 'ready' as const,
    reason_code: 'indexed_waveform_ready',
    sample_count: 4,
    samples: [70.5, 71.25, 69.75, 72],
};

let container: HTMLDivElement;
let root: Root;
let onViewerSessionChange: ComponentProps<typeof ReadAndSignalWorkbench>['onViewerSessionChange'];
let onNavigateIgv: ComponentProps<typeof ReadAndSignalWorkbench>['onNavigateIgv'];
const originalFetch = globalThis.fetch;

function baseProps(): ComponentProps<typeof ReadAndSignalWorkbench> {
    return {
        datasetId: 'dataset-1',
        runId: 'run-1',
        observedGeneration: 3,
        alignmentJobId: 'alignment-job-1',
        alignmentSession,
        referenceRevisionId: 'reference-revision-7',
        currentLocus: null,
        viewerSession: viewerSession(),
        igvState: {
            alignment_display_mode: 'EXPANDED',
            alignment_color_by: 'strand',
            alignment_group_by: 'none',
            reads_track_loaded: true,
        },
        onViewerSessionChange,
        onNavigateIgv,
    };
}

async function renderWorkbench(overrides: Partial<ComponentProps<typeof ReadAndSignalWorkbench>> = {}) {
    await act(async () => {
        root.render(<ReadAndSignalWorkbench {...baseProps()} {...overrides} />);
        await Promise.resolve();
    });
}

async function settlePromises() {
    await act(async () => {
        for (let attempt = 0; attempt < 5; attempt += 1) await Promise.resolve();
    });
}

async function blobText(blob: Blob): Promise<string> {
    if (typeof blob.text === 'function') return blob.text();
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener('load', () => resolve(String(reader.result || '')));
        reader.addEventListener('error', () => reject(reader.error));
        reader.readAsText(blob);
    });
}

async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        try {
            assertion();
            return;
        } catch {
            await act(async () => {
                await new Promise((resolve) => setTimeout(resolve, 0));
            });
        }
    }
    assertion();
}

function button(label: string): HTMLButtonElement {
    const match = Array.from(container.querySelectorAll('button')).find((candidate) => candidate.textContent?.trim() === label);
    if (!match) throw new Error(`Button not found: ${label}`);
    return match;
}

function input(placeholder: string): HTMLInputElement {
    const match = container.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`);
    if (!match) throw new Error(`Input not found: ${placeholder}`);
    return match;
}

beforeEach(() => {
    vi.useRealTimers();
    for (const mock of Object.values(apiMocks)) mock.mockReset();
    for (const mock of Object.values(alignmentMocks)) mock.mockReset();
    contextMocks.contextHref.mockReset();
    contextMocks.contextHref.mockImplementation((pathname: string) => pathname);
    contextMocks.updateQueryParams.mockReset();
    for (const mock of Object.values(igvMocks)) mock.mockReset();
    renderMocks.rawInspectorRender.mockReset();
    renderMocks.suspendedAlignmentSessionId = null;
    renderMocks.suspension = null;

    apiMocks.fetchCapabilities.mockResolvedValue(capabilities());
    apiMocks.fetchMoveSources.mockResolvedValue({ items: [moveSource] });
    apiMocks.fetchProfiles.mockResolvedValue({ items: [] });
    apiMocks.fetchRawWaveform.mockResolvedValue(rawWaveform);
    apiMocks.fetchCalibration.mockResolvedValue(calibrationJob('ready', true));
    apiMocks.fetchMapping.mockResolvedValue(readyMapping());
    apiMocks.fetchView.mockResolvedValue(readyView());
    apiMocks.fetchViewArtifact.mockResolvedValue(new Blob(['<html>bounded signal</html>'], { type: 'text/html' }));
    apiMocks.createCalibration.mockResolvedValue(calibrationJob('requested'));
    apiMocks.createProfile.mockResolvedValue(approvedProfile);
    apiMocks.createMapping.mockResolvedValue(readyMapping());
    apiMocks.createView.mockResolvedValue(readyView());
    apiMocks.createViewerSession.mockResolvedValue(viewerSession());
    apiMocks.requestRawWaveform.mockResolvedValue(rawWaveform);
    apiMocks.updateViewerSession.mockResolvedValue(viewerSession({ revision: 8 }));
    alignmentMocks.fetchRead.mockResolvedValue(selectedRead);
    alignmentMocks.fetchReads.mockResolvedValue({ reads: [selectedRead], next_cursor: null, limit: 50, sequence_included: false, scan_truncated: false });
    alignmentMocks.fetchSessions.mockResolvedValue([]);
    alignmentMocks.isAccessDenied.mockReturnValue(false);
    alignmentMocks.rotateAccess.mockResolvedValue({ rotated: true });

    Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: vi.fn(() => 'blob:bounded-signal-view'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: vi.fn(),
    });

    onViewerSessionChange = vi.fn();
    onNavigateIgv = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    vi.useRealTimers();
    await act(async () => root.unmount());
    document.body.replaceChildren();
    globalThis.fetch = originalFetch;
});

describe('ReadAndSignalWorkbench governed behavior', () => {
    it('keeps frontend mapping and move-source request contracts exact to the staged closed router', () => {
        const source = readFileSync(`${process.cwd()}/src/lib/api.ts`, 'utf8');
        const profileContract = source.slice(
            source.indexOf('export interface OntSignalMappingProfile'),
            source.indexOf('export interface OntSignalMappingArtifact'),
        );
        const registrationContract = source.slice(
            source.indexOf('export const registerOntMoveSource'),
            source.indexOf('export const fetchOntSignalMappingProfiles'),
        );
        const mappingJobContract = source.slice(
            source.indexOf('export interface OntSignalMappingJob'),
            source.indexOf('export interface OntSignalViewArtifact'),
        );
        const viewResponseContract = source.slice(
            source.indexOf('export interface OntSignalViewArtifact'),
            source.indexOf('export interface OntSignalViewerSession'),
        );

        expect(profileContract).toContain("parameter_source: 'approved_calibration';");
        expect(profileContract).toContain('calibration_artifact_id: string;');
        expect(profileContract).not.toContain('exact_upstream_profile');
        expect(profileContract).not.toContain('calibration_artifact_id: string | null');
        expect(registrationContract).toContain('source_job_id: string;');
        expect(registrationContract).not.toContain('external_registration_receipt:');
        expect(registrationContract).not.toContain('external_registration_receipt_id:');
        expect(source).toContain('external_registration_receipt_id: string | null;');
        expect(mappingJobContract).toContain('request_fingerprint: string;');
        expect(viewResponseContract).toContain('export interface OntSignalRenderParamsResponse extends OntSignalRenderParams');
        expect(viewResponseContract).toContain('render_params: OntSignalRenderParamsResponse;');
        expect(viewResponseContract).toContain('request_fingerprint: string;');
        expect(viewResponseContract).toContain('command?: Record<string, unknown> | null;');
        expect(viewResponseContract).toContain('network?: string | null;');
        expect(viewResponseContract).not.toContain('[key: string]: unknown;');
    });

    it('models persisted viewer IGV and signal update state as closed typed contracts', () => {
        const source = readFileSync(`${process.cwd()}/src/lib/api.ts`, 'utf8');
        const viewerContract = source.slice(
            source.indexOf('export interface OntSignalViewerIgvUpdateState'),
            source.indexOf('const signalWorkbenchRoot'),
        );

        expect(viewerContract).toContain('igv_state: OntSignalViewerIgvState;');
        expect(viewerContract).toContain('signal_state: OntSignalViewerSignalState;');
        expect(viewerContract).toContain('igv_state: OntSignalViewerIgvUpdateState;');
        expect(viewerContract).toContain('signal_state: OntSignalViewerSignalUpdateState;');
        expect(viewerContract).toContain("mode: OntSignalViewMode | 'raw_waveform';");
        expect(viewerContract).toContain('render_params: OntSignalRenderParams;');
        expect(viewerContract).toContain('read_mapping_job_id: string | null;');
        expect(viewerContract).toContain('reference_mapping_job_id: string | null;');
        expect(viewerContract).not.toContain('igv_state: Record<string, unknown>;');
        expect(viewerContract).not.toContain('signal_state: Record<string, unknown>;');
    });

    it('queries capabilities with the complete exact alignment authority tuple', async () => {
        await renderWorkbench();

        await waitUntil(() => expect(apiMocks.fetchCapabilities).toHaveBeenCalledWith(
            'run-1',
            3,
            {
                alignment_job_id: 'alignment-job-1',
                alignment_session_id: 'alignment-session-1',
                reference_revision_id: 'reference-revision-7',
            },
        ));
    });

    it('rejects a current resolved mapping job tuple with the wrong slot mode', async () => {
        const resolved = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: 'mapping-resolved-read',
            signal_to_reference_mapping_job_id: 'mapping-resolved-reference',
        });
        apiMocks.fetchCapabilities.mockResolvedValue(resolved);
        apiMocks.fetchProfiles.mockResolvedValue({ items: [approvedProfile] });
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => {
            if (mappingJobId === 'mapping-resolved-read') {
                return {
                    ...readyReferenceMapping(),
                    mapping_job_id: mappingJobId,
                    parent_mapping_job_id: null,
                };
            }
            return {
                ...readyReferenceMapping(),
                mapping_job_id: mappingJobId,
                parent_mapping_job_id: 'mapping-resolved-read',
            };
        });

        await renderWorkbench({ viewerSession: null });
        await waitUntil(() => expect(container.textContent).toContain(
            'Mapping job tuple does not match the viewer immutable authority.',
        ));
    });

    it('rejects a persisted mapping job tuple whose reference has another read parent', async () => {
        const persistedRead = readyMapping('mapping-persisted-read');
        const persistedReference = {
            ...readyReferenceMapping(),
            mapping_job_id: 'mapping-persisted-reference',
            parent_mapping_job_id: 'mapping-other-read',
        };
        apiMocks.fetchCapabilities.mockResolvedValue(capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
        }));
        apiMocks.fetchProfiles.mockResolvedValue({ items: [approvedProfile] });
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === persistedRead.mapping_job_id ? persistedRead : persistedReference
        ));

        await renderWorkbench({
            viewerSession: viewerSession({
                mapping_profile_id: approvedProfile.mapping_profile_id,
                signal_state: {
                    read_mapping_job_id: persistedRead.mapping_job_id,
                    reference_mapping_job_id: persistedReference.mapping_job_id,
                },
            }),
        });
        await waitUntil(() => expect(container.textContent).toContain(
            'Mapping job tuple does not match the viewer immutable authority.',
        ));
    });

    it('reports the persisted raw representation used by the waveform in provenance', async () => {
        const persistedSource: OntMoveTableSource = {
            ...moveSource,
            move_source_id: 'moves-persisted',
            raw_representation_id: 'blow5-persisted',
        };
        apiMocks.fetchCapabilities.mockResolvedValue(capabilities({
            raw_representation_id: 'blow5-newer',
            move_source_id: 'moves-newer',
        }));
        apiMocks.fetchMoveSources.mockResolvedValue({ items: [persistedSource] });

        await renderWorkbench({
            viewerSession: viewerSession({
                raw_representation_id: 'blow5-persisted',
                move_source_id: persistedSource.move_source_id,
                signal_state: { mode: 'raw_waveform' },
            }),
        });
        await waitUntil(() => expect(container.textContent).toContain('BLOW5 blow5-persisted'));
        await act(async () => button('Show provenance').click());

        expect(container.textContent).toContain('"raw_representation_id": "blow5-persisted"');
        expect(container.textContent).not.toContain('"raw_representation_id": "blow5-newer"');
    });

    it('reopens the persisted raw move and profile authorities instead of newer resolutions', async () => {
        const persistedSource: OntMoveTableSource = {
            ...moveSource,
            move_source_id: 'moves-persisted',
            raw_representation_id: 'blow5-persisted',
        };
        const newerSource: OntMoveTableSource = {
            ...moveSource,
            move_source_id: 'moves-newer',
            raw_representation_id: 'blow5-newer',
        };
        const persistedProfile: OntSignalMappingProfile = {
            ...approvedProfile,
            mapping_profile_id: 'profile-persisted',
        };
        const newerProfile: OntSignalMappingProfile = {
            ...approvedProfile,
            mapping_profile_id: 'profile-newer',
        };
        const persistedMapping: OntSignalMappingJob = {
            ...readyMapping('mapping-persisted-read'),
            raw_representation_id: 'blow5-persisted',
            move_source_id: persistedSource.move_source_id,
            mapping_profile_id: persistedProfile.mapping_profile_id,
        };
        const newerMapping: OntSignalMappingJob = {
            ...readyMapping('mapping-newer-read'),
            raw_representation_id: 'blow5-newer',
            move_source_id: newerSource.move_source_id,
            mapping_profile_id: newerProfile.mapping_profile_id,
        };
        const newerCapabilities = capabilities({
            raw_representation_id: 'blow5-newer',
            move_source_id: newerSource.move_source_id,
            mapping_profile_id: newerProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: newerMapping.mapping_job_id,
        });
        newerCapabilities.modes.signal_to_read = { state: 'ready', reason_code: 'validated_reform_mapping_ready' };
        const persisted = viewerSession({
            raw_representation_id: 'blow5-persisted',
            move_source_id: persistedSource.move_source_id,
            mapping_profile_id: persistedProfile.mapping_profile_id,
            selected_read_id: 'persisted-read',
            signal_state: {
                mode: 'raw_waveform',
                read_mapping_job_id: persistedMapping.mapping_job_id,
                reference_mapping_job_id: null,
            },
        });
        apiMocks.fetchCapabilities.mockResolvedValue(newerCapabilities);
        apiMocks.fetchMoveSources.mockResolvedValue({ items: [persistedSource, newerSource] });
        apiMocks.fetchProfiles.mockResolvedValue({ items: [persistedProfile, newerProfile] });
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === persistedMapping.mapping_job_id ? persistedMapping : newerMapping
        ));

        await renderWorkbench({ viewerSession: persisted });
        await waitUntil(() => expect(container.textContent).toContain('BLOW5 blow5-persisted'));
        expect(container.textContent).toContain('move source moves-persisted');
        expect(container.textContent).toContain('profile profile-persisted');
        expect(apiMocks.fetchMapping).toHaveBeenCalledWith(persistedMapping.mapping_job_id);
        expect(apiMocks.fetchMapping).not.toHaveBeenCalledWith(newerMapping.mapping_job_id);

        await act(async () => {
            button('Inspect raw waveform').click();
            await Promise.resolve();
        });
        await settlePromises();
        expect(apiMocks.requestRawWaveform).toHaveBeenCalledWith(
            'run-1',
            3,
            'blow5-persisted',
            'persisted-read',
        );
    });

    it('does not publish a refreshAuthorities rejection from an older mounted identity', async () => {
        let rejectStaleAuthorities: (reason: unknown) => void = () => undefined;
        const staleAuthorities = new Promise<OntSignalWorkbenchCapabilities>((_resolve, reject) => {
            rejectStaleAuthorities = reject;
        });
        apiMocks.fetchCapabilities
            .mockReturnValueOnce(staleAuthorities)
            .mockResolvedValueOnce(capabilities());

        await renderWorkbench({
            datasetId: 'dataset-a',
            viewerSession: viewerSession({ viewer_session_id: 'viewer-session-a', dataset_id: 'dataset-a' }),
        });
        await waitUntil(() => expect(apiMocks.fetchCapabilities).toHaveBeenCalledTimes(1));

        await renderWorkbench({
            datasetId: 'dataset-b',
            runId: 'run-b',
            viewerSession: viewerSession({
                viewer_session_id: 'viewer-session-b',
                dataset_id: 'dataset-b',
                run_id: 'run-b',
            }),
        });
        await waitUntil(() => expect(apiMocks.fetchCapabilities).toHaveBeenCalledTimes(2));
        await waitUntil(() => expect(container.querySelector('[title="indexed_blow5_ready"]')).not.toBeNull());

        await act(async () => {
            rejectStaleAuthorities(new Error('stale authority failure'));
            await Promise.resolve();
        });
        await settlePromises();

        expect(container.textContent).not.toContain('stale authority failure');
    });

    it('gates mapping calibration and view cancellation completions to their captured viewer identity', async () => {
        const pendingReadMapping: OntSignalMappingJob = {
            ...readyMapping('mapping-read-a'),
            state: 'requested',
            reason_code: 'read-a-requested',
            completed_at: null,
        };
        const pendingReferenceMapping: OntSignalMappingJob = {
            ...readyReferenceMapping(),
            mapping_job_id: 'mapping-reference-a',
            parent_mapping_job_id: pendingReadMapping.mapping_job_id,
            state: 'requested',
            reason_code: 'reference-a-requested',
            completed_at: null,
        };
        const pendingCalibration = calibrationJob('requested');
        const pendingView: OntSignalViewJob = {
            ...readyView('view-a', 'artifact-a'),
            state: 'requested',
            reason_code: 'view-a-requested',
            output_manifest: { artifacts: [] },
            completed_at: null,
        };
        const initialCapabilities = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: pendingReadMapping.mapping_job_id,
            signal_to_reference_mapping_job_id: pendingReferenceMapping.mapping_job_id,
            calibration_job_id: pendingCalibration.calibration_job_id,
        });
        apiMocks.fetchCapabilities.mockResolvedValue(initialCapabilities);
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === pendingReadMapping.mapping_job_id ? pendingReadMapping : pendingReferenceMapping
        ));
        apiMocks.fetchCalibration.mockResolvedValue(pendingCalibration);
        apiMocks.fetchView.mockResolvedValue(pendingView);

        let resolveReadCancel: (job: OntSignalMappingJob) => void = () => undefined;
        let resolveReferenceCancel: (job: OntSignalMappingJob) => void = () => undefined;
        let resolveCalibrationCancel: (job: OntSignalCalibrationJob) => void = () => undefined;
        let resolveViewCancel: (job: OntSignalViewJob) => void = () => undefined;
        apiMocks.cancelMapping.mockImplementation((mappingJobId: string) => new Promise<OntSignalMappingJob>((resolve) => {
            if (mappingJobId === pendingReadMapping.mapping_job_id) resolveReadCancel = resolve;
            else resolveReferenceCancel = resolve;
        }));
        apiMocks.cancelCalibration.mockReturnValue(new Promise<OntSignalCalibrationJob>((resolve) => {
            resolveCalibrationCancel = resolve;
        }));
        apiMocks.cancelView.mockReturnValue(new Promise<OntSignalViewJob>((resolve) => {
            resolveViewCancel = resolve;
        }));

        await renderWorkbench({
            viewerSession: viewerSession({
                viewer_session_id: 'viewer-session-a',
                mapping_profile_id: approvedProfile.mapping_profile_id,
                signal_state: { view_job_id: pendingView.view_job_id },
            }),
        });
        await waitUntil(() => expect(Array.from(container.querySelectorAll('button')).filter((candidate) => candidate.textContent?.trim() === 'Cancel')).toHaveLength(2));
        await waitUntil(() => expect(button('Cancel calibration')).not.toBeNull());
        await waitUntil(() => expect(button('Cancel render')).not.toBeNull());

        await act(async () => {
            for (const cancel of Array.from(container.querySelectorAll<HTMLButtonElement>('button')).filter((candidate) => candidate.textContent?.trim() === 'Cancel')) cancel.click();
            button('Cancel calibration').click();
            button('Cancel render').click();
            await Promise.resolve();
        });

        apiMocks.fetchCapabilities.mockResolvedValue(capabilities());
        apiMocks.fetchMapping.mockResolvedValue(readyMapping());
        await renderWorkbench({
            datasetId: 'dataset-b',
            viewerSession: viewerSession({
                viewer_session_id: 'viewer-session-b',
                dataset_id: 'dataset-b',
                signal_state: {},
            }),
        });
        await waitUntil(() => expect(container.textContent).toContain('fresh click will request deterministic calibration'));

        await act(async () => {
            resolveReadCancel({ ...pendingReadMapping, state: 'cancelled', reason_code: 'stale-read-cancelled' });
            resolveReferenceCancel({ ...pendingReferenceMapping, state: 'cancelled', reason_code: 'stale-reference-cancelled' });
            resolveCalibrationCancel({ ...pendingCalibration, state: 'cancelled', reason_code: 'stale-calibration-cancelled' });
            resolveViewCancel({ ...pendingView, state: 'cancelled', reason_code: 'stale-view-cancelled' });
            await Promise.resolve();
        });
        await settlePromises();

        expect(container.textContent).not.toContain('stale-read-cancelled');
        expect(container.textContent).not.toContain('stale-reference-cancelled');
        expect(container.textContent).not.toContain('stale-calibration-cancelled');
        expect(container.textContent).not.toContain('stale-view-cancelled');
    });

    it('persists a raw-waveform viewer with a null alignment tuple while the selected session is unready', async () => {
        const unreadySession: AlignmentSession = {
            ...alignmentSession,
            ready: false,
            unavailable_reason: 'alignment_materialization_pending',
        };
        const rawViewer = viewerSession({
            alignment_session_id: null,
            reference_revision_id: null,
        });
        apiMocks.createViewerSession.mockResolvedValue(rawViewer);

        await renderWorkbench({
            alignmentSession: unreadySession,
            referenceRevisionId: 'reference-revision-7',
            viewerSession: null,
        });

        await waitUntil(() => expect(apiMocks.createViewerSession).toHaveBeenCalledWith(expect.objectContaining({
            alignment_job_id: 'alignment-job-1',
            alignment_session_id: null,
            reference_revision_id: null,
        })));
        await waitUntil(() => expect(onViewerSessionChange).toHaveBeenCalledWith(rawViewer));
    });

    it('persists the exact alignment tuple only when the selected session is ready and both values exist', async () => {
        await renderWorkbench({ viewerSession: null });

        await waitUntil(() => expect(apiMocks.createViewerSession).toHaveBeenCalledWith(expect.objectContaining({
            alignment_job_id: 'alignment-job-1',
            alignment_session_id: 'alignment-session-1',
            reference_revision_id: 'reference-revision-7',
        })));
    });

    it('exposes bounded typed render controls and prevents pileup-only pinned-runtime failures', async () => {
        const readyCapabilities = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: 'mapping-read-1',
            signal_to_reference_mapping_job_id: 'mapping-reference-1',
        });
        readyCapabilities.modes.signal_to_read = { state: 'ready', reason_code: 'validated_reform_mapping_ready' };
        readyCapabilities.modes.signal_to_reference = { state: 'ready', reason_code: 'validated_realign_mapping_ready' };
        readyCapabilities.modes.signal_pileup = { state: 'ready', reason_code: 'validated_realign_mapping_ready' };
        apiMocks.fetchCapabilities.mockResolvedValue(readyCapabilities);
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === 'mapping-reference-1' ? readyReferenceMapping() : readyMapping()
        ));

        await renderWorkbench({
            viewerSession: viewerSession({ mapping_profile_id: approvedProfile.mapping_profile_id }),
        });
        await waitUntil(() => expect(button('Reference').disabled).toBe(false));
        await act(async () => button('Render settings').click());

        const baseShiftSource = container.querySelector<HTMLSelectElement>('[aria-label="Base shift source"]');
        const baseShiftValue = container.querySelector<HTMLInputElement>('[aria-label="Base shift value"]');
        const pointSize = container.querySelector<HTMLInputElement>('[aria-label="Point size"]');
        expect(baseShiftSource).not.toBeNull();
        expect(baseShiftValue?.disabled).toBe(true);
        expect(pointSize?.getAttribute('min')).toBe('0.5');
        expect(pointSize?.getAttribute('max')).toBe('10');
        expect(container.querySelector('[aria-label="Managed BED artifact ID"]')).toBeNull();

        await act(async () => {
            const selectSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
            selectSetter?.call(baseShiftSource, 'explicit');
            baseShiftSource?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(baseShiftValue?.disabled).toBe(false);

        await act(async () => button('Reference').click());
        expect(container.querySelector<HTMLInputElement>('[aria-label="Managed BED artifact ID"]')).not.toBeNull();
        const looseBound = container.querySelector<HTMLInputElement>('[aria-label="Loose bound"]');
        expect(looseBound).not.toBeNull();
        await act(async () => {
            looseBound?.click();
            button('Pileup').click();
        });
        expect(container.querySelector('[aria-label="Loose bound"]')).toBeNull();

        await act(async () => {
            button('Render pileup').click();
            await Promise.resolve();
        });
        await settlePromises();
        expect(apiMocks.createView).toHaveBeenCalledWith(expect.objectContaining({
            mode: 'pileup',
            mapping_artifact_id: 'realign-paf-1',
            render_params: expect.objectContaining({
                base_shift_source: 'explicit',
                loose_bound: false,
                point_size: 0.5,
                managed_bed_artifact_id: null,
            }),
        }));
    });

    it('replaces and unmounts artifact URLs exactly once without invalidating the active identity or leaving busy stale', async () => {
        const viewA = readyView('view-a', 'artifact-a');
        const viewB = readyView('view-b', 'artifact-b');
        let resolveViewB: (job: OntSignalViewJob) => void = () => undefined;
        const pendingViewB = new Promise<OntSignalViewJob>((resolve) => {
            resolveViewB = resolve;
        });
        const readyCapabilities = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: 'mapping-read-1',
            signal_to_reference_mapping_job_id: 'mapping-reference-1',
        });
        readyCapabilities.modes.signal_to_reference = { state: 'ready', reason_code: 'validated_realign_mapping_ready' };
        apiMocks.fetchCapabilities.mockResolvedValue(readyCapabilities);
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === 'mapping-reference-1' ? readyReferenceMapping() : readyMapping()
        ));
        apiMocks.fetchView.mockResolvedValue(viewA);
        apiMocks.createView.mockReturnValue(pendingViewB);
        apiMocks.fetchViewArtifact.mockImplementation(async (viewJobId: string) => (
            new Blob([viewJobId], { type: 'text/html' })
        ));
        vi.mocked(URL.createObjectURL)
            .mockReturnValueOnce('blob:view-a')
            .mockReturnValueOnce('blob:view-b');
        const persisted = viewerSession({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_state: {
                mode: 'reference',
                render_params: renderParams,
                view_job_id: 'view-a',
            },
        });

        await renderWorkbench({ viewerSession: persisted });
        await waitUntil(() => expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:view-a'));
        await waitUntil(() => expect(button('Render reference').disabled).toBe(false));

        await act(async () => {
            button('Render reference').click();
            await Promise.resolve();
        });
        await waitUntil(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:view-a'));

        await act(async () => {
            resolveViewB(viewB);
            await Promise.resolve();
        });
        await waitUntil(() => expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:view-b'));
        expect(button('Render reference').disabled).toBe(false);
        expect(vi.mocked(URL.revokeObjectURL).mock.calls.filter(([url]) => url === 'blob:view-a')).toHaveLength(1);

        await act(async () => root.unmount());
        expect(vi.mocked(URL.revokeObjectURL).mock.calls.filter(([url]) => url === 'blob:view-b')).toHaveLength(1);
        root = createRoot(container);
    });

    it('publishes only the newest view artifact within one dataset identity', async () => {
        const workbenchSource = readFileSync(`${process.cwd()}/src/components/ngs/ReadAndSignalWorkbench.tsx`, 'utf8');
        expect(workbenchSource).toContain('artifactRequestGenerationRef');
        const viewA = readyView('view-a', 'artifact-a');
        const viewB = readyView('view-b', 'artifact-b');
        const blobA = new Blob(['stale A'], { type: 'text/html' });
        const blobB = new Blob(['current B'], { type: 'text/html' });
        let resolveArtifactA: (blob: Blob) => void = () => undefined;
        const pendingArtifactA = new Promise<Blob>((resolve) => {
            resolveArtifactA = resolve;
        });
        const readyCapabilities = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: 'mapping-read-1',
            signal_to_reference_mapping_job_id: 'mapping-reference-1',
        });
        readyCapabilities.modes.signal_to_reference = { state: 'ready', reason_code: 'validated_realign_mapping_ready' };
        apiMocks.fetchCapabilities.mockResolvedValue(readyCapabilities);
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === 'mapping-reference-1' ? readyReferenceMapping() : readyMapping()
        ));
        apiMocks.fetchView.mockResolvedValue(viewA);
        apiMocks.createView.mockResolvedValue(viewB);
        apiMocks.fetchViewArtifact.mockImplementation(async (viewJobId: string) => (
            viewJobId === 'view-a' ? pendingArtifactA : blobB
        ));
        vi.mocked(URL.createObjectURL).mockReturnValue('blob:current-b');
        const persisted = viewerSession({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_state: {
                mode: 'reference',
                render_params: renderParams,
                view_job_id: 'view-a',
            },
        });

        await renderWorkbench({ viewerSession: persisted });
        await waitUntil(() => expect(apiMocks.fetchViewArtifact).toHaveBeenCalledWith('view-a', 'artifact-a'));
        await waitUntil(() => expect(button('Render reference').disabled).toBe(false));
        await act(async () => {
            button('Render reference').click();
            await Promise.resolve();
        });
        await waitUntil(() => expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:current-b'));

        await act(async () => {
            resolveArtifactA(blobA);
            await Promise.resolve();
        });
        await settlePromises();

        expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:current-b');
        expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
        const publishedBlob = vi.mocked(URL.createObjectURL).mock.calls[0]?.[0] as Blob;
        expect(publishedBlob).not.toBe(blobA);
        expect(publishedBlob).not.toBe(blobB);
        expect(await blobText(publishedBlob)).toContain('current B');
        expect(await blobText(publishedBlob)).not.toContain('stale A');
    });

    it('presents authenticated governed HTML through a network-silent CSP blob while preserving inline scripts', async () => {
        const authenticatedBlob = new Blob([
            '<!doctype html><html><head><title>Governed plot</title></head><body>'
            + '<script>window.__selfContainedPlot = true;</script>'
            + '<img src="https://forbidden.example/subresource.png"></body></html>',
        ], { type: 'text/html' });
        apiMocks.fetchViewArtifact.mockResolvedValue(authenticatedBlob);
        const persisted = viewerSession({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_state: {
                mode: 'reference',
                render_params: renderParams,
                view_job_id: 'view-ready-1',
            },
        });

        await renderWorkbench({ viewerSession: persisted });
        await waitUntil(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));

        const securedBlob = vi.mocked(URL.createObjectURL).mock.calls[0]?.[0];
        expect(securedBlob).toBeInstanceOf(Blob);
        expect(securedBlob).not.toBe(authenticatedBlob);
        const securedHtml = await blobText(securedBlob as Blob);
        expect(securedHtml).toContain('http-equiv="Content-Security-Policy"');
        expect(securedHtml).toContain("default-src 'none'");
        expect(securedHtml).toContain("script-src 'unsafe-inline'");
        expect(securedHtml).toContain("connect-src 'none'");
        expect(securedHtml).toContain("navigate-to 'none'");
        expect(securedHtml.indexOf('Content-Security-Policy')).toBeLessThan(securedHtml.indexOf('<script>'));
        expect(securedHtml).toContain('window.__selfContainedPlot = true;');
        expect(container.querySelector('iframe')?.getAttribute('sandbox')).toBe('allow-scripts');

        await act(async () => root.unmount());
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:bounded-signal-view');
        root = createRoot(container);
    });

    it('opens a newly created raw-signal session with its exact fresh viewer identity', () => {
        const source = readFileSync(`${process.cwd()}/src/components/NGSToolkit.tsx`, 'utf8');

        expect(source).toContain('openSignalWorkbench(created.viewer_session_id);');
        expect(source).not.toContain('acceptSignalViewerSession(created);\n            openSignalWorkbench();');
    });

    it('gates read-launched viewer creation and navigation to the captured exact toolkit identity', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const alternateSession: AlignmentSession = {
            ...alignmentSession,
            session_id: 'alignment-session-2',
            mode: 'dimer_candidates',
        };
        let resolveCreated: (session: OntSignalViewerSession) => void = () => undefined;
        apiMocks.createViewerSession.mockReturnValue(new Promise<OntSignalViewerSession>((resolve) => {
            resolveCreated = resolve;
        }));
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession, alternateSession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=alignment-job-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(button('Open IGV').disabled).toBe(false));
        await act(async () => button('Open IGV').click());
        await waitUntil(() => expect(button('Open raw signal for read')).not.toBeNull());

        await act(async () => {
            button('Open raw signal for read').click();
            await Promise.resolve();
        });
        expect(apiMocks.createViewerSession).toHaveBeenCalledWith(expect.objectContaining({
            dataset_id: 'dataset-1',
            run_id: 'run-1',
            observed_generation: 3,
            alignment_job_id: 'alignment-job-1',
            alignment_session_id: 'alignment-session-1',
            reference_revision_id: 'reference-revision-7',
            selected_read_id: 'read-from-inspector',
            igv_state: {
                alignment_display_mode: 'EXPANDED',
                alignment_color_by: 'strand',
                alignment_group_by: 'none',
                reads_track_loaded: false,
            },
            signal_state: {
                mode: 'read',
                render_params: expect.objectContaining({
                    strand: 'forward',
                    signal_units: 'pA',
                    base_shift_source: 'profile',
                }),
                view_job_id: null,
                read_mapping_job_id: null,
                reference_mapping_job_id: null,
            },
        }));

        const sessionSelect = container.querySelector<HTMLSelectElement>('select[title="Authoritative job-scoped alignment session"]');
        expect(sessionSelect).not.toBeNull();
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
            setter?.call(sessionSelect, alternateSession.session_id);
            sessionSelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await waitUntil(() => expect(sessionSelect?.value).toBe(alternateSession.session_id));
        contextMocks.updateQueryParams.mockClear();

        await act(async () => {
            resolveCreated(viewerSession({
                viewer_session_id: 'viewer-created-for-a',
                selected_read_id: 'read-from-inspector',
            }));
            await Promise.resolve();
        });
        await settlePromises();

        expect(contextMocks.updateQueryParams).not.toHaveBeenCalledWith({
            view: 'workbench',
            viewer_session_id: 'viewer-created-for-a',
            job_id: 'alignment-job-1',
        }, { replace: true });
        expect(contextMocks.updateQueryParams).not.toHaveBeenCalledWith({
            view: 'workbench',
            viewer_session_id: 'viewer-created-for-a',
        });
        client.clear();
    });

    it('does not let an abandoned identity render authorize an old committed read handler after the new identity commits', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const alternateSession: AlignmentSession = {
            ...alignmentSession,
            session_id: 'alignment-session-2',
            mode: 'dimer_candidates',
        };
        let releaseSuspension: () => void = () => undefined;
        renderMocks.suspendedAlignmentSessionId = alternateSession.session_id;
        renderMocks.suspension = new Promise<void>((resolve) => {
            releaseSuspension = resolve;
        });
        let resolveCreated: (session: OntSignalViewerSession) => void = () => undefined;
        apiMocks.createViewerSession.mockReturnValue(new Promise<OntSignalViewerSession>((resolve) => {
            resolveCreated = resolve;
        }));
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession, alternateSession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=alignment-job-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(button('Open IGV').disabled).toBe(false));
        await act(async () => button('Open IGV').click());
        await waitUntil(() => expect(button('Open raw signal for read')).not.toBeNull());

        const sessionSelect = container.querySelector<HTMLSelectElement>('select[title="Authoritative job-scoped alignment session"]');
        expect(sessionSelect).not.toBeNull();
        await act(async () => {
            React.startTransition(() => {
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
                setter?.call(sessionSelect, alternateSession.session_id);
                sessionSelect?.dispatchEvent(new Event('change', { bubbles: true }));
            });
            await Promise.resolve();
        });
        await waitUntil(() => expect(renderMocks.rawInspectorRender).toHaveBeenCalledWith(alternateSession.session_id));

        await act(async () => {
            button('Open raw signal for read').click();
            await Promise.resolve();
        });
        expect(apiMocks.createViewerSession).toHaveBeenCalledWith(expect.objectContaining({
            alignment_session_id: alignmentSession.session_id,
        }));

        renderMocks.suspendedAlignmentSessionId = null;
        renderMocks.suspension = null;
        await act(async () => {
            releaseSuspension();
            await Promise.resolve();
        });
        await waitUntil(() => expect(sessionSelect?.value).toBe(alternateSession.session_id));
        contextMocks.updateQueryParams.mockClear();

        await act(async () => {
            resolveCreated(viewerSession({ viewer_session_id: 'viewer-created-by-old-handler' }));
            await Promise.resolve();
        });
        await settlePromises();

        expect(contextMocks.updateQueryParams).not.toHaveBeenCalledWith({
            view: 'workbench',
            viewer_session_id: 'viewer-created-by-old-handler',
            job_id: selectedJob.id,
        }, { replace: true });
        const source = readFileSync(`${process.cwd()}/src/components/NGSToolkit.tsx`, 'utf8');
        expect(source).not.toContain('if (selectedJobIdRef.current !== selectedJobId) {');
        client.clear();
    });

    it('keeps newer same-session IGV navigation selected when the initial search resolves last', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const artifact = (artifactId: string, url: string) => ({
            artifact_id: artifactId,
            url,
            sha256: 'a'.repeat(64),
            size_bytes: 1024,
            mime_type: 'application/octet-stream',
            range_capable: true,
            declared_sha256: 'a'.repeat(64),
            declared_size_bytes: 1024,
            observed_sha256: 'a'.repeat(64),
            observed_size_bytes: 1024,
            integrity_valid: true,
            manifest: 'alignment-session-manifest.json',
        });
        const readySession: AlignmentSession = {
            ...alignmentSession,
            artifacts: {
                alignment: artifact('alignment-bam', '/session/alignment.bam'),
                alignment_index: artifact('alignment-bai', '/session/alignment.bam.bai'),
                reference: artifact('reference-fasta', '/session/reference.fasta'),
                reference_index: artifact('reference-fai', '/session/reference.fasta.fai'),
            },
        };
        let selectedLocus = '';
        let resolveInitialSearch: () => void = () => undefined;
        const search = vi.fn((locus: string) => {
            if (locus === 'chr7:101-120' && search.mock.calls.length === 1) {
                return new Promise<void>((resolve) => {
                    resolveInitialSearch = () => {
                        selectedLocus = locus;
                        resolve();
                    };
                });
            }
            selectedLocus = locus;
            return Promise.resolve();
        });
        const browser = {
            search,
            on: vi.fn(),
            off: vi.fn(),
            loadTrack: vi.fn().mockResolvedValue(undefined),
            findTracks: vi.fn(() => []),
            removeTrack: vi.fn(),
        };
        igvMocks.createBrowser.mockResolvedValue(browser);
        globalThis.fetch = vi.fn(async () => ({
            ok: true,
            text: async () => `>chr7\n${'A'.repeat(300)}`,
        } as Response));
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        alignmentMocks.fetchSessions.mockResolvedValue([readySession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=alignment-job-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(button('Queue locus A')).not.toBeNull());
        await act(async () => button('Queue locus A').click());
        await waitUntil(() => expect(search).toHaveBeenCalledWith('chr7:101-120'));

        await act(async () => {
            button('Queue locus B').click();
            await Promise.resolve();
        });
        await waitUntil(() => expect(search).toHaveBeenCalledWith('chr7:201-220'));
        expect(selectedLocus).toBe('chr7:201-220');

        await act(async () => {
            resolveInitialSearch();
            await Promise.resolve();
        });
        await settlePromises();

        expect(selectedLocus).toBe('chr7:201-220');
        expect(search.mock.calls.filter(([locus]) => locus === 'chr7:201-220')).toHaveLength(2);
        client.clear();
    });

    it('searches the newest pending locus that arrives while IGV createBrowser is unresolved', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const artifact = (artifactId: string, url: string) => ({
            artifact_id: artifactId,
            url,
            sha256: 'a'.repeat(64),
            size_bytes: 1024,
            mime_type: 'application/octet-stream',
            range_capable: true,
            declared_sha256: 'a'.repeat(64),
            declared_size_bytes: 1024,
            observed_sha256: 'a'.repeat(64),
            observed_size_bytes: 1024,
            integrity_valid: true,
            manifest: 'alignment-session-manifest.json',
        });
        const readySession: AlignmentSession = {
            ...alignmentSession,
            artifacts: {
                alignment: artifact('alignment-bam', '/session/alignment.bam'),
                alignment_index: artifact('alignment-bai', '/session/alignment.bam.bai'),
                reference: artifact('reference-fasta', '/session/reference.fasta'),
                reference_index: artifact('reference-fai', '/session/reference.fasta.fai'),
            },
        };
        const search = vi.fn().mockResolvedValue(undefined);
        const browser = {
            search,
            on: vi.fn(),
            off: vi.fn(),
            loadTrack: vi.fn().mockResolvedValue(undefined),
            findTracks: vi.fn(() => []),
            removeTrack: vi.fn(),
        };
        let resolveBrowser: (value: typeof browser) => void = () => undefined;
        igvMocks.createBrowser.mockReturnValue(new Promise<typeof browser>((resolve) => {
            resolveBrowser = resolve;
        }));
        globalThis.fetch = vi.fn(async () => ({
            ok: true,
            text: async () => `>chr7\n${'A'.repeat(300)}`,
        } as Response));
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        alignmentMocks.fetchSessions.mockResolvedValue([readySession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=alignment-job-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(button('Open IGV').disabled).toBe(false));
        await act(async () => button('Open IGV').click());
        await waitUntil(() => expect(igvMocks.createBrowser).toHaveBeenCalledTimes(1));

        await act(async () => {
            button('Queue locus B').click();
            await Promise.resolve();
        });
        expect(search).not.toHaveBeenCalled();

        await act(async () => {
            resolveBrowser(browser);
            await Promise.resolve();
        });
        await waitUntil(() => expect(search).toHaveBeenCalledWith('chr7:201-220'));
        expect(search.mock.calls.at(-1)?.[0]).toBe('chr7:201-220');
        client.clear();
    });

    it('clears viewer session authority when inspecting another job', () => {
        const source = readFileSync(`${process.cwd()}/src/components/NGSToolkit.tsx`, 'utf8');

        expect(source).toContain("onClick={() => updateQueryParams({ job_id: job.id, viewer_session_id: null })}");
    });

    it('mounts job and alignment selection boundaries without reusing an incompatible viewer session', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
                fastq_path: '/managed/input.fastq',
            },
        };
        const otherJob = { ...selectedJob, id: 'alignment-job-2', name: 'Other ONT job' };
        const alternateSession: AlignmentSession = {
            ...alignmentSession,
            session_id: 'alignment-session-2',
            mode: 'dimer_candidates',
        };
        const staleViewer = viewerSession({
            viewer_session_id: 'viewer-session-stale',
            alignment_session_id: 'alignment-session-stale',
        });
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob, otherJob], total: 2 } });
        apiMocks.fetchFullJob.mockImplementation(async (jobId: string) => (
            jobId === otherJob.id ? otherJob : selectedJob
        ));
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        apiMocks.fetchViewerSession.mockResolvedValue(staleViewer);
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession, alternateSession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', {
            configurable: true,
            value: vi.fn(),
        });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&job_id=alignment-job-1&viewer_session_id=viewer-session-stale']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(button('Read & Signal Workbench').disabled).toBe(false));
        await waitUntil(() => expect(apiMocks.fetchViewerSession).toHaveBeenCalledWith('viewer-session-stale'));

        contextMocks.updateQueryParams.mockClear();
        await act(async () => button('Read & Signal Workbench').click());
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({
            view: 'workbench',
            viewer_session_id: null,
        });

        contextMocks.updateQueryParams.mockClear();
        const sessionSelect = container.querySelector<HTMLSelectElement>('select[title="Authoritative job-scoped alignment session"]');
        expect(sessionSelect).not.toBeNull();
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
            setter?.call(sessionSelect, alternateSession.session_id);
            sessionSelect?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await waitUntil(() => expect(contextMocks.updateQueryParams).toHaveBeenCalledWith(
            { viewer_session_id: null },
            { replace: true },
        ));

        contextMocks.updateQueryParams.mockClear();
        const otherRow = Array.from(container.querySelectorAll('tr')).find((row) => row.textContent?.includes(otherJob.name));
        const inspectOther = Array.from(otherRow?.querySelectorAll('button') || []).find((candidate) => candidate.textContent?.trim() === 'Inspect');
        expect(inspectOther).toBeDefined();
        await act(async () => inspectOther?.click());
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({
            job_id: otherJob.id,
            viewer_session_id: null,
        });

        contextMocks.contextHref.mockClear();
        const openOther = Array.from(otherRow?.querySelectorAll('button') || []).find((candidate) => candidate.textContent?.trim() === 'Open');
        expect(openOther).toBeDefined();
        await act(async () => openOther?.click());
        expect(contextMocks.contextHref).toHaveBeenCalledWith('/ngs', {
            section: 'analyses',
            job_id: otherJob.id,
            viewer_session_id: null,
        });

        client.clear();
    });

    it('withholds a persisted viewer session until exact alignment compatibility is discovered', async () => {
        let resolveSessions: (sessions: AlignmentSession[]) => void = () => undefined;
        alignmentMocks.fetchSessions.mockReturnValue(new Promise<AlignmentSession[]>((resolve) => {
            resolveSessions = resolve;
        }));
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const persisted = viewerSession({ selected_read_id: 'persisted-read' });
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        apiMocks.fetchViewerSession.mockResolvedValue(persisted);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&view=workbench&job_id=alignment-job-1&viewer_session_id=viewer-session-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(container.textContent).toContain('Viewer session compatibility is still being verified.'));

        expect(container.querySelector('input[placeholder="Exact read ID"]')).toBeNull();
        expect(apiMocks.createViewerSession).not.toHaveBeenCalled();

        await act(async () => {
            resolveSessions([alignmentSession]);
            await Promise.resolve();
        });
        await waitUntil(() => expect(input('Exact read ID').value).toBe('persisted-read'));
        client.clear();
    });

    it('restores a persisted non-primary alignment session as the selected owner', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const nonPrimarySession: AlignmentSession = {
            ...alignmentSession,
            session_id: 'alignment-session-non-primary',
            mode: 'dimer_candidates',
        };
        const persisted = viewerSession({
            alignment_session_id: nonPrimarySession.session_id,
            selected_read_id: 'persisted-non-primary-read',
        });
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        apiMocks.fetchViewerSession.mockResolvedValue(persisted);
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession, nonPrimarySession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&view=workbench&job_id=alignment-job-1&viewer_session_id=viewer-session-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        await waitUntil(() => expect(
            container.querySelector<HTMLSelectElement>('select[title="Authoritative job-scoped alignment session"]')?.value,
        ).toBe(nonPrimarySession.session_id));
        await waitUntil(() => expect(input('Exact read ID').value).toBe('persisted-non-primary-read'));
        expect(contextMocks.updateQueryParams).not.toHaveBeenCalledWith({ viewer_session_id: null }, { replace: true });
        client.clear();
    });

    it('fails closed when a requested persisted viewer session cannot be fetched', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        apiMocks.fetchViewerSession.mockRejectedValue(new Error('403 persisted viewer session forbidden'));
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&view=workbench&job_id=alignment-job-1&viewer_session_id=viewer-session-forbidden']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        await waitUntil(() => expect(container.textContent).toContain('Requested viewer session could not be reopened.'));
        expect(container.textContent).toContain('No replacement session was created.');
        expect(container.querySelector('input[placeholder="Exact read ID"]')).toBeNull();
        expect(apiMocks.createViewerSession).not.toHaveBeenCalled();
        expect(contextMocks.updateQueryParams).not.toHaveBeenCalledWith({ viewer_session_id: null }, { replace: true });
        client.clear();
    });

    it('rejects persisted viewer state whose dataset run or generation differs from the selected job', async () => {
        const selectedJob = {
            id: 'alignment-job-1',
            name: 'Selected ONT job',
            model_id: 'nanopore',
            mode: 'ont_fastq_qc',
            status: 'completed',
            created_at: '2026-08-16T12:00:00Z',
            params: {
                dataset_id: 'dataset-1',
                ngs_reference_revision_id: 'reference-revision-7',
                source_instrument_run_id: 'run-1',
                source_instrument_observed_generation: 3,
            },
        };
        const incompatible = viewerSession({
            dataset_id: 'dataset-other',
            run_id: 'run-other',
            observed_generation: 99,
            selected_read_id: 'incompatible-read',
        });
        apiMocks.fetchJobs.mockResolvedValue({ data: { jobs: [selectedJob], total: 1 } });
        apiMocks.fetchFullJob.mockResolvedValue(selectedJob);
        apiMocks.fetchJobStages.mockResolvedValue({ data: { job_id: selectedJob.id, all_stages: [], completed_stages: [], stage_outputs: {} } });
        apiMocks.fetchRawSignalCapabilities.mockResolvedValue({
            modes: { raw_waveform: { state: 'ready', representation_id: 'blow5-indexed-1' } },
        });
        apiMocks.fetchViewerSession.mockResolvedValue(incompatible);
        alignmentMocks.fetchSessions.mockResolvedValue([alignmentSession]);
        Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={['/ngs?section=analyses&view=workbench&job_id=alignment-job-1&viewer_session_id=viewer-session-1']}>
                        <Routes><Route path="/ngs" element={<NGSToolkit />} /></Routes>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });
        await waitUntil(() => expect(input('Exact read ID')).not.toBeNull());
        await settlePromises();

        expect(input('Exact read ID').value).toBe('');
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({ viewer_session_id: null }, { replace: true });
        client.clear();
    });

    it('does not render selected-job output paths or substitute another path-like public field', () => {
        const source = readFileSync(`${process.cwd()}/src/components/NGSToolkit.tsx`, 'utf8');
        expect(source).not.toContain('{selectedJob.output_dir');
        expect(source).not.toContain('Output Directory');
        expect(source).not.toContain("['POD5 directory', selectedJob.params?.pod5_dir]");
        expect(source).not.toContain("['BAM path', selectedJob.params?.bam_path]");
        expect(source).not.toContain("['FASTQ path', selectedJob.params?.fastq_path]");
        expect(source).not.toContain("['Reference FASTA', selectedJob.params?.reference_fasta]");
        expect(source).not.toContain('{check.path ||');
        expect(source).not.toContain('>{output}</span>');
        expect(source).toContain('>{selectedJob.id}</');
    });

    it('opens raw waveform and signal-to-read capabilities without alignment while reference controls remain gated', async () => {
        await renderWorkbench({ alignmentSession: null, referenceRevisionId: null });
        await waitUntil(() => expect(container.textContent).toContain('raw waveform'));
        const rawCapability = Array.from(container.querySelectorAll<HTMLElement>('[title]'))
            .find((candidate) => candidate.textContent?.includes('raw waveform'));
        expect(rawCapability?.getAttribute('title')).toBe('indexed_blow5_ready');

        expect(button('Raw waveform').disabled).toBe(false);
        expect(button('Reference').disabled).toBe(true);
        expect(button('Pileup').disabled).toBe(true);
        expect(button('Prepare aligned signal').disabled).toBe(false);

        await act(async () => button('Raw waveform').click());
        const readInput = input('Exact read ID');
        await act(async () => {
            const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            valueSetter?.call(readInput, 'read-42');
            readInput.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => {
            button('Inspect raw waveform').click();
            await Promise.resolve();
        });
        await settlePromises();

        expect(apiMocks.requestRawWaveform).toHaveBeenCalledWith(
            'run-1',
            3,
            'blow5-indexed-1',
            'read-42',
        );
        expect(container.textContent).toContain('Run run-1 generation 3');
        expect(container.textContent).toContain('blow5-indexed-1');
        expect(container.querySelector('svg[aria-label="Raw electrical signal waveform"]')).not.toBeNull();
        expect(alignmentMocks.fetchRead).not.toHaveBeenCalled();
        expect(alignmentMocks.fetchReads).not.toHaveBeenCalled();
    });

    it('does not couple the NGSToolkit workbench launch or panel mount to an alignment session', () => {
        const source = readFileSync(`${process.cwd()}/src/components/NGSToolkit.tsx`, 'utf8');
        const labelAt = source.indexOf('Read &amp; Signal Workbench');
        const launchMarkup = source.slice(source.lastIndexOf('<button', labelAt), labelAt);
        const workbenchAt = source.lastIndexOf('<ReadAndSignalWorkbench');
        const mountGuard = source.slice(source.lastIndexOf('signalWorkbenchRequested ? (', workbenchAt), workbenchAt);

        expect(launchMarkup).not.toContain('selectedAlignmentSession');
        expect(mountGuard).not.toContain('selectedAlignmentSession?.ready');
    });

    it('keeps IGV context and unavailable reasons visible while one prepare control calibrates before a fresh approval click', async () => {
        let capabilityFetch = 0;
        apiMocks.fetchCapabilities.mockImplementation(async () => {
            capabilityFetch += 1;
            return capabilityFetch === 1
                ? capabilities()
                : capabilities({
                    calibration_job_id: 'calibration-job-1',
                    calibration_artifact_id: calibrationArtifact.calibration_artifact_id,
                });
        });

        await renderWorkbench();
        await waitUntil(() => expect(container.textContent).toContain('approved_mapping_profile_required'));

        expect(container.textContent).toContain('IGV remains the alignment authority.');
        expect(container.textContent).toContain('igv');
        expect(container.textContent).toContain('independent');
        expect(container.textContent).toContain('signal_to_read_mapping_required');
        const pileupCapability = Array.from(container.querySelectorAll<HTMLElement>('[title]'))
            .find((candidate) => candidate.textContent?.includes('signal pileup'));
        expect(pileupCapability).toBeDefined();
        expect(pileupCapability?.getAttribute('title')).toBe('validated_realign_mapping_required');
        expect(Array.from(container.querySelectorAll('button')).filter((candidate) => candidate.textContent?.trim() === 'Prepare aligned signal')).toHaveLength(1);

        vi.useFakeTimers();
        await act(async () => {
            button('Prepare aligned signal').click();
            await Promise.resolve();
        });

        expect(apiMocks.createCalibration).toHaveBeenCalledTimes(1);
        expect(apiMocks.createCalibration).toHaveBeenCalledWith('run-1', 3, {
            raw_representation_id: 'blow5-indexed-1',
            move_source_id: moveSource.move_source_id,
            sample_count: 100,
        });
        expect(apiMocks.createProfile).not.toHaveBeenCalled();
        expect(apiMocks.createMapping).not.toHaveBeenCalled();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(1_500);
        });
        await settlePromises();

        expect(apiMocks.fetchCalibration).toHaveBeenCalledWith('calibration-job-1');
        expect(container.textContent).toContain('calibration_evidence_ready');
        expect(container.textContent).toContain(calibrationArtifact.calibration_artifact_id);
        expect(container.textContent).toContain(calibrationArtifact.artifact_sha256);
        expect(container.textContent).toContain('digest_ordered_intersection');
        expect(container.textContent).toContain('squigualiser@sha256:fixture');

        await act(async () => {
            button('Prepare aligned signal').click();
            await Promise.resolve();
        });
        await settlePromises();

        expect(apiMocks.createProfile).toHaveBeenCalledTimes(1);
        expect(apiMocks.createProfile).toHaveBeenCalledWith(expect.objectContaining({
            molecule_type: 'dna',
            basecall_model_id: moveSource.basecall_model_id,
            kmer_length: calibrationArtifact.recommended_kmer_length,
            signal_move_offset: calibrationArtifact.recommended_signal_move_offset,
            parameter_source: 'approved_calibration',
            calibration_artifact_id: calibrationArtifact.calibration_artifact_id,
            primary_alignment_policy: 'primary_only',
            minimum_mapq: 0,
            include_supplementary: false,
            read_set_selection: 'immutable_full_set',
            approval_receipt: expect.objectContaining({
                approved: true,
                action: 'fresh_explicit_prepare_click',
                calibration_artifact_sha256: calibrationArtifact.artifact_sha256,
            }),
        }));
        expect(apiMocks.createMapping).toHaveBeenCalledWith('run-1', 3, {
            mode: 'signal_to_read',
            raw_representation_id: 'blow5-indexed-1',
            move_source_id: moveSource.move_source_id,
            mapping_profile_id: approvedProfile.mapping_profile_id,
            reference_revision_id: null,
            alignment_job_id: null,
            alignment_session_id: null,
        });
        expect(container.textContent).toContain(approvedProfile.mapping_profile_id);
        expect(container.textContent).toContain('validated_reform_mapping_ready');
    });

    it('creates and displays the operator-selected mapping profile base shift', async () => {
        const shiftedProfile: OntSignalMappingProfile = {
            ...approvedProfile,
            base_shift_value: 5,
        };
        apiMocks.fetchCapabilities.mockResolvedValue(capabilities({
            calibration_job_id: 'calibration-job-1',
            calibration_artifact_id: calibrationArtifact.calibration_artifact_id,
        }));
        apiMocks.createProfile.mockResolvedValue(shiftedProfile);

        await renderWorkbench();
        await waitUntil(() => expect(container.textContent).toContain('calibration_evidence_ready'));

        const profileShift = container.querySelector<HTMLInputElement>('input[aria-label="Mapping profile base shift"]');
        expect(profileShift).not.toBeNull();
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            setter?.call(profileShift, '5');
            profileShift?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => {
            button('Prepare aligned signal').click();
            await Promise.resolve();
        });
        await settlePromises();

        expect(apiMocks.createProfile).toHaveBeenCalledWith(expect.objectContaining({
            base_shift_value: 5,
            approval_receipt: expect.objectContaining({ base_shift_value: 5 }),
        }));

        await act(async () => button('Render settings').click());
        const renderShift = container.querySelector<HTMLInputElement>('input[aria-label="Base shift value"]');
        expect(renderShift?.disabled).toBe(true);
        expect(renderShift?.value).toBe('5');
        expect(container.textContent).toContain('profile base shift 5');
    });

    it('restores persisted read, locus, and viewer identity and returns the bounded signal locus through a sandboxed frame', async () => {
        const persisted = viewerSession({
            selected_read_id: 'read-42',
            igv_state: { locus: 'chr7:401-460', selectedTrack: 'reads' },
            signal_state: {
                mode: 'reference',
                render_params: { ...renderParams, strand: 'reverse', scale: 'medmad' },
                view_job_id: 'view-ready-1',
                read_mapping_job_id: 'mapping-read-1',
                reference_mapping_job_id: 'mapping-reference-1',
            },
        });
        apiMocks.updateViewerSession.mockResolvedValue({ ...persisted, revision: 8 });

        await renderWorkbench({ viewerSession: persisted });
        await waitUntil(() => expect(input('Exact read ID').value).toBe('read-42'));
        await waitUntil(() => expect(container.querySelector('iframe')).not.toBeNull());

        expect(input('Reference contig').value).toBe('chr7');
        expect(input('Start').value).toBe('401');
        expect(input('End').value).toBe('460');
        expect(apiMocks.createViewerSession).not.toHaveBeenCalled();
        expect(apiMocks.fetchView).toHaveBeenCalledWith('view-ready-1');

        await act(async () => {
            button('Resolve').click();
            await Promise.resolve();
        });
        await settlePromises();

        expect(alignmentMocks.fetchRead).toHaveBeenCalledWith(
            'alignment-job-1',
            alignmentSession.session_id,
            'read-42',
            { contig: 'chr7', start: 401, end: 460 },
        );
        expect(container.textContent).toContain('read-42 · - · MAPQ 60 · 21M');

        await act(async () => button('Locate read in IGV').click());
        expect(onNavigateIgv).toHaveBeenCalledWith('chr7', 510, 530, 'selected raw-signal read');

        await act(async () => button('Open mapped locus in IGV').click());
        expect(onNavigateIgv).toHaveBeenCalledWith('chr7', 500, 560, 'signal view');

        await act(async () => {
            button('Save session').click();
            await Promise.resolve();
        });
        await settlePromises();
        expect(apiMocks.updateViewerSession).toHaveBeenCalledWith('viewer-session-1', expect.objectContaining({
            expected_revision: 7,
            contig: 'chr7',
            locus_start: 401,
            locus_end: 460,
            selected_read_id: 'read-42',
            igv_state: {
                alignment_display_mode: 'EXPANDED',
                alignment_color_by: 'strand',
                alignment_group_by: 'none',
                reads_track_loaded: true,
            },
            signal_state: expect.objectContaining({ mode: 'reference', view_job_id: 'view-ready-1' }),
        }));

        await act(async () => button('Show provenance').click());
        expect(container.textContent).toContain('"viewer_session_id": "viewer-session-1"');
        expect(container.textContent).toContain('"alignment_session_id": "alignment-session-1"');
        expect(container.textContent).toContain('"reference_revision_id": "reference-revision-7"');

        const frame = container.querySelector<HTMLIFrameElement>('iframe[title="Bounded Squigualiser artifact"]');
        expect(frame).not.toBeNull();
        expect(frame?.getAttribute('src')).toBe('blob:bounded-signal-view');
        expect(frame?.getAttribute('sandbox')).toBe('allow-scripts');
        expect(frame?.getAttribute('sandbox')).not.toContain('allow-same-origin');
        expect(frame?.getAttribute('referrerpolicy')).toBe('no-referrer');
        expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    });

    it('clears a persisted view artifact when switching viewer sessions within the same dataset authority', async () => {
        const sessionA = viewerSession({
            viewer_session_id: 'viewer-session-a',
            signal_state: {
                mode: 'reference',
                render_params: renderParams,
                view_job_id: 'view-a',
            },
        });
        const sessionB = viewerSession({
            viewer_session_id: 'viewer-session-b',
            signal_state: {},
        });
        apiMocks.fetchView.mockResolvedValue(readyView('view-a', 'artifact-a'));

        await renderWorkbench({ viewerSession: sessionA });
        await waitUntil(() => expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:bounded-signal-view'));

        await renderWorkbench({ viewerSession: sessionB });
        await settlePromises();

        expect(container.querySelector('iframe')).toBeNull();
        expect(container.textContent).toContain('No ready bounded signal artifact.');
        expect(apiMocks.fetchView).toHaveBeenCalledTimes(1);
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:bounded-signal-view');
    });

    it('clears raw waveform state when switching viewer sessions with otherwise identical authority', async () => {
        const signalState = { mode: 'raw_waveform' };
        const sessionA = viewerSession({
            viewer_session_id: 'viewer-session-a',
            selected_read_id: 'read-42',
            signal_state: signalState,
        });
        const sessionB = viewerSession({
            viewer_session_id: 'viewer-session-b',
            selected_read_id: 'read-42',
            signal_state: signalState,
        });

        await renderWorkbench({ viewerSession: sessionA });
        await waitUntil(() => expect(button('Inspect raw waveform').disabled).toBe(false));
        await act(async () => {
            button('Inspect raw waveform').click();
            await Promise.resolve();
        });
        await waitUntil(() => expect(container.textContent).toContain('4 source samples'));

        await renderWorkbench({ viewerSession: sessionB });
        await settlePromises();

        expect(container.textContent).not.toContain('4 source samples');
        expect(container.querySelector('svg[aria-label="Raw electrical signal waveform"]')).toBeNull();
    });

    it('does not let a deferred poll for view A replace a newly created view B', async () => {
        const pendingViewA: OntSignalViewJob = {
            ...readyView('view-a', 'artifact-a'),
            state: 'requested',
            reason_code: 'squigualiser_view_requested',
            output_manifest: { artifacts: [] },
            completed_at: null,
        };
        const readyViewA = readyView('view-a', 'artifact-a');
        const readyViewB = readyView('view-b', 'artifact-b');
        let resolveViewAPoll: (job: OntSignalViewJob) => void = () => undefined;
        const deferredViewAPoll = new Promise<OntSignalViewJob>((resolve) => {
            resolveViewAPoll = resolve;
        });
        let resolveViewBCreate: (job: OntSignalViewJob) => void = () => undefined;
        const deferredViewBCreate = new Promise<OntSignalViewJob>((resolve) => {
            resolveViewBCreate = resolve;
        });
        let fetchViewACount = 0;
        apiMocks.fetchView.mockImplementation((viewJobId: string) => {
            if (viewJobId !== 'view-a') return Promise.resolve(readyViewB);
            fetchViewACount += 1;
            return fetchViewACount === 1 ? Promise.resolve(pendingViewA) : deferredViewAPoll;
        });
        apiMocks.createView.mockReturnValue(deferredViewBCreate);
        const viewBArtifact = new Blob(['view-b'], { type: 'text/html' });
        Object.defineProperty(viewBArtifact, 'text', {
            configurable: true,
            value: vi.fn().mockResolvedValue('view-b'),
        });
        apiMocks.fetchViewArtifact.mockImplementation(async (viewJobId: string) => (
            viewJobId === 'view-b' ? viewBArtifact : new Blob([viewJobId], { type: 'text/html' })
        ));
        let objectUrlCount = 0;
        vi.mocked(URL.createObjectURL).mockImplementation(() => {
            objectUrlCount += 1;
            return objectUrlCount === 1 ? 'blob:view-b' : 'blob:view-a';
        });
        const persisted = viewerSession({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_state: {
                mode: 'reference',
                render_params: renderParams,
                view_job_id: 'view-a',
            },
        });
        const readyCapabilities = capabilities({
            mapping_profile_id: approvedProfile.mapping_profile_id,
            signal_to_read_mapping_job_id: 'mapping-read-1',
            signal_to_reference_mapping_job_id: 'mapping-reference-1',
        });
        readyCapabilities.modes.signal_to_reference = { state: 'ready', reason_code: 'validated_realign_mapping_ready' };
        apiMocks.fetchCapabilities.mockResolvedValue(readyCapabilities);
        apiMocks.fetchMapping.mockImplementation(async (mappingJobId: string) => (
            mappingJobId === 'mapping-reference-1' ? readyReferenceMapping() : readyMapping()
        ));

        vi.useFakeTimers();
        await renderWorkbench({ viewerSession: persisted });
        await settlePromises();
        expect(container.textContent).toContain('requested: squigualiser_view_requested');
        await act(async () => {
            await vi.advanceTimersByTimeAsync(1_500);
        });
        expect(fetchViewACount).toBe(2);

        await act(async () => {
            button('Render reference').click();
            await Promise.resolve();
            resolveViewBCreate(readyViewB);
            await Promise.resolve();
            resolveViewAPoll(readyViewA);
            await Promise.resolve();
        });
        await settlePromises();

        expect(container.querySelector<HTMLIFrameElement>('iframe')?.getAttribute('src')).toBe('blob:view-b');
        expect(apiMocks.fetchViewArtifact).not.toHaveBeenCalledWith('view-a', 'artifact-a');
    });

    it('does not publish a selected-read response that completes after the dataset and viewer session change', async () => {
        let resolveStaleRead: (read: AlignmentRead) => void = () => undefined;
        alignmentMocks.fetchRead.mockImplementationOnce(() => new Promise<AlignmentRead>((resolve) => {
            resolveStaleRead = resolve;
        }));
        const datasetA = viewerSession({
            viewer_session_id: 'viewer-session-a',
            dataset_id: 'dataset-a',
            selected_read_id: 'read-a',
        });
        const datasetB = viewerSession({
            viewer_session_id: 'viewer-session-b',
            dataset_id: 'dataset-b',
            selected_read_id: 'fresh-read-b',
        });

        await renderWorkbench({ datasetId: 'dataset-a', viewerSession: datasetA });
        await waitUntil(() => expect(input('Exact read ID').value).toBe('read-a'));
        await act(async () => button('Resolve').click());
        expect(alignmentMocks.fetchRead).toHaveBeenCalledWith(
            'alignment-job-1',
            alignmentSession.session_id,
            'read-a',
            { contig: 'chr7', start: 401, end: 460 },
        );

        await renderWorkbench({ datasetId: 'dataset-b', viewerSession: datasetB });
        await waitUntil(() => expect(input('Exact read ID').value).toBe('fresh-read-b'));

        await act(async () => {
            resolveStaleRead({ ...selectedRead, read_id: 'stale-read-a' });
            await Promise.resolve();
        });
        await settlePromises();

        expect(input('Exact read ID').value).toBe('fresh-read-b');
        expect(container.textContent).not.toContain('stale-read-a');
    });
});
