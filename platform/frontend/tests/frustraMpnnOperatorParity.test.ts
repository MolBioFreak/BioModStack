import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    createFrustraMpnnGuidance,
    createFrustraMpnnMultiComparison,
    fetchFrustraMpnnComparisonRows,
    fetchFrustraMpnnGuidance,
    fetchFrustraMpnnStructureMap,
    parseFrustraMpnnArtifactList,
    parseFrustraMpnnChildReceipt,
    parseFrustraMpnnComparison,
    parseFrustraMpnnGuidance,
    parseFrustraMpnnResultDetail,
    parseFrustraMpnnSavedReview,
    parseFrustraMpnnStatisticsQueryResponse,
    parseFrustraMpnnStatisticsResponse,
} from '../src/lib/frustraMpnnApi.js';
import * as frustraMpnnApi from '../src/lib/frustraMpnnApi.js';
import { api } from '../src/lib/api.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from '../src/components/frustrampnn/frustraMpnnSettingsState.js';
import {
    backendArtifactList,
    backendGuidance,
    backendStatistics,
} from './fixtures/frustraMpnnBackendContracts.js';

const hashes = {
    a: 'a'.repeat(64), b: 'b'.repeat(64), c: 'c'.repeat(64), d: 'd'.repeat(64),
    e: 'e'.repeat(64), f: 'f'.repeat(64), g: '0'.repeat(64),
};

const distribution = {
    count: 4,
    mean: 0.25,
    median: 0.2,
    sample_sd: 0.1,
    min: -1,
    max: 1,
    q1: 0,
    q3: 0.5,
    iqr: 0.5,
    denominators: Object.fromEntries(
        ['count', 'mean', 'median', 'sample_sd', 'min', 'max', 'q1', 'q3', 'iqr']
            .map((name) => [name, { kind: name === 'sample_sd' ? 'sample_degrees_of_freedom_n_minus_1' : 'selected_substitution_slots', count: name === 'sample_sd' ? 3 : 4 }]),
    ),
    missingness_reasons: Object.fromEntries(
        ['count', 'mean', 'median', 'sample_sd', 'min', 'max', 'q1', 'q3', 'iqr'].map((name) => [name, null]),
    ),
};

const classBurden = {
    support_count: 4,
    counts: { high: 1, neutral: 2, minimal: 1 },
    fractions: { high: 0.25, neutral: 0.5, minimal: 0.25 },
    denominator: { kind: 'scoreable_substitution_slots', count: 4 },
    missingness_reason: null,
};

const statistics = structuredClone(backendStatistics);

