"""Pinned Boltz native identity derivation from caller-owned byte snapshots.

This module performs no file reads, writes, or publication. Both the publisher
and trusted ingestion can independently derive the native ordering from the
processed structured ledger, then compare a claimed descriptor to that evidence.
Containment/current-job authority belongs to the caller, not to a sidecar hash.
"""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import numpy as np
from .structure_identity import _strict_structure_records, residue_identity_axis
from write_sequence_producer_manifest import _reject_duplicate_keys, _reject_json_constant

_BOLTZ_REVISION = "7ebf1be087d4d61a02234c878402838bf3712d8b"

_BOLTZ_CURRENT_REVISION = "d91963a31900df736accc2eaf6e722b8ebf9f586"


def _validate_current_confidence(confidence, chain_map):
    # These additional fields are saved by 2.15.1. Keep the old format strict
    # and do not reinterpret new summary values as per-token measurements.
    ids = {str(c['native_asym_id']): c['chain_id'] for c in chain_map}
    if confidence['chain_ids'] != ids:
        raise ValueError('native confidence chain labels disagree with ledger')
    def distance(v):
        return v is None or (type(v) in (int, float) and np.isfinite(v) and v >= 0)
    def keyed(v):
        return isinstance(v, dict) and v.keys() == ids.keys()
    for suffix in ('', '_unweighted'):
        diagonal = confidence['chains_pae' + suffix]
        pairs = confidence['pair_chains_pae' + suffix]
        if not (distance(confidence['complex_pae' + suffix]) and distance(confidence['complex_ipae' + suffix])
                and keyed(diagonal) and all(distance(x) for x in diagonal.values()) and keyed(pairs)
                and all(keyed(row) and all(distance(x) for x in row.values()) for row in pairs.values())):
            raise ValueError('invalid native PAE summary')
    if type(confidence['structure_checks_passed']) is not bool or any(
        type(confidence[k]) is not int or confidence[k] < 0 for k in _CHECK_COUNTS
    ):
        raise ValueError('invalid native structure check summary')


_CHECK_COUNTS = {'chiral_centers_checked', 'chiral_centers_inverted', 'chiral_centers_flattened',
                 'bonds_checked', 'bonds_out_of_range'}
_CURRENT_EXTRA = _CHECK_COUNTS | {'chain_ids', 'structure_checks_passed'} | {
    k + suffix for k in ('complex_pae', 'complex_ipae', 'chains_pae', 'pair_chains_pae') for suffix in ('', '_unweighted')
}


