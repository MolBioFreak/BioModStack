import assert from 'node:assert/strict';
import test from 'node:test';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { stateLandscapeRowKey, validateStateLandscapeWorkspaceRowsPage, validateStateLandscapeWorkspaceSummary } from '../src/components/conformationalMapping/stateLandscapeWorkspace.js';

const sha = (letter: string) => letter.repeat(64);
const summary = validateStateLandscapeWorkspaceSummary({
    request_id: 'request-a', analysis_id: `cm_state_landscape_analysis_${'a'.repeat(32)}`,
    authority: { content_sha256: sha('a'), source_ensemble_sha256: sha('b'), source_landscape_sha256: sha('c'), source_structure_map_sha256: sha('d'), comparison_sha256: sha('e'), formula_version: 'cm_state_landscape_analysis_v1', formula_sha256: sha('f'), policy_sha256: sha('1') },
    comparison: { mode: 'pairwise', target_id: 'target-a', scope: 'all_within_target', reference_backend_coordinates: null, reference_candidate_id: null },
    counts: { pairs: 1, rows: 1, exclusions: 0 }, pairs: [{ pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b' }], artifact: null,
}, null);
const page = validateStateLandscapeWorkspaceRowsPage({
    request_id: 'request-a', selected_analysis_id: summary.analysis_id, offset: 0, limit: 50,
    applied_filters: { pair_id: 'candidate-a__candidate-b', candidate_id: null, entity_instance_id: null, auth_asym_id: null, sequence_start: null, sequence_end: null }, next_offset: null,
    rows: [{ pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b', identity: { target_id: 'target-a', entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: 42, insertion_code: '', sequence_index: 7, validated_wt: 'G' }, metrics: { native_score: { a: 1, b: 2, delta_b_minus_a: 1, status: 'ok', reason: null }, high_non_native_highly_frustrated_fraction: { a: null, b: null, delta_b_minus_a: null, status: 'unavailable', reason: 'missing_slot' }, maximum_non_native_substitution_delta_relative_to_native: { a: 1, b: 2, delta_b_minus_a: 1, status: 'ok', reason: null }, native_class: { a: 'neutral', b: 'high', transition: 'neutral_to_high', status: 'ok', reason: null } }, availability: { native_score: { status: 'ok', reason: null }, high_non_native_highly_frustrated_fraction: { status: 'unavailable', reason: 'missing_slot' }, maximum_non_native_substitution_delta_relative_to_native: { status: 'ok', reason: null }, native_class: { status: 'ok', reason: null } } }],
}, summary, 'candidate-a__candidate-b', 0);

test('state-analysis panel renders pair rail, exact residue inspector, unavailable reason, and candidate inspect controls', async () => {
    const panelModule = await import('../src/components/conformationalMapping/StateLandscapeWorkspacePanel.js').catch(() => null);
    assert.ok(panelModule, 'C2 state-analysis panel must exist');
    const Panel = panelModule!.StateLandscapeWorkspacePanel;
    assert.equal(typeof Panel, 'function');
    const html = renderToStaticMarkup(React.createElement(Panel as React.ComponentType<Record<string, unknown>>, {
        summary, page, selectedPairId: 'candidate-a__candidate-b', selectedStateRowKey: stateLandscapeRowKey(page.rows[0]!), selectedMetric: 'native_score', inspectorMinimized: false,
        onSelectPair: () => undefined, onSelectRow: () => undefined, onInspectCandidate: () => undefined, onSelectMetric: () => undefined, onToggleInspector: () => undefined, onLoadMore: () => undefined,
    }));
    for (const text of ['State-analysis pairs', 'A: candidate-a', 'Inspect A', 'A:42', 'Unavailable: missing_slot', 'Docked residue inspector']) assert.match(html, new RegExp(text));
});

test('state-analysis renders a fail-closed availability alert for malformed and failed B2 summaries', async () => {
    const panelModule = await import('../src/components/conformationalMapping/StateLandscapeWorkspacePanel.js').catch(() => null);
    assert.ok(panelModule, 'C2 state-analysis panel must exist');
    const Alert = panelModule!.StateLandscapeStatusAlert as React.ComponentType<{ error: string | null | undefined }> | undefined;
    assert.equal(typeof Alert, 'function');

    for (const error of ['State-analysis B2 projection does not bind canonical authority', 'State-analysis summary request failed with HTTP 503']) {
        const html = renderToStaticMarkup(React.createElement(Alert!, { error }));
        assert.match(html, /role="alert"/);
        assert.match(html, /State analysis is unavailable/);
        assert.match(html, new RegExp(error));
    }
});