const persistedRequestedSettings = {
    ...CANONICAL_FRUSTRAMPNN_SETTINGS,
    settings_value_origin: 'operator_request',
};
const effectiveSettings = {
    schema_name: 'frustrampnn_effective_settings',
    schema_version: 1,
    requested_settings: persistedRequestedSettings,
    settings_value_origin: 'operator_request',
    resolved_chains: [{
        entity: { entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A' },
        pdb_chain_id: 'A',
        residues: [{
            entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', label_seq_id: 10,
            auth_asym_id: 'A', auth_seq_id: 42, insertion_code: 'B', sequence_index: 10, wt: 'G',
            pdb_chain_id: 'A', pdb_residue_id: 42, pdb_insertion_code: 'B', model_position: 9,
            residue_name: 'GLY',
        }],
    }],
    normalization_policy_id: 'frustrampnn_structure_normalizer',
    normalization_policy_version: 1,
    threshold_policy_id: 'frustrampnn_class_v1',
    threshold_policy_sha256: hashes.c,
    settings_sha256: hashes.a,
    capability_inventory_byte_sha256: hashes.d,
    resolution_identity: {
        source_artifact_sha256: hashes.a,
        structure_map_schema_name: 'frustrampnn_structure_map',
        structure_map_schema_version: 1,
        structure_map_sha256: hashes.b,
        normalized_pdb_sha256: hashes.c,
    },
    value_sources: {
        protein_selection: { mode: 'operator_request', entities: 'operator_request', regions: 'operator_request', residues: 'operator_request' },
        source_structure: { selected_model_number: 'operator_request', preferred_altloc: 'operator_request' },
        classification_policy: { mode: 'operator_request', high_max: 'operator_request', minimal_min: 'operator_request' },
    },
    effective_settings_sha256: hashes.b,
};

const resultDetail = {
    invocation_id: 'invoke-1',
    parent_job_id: 'job-1',
    parent_workflow_id: 'structure_prediction',
    candidate_id: 'candidate-1',
    design_id: null,
    requiredness: 'required',
    source_artifact_id: null,
    source_artifact_sha256: hashes.a,
    request_sha256: hashes.b,
    manifest_sha256: hashes.c,
    summary_sha256: hashes.d,
    created_at: '2026-08-09T00:00:00Z',
    authority_version: 'v2',
    availability: true,
    statistics_available: true,
    missing_fields: [],
    settings_sha256: hashes.a,
    effective_settings_sha256: hashes.b,
    effective_settings_json: effectiveSettings,
    capability_inventory_sha256: hashes.d,
    statistics_sha256: hashes.f,
    statistics_json: statistics,
    comparison_compatibility_id: hashes.e,
    status: 'succeeded',
    component_contract_version: '2.0',
    runtime_identity: { runtime_identity_sha256: hashes.g },
    runtime_identity_sha256: hashes.g,
    gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
    failure_class: null,
    reopen_destination: { surface: 'frustrampnn-workbench', params: { job_id: 'job-1', invocation_id: 'invoke-1' } },
    summary: {
        schema_name: 'frustrampnn_summary', schema_version: 2,
        execution_configuration_id: 'frustrampnn_execution_configuration_v2',
        execution_configuration_sha256: hashes.a, requested_settings_sha256: hashes.a,
        effective_settings_sha256: hashes.b, runtime_identity_sha256: hashes.g,
        target_id: 'candidate-1', parent_job_id: 'job-1', candidate_id: 'candidate-1',
        source_artifact_sha256: hashes.a, structure_map_sha256: hashes.b,
        normalized_pdb_sha256: hashes.c, landscape_sha256: hashes.d,
        threshold_policy_id: 'frustrampnn_class_v1',
        threshold_policy: { mode: 'canonical', high_max: -1, minimal_min: 0.58 },
        threshold_policy_sha256: hashes.c,
        residue_support: { expected: 1, mapped: 1, scoreable: 1, excluded: 0, ambiguous: 0 },
        slot_support: { expected: 20, observed: 20, scoreable: 20 },
        missingness_by_reason: {},
        native_slot_counts: { high: 0, neutral: 1, minimal: 0 },
        native_slot_fractions: { high: 0, neutral: 1, minimal: 0 },
        complete_landscape_counts: { high: 1, neutral: 18, minimal: 1 },
        complete_landscape_fractions: { high: 0.05, neutral: 0.9, minimal: 0.05 },
        support_by_entity_chain: [{
            entity_instance_id: 'entity-1', auth_asym_id: 'A', expected_residues: 1,
            mapped_residues: 1, scoreable_residues: 1, expected_slots: 20,
            observed_slots: 20, scoreable_slots: 20,
        }],
    },
    terminal_result: {
        schema_name: 'workflow_component_result', schema_version: 2, request_sha256: hashes.b,
        invocation_id: 'invoke-1', component_id: 'frustrampnn', component_contract_version: '2.0',
        candidate_id: 'candidate-1', parent_job_id: 'job-1', parent_workflow_id: 'structure_prediction',
        status: 'succeeded', failure_class: null,
        source_artifact: { artifact_id: null, sha256: hashes.a, media_type: 'chemical/x-pdb', producer_stage: 'structure_prediction' },
        runtime_identity: { runtime_identity_sha256: hashes.g }, artifacts: [],
        result_payload: { schema_name: 'frustrampnn_summary', schema_version: 2, sha256: hashes.d },
        started_at: '2026-08-09T00:00:00Z', ended_at: '2026-08-09T00:00:01Z', duration_seconds: 1,
        gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
    },
    execution_receipt: {
        schema_name: 'frustrampnn_execution_receipt', schema_version: 2, invocation_id: 'invoke-1',
        execution_configuration_sha256: hashes.a, requested_settings_sha256: hashes.a,
        effective_settings_sha256: hashes.b, runtime_identity_sha256: hashes.g,
        source_artifact_sha256: hashes.a, structure_map_sha256: hashes.b, normalized_pdb_sha256: hashes.c,
        command_count: 1, gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
        started_at: '2026-08-09T00:00:00Z', ended_at: '2026-08-09T00:00:01Z', duration_seconds: 1,
    },
};

const identityAlignment = {
    status: 'exact',
    reasons: [],
    differences: [],
    reference_identity_count: 1,
    target_identity_count: 1,
    aligned_identity_count: 1,
};
const rawCompatible = { status: 'compatible', reasons: [], differences: [] };
const classDifferent = {
    status: 'policy_different',
    reasons: ['classification_policy_differs'],
    differences: [{ field_path: 'classification_policy.threshold_policy_sha256', left: hashes.a, right: hashes.b }],
};
const pairRow = {
    residue_key: { entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: 10, insertion_code: '' },
    sequence_index: 1,
    mutation_aa: 'A',
    wt: 'G',
    mapping_state: 'mapped',
    missingness_state: 'none',
    biological_status: 'biologically_scored',
    reference: { sequence_index: 1, auth_seq_id: 10, score: 0, class: 'neutral', scoreable: true, status: 'ok' },
    target: { sequence_index: 1, auth_seq_id: 10, score: 0.5, class: 'minimal', scoreable: true, status: 'ok' },
    raw_score_delta: 0.5,
    classification_transition: null,
};
const pairComparison = {
    schema_name: 'frustrampnn_comparison',
    schema_version: 1,
    comparison_id: 'cmp-1',
    comparison_sha256: hashes.a,
    reference_landscape_sha256: hashes.b,
    target_landscape_sha256: hashes.c,
    configuration_id: null,
    configuration_sha256: null,
    reference_configuration_sha256: null,
    target_configuration_sha256: null,
    comparability: {
        status: 'comparable', reasons: ['classification_policy_differs'], reference_configuration_id: null,
        target_configuration_id: null, reference_configuration_sha256: null, target_configuration_sha256: null,
    },
    compatibility_domains: { raw_score: rawCompatible, classification: classDifferent, identity_alignment: identityAlignment },
    summary: { total_rows: 1, biologically_scored: 1, incompatible: 0, unmapped: 0, missing_reference: 0, missing_target: 0, missing_both: 0, transitions: 0 },
    rows: [pairRow],
    persisted: true,
    created_at: '2026-08-09T00:00:00Z',
    reference: { parent_job_id: 'job-1', invocation_id: 'invoke-1' },
    target: { parent_job_id: 'job-2', invocation_id: 'invoke-2' },
    compatibility_status: 'compatible',
    left_comparison_compatibility_id: hashes.d,
    right_comparison_compatibility_id: hashes.e,
    override_used: false,
    compatibility_differences: [],
};

const pairCompatibility = {
    target_label: 'target-0001',
    target_id: 'candidate-2',
    target_landscape_sha256: hashes.c,
    target_configuration_sha256: null,
    compatibility_status: 'compatible',
    left_comparison_compatibility_id: hashes.d,
    right_comparison_compatibility_id: hashes.e,
    override_used: false,
    compatibility_differences: [],
    compatibility_domains: { raw_score: rawCompatible, classification: classDifferent, identity_alignment: identityAlignment },
};
const multiComparison = {
    schema_name: 'frustrampnn_multistate_comparison',
    schema_version: 1,
    comparison_mode: 'multi_state',
    comparison_id: 'cmp-multi',
    comparison_sha256: hashes.a,
    reference_landscape_sha256: hashes.b,
    target_landscape_sha256: hashes.c,
    target_landscape_sha256s: [hashes.c],
    target_labels: ['target-0001'],
    configuration_id: null,
    configuration_sha256: null,
    reference_configuration_sha256: null,
    target_configuration_sha256s: [null],
    pair_compatibility: [pairCompatibility],
    source_result_references: [
        { role: 'reference', target_label: null, parent_job_id: 'job-1', invocation_id: 'invoke-1', landscape_sha256: hashes.b, configuration_sha256: null },
        { role: 'target', target_label: 'target-0001', parent_job_id: 'job-2', invocation_id: 'invoke-2', landscape_sha256: hashes.c, configuration_sha256: null },
    ],
    comparability: {
        status: 'comparable', reasons: [], target_count: 1, pair_compatibility: [pairCompatibility],
        compatibility_status: 'compatible', left_comparison_compatibility_id: hashes.d,
        right_comparison_compatibility_id: hashes.e, override_used: false, compatibility_differences: [],
    },
    summary: { target_count: 1, total_rows: 1, biologically_scored: 1, partially_scored: 0, missing: 0, unmapped: 0, incompatible: 0, transitions: 0 },
    rows: [{
        residue_key: pairRow.residue_key,
        sequence_index: 1,
        mutation_aa: 'A',
        mapping_state: 'mapped',
        missingness_state: 'none',
        missingness_by_target: ['none'],
        biological_status: 'biologically_scored',
        reference: pairRow.reference,
        targets: [pairRow.target],
        raw_score_deltas: [0.5],
        classification_transitions: [null],
    }],
    persisted: true,
    created_at: '2026-08-09T00:00:00Z',
    reference: { parent_job_id: 'job-1', invocation_id: 'invoke-1' },
    target: { parent_job_id: 'job-2', invocation_id: 'invoke-2' },
    compatibility_status: 'compatible',
    left_comparison_compatibility_id: hashes.d,
    right_comparison_compatibility_id: hashes.e,
    override_used: false,
    compatibility_differences: [],
};

const structureMap = {
    schema_name: 'frustrampnn_structure_map', schema_version: 1,
    target_id: 'candidate-1', parent_job_id: 'job-1', candidate_id: 'candidate-1',
    source_format: 'pdb', source_sha256: hashes.a, source_bytes: 100,
    identity_authority: 'pdb_self_identity_v1', identity_domain: 'candidate_local',
    authority_artifact_sha256: hashes.b, normalized_pdb_sha256: hashes.c,
    selected_source_model: 1, altloc_policy: 'blank_or_explicit:<blank>',
    normalizer_version: 'frustrampnn_structure_normalizer_v1',
    model_ready_sequence: 'A', model_ready_sequence_sha256: hashes.d,
    excluded_records: [],
    rows: [{
        entity_instance_id: 'entity-1', source_entity_id: null, label_asym_id: null,
        auth_asym_id: 'A', label_seq_id: null, auth_seq_id: 10, insertion_code: '',
        sequence_index: 1, pdb_chain_id: 'A', pdb_residue_id: 10, pdb_insertion_code: '',
        model_position: 0, residue_name: 'ALA', wt: 'A', selected_model: 1,
        selected_altloc: '', backbone_complete: true,
        backbone_atoms: { N: '1', CA: '2', C: '3', O: '4' }, status: 'mapped', reason: null,
    }],
};

const handoffReceipt = {
    job_id: 'child-job-1',
    child_job_id: 'child-job-1',
    result_job_id: 'child-job-1',
    name: 'FrustraMPNN external candidate handoff',
    parent_job_id: 'job-1',
    source_parent_job_id: 'job-1',
    trigger: 'external_candidate_handoff',
    status: 'queued',
    created_at: '2026-08-09T00:00:00Z',
    started_at: null,
    completed_at: null,
    settings_value_origin: 'operator_request',
    requested_settings: persistedRequestedSettings,
    requested_settings_sha256: hashes.a,
    candidates: [],
    results: [],
    handoff: {
        parent_landscape_sha256: hashes.d,
        parent_candidate_id: 'candidate-1',
        guidance_id: 'guidance-1',
        producer_id: 'producer-1',
    },
};

test('structure dataset fan-out parser exposes every scheduler child and smaller remainder', () => {
    const parseFanout = (frustraMpnnApi as unknown as {
        parseFrustraMpnnStructureDatasetFanout: (value: unknown) => {
            child_jobs: Array<{ child_job_id: string; structure_count: number }>;
        };
    }).parseFrustraMpnnStructureDatasetFanout;
    assert.equal(typeof parseFanout, 'function');
    const child = ({ id, count }: { id: string; count: number }) => ({
        ...handoffReceipt,
        job_id: id,
        child_job_id: id,
        result_job_id: id,
        name: `FrustraMPNN ${id}`,
        trigger: 'design_analyze',
        handoff: undefined,
        structure_count: count,
    });
    const parsed = parseFanout({
        schema_name: 'bms.structure-dataset-fanout.v1',
        fanout_id: hashes.f,
        parent_job_id: 'job-1',
        selected_structure_count: 3,
        structures_per_job: 2,
        replayed: false,
        child_jobs: [child({ id: 'child-1', count: 2 }), child({ id: 'child-2', count: 1 })].map(({ handoff: _handoff, ...value }) => value),
    });
    assert.deepEqual(parsed.child_jobs.map((item) => [item.child_job_id, item.structure_count]), [
        ['child-1', 2],
        ['child-2', 1],
    ]);
});


test('handoff parser preserves exact backend metadata and rejects legacy or malformed handoff contracts', () => {
    const parsed = parseFrustraMpnnChildReceipt(handoffReceipt, true);
    assert.deepEqual(parsed.handoff, handoffReceipt.handoff);
    assert.throws(
        () => parseFrustraMpnnChildReceipt({ ...handoffReceipt, handoff: 'accepted' }, true),
        /handoff/i,
    );
    assert.throws(
        () => parseFrustraMpnnChildReceipt({
            ...handoffReceipt,
            handoff: { ...handoffReceipt.handoff, parent_landscape_sha256: 'not-a-hash' },
        }, true),
        /SHA-256/i,
    );
    assert.throws(
        () => parseFrustraMpnnChildReceipt({
            ...handoffReceipt,
            handoff: { ...handoffReceipt.handoff, relative_path: 'private/candidate.pdb' },
        }, true),
        /unknown|forbidden/i,
    );
});

test('artifact parser accepts the exact backend path-free contract and uses its download URL', () => {
    const response = structuredClone(backendArtifactList);
    const parsed = parseFrustraMpnnArtifactList(response);
    assert.equal(parsed.items[0]?.artifact_id, 'artifact/structure-map');
    assert.equal('relative_path' in parsed.items[0]!, false);
    assert.equal(parsed.items[0]?.download_url, response.items[0]?.download_url);
    assert.throws(
        () => parseFrustraMpnnArtifactList({
            ...response,
            items: [{ ...response.items[0], relative_path: 'private/structure-map.json' }],
        }),
        /unknown|forbidden/i,
    );
    assert.throws(
        () => parseFrustraMpnnArtifactList({
            ...response,
            items: [{ ...response.items[0], invocation_id: 'invoke-1' }],
        }),
        /unknown|forbidden/i,
    );
});

test('artifact selection requires one exact role, schema, version, and media identity', () => {
    const selectArtifact = (frustraMpnnApi as unknown as {
        selectFrustraMpnnArtifactByIdentity: (
            items: Array<Record<string, unknown>>,
            identity: Record<string, unknown>,
        ) => Record<string, unknown> | undefined;
    }).selectFrustraMpnnArtifactByIdentity;
    const exact = {
        artifact_id: 'map-exact', role: 'structure_map',
        content_sha256: hashes.a, size_bytes: 123, media_type: 'application/json',
        schema_name: 'frustrampnn_structure_map', schema_version: 1,
        cardinality: { kind: 'residues', count: 1 },
        download_url: '/api/frustrampnn/artifacts/map-exact?job_id=job-1',
    };
    const wrongSchema = { ...exact, artifact_id: 'map-wrong-schema', schema_name: 'other_map' };
    const wrongMedia = { ...exact, artifact_id: 'map-wrong-media', media_type: 'text/plain' };
    const identity = {
        role: 'structure_map', media_type: 'application/json',
        schema_name: 'frustrampnn_structure_map', schema_version: 1,
    };
    assert.equal(selectArtifact([wrongSchema, exact, wrongMedia], identity)?.artifact_id, 'map-exact');
    assert.equal(selectArtifact([wrongSchema, wrongMedia], identity), undefined);
    assert.throws(
        () => selectArtifact([exact, { ...exact, artifact_id: 'map-duplicate' }], identity),
        /ambiguous|multiple/i,
    );
});

test('structure-map download recursively validates the complete canonical document before returning authority', async () => {
    const originalGet = api.get;
    let response: unknown = structureMap;
    (api as unknown as { get: () => Promise<{ data: unknown }> }).get = async () => ({ data: response });
    try {
        assert.equal((await fetchFrustraMpnnStructureMap('/download/map')).rows[0]?.pdb_residue_id, 10);
        for (const tampered of [
            { ...structureMap, browser_only: true },
            { ...structureMap, rows: [] },
            { ...structureMap, source_bytes: Number.POSITIVE_INFINITY },
            { ...structureMap, identity_domain: 'browser_inferred' },
            { ...structureMap, rows: [{ ...structureMap.rows[0], browser_only: true }] },
            { ...structureMap, rows: [{ ...structureMap.rows[0], label_seq_id: 10_000 }] },
            { ...structureMap, rows: [{ ...structureMap.rows[0], pdb_residue_id: 10_000 }] },
            { ...structureMap, rows: [{ ...structureMap.rows[0], backbone_atoms: { ...structureMap.rows[0]!.backbone_atoms, CB: '5' } }] },
            { ...structureMap, excluded_records: [{ source_identity: 'x', reason_code: 'browser_reason', reason: 'bad' }] },
        ]) {
            response = tampered;
            await assert.rejects(() => fetchFrustraMpnnStructureMap('/download/map'), /structure map|unknown|invalid|finite|rows|identity|pdb_residue_id|reason_code/i);
        }
    } finally {
        (api as unknown as { get: typeof originalGet }).get = originalGet;
    }
});

test('closed v2 result detail preserves authority, settings, statistics, and safe receipt identities', () => {
    const parsed = parseFrustraMpnnResultDetail(resultDetail);
    assert.equal(parsed.authority_version, 'v2');
    assert.equal(parsed.availability, true);
    assert.equal(parsed.effective_settings_json?.settings_value_origin, 'operator_request');
    assert.equal(parsed.effective_settings_json?.requested_settings.source_structure.selected_model_number, 1);
    assert.deepEqual(parsed.effective_settings_json?.resolved_chains[0].residues[0], effectiveSettings.resolved_chains[0].residues[0]);
    assert.equal(parsed.statistics_json?.distributions.overall.sample_sd, 0.1);
    assert.equal(parsed.execution_receipt?.runtime_identity_sha256, hashes.g);
    assert.equal('command_plan' in parsed.execution_receipt!, false);
    assert.equal('path' in parsed.effective_settings_json!, false);

    for (const [field, invalid] of [
        ['pdb_insertion_code', 'AB'],
        ['residue_name', 'GLYCINE'],
    ] as const) {
        const malformed = structuredClone(resultDetail);
        malformed.effective_settings_json.resolved_chains[0]!.residues[0]![field] = invalid;
        assert.throws(() => parseFrustraMpnnResultDetail(malformed), new RegExp(field));
    }
});

test('closed v3 result detail opens before derived statistics complete', () => {
    const current = structuredClone(resultDetail);
    current.authority_version = 'v3';
    current.statistics_available = false;
    current.missing_fields = [
        'statistics_sha256',
        'statistics_json',
        'comparison_compatibility_id',
    ];
    current.statistics_sha256 = null;
    current.statistics_json = null;
    current.comparison_compatibility_id = null;
    current.component_contract_version = '3.0';
    current.summary.schema_version = 3;
    current.summary.execution_configuration_id = 'frustrampnn_execution_configuration_v3';
    current.terminal_result.schema_version = 3;
    current.terminal_result.component_contract_version = '3.0';
    current.terminal_result.result_payload.schema_version = 3;
    current.execution_receipt.schema_version = 3;

    const parsed = parseFrustraMpnnResultDetail(current);
    assert.equal(parsed.authority_version, 'v3');
    assert.equal(parsed.summary.schema_version, 3);
    assert.equal(parsed.statistics_json, null);
});

test('v2 result summary enforces canonical minima, nonempty chain support, and closed finite fields', () => {
    const emptyChains = structuredClone(resultDetail);
    emptyChains.summary.support_by_entity_chain = [];
    assert.throws(() => parseFrustraMpnnResultDetail(emptyChains), /support_by_entity_chain/i);

    for (const [field, invalid] of [
        ['residue_support.expected', 0],
        ['residue_support.mapped', 0],
        ['residue_support.scoreable', 0],
        ['slot_support.expected', 19],
        ['slot_support.observed', 19],
        ['slot_support.scoreable', 19],
        ['support_by_entity_chain.0.expected_residues', 0],
        ['support_by_entity_chain.0.mapped_residues', 0],
        ['support_by_entity_chain.0.scoreable_residues', 0],
        ['support_by_entity_chain.0.expected_slots', 19],
        ['support_by_entity_chain.0.observed_slots', 19],
        ['support_by_entity_chain.0.scoreable_slots', 19],
    ] as const) {
        const tampered = structuredClone(resultDetail) as any;
        const segments = field.split('.');
        let target = tampered.summary;
        for (const segment of segments.slice(0, -1)) target = target[segment];
        target[segments.at(-1)!] = invalid;
        assert.throws(() => parseFrustraMpnnResultDetail(tampered), new RegExp(field.split('.').at(-1)!, 'i'));
    }

    const unknown = structuredClone(resultDetail) as any;
    unknown.summary.support_by_entity_chain[0].browser_only = true;
    assert.throws(() => parseFrustraMpnnResultDetail(unknown), /unknown|missing/i);
    const nonFinite = structuredClone(resultDetail) as any;
    nonFinite.summary.threshold_policy.high_max = Number.POSITIVE_INFINITY;
    assert.throws(() => parseFrustraMpnnResultDetail(nonFinite), /finite/i);
});

test('historical result parsing remains explicit and does not infer unavailable v2 authority', () => {
    const historical = {
        ...resultDetail,
        authority_version: 'historical_v1', availability: false, statistics_available: false,
        missing_fields: ['settings_sha256', 'effective_settings_json', 'statistics_json'],
        settings_sha256: null, effective_settings_sha256: null, effective_settings_json: null,
        capability_inventory_sha256: null, statistics_sha256: null, statistics_json: null,
        comparison_compatibility_id: null, component_contract_version: '1.0', execution_receipt: null,
    };
    const parsed = parseFrustraMpnnResultDetail(historical);
    assert.equal(parsed.authority_version, 'historical_v1');
    assert.equal(parsed.availability, false);
    assert.deepEqual(parsed.missing_fields, historical.missing_fields);

    const safeV1Summary = {
        schema_name: 'frustrampnn_summary', schema_version: 1,
        target_id: 'candidate-1', parent_job_id: 'job-1', candidate_id: 'candidate-1', landscape_sha256: hashes.a,
        residue_support: { expected: 0, mapped: 0, scoreable: 0, excluded: 0, ambiguous: 0 },
        slot_support: { expected: 0, observed: 0, scoreable: 0 }, missingness_by_reason: {},
        native_slot_counts: { high: 0, neutral: 0, minimal: 0 },
        native_slot_fractions: { high: 0, neutral: 0, minimal: 0 },
        complete_landscape_counts: { high: 0, neutral: 0, minimal: 0 },
        complete_landscape_fractions: { high: 0, neutral: 0, minimal: 0 },
        support_by_entity_chain: [],
        configuration_id: 'frustrampnn_global_v1', configuration_sha256: hashes.c,
        threshold_policy: { id: 'frustrampnn_class_v1', high_max: -1.0, minimal_min: 0.58 },
        threshold_policy_sha256: hashes.b,
    };
    assert.equal(parseFrustraMpnnResultDetail({ ...historical, summary: safeV1Summary }).summary.schema_version, 1);
    assert.throws(
        () => parseFrustraMpnnResultDetail({ ...historical, summary: { ...safeV1Summary, threshold_policy: { ...safeV1Summary.threshold_policy, high_max: -2 } } }),
        /threshold_policy|high_max/i,
    );
});

test('statistics response and discriminated query preserve canonical persisted values and reject unsafe fields', () => {
    const response = {
        result_id: 'result-1', parent_job_id: 'job-1', candidate_id: 'candidate-1', invocation_id: 'invoke-1',
        authority_version: 'v2', availability: true, missing_fields: [], settings_sha256: hashes.a,
        effective_settings_sha256: hashes.b, effective_settings_json: effectiveSettings,
        capability_inventory_sha256: hashes.d, statistics_sha256: hashes.f, statistics_json: statistics,
        comparison_compatibility_id: hashes.e, statistics,
    };
    const parsed = parseFrustraMpnnStatisticsResponse(response);
    assert.equal(parsed.statistics?.support.scoreable_slot_count, 20);
    assert.equal(parsed.statistics?.class_burden.all.counts.neutral, 2);
    assert.throws(() => parseFrustraMpnnStatisticsResponse({ ...response, storage_path: '/private/result.json' }), /unknown|forbidden/i);
    const nestedExtra = structuredClone(response);
    nestedExtra.statistics.support.residue_fractions.selected.browser_imputed = true;
    assert.throws(() => parseFrustraMpnnStatisticsResponse(nestedExtra), /unknown|missing/i);
    const nestedMissing = structuredClone(response);
    delete nestedMissing.statistics.comparison_compatibility_basis.raw_score_semantics.model.checkpoint_id;
    assert.throws(() => parseFrustraMpnnStatisticsResponse(nestedMissing), /unknown|missing/i);
    const nonFinite = structuredClone(response);
    nonFinite.statistics.per_residue[0]!.native_score = Number.POSITIVE_INFINITY;
    assert.throws(() => parseFrustraMpnnStatisticsResponse(nonFinite), /finite/i);

    const query = parseFrustraMpnnStatisticsQueryResponse({
        items: [{
            dataset: { parent_job_id: 'job-1', invocation_id: 'invoke-1' },
            level: 'overview', key: {}, availability: true, unavailable_reason: null,
            distribution, native_distribution: distribution, non_native_distribution: distribution,
            class_burden: classBurden, native_score: null, native_class: null,
            support: statistics.support,
        }],
        total: 1, limit: 20, offset: 0, next_offset: null,
    });
    assert.equal(query.total, 1);
    assert.equal(query.items[0]?.level, 'overview');
    assert.equal(query.items[0]?.support?.scoreable_slot_count, 20);
});

test('statistics query rows enforce level-specific key and support unions plus coherent pagination', () => {
    const base = {
        dataset: { parent_job_id: 'job-1', invocation_id: 'invoke-1' },
        availability: true, unavailable_reason: null, distribution: null,
        native_distribution: null, non_native_distribution: null, class_burden: null,
        native_score: null, native_class: null,
    };
    const rows = [
        { ...base, level: 'overview', key: {}, support: statistics.support },
        { ...base, level: 'residue', key: null, support: null },
        { ...base, level: 'mutation_aa', key: { mutation_aa: 'A' }, support: null },
        { ...base, level: 'chain', key: { entity_instance_id: 'entity-1', source_entity_id: null, label_asym_id: null, auth_asym_id: 'A', pdb_chain_id: 'A' }, support: statistics.per_chain[0]!.support },
        { ...base, level: 'entity', key: { entity_instance_id: 'entity-1', source_entity_id: null, label_asym_id: null }, support: statistics.per_entity[0]!.support },
    ];
    const parsed = parseFrustraMpnnStatisticsQueryResponse({ items: rows, total: 5, limit: 5, offset: 0, next_offset: null });
    assert.equal(parsed.items[0]?.level, 'overview');
    assert.equal(parsed.items[1]?.level, 'residue');
    assert.equal(parsed.items[3]?.level, 'chain');

    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: [{ ...rows[0], key: null }], total: 1, limit: 1, offset: 0, next_offset: null }), /overview|key/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: [{ ...rows[1], support: statistics.per_chain[0]!.support }], total: 1, limit: 1, offset: 0, next_offset: null }), /residue|support/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: [{ ...rows[2], support: statistics.per_chain[0]!.support }], total: 1, limit: 1, offset: 0, next_offset: null }), /mutation_aa|support/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: [{ ...rows[3], key: { ...rows[3]!.key, mutation_aa: 'A' } }], total: 1, limit: 1, offset: 0, next_offset: null }), /unknown|missing/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: rows.slice(0, 2), total: 1, limit: 2, offset: 0, next_offset: null }), /pagination/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: rows.slice(0, 2), total: 3, limit: 1, offset: 0, next_offset: 2 }), /pagination/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: rows.slice(0, 1), total: 2, limit: 501, offset: 0, next_offset: 1 }), /pagination|limit/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: rows.slice(0, 1), total: 2, limit: 1, offset: 0, next_offset: null }), /pagination|next_offset/i);
    assert.throws(() => parseFrustraMpnnStatisticsQueryResponse({ items: rows.slice(0, 1), total: 1, limit: 1, offset: 0, next_offset: 1 }), /pagination|next_offset/i);
});

