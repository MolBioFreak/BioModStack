import { api } from './api.js';
import { withAlignmentAccessRecovery } from './ngsAlignmentSession.js';

export type OntFastqQcScalar = string | number | boolean;
export type OntFastqQcJsonValue = string | number | boolean | null | OntFastqQcJsonValue[] | { [key: string]: OntFastqQcJsonValue };

export interface OntFastqQcCheck {
    status: 'pass' | 'fail' | 'review' | 'not_evaluated';
    purpose: string;
    reason_codes: string[];
    metrics: Record<string, OntFastqQcJsonValue>;
    units: Record<string, string>;
}

export interface OntFastqQcThresholdProfile {
    id: string;
    version: string;
    sha256: string;
    calibration_status: 'experimental' | 'calibrated';
    public_accuracy_validated: boolean;
    values: {
        automatic_pass_eligible: boolean;
        calibration_status: 'experimental' | 'calibrated';
        description: string;
        expected_topology: 'linear' | 'circular';
        max_ambiguous_base_fraction: number;
        max_low_depth_fraction: number;
        max_secondary_anomaly_fraction: number;
        max_strand_dominance_fraction: number;
        max_unmapped_fraction: number;
        min_coverage_fraction: number;
        min_depth: number;
        min_major_allele_fraction: number;
        min_origin_spanning_reads: number;
        min_variant_support_fraction: number;
        public_accuracy_validated: boolean;
        require_zero_variants_for_pass: boolean;
        version: string;
    };
}

export interface OntFastqQcCoveragePoint {
    reference: string;
    position_1based: number;
    depth: number;
}

export interface OntFastqQcHistogramBin {
    start_bp: number;
    end_bp_exclusive: number;
    read_count: number;
}

export interface OntFastqQcVariant {
    id: string;
    kind: 'SNV' | 'INS' | 'DEL' | 'MNV' | 'COMPLEX';
    normalization: 'vcf_left_anchored_v1';
    record_start_1based: number;
    record_end_1based: number;
    affected_interval_kind: 'reference_bases' | 'between_bases';
    affected_start_1based: number;
    affected_end_1based: number;
    ref: string;
    alt: string;
    support_status: 'supported' | 'ambiguous' | 'unsupported' | 'not_evaluated';
    depth: number | null;
    support_fraction: number | null;
    circular_event_id: string | null;
}

export interface OntFastqQcResult {
    schema: 'bms.ngs.fastq-qc-result.v1';
    job: {
        id: string;
        name: string;
        status: string;
        queue_status: string;
        workflow_id: 'ont_fastq_qc';
        input_mode: 'fastq';
        created_at: string | null;
        started_at: string | null;
        completed_at: string | null;
        error_message: string | null;
    };
    authority: {
        sequence_qc_manifest_sha256: string;
        construct_verification_manifest_sha256: string;
        reference_sequence_sha256: string;
        artifact_set_sha256: string;
        declared_artifact_count: number;
        present_artifact_count: number;
        unavailable_artifact_count: number;
        manifest_readiness: 'ready';
        alignment_readiness: 'ready' | 'unavailable';
    };
    artifacts: Array<{
        kind: string;
        source: string;
        state: string;
        artifact_id: string | null;
        owner_scope: 'result_root' | 'managed_input_snapshot';
        sha256: string | null;
        size_bytes: number | null;
        mime_type: string | null;
        url: string | null;
        range_capable: boolean;
        scientific_role: string;
        display_order: number;
        content_disposition: 'inline' | 'attachment' | 'none';
        filename_extension: string | null;
        unavailable_reason?: string | null;
    }>;
    alignment_sessions: Array<{
        session_id: string;
        mode: string;
        reference_contig: string | null;
        ready: boolean;
        unavailable_reason: string | null;
    }>;
    summary: Record<string, OntFastqQcScalar>;
    alignment: Record<string, OntFastqQcScalar>;
    read_length_histogram: {
        method: 'fixed_width_v1';
        source_row_count: number;
        bin_width_bp: number;
        bins: OntFastqQcHistogramBin[];
    };
    coverage: {
        method: 'minmax_envelope_v1';
        source_row_count: number;
        maximum_point_count: 2048;
        bucket_width_rows: number;
        minimum_depth: number;
        minimum_depth_position_1based: number;
        depth_basis: 'samtools_depth_aa_default_filters_excludes_deletions_v1';
        depth_unit: 'base_covering_alignment_records';
        tie_breaking: 'minimum:earliest_position;maximum:earliest_position';
        endpoint_policy: 'natural_bucket_extrema_only';
        circular_policy: 'linearized_1based_reference_order_no_wrap';
        construction_attestation: {
            validator: 'bms.ngs.fastq-qc-result-construction-validator.v1';
            source_rows_sha256: string;
            source_row_count: number;
            projection_sha256: string;
            validated_at: string;
        };
        points: OntFastqQcCoveragePoint[];
    };
    verification: {
        verdict: 'PASS' | 'FAIL' | 'REVIEW';
        reason_codes: string[];
        summary: Record<string, OntFastqQcScalar>;
        checks: {
            expected_reference_screen: OntFastqQcCheck;
            coverage: OntFastqQcCheck;
            read_support: OntFastqQcCheck;
            sequence_identity: OntFastqQcCheck;
            topology: OntFastqQcCheck;
        };
        variants: OntFastqQcVariant[];
        threshold_profile: OntFastqQcThresholdProfile;
    };
    stages: Array<{ stage: string; status: 'complete' | 'missing'; output_count: number }>;
    execution_resources: {
        accelerator_applicability: 'not_applicable';
        reason: string;
        dorado_invoked: false;
        scheduler_gpu_assignment: string | number | null;
        configured_dorado_device_ignored: string | null;
        evidence_status: 'accepted' | 'historical_unavailable';
        receipt_schema: string | null;
        receipt_id: string | null;
        receipt_sha256: string | null;
        run_attempt_id: string | null;
        gpu_index: number | null;
        gpu_uuid: string | null;
        admitted_vram_bytes: number | null;
        execution_invocation_id: string | null;
        outcome: string | null;
        admitted_cpu_threads: number | null;
        observed_memory_peak_bytes: number | null;
        observed_pids_peak: number | null;
    };
}

