"""Deterministic CPU-only PDB/mmCIF normalization for neutral FrustraMPNN."""

from __future__ import annotations

import hashlib
import math
import os
import shlex
import stat
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import canonical_json_bytes, canonical_json_loads, validate_schema

BACKBONE_ATOMS = ("N", "CA", "C", "O")
NORMALIZER_VERSION = "frustrampnn_structure_normalizer_v1"
PDB_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
STANDARD_PROTEIN_RESIDUES = frozenset(THREE_TO_ONE)
_CLEAR_NONPROTEIN_RESIDUES = frozenset({
    "A", "C", "G", "U", "I", "DA", "DC", "DG", "DT", "DU", "DI",
    "ADE", "CYT", "GUA", "THY", "URA",
    "HOH", "WAT", "DOD", "LIG", "UNL",
})
_AUTHORIZED_MODIFICATION_LETTERS = {"MSE": "M"}


class StructureNormalizationError(ValueError):
    """A structure cannot be normalized without guessing or losing identity."""


def _protein_atom_names(heavy_atoms: str, hydrogen_atoms: str) -> frozenset[str]:
    names = set(heavy_atoms.split()) | set(hydrogen_atoms.split())
    names.add("HXT")
    names.update(
        f"{name[-1]}{name[:-1]}"
        for name in tuple(names)
        if name.startswith("H") and name[-1].isdigit()
    )
    return frozenset(names)


_STANDARD_PROTEIN_ATOMS = {
    "ALA": _protein_atom_names("N CA C O OXT CB", "H H1 H2 H3 HA HB1 HB2 HB3"),
    "ARG": _protein_atom_names("N CA C O OXT CB CG CD NE CZ NH1 NH2", "H H1 H2 H3 HA HB2 HB3 HG2 HG3 HD2 HD3 HE HH11 HH12 HH21 HH22"),
    "ASN": _protein_atom_names("N CA C O OXT CB CG OD1 ND2", "H H1 H2 H3 HA HB2 HB3 HD21 HD22"),
    "ASP": _protein_atom_names("N CA C O OXT CB CG OD1 OD2", "H H1 H2 H3 HA HB2 HB3 HD2"),
    "CYS": _protein_atom_names("N CA C O OXT CB SG", "H H1 H2 H3 HA HB2 HB3 HG"),
    "GLN": _protein_atom_names("N CA C O OXT CB CG CD OE1 NE2", "H H1 H2 H3 HA HB2 HB3 HG2 HG3 HE21 HE22"),
    "GLU": _protein_atom_names("N CA C O OXT CB CG CD OE1 OE2", "H H1 H2 H3 HA HB2 HB3 HG2 HG3 HE2"),
    "GLY": _protein_atom_names("N CA C O OXT", "H H1 H2 H3 HA2 HA3"),
    "HIS": _protein_atom_names("N CA C O OXT CB CG ND1 CD2 CE1 NE2", "H H1 H2 H3 HA HB2 HB3 HD1 HD2 HE1 HE2"),
    "ILE": _protein_atom_names("N CA C O OXT CB CG1 CG2 CD1", "H H1 H2 H3 HA HB HG12 HG13 HG21 HG22 HG23 HD11 HD12 HD13"),
    "LEU": _protein_atom_names("N CA C O OXT CB CG CD1 CD2", "H H1 H2 H3 HA HB2 HB3 HG HD11 HD12 HD13 HD21 HD22 HD23"),
    "LYS": _protein_atom_names("N CA C O OXT CB CG CD CE NZ", "H H1 H2 H3 HA HB2 HB3 HG2 HG3 HD2 HD3 HE2 HE3 HZ1 HZ2 HZ3"),
    "MET": _protein_atom_names("N CA C O OXT CB CG SD CE", "H H1 H2 H3 HA HB2 HB3 HG2 HG3 HE1 HE2 HE3"),
    "PHE": _protein_atom_names("N CA C O OXT CB CG CD1 CD2 CE1 CE2 CZ", "H H1 H2 H3 HA HB2 HB3 HD1 HD2 HE1 HE2 HZ"),
    "PRO": _protein_atom_names("N CA C O OXT CB CG CD", "H1 H2 H3 HA HB2 HB3 HG2 HG3 HD2 HD3"),
    "SER": _protein_atom_names("N CA C O OXT CB OG", "H H1 H2 H3 HA HB2 HB3 HG"),
    "THR": _protein_atom_names("N CA C O OXT CB OG1 CG2", "H H1 H2 H3 HA HB HG1 HG21 HG22 HG23"),
    "TRP": _protein_atom_names("N CA C O OXT CB CG CD1 CD2 NE1 CE2 CE3 CZ2 CZ3 CH2", "H H1 H2 H3 HA HB2 HB3 HD1 HE1 HE3 HZ2 HZ3 HH2"),
    "TYR": _protein_atom_names("N CA C O OXT CB CG CD1 CD2 CE1 CE2 CZ OH", "H H1 H2 H3 HA HB2 HB3 HD1 HD2 HE1 HE2 HH"),
    "VAL": _protein_atom_names("N CA C O OXT CB CG1 CG2", "H H1 H2 H3 HA HB HG11 HG12 HG13 HG21 HG22 HG23"),
}


def _mmcif_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(";"):
            block = [line[1:]]
            index += 1
            while index < len(lines) and not lines[index].startswith(";"):
                block.append(lines[index])
                index += 1
            if index == len(lines):
                raise StructureNormalizationError("unterminated mmCIF semicolon text field")
            tokens.append("\n".join(block))
            index += 1
            continue
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens.extend(lexer)
        except ValueError as exc:
            raise StructureNormalizationError(f"invalid mmCIF quoting on line {index + 1}: {exc}") from exc
        index += 1
    return tokens


def _is_cif_control(token: str) -> bool:
    lowered = token.lower()
    return (
        lowered in {"loop_", "stop_"}
        or lowered.startswith(("data_", "save_"))
        or token.startswith("_")
    )