test('statistics query requests enforce the backend level-specific filters and pagination bound', async () => {
    const datasets = [{ parent_job_id: 'job-1', invocation_id: 'invoke-1' }];
    await assert.rejects(
        () => frustraMpnnApi.queryFrustraMpnnStatistics({ datasets, level: 'overview', filters: { mutation_aa: 'A' } } as never),
        /overview filters do not allow mutation_aa/,
    );
    await assert.rejects(
        () => frustraMpnnApi.queryFrustraMpnnStatistics({ datasets, level: 'chain', offset: 1_000_001 }),
        /pagination is invalid/,
    );
});

test('persisted comparison row pages discriminate exact pair and multi rows while preserving pagination', async () => {
    const originalGet = api.get;
    const pages = [
        { comparison_id: 'cmp-1', items: [pairRow], total: 2, limit: 1, offset: 0, next_offset: 1 },
        { comparison_id: 'cmp-multi', items: [multiComparison.rows[0]], total: 1, limit: 1, offset: 0, next_offset: null },
    ];
    let index = 0;
    (api as unknown as { get: () => Promise<{ data: unknown }> }).get = async () => ({ data: pages[index++] });
    try {
        const pair = await fetchFrustraMpnnComparisonRows('cmp-1', 1, 0);
        assert.equal(pair.items[0]?.kind, 'pair');
        assert.equal(pair.next_offset, 1);
        const multi = await fetchFrustraMpnnComparisonRows('cmp-multi', 1, 0);
        assert.equal(multi.items[0]?.kind, 'multi');
        assert.deepEqual(multi.items[0]?.raw_score_deltas, [0.5]);
        assert.equal(multi.next_offset, null);
    } finally {
        (api as unknown as { get: typeof originalGet }).get = originalGet;
    }
});

