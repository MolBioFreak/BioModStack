import assert from 'node:assert/strict';
import test from 'node:test';

const sha = (letter: string) => letter.repeat(64);

const summary = () => ({
    request_id: 'request-a',
    analysis_id: `cm_state_landscape_analysis_${'a'.repeat(32)}`,
    authority: {
        content_sha256: sha('a'), source_ensemble_sha256: sha('b'), source_landscape_sha256: sha('c'),
        source_structure_map_sha256: sha('d'), comparison_sha256: sha('e'), formula_version: 'cm_state_landscape_analysis_v1',
        formula_sha256: sha('f'), policy_sha256: sha('1'),
    },
    comparison: { mode: 'pairwise', target_id: 'target-a', scope: 'all_within_target', reference_backend_coordinates: null, reference_candidate_id: null },
    counts: { pairs: 2, rows: 2, exclusions: 0 },
    pairs: [
        { pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b' },
        { pair_id: 'candidate-a__candidate-c', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-c' },
    ],
    artifact: { artifact_id: 'artifact-a', content_sha256: sha('a'), size_bytes: 123, media_type: 'application/json', download_url: '/api/conformational-mapping/requests/request-a/artifacts/artifact-a' },
});

const numeric = () => ({ a: 1, b: 2, delta_b_minus_a: 1, status: 'ok', reason: null });
const unavailable = () => ({ a: null, b: null, delta_b_minus_a: null, status: 'unavailable', reason: 'missing_slot' });
const row = (pairId = 'candidate-a__candidate-b') => ({
    pair_id: pairId,
    candidate_a_id: 'candidate-a',
    candidate_b_id: pairId.endsWith('candidate-b') ? 'candidate-b' : 'candidate-c',
    identity: { target_id: 'target-a', entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: 42, insertion_code: 'B', sequence_index: 7, validated_wt: 'G' },
    metrics: {
        native_score: numeric(), high_non_native_highly_frustrated_fraction: unavailable(),
        maximum_non_native_substitution_delta_relative_to_native: numeric(),
        native_class: { a: 'neutral', b: 'high', transition: 'neutral_to_high', status: 'ok', reason: null },
    },
    availability: {
        native_score: { status: 'ok', reason: null }, high_non_native_highly_frustrated_fraction: { status: 'unavailable', reason: 'missing_slot' },
        maximum_non_native_substitution_delta_relative_to_native: { status: 'ok', reason: null }, native_class: { status: 'ok', reason: null },
    },
});

const workspaceModule = async (): Promise<Record<string, unknown> | null> => {
    try { return await import('../src/components/conformationalMapping/stateLandscapeWorkspace.js') as Record<string, unknown>; } catch { return null; }
};

test('workspace accepts an authoritative B2 summary in persisted pair order and binds a selected-pair page', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const validateSummary = module!.validateStateLandscapeWorkspaceSummary as ((value: unknown, authority: unknown) => unknown) | undefined;
    const validateRows = module!.validateStateLandscapeWorkspaceRowsPage as ((value: unknown, summaryValue: unknown, pairId: string, offset: number) => unknown) | undefined;
    assert.equal(typeof validateSummary, 'function');
    assert.equal(typeof validateRows, 'function');

    const accepted = validateSummary!(summary(), null) as { pairs: Array<{ pair_id: string }> };
    assert.deepEqual(accepted.pairs.map((pair) => pair.pair_id), ['candidate-a__candidate-b', 'candidate-a__candidate-c']);
    const page = validateRows!({
        request_id: 'request-a', selected_analysis_id: summary().analysis_id, offset: 0, limit: 50,
        applied_filters: { pair_id: 'candidate-a__candidate-b', candidate_id: null, entity_instance_id: null, auth_asym_id: null, sequence_start: null, sequence_end: null },
        next_offset: null, rows: [row()],
    }, accepted, 'candidate-a__candidate-b', 0) as { rows: unknown[] };
    assert.equal(page.rows.length, 1);
});

test('workspace selection resets stale row and page on pair change while candidate inspection preserves pair identity', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const initial = module!.initialStateLandscapeWorkspaceState as ((summaryValue: unknown) => unknown) | undefined;
    const selectPair = module!.selectStateLandscapeWorkspacePair as ((state: unknown, pairId: string) => unknown) | undefined;
    const selectRow = module!.selectStateLandscapeWorkspaceRow as ((state: unknown, rowKey: string) => unknown) | undefined;
    const inspectCandidate = module!.inspectStateLandscapeWorkspaceCandidate as ((state: unknown) => unknown) | undefined;
    assert.equal(typeof initial, 'function');
    assert.equal(typeof selectPair, 'function');
    assert.equal(typeof selectRow, 'function');
    assert.equal(typeof inspectCandidate, 'function');

    const selected = selectRow!(initial!(summary()), 'exact-row-key') as { selectedPairId: string; selectedStateRowKey: string | null; pageOffset: number };
    const inspected = inspectCandidate!(selected) as typeof selected;
    assert.deepEqual(inspected, selected, 'candidate A/B inspect must not change pair or residue selection');
    const changed = selectPair!(selected, 'candidate-a__candidate-c') as typeof selected;
    assert.deepEqual(changed, { selectedPairId: 'candidate-a__candidate-c', selectedStateRowKey: null, pageOffset: 0 });
});

