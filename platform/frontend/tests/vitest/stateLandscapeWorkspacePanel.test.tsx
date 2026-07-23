import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StateLandscapeWorkspacePanel } from '../../src/components/conformationalMapping/StateLandscapeWorkspacePanel';
import { validateStateLandscapeWorkspaceRowsPage, validateStateLandscapeWorkspaceSummary } from '../../src/components/conformationalMapping/stateLandscapeWorkspace';

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

afterEach(() => { document.body.replaceChildren(); });

describe('StateLandscapeWorkspacePanel keyboard selection', () => {
    it('selects the exact state row with Enter and Space on its identity button', async () => {
        const onSelectRow = vi.fn();
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(<StateLandscapeWorkspacePanel
                summary={summary} page={page} selectedPairId="candidate-a__candidate-b" selectedStateRowKey={null} selectedMetric="native_score" inspectorMinimized={false}
                onSelectPair={() => undefined} onSelectRow={onSelectRow} onInspectCandidate={() => undefined} onSelectMetric={() => undefined} onToggleInspector={() => undefined} onLoadMore={() => undefined}
            />);
        });

        const rowButton = container.querySelector<HTMLButtonElement>('button[aria-label^="Select state-analysis residue"]');
        expect(rowButton).toBeTruthy();
        await act(async () => {
            rowButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            rowButton?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
        });
        expect(onSelectRow).toHaveBeenCalledTimes(2);
        expect(onSelectRow).toHaveBeenNthCalledWith(1, page.rows[0]);
        expect(onSelectRow).toHaveBeenNthCalledWith(2, page.rows[0]);

        await act(async () => { root.unmount(); });
    });
});