test('guidance parser and create/get clients validate the exact nested backend response', async () => {
    const parsed = parseFrustraMpnnGuidance(backendGuidance);
    assert.equal(parsed.configuration_id, 'frustrampnn_execution_configuration_v2');
    assert.equal(parsed.ranked_slots[0]?.scoreable, true);
    assert.equal(parsed.observed_outcome, null);
    assert.equal(parsed.persisted, true);
    assert.throws(
        () => parseFrustraMpnnGuidance({
            ...backendGuidance,
            region: { ...backendGuidance.region, relative_path: 'private/region.json' },
        }),
        /unknown|missing/i,
    );

    const originalPost = api.post;
    const originalGet = api.get;
    let postedGuidance: unknown = null;
    (api as unknown as { post: (_url: string, body: unknown) => Promise<{ data: unknown }> }).post = async (_url, body) => { postedGuidance = body; return { data: backendGuidance }; };
    (api as unknown as { get: () => Promise<{ data: unknown }> }).get = async () => ({ data: backendGuidance });
    try {
        const request = {
            source_job_id: 'job-1', source_invocation_id: 'invoke-1',
            region: { region_type: 'residue_set' as const, residues: [{ auth_asym_id: 'A', auth_seq_id: 10, insertion_code: '' }] },
            objective: { objective_type: 'score_aggregate' as const, direction: 'higher_is_better' as const, aggregation: 'mean' as const },
            constraints: { prohibited_mutations: [] }, ranking: { mode: 'lexicographic' }, rationale: 'Test hypothesis.',
        };
        assert.equal((await createFrustraMpnnGuidance(request)).guidance_id, 'guidance-1');
        assert.deepEqual(postedGuidance, {
            ...request,
            constraints: { prohibited_mutations: [] },
            ranking: { mode: 'lexicographic', tie_break: null },
            objective: { ...request.objective, target_class: null, reference_class: null },
        });
        await assert.rejects(
            () => createFrustraMpnnGuidance({ ...request, objective: { objective_type: 'class_count', direction: 'higher_is_better' } } as never),
            /target_class|missing/i,
        );
        await assert.rejects(
            () => createFrustraMpnnGuidance({ ...request, constraints: { arbitrary: true } } as never),
            /unknown|missing/i,
        );
        assert.equal((await fetchFrustraMpnnGuidance('guidance-1')).guidance_id, 'guidance-1');
    } finally {
        (api as unknown as { post: typeof originalPost }).post = originalPost;
        (api as unknown as { get: typeof originalGet }).get = originalGet;
    }
});