test('workspace hides its lens and does not fetch a B2 projection without a canonical authority', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const enabled = module!.stateLandscapeSummaryEnabled as ((authority: unknown) => boolean) | undefined;
    const tabs = module!.stateLandscapeWorkspaceTabs as ((available: boolean) => string[]) | undefined;
    assert.equal(typeof enabled, 'function');
    assert.equal(typeof tabs, 'function');
    assert.equal(enabled!(null), false);
    assert.deepEqual(tabs!(false), ['ensemble', 'mapping', 'landscape', 'analysis', 'evidence', 'downloads']);
    assert.equal(enabled!({ analysis_id: summary().analysis_id }), true);
    assert.deepEqual(tabs!(true), ['ensemble', 'mapping', 'landscape', 'state-analysis', 'analysis', 'evidence', 'downloads']);
});

test('workspace rejects a malformed summary and a row page whose next offset skips persisted rows', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const validateSummary = module!.validateStateLandscapeWorkspaceSummary as ((value: unknown, authority: unknown) => unknown);
    const validateRows = module!.validateStateLandscapeWorkspaceRowsPage as (value: unknown, summaryValue: unknown, pairId: string, offset: number) => unknown;
    assert.throws(() => validateSummary({ ...summary(), pairs: [] }, null), /pair/i);
    const accepted = validateSummary(summary(), null);
    assert.throws(() => validateRows({
        request_id: 'request-a', selected_analysis_id: summary().analysis_id, offset: 0, limit: 50,
        applied_filters: { pair_id: 'candidate-a__candidate-b', candidate_id: null, entity_instance_id: null, auth_asym_id: null, sequence_start: null, sequence_end: null },
        next_offset: 2, rows: [row()],
    }, accepted, 'candidate-a__candidate-b', 0), /offset|bounds/i);
});

test('workspace selects a 3D residue only from one exact persisted identity mapping', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const resolveResidue = module!.resolveStateLandscapeResidueRef as ((identity: unknown, structureMap: unknown) => unknown) | undefined;
    assert.equal(typeof resolveResidue, 'function');
    const identity = row().identity;
    const mapRow = {
        status: 'mapped', entity_instance_id: identity.entity_instance_id, source_entity_id: 'source-entity', label_asym_id: 'L',
        auth_asym_id: identity.auth_asym_id, label_seq_id: 12, auth_seq_id: identity.auth_seq_id,
        insertion_code: identity.insertion_code, sequence_index: identity.sequence_index,
    };
    const structureMap = { target_id: identity.target_id, rows: [mapRow] };
    assert.deepEqual(resolveResidue!(identity, structureMap), {
        documentId: 'primary', entityId: 'source-entity', sourceEntityId: 'source-entity', sourceInstanceId: 'entity-1',
        labelAsymId: 'L', authAsymId: 'A', labelSeqId: 12, authSeqId: 42, insertionCode: 'B',
    });
    assert.equal(resolveResidue!(identity, { ...structureMap, rows: [mapRow, { ...mapRow }] }), null);
    assert.equal(resolveResidue!(identity, { ...structureMap, target_id: 'other-target' }), null);
});

test('workspace rejects malformed B2 rows and renders unavailable metrics with their reason instead of a fabricated zero', async () => {
    const module = await workspaceModule();
    assert.ok(module, 'C2 workspace module must exist');
    const validateSummary = module!.validateStateLandscapeWorkspaceSummary as ((value: unknown, authority: unknown) => unknown);
    const validateRows = module!.validateStateLandscapeWorkspaceRowsPage as (value: unknown, summaryValue: unknown, pairId: string, offset: number) => unknown;
    const display = module!.stateLandscapeMetricText as ((metric: unknown) => string) | undefined;
    assert.equal(typeof display, 'function');
    const accepted = validateSummary(summary(), null);
    assert.equal(display!(row().metrics.high_non_native_highly_frustrated_fraction), 'Unavailable: missing_slot');
    const malformed = row();
    malformed.metrics.high_non_native_highly_frustrated_fraction = { a: 0, b: 0, delta_b_minus_a: 0, status: 'unavailable', reason: 'missing_slot' };
    assert.throws(() => validateRows({
        request_id: 'request-a', selected_analysis_id: summary().analysis_id, offset: 0, limit: 50,
        applied_filters: { pair_id: 'candidate-a__candidate-b', candidate_id: null, entity_instance_id: null, auth_asym_id: null, sequence_start: null, sequence_end: null },
        next_offset: null, rows: [malformed],
    }, accepted, 'candidate-a__candidate-b', 0), /unavailable|metric/i);
});