const TOP_LEVEL_KEYS = [
    'schema', 'job', 'authority', 'artifacts', 'alignment_sessions', 'summary', 'alignment',
    'read_length_histogram', 'coverage', 'verification', 'stages', 'execution_resources',
] as const;
const JOB_KEYS = [
    'id', 'name', 'status', 'queue_status', 'workflow_id', 'input_mode', 'created_at',
    'started_at', 'completed_at', 'error_message',
] as const;
const AUTHORITY_KEYS = [
    'sequence_qc_manifest_sha256', 'construct_verification_manifest_sha256',
    'reference_sequence_sha256', 'artifact_set_sha256', 'declared_artifact_count',
    'present_artifact_count', 'unavailable_artifact_count', 'manifest_readiness',
    'alignment_readiness',
] as const;
const VERIFICATION_KEYS = [
    'verdict', 'reason_codes', 'summary', 'checks', 'variants', 'threshold_profile',
] as const;
const CHECK_KEYS = ['expected_reference_screen', 'coverage', 'read_support', 'sequence_identity', 'topology'] as const;
const RESOURCE_KEYS = [
    'accelerator_applicability', 'reason', 'dorado_invoked', 'scheduler_gpu_assignment',
    'configured_dorado_device_ignored', 'evidence_status', 'receipt_schema', 'receipt_id',
    'receipt_sha256', 'run_attempt_id', 'gpu_index', 'gpu_uuid', 'admitted_vram_bytes',
    'execution_invocation_id', 'outcome', 'admitted_cpu_threads', 'observed_memory_peak_bytes',
    'observed_pids_peak',
] as const;
const SUMMARY_KEYS = new Set([
    'reference_name', 'reference_length', 'reads_considered', 'mapped_reads', 'mapping_rate_pct',
    'fastq_minimap2_preset', 'fastq_minimap2_allow_secondary', 'mean_read_length_bp',
    'n50_read_length_bp', 'estimated_copy_number_mean', 'dimer_like_reads', 'trimer_plus_reads',
    'mean_coverage_depth', 'covered_fraction_pct', 'consensus_status', 'consensus_length',
    'igv_report_status',
]);
const VERIFICATION_SUMMARY_KEYS = new Set([
    'coverage_fraction', 'observed_length', 'reference_length', 'reference_name',
    'reference_topology', 'sequence_identity_fraction', 'unmapped_fraction', 'variant_count',
]);
const ALIGNMENT_KEYS = new Set([
    'reference_name', 'reference_length', 'expected_plasmid_size', 'min_fastq_read_length',
    'fastq_minimap2_preset', 'fastq_minimap2_allow_secondary', 'total_reads',
    'reads_passing_length_filter', 'total_bases', 'mean_read_length_bp', 'median_read_length_bp',
    'n50_read_length_bp', 'estimated_copy_number_mean', 'dimer_like_reads', 'trimer_plus_reads',
    'mapped_reads', 'unmapped_reads', 'logical_read_records', 'mapped_alignment_records',
    'unmapped_alignment_records', 'total_alignment_records', 'mapping_rate_pct',
    'primary_mapped_reads', 'primary_mapping_rate_pct', 'secondary_alignments',
    'supplementary_alignments', 'coverage_positions', 'covered_positions', 'covered_fraction_pct',
    'mean_coverage_depth', 'median_coverage_depth', 'consensus_status', 'consensus_name',
    'consensus_length', 'igv_track_window_bp', 'igv_report_max_sites', 'igv_report_flanking_bp',
    'igv_report_cli_available', 'igv_report_status',
]);
const ARTIFACT_KINDS = new Set([
    'alignment_bai', 'alignment_bam', 'alignment_stats', 'consensus', 'consensus_index', 'consensus_log',
    'construct_verification_manifest', 'coverage', 'human_evidence_report', 'igv_coverage_depth',
    'igv_gc_content', 'igv_gc_zscore', 'igv_junction_hotspots', 'igv_position_gradient', 'igv_report', 'igv_report_sites_bed',
    'igv_report_sites_tsv', 'igv_softclip_density', 'igv_split_read_density', 'igv_track_config', 'log',
    'modified_bases', 'normalized_variants', 'observed_consensus', 'per_base_metrics', 'per_base_support',
    'read_lengths', 'reference', 'reference_index', 'sequence_qc_manifest', 'signal_data',
    'source_read_provenance', 'source_reads_fastq', 'summary', 'verification_summary',
]);
const SCIENTIFIC_ROLES = new Set([
    'alignment', 'audit_log', 'authority', 'consensus', 'optional_evidence', 'qc_metrics', 'reference',
    'report', 'source_input', 'verification', 'viewer_auxiliary',
]);
const FILENAME_EXTENSIONS = new Set([
    'bai', 'bam', 'bed', 'bedgraph', 'fai', 'fasta', 'fastq.gz', 'html', 'json', 'log', 'tsv', 'vcf',
]);
const CONTENT_DISPOSITIONS = new Set(['inline', 'attachment', 'none']);

function object(value: unknown, label: string): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
    return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
        throw new Error(`${label} has an unsupported wire shape`);
    }
}

function string(value: unknown, label: string): string {
    if (typeof value !== 'string' || !value) throw new Error(`${label} must be a non-empty string`);
    return value;
}

function nullableString(value: unknown, label: string): string | null {
    if (value === null) return null;
    return string(value, label);
}

function nullableInteger(value: unknown, label: string): number | null {
    if (value === null) return null;
    return integer(value, label);
}

function nullableSha256(value: unknown, label: string): string | null {
    if (value === null) return null;
    return sha256(value, label);
}