test('pair parser uses native domains and suppresses only unsafe transitions', () => {
    const parsed = parseFrustraMpnnComparison(pairComparison);
    assert.equal(parsed.schema_name, 'frustrampnn_comparison');
    assert.equal(parsed.compatibility_domains.raw_score.status, 'compatible');
    assert.equal(parsed.compatibility_domains.classification.status, 'policy_different');
    assert.equal(parsed.rows[0]?.raw_score_delta, 0.5);
    assert.equal(parsed.rows[0]?.classification_transition, null);
    assert.equal('raw_score_compatibility' in parsed, false);

    assert.throws(() => parseFrustraMpnnComparison({
        ...pairComparison,
        compatibility_domains: {
            ...pairComparison.compatibility_domains,
            raw_score: { status: 'hard_incompatible', reasons: ['checkpoint_mismatch'], differences: [] },
        },
        rows: [{ ...pairRow, biological_status: 'incompatible', raw_score_delta: 0.5 }],
        override_used: true,
    }), /delta|unsafe|compatible/i);
    assert.throws(() => parseFrustraMpnnComparison({
        ...pairComparison,
        override_used: true,
    }), /override.*must not contain/i);
});

test('multi parser preserves ordered pair domains and rejects unsafe per-target deltas and transitions', () => {
    const parsed = parseFrustraMpnnComparison(multiComparison);
    assert.equal(parsed.schema_name, 'frustrampnn_multistate_comparison');
    assert.deepEqual(parsed.target_labels, ['target-0001']);
    assert.equal(parsed.pair_compatibility[0]?.compatibility_domains.classification.status, 'policy_different');
    assert.deepEqual(parsed.rows[0]?.raw_score_deltas, [0.5]);
    assert.deepEqual(parsed.rows[0]?.classification_transitions, [null]);
    assert.equal(parsed.source_result_references[1]?.invocation_id, 'invoke-2');

    const inconsistentOverridePair = { ...pairCompatibility, override_used: true };
    assert.throws(() => parseFrustraMpnnComparison({
        ...multiComparison,
        pair_compatibility: [inconsistentOverridePair],
        comparability: {
            ...multiComparison.comparability,
            pair_compatibility: [inconsistentOverridePair],
            override_used: true,
        },
        override_used: true,
    }), /override.*must not contain/i);

    const hardPair = {
        ...pairCompatibility,
        compatibility_status: 'incompatible',
        override_used: true,
        compatibility_domains: {
            ...pairCompatibility.compatibility_domains,
            raw_score: { status: 'hard_incompatible', reasons: ['checkpoint_mismatch'], differences: [] },
        },
    };
    assert.throws(() => parseFrustraMpnnComparison({
        ...multiComparison,
        pair_compatibility: [hardPair],
        comparability: { ...multiComparison.comparability, status: 'incompatible', compatibility_status: 'incompatible', pair_compatibility: [hardPair], override_used: true },
        rows: [{ ...multiComparison.rows[0], biological_status: 'incompatible', raw_score_deltas: [0.5] }],
        compatibility_status: 'incompatible',
        override_used: true,
    }), /delta|unsafe|compatible/i);
});

