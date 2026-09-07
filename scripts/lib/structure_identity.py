"""Pure shared structure identity and axis definitions for API and producers.

No API service, runtime path resolver, or database imports. Scientific semantics
are unchanged from the strict aligned-error owner; that owner re-exports these
symbols for existing consumers. Source bytes are supplied by the caller.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any
import numpy as np

NUCLEIC_RESIDUES = {"DA", "DC", "DT", "DG", "A", "C", "U", "G"}


@dataclass(frozen=True)
class ResidueRecord:
    index: int
    chain_id: str
    residue_name: str
    residue_number: int
    ca_coord: np.ndarray
    cb_coord: np.ndarray
    chain_type: str
    insertion_code: str = ""
    selected_model: int | None = None
    selected_altloc: str | None = None
    auth_asym_id: str | None = None
    auth_seq_id: int | None = None
    label_asym_id: str | None = None
    label_seq_id: int | None = None
    source_entity_id: str | None = None
    entity_instance_id: str | None = None
    source_sha256: str | None = None


def _parse_pdb_atom_line(line: str) -> dict[str, Any] | None:
    atom_num = int(line[6:11].strip())
    atom_name = line[12:16].strip()
    residue_name = line[17:20].strip()
    chain_id = line[21].strip()
    residue_seq_num = line[22:26].strip()
    if not residue_seq_num:
        return None
    if residue_name == "LIG":
        return None
    return {
        "atom_num": atom_num,
        "atom_name": atom_name,
        "residue_name": residue_name,
        "chain_id": chain_id,
        "residue_seq_num": int(residue_seq_num),
        "insertion_code": line[26].strip(),
        "x": float(line[30:38].strip()),
        "y": float(line[38:46].strip()),
        "z": float(line[46:54].strip()),
    }


def _strict_structure_records(
    source: bytes, is_cif: bool, selected_model: int, selected_altloc: str,
) -> tuple[list[ResidueRecord], np.ndarray]:
    """Candidate-local polymer identity from one immutable byte snapshot.

    A model number and blank-or-explicit altloc policy are mandatory selections.
    PDB has no label namespace: nulls are intentional, not inferred sequence IDs.
    The mask is unavailable in this strict lane; token reduction needs producer axes.
    """
    import hashlib
    from io import StringIO

    if type(selected_model) is not int or selected_model < 1:
        raise ValueError("Invalid selected model")
    if not isinstance(selected_altloc, str) or len(selected_altloc) > 1:
        raise ValueError("Invalid selected altloc")
    digest = hashlib.sha256(source).hexdigest()
    atoms = []
    if is_cif:
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
        cif = MMCIF2Dict(StringIO(source.decode("utf-8")))
        def value(field, i):
            values = cif.get("_atom_site." + field)
            raw = values[i] if values is not None else None
            return None if raw in (None, ".", "?") else raw
        for i in range(len(cif.get("_atom_site.id", []))):
            if value("group_PDB", i) not in ("ATOM", "HETATM"):
                continue
            auth_seq = value("auth_seq_id", i)
            label_seq = value("label_seq_id", i)
            if auth_seq is None and label_seq is None:
                continue
            atoms.append(dict(
                atom_name=value("label_atom_id", i), residue_name=value("label_comp_id", i),
                auth_asym_id=value("auth_asym_id", i), label_asym_id=value("label_asym_id", i),
                auth_seq_id=int(auth_seq) if auth_seq is not None else None,
                label_seq_id=int(label_seq) if label_seq is not None else None,
                source_entity_id=value("label_entity_id", i),
                insertion_code=value("pdbx_PDB_ins_code", i) or "",
                selected_model=int(value("pdbx_PDB_model_num", i) or 1),
                selected_altloc=value("label_alt_id", i) or "",
                coord=np.array([float(value(f, i)) for f in ("Cartn_x", "Cartn_y", "Cartn_z")]),
            ))
    else:
        model = 1
        for line in source.decode("utf-8").splitlines():
            if line.startswith("MODEL "):
                model = int(line[10:14])
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            atom = _parse_pdb_atom_line(line)
            if atom is None:
                continue
            atoms.append(dict(
                atom_name=atom["atom_name"], residue_name=atom["residue_name"],
                auth_asym_id=atom["chain_id"], auth_seq_id=atom["residue_seq_num"],
                label_asym_id=None, label_seq_id=None, source_entity_id=None,
                insertion_code=atom["insertion_code"], selected_model=model,
                selected_altloc=line[16].strip(),
                coord=np.array([atom["x"], atom["y"], atom["z"]]),
            ))
    groups: dict[tuple, dict] = {}
    ordered = []
    for atom in atoms:
        if atom["selected_model"] != selected_model or atom["selected_altloc"] not in ("", selected_altloc):
            continue
        instance_chain = atom["label_asym_id"] if atom["label_asym_id"] is not None else atom["auth_asym_id"]
        if instance_chain is None:
            raise ValueError("Missing chain instance namespace")
        atom["entity_instance_id"] = json.dumps([selected_model, "label" if is_cif else "auth", instance_chain], separators=(",", ":"))
        # Group by the instance's residue position, then validate both namespaces.
        # Including contradictory auth/entity claims in the key would hide them in
        # separate partial groups instead of rejecting a mixed-identity CA/CB pair.
        position = ("label", atom["label_seq_id"]) if atom["label_seq_id"] is not None else ("auth", atom["auth_seq_id"])
        key = (atom["entity_instance_id"], position, atom["insertion_code"])
        group = groups.setdefault(key, {"identity": atom, "atoms": {}})
        identity_fields = ("residue_name", "source_entity_id", "auth_asym_id", "auth_seq_id",
                           "label_asym_id", "label_seq_id", "insertion_code", "selected_model", "entity_instance_id")
        if any(group["identity"][field] != atom[field] for field in identity_fields):
            raise ValueError("Conflicting residue identity")
        name = atom["atom_name"]
        if name in group["atoms"]:
            raise ValueError("Duplicate atom in selected model/altloc identity")
        group["atoms"][name] = atom
        if name in ("CA", "C1'", "C1"):
            if key in ordered:
                raise ValueError("Duplicate representative atom")
            ordered.append(key)
    records = []
    for key in ordered:
        group = groups[key]
        a = group["identity"]
        atom_map = group["atoms"]
        ca = next(atom_map[n] for n in ("CA", "C1'", "C1") if n in atom_map)
        cb = next((atom_map[n] for n in ("CB", "C3'", "C3") if n in atom_map), ca)
        records.append(ResidueRecord(
            index=len(records), chain_id=a["auth_asym_id"] if a["auth_asym_id"] is not None else a["label_asym_id"],
            residue_name=a["residue_name"], residue_number=a["auth_seq_id"] if a["auth_seq_id"] is not None else a["label_seq_id"],
            ca_coord=ca["coord"], cb_coord=cb["coord"],
            chain_type="nucleic_acid" if a["residue_name"] in NUCLEIC_RESIDUES else "protein",
            source_sha256=digest,
            **{k: a[k] for k in ("insertion_code", "selected_model", "auth_asym_id", "auth_seq_id", "label_asym_id", "label_seq_id", "source_entity_id", "entity_instance_id")},
            selected_altloc=selected_altloc,
        ))
    if not records:
        raise ValueError("No polymer residues in selected model/altloc")
    return records, np.asarray([], dtype=bool)


_IDENTITY_FIELDS = (
    "index", "chain_id", "residue_name", "insertion_code", "selected_model",
    "selected_altloc", "auth_asym_id", "auth_seq_id", "label_asym_id",
    "label_seq_id", "source_entity_id", "entity_instance_id",
)


def residue_identity_axis(
    residues: list[ResidueRecord], *, candidate_id: str, document_id: str,
) -> dict[str, Any]:
    """Serialize native order, not sorted chain order; index is source position.

    This describes identity only, not authorization. Producers must supply the
    actual native ordering; consumers must obtain evidence from trusted provenance.
    """
    hashes = {r.source_sha256 for r in residues}
    if not residues or None in hashes or len(hashes) != 1 or not candidate_id or not document_id:
        raise ValueError("Unavailable residue identity")
    return dict(candidate_id=candidate_id, document_id=document_id,
                source_sha256=residues[0].source_sha256,
                residues=[{k: getattr(r, k) for k in _IDENTITY_FIELDS} for r in residues])