def derive_boltz_native_identity(*, source: bytes, structure_name: str,
                                 ledger_bytes: bytes, pae_bytes: bytes,
                                 plddt_bytes: bytes, confidence_bytes: bytes,
                                 candidate_id: str, document_id: str,
                                 provider_revision: str | None = None) -> dict[str, Any]:
    for identity in (candidate_id, document_id):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("empty producer identity")
    match = re.fullmatch(r"(.+)_model_(\d+)\.pdb", structure_name)
    if match is None or Path(structure_name).name != structure_name:
        raise ValueError("non-native Boltz structure filename")
    record_id = match.group(1)
    with np.load(BytesIO(ledger_bytes), allow_pickle=False) as ledger:
        chains, residues, atoms, mask = (ledger[k] for k in ("chains", "residues", "atoms", "mask"))
    required = ((chains, {"name", "mol_type", "entity_id", "sym_id", "asym_id", "atom_idx", "atom_num", "res_idx", "res_num", "cyclic_period"}),
                (residues, {"name", "res_type", "res_idx", "atom_idx", "atom_num", "atom_center", "atom_disto", "is_standard", "is_present"}),
                (atoms, {"name", "element", "coords", "is_present", "bfactor", "plddt"}))
    for table, fields in required:
        if table.ndim != 1 or set(table.dtype.names or ()) != fields:
            raise ValueError("invalid pinned native structured table")
        for field in fields:
            dtype = table.dtype.fields[field][0]
            if field == "name":
                expected = np.dtype("<U4" if table is atoms else "<U5")
            elif field in {"is_standard", "is_present"}:
                expected = np.dtype("?")
            elif field == "coords":
                expected = np.dtype("3f4")
            elif field in {"bfactor", "plddt"}:
                expected = np.dtype("f4")
            elif field in {"mol_type", "res_type", "element"}:
                expected = np.dtype("i1")
            else:
                expected = np.dtype("i4")
            if dtype != expected:
                raise ValueError("invalid pinned native field dtype")
    if mask.dtype.kind != "b" or mask.shape != (len(chains),) or not mask.any():
        raise ValueError("invalid native chain mask")
    names = [str(c["name"]) for c in chains]
    asym_ids = [int(c["asym_id"]) for c in chains]
    if any(len(n) != 1 or not n.strip() for n in names) or len(set(names)) != len(names):
        raise ValueError("empty, duplicate or PDB-incompatible chain IDs")
    if any(i < 0 for i in asym_ids) or len(set(asym_ids)) != len(asym_ids):
        raise ValueError("invalid native asym IDs")

    records, _ = _strict_structure_records(source, False, 1, "")
    by_identity = {(r.auth_asym_id, r.auth_seq_id, r.residue_name, r.insertion_code): r for r in records}
    native_records, token_map, chain_map, expected_atoms = [], [], [], []
    used_residues, used_atoms = set(), set()
    for source_chain_index, chain in enumerate(chains):
        if not mask[source_chain_index]:
            continue
        if int(chain["mol_type"]) != 0:
            raise ValueError("native identity unavailable: non-protein/atom-token axis unsupported")
        start, count = int(chain["res_idx"]), int(chain["res_num"])
        if start < 0 or count <= 0 or start + count > len(residues):
            raise ValueError("native residue span out of bounds")
        chain_atom_start, chain_atom_count = int(chain["atom_idx"]), int(chain["atom_num"])
        chain_atoms = []
        chain_map.append(dict(native_asym_id=int(chain["asym_id"]), source_chain_index=source_chain_index,
                              output_asym_id=len(chain_map), chain_id=str(chain["name"]),
                              native_entity_id=int(chain["entity_id"]), native_sym_id=int(chain["sym_id"])))
        for ri in range(start, start + count):
            res = residues[ri]
            identity = (str(chain["name"]), int(res["res_idx"]) + 1, str(res["name"])[:3], "")
            if ri in used_residues or identity not in by_identity:
                raise ValueError("native ledger does not identify written polymer residue")
            used_residues.add(ri)
            native_records.append(by_identity[identity])
            ast, anum = int(res["atom_idx"]), int(res["atom_num"])
            if ast < 0 or anum <= 0 or ast + anum > len(atoms):
                raise ValueError("native atom span out of bounds")
            for ai in range(ast, ast + anum):
                if ai in used_atoms:
                    raise ValueError("overlapping native atom spans")
                used_atoms.add(ai)
                chain_atoms.append(ai)
                expected_atoms.append((identity[0], identity[1], identity[2], str(atoms[ai]["name"])))
            token_map.append(dict(token_index=len(token_map), source_chain_index=source_chain_index,
                                  native_asym_id=int(chain["asym_id"]), source_residue_index=ri,
                                  native_res_idx=int(res["res_idx"]), structure_residue_index=by_identity[identity].index))
        if chain_atoms != list(range(chain_atom_start, chain_atom_start + chain_atom_count)):
            raise ValueError("chain and residue atom spans disagree")
    if len(native_records) != len(records) or len({r.index for r in native_records}) != len(records):
        raise ValueError("native token axis is not a bijection to written structure")
    actual_atoms = []
    for line in source.decode("utf-8").splitlines():
        if line.startswith("MODEL ") or line.startswith("HETATM"):
            raise ValueError("unexpected model/heteroatom in native protein output")
        if line.startswith("ATOM  "):
            if line[16].strip() or line[26].strip():
                raise ValueError("native output contains alternate or insertion identity")
            actual_atoms.append((line[21].strip(), int(line[22:26]), line[17:20].strip(), line[12:16].strip()))
    if actual_atoms != expected_atoms:
        raise ValueError("native atom ledger disagrees with exact written structure")
    axis = residue_identity_axis(native_records, candidate_id=candidate_id, document_id=document_id)
    n = len(token_map)

    def artifact(prefix, key, shape):
        name = f"{prefix}_{Path(structure_name).stem}.npz"
        data = pae_bytes if key == "pae" else plddt_bytes
        with np.load(BytesIO(data), allow_pickle=False) as payload:
            if payload.files != [key]:
                raise ValueError("native confidence NPZ keys are not exact")
            array = payload[key]
            if array.shape != shape or array.dtype.kind not in "fi" or not np.isfinite(array).all():
                raise ValueError("native confidence dimensions/values invalid")
            if np.any(array < 0) or (key == "plddt" and np.any(array > 1)):
                raise ValueError("native confidence values outside native domain")
        return dict(artifact_key=name, artifact_sha256=hashlib.sha256(data).hexdigest())

    pae = artifact("pae", "pae", (n, n))
    pae.update(format="boltz_pae_npz", matrix_key="pae", identity_evidence=dict(
        artifact_sha256=pae["artifact_sha256"], matrix_key="pae", row_axis=axis, column_axis=axis))
    plddt = artifact("plddt", "plddt", (n,))
    plddt.update(vector_key="plddt", axis=axis, units="fraction", source_axis="boltz_native_tokens")
    confidence_name = f"confidence_{Path(structure_name).stem}.json"
    confidence = json.loads(confidence_bytes, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_json_constant)
    scalar_keys = {"confidence_score", "ptm", "iptm", "ligand_iptm", "protein_iptm",
                   "complex_plddt", "complex_iplddt", "complex_pde", "complex_ipde"}
    base = scalar_keys | {"chains_ptm", "pair_chains_iptm"}
    if not isinstance(confidence, dict) or set(confidence) not in (base, base | _CURRENT_EXTRA):
        raise ValueError("native confidence JSON fields are not exact")
    extra = set(confidence) != base
    if provider_revision is None:
        # Reproduce legacy receipts byte-for-byte. A newer file shape alone is
        # not evidence of a particular Git revision.
        provider_revision = "unrecorded" if extra else _BOLTZ_REVISION
    if provider_revision not in (_BOLTZ_REVISION, _BOLTZ_CURRENT_REVISION):
        provider_revision = 'unrecorded'
    if (provider_revision == _BOLTZ_REVISION and extra) or (provider_revision == _BOLTZ_CURRENT_REVISION and not extra):
        raise ValueError("native confidence fields disagree with recorded release")
    if any(type(confidence[k]) not in (int, float) or not np.isfinite(confidence[k]) or confidence[k] < 0 for k in scalar_keys):
        raise ValueError("non-finite or negative native confidence scalar")
    if extra:
        _validate_current_confidence(confidence, chain_map)
    keys = {str(c["native_asym_id"]) for c in chain_map}
    chain_values = confidence.get("chains_ptm")
    pairs = confidence.get("pair_chains_iptm")
    if not isinstance(chain_values, dict) or set(chain_values) != keys or not isinstance(pairs, dict) or set(pairs) != keys:
        raise ValueError("confidence chain keys disagree with native asym ledger")
    def score(v):
        return type(v) in (int, float) and np.isfinite(v) and 0 <= v <= 1
    if not all(score(v) for v in chain_values.values()) or any(
        not isinstance(row, dict) or set(row) != keys or not all(score(v) for v in row.values()) for row in pairs.values()
    ):
        raise ValueError("invalid native chain confidence values/keys")
    ledger_key = f"native_structure_{record_id}.npz"
    return dict(schema_name="boltz_native_identity", schema_version=1, provider_revision=provider_revision,
                source_axis="boltz_native_tokens", native_token_count=n, token_to_structure=token_map,
                structure_sha256=hashlib.sha256(source).hexdigest(),
                processed_structure=dict(artifact_key=ledger_key, artifact_sha256=hashlib.sha256(ledger_bytes).hexdigest()),
                chain_index_map=chain_map, aligned_error=pae, vectors=[plddt],
                confidence=dict(artifact_key=confidence_name, artifact_sha256=hashlib.sha256(confidence_bytes).hexdigest(),
                                chain_key_namespace="native_asym_id"), role_assignment=None)



def verify_boltz_native_identity(claimed: Any, **snapshots) -> dict[str, Any]:
    """Return independently derived evidence only after exact closed agreement.

    A consistently reordered sidecar is still false if it disagrees with native
    token order. JSON comparison also distinguishes true/1 and exact field types.
    """
    if not isinstance(claimed, dict):
        raise ValueError("invalid native identity claim")
    expected = derive_boltz_native_identity(**snapshots, provider_revision=claimed.get("provider_revision"))
    try:
        matches = json.dumps(claimed, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("claimed identity disagrees with independently derived native ledger")
    return expected