test('comparison summaries use exact closed pair and multi contracts', () => {
    const parsedPair = parseFrustraMpnnComparison(pairComparison);
    assert.equal(parsedPair.schema_name, 'frustrampnn_comparison');
    assert.equal(parsedPair.summary.missing_reference, 0);
    const pairExtra = structuredClone(pairComparison) as any;
    pairExtra.summary.browser_metric = 1;
    assert.throws(() => parseFrustraMpnnComparison(pairExtra), /unknown|missing/i);
    const pairMissing = structuredClone(pairComparison) as any;
    delete pairMissing.summary.transitions;
    assert.throws(() => parseFrustraMpnnComparison(pairMissing), /unknown|missing/i);

    const parsedMulti = parseFrustraMpnnComparison(multiComparison);
    assert.equal(parsedMulti.schema_name, 'frustrampnn_multistate_comparison');
    assert.equal(parsedMulti.summary.partially_scored, 0);
    const multiExtra = structuredClone(multiComparison) as any;
    multiExtra.summary.browser_metric = 1;
    assert.throws(() => parseFrustraMpnnComparison(multiExtra), /unknown|missing/i);
    const multiTooManyTargets = structuredClone(multiComparison) as any;
    multiTooManyTargets.summary.target_count = 9;
    assert.throws(() => parseFrustraMpnnComparison(multiTooManyTargets), /target_count|summary/i);
});

