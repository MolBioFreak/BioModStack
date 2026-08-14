import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import type { CmLandscapeRow, CmResults } from '../src/components/conformationalMapping/conformationalMappingApi.js';
import {
    CANONICAL_AMINO_ACIDS,
    candidateLabel,
    candidateStructureArtifact,
    candidateStructureMap,
    canonicalAnalysis,
    ensembleCandidates,
    groupExact20Landscape,
    requireApprovedCmResults,
} from '../src/components/conformationalMapping/conformationalMappingSemantics.js';

const sha = (letter: string) => letter.repeat(64);
const coordinate = { backend: 'protenix_v2_ensemble', target_id: 'target-a', ordered_seed: 101, sample_index: 0 };
const candidate = {
    candidate_id: 'candidate-a', backend_coordinates: coordinate,
    authoritative_structure_path: 'native/target-a/structure.cif', authoritative_structure_sha256: sha('b'),
    sidecar_paths: ['native/target-a/confidence.json', 'native/target-a/full-data.json'],
};
const ensemble = {
    schema_name: 'cm_ensemble', schema_version: 1, request_id: 'request-a', request_sha256: sha('a'),
    source_snapshot_sha256: sha('c'), backend: 'protenix_v2_ensemble', runtime_identity: 'runtime',
    container_digest: `sha256:${sha('d')}`, checkpoint_sha256: sha('e'), feature_policy_sha256: sha('f'),
    expected_cardinality: 1, expected_coordinates: [coordinate], candidates: [candidate],
    native_manifest_path: 'cm_native_artifacts_v1.json', native_manifest_sha256: sha('1'), warnings: [], omissions: [],
    terminal_status: 'complete', started_at: '2026-07-19T00:00:00Z', completed_at: '2026-07-19T00:01:00Z',
    resumable: true, resume_key: sha('2'),
};
const baseResults = (): CmResults => ({
    request_id: 'request-a', backend: 'protenix_v2_ensemble', status: 'completed',
    result_contract_id: 'conformational_mapping_protenix_v1',
    records: [{ type: 'ensemble', key: 'primary', sha256: sha('3'), payload: ensemble }],
    artifacts: [{ artifact_id: 'artifact-a', candidate_id: 'candidate-a', role: 'authoritative_cif',
        relative_path: 'native/target-a/structure.cif', sha256: sha('b'), bytes: 20, media_type: 'chemical/x-mmcif' }],
});

test('test_cm13_001_only_approved_contracts_render', () => {
    assert.equal(requireApprovedCmResults(baseResults()).request_id, 'request-a');
});

test('test_cm13_002_unknown_contract_fails_closed', () => {
    const results = baseResults();
    results.result_contract_id = 'conformational_mapping_future_v9';
    assert.throws(() => requireApprovedCmResults(results), /Unknown/);
});

test('test_cm13_003_candidate_order_and_identity', () => {
    assert.deepEqual(ensembleCandidates(baseResults()).map((item) => item.candidate_id), ['candidate-a']);
    const reordered = baseResults();
    (reordered.records[0].payload as typeof ensemble).expected_coordinates = [{ ...coordinate, sample_index: 1 }];
    assert.throws(() => ensembleCandidates(reordered), /order/);
});

test('external-import ensemble admits exactly zero candidate sidecars', () => {
    const results = baseResults();
    const payload = results.records[0].payload as typeof ensemble;
    const imported = {
        backend: 'external_import', target_id: 'target-a', staged_index: 0,
        source_content_sha256: sha('7'), staged_receipt_sha256: sha('8'),
    };
    results.backend = 'external_import';
    results.result_contract_id = 'conformational_mapping_import_v1';
    payload.backend = 'external_import';
    payload.expected_coordinates = [imported];
    payload.candidates[0] = {
        ...payload.candidates[0], backend_coordinates: imported, sidecar_paths: [],
    };
    assert.deepEqual(ensembleCandidates(results)[0].sidecar_paths, []);
    payload.candidates[0].sidecar_paths = ['global-import-receipt.json'];
    assert.throws(() => ensembleCandidates(results), /sidecar authority/);
});

