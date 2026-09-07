#!/usr/bin/env python3
"""Extract FA-MPNN sequence-probability confidence metrics from sample_pkls.

Upstream FA-MPNN seq_design.py writes sample PKLs containing seq_probs and pred_aatype.
BioModStack uses this script to report sequence confidence separately from pSCE sidechain QC.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pickle
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover - exercised only in minimal runtime images
    np = None  # type: ignore[assignment]


AA_ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
EPS = 1e-12


def _to_array(value: Any) -> Any:
    if np is None:
        return value
    return np.asarray(value)


def _as_bool_mask(value: Any, length: int) -> List[bool]:
    if value is None:
        return [True] * length
    arr = _to_array(value)
    try:
        flat = arr.reshape(-1).tolist()
    except Exception:
        flat = list(value) if isinstance(value, Iterable) else []
    mask = [bool(item) for item in flat[:length]]
    if len(mask) < length:
        mask.extend([True] * (length - len(mask)))
    return mask


def _as_int_list(value: Any, length: int, default_start: int = 0) -> List[int]:
    if value is None:
        return list(range(default_start, default_start + length))
    arr = _to_array(value)
    try:
        flat = arr.reshape(-1).tolist()
    except Exception:
        flat = list(value) if isinstance(value, Iterable) else []
    out: List[int] = []
    for item in flat[:length]:
        try:
            out.append(int(item))
        except Exception:
            out.append(default_start + len(out))
    if len(out) < length:
        out.extend(range(default_start + len(out), default_start + length))
    return out


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _entropy(probabilities: List[float]) -> float:
    total = sum(max(float(p), 0.0) for p in probabilities)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for prob in probabilities:
        p = max(float(prob), 0.0) / total
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


def _mutation_delta_candidates(
    *,
    normalized_row: List[float],
    sampled_aa_index: int,
    chain_index: int,
    residue_index: int,
    min_log_odds_delta: float,
) -> List[Dict[str, Any]]:
    if sampled_aa_index < 0 or sampled_aa_index >= min(len(normalized_row), len(AA_ALPHABET)):
        return []
    from_prob = max(float(normalized_row[sampled_aa_index]), EPS)
    from_aa = AA_ALPHABET[sampled_aa_index]
    candidates: List[Dict[str, Any]] = []
    for to_aa_index, to_aa in enumerate(AA_ALPHABET):
        if to_aa_index == sampled_aa_index or to_aa_index >= len(normalized_row):
            continue
        to_prob = max(float(normalized_row[to_aa_index]), EPS)
        log_odds_delta = math.log(to_prob) - math.log(from_prob)
        if log_odds_delta < min_log_odds_delta:
            continue
        candidates.append(
            {
                "chain_index": chain_index,
                "residue_index": residue_index,
                "from_aa": from_aa,
                "to_aa": to_aa,
                "mutation": f"{from_aa}{residue_index}{to_aa}",
                "from_prob": _round(from_prob),
                "to_prob": _round(to_prob),
                "log_odds_delta": _round(log_odds_delta),
            }
        )
    candidates.sort(key=lambda row: float(row.get("log_odds_delta") or 0.0), reverse=True)
    return candidates


# Explicit producer contract; channel width is validation, never dialect detection.
STRICT_DIALECT = 'fampnn-18363df253dbeb7b2cb963daf7a732fbaa25157d'
STRICT_ALPHABET = 'ARNDCQEGHILKMFPSTWYVX'
WORKFLOW_DECLARATIONS = {
    'protein_design': {'binder_role_residues', 'declared_protein_inputs'},
    'antibody_denovo': {'authorized_sequence_design_region'},
    'protein_local_redesign': {'sequence_redesign_positions_spec'},
}


def _source_identities(data: bytes) -> Dict[tuple, str]:
    """Conservative ATOM-only subset of pinned protein.read_pdb.

    protein.py:117-192 enumerates chains in encounter order and adds a running
    insertion-code offset to residue numbers. It does NOT use alphabet letters.
    Unknown/noncanonical HETATM and disordered residue variants are blocked,
    rather than guessing the producer's ncaa/alternate-residue interpretation.
    """

    atoms = set('N CA C CB O CG CG1 CG2 OG OG1 SG CD CD1 CD2 ND1 ND2 OD1 OD2 SD CE CE1 CE2 CE3 NE NE1 NE2 OE1 OE2 CH2 NH1 NH2 OH CZ CZ2 CZ3 NZ OXT'.split())
    chains: Dict[str, dict] = {}
    models = 0
    for line in data.decode('utf-8').splitlines():
        if line.startswith('MODEL '):
            models += 1
            if models > 1:
                raise ValueError('source identity: multiple models unsupported')
        if line.startswith('HETATM'):
            raise ValueError('source identity: HETATM requires full pinned parser mapping')
        if not line.startswith('ATOM  '):
            continue
        if len(line) < 54:
            raise ValueError('source identity: malformed ATOM record')
        chain = line[21]
        number = int(line[22:26])
        insertion = line[26].strip()
        if chain == ':' or insertion == ':':
            raise ValueError('source identity: delimiter characters unsupported')
        residues = chains.setdefault(chain, {})
        residue = residues.setdefault((number, insertion), {'name': line[17:20], 'known': False})
        if residue['name'] != line[17:20]:
            raise ValueError('source identity: disordered residue unsupported')
        residue['known'] |= line[12:16].strip() in atoms
    mapping = {}
    for chain_index, (chain, residues) in enumerate(chains.items()):
        offset = 0
        for (number, insertion), residue in residues.items():
            offset += bool(insertion)
            if not residue['known']:
                continue
            key = (chain_index, number + offset)
            if key in mapping:
                raise ValueError('source identity: ambiguous producer residue index')
            mapping[key] = f'{chain}:{number}:{insertion}'
    if not mapping:
        raise ValueError('source identity: no supported residues')
    return mapping


def _strict_array(payload: dict, key: str, shape: tuple, *, integer=False, mask=False):
    if key not in payload:
        raise ValueError(f'missing required producer array: {key}')
    arr = np.asarray(payload[key])
    if arr.shape != shape or arr.dtype.kind not in ('biuf' if mask else 'iuf'):
        raise ValueError(f'{key}: expected numeric shape {shape}, got {arr.shape}')
    if not np.isfinite(arr).all():
        raise ValueError(f'{key}: nonfinite values')
    if integer and not np.equal(arr, np.floor(arr)).all():
        raise ValueError(f'{key}: integer values required')
    if mask and not np.isin(arr, [0, 1]).all():
        raise ValueError(f'{key}: binary mask required')
    return arr


def _policy_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'analysis policy duplicate JSON key: {key}')
        result[key] = value
    return result


def _validate_policy(policy):
    """Complete closed v1 structural schema, independent of sample membership.

    Kept stdlib-only for the model image; semantic source membership is resolved
    separately. All entries (including unused inputs) must be safe to republish.
    """
    keys = {'schema_version', 'owner', 'version', 'declaration', 'dialect',
            'require_full_coverage', 'allow_summary_override', 'inputs'}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError('analysis policy required with exact versioned fields')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1 or type(policy['version']) is not int or policy['version'] != 1:
        raise ValueError('unsupported analysis policy version')
    if policy['dialect'] != STRICT_DIALECT:
        raise ValueError('unsupported producer dialect')
    if not isinstance(policy['owner'], str) or not isinstance(policy['declaration'], str) or policy['owner'] not in WORKFLOW_DECLARATIONS or policy['declaration'] not in WORKFLOW_DECLARATIONS[policy['owner']]:
        raise ValueError('missing/conflicting workflow policy authority; children must inherit parent')
    for key in ('require_full_coverage', 'allow_summary_override'):
        if type(policy[key]) is not bool:
            raise ValueError(f'policy {key}: boolean required')
    if not isinstance(policy['inputs'], dict) or not policy['inputs']:
        raise ValueError('analysis policy inputs: nonempty object required')
    for name, entry in policy['inputs'].items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('policy input name must be a string')
        if not isinstance(entry, dict) or set(entry) != {'input_domain', 'sequence_design', 'summary', 'summary_override', 'mutation_override', 'artifact_binding'}:
            raise ValueError('policy input must declare exact scopes, overrides and artifact binding')
        binding = entry['artifact_binding']
        if not isinstance(binding, dict) or set(binding) != {'producer_input_id', 'source_pdb_sha256', 'producer_candidate_ids'}:
            raise ValueError('policy artifact binding: exact fields required')
        if binding['producer_input_id'] != name:
            raise ValueError('policy artifact binding: foreign producer input identity')
        digest = binding['source_pdb_sha256']
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('policy source binding: SHA-256 required')
        candidates = binding['producer_candidate_ids']
        if not isinstance(candidates, list) or not candidates or any(not isinstance(c, str) or not re.fullmatch(re.escape(name) + r'_sample(?:0|[1-9][0-9]*)', c) for c in candidates) or len(candidates) != len(set(candidates)):
            raise ValueError('policy candidate binding: unique native producer candidate identities required')
        for key, value in entry.items():
            if key == 'artifact_binding':
                continue
            if key.endswith('_override') and value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value) or len(value) != len(set(value)):
                raise ValueError(f'policy {name}.{key}: unique residue identities required')


def _resolve_policy(policy: dict, input_name: str, identities: List[str]):
    _validate_policy(policy)
    if input_name not in policy['inputs']:
        raise ValueError(f'policy missing input authority: {input_name}')
    entry = policy['inputs'][input_name]

    def scope(key, domain):
        value = entry[key]
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value) or len(value) != len(set(value)):
            raise ValueError(f'policy {key}: unique residue identities required')
        if not set(value) <= set(domain):
            raise ValueError(f'policy {key}: outside authorized identity domain')
        return set(value)

    domain = scope('input_domain', identities)
    authorized = scope('sequence_design', domain)
    summary = scope('summary', domain)
    if policy['declaration'] in {'authorized_sequence_design_region', 'sequence_redesign_positions_spec'} and summary != authorized:
        raise ValueError('policy declaration conflicts with summary scope')
    if policy['declaration'] == 'declared_protein_inputs' and summary != domain:
        raise ValueError('policy declaration conflicts with declared protein inputs')
    if entry['summary_override'] is not None:
        if not policy['allow_summary_override']:
            raise ValueError('summary override forbidden by workflow policy')
        summary = scope('summary_override', domain)
    mutations = authorized
    if entry['mutation_override'] is not None:
        mutations = scope('mutation_override', authorized)
    return summary, authorized, mutations


def _analyze_strict(path, payload, policy, source_pdb_dir, mutation_top_n, mutation_min_log_odds_delta,
                    sample_bytes, policy_bytes, candidate_pdb_dir):
    if np is None:
        raise ValueError('strict analysis requires numpy')
    if not isinstance(policy, dict):
        raise ValueError('marked analysis requires workflow-owned analysis policy')
    if not isinstance(payload, dict) or 'seq_probs' not in payload:
        raise ValueError('strict producer dictionary with seq_probs required')
    probs = np.asarray(payload['seq_probs'])
    if probs.ndim != 2 or probs.shape[1] != len(STRICT_ALPHABET):
        raise ValueError('seq_probs: pinned dialect requires exact [N,21] shape')
    n = len(probs)
    probs = _strict_array(payload, 'seq_probs', (n, 21))
    if ((probs < 0) | (probs > 1)).any():
        raise ValueError('seq_probs: probabilities must be in [0,1]')
    pred = _strict_array(payload, 'pred_aatype', (n,), integer=True)
    if ((pred < 0) | (pred >= 21)).any():
        raise ValueError('pred_aatype: token outside pinned vocabulary')
    present = _strict_array(payload, 'seq_mask', (n,), mask=True).astype(bool)
    fixed = _strict_array(payload, 'aatype_override_mask', (n,), mask=True).astype(bool)
    chain = _strict_array(payload, 'chain_index', (n,), integer=True)
    residue = _strict_array(payload, 'residue_index', (n,), integer=True)
    # Validate other producer token/mask fields when present, without pretending
    # they are evidence of sequence presence or sequence-design authorization.
    for key in ('interface_residue_mask', 'scn_override_mask'):
        if key in payload:
            _strict_array(payload, key, (n,), mask=True)
    if 'original_aatype' in payload:
        original = _strict_array(payload, 'original_aatype', (n,), integer=True)
        if ((original < 0) | (original >= 21)).any():
            raise ValueError('original_aatype: invalid token')
    if source_pdb_dir is None or '_sample' not in path.stem:
        raise ValueError('source PDB identity unavailable')
    input_name, sample_index = path.stem.rsplit('_sample', 1)
    if not sample_index.isdigit():
        raise ValueError('source identity: invalid producer sample name')
    source = Path(source_pdb_dir) / f'{input_name}.pdb'
    if input_name not in policy['inputs']:
        raise ValueError('policy source binding: missing producer input authority')
    authority = policy['inputs'][input_name]['artifact_binding']
    if path.stem not in authority['producer_candidate_ids']:
        raise ValueError('policy candidate binding: foreign candidate')
    source_bytes = _capture_bytes(source, 'source PDB')
    if hashlib.sha256(source_bytes).hexdigest() != authority['source_pdb_sha256']:
        raise ValueError('source PDB binding: bytes differ from workflow authority')
    mapping = _source_identities(source_bytes)
    candidate = Path(candidate_pdb_dir or (path.parent / 'samples')) / f'{path.stem}.pdb'
    # Bind the native candidate artifact as bytes, not as a second source-author
    # namespace: native serialization may renumber chains/insertion positions.
    # This digest is observed output evidence, not structural validation.
    candidate_bytes = _capture_bytes(candidate, 'candidate PDB')
    keys = list(zip(chain.astype(int).tolist(), residue.astype(int).tolist()))
    if len(set(keys)) != n or any(k not in mapping for k in keys):
        raise ValueError('source identity: duplicate or unmapped chain/residue index')
    identities = [mapping[k] for k in keys]

    summary, authorized, mutations = _resolve_policy(policy, input_name, list(mapping.values()))
    # The pinned writer crops every field by seq_mask. Missing source residues
    # cannot silently disappear from a declared scope's coverage denominator.
    present_ids = {identities[i] for i in range(n) if present[i]}
    selected_ids = summary
    fixed_ids = {identities[i] for i in range(n) if fixed[i]}
    if (present_ids - authorized) - fixed_ids:
        raise ValueError('constraint authority mismatch: producer leaves unauthorized sequence residues unfixed')
    mutation_ids = (mutations & present_ids) - fixed_ids
    evidence, candidates, omissions = [], [], []
    sampled, entropies, logs = [], [], []
    for i, identity in enumerate(identities):
        values = probs[i].astype(float).tolist()
        total = math.fsum(values)
        if not math.isfinite(total):
            raise ValueError('seq_probs: row total nonfinite')
        # Pinned fampnn_denoiser.py emits F.softmax(seq_logits, dim=-1).
        # Allow only float32 summation roundoff (absolute tolerance 1e-6).
        # Zero-total rows retain their separate unscored semantics.
        if total > 0 and abs(total - 1.0) > 1e-6:
            raise ValueError('seq_probs: probability row total outside 1e-6 tolerance')
        scored = bool(present[i] and total > 0)
        selected = identity in selected_ids
        p = entropy = logp = None
        reason = 'absent' if not present[i] else 'zero_total'
        if scored:
            normalized = [v / total for v in values]
            p = normalized[int(pred[i])]
            entropy = -math.fsum(v * math.log(v) for v in normalized if v > 0)
            logp = math.log(p) if p > 0 else None
            reason = None if p > 0 else 'zero_probability'
            if selected:
                sampled.append(p)
                entropies.append(entropy)
                logs.append(logp)
            if identity in mutation_ids and STRICT_ALPHABET[int(pred[i])] != 'X':
                for target, target_p in enumerate(normalized):
                    if target == int(pred[i]) or STRICT_ALPHABET[target] == 'X':
                        continue
                    if p == 0 or target_p == 0:
                        omissions.append({'identity': identity, 'to_aa': STRICT_ALPHABET[target], 'reason': 'zero_probability'})
                        continue
                    delta = math.log(target_p) - math.log(p)
                    if delta < mutation_min_log_odds_delta:
                        continue
                    from_aa, to_aa = STRICT_ALPHABET[int(pred[i])], STRICT_ALPHABET[target]
                    candidates.append({'identity': identity, 'chain_index': int(chain[i]),
                        'residue_index': int(residue[i]), 'from_aa': from_aa, 'to_aa': to_aa,
                        'mutation': f"{from_aa}{identity.split(':')[1]}{identity.split(':')[2]}{to_aa}",
                        'from_prob': p, 'to_prob': target_p, 'log_odds_delta': delta})
        evidence.append({'identity': identity, 'chain_index': int(chain[i]), 'residue_index': int(residue[i]),
            'aa': STRICT_ALPHABET[int(pred[i])], 'present': bool(present[i]), 'fixed': bool(fixed[i]),
            'selected': selected, 'scored': scored, 'mutation_selected': identity in mutation_ids,
            'sampled_prob': p, 'entropy': entropy, 'log_prob': logp, 'log_prob_reason': reason})
    selected_count = len(summary)
    scored_count = len(sampled)
    coverage = scored_count / selected_count if selected_count else None
    if policy['require_full_coverage'] and coverage != 1:
        raise ValueError('workflow requires full summary coverage')
    candidates.sort(key=lambda item: item['log_odds_delta'], reverse=True)
    valid_logs = bool(logs) and all(v is not None for v in logs)
    log_reason = ('zero_probability' if logs else 'empty_scored_selection') if not valid_logs else None
    result = {'design': path.stem, 'sample_pkl_path': str(path),
        'artifact_binding': {'producer_input_id': authority['producer_input_id'],
            'producer_candidate_id': path.stem,
            'source_pdb': _byte_binding(source_bytes), 'sample_pkl': _byte_binding(sample_bytes),
            'candidate_pdb': _byte_binding(candidate_bytes), 'analysis_policy': _byte_binding(policy_bytes)},
        'candidate_pdb_path': str(candidate),
        'metric_source': 'fampnn_sample_pkl_seq_probs', 'core_protein_scientific_contract': 1,
        'dialect': STRICT_DIALECT, 'alphabet': STRICT_ALPHABET, 'analysis_policy': policy,
        'source_pdb_path': str(source), 'identity_mapping_source': STRICT_DIALECT + ':protein.read_pdb:ATOM-subset',
        'resolved_summary_membership': sorted(summary), 'resolved_mutation_membership': sorted(mutation_ids),
        'summary_denominator': 'scored_selected_count', 'present_count': int(present.sum()),
        'selected_count': selected_count, 'scored_selected_count': scored_count,
        'unscored_selected_count': selected_count - scored_count, 'invalid_selected_count': 0,
        'coverage': coverage, 'absent_selected_membership': sorted(summary - present_ids),
        'residue_evidence': evidence, 'mutation_omissions': omissions,
        'fampnn_seq_probs_available': bool(sampled), 'missing': [] if sampled else ['scored_selected_probabilities'],
        'total_residue_count': n, 'designed_residue_count': len(mutation_ids),
        'fampnn_mean_sampled_prob': math.fsum(sampled)/scored_count if sampled else None,
        'fampnn_min_sampled_prob': min(sampled) if sampled else None,
        'fampnn_mean_entropy': math.fsum(entropies)/scored_count if entropies else None,
        'fampnn_max_entropy': max(entropies) if entropies else None,
        'fampnn_total_sampled_log_prob': math.fsum(logs) if valid_logs else None,
        'fampnn_mean_sampled_log_prob': math.fsum(logs)/scored_count if valid_logs else None,
        'sampled_log_prob_reason': log_reason,
        'fampnn_mutation_scoring_available': any(r['scored'] and r['mutation_selected'] for r in evidence),
        'fampnn_mutation_score_source': 'seq_probs_log_odds_delta',
        'fampnn_mutation_score_scope': 'workflow_authorized_sequence_design_minus_fixed',
        'fampnn_mutation_opportunity_count': len(candidates),
        'fampnn_top_model_favored_mutations': candidates[:mutation_top_n]}
    return result


def _capture_bytes(path, role):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f'{role} binding: cannot capture {path}') from exc


def _byte_binding(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


def _capture_policy(value):
    # A dict caller has no file bytes: snapshot its explicit JSON serialization.
    # CLI callers always pass the original captured bytes, never reserialization.
    data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode('utf-8')
    policy = json.loads(data, object_pairs_hook=_policy_json_pairs)
    _validate_policy(policy)
    return policy, data


def analyze_sample_pkl(path: Path, *, mutation_top_n: int = 25, mutation_min_log_odds_delta: float = 0.0,
                       core_protein_scientific_contract=None, analysis_policy=None, source_pdb_dir=None,
                       candidate_pdb_dir=None) -> Dict[str, Any]:
    if core_protein_scientific_contract is not None:
        if type(core_protein_scientific_contract) is not int or core_protein_scientific_contract != 1:
            raise ValueError('unsupported core_protein_scientific_contract')
        if type(mutation_top_n) is not int or mutation_top_n < 0 or not math.isfinite(mutation_min_log_odds_delta):
            raise ValueError('invalid mutation analysis controls')
        policy, policy_bytes = _capture_policy(analysis_policy)
        sample_bytes = _capture_bytes(path, 'sample PKL')
        payload = pickle.loads(sample_bytes)
        return _analyze_strict(path, payload, policy, source_pdb_dir,
                               mutation_top_n, mutation_min_log_odds_delta,
                               sample_bytes, policy_bytes, candidate_pdb_dir)
    # Unmarked attempts retain their historical numerical and missingness contract.
    with path.open("rb") as handle:
        payload = pickle.load(handle)

    base = {
        "design": path.stem,
        "sample_pkl_path": str(path),
        "metric_source": "fampnn_sample_pkl_seq_probs",
        "fampnn_seq_probs_available": False,
        "missing": [],
    }
    if not isinstance(payload, dict):
        return {**base, "missing": ["dict_payload"]}

    seq_probs_raw = payload.get("seq_probs")
    pred_raw = payload.get("pred_aatype")
    if seq_probs_raw is None:
        return {**base, "missing": ["seq_probs"]}
    if pred_raw is None:
        return {**base, "missing": ["pred_aatype"]}

    probs_arr = _to_array(seq_probs_raw)
    pred_arr = _to_array(pred_raw)
    try:
        probs = probs_arr.reshape((-1, probs_arr.shape[-1]))
        pred = pred_arr.reshape(-1)
    except Exception as exc:
        return {**base, "missing": ["array_shape"], "error": str(exc)}

    length = min(len(probs), len(pred))
    if length == 0:
        return {**base, "missing": ["empty_seq_probs"]}

    seq_mask = _as_bool_mask(payload.get("seq_mask"), length)
    residue_index = _as_int_list(payload.get("residue_index"), length, default_start=1)
    chain_index = _as_int_list(payload.get("chain_index"), length, default_start=0)

    sampled_probs: List[float] = []
    entropies: List[float] = []
    low_confidence_positions: List[Dict[str, Any]] = []
    mutation_candidates: List[Dict[str, Any]] = []
    for idx in range(length):
        if not seq_mask[idx]:
            continue
        row = [float(x) for x in probs[idx].tolist()]
        aa_index = int(pred[idx])
        if aa_index < 0 or aa_index >= len(row):
            continue
        row_total = sum(max(x, 0.0) for x in row)
        normalized_row = [max(x, 0.0) / row_total for x in row] if row_total > 0 else row
        sampled_prob = max(float(normalized_row[aa_index]), EPS)
        entropy = _entropy(normalized_row)
        sampled_probs.append(sampled_prob)
        entropies.append(entropy)
        mutation_candidates.extend(
            _mutation_delta_candidates(
                normalized_row=normalized_row,
                sampled_aa_index=aa_index,
                chain_index=chain_index[idx],
                residue_index=residue_index[idx],
                min_log_odds_delta=mutation_min_log_odds_delta,
            )
        )
        if sampled_prob < 0.5 or entropy > 1.5:
            low_confidence_positions.append(
                {
                    "chain_index": chain_index[idx],
                    "residue_index": residue_index[idx],
                    "aa_index": aa_index,
                    "aa": AA_ALPHABET[aa_index] if aa_index < len(AA_ALPHABET) else str(aa_index),
                    "sampled_prob": _round(sampled_prob),
                    "entropy": _round(entropy),
                }
            )

    if not sampled_probs:
        return {**base, "missing": ["designed_residue_probabilities"]}

    log_probs = [math.log(max(p, EPS)) for p in sampled_probs]
    mutation_candidates.sort(key=lambda row: float(row.get("log_odds_delta") or 0.0), reverse=True)
    top_mutations = mutation_candidates[: max(int(mutation_top_n), 0)]
    result = {
        **base,
        "fampnn_seq_probs_available": True,
        "missing": [],
        "total_residue_count": length,
        "designed_residue_count": len(sampled_probs),
        "fampnn_mean_sampled_prob": _round(sum(sampled_probs) / len(sampled_probs)),
        "fampnn_min_sampled_prob": _round(min(sampled_probs)),
        "fampnn_mean_sampled_log_prob": _round(sum(log_probs) / len(log_probs)),
        "fampnn_total_sampled_log_prob": _round(sum(log_probs)),
        "fampnn_mean_entropy": _round(sum(entropies) / len(entropies)),
        "fampnn_max_entropy": _round(max(entropies)),
        "fampnn_low_confidence_positions": low_confidence_positions,
        "fampnn_mutation_scoring_available": True,
        "fampnn_mutation_score_source": "seq_probs_log_odds_delta",
        "fampnn_mutation_score_scope": "single_residue_substitutions_from_sample_pkl_seq_probs",
        "fampnn_mutation_opportunity_count": len(mutation_candidates),
        "fampnn_top_model_favored_mutations": top_mutations,
    }
    return result


def iter_sample_pkls(sample_pkl_dir: Path) -> Iterable[Path]:
    for suffix in ("*.pkl", "*.pickle"):
        yield from sorted(sample_pkl_dir.glob(suffix))


def write_jsonl(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scalar_keys = sorted(
        key
        for row in rows
        for key, value in row.items()
        if not isinstance(value, (list, dict))
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in scalar_keys})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-pkl-dir", required=True, type=Path)
    parser.add_argument("--out-jsonl", required=True, type=Path)
    parser.add_argument("--out-csv", type=Path)
    parser.add_argument("--mutation-top-n", type=int, default=25)
    parser.add_argument("--mutation-min-log-odds-delta", type=float, default=0.0)
    parser.add_argument('--core-protein-scientific-contract', type=int, choices=[1])
    parser.add_argument('--analysis-policy', type=Path)
    parser.add_argument('--source-pdb-dir', type=Path)
    parser.add_argument('--candidate-pdb-dir', type=Path)
    args = parser.parse_args()
    policy = None
    policy_bytes = None
    if args.core_protein_scientific_contract is not None:
        if args.analysis_policy is None:
            parser.error('marked analysis requires workflow-owned analysis policy')
        policy, policy_bytes = _capture_policy(args.analysis_policy.read_bytes())
        if args.source_pdb_dir is None:
            parser.error('marked analysis requires source PDB directory')

    if not args.sample_pkl_dir.exists():
        raise SystemExit(f"sample PKL directory does not exist: {args.sample_pkl_dir}")

    rows = [
        analyze_sample_pkl(
            path,
            mutation_top_n=args.mutation_top_n,
            mutation_min_log_odds_delta=args.mutation_min_log_odds_delta,
            core_protein_scientific_contract=args.core_protein_scientific_contract,
            analysis_policy=policy_bytes,
            source_pdb_dir=args.source_pdb_dir,
            candidate_pdb_dir=args.candidate_pdb_dir,
        )
        for path in iter_sample_pkls(args.sample_pkl_dir)
    ]
    if policy is not None and policy.get('require_full_coverage') is True and not rows:
        raise ValueError('workflow requires full summary coverage; no samples available')
    write_jsonl(rows, args.out_jsonl)
    if args.out_csv:
        write_csv(rows, args.out_csv)
    print(json.dumps({"sample_count": len(rows), "seq_probs_available": sum(1 for row in rows if row.get("fampnn_seq_probs_available"))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