test('typed multi comparison client preserves ordered scoped result references', async () => {
    const originalPost = api.post;
    let capturedUrl = '';
    let capturedBody: unknown;
    (api as unknown as { post: (url: string, body: unknown) => Promise<{ data: unknown }> }).post = async (url, body) => {
        capturedUrl = url;
        capturedBody = body;
        return { data: multiComparison };
    };
    try {
        const parsed = await createFrustraMpnnMultiComparison(
            { parent_job_id: 'job-1', invocation_id: 'invoke-1' },
            [{ parent_job_id: 'job-2', invocation_id: 'invoke-2' }],
        );
        assert.equal(parsed.schema_name, 'frustrampnn_multistate_comparison');
        assert.equal(capturedUrl, '/api/frustrampnn/comparisons/multi');
        assert.deepEqual(capturedBody, {
            reference_job_id: 'job-1',
            reference_invocation_id: 'invoke-1',
            targets: [{
                reference_job_id: 'job-1', reference_invocation_id: 'invoke-1',
                target_job_id: 'job-2', target_invocation_id: 'invoke-2',
            }],
            allow_incompatible: false,
        });
    } finally {
        (api as unknown as { post: typeof originalPost }).post = originalPost;
    }
});

test('multi target selection stays ordered, unique, reference-safe, and bounded', async () => {
    const {
        appendFrustraMpnnComparisonTarget,
        moveFrustraMpnnComparisonTarget,
        MAX_FRUSTRAMPNN_MULTI_TARGETS,
    } = await import('../src/components/frustrampnn/frustraMpnnComparisonSelection.js');
    const reference = { parent_job_id: 'job-reference', invocation_id: 'invoke-reference' };
    const candidates = Array.from({ length: MAX_FRUSTRAMPNN_MULTI_TARGETS + 1 }, (_, index) => ({
        parent_job_id: `job-${index + 1}`,
        invocation_id: `invoke-${index + 1}`,
        label: `Candidate ${index + 1}`,
    }));
    let selected = candidates.slice(0, 2);
    selected = appendFrustraMpnnComparisonTarget(selected, candidates[0]!, reference);
    assert.deepEqual(selected.map((item) => item.label), ['Candidate 1', 'Candidate 2']);
    selected = moveFrustraMpnnComparisonTarget(selected, 1, -1);
    assert.deepEqual(selected.map((item) => item.label), ['Candidate 2', 'Candidate 1']);
    selected = candidates.reduce(
        (current, candidate) => appendFrustraMpnnComparisonTarget(current, candidate, reference),
        selected,
    );
    assert.equal(selected.length, MAX_FRUSTRAMPNN_MULTI_TARGETS);
    assert.throws(
        () => appendFrustraMpnnComparisonTarget(selected, {
            ...reference,
            label: 'Reference result',
        }, reference),
        /reference/i,
    );
});

