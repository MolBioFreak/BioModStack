"""Model-owned reader for experimental Foundry LigandMPNN context evidence.

The shared result owner registers this reader; it never classifies a binding verdict.
"""
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'bms.ligandmpnn.interface-context.experimental.v1'
METHOD = 'masked_whole_patch_sampling_with_binder_ablation'
CHECKPOINT_SHA256 = '161cd264061fda9680cbb940255522ae42f2966c552d045d87913d9452a80970'
FOUNDRY_VERSION = '0.1.9'
AA = set('ARNDCEQGHILKMFPSTWYV')


def _atoms(path):
    """Inspect the exact staged bytes, not a caller-supplied account of masking."""
    residues = {}
    for line in path.read_text().splitlines():
        if not line.startswith('ATOM  '):
            continue
        if len(line) < 66:
            raise ValueError('truncated staged PDB atom')
        key = f'{line[21]}{line[22:26].strip()}{line[26].strip()}'
        residues.setdefault(key, []).append((line[17:20], line[12:16].strip(), line[21]))
    return residues


def read_context_result(path, *, candidate_id, round_id, source_sha256):
    path = Path(path)
    result = json.loads(path.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'nonfinite JSON: {x}')))
    if result.get('schema') != SCHEMA or result.get('status') != 'completed_unclassified' or result.get('qualification') != 'unqualified':
        raise ValueError('not a completed, unclassified context result')
    if (result.get('candidate_id'), result.get('round_id'), result.get('source_sha256')) != (candidate_id, round_id, source_sha256):
        raise ValueError('candidate/round/artifact identity mismatch')
    if result.get('model_type') != 'ligand_mpnn' or result.get('method') != METHOD:
        raise ValueError('unrecognized native method')
    if (result.get('checkpoint_sha256') != CHECKPOINT_SHA256 or
            result.get('foundry_version') != FOUNDRY_VERSION):
        raise ValueError('unrecognized native checkpoint or Foundry version')
    patch = result['target_patch']
    reference = result['reference_patch']
    binder = result['fixed_binder_chain']
    target = result['target_chain']
    if (not isinstance(patch, list) or not patch or len(patch) > 32 or
            len(set(patch)) != len(patch) or set(reference) != set(patch) or
            any(not k.startswith(target) or reference[k] not in AA for k in patch) or
            binder == target):
        raise ValueError('invalid patch identity')
    if (type(result['samples']) is not int or not 1 <= result['samples'] <= 16 or
            type(result['seed']) is not int or not 0 <= result['seed'] <= 2**31 - 1 or
            type(result['temperature']) not in (float, int) or
            not math.isfinite(result['temperature']) or not 0.01 <= result['temperature'] <= 2):
        raise ValueError('invalid effective native settings')
    if set(result['conditions']) != {'supplied_complex', 'without_binder'}:
        raise ValueError('missing or unexpected comparator')
    structures = {}
    for label in ('supplied_complex', 'without_binder'):
        rows = result['conditions'][label]['samples']
        staged = path.parent / ('masked_complex.pdb' if label == 'supplied_complex' else 'masked_without_binder.pdb')
        if not staged.is_file() or hashlib.sha256(staged.read_bytes()).hexdigest() != result['conditions'][label]['masked_input_sha256']:
            raise ValueError('masked native input artifact mismatch')
        structures[label] = _atoms(staged)
        for key in patch:
            atoms = structures[label].get(key, [])
            if {a for _, a, _ in atoms} != {'N', 'CA', 'C', 'O'} or any(name != 'UNK' for name, _, _ in atoms):
                raise ValueError('held-out identity or side chain visible in staged input')
        if len(rows) != result['samples'] or {(r['batch_idx'], r['design_idx']) for r in rows} != {(0, i) for i in range(result['samples'])}:
            raise ValueError('native sample cardinality/identity mismatch')
        for row in rows:
            if (set(row['sampled_patch']) != set(patch) or row['patch_residue_count'] != len(patch) or
                    any(aa not in AA for aa in row['sampled_patch'].values())):
                raise ValueError('sampled patch mismatch')
            count = sum(row['sampled_patch'][k] == reference[k] for k in patch)
            if row['patch_exact_matches'] != count:
                raise ValueError('patch recovery count mismatch')
    full = structures['supplied_complex']
    ablated = structures['without_binder']
    if (not any(atoms[0][2] == binder for atoms in full.values()) or
            any(atoms[0][2] == binder for atoms in ablated.values()) or
            {k: v for k, v in full.items() if v[0][2] != binder} != ablated):
        raise ValueError('comparator differs beyond binder removal')
    return result