test('test_cm13_004_mapping_overlay_uses_api_identity', () => {
    const results = baseResults();
    results.records.push({ type: 'structure_map', key: 'candidate-a', sha256: sha('4'), payload: {
        schema_name: 'cm_structure_map', schema_version: 1, target_id: 'target-a', candidate_id: 'candidate-a',
        original_cif_sha256: sha('5'), source_format: 'mmcif', source_sha256: sha('5'), source_bytes: 20,
        normalized_pdb_sha256: sha('6'), selected_source_model: 1, altloc_policy: 'highest_occupancy', normalizer_version: 'v1',
        rows: [{ entity_instance_id: 'copy1', source_entity_id: 'protein', source_model: 1, label_asym_id: 'A', auth_asym_id: 'AUTH',
            label_seq_id: 1, auth_seq_id: 7, insertion_code: '', residue_name: 'ALA', sequence_index: 1,
            pdb_chain_id: 'A', pdb_residue_id: 1, pdb_insertion_code: '', backbone_atoms: { N: '1', CA: '2', C: '3', O: '4' },
            selected_altloc: '', model_decision: 'selected_model_1', status: 'mapped', reason: null }],
    } });
    assert.equal(candidateStructureMap(results, 'candidate-a').rows[0].auth_asym_id, 'AUTH');
    assert.equal(candidateStructureArtifact(candidate, results.artifacts).artifact_id, 'artifact-a');
});

test('test_cm13_005_independent_hypothesis_label', () => {
    assert.equal(candidateLabel(candidate), 'target-a · seed 101 · sample 1');
    const semantics = readFileSync(resolve(process.cwd(), 'src/components/conformationalMapping/conformationalMappingSemantics.ts'), 'utf8');
    assert.match(semantics, /Independent structural hypotheses/);
});

test('test_cm13_006_no_trajectory_or_thermodynamic_copy', () => {
    const root = resolve(process.cwd(), 'src/components/conformationalMapping');
    const copy = ['ConformationalMappingViewer.tsx', 'ConformationalMappingLauncher.tsx', 'conformationalMappingSemantics.ts']
        .map((name) => readFileSync(resolve(root, name), 'utf8')).join('\n');
    assert.doesNotMatch(copy, /trajectory|equilibrium population|free energy|ΔΔG|beneficial mutation/i);
});

test('test_cm13_007_missing_analysis_is_explicit', () => {
    assert.throws(() => canonicalAnalysis(baseResults()), /explicitly unavailable/);
});

test('test_cm13_008_legacy_viewer_no_regression', () => {
    const resultsViewer = readFileSync(resolve(process.cwd(), 'src/components/ResultsViewer.tsx'), 'utf8');
    assert.match(resultsViewer, /ConformationalMappingViewer/);
    assert.match(resultsViewer, /StructureViewerPane/);
});

test('canonical landscape display requires exact 20 API slots', () => {
    const rows = CANONICAL_AMINO_ACIDS.map((mutation_aa, index): CmLandscapeRow => ({
        candidate_id: 'candidate-a', entity_instance_id: 'copy1', auth_asym_id: 'AUTH', auth_seq_id: '7', insertion_code: '',
        source_entity_id: '1', label_asym_id: 'A', label_seq_id: 1,
        sequence_index: 1, wt: 'A', pdb_chain_id: 'A', pdb_residue_id: 7, pdb_insertion_code: '',
        model_position: 0, residue_name: 'ALA', mutation_aa, score: index, class: 'neutral', scoreable: true, status: 'ok', reason: null,
        provenance: {},
    }));
    assert.equal(groupExact20Landscape(rows).length, 1);
    assert.throws(() => groupExact20Landscape(rows.slice(1)), /exact-20/);
});
