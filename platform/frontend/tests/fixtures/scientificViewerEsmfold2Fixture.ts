import type { StructureDocumentRef } from '../../src/structureViewer/contracts/structureIdentity';

// Decoded fixture pinned to native sibling 90331ebb:
// scientific_viewer_contract.py, esmfold2_scientific_consumer.py and
// test_esmfold2_native_confidence.py (three collapsed CIF residues, five tokens).
// Inert transport data, not an inference or live API receipt.
export function esmfold2Fixture(candidateId = 'candidate', sample = 0) {
    const document: StructureDocumentRef = { documentId: 'primary', candidateId, contentSha256: (sample ? 'b' : 'a').repeat(64), sourceKind: 'mmcif' };
    const producer_version = 'Biohub/esm@c94ed8d763bbd7088b296949e5b401e8ea12073a;Biohub/transformers@3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf';
    const producer_binding = { candidate_id: `fixture_00${sample}`, document_id: 'structure' };
    const binding = { ...producer_binding, source_sha256: document.contentSha256 };
    const base = { schema_name: 'core_protein_viewer_metric', schema_version: 1, contract_revision: 1, design_id: candidateId, design_name: `fixture_00${sample}`, status: 'ok', reason: null, document, producer_binding };
    const residues = ['A', 'B', 'L'].map((chain_id, index) => ({ index, chain_id, residue_name: index === 2 ? 'LIG' : 'ALA', insertion_code: index === 0 ? 'A' : '', selected_model: 1, selected_altloc: '', auth_asym_id: chain_id, auth_seq_id: index === 2 ? 1 : 42, label_asym_id: ['X','Y','Z'][index], label_seq_id: [7,1,null][index], source_entity_id: index === 2 ? '2' : '1', entity_instance_id: null }));
    const confidence = { ...base, artifact_sha256: document.contentSha256, metric: 'residue_plddt', units: 'fraction', values: [.005,.8,.4], native_positions: [0,1,2], axis: { ...binding, residues, producer_version, confidence_scope: 'collapsed_residue', stored_units: 'percent' } };
    const tokenAxis = { ...binding, axis_kind: 'model_token', producer_version, mapping_reason: 'native_token_to_structure_mapping_unavailable', orientation: 'native_output_order', tokens: Array.from({ length: 5 }, (_, index) => ({ index, residue_index: 0, entity_id: index < 2 ? 1 : 2 })) };
    const pae = { ...base, artifact_sha256: 'c'.repeat(64), metric: 'pae', row_axis: structuredClone(tokenAxis), column_axis: structuredClone(tokenAxis), native_shape: [5,5], native_row_positions: [0,1,2,3,4], native_column_positions: [0,1,2,3,4], sampled_row_indices: [0,1,2,3,4], sampled_column_indices: [0,1,2,3,4], pae_matrix: Array.from({ length: 5 }, (_, r) => Array.from({ length: 5 }, (_, c) => (r * 5 + c) / 2 + sample)), size: 5 };
    return { document, confidence, pae };
}
