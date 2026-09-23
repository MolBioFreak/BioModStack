"""Model-owned reader for experimental Foundry LigandMPNN context evidence.

The shared result owner registers this reader; it never classifies a binding verdict.
"""
import json
import hashlib
from pathlib import Path

SCHEMA = 'bms.ligandmpnn.interface-context.experimental.v1'


def read_context_result(path, *, candidate_id, round_id, source_sha256):
    path = Path(path)
    result = json.loads(path.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'nonfinite JSON: {x}')))
    if result.get('schema') != SCHEMA or result.get('status') != 'completed_unclassified' or result.get('qualification') != 'unqualified':
        raise ValueError('not a completed, unclassified context result')
    if (result.get('candidate_id'), result.get('round_id'), result.get('source_sha256')) != (candidate_id, round_id, source_sha256):
        raise ValueError('candidate/round/artifact identity mismatch')
    if result.get('model_type') != 'ligand_mpnn' or result.get('method') != 'masked_whole_patch_sampling_with_binder_ablation':
        raise ValueError('unrecognized native method')
    patch = result['target_patch']
    if not patch or len(set(patch)) != len(patch) or set(result['reference_patch']) != set(patch):
        raise ValueError('invalid patch identity')
    for label in ('supplied_complex', 'without_binder'):
        rows = result['conditions'][label]['samples']
        staged = path.parent / ('masked_complex.pdb' if label == 'supplied_complex' else 'masked_without_binder.pdb')
        if not staged.is_file() or hashlib.sha256(staged.read_bytes()).hexdigest() != result['conditions'][label]['masked_input_sha256']:
            raise ValueError('masked native input artifact mismatch')
        if len(rows) != result['samples'] or {(r['batch_idx'], r['design_idx']) for r in rows} != {(0, i) for i in range(result['samples'])}:
            raise ValueError('native sample cardinality/identity mismatch')
        for row in rows:
            if set(row['sampled_patch']) != set(patch) or row['patch_residue_count'] != len(patch):
                raise ValueError('sampled patch mismatch')
            count = sum(row['sampled_patch'][k] == result['reference_patch'][k] for k in patch)
            if row['patch_exact_matches'] != count:
                raise ValueError('patch recovery count mismatch')
    return result