function finite(value: unknown, label: string): number {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be finite`);
    return value;
}

function integer(value: unknown, label: string, minimum = 0): number {
    const parsed = finite(value, label);
    if (!Number.isInteger(parsed) || parsed < minimum) throw new Error(`${label} must be an integer`);
    return parsed;
}

function sha256(value: unknown, label: string): string {
    const parsed = string(value, label);
    if (!/^[0-9a-f]{64}$/.test(parsed)) throw new Error(`${label} must be SHA-256`);
    return parsed;
}

function scalarMap(value: unknown, expected: Set<string>, label: string): Record<string, OntFastqQcScalar> {
    const parsed = object(value, label);
    exactKeys(parsed, [...expected], label);
    for (const [key, item] of Object.entries(parsed)) {
        if (!['string', 'number', 'boolean'].includes(typeof item)) throw new Error(`${label}.${key} is invalid`);
        if (typeof item === 'number' && !Number.isFinite(item)) throw new Error(`${label}.${key} is not finite`);
    }
    return parsed as Record<string, OntFastqQcScalar>;
}

function validateJsonValue(value: unknown, label: string, depth = 0): asserts value is OntFastqQcJsonValue {
    if (depth > 12) throw new Error(`${label} exceeds the nested value limit`);
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new Error(`${label} is not finite`);
        return;
    }
    if (Array.isArray(value)) {
        value.forEach((item, index) => validateJsonValue(item, `${label}[${index}]`, depth + 1));
        return;
    }
    if (typeof value === 'object') {
        for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
            if (!key) throw new Error(`${label} has an empty key`);
            validateJsonValue(item, `${label}.${key}`, depth + 1);
        }
        return;
    }
    throw new Error(`${label} contains an unsupported value`);
}

function reasonCodes(value: unknown, label: string): string[] {
    if (!Array.isArray(value) || !value.every((item) => typeof item === 'string' && /^[A-Z][A-Z0-9_]+$/.test(item))) {
        throw new Error(`${label} is invalid`);
    }
    if (new Set(value).size !== value.length) throw new Error(`${label} contains duplicates`);
    return value;
}

function bool(value: unknown, label: string): boolean {
    if (typeof value !== 'boolean') throw new Error(`${label} must be boolean`);
    return value;
}

function fraction(value: unknown, label: string): number {
    const parsed = finite(value, label);
    if (parsed < 0 || parsed > 1) throw new Error(`${label} must be a fraction`);
    return parsed;
}

type CheckMetricValidator = (metrics: Record<string, unknown>, label: string) => void;

function parseClosedCheck(
    value: unknown,
    label: string,
    purpose: string,
    units: Record<string, string>,
    validateMetrics: CheckMetricValidator,
): OntFastqQcCheck {
    const parsed = object(value, label);
    exactKeys(parsed, ['status', 'purpose', 'reason_codes', 'metrics', 'units'], label);
    if (!['pass', 'fail', 'review', 'not_evaluated'].includes(String(parsed.status))) {
        throw new Error(`${label}.status is invalid`);
    }
    if (parsed.purpose !== purpose) throw new Error(`${label}.purpose is invalid`);
    const metrics = object(parsed.metrics, `${label}.metrics`);
    const parsedUnits = object(parsed.units, `${label}.units`);
    exactKeys(parsedUnits, Object.keys(units), `${label}.units`);
    for (const [key, expected] of Object.entries(units)) {
        if (parsedUnits[key] !== expected) throw new Error(`${label}.units.${key} is invalid`);
    }
    validateMetrics(metrics, `${label}.metrics`);
    validateJsonValue(metrics, `${label}.metrics`);
    return {
        status: parsed.status as OntFastqQcCheck['status'],
        purpose,
        reason_codes: reasonCodes(parsed.reason_codes, `${label}.reason_codes`),
        metrics: metrics as Record<string, OntFastqQcJsonValue>,
        units: parsedUnits as Record<string, string>,
    };
}

const EXPECTED_REFERENCE_SCREEN_UNITS = {
    screen_basis: 'categorical',
    organism_identity_claimed: 'boolean',
    total_reads: 'reads',
    mapped_reads: 'reads',
    unmapped_reads: 'reads',
    unmapped_fraction: 'fraction',
} as const;
const COVERAGE_CHECK_UNITS = {
    row_count: 'reference_positions',
    coverage_fraction: 'fraction',
    low_depth_fraction: 'fraction',
    low_depth_positions: 'reference_positions',
    minimum_depth: 'alignment_observations',
    mixed_allele_positions: 'reference_positions',
    strand_imbalanced_positions: 'reference_positions',
} as const;
const SEQUENCE_IDENTITY_UNITS = {
    canonicalization: 'categorical',
    consensus_support_validation: 'evidence',
    edit_cost: 'edits',
    identity_fraction: 'fraction',
    observed_length: 'base_pairs',
    orientation: 'categorical',
    reference_length: 'base_pairs',
    rotation_offset: 'base_pairs',
} as const;
const TOPOLOGY_UNITS = {
    aligned_dimer_reads: 'reads',
    alignment_records: 'alignment_records',
    contradictory_breakpoint_evidence: 'boolean',
    edge_window_bp: 'base_pairs',
    evidence_basis: 'categorical',
    expected_topology: 'categorical',
    mapped_unique_reads: 'reads',
    non_boundary_split_reads: 'reads',
    origin_spanning_reads: 'reads',
    evidence_sha256: 'sha256_digests',
    reason: 'categorical_or_null',
    schema: 'schema_id',
    secondary_anomaly_fraction: 'fraction',
    samtools_returncode: 'exit_code',
    state: 'categorical',
} as const;

function validateExpectedReferenceScreen(metrics: Record<string, unknown>, label: string): void {
    exactKeys(metrics, Object.keys(EXPECTED_REFERENCE_SCREEN_UNITS), label);
    if (metrics.screen_basis !== 'expected_reference_mapping_only' || metrics.organism_identity_claimed !== false) {
        throw new Error('expected-reference screen overclaims scientific authority');
    }
    integer(metrics.total_reads, `${label}.total_reads`);
    integer(metrics.mapped_reads, `${label}.mapped_reads`);
    integer(metrics.unmapped_reads, `${label}.unmapped_reads`);
    fraction(metrics.unmapped_fraction, `${label}.unmapped_fraction`);
}

function validateCoverageCheck(metrics: Record<string, unknown>, label: string): void {
    exactKeys(metrics, Object.keys(COVERAGE_CHECK_UNITS), label);
    integer(metrics.row_count, `${label}.row_count`, 1);
    fraction(metrics.coverage_fraction, `${label}.coverage_fraction`);
    fraction(metrics.low_depth_fraction, `${label}.low_depth_fraction`);
    integer(metrics.low_depth_positions, `${label}.low_depth_positions`);
    integer(metrics.minimum_depth, `${label}.minimum_depth`);
    integer(metrics.mixed_allele_positions, `${label}.mixed_allele_positions`);
    integer(metrics.strand_imbalanced_positions, `${label}.strand_imbalanced_positions`);
}

function validateSequenceIdentity(metrics: Record<string, unknown>, label: string): void {
    exactKeys(metrics, Object.keys(SEQUENCE_IDENTITY_UNITS), label);
    string(metrics.canonicalization, `${label}.canonicalization`);
    const support = object(metrics.consensus_support_validation, `${label}.consensus_support_validation`);
    exactKeys(support, ['reason', 'status', 'validator'], `${label}.consensus_support_validation`);
    nullableString(support.reason, `${label}.consensus_support_validation.reason`);
    if (!['valid', 'invalid', 'unavailable', 'not_applicable'].includes(String(support.status))) {
        throw new Error(`${label}.consensus_support_validation.status is invalid`);
    }
    string(support.validator, `${label}.consensus_support_validation.validator`);
    integer(metrics.edit_cost, `${label}.edit_cost`);
    fraction(metrics.identity_fraction, `${label}.identity_fraction`);
    integer(metrics.observed_length, `${label}.observed_length`);
    if (!['forward', 'reverse_complement'].includes(String(metrics.orientation))) {
        throw new Error(`${label}.orientation is invalid`);
    }
    integer(metrics.reference_length, `${label}.reference_length`, 1);
    integer(metrics.rotation_offset, `${label}.rotation_offset`);
}

function validateTopology(metrics: Record<string, unknown>, label: string): void {
    exactKeys(metrics, Object.keys(TOPOLOGY_UNITS), label);
    integer(metrics.aligned_dimer_reads, `${label}.aligned_dimer_reads`);
    integer(metrics.alignment_records, `${label}.alignment_records`);
    bool(metrics.contradictory_breakpoint_evidence, `${label}.contradictory_breakpoint_evidence`);
    integer(metrics.edge_window_bp, `${label}.edge_window_bp`);
    string(metrics.evidence_basis, `${label}.evidence_basis`);
    if (!['linear', 'circular'].includes(String(metrics.expected_topology))) throw new Error(`${label}.expected_topology is invalid`);
    integer(metrics.mapped_unique_reads, `${label}.mapped_unique_reads`);
    integer(metrics.non_boundary_split_reads, `${label}.non_boundary_split_reads`);
    integer(metrics.origin_spanning_reads, `${label}.origin_spanning_reads`);
    const evidence = object(metrics.evidence_sha256, `${label}.evidence_sha256`);
    exactKeys(evidence, ['alignment_bam', 'breakpoint_call', 'reference', 'secondary_summary'], `${label}.evidence_sha256`);
    for (const [key, digest] of Object.entries(evidence)) sha256(digest, `${label}.evidence_sha256.${key}`);
    nullableString(metrics.reason, `${label}.reason`);
    if (metrics.schema !== 'biomodstack.construct_topology_evidence.v1') throw new Error(`${label}.schema is invalid`);
    fraction(metrics.secondary_anomaly_fraction, `${label}.secondary_anomaly_fraction`);
    integer(metrics.samtools_returncode, `${label}.samtools_returncode`);
    if (!['present', 'absent', 'unavailable', 'not_applicable'].includes(String(metrics.state))) throw new Error(`${label}.state is invalid`);
}

function parseChecks(value: unknown): OntFastqQcResult['verification']['checks'] {
    const parsed = object(value, 'verification checks');
    exactKeys(parsed, CHECK_KEYS, 'verification checks');
    return {
        expected_reference_screen: parseClosedCheck(
            parsed.expected_reference_screen,
            'verification checks.expected_reference_screen',
            'Expected-reference mapping and unmapped-fraction screen only.',
            EXPECTED_REFERENCE_SCREEN_UNITS,
            validateExpectedReferenceScreen,
        ),
        coverage: parseClosedCheck(
            parsed.coverage,
            'verification checks.coverage',
            'Coverage completeness and low-depth exclusion across the bound reference.',
            COVERAGE_CHECK_UNITS,
            validateCoverageCheck,
        ),
        read_support: parseClosedCheck(
            parsed.read_support,
            'verification checks.read_support',
            'Per-position depth, allele-mixture, and strand-balance evidence.',
            COVERAGE_CHECK_UNITS,
            validateCoverageCheck,
        ),
        sequence_identity: parseClosedCheck(
            parsed.sequence_identity,
            'verification checks.sequence_identity',
            'Observed consensus identity and edit evidence against the bound reference.',
            SEQUENCE_IDENTITY_UNITS,
            validateSequenceIdentity,
        ),
        topology: parseClosedCheck(
            parsed.topology,
            'verification checks.topology',
            'Circular-boundary support and contradictory breakpoint evidence.',
            TOPOLOGY_UNITS,
            validateTopology,
        ),
    };
}

function parseThresholdProfile(value: unknown): OntFastqQcThresholdProfile {
    const parsed = object(value, 'threshold profile');
    exactKeys(parsed, [
        'id', 'version', 'sha256', 'calibration_status', 'public_accuracy_validated', 'values',
    ], 'threshold profile');
    if (!['experimental', 'calibrated'].includes(String(parsed.calibration_status))) {
        throw new Error('threshold profile calibration status is invalid');
    }
    const publicAccuracyValidated = bool(
        parsed.public_accuracy_validated,
        'threshold profile public accuracy state',
    );
    const values = object(parsed.values, 'threshold profile values');
    exactKeys(values, [
        'automatic_pass_eligible', 'calibration_status', 'description', 'expected_topology',
        'max_ambiguous_base_fraction', 'max_low_depth_fraction', 'max_secondary_anomaly_fraction',
        'max_strand_dominance_fraction', 'max_unmapped_fraction', 'min_coverage_fraction',
        'min_depth', 'min_major_allele_fraction', 'min_origin_spanning_reads',
        'min_variant_support_fraction', 'public_accuracy_validated', 'require_zero_variants_for_pass',
        'version',
    ], 'threshold profile values');
    if (!['experimental', 'calibrated'].includes(String(values.calibration_status))) {
        throw new Error('threshold profile values calibration status is invalid');
    }
    if (!['linear', 'circular'].includes(String(values.expected_topology))) {
        throw new Error('threshold profile expected topology is invalid');
    }
    const parsedValues: OntFastqQcThresholdProfile['values'] = {
        automatic_pass_eligible: bool(values.automatic_pass_eligible, 'threshold profile automatic pass eligibility'),
        calibration_status: values.calibration_status as 'experimental' | 'calibrated',
        description: string(values.description, 'threshold profile description'),
        expected_topology: values.expected_topology as 'linear' | 'circular',
        max_ambiguous_base_fraction: fraction(values.max_ambiguous_base_fraction, 'threshold profile max ambiguous fraction'),
        max_low_depth_fraction: fraction(values.max_low_depth_fraction, 'threshold profile max low-depth fraction'),
        max_secondary_anomaly_fraction: fraction(values.max_secondary_anomaly_fraction, 'threshold profile max secondary anomaly fraction'),
        max_strand_dominance_fraction: fraction(values.max_strand_dominance_fraction, 'threshold profile max strand dominance fraction'),
        max_unmapped_fraction: fraction(values.max_unmapped_fraction, 'threshold profile max unmapped fraction'),
        min_coverage_fraction: fraction(values.min_coverage_fraction, 'threshold profile min coverage fraction'),
        min_depth: integer(values.min_depth, 'threshold profile min depth'),
        min_major_allele_fraction: fraction(values.min_major_allele_fraction, 'threshold profile min major allele fraction'),
        min_origin_spanning_reads: integer(values.min_origin_spanning_reads, 'threshold profile min origin-spanning reads'),
        min_variant_support_fraction: fraction(values.min_variant_support_fraction, 'threshold profile min variant support fraction'),
        public_accuracy_validated: bool(values.public_accuracy_validated, 'threshold profile values public accuracy state'),
        require_zero_variants_for_pass: bool(values.require_zero_variants_for_pass, 'threshold profile zero-variant pass requirement'),
        version: string(values.version, 'threshold profile values version'),
    };
    if (
        parsedValues.calibration_status !== parsed.calibration_status
        || parsedValues.public_accuracy_validated !== publicAccuracyValidated
    ) {
        throw new Error('threshold profile values disagree with profile authority');
    }
    return {
        id: string(parsed.id, 'threshold profile id'),
        version: string(parsed.version, 'threshold profile version'),
        sha256: sha256(parsed.sha256, 'threshold profile digest'),
        calibration_status: parsed.calibration_status as OntFastqQcThresholdProfile['calibration_status'],
        public_accuracy_validated: publicAccuracyValidated,
        values: parsedValues,
    };
}

function parseVariant(value: unknown): OntFastqQcVariant {
    const parsed = object(value, 'variant');
    exactKeys(parsed, [
        'id', 'kind', 'normalization', 'record_start_1based', 'record_end_1based',
        'affected_interval_kind', 'affected_start_1based', 'affected_end_1based',
        'ref', 'alt', 'support_status', 'depth', 'support_fraction', 'circular_event_id',
    ], 'variant');
    const kind = string(parsed.kind, 'variant.kind');
    const supportStatus = string(parsed.support_status, 'variant.support_status');
    if (!['SNV', 'INS', 'DEL', 'MNV', 'COMPLEX'].includes(kind)) throw new Error('variant.kind is invalid');
    if (parsed.normalization !== 'vcf_left_anchored_v1') throw new Error('variant normalization is invalid');
    if (!['reference_bases', 'between_bases'].includes(String(parsed.affected_interval_kind))) {
        throw new Error('variant interval kind is invalid');
    }
    if (!['supported', 'ambiguous', 'unsupported', 'not_evaluated'].includes(supportStatus)) {
        throw new Error('variant.support_status is invalid');
    }
    const supportFraction = parsed.support_fraction === null ? null : fraction(parsed.support_fraction, 'variant.support_fraction');
    const variant: OntFastqQcVariant = {
        id: string(parsed.id, 'variant.id'),
        kind: kind as OntFastqQcVariant['kind'],
        normalization: 'vcf_left_anchored_v1',
        record_start_1based: integer(parsed.record_start_1based, 'variant.record_start_1based', 1),
        record_end_1based: integer(parsed.record_end_1based, 'variant.record_end_1based', 1),
        affected_interval_kind: parsed.affected_interval_kind as OntFastqQcVariant['affected_interval_kind'],
        affected_start_1based: integer(parsed.affected_start_1based, 'variant.affected_start_1based', 1),
        affected_end_1based: integer(parsed.affected_end_1based, 'variant.affected_end_1based', 1),
        ref: string(parsed.ref, 'variant.ref'),
        alt: string(parsed.alt, 'variant.alt'),
        support_status: supportStatus as OntFastqQcVariant['support_status'],
        depth: parsed.depth === null ? null : integer(parsed.depth, 'variant.depth'),
        support_fraction: supportFraction,
        circular_event_id: nullableString(parsed.circular_event_id, 'variant.circular_event_id'),
    };
    const expectedRecordEnd = variant.record_start_1based + variant.ref.length - 1;
    let expectedInterval: readonly [OntFastqQcVariant['affected_interval_kind'], number, number];
    let kindValid = variant.ref !== variant.alt;
    if (variant.kind === 'SNV') {
        kindValid = kindValid && variant.ref.length === 1 && variant.alt.length === 1;
        expectedInterval = ['reference_bases', variant.record_start_1based, variant.record_end_1based];
    } else if (variant.kind === 'MNV') {
        kindValid = kindValid && variant.ref.length > 1 && variant.ref.length === variant.alt.length;
        expectedInterval = ['reference_bases', variant.record_start_1based, variant.record_end_1based];
    } else if (variant.kind === 'DEL') {
        kindValid = variant.ref.length > variant.alt.length && variant.ref.startsWith(variant.alt);
        expectedInterval = ['reference_bases', variant.record_start_1based + variant.alt.length, variant.record_end_1based];
    } else if (variant.kind === 'INS') {
        kindValid = variant.alt.length > variant.ref.length && variant.alt.startsWith(variant.ref);
        expectedInterval = ['between_bases', variant.record_end_1based, variant.record_end_1based];
    } else {
        expectedInterval = ['reference_bases', variant.record_start_1based, variant.record_end_1based];
    }
    if (
        !kindValid
        || variant.record_end_1based !== expectedRecordEnd
        || variant.affected_interval_kind !== expectedInterval[0]
        || variant.affected_start_1based !== expectedInterval[1]
        || variant.affected_end_1based !== expectedInterval[2]
    ) {
        throw new Error('variant interval is invalid');
    }
    return variant;
}

export function parseOntFastqQcResult(value: unknown, expectedJobId: string): OntFastqQcResult {
    const root = object(value, 'NGS result');
    exactKeys(root, TOP_LEVEL_KEYS, 'NGS result');
    const encodedSize = new TextEncoder().encode(JSON.stringify(value)).byteLength;
    if (encodedSize > 256 * 1024) throw new Error('NGS result exceeds the response-size bound');
    if (root.schema !== 'bms.ngs.fastq-qc-result.v1') throw new Error('Unsupported NGS result schema');

    const job = object(root.job, 'NGS result job');
    exactKeys(job, JOB_KEYS, 'NGS result job');
    if (string(job.id, 'job.id') !== expectedJobId) throw new Error('NGS result job mismatch');
    if (job.workflow_id !== 'ont_fastq_qc' || job.input_mode !== 'fastq') throw new Error('NGS result workflow mismatch');
    const parsedJob: OntFastqQcResult['job'] = {
        id: expectedJobId,
        name: string(job.name, 'job.name'),
        status: string(job.status, 'job.status'),
        queue_status: string(job.queue_status, 'job.queue_status'),
        workflow_id: 'ont_fastq_qc',
        input_mode: 'fastq',
        created_at: nullableString(job.created_at, 'job.created_at'),
        started_at: nullableString(job.started_at, 'job.started_at'),
        completed_at: nullableString(job.completed_at, 'job.completed_at'),
        error_message: nullableString(job.error_message, 'job.error_message'),
    };

    const authority = object(root.authority, 'NGS result authority');
    exactKeys(authority, AUTHORITY_KEYS, 'NGS result authority');
    const sequenceManifestSha256 = sha256(authority.sequence_qc_manifest_sha256, 'sequence manifest digest');
    const verificationManifestSha256 = sha256(authority.construct_verification_manifest_sha256, 'verification manifest digest');
    const referenceSequenceSha256 = sha256(authority.reference_sequence_sha256, 'reference digest');
    const artifactSetSha256 = sha256(authority.artifact_set_sha256, 'artifact set digest');
    const declaredArtifactCount = integer(authority.declared_artifact_count, 'declared artifact count');
    const presentArtifactCount = integer(authority.present_artifact_count, 'present artifact count');
    const unavailableArtifactCount = integer(authority.unavailable_artifact_count, 'unavailable artifact count');
    if (declaredArtifactCount !== presentArtifactCount + unavailableArtifactCount || declaredArtifactCount > 256) {
        throw new Error('artifact counts are inconsistent');
    }
    if (authority.manifest_readiness !== 'ready' || !['ready', 'unavailable'].includes(String(authority.alignment_readiness))) {
        throw new Error('NGS result readiness is invalid');
    }
    const parsedAuthority: OntFastqQcResult['authority'] = {
        sequence_qc_manifest_sha256: sequenceManifestSha256,
        construct_verification_manifest_sha256: verificationManifestSha256,
        reference_sequence_sha256: referenceSequenceSha256,
        artifact_set_sha256: artifactSetSha256,
        declared_artifact_count: declaredArtifactCount,
        present_artifact_count: presentArtifactCount,
        unavailable_artifact_count: unavailableArtifactCount,
        manifest_readiness: 'ready',
        alignment_readiness: authority.alignment_readiness as 'ready' | 'unavailable',
    };

    if (!Array.isArray(root.artifacts) || root.artifacts.length !== declaredArtifactCount) {
        throw new Error('artifact inventory is invalid');
    }
    const artifacts: OntFastqQcResult['artifacts'] = root.artifacts.map((entry) => {
        const artifact = object(entry, 'artifact');
        const state = string(artifact.state, 'artifact.state');
        const source = string(artifact.source, 'artifact.source');
        const kind = string(artifact.kind, 'artifact.kind');
        if (!['sequence_qc', 'construct_verification', 'construct_verification_input', 'input_mode'].includes(source)) {
            throw new Error('artifact source is invalid');
        }
        if (!ARTIFACT_KINDS.has(kind)) throw new Error('artifact kind is invalid');
        if (state === 'present') {
            exactKeys(artifact, [
                'kind', 'source', 'state', 'artifact_id', 'owner_scope', 'sha256', 'size_bytes', 'mime_type', 'url', 'range_capable',
                'scientific_role', 'display_order', 'content_disposition', 'filename_extension', 'unavailable_reason',
            ], 'artifact');
            const digest = sha256(artifact.sha256, 'artifact.sha256');
            const artifactId = sha256(artifact.artifact_id, 'artifact.artifact_id');
            const url = string(artifact.url, 'artifact.url');
            const mimeType = string(artifact.mime_type, 'artifact.mime_type');
            const ownerScope = string(artifact.owner_scope, 'artifact.owner_scope') as 'result_root' | 'managed_input_snapshot';
            const scientificRole = string(artifact.scientific_role, 'artifact.scientific_role');
            const contentDisposition = string(artifact.content_disposition, 'artifact.content_disposition') as 'inline' | 'attachment';
            const filenameExtension = string(artifact.filename_extension, 'artifact.filename_extension');
            if (
                artifactId === digest
                || !['result_root', 'managed_input_snapshot'].includes(ownerScope)
                || (kind === 'source_reads_fastq' && ownerScope !== 'managed_input_snapshot')
                || (kind !== 'source_reads_fastq' && ownerScope !== 'result_root')
                || artifact.range_capable !== true
                || mimeType.length > 255
                || !SCIENTIFIC_ROLES.has(scientificRole)
                || !CONTENT_DISPOSITIONS.has(contentDisposition)
                || !['inline', 'attachment'].includes(contentDisposition)
                || !FILENAME_EXTENSIONS.has(filenameExtension)
                || url !== `/api/jobs/${encodeURIComponent(expectedJobId)}/ngs-artifacts/${artifactId}`
            ) {
                throw new Error('present artifact descriptor is invalid');
            }
            return {
                kind,
                source,
                state,
                artifact_id: artifactId,
                owner_scope: ownerScope,
                sha256: digest,
                size_bytes: integer(artifact.size_bytes, 'artifact.size_bytes'),
                mime_type: mimeType,
                url,
                range_capable: true,
                scientific_role: scientificRole,
                display_order: integer(artifact.display_order, 'artifact.display_order', 1),
                content_disposition: contentDisposition,
                filename_extension: filenameExtension,
                unavailable_reason: null,
            };
        }
        exactKeys(artifact, [
            'kind', 'source', 'state', 'artifact_id', 'owner_scope', 'sha256', 'size_bytes', 'mime_type', 'url',
            'range_capable', 'unavailable_reason', 'scientific_role', 'display_order', 'content_disposition', 'filename_extension',
        ], 'artifact');
        if (![
            'missing_required', 'missing_optional', 'not_applicable', 'not_produced',
            'not_applicable_to_input_mode', 'unavailable',
        ].includes(state)) {
            throw new Error('unavailable artifact state is invalid');
        }
        const unavailableReason = string(artifact.unavailable_reason, 'artifact.unavailable_reason');
        const ownerScope = string(artifact.owner_scope, 'artifact.owner_scope') as 'result_root' | 'managed_input_snapshot';
        const scientificRole = string(artifact.scientific_role, 'artifact.scientific_role');
        const contentDisposition = string(artifact.content_disposition, 'artifact.content_disposition');
        if (
            artifact.artifact_id !== null
            || !['result_root', 'managed_input_snapshot'].includes(ownerScope)
            || (kind === 'source_reads_fastq' && ownerScope !== 'managed_input_snapshot')
            || (kind !== 'source_reads_fastq' && ownerScope !== 'result_root')
            || artifact.sha256 !== null
            || artifact.size_bytes !== null
            || artifact.mime_type !== null
            || artifact.url !== null
            || artifact.range_capable !== false
            || !SCIENTIFIC_ROLES.has(scientificRole)
            || contentDisposition !== 'none'
            || artifact.filename_extension !== null
            || unavailableReason.length > 2048
        ) {
            throw new Error('unavailable artifact descriptor is invalid');
        }
        return {
            kind,
            source,
            state,
            artifact_id: null,
            owner_scope: ownerScope,
            sha256: null,
            size_bytes: null,
            mime_type: null,
            url: null,
            range_capable: false,
            scientific_role: scientificRole,
            display_order: integer(artifact.display_order, 'artifact.display_order', 1),
            content_disposition: 'none',
            filename_extension: null,
            unavailable_reason: unavailableReason,
        };
    });
    const actualPresentArtifactCount = artifacts.filter((artifact) => artifact.state === 'present').length;
    if (
        actualPresentArtifactCount !== presentArtifactCount
        || artifacts.length - actualPresentArtifactCount !== unavailableArtifactCount
    ) {
        throw new Error('artifact counts are inconsistent');
    }

    if (!Array.isArray(root.alignment_sessions) || root.alignment_sessions.length > 2) {
        throw new Error('alignment session inventory is invalid');
    }
    const alignmentSessions: OntFastqQcResult['alignment_sessions'] = root.alignment_sessions.map((entry) => {
        const session = object(entry, 'alignment session');
        exactKeys(session, ['session_id', 'mode', 'reference_contig', 'ready', 'unavailable_reason'], 'alignment session');
        if (typeof session.ready !== 'boolean') throw new Error('alignment session readiness is invalid');
        const mode = string(session.mode, 'alignment session mode');
        const referenceContig = nullableString(session.reference_contig, 'alignment reference contig');
        const unavailableReason = nullableString(session.unavailable_reason, 'alignment unavailable reason');
        if (!['primary', 'dimer_candidates'].includes(mode)) {
            throw new Error('alignment session branch is invalid');
        }
        if (
            (session.ready && (referenceContig === null || unavailableReason !== null))
            || (!session.ready && (referenceContig !== null || unavailableReason === null))
        ) {
            throw new Error('alignment session branch is invalid');
        }
        return {
            session_id: string(session.session_id, 'alignment session id'),
            mode,
            reference_contig: referenceContig,
            ready: session.ready,
            unavailable_reason: unavailableReason,
        };
    });
    const effectiveAlignmentReadiness = alignmentSessions.some((session) => session.ready)
        ? 'ready'
        : 'unavailable';
    if (parsedAuthority.alignment_readiness !== effectiveAlignmentReadiness) {
        throw new Error('alignment readiness is inconsistent');
    }

    const histogram = object(root.read_length_histogram, 'read length histogram');
    exactKeys(histogram, ['method', 'source_row_count', 'bin_width_bp', 'bins'], 'read length histogram');
    if (histogram.method !== 'fixed_width_v1' || !Array.isArray(histogram.bins)) throw new Error('read length histogram is invalid');
    const histogramSourceRowCount = integer(histogram.source_row_count, 'histogram.source_row_count', 1);
    const histogramBinWidth = integer(histogram.bin_width_bp, 'histogram.bin_width_bp', 1);
    const bins = histogram.bins.map((entry) => {
        const bin = object(entry, 'read length bin');
        exactKeys(bin, ['start_bp', 'end_bp_exclusive', 'read_count'], 'read length bin');
        return {
            start_bp: integer(bin.start_bp, 'bin.start_bp'),
            end_bp_exclusive: integer(bin.end_bp_exclusive, 'bin.end_bp_exclusive', 1),
            read_count: integer(bin.read_count, 'bin.read_count'),
        };
    });
    if (bins.length !== 50 || bins.some((bin, index) => (
        bin.start_bp !== index * histogramBinWidth
        || bin.end_bp_exclusive !== (index + 1) * histogramBinWidth
    ))) {
        throw new Error('histogram bins are not canonical fixed_width_v1 bins');
    }
    if (bins.reduce((total, bin) => total + bin.read_count, 0) !== histogramSourceRowCount) {
        throw new Error('histogram count does not equal its source row count');
    }

    const coverageDocument = object(root.coverage, 'coverage');
    exactKeys(coverageDocument, [
        'method', 'source_row_count', 'maximum_point_count', 'bucket_width_rows',
        'minimum_depth', 'minimum_depth_position_1based', 'depth_basis', 'depth_unit',
        'tie_breaking', 'endpoint_policy', 'circular_policy', 'construction_attestation', 'points',
    ], 'coverage');
    if (
        coverageDocument.method !== 'minmax_envelope_v1'
        || coverageDocument.maximum_point_count !== 2048
        || coverageDocument.depth_basis !== 'samtools_depth_aa_default_filters_excludes_deletions_v1'
        || coverageDocument.depth_unit !== 'base_covering_alignment_records'
        || coverageDocument.tie_breaking !== 'minimum:earliest_position;maximum:earliest_position'
        || coverageDocument.endpoint_policy !== 'natural_bucket_extrema_only'
        || coverageDocument.circular_policy !== 'linearized_1based_reference_order_no_wrap'
        || !Array.isArray(coverageDocument.points)
        || coverageDocument.points.length < 1
        || coverageDocument.points.length > 2048
    ) {
        throw new Error('coverage projection is invalid');
    }
    const sourceRowCount = integer(coverageDocument.source_row_count, 'coverage.source_row_count', 1);
    const bucketWidthRows = integer(coverageDocument.bucket_width_rows, 'coverage.bucket_width_rows', 1);
    if (bucketWidthRows !== Math.max(1, Math.ceil(sourceRowCount / 1024))) {
        throw new Error('coverage bucket width is invalid');
    }
    const constructionAttestation = object(coverageDocument.construction_attestation, 'coverage.construction_attestation');
    exactKeys(constructionAttestation, [
        'validator', 'source_rows_sha256', 'source_row_count', 'projection_sha256', 'validated_at',
    ], 'coverage construction attestation');
    if (
        constructionAttestation.validator !== 'bms.ngs.fastq-qc-result-construction-validator.v1'
        || integer(constructionAttestation.source_row_count, 'coverage construction source row count', 1) !== sourceRowCount
    ) {
        throw new Error('coverage construction attestation is invalid');
    }
    sha256(constructionAttestation.source_rows_sha256, 'coverage construction source rows');
    sha256(constructionAttestation.projection_sha256, 'coverage construction projection');
    const validatedAt = string(constructionAttestation.validated_at, 'coverage construction validated_at');
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(validatedAt)) {
        throw new Error('coverage construction timestamp is invalid');
    }
    const coveragePoints = coverageDocument.points.map((entry) => {
        const point = object(entry, 'coverage point');
        exactKeys(point, ['reference', 'position_1based', 'depth'], 'coverage point');
        return {
            reference: string(point.reference, 'coverage.reference'),
            position_1based: integer(point.position_1based, 'coverage.position_1based', 1),
            depth: integer(point.depth, 'coverage.depth'),
        };
    });
    for (let index = 1; index < coveragePoints.length; index += 1) {
        const previous = coveragePoints[index - 1];
        const current = coveragePoints[index];
        if (current.reference !== previous.reference || current.position_1based <= previous.position_1based) {
            throw new Error('coverage order is invalid');
        }
    }
    const minimumPoint = coveragePoints.reduce((best, point) => (
        point.depth < best.depth || (point.depth === best.depth && point.position_1based < best.position_1based)
            ? point
            : best
    ));
    const minimumDepth = integer(coverageDocument.minimum_depth, 'coverage.minimum_depth');
    const minimumDepthPosition = integer(
        coverageDocument.minimum_depth_position_1based,
        'coverage.minimum_depth_position_1based',
        1,
    );
    if (minimumDepth !== minimumPoint.depth || minimumDepthPosition !== minimumPoint.position_1based) {
        throw new Error('coverage minimum is inconsistent with the envelope');
    }
    const coverage: OntFastqQcResult['coverage'] = {
        method: 'minmax_envelope_v1',
        source_row_count: sourceRowCount,
        maximum_point_count: 2048,
        bucket_width_rows: bucketWidthRows,
        minimum_depth: minimumDepth,
        minimum_depth_position_1based: minimumDepthPosition,
        depth_basis: 'samtools_depth_aa_default_filters_excludes_deletions_v1',
        depth_unit: 'base_covering_alignment_records',
        tie_breaking: 'minimum:earliest_position;maximum:earliest_position',
        endpoint_policy: 'natural_bucket_extrema_only',
        circular_policy: 'linearized_1based_reference_order_no_wrap',
        construction_attestation: {
            validator: 'bms.ngs.fastq-qc-result-construction-validator.v1',
            source_rows_sha256: sha256(constructionAttestation.source_rows_sha256, 'coverage construction source rows'),
            source_row_count: integer(constructionAttestation.source_row_count, 'coverage construction source row count', 1),
            projection_sha256: sha256(constructionAttestation.projection_sha256, 'coverage construction projection'),
            validated_at: validatedAt,
        },
        points: coveragePoints,
    };

    const verification = object(root.verification, 'verification');
    exactKeys(verification, VERIFICATION_KEYS, 'verification');
    if (!['PASS', 'FAIL', 'REVIEW'].includes(String(verification.verdict))) throw new Error('verification verdict is invalid');
    const verificationReasonCodes = reasonCodes(verification.reason_codes, 'verification reason codes');
    if (!Array.isArray(verification.variants)) throw new Error('verification variants are invalid');
    const parsedSummary = scalarMap(root.summary, SUMMARY_KEYS, 'summary');
    const parsedAlignment = scalarMap(root.alignment, ALIGNMENT_KEYS, 'alignment');
    const parsedVerificationSummary = scalarMap(
        verification.summary,
        VERIFICATION_SUMMARY_KEYS,
        'verification summary',
    );
    const parsedChecks = parseChecks(verification.checks);
    const parsedVariants = verification.variants.map(parseVariant);
    const parsedThresholdProfile = parseThresholdProfile(verification.threshold_profile);
    const referenceName = parsedSummary.reference_name;
    const referenceLength = parsedSummary.reference_length;
    if (
        typeof referenceName !== 'string'
        || typeof referenceLength !== 'number'
        || !Number.isInteger(referenceLength)
        || referenceLength < 1
        || parsedAlignment.reference_name !== referenceName
        || parsedAlignment.reference_length !== referenceLength
        || parsedVerificationSummary.reference_name !== referenceName
        || parsedVerificationSummary.reference_length !== referenceLength
        || sourceRowCount !== referenceLength
        || coveragePoints.some((point) => point.reference !== referenceName || point.position_1based > referenceLength)
        || alignmentSessions.some((session) => session.reference_contig !== null && session.reference_contig !== referenceName)
    ) {
        throw new Error('reference identity is inconsistent across the result');
    }
    if (
        parsedVerificationSummary.variant_count !== parsedVariants.length
        || parsedVariants.some((variant) => (
            variant.record_end_1based > referenceLength
            || variant.affected_start_1based > referenceLength
            || variant.affected_end_1based > referenceLength
        ))
    ) {
        throw new Error('variant count or variant interval is inconsistent');
    }
    if (
        parsedChecks.coverage.metrics.row_count !== referenceLength
        || parsedChecks.read_support.metrics.row_count !== referenceLength
        || parsedChecks.coverage.metrics.minimum_depth !== parsedChecks.read_support.metrics.minimum_depth
        || histogramSourceRowCount !== parsedAlignment.total_reads
    ) {
        throw new Error('scientific source row counts are inconsistent');
    }

    if (!Array.isArray(root.stages)) throw new Error('stages must be an array');
    const stages = root.stages.map((entry) => {
        const stage = object(entry, 'stage');
        exactKeys(stage, ['stage', 'status', 'output_count'], 'stage');
        if (!['complete', 'missing'].includes(String(stage.status))) throw new Error('stage is invalid');
        return {
            stage: string(stage.stage, 'stage.stage'),
            status: stage.status as 'complete' | 'missing',
            output_count: integer(stage.output_count, 'stage.output_count'),
        };
    });
    const canonicalStages = ['fastq_align', 'dimer_qc', 'fastq_qc', 'construct_verification'];
    if (
        stages.length !== canonicalStages.length
        || stages.some((stage, index) => stage.stage !== canonicalStages[index])
    ) {
        throw new Error('canonical stage order is invalid');
    }

    const resources = object(root.execution_resources, 'execution resources');
    exactKeys(resources, RESOURCE_KEYS, 'execution resources');
    if (resources.accelerator_applicability !== 'not_applicable' || resources.dorado_invoked !== false) {
        throw new Error('FASTQ-only accelerator semantics are invalid');
    }
    const schedulerGpuAssignment = resources.scheduler_gpu_assignment;
    if (schedulerGpuAssignment !== null && typeof schedulerGpuAssignment !== 'string' && typeof schedulerGpuAssignment !== 'number') {
        throw new Error('scheduler GPU assignment is invalid');
    }
    const parsedResources: OntFastqQcResult['execution_resources'] = {
        accelerator_applicability: 'not_applicable',
        reason: string(resources.reason, 'execution resources reason'),
        dorado_invoked: false,
        scheduler_gpu_assignment: schedulerGpuAssignment,
        configured_dorado_device_ignored: nullableString(
            resources.configured_dorado_device_ignored,
            'configured Dorado device',
        ),
        evidence_status: string(resources.evidence_status, 'resource evidence status') as 'accepted' | 'historical_unavailable',
        receipt_schema: nullableString(resources.receipt_schema, 'resource receipt schema'),
        receipt_id: nullableString(resources.receipt_id, 'resource receipt id'),
        receipt_sha256: nullableSha256(resources.receipt_sha256, 'resource receipt digest'),
        run_attempt_id: nullableString(resources.run_attempt_id, 'resource run attempt id'),
        gpu_index: nullableInteger(resources.gpu_index, 'resource GPU index'),
        gpu_uuid: nullableString(resources.gpu_uuid, 'resource GPU UUID'),
        admitted_vram_bytes: nullableInteger(resources.admitted_vram_bytes, 'resource admitted VRAM'),
        execution_invocation_id: nullableString(resources.execution_invocation_id, 'resource invocation id'),
        outcome: nullableString(resources.outcome, 'resource outcome'),
        admitted_cpu_threads: nullableInteger(resources.admitted_cpu_threads, 'resource CPU threads'),
        observed_memory_peak_bytes: nullableInteger(resources.observed_memory_peak_bytes, 'resource memory peak'),
        observed_pids_peak: nullableInteger(resources.observed_pids_peak, 'resource PID peak'),
    };

    return {
        schema: 'bms.ngs.fastq-qc-result.v1',
        job: parsedJob,
        authority: parsedAuthority,
        artifacts,
        alignment_sessions: alignmentSessions,
        summary: parsedSummary,
        alignment: parsedAlignment,
        read_length_histogram: {
            method: 'fixed_width_v1',
            source_row_count: histogramSourceRowCount,
            bin_width_bp: histogramBinWidth,
            bins,
        },
        coverage,
        verification: {
            verdict: verification.verdict as OntFastqQcResult['verification']['verdict'],
            reason_codes: verificationReasonCodes,
            summary: parsedVerificationSummary,
            checks: parsedChecks,
            variants: parsedVariants,
            threshold_profile: parsedThresholdProfile,
        },
        stages,
        execution_resources: parsedResources,
    };
}

export async function fetchOntFastqQcResult(jobId: string): Promise<OntFastqQcResult> {
    return withAlignmentAccessRecovery(jobId, async () => {
        const response = await api.get<unknown>(`/api/jobs/${encodeURIComponent(jobId)}/ngs-result`);
        return parseOntFastqQcResult(response.data, jobId);
    });
}