def _atom_site_table(source_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        tokens = _mmcif_tokens(source_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise StructureNormalizationError(f"mmCIF source is not UTF-8: {exc}") from exc
    found: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].lower() != "loop_":
            index += 1
            continue
        index += 1
        tags: list[str] = []
        while index < len(tokens) and tokens[index].startswith("_"):
            tags.append(tokens[index])
            index += 1
        values: list[str] = []
        while index < len(tokens) and not _is_cif_control(tokens[index]):
            values.append(tokens[index])
            index += 1
        if not tags:
            raise StructureNormalizationError("mmCIF loop has no tags")
        if len(values) % len(tags):
            raise StructureNormalizationError("mmCIF loop value cardinality does not match its tags")
        if any(tag.startswith("_atom_site.") for tag in tags):
            if not all(tag.startswith("_atom_site.") for tag in tags):
                raise StructureNormalizationError("mixed-category atom_site loop is unsupported")
            found.append((tags, [
                values[offset : offset + len(tags)]
                for offset in range(0, len(values), len(tags))
            ]))
    if len(found) != 1:
        raise StructureNormalizationError(
            f"expected exactly one atom_site loop, found {len(found)}"
        )
    return found[0]


def derive_mmcif_atom_site_authority(source_bytes: bytes) -> dict[str, Any]:
    """Derive exact self-identity from one immutable, complete mmCIF atom_site table."""
    tags, rows = _atom_site_table(source_bytes)
    names = [tag.removeprefix("_atom_site.") for tag in tags]
    required = {
        "group_PDB", "label_comp_id", "label_asym_id", "label_entity_id",
        "label_seq_id", "auth_asym_id",
    }
    missing = sorted(required - set(names))
    if missing:
        raise StructureNormalizationError(
            f"mmCIF self identity columns are missing: {missing}"
        )
    positions = {name: names.index(name) for name in names}
    sequences: dict[tuple[str, str, str], dict[int, str]] = defaultdict(dict)
    for row in rows:
        if row[positions["group_PDB"]].upper() != "ATOM":
            continue
        key = (
            _required(row[positions["label_entity_id"]], "label_entity_id"),
            _required(row[positions["label_asym_id"]], "label_asym_id"),
            _required(row[positions["auth_asym_id"]], "auth_asym_id"),
        )
        seq_id = _integer(
            _required(row[positions["label_seq_id"]], "label_seq_id"),
            "label_seq_id",
        )
        residue = _required(
            row[positions["label_comp_id"]], "label_comp_id"
        ).upper()
        letter = _authority_residue_letter(residue)
        if letter is None:
            raise StructureNormalizationError(
                f"mmCIF self identity cannot authorize residue {residue!r}"
            )
        prior = sequences[key].setdefault(seq_id, letter)
        if prior != letter:
            raise StructureNormalizationError(
                "mmCIF self identity has contradictory residue labels"
            )
    if not sequences:
        raise StructureNormalizationError("mmCIF self identity contains no protein ATOM rows")
    entities: list[dict[str, str]] = []
    for key in sorted(sequences):
        positions_by_id = sequences[key]
        ordered = sorted(positions_by_id)
        if ordered != list(range(1, ordered[-1] + 1)):
            raise StructureNormalizationError(
                "mmCIF self identity requires contiguous complete label_seq_id coverage"
            )
        source_entity_id, label_asym_id, auth_asym_id = key
        entities.append({
            "entity_instance_id": f"mmcif:{source_entity_id}:{label_asym_id}:{auth_asym_id}",
            "source_entity_id": source_entity_id,
            "label_asym_id": label_asym_id,
            "auth_asym_id": auth_asym_id,
            "sequence": "".join(positions_by_id[index] for index in ordered),
        })
    return {
        "kind": "mmcif_atom_site_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": _sha256(source_bytes),
        "entities": entities,
    }


def _pdb_atom_name(atom_name: str, element: str) -> str:
    if len(atom_name) > 4:
        raise StructureNormalizationError(f"PDB atom name cannot represent {atom_name!r}")
    if len(atom_name) == 4:
        return atom_name
    if len(element) == 1 and atom_name and atom_name[0].isalpha():
        return f" {atom_name:<3}"
    return f"{atom_name:>4}"


def _pdb_field(value: float, width: int, precision: int, field: str) -> str:
    rendered = f"{value:{width}.{precision}f}"
    if len(rendered) > width:
        raise StructureNormalizationError(f"PDB {field} field overflow: {value}")
    return rendered


def validate_pdb_atom_representability(
    *, atom_name: str, element: str, residue_name: str, residue_id: int,
    insertion_code: str, x: float, y: float, z: float, occupancy: float,
    b_factor: float,
) -> None:
    for field, value in (
        ("atom name", atom_name), ("element", element),
        ("residue name", residue_name), ("insertion code", insertion_code),
    ):
        if not value.isascii():
            raise StructureNormalizationError(f"PDB {field} must be ASCII")
    if len(element) > 2:
        raise StructureNormalizationError(f"PDB element field cannot represent {element!r}")
    if len(residue_name) > 3:
        raise StructureNormalizationError(
            f"PDB residue name cannot represent {residue_name!r}"
        )
    if len(insertion_code) > 1:
        raise StructureNormalizationError("PDB insertion code exceeds one character")
    allowed_atoms = _STANDARD_PROTEIN_ATOMS.get(residue_name)
    if allowed_atoms is not None:
        if atom_name not in allowed_atoms:
            raise StructureNormalizationError(
                f"atom name {atom_name!r} is not valid for standard residue {residue_name}"
            )
        expected_element = next(
            (character for character in atom_name if character.isalpha()), ""
        )
        if expected_element != element:
            raise StructureNormalizationError(
                f"atom name {atom_name!r} is inconsistent with element {element!r}"
            )
    if not -999 <= residue_id <= 9999:
        raise StructureNormalizationError(
            f"PDB residue number field cannot represent {residue_id}"
        )
    _pdb_atom_name(atom_name, element)
    _pdb_field(x, 8, 3, "x coordinate")
    _pdb_field(y, 8, 3, "y coordinate")
    _pdb_field(z, 8, 3, "z coordinate")
    _pdb_field(occupancy, 6, 2, "occupancy")
    _pdb_field(b_factor, 6, 2, "B factor")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _open_parent(path: Path) -> tuple[int, str]:
    """Open the caller's unresolved path component-by-component without following links."""
    raw = os.fspath(path)
    if not raw or "\x00" in raw:
        raise StructureNormalizationError("path is empty or contains NUL")
    absolute = raw.startswith(os.sep)
    components = raw.split(os.sep)
    if absolute:
        components = components[1:]
    if not components or any(part in {"", ".", ".."} for part in components):
        raise StructureNormalizationError(f"unsafe lexical path component: {path}")
    leaf = components[-1]
    try:
        fd = os.open(os.sep if absolute else ".", os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise StructureNormalizationError(f"cannot open path root for {path}: {exc}") from exc
    try:
        for part in components[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd, leaf
    except OSError as exc:
        os.close(fd)
        raise StructureNormalizationError(f"symlink/invalid path component: {path}: {exc}") from exc


def _read_regular_no_follow(path: Path) -> bytes:
    parent, leaf = _open_parent(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=parent)
    except OSError as exc:
        os.close(parent)
        raise StructureNormalizationError(f"cannot open source without following symlinks: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StructureNormalizationError("source must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _protein_sequence(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not set(value) <= set(THREE_TO_ONE.values())
    ):
        raise StructureNormalizationError(f"{field} is not an exact protein sequence")
    return value


def _producer_authority_entities(
    artifact: Mapping[str, Any], *, source_hash: str,
) -> list[dict[str, Any]]:
    base_fields = {"schema_name", "schema_version", "source_sha256", "entities"}
    if set(artifact) not in (
        base_fields,
        base_fields | {"cm_complex_snapshot_sha256"},
    ):
        raise StructureNormalizationError("producer authority artifact has an invalid typed schema")
    if artifact.get("schema_name") != "producer_manifest" or artifact.get("schema_version") != 1:
        raise StructureNormalizationError("producer authority artifact schema kind/version is invalid")
    if artifact.get("source_sha256") != source_hash:
        raise StructureNormalizationError("producer authority artifact source_sha256 binding mismatch")
    snapshot_digest = artifact.get("cm_complex_snapshot_sha256")
    if snapshot_digest is not None and (
        not isinstance(snapshot_digest, str)
        or len(snapshot_digest) != 64
        or any(character not in "0123456789abcdef" for character in snapshot_digest)
    ):
        raise StructureNormalizationError(
            "producer authority CM complex snapshot SHA-256 is malformed"
        )
    raw_entities = artifact.get("entities")
    if not isinstance(raw_entities, list) or not raw_entities:
        raise StructureNormalizationError("producer authority artifact has no protein entities")
    contexts: list[dict[str, Any]] = []
    required = {
        "entity_type", "entity_instance_id", "source_entity_id", "label_asym_id",
        "auth_asym_id", "sequence",
    }
    for raw in raw_entities:
        if not isinstance(raw, Mapping) or not required <= set(raw) or set(raw) - (required | {"residue_mappings"}):
            raise StructureNormalizationError("producer authority entity schema is invalid")
        if raw["entity_type"] != "protein":
            continue
        identity = [raw[name] for name in (
            "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id"
        )]
        if any(not isinstance(value, str) or not value for value in identity):
            raise StructureNormalizationError("producer authority protein identity is incomplete")
        sequence = _protein_sequence(raw["sequence"], "producer authority sequence")
        residue_mappings: dict[tuple[int, str], int] = {}
        for record in raw.get("residue_mappings", []):
            if not isinstance(record, Mapping) or set(record) != {
                "auth_seq_id", "insertion_code", "label_seq_id"
            }:
                raise StructureNormalizationError("producer residue mapping schema is invalid")
            auth_seq = record["auth_seq_id"]
            insertion = record["insertion_code"]
            label_seq = record["label_seq_id"]
            if (
                isinstance(auth_seq, bool) or not isinstance(auth_seq, int)
                or not isinstance(insertion, str) or len(insertion) > 1
                or isinstance(label_seq, bool) or not isinstance(label_seq, int)
                or not 1 <= label_seq <= len(sequence)
            ):
                raise StructureNormalizationError("producer residue mapping identity is invalid")
            key = (auth_seq, insertion)
            if key in residue_mappings or label_seq in residue_mappings.values():
                raise StructureNormalizationError("producer residue mapping identity is ambiguous")
            residue_mappings[key] = label_seq
        contexts.append({
            "entity_instance_id": identity[0], "source_entity_id": identity[1],
            "label_asym_id": identity[2], "auth_asym_id": identity[3],
            "sequence": sequence, "residue_mappings": residue_mappings,
        })
    if not contexts:
        raise StructureNormalizationError("producer authority artifact authorizes no protein entities")
    return contexts


def _load_external_authority(
    path: Path | str | None, *, expected_kind: str, expected_hash: str,
    source_hash: str,
) -> list[dict[str, Any]]:
    if path is None:
        raise StructureNormalizationError("external authority artifact path is required")
    payload = _read_regular_no_follow(Path(path))
    if _sha256(payload) != expected_hash:
        raise StructureNormalizationError("external authority artifact hash/digest mismatch")
    try:
        artifact = canonical_json_loads(payload)
    except Exception as exc:
        raise StructureNormalizationError(f"external authority artifact JSON is invalid: {exc}") from exc
    if not isinstance(artifact, Mapping) or canonical_json_bytes(artifact) != payload:
        raise StructureNormalizationError("external authority artifact is not canonical JSON")
    if expected_kind != "producer_manifest_v1":
        raise StructureNormalizationError("external authority kind is unsupported")
    contexts = _producer_authority_entities(artifact, source_hash=source_hash)
    identities = [
        (context["entity_instance_id"], context["source_entity_id"],
         context["label_asym_id"], context["auth_asym_id"])
        for context in contexts
    ]
    if len(set(identities)) != len(identities) or len({item[3] for item in identities}) != len(identities):
        raise StructureNormalizationError("external authority protein chain identity is ambiguous")
    return contexts


def _apply_pdb_authority(
    atoms: list[dict[str, Any]], contexts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    by_chain = {context["auth_asym_id"]: context for context in contexts}
    observed_chains: set[str] = set()
    excluded_chains: set[str] = set()
    for atom in atoms:
        context = by_chain.get(atom["auth_asym_id"])
        if context is None:
            excluded_chains.add(atom["auth_asym_id"] or "<blank>")
            continue
        observed_chains.add(context["auth_asym_id"])
        atom.update({
            "entity_instance_id": context["entity_instance_id"],
            "source_entity_id": context["source_entity_id"],
            "label_asym_id": context["label_asym_id"],
            "label_seq_id": context["residue_mappings"].get(
                (atom["auth_seq_id"], atom["insertion_code"])
            ),
            "authorized_sequence": context["sequence"],
            "externally_authorized": True,
        })
    missing = set(by_chain) - observed_chains
    if missing:
        raise StructureNormalizationError(
            f"external authority protein chain is absent from PDB: {sorted(missing)}"
        )
    atoms[:] = [atom for atom in atoms if atom["auth_asym_id"] in by_chain]
    return [{
        "source_identity": chain,
        "reason_code": "non_protein_entity",
        "reason": "chain is outside external protein authority",
    } for chain in sorted(excluded_chains)]


def _finite(text: str, field: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise StructureNormalizationError(f"{field} is malformed") from exc
    if not math.isfinite(value):
        raise StructureNormalizationError(f"{field} must be finite")
    return value


def _integer(text: str, field: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise StructureNormalizationError(f"{field} is malformed") from exc


def _parse_pdb(payload: bytes) -> tuple[list[dict[str, Any]], set[int]]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise StructureNormalizationError("PDB source must be ASCII") from exc
    atoms: list[dict[str, Any]] = []
    models: set[int] = set()
    current_model = 1
    saw_model = False
    for line_number, line in enumerate(lines, start=1):
        record = line[:6].strip().upper()
        if record == "MODEL":
            current_model = _integer(line[10:14].strip(), "PDB MODEL")
            if current_model < 1:
                raise StructureNormalizationError("PDB model number must be >= 1")
            models.add(current_model)
            saw_model = True
            continue
        if record not in {"ATOM", "HETATM"}:
            continue
        if len(line) < 78:
            raise StructureNormalizationError(f"PDB atom record {line_number} is truncated")
        if not saw_model:
            models.add(1)
        atom_name = line[12:16].strip()
        residue_name = line[17:20].strip().upper()
        chain = line[21].strip()
        if not chain:
            raise StructureNormalizationError("PDB protein chain identity is blank")
        insertion = line[26].strip()
        auth_seq = _integer(line[22:26].strip(), "PDB author residue number")
        occupancy = _finite(line[54:60].strip(), "PDB occupancy")
        if not 0 <= occupancy <= 1:
            raise StructureNormalizationError("PDB occupancy is outside [0, 1]")
        atom = {
            "source_id": f"pdb:{line_number}",
            "record_type": record,
            "element": line[76:78].strip().upper(),
            "atom_name": atom_name,
            "altloc": line[16].strip(),
            "residue_name": residue_name,
            "label_asym_id": None,
            "auth_asym_id": chain,
            "source_entity_id": None,
            "label_seq_id": None,
            "auth_seq_id": auth_seq,
            "insertion_code": insertion,
            "model": current_model,
            "x": _finite(line[30:38].strip(), "PDB x coordinate"),
            "y": _finite(line[38:46].strip(), "PDB y coordinate"),
            "z": _finite(line[46:54].strip(), "PDB z coordinate"),
            "occupancy": occupancy,
            "b_factor": _finite(line[60:66].strip(), "PDB B factor"),
            "entity_instance_id": f"pdb:{chain}",
        }
        if not atom["element"]:
            raise StructureNormalizationError("PDB atom element is missing")
        atoms.append(atom)
    if not atoms:
        raise StructureNormalizationError("PDB has no coordinate atoms")
    return atoms, models


def _required(value: str, field: str) -> str:
    if value in {"", ".", "?"}:
        raise StructureNormalizationError(f"atom_site.{field} is required")
    return value


def _optional(value: str) -> str:
    return "" if value in {"", ".", "?"} else value


def _parse_mmcif(
    payload: bytes, authority: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], set[int], list[dict[str, str]]]:
    try:
        tags, values = _atom_site_table(payload)
    except Exception as exc:
        raise StructureNormalizationError(f"invalid mmCIF atom_site table: {exc}") from exc
    names = [tag.removeprefix("_atom_site.") for tag in tags]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise StructureNormalizationError(f"duplicate atom_site tags: {sorted(duplicates)}")
    required = {
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
        "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z",
        "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
        "auth_atom_id", "pdbx_PDB_model_num",
    }
    missing = sorted(required - set(names))
    if missing:
        raise StructureNormalizationError(f"mmCIF identity columns are missing: {missing}")
    positions = {name: names.index(name) for name in names}
    entities = authority.get("entities")
    if not isinstance(entities, list) or not entities:
        raise StructureNormalizationError("mmCIF identity authority requires protein entities")
    contexts: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for entity in entities:
        key = (
            str(entity.get("source_entity_id") or ""),
            str(entity.get("label_asym_id") or ""),
            str(entity.get("auth_asym_id") or ""),
        )
        if not all(key) or key in contexts:
            raise StructureNormalizationError("mmCIF authorized entity identity is ambiguous")
        sequence = entity.get("sequence")
        if not isinstance(sequence, str) or not sequence or not set(sequence) <= set(THREE_TO_ONE.values()):
            raise StructureNormalizationError("mmCIF authorized protein sequence is invalid")
        contexts[key] = entity
    atoms: list[dict[str, Any]] = []
    models: set[int] = set()
    excluded_keys: set[tuple[str, str, str]] = set()
    source_ids: set[str] = set()
    for row_number, raw in enumerate(values, start=1):
        def get(name: str) -> str:
            return raw[positions[name]]

        entity_key = (get("label_entity_id"), get("label_asym_id"), get("auth_asym_id"))
        context = contexts.get(entity_key)
        if context is None:
            excluded_keys.add(entity_key)
            continue
        source_id = _required(get("id"), "id")
        if source_id in source_ids:
            raise StructureNormalizationError(f"duplicate atom_site.id: {source_id}")
        source_ids.add(source_id)
        label_seq = _integer(_required(get("label_seq_id"), "label_seq_id"), "label_seq_id")
        auth_seq = _integer(_required(get("auth_seq_id"), "auth_seq_id"), "auth_seq_id")
        model = _integer(_required(get("pdbx_PDB_model_num"), "pdbx_PDB_model_num"), "model")
        residue = _required(get("label_comp_id"), "label_comp_id").upper()
        if residue != _required(get("auth_comp_id"), "auth_comp_id").upper():
            raise StructureNormalizationError("mmCIF label/auth residue identity disagrees")
        # Some structure predictors (including ESMFold2) omit atom_site.occupancy
        # entirely for deterministic single-conformer outputs. In that producer
        # form every emitted atom is fully occupied, so normalize the absent
        # column to 1.0. If the producer does emit occupancy, keep validating it
        # strictly rather than masking blank or malformed values.
        occupancy = (
            _finite(_required(get("occupancy"), "occupancy"), "mmCIF occupancy")
            if "occupancy" in positions
            else 1.0
        )
        if not 0 <= occupancy <= 1:
            raise StructureNormalizationError("mmCIF occupancy is outside [0, 1]")
        models.add(model)
        atoms.append({
            "source_id": f"cif:{source_id}",
            "record_type": _required(get("group_PDB"), "group_PDB").upper(),
            "element": _required(get("type_symbol"), "type_symbol").upper(),
            "atom_name": _required(get("label_atom_id"), "label_atom_id"),
            "altloc": _optional(get("label_alt_id")),
            "residue_name": residue,
            "label_asym_id": entity_key[1],
            "auth_asym_id": entity_key[2],
            "source_entity_id": entity_key[0],
            "label_seq_id": label_seq,
            "auth_seq_id": auth_seq,
            "insertion_code": _optional(get("pdbx_PDB_ins_code")),
            "model": model,
            "x": _finite(get("Cartn_x"), "mmCIF x coordinate"),
            "y": _finite(get("Cartn_y"), "mmCIF y coordinate"),
            "z": _finite(get("Cartn_z"), "mmCIF z coordinate"),
            "occupancy": occupancy,
            "b_factor": _finite(get("B_iso_or_equiv"), "mmCIF B factor"),
            "entity_instance_id": str(context["entity_instance_id"]),
            "authorized_sequence": str(context["sequence"]),
        })
    observed_contexts = {
        (atom["source_entity_id"], atom["label_asym_id"], atom["auth_asym_id"])
        for atom in atoms
    }
    if observed_contexts != set(contexts):
        raise StructureNormalizationError("missing authorized protein chain in mmCIF")
    excluded = [
        {
            "source_identity": ":".join(key),
            "reason_code": "non_protein_entity",
            "reason": "entity is outside authorized protein selection",
        }
        for key in sorted(excluded_keys)
    ]
    return atoms, models, excluded


def _is_protein_coordinate(atom: Mapping[str, Any]) -> bool:
    residue = str(atom["residue_name"])
    if residue in _CLEAR_NONPROTEIN_RESIDUES:
        return False
    if atom["record_type"] == "ATOM":
        # Unknown/noncanonical ATOM residues remain explicit protein rows; they
        # are never silently discarded or emitted as model-ready residues.
        return True
    return bool(
        atom.get("externally_authorized")
        and residue in _AUTHORIZED_MODIFICATION_LETTERS
    )


def _authority_residue_letter(residue_name: str) -> str | None:
    return THREE_TO_ONE.get(residue_name) or _AUTHORIZED_MODIFICATION_LETTERS.get(residue_name)


def _select_model(
    atoms: list[dict[str, Any]], models: set[int], selected_model: int | None
) -> tuple[int, list[dict[str, Any]]]:
    available = sorted(models)
    if selected_model is None:
        if len(available) != 1:
            raise StructureNormalizationError(
                f"multiple source models {available} require explicit model selection"
            )
        selected_model = available[0]
    if isinstance(selected_model, bool) or not isinstance(selected_model, int) or selected_model < 1:
        raise StructureNormalizationError("selected model must be an integer >= 1")
    if selected_model not in models:
        raise StructureNormalizationError(
            f"selected model {selected_model} is absent; available={available}"
        )
    return selected_model, [atom for atom in atoms if atom["model"] == selected_model]


def _select_altloc(
    members: Iterable[dict[str, Any]], selected_altloc: str
) -> tuple[list[dict[str, Any]], str]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_members = list(members)
    for atom in source_members:
        by_name[atom["atom_name"]].append(atom)
    selected: list[dict[str, Any]] = []
    for atom_name, candidates in by_name.items():
        preferred = [atom for atom in candidates if atom["altloc"] == selected_altloc and selected_altloc]
        blank = [atom for atom in candidates if not atom["altloc"]]
        chosen = preferred or blank
        if len(chosen) > 1:
            raise StructureNormalizationError(f"ambiguous altloc/atom identity for {atom_name}")
        if chosen:
            selected.append(chosen[0])
    if not selected:
        raise StructureNormalizationError("altloc policy selected no atoms for residue")
    order = {name: index for index, name in enumerate(BACKBONE_ATOMS)}
    selected.sort(key=lambda atom: (order.get(atom["atom_name"], 4), atom["atom_name"], atom["source_id"]))
    return selected, selected_altloc if any(atom["altloc"] for atom in source_members) else ""


def _atom_name_field(atom_name: str, element: str) -> str:
    if len(atom_name) == 4:
        return atom_name
    if len(element) == 1 and atom_name and atom_name[0].isalpha():
        return f" {atom_name:<3}"
    return f"{atom_name:>4}"


def _render_atom(atom: Mapping[str, Any], serial: int, chain: str) -> bytes:
    validate_pdb_atom_representability(
        atom_name=atom["atom_name"], element=atom["element"],
        residue_name=atom["residue_name"], residue_id=atom["auth_seq_id"],
        insertion_code=atom["insertion_code"], x=atom["x"], y=atom["y"], z=atom["z"],
        occupancy=atom["occupancy"], b_factor=atom["b_factor"],
    )
    if not 1 <= serial <= 99999:
        raise StructureNormalizationError("PDB atom serial overflow")
    record = "ATOM  " if atom["record_type"] == "ATOM" else "HETATM"
    line = (
        f"{record}{serial:5d} {_atom_name_field(atom['atom_name'], atom['element'])} "
        f"{atom['residue_name']:>3} {chain}{atom['auth_seq_id']:4d}{atom['insertion_code'] or ' '}"
        f"   {atom['x']:8.3f}{atom['y']:8.3f}{atom['z']:8.3f}"
        f"{atom['occupancy']:6.2f}{atom['b_factor']:6.2f}          {atom['element']:>2}  "
    )
    if len(line) != 80:
        raise StructureNormalizationError("internal PDB fixed-width rendering error")
    return (line + "\n").encode("ascii")


def _publish_pair(output: Path, pdb: bytes, sidecar: Path, mapping: bytes) -> None:
    parents: list[tuple[int, str, Path]] = []
    staged: list[tuple[int, str, str]] = []
    backups: list[tuple[int, str, str | None, bool]] = []
    preserved_backups: set[tuple[int, str]] = set()
    try:
        for path in (output, sidecar):
            parent, leaf = _open_parent(path)
            parents.append((parent, leaf, path))
            try:
                existing = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode):
                    raise StructureNormalizationError(f"output overwrite must be regular: {path}")
            except FileNotFoundError:
                pass
        for (parent, leaf, _), payload in zip(parents, (pdb, mapping), strict=True):
            temporary = f".{leaf}.stage.{uuid.uuid4().hex}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb", closefd=False) as handle:
                    handle.write(payload)
                    handle.flush()
                os.fsync(fd)
            finally:
                os.close(fd)
            staged.append((parent, temporary, leaf))
        for parent, temporary, leaf in staged:
            existed = True
            try:
                os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existed = False
            backup = f".{leaf}.backup.{uuid.uuid4().hex}" if existed else None
            if backup is not None:
                os.replace(leaf, backup, src_dir_fd=parent, dst_dir_fd=parent)
            backups.append((parent, leaf, backup, existed))
            os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
    except OSError as exc:
        for parent, leaf, backup, existed in reversed(backups):
            try:
                if backup is not None:
                    try: os.unlink(leaf, dir_fd=parent)
                    except FileNotFoundError: pass
                    os.replace(backup, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                elif not existed:
                    try: os.unlink(leaf, dir_fd=parent)
                    except FileNotFoundError: pass
                os.fsync(parent)
            except OSError:
                if backup is not None:
                    preserved_backups.add((parent, backup))
        raise StructureNormalizationError(f"paired publication failed: {exc}") from exc
    finally:
        for parent, temporary, _ in staged:
            try: os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError: pass
        for parent, _, backup, _ in backups:
            if backup is not None and (parent, backup) not in preserved_backups:
                try: os.unlink(backup, dir_fd=parent)
                except FileNotFoundError: pass
        for parent, _, _ in parents:
            os.close(parent)


def read_structure_bytes(path: Path | str) -> bytes:
    """Read exactly one regular structure generation without following any symlink."""

    return _read_regular_no_follow(Path(path))


def _normalize_structure(
    *,
    input_path: Path | str,
    source_bytes: bytes | None,
    output_pdb_path: Path | str,
    map_path: Path | str,
    target_id: str,
    parent_job_id: str,
    candidate_id: str,
    identity_authority: Mapping[str, Any],
    authority_artifact_path: Path | str | None = None,
    protein_selection: Mapping[str, Any],
    selected_model: int | None,
    altloc_policy: str,
) -> dict[str, Any]:
    """Normalize one explicitly authorized PDB/mmCIF structure without inference."""

    for name, value in (
        ("target_id", target_id), ("parent_job_id", parent_job_id), ("candidate_id", candidate_id)
    ):
        if not isinstance(value, str) or not value:
            raise StructureNormalizationError(f"{name} must be nonempty")
    source = Path(input_path)
    output = Path(output_pdb_path)
    sidecar = Path(map_path)
    compared_paths = [source, output, sidecar]
    if authority_artifact_path is not None:
        compared_paths.append(Path(authority_artifact_path))
    absolute = {Path(os.path.abspath(path)) for path in compared_paths}
    if len(absolute) != len(compared_paths):
        raise StructureNormalizationError("source, authority artifact, PDB, and map paths must be distinct")
    if not isinstance(identity_authority, Mapping):
        raise StructureNormalizationError("identity authority must be explicit")
    kind = identity_authority.get("kind")
    domain = identity_authority.get("identity_domain")
    authority_hash = identity_authority.get("authority_artifact_sha256")
    external_kinds = {"producer_manifest_v1"}
    self_kinds = {"pdb_self_identity_v1", "mmcif_atom_site_v1"}
    if kind not in self_kinds | external_kinds:
        raise StructureNormalizationError("identity authority kind is unsupported")
    if kind in external_kinds:
        if domain != "source_authoritative":
            raise StructureNormalizationError("external identity authority must be source_authoritative")
        if set(identity_authority) != {
            "kind", "identity_domain", "authority_artifact_sha256", "source_sha256"
        }:
            raise StructureNormalizationError(
                "external identity authority envelope cannot supply arbitrary entity mappings"
            )
    elif authority_artifact_path is not None:
        raise StructureNormalizationError("self identity authority must not use an external artifact path")
    if protein_selection.get("mode") not in {"all_protein_entities", "explicit"}:
        raise StructureNormalizationError("protein selection is invalid")
    prefix = "blank_or_explicit:"
    if not isinstance(altloc_policy, str) or not altloc_policy.startswith(prefix):
        raise StructureNormalizationError("altloc policy is invalid")
    altloc_token = altloc_policy[len(prefix):]
    selected_altloc = "" if altloc_token == "<blank>" else altloc_token
    if len(selected_altloc) > 1:
        raise StructureNormalizationError("selected altloc must be blank or one character")

    if source_bytes is None:
        source_bytes = read_structure_bytes(source)
    elif not isinstance(source_bytes, bytes):
        raise StructureNormalizationError("source_bytes must be immutable bytes")
    source_hash = _sha256(source_bytes)
    if not isinstance(authority_hash, str) or len(authority_hash) != 64:
        raise StructureNormalizationError("identity authority artifact hash is invalid")
    if kind in self_kinds and authority_hash != source_hash:
        raise StructureNormalizationError("self identity authority artifact hash does not match source")
    external_contexts: list[dict[str, Any]] = []
    if kind in external_kinds:
        if identity_authority.get("source_sha256") != source_hash:
            raise StructureNormalizationError("external authority source_sha256 binding mismatch")
        external_contexts = _load_external_authority(
            authority_artifact_path, expected_kind=str(kind), expected_hash=authority_hash,
            source_hash=source_hash,
        )
    suffix = source.suffix.lower()
    excluded: list[dict[str, str]] = []
    if suffix in {".pdb", ".ent"}:
        if kind == "pdb_self_identity_v1":
            if domain != "candidate_local":
                raise StructureNormalizationError("PDB self identity must remain candidate_local")
        elif kind not in external_kinds or domain != "source_authoritative":
            raise StructureNormalizationError("PDB input lacks an authorized identity domain")
        atoms, models = _parse_pdb(source_bytes)
        if external_contexts:
            excluded.extend(_apply_pdb_authority(atoms, external_contexts))
        source_format = "pdb"
    elif suffix in {".cif", ".mmcif"}:
        if kind not in {"mmcif_atom_site_v1", *external_kinds} or domain != "source_authoritative":
            raise StructureNormalizationError("mmCIF input requires source-authoritative identity")
        mmcif_authority: Mapping[str, Any]
        if external_contexts:
            mmcif_authority = {"entities": external_contexts}
        else:
            mmcif_authority = identity_authority
        atoms, models, excluded = _parse_mmcif(source_bytes, mmcif_authority)
        if external_contexts:
            for atom in atoms:
                atom["externally_authorized"] = True
        source_format = "mmcif"
    else:
        raise StructureNormalizationError("unsupported structure format")

    model, atoms = _select_model(atoms, models, selected_model)
    selected_sequences: dict[str, str] = (
        {context["entity_instance_id"]: context["sequence"] for context in external_contexts}
        if external_contexts and protein_selection.get("mode") == "all_protein_entities"
        else {}
    )
    if protein_selection.get("mode") == "explicit":
        entities = protein_selection.get("entities")
        if not isinstance(entities, list) or not entities:
            raise StructureNormalizationError("explicit protein selection requires entities")
        selected_ids: set[str] = set()
        for entity in entities:
            if not isinstance(entity, Mapping):
                raise StructureNormalizationError("selected entity must be an object")
            entity_id = entity.get("entity_instance_id")
            sequence = entity.get("sequence")
            if not isinstance(entity_id, str) or not entity_id or entity_id in selected_ids:
                raise StructureNormalizationError("explicit protein identity is missing or duplicate")
            if not isinstance(sequence, str) or not sequence or not set(sequence) <= set(THREE_TO_ONE.values()):
                raise StructureNormalizationError("explicit protein sequence is invalid")
            selected_ids.add(entity_id)
            selected_sequences[entity_id] = sequence
            observed = next((atom for atom in atoms if atom["entity_instance_id"] == entity_id), None)
            if observed is not None:
                for field in ("source_entity_id", "label_asym_id", "auth_asym_id"):
                    if entity.get(field) != observed.get(field):
                        raise StructureNormalizationError(f"explicit tuple {field} identity mismatch")
                if observed.get("authorized_sequence") is not None and sequence != observed.get("authorized_sequence"):
                    raise StructureNormalizationError("explicit tuple authorized sequence identity mismatch")
        observed_ids = {str(atom["entity_instance_id"]) for atom in atoms}
        missing_ids = selected_ids - observed_ids
        if missing_ids:
            raise StructureNormalizationError(
                f"explicit protein selection is absent from selected model: {sorted(missing_ids)}"
            )
        for entity_id in sorted(observed_ids - selected_ids):
            excluded.append({
                "source_identity": entity_id,
                "reason_code": "not_selected",
                "reason": "protein entity is outside explicit protein selection",
            })
        atoms = [atom for atom in atoms if atom["entity_instance_id"] in selected_ids]
    protein_atoms = [atom for atom in atoms if _is_protein_coordinate(atom)]
    protein_atom_ids = {id(atom) for atom in protein_atoms}
    for atom in atoms:
        if id(atom) not in protein_atom_ids:
            identity = f"{atom['auth_asym_id']}:{atom['auth_seq_id']}{atom['insertion_code']}:{atom['residue_name']}"
            if not any(record["source_identity"] == identity for record in excluded):
                excluded.append({
                    "source_identity": identity,
                    "reason_code": "non_protein_entity",
                    "reason": "non-protein coordinate record is excluded from model input",
                })
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for atom in protein_atoms:
        key = (
            atom["entity_instance_id"], atom["auth_asym_id"], atom["auth_seq_id"],
            atom["insertion_code"], atom["label_asym_id"], atom["label_seq_id"],
            atom["residue_name"], atom["source_entity_id"],
        )
        grouped[key].append(atom)
    if not grouped:
        raise StructureNormalizationError("selected model has no standard protein residues")
    auth_keys = [(key[1], key[2], key[3]) for key in grouped]
    if len(set(auth_keys)) != len(auth_keys):
        raise StructureNormalizationError("duplicate author residue identity")
    if source_format == "mmcif":
        label_keys = [(key[0], key[5]) for key in grouped]
        if len(set(label_keys)) != len(label_keys):
            raise StructureNormalizationError("duplicate label sequence identity")
    ordered_keys = sorted(
        grouped,
        key=lambda key: (
            key[0], key[5] if key[5] is not None else 10**9,
            key[2], key[3], key[6],
        ),
    )
    instance_order = list(dict.fromkeys(key[0] for key in ordered_keys))
    if source_format == "pdb":
        # PDB authority is itself the identity authority: remapping its chain is loss.
        chain_map = {key[0]: key[1] for key in ordered_keys}
    else:
        if len(instance_order) > len(PDB_CHAIN_IDS):
            raise StructureNormalizationError("too many protein chains for normalized PDB")
        chain_map = {instance: PDB_CHAIN_IDS[index] for index, instance in enumerate(instance_order)}
    sequence_index: dict[str, int] = defaultdict(int)
    model_position: dict[str, int] = defaultdict(int)
    pdb_parts: list[bytes] = []
    rows: list[dict[str, Any]] = []
    serial = 1
    for key in ordered_keys:
        instance, auth_chain, auth_seq, insertion, label_chain, label_seq, residue, source_entity = key
        selected_atoms, selected_altloc_value = _select_altloc(grouped[key], selected_altloc)
        sequence_index[instance] += 1
        backbone = {
            name: next((atom["source_id"] for atom in selected_atoms if atom["atom_name"] == name), None)
            for name in BACKBONE_ATOMS
        }
        missing = [name for name, source_id in backbone.items() if source_id is None]
        wt = THREE_TO_ONE.get(residue)
        if wt is None:
            status, reason = "nonstandard_residue", f"nonstandard protein residue: {residue}"
        elif missing:
            status, reason = "missing_backbone", f"missing required backbone atoms: {', '.join(missing)}"
        else:
            status, reason = "mapped", None
        if source_format == "mmcif":
            context_sequence = grouped[key][0]["authorized_sequence"]
            if label_seq is None or not 1 <= label_seq <= len(context_sequence):
                raise StructureNormalizationError("mmCIF sequence index is outside authorized sequence")
            if wt is not None and wt != context_sequence[label_seq - 1]:
                raise StructureNormalizationError("mmCIF residue identity disagrees with authorized sequence")
            sequence_index[instance] = label_seq
        chain = chain_map[instance]
        position = model_position[chain]
        if status == "mapped":
            for atom in selected_atoms:
                pdb_parts.append(_render_atom(atom, serial, chain))
                serial += 1
            model_position[chain] += 1
        rows.append({
            "entity_instance_id": instance,
            "source_entity_id": source_entity,
            "label_asym_id": label_chain,
            "auth_asym_id": auth_chain,
            "label_seq_id": label_seq,
            "auth_seq_id": auth_seq,
            "insertion_code": insertion,
            "sequence_index": sequence_index[instance],
            "pdb_chain_id": chain,
            "pdb_residue_id": auth_seq,
            "pdb_insertion_code": insertion,
            "model_position": position,
            "residue_name": residue,
            "wt": wt,
            "selected_model": model,
            "selected_altloc": selected_altloc_value,
            "backbone_complete": status == "mapped",
            "backbone_atoms": backbone,
            "status": status,
            "reason": reason,
        })
        if status != "mapped":
            excluded.append({
                "source_identity": f"{auth_chain}:{auth_seq}{insertion}:{residue}",
                "reason_code": status,
                "reason": reason or status,
            })
    if source_format == "mmcif":
        for instance in instance_order:
            instance_key = next(key for key in ordered_keys if key[0] == instance)
            authorized = grouped[instance_key][0]["authorized_sequence"]
            entity_rows = [row for row in rows if row["entity_instance_id"] == instance]
            labels = [row["label_seq_id"] for row in entity_rows]
            if labels != list(range(1, len(authorized) + 1)) or len(entity_rows) != len(authorized):
                raise StructureNormalizationError("mmCIF authorized label sequence coverage is incomplete")
            if any(
                row["wt"] is not None and row["wt"] != authorized[row["label_seq_id"] - 1]
                for row in entity_rows
            ):
                raise StructureNormalizationError("mmCIF authorized sequence identity mismatch")
    mapped = [row for row in rows if row["status"] == "mapped"]
    if not mapped:
        raise StructureNormalizationError("normalization produced no scoreable protein residues")
    for instance_id, expected_sequence in selected_sequences.items():
        if source_format == "mmcif":
            observed_sequence = next(
                grouped[key][0]["authorized_sequence"]
                for key in ordered_keys if key[0] == instance_id
            )
        else:
            entity_rows = [
                row for row in rows if row["entity_instance_id"] == instance_id
            ]
            if external_contexts:
                letters = [_authority_residue_letter(row["residue_name"]) for row in entity_rows]
                if any(letter is None for letter in letters):
                    raise StructureNormalizationError(
                        f"external PDB residue identity cannot be bound for {instance_id}"
                    )
                observed_sequence = "".join(str(letter) for letter in letters)
            else:
                observed_sequence = "".join(
                    row["wt"] for row in entity_rows if row["wt"] is not None
                )
        if observed_sequence != expected_sequence:
            raise StructureNormalizationError(
                f"selected protein sequence is stale for {instance_id}: "
                f"expected={expected_sequence}, observed={observed_sequence}"
            )
    pdb_bytes = b"".join(pdb_parts) + b"END\n"
    model_sequence = "".join(row["wt"] for row in mapped)
    structure_map = {
        "schema_name": "frustrampnn_structure_map",
        "schema_version": 1,
        "target_id": target_id,
        "parent_job_id": parent_job_id,
        "candidate_id": candidate_id,
        "source_format": source_format,
        "source_sha256": source_hash,
        "source_bytes": len(source_bytes),
        "identity_authority": kind,
        "identity_domain": domain,
        "authority_artifact_sha256": authority_hash,
        "normalized_pdb_sha256": _sha256(pdb_bytes),
        "selected_source_model": model,
        "altloc_policy": altloc_policy,
        "normalizer_version": NORMALIZER_VERSION,
        "model_ready_sequence": model_sequence,
        "model_ready_sequence_sha256": _sha256(model_sequence.encode("ascii")),
        "excluded_records": sorted(
            excluded, key=lambda record: (record["source_identity"], record["reason_code"])
        ),
        "rows": rows,
    }
    try:
        validate_schema("frustrampnn_structure_map_v1", structure_map)
    except Exception as exc:
        raise StructureNormalizationError(f"structure-map contract failed: {exc}") from exc
    _publish_pair(output, pdb_bytes, sidecar, canonical_json_bytes(structure_map))
    return structure_map


def normalize_structure(
    *,
    input_path: Path | str,
    output_pdb_path: Path | str,
    map_path: Path | str,
    target_id: str,
    parent_job_id: str,
    candidate_id: str,
    identity_authority: Mapping[str, Any],
    authority_artifact_path: Path | str | None = None,
    protein_selection: Mapping[str, Any],
    selected_model: int | None,
    altloc_policy: str,
) -> dict[str, Any]:
    """Read and normalize one explicitly authorized structure generation."""

    return _normalize_structure(
        input_path=input_path,
        source_bytes=None,
        output_pdb_path=output_pdb_path,
        map_path=map_path,
        target_id=target_id,
        parent_job_id=parent_job_id,
        candidate_id=candidate_id,
        identity_authority=identity_authority,
        authority_artifact_path=authority_artifact_path,
        protein_selection=protein_selection,
        selected_model=selected_model,
        altloc_policy=altloc_policy,
    )


def normalize_structure_bytes(
    *,
    source_bytes: bytes,
    input_path: Path | str,
    output_pdb_path: Path | str,
    map_path: Path | str,
    target_id: str,
    parent_job_id: str,
    candidate_id: str,
    identity_authority: Mapping[str, Any],
    authority_artifact_path: Path | str | None = None,
    protein_selection: Mapping[str, Any],
    selected_model: int | None,
    altloc_policy: str,
) -> dict[str, Any]:
    """Normalize immutable bytes already read from one no-follow source descriptor."""

    return _normalize_structure(
        input_path=input_path,
        source_bytes=source_bytes,
        output_pdb_path=output_pdb_path,
        map_path=map_path,
        target_id=target_id,
        parent_job_id=parent_job_id,
        candidate_id=candidate_id,
        identity_authority=identity_authority,
        authority_artifact_path=authority_artifact_path,
        protein_selection=protein_selection,
        selected_model=selected_model,
        altloc_policy=altloc_policy,
    )


__all__ = [
    "NORMALIZER_VERSION", "StructureNormalizationError", "normalize_structure",
    "normalize_structure_bytes", "read_structure_bytes",
]