test('requested-effective settings summary preserves safe identities, counts, thresholds, and origins', async () => {
    const { buildFrustraMpnnRequestedEffectiveSummary } = await import(
        '../src/components/frustrampnn/frustraMpnnSettingsSummary.js'
    );
    const resolved = {
        ...effectiveSettings,
        requested_settings: {
            ...persistedRequestedSettings,
            protein_selection: {
                mode: 'selected_residues',
                entities: [],
                regions: [],
                residues: [{
                    entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A',
                    auth_seq_id: 10, insertion_code: '', sequence_index: 1,
                }],
            },
            source_structure: { selected_model_number: 2, preferred_altloc: 'A' },
            classification_policy: { mode: 'custom', high_max: -0.75, minimal_min: 0.25 },
        },
        resolved_chains: [{
            entity: { entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A', pdb_chain_id: 'A' },
            pdb_chain_id: 'A',
            residues: [{
                entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A',
                auth_seq_id: 10, insertion_code: '', sequence_index: 1, wt: 'G', pdb_chain_id: 'A', model_position: 0,
            }],
        }],
    } as const;
    const summary = buildFrustraMpnnRequestedEffectiveSummary(resolved);
    assert.deepEqual(summary.model, { requested: 2, effective: 2, origin: 'operator_request' });
    assert.deepEqual(summary.altloc, { requested: 'A', effective: 'A', origin: 'operator_request' });
    assert.deepEqual(summary.thresholds, {
        mode: 'custom', highMax: -0.75, minimalMin: 0.25,
        origins: { mode: 'operator_request', highMax: 'operator_request', minimalMin: 'operator_request' },
    });
    assert.deepEqual(summary.counts, {
        selectedEntities: 0, selectedRegions: 0, selectedResidues: 1,
        resolvedEntities: 1, resolvedChains: 1, resolvedResidues: 1,
    });
    assert.match(summary.selectedResidues[0]!, /entity-1.*A:10.*sequence 1/i);
    assert.match(summary.resolvedResidues[0]!, /entity-1.*A:10.*G.*model position 0/i);
    assert.equal(Object.keys(summary.valueOrigins).length, 9);
    assert.equal(JSON.stringify(summary).includes('/private/'), false);
});

test('requested-effective settings summary reports source sequence regions separately', async () => {
    const { buildFrustraMpnnRequestedEffectiveSummary } = await import(
        '../src/components/frustrampnn/frustraMpnnSettingsSummary.js'
    );
    const resolved = {
        ...effectiveSettings,
        requested_settings: {
            ...persistedRequestedSettings,
            protein_selection: {
                mode: 'selected_regions',
                entities: [],
                regions: [{
                    entity_instance_id: 'entity-1', source_entity_id: '1',
                    label_asym_id: null, auth_asym_id: null,
                    sequence_start: 10, sequence_end: 24,
                }],
                residues: [],
            },
        },
    } as const;

    const summary = buildFrustraMpnnRequestedEffectiveSummary(resolved as never);
    assert.equal(summary.counts.selectedRegions, 1);
    assert.match(summary.selectedRegions[0]!, /source instance entity-1.*sequence 10–24/i);
    assert.equal(summary.valueOrigins['selected regions'], 'operator_request');
});


test('all standard launch surfaces own one typed settings panel and the typed statistics route', () => {
    const apiSource = readFileSync('src/lib/frustraMpnnApi.ts', 'utf8');
    const analysisSource = readFileSync('src/components/FrustraMpnnAnalysisControls.tsx', 'utf8');
    const uploadSource = readFileSync('src/components/FrustraMpnnUploadAnalysisPanel.tsx', 'utf8');
    const resultsViewerSource = readFileSync('src/components/ResultsViewer.tsx', 'utf8');
    const dataViewerLandingSource = readFileSync('src/components/DataViewerLanding.tsx', 'utf8');
    const resultSource = readFileSync('src/components/FrustraMpnnResultsViewer.tsx', 'utf8');
    const handoffSource = readFileSync('src/components/FrustraMpnnCandidateHandoffPanel.tsx', 'utf8');
    const panelSource = readFileSync('src/components/frustrampnn/FrustraMpnnSettingsPanel.tsx', 'utf8');
    const structureSource = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');

    assert.match(apiSource, /\/api\/frustrampnn\/statistics\/query/);
    assert.match(apiSource, /createFrustraMpnnMultiComparison/);
    assert.match(apiSource, /\/api\/frustrampnn\/comparisons\/multi/);
    for (const route of [
        '/api/frustrampnn/sources/inspect/owned',
        '/api/frustrampnn/sources/inspect/upload',
        '/api/frustrampnn/settings/validate/owned',
        '/api/frustrampnn/settings/validate/upload',
    ]) assert.ok(apiSource.includes(route), `missing governed client route ${route}`);
    assert.doesNotMatch(apiSource, /['"]\/api\/frustrampnn\/sources\/inspect['"]/);
    assert.doesNotMatch(apiSource, /['"]\/api\/frustrampnn\/settings\/validate['"]/);
    assert.equal((analysisSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.equal((uploadSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.doesNotMatch(resultsViewerSource, /<FrustraMpnnUploadAnalysisPanel/);
    assert.match(dataViewerLandingSource, /<FrustraMpnnUploadAnalysisPanel\s+onOpenJob=\{onSelectJob\}/);
    assert.equal((resultSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.equal((handoffSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.equal((structureSource.match(/<FrustraMpnnSettingsPanel/g) || []).length, 1);
    assert.match(resultSource, /selectFrustraMpnnArtifactByIdentity/);
    assert.match(resultSource, /role:\s*'structure_map'[\s\S]*schema_name:\s*'frustrampnn_structure_map'[\s\S]*schema_version:\s*1[\s\S]*media_type:\s*'application\/json'/);
    assert.match(resultSource, /role:\s*'normalized_input'[\s\S]*schema_name:\s*null[\s\S]*schema_version:\s*null[\s\S]*media_type:\s*'chemical\/x-pdb'/);
    assert.match(resultSource, /role:\s*'identity_authority'[\s\S]*schema_name:\s*'producer_manifest'[\s\S]*schema_version:\s*1[\s\S]*media_type:\s*'application\/json'/);
    assert.ok(analysisSource.indexOf('validateFrustraMpnnUploadedSettings') < analysisSource.indexOf('analyzeFrustraMpnnDesigns(parentJobId'));
    assert.ok(resultSource.indexOf('validateFrustraMpnnOwnedSettings') < resultSource.indexOf('reanalyzeFrustraMpnn(job.id'));
    assert.ok(handoffSource.indexOf('validateFrustraMpnnUploadedSettings') < handoffSource.indexOf('handoffFrustraMpnnCandidate(candidateFile'));
    assert.match(panelSource, /inspectFrustraMpnnOwnedSource/);
    assert.match(panelSource, /inspectFrustraMpnnUploadedSource/);
    assert.match(panelSource, /<FrustraMpnnRequestedEffectiveSummary/);
    assert.doesNotMatch(panelSource, /structureMap|structure_map/);
    for (const source of [analysisSource, resultSource, handoffSource, structureSource]) {
        assert.doesNotMatch(source, /raw\s*json|textarea[^>]*frustra|runtime command|scheduler field|storage path/i);
    }
});

test('saved review parser preserves the closed persisted contract and rejects nested state', () => {
    const value = {
        schema_name: 'frustrampnn_saved_review', schema_version: 1,
        review_id: 'review-1', parent_job_id: 'job-1', invocation_id: 'inv-1',
        landscape_sha256: 'a'.repeat(64), effective_settings_sha256: 'b'.repeat(64), review_sha256: 'c'.repeat(64), supersedes_review_id: null,
        title: 'Review', notes: '',
        result_references: [{ parent_job_id: 'job-1', invocation_id: 'inv-1' }],
        selected_residues: [{ auth_asym_id: 'A', auth_seq_id: '42', insertion_code: '' }],
        filters: { chain: 'A', mutation: 'W' },
        viewer_state: { active_metric_id: 'frustrampnn-native-index', landscape_offset: 0, metric_workbench_open: true, chart_x_axis: 'sequence_index', chart_y_axis: 'score', structure_camera: null, structure_representations: [], structure_layers: [] },
        tags: ['confirmed'], created_at: '2026-08-11T00:00:00',
    };
    assert.deepEqual(parseFrustraMpnnSavedReview(value), value);
    assert.throws(() => parseFrustraMpnnSavedReview({ ...value, viewer_state: [] }), /must be an object/);
    assert.throws(() => parseFrustraMpnnSavedReview({ ...value, viewer_state: { ...value.viewer_state, structure_camera: { mode: 'perspective', target: [0, 1] } } }), /target/);
    assert.throws(() => parseFrustraMpnnSavedReview({ ...value, viewer_state: { ...value.viewer_state, structure_representations: [{ representationId: 'r', documentId: 'primary', kind: 'invalid', visible: true, opacity: 1 }] } }), /kind/);
    assert.throws(() => parseFrustraMpnnSavedReview({ ...value, viewer_state: { ...value.viewer_state, structure_layers: [{ layerId: 'l', visible: true, opacity: 2, order: 0 }] } }), /opacity/);
    assert.throws(() => parseFrustraMpnnSavedReview({ ...value, unexpected: true }), /unknown or missing keys/);
});
