"""Deterministic, fail-closed structure normalization for conformational mapping.

The source mmCIF remains authoritative.  This module emits a derived, single-
model PDB plus a ``cm_structure_map_v1`` sidecar that preserves enough source
identity to audit every normalized protein residue and its N/CA/C/O atoms.
"""

from __future__ import annotations

import hashlib
import math
import os
import shlex
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    canonical_json_bytes,
    canonical_json_loads,
    validate_schema,
    validate_structure_map_snapshot_binding,
)


BACKBONE_ATOMS = ("N", "CA", "C", "O")
STANDARD_PROTEIN_RESIDUES = frozenset(
    {
        "ALA",
        "ARG",
        "ASN",
        "ASP",
        "CYS",
        "GLN",
        "GLU",
        "GLY",
        "HIS",
        "ILE",
        "LEU",
        "LYS",
        "MET",
        "PHE",
        "PRO",
        "SER",
        "THR",
        "TRP",
        "TYR",
        "VAL",
    }
)
THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
ONE_TO_THREE = {one: three for three, one in THREE_TO_ONE.items()} | {"X": "UNK"}
PDB_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
NORMALIZER_VERSION = "cm_structure_normalizer_v1"

_ATOM_SITE_COLUMNS = (
    "group_PDB",
    "id",
    "type_symbol",
    "label_atom_id",
    "label_alt_id",
    "label_comp_id",
    "label_asym_id",
    "label_entity_id",
    "label_seq_id",
    "pdbx_PDB_ins_code",
    "Cartn_x",
    "Cartn_y",
    "Cartn_z",
    "occupancy",
    "B_iso_or_equiv",
    "auth_seq_id",
    "auth_comp_id",
    "auth_asym_id",
    "auth_atom_id",
    "pdbx_PDB_model_num",
)


class StructureMapError(ValueError):
    """The source cannot be normalized without losing or guessing identity."""


@dataclass(frozen=True)
class _AtomSite:
    source_id: str
    record_type: str
    element: str
    atom_name: str
    auth_atom_name: str
    altloc: str
    residue_name: str
    auth_residue_name: str
    label_asym_id: str
    auth_asym_id: str
    source_entity_id: str
    label_seq_id: int
    auth_seq_id: int
    insertion_code: str
    model: int
    x: float
    y: float
    z: float
    occupancy: float
    b_factor: float
    entity_instance_id: str
    instance_order: int
    sequence: str
    authoritative_residue_names: tuple[str, ...]


@dataclass(frozen=True)
class _Residue:
    label_asym_id: str
    auth_asym_id: str
    source_entity_id: str
    label_seq_id: int
    auth_seq_id: int
    insertion_code: str
    residue_name: str
    entity_instance_id: str
    instance_order: int
    sequence: str
    authoritative_residue_names: tuple[str, ...]
    atoms: tuple[_AtomSite, ...]


@dataclass(frozen=True)
class _AuthorizedInstance:
    source_entity_id: str
    entity_instance_id: str
    label_asym_id: str
    auth_asym_id: str
    sequence: str
    authoritative_residue_names: tuple[str, ...]
    order: int


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mmcif_tokens(text: str) -> list[str]:
    """Tokenize CIF 1.1 text, including quoted and semicolon text values."""

    tokens: list[str] = []
    lines = text.splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if line.startswith(";"):
            block: list[str] = [line[1:]]
            line_index += 1
            while line_index < len(lines) and not lines[line_index].startswith(";"):
                block.append(lines[line_index])
                line_index += 1
            if line_index == len(lines):
                raise StructureMapError("unterminated mmCIF semicolon text field")
            tokens.append("\n".join(block))
            line_index += 1
            continue
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens.extend(lexer)
        except ValueError as exc:
            raise StructureMapError(f"invalid mmCIF quoting on line {line_index + 1}: {exc}") from exc
        line_index += 1
    return tokens


def _is_cif_control(token: str) -> bool:
    lowered = token.lower()
    return (
        lowered == "loop_"
        or lowered == "stop_"
        or lowered.startswith("data_")
        or lowered.startswith("save_")
        or token.startswith("_")
    )


def _atom_site_table(source_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StructureMapError(f"mmCIF source is not UTF-8: {exc}") from exc
    tokens = _mmcif_tokens(text)
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
            raise StructureMapError("mmCIF loop has no tags")
        if len(values) % len(tags):
            raise StructureMapError("mmCIF loop value cardinality does not match its tags")
        if any(tag.startswith("_atom_site.") for tag in tags):
            if not all(tag.startswith("_atom_site.") for tag in tags):
                raise StructureMapError("mixed-category atom_site loop is unsupported")
            rows = [values[offset : offset + len(tags)] for offset in range(0, len(values), len(tags))]
            found.append((tags, rows))
    if len(found) != 1:
        raise StructureMapError(f"expected exactly one atom_site loop, found {len(found)}")
    return found[0]


def _required(value: str, field: str) -> str:
    if value in {".", "?", ""}:
        raise StructureMapError(f"atom_site.{field} is required for honest source identity")
    return value


def _optional_code(value: str, field: str) -> str:
    if value in {".", "?", ""}:
        return ""
    if len(value) != 1:
        raise StructureMapError(f"atom_site.{field} cannot be represented without truncation: {value!r}")
    return value


def _integer(value: str, field: str, *, minimum: int | None = None) -> int:
    value = _required(value, field)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise StructureMapError(f"atom_site.{field} is not an integer: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise StructureMapError(f"atom_site.{field} must be >= {minimum}: {parsed}")
    return parsed


def _finite_float(value: str, field: str) -> float:
    value = _required(value, field)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise StructureMapError(f"atom_site.{field} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise StructureMapError(f"atom_site.{field} must be finite")
    return parsed


def validate_coordinate_mmcif(
    source_bytes: bytes, *, expected_sequence: str, expected_chain_id: str
) -> None:
    """Parse and validate a single-chain coordinate-bearing candidate mmCIF."""

    if not source_bytes:
        raise StructureMapError("coordinate mmCIF is empty")
    if not expected_sequence or not set(expected_sequence).issubset(set(ONE_TO_THREE)):
        raise StructureMapError("expected coordinate sequence is invalid")
    if not expected_chain_id:
        raise StructureMapError("expected coordinate chain identity is empty")
    tags, raw_rows = _atom_site_table(source_bytes)
    if not raw_rows:
        raise StructureMapError("coordinate mmCIF has no atom_site rows")
    names = [tag.removeprefix("_atom_site.") for tag in tags]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise StructureMapError(f"duplicate atom_site tags: {sorted(duplicates)}")
    missing = sorted(set(_ATOM_SITE_COLUMNS) - set(names))
    if missing:
        raise StructureMapError(f"coordinate mmCIF identity columns are missing: {missing}")
    positions = {name: names.index(name) for name in names}
    source_ids: set[str] = set()
    models: set[int] = set()
    observed_residues: dict[int, str] = {}
    for row_number, raw in enumerate(raw_rows, start=1):
        def get(name: str) -> str:
            return raw[positions[name]]

        source_id = _required(get("id"), "id")
        if source_id in source_ids:
            raise StructureMapError(f"coordinate mmCIF duplicates atom_site.id {source_id!r}")
        source_ids.add(source_id)
        record_type = _required(get("group_PDB"), "group_PDB").upper()
        if record_type not in {"ATOM", "HETATM"}:
            raise StructureMapError(
                f"coordinate mmCIF row {row_number} has unsupported group_PDB {record_type!r}"
            )
        _required(get("type_symbol"), "type_symbol")
        _required(get("label_atom_id"), "label_atom_id")
        _required(get("auth_atom_id"), "auth_atom_id")
        label_chain = _required(get("label_asym_id"), "label_asym_id")
        auth_chain = _required(get("auth_asym_id"), "auth_asym_id")
        if label_chain != expected_chain_id or auth_chain != expected_chain_id:
            raise StructureMapError(
                "coordinate mmCIF chain identity does not match canonical request"
            )
        label_seq_id = _integer(get("label_seq_id"), "label_seq_id", minimum=1)
        if label_seq_id > len(expected_sequence):
            raise StructureMapError(
                "coordinate mmCIF residue position exceeds canonical sequence"
            )
        residue_name = _required(get("label_comp_id"), "label_comp_id").upper()
        auth_residue_name = _required(get("auth_comp_id"), "auth_comp_id").upper()
        if residue_name != auth_residue_name:
            raise StructureMapError("coordinate mmCIF label/auth residue identity disagrees")
        expected_name = ONE_TO_THREE[expected_sequence[label_seq_id - 1]]
        if expected_name != "UNK" and residue_name != expected_name:
            raise StructureMapError(
                "coordinate mmCIF residue identity does not match canonical sequence"
            )
        previous_name = observed_residues.setdefault(label_seq_id, residue_name)
        if previous_name != residue_name:
            raise StructureMapError("coordinate mmCIF residue identity is ambiguous")
        _optional_code(get("label_alt_id"), "label_alt_id")
        _optional_code(get("pdbx_PDB_ins_code"), "pdbx_PDB_ins_code")
        _integer(get("auth_seq_id"), "auth_seq_id")
        models.add(_integer(get("pdbx_PDB_model_num"), "pdbx_PDB_model_num", minimum=1))
        _finite_float(get("Cartn_x"), "Cartn_x")
        _finite_float(get("Cartn_y"), "Cartn_y")
        _finite_float(get("Cartn_z"), "Cartn_z")
        occupancy = _finite_float(get("occupancy"), "occupancy")
        if not 0.0 <= occupancy <= 1.0:
            raise StructureMapError("coordinate mmCIF occupancy is outside [0, 1]")
        _finite_float(get("B_iso_or_equiv"), "B_iso_or_equiv")
    if len(models) != 1:
        raise StructureMapError("coordinate mmCIF must contain exactly one model")
    expected_positions = set(range(1, len(expected_sequence) + 1))
    if set(observed_residues) != expected_positions:
        raise StructureMapError(
            "coordinate mmCIF does not contain every canonical sequence residue"
        )


def _authorized_protein_instances(
    snapshot: Mapping[str, Any], *, target_id: str, candidate_id: str
) -> list[_AuthorizedInstance]:
    try:
        validate_schema("cm_complex_snapshot_v1", snapshot)
    except Exception as exc:
        raise StructureMapError(f"authoritative complex snapshot is invalid: {exc}") from exc
    if snapshot["target_id"] != target_id:
        raise StructureMapError("complex snapshot target_id does not match normalization target")
    entities = {entity["source_entity_id"]: entity for entity in snapshot["entities"]}
    mapping_by_source = {
        (mapping["source_entity_id"], mapping["source_instance_id"]): mapping
        for mapping in snapshot["instance_mappings"]
        if mapping["candidate_id"] == candidate_id
    }
    contexts: list[_AuthorizedInstance] = []
    instance_order = 0
    for entity in snapshot["entities"]:
        modification_by_position = {
            modification["position"]: modification["modification"]
            for modification in entity.get("modifications", [])
        }
        for instance_id in entity["ordered_instance_ids"]:
            if entity["entity_type"] != "protein":
                continue
            mapping = mapping_by_source.get((entity["source_entity_id"], instance_id))
            if mapping is None:
                raise StructureMapError(
                    "complex snapshot has no candidate output mapping for an authorized protein instance"
                )
            contexts.append(
                _AuthorizedInstance(
                    source_entity_id=entity["source_entity_id"],
                    entity_instance_id=instance_id,
                    label_asym_id=mapping["output_label_asym_id"],
                    auth_asym_id=mapping["output_auth_asym_id"],
                    sequence=entity["sequence"],
                    authoritative_residue_names=tuple(
                        modification_by_position.get(index, ONE_TO_THREE[letter])
                        for index, letter in enumerate(entity["sequence"], start=1)
                    ),
                    order=instance_order,
                )
            )
            instance_order += 1
    if not contexts:
        raise StructureMapError("complex snapshot authorizes no protein instances for normalization")
    identities = [
        (context.source_entity_id, context.label_asym_id, context.auth_asym_id)
        for context in contexts
    ]
    if len(set(identities)) != len(identities):
        raise StructureMapError("complex snapshot protein output identity is ambiguous")
    if len({context.label_asym_id for context in contexts}) != len(contexts):
        raise StructureMapError("complex snapshot reuses a protein output label_asym_id")
    if any(context.source_entity_id not in entities for context in contexts):
        raise StructureMapError("complex snapshot instance references an unknown entity")
    return contexts


def _parse_atom_sites(
    source_bytes: bytes, contexts: Sequence[_AuthorizedInstance]
) -> list[_AtomSite]:
    tags, raw_rows = _atom_site_table(source_bytes)
    names = [tag.removeprefix("_atom_site.") for tag in tags]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise StructureMapError(f"duplicate atom_site tags: {sorted(duplicates)}")
    missing = sorted(set(_ATOM_SITE_COLUMNS) - set(names))
    if missing:
        raise StructureMapError(f"atom_site identity columns are missing: {missing}")
    positions = {name: names.index(name) for name in names}
    context_by_output = {
        (context.source_entity_id, context.label_asym_id, context.auth_asym_id): context
        for context in contexts
    }

    atoms: list[_AtomSite] = []
    source_ids: set[str] = set()
    for row_number, raw in enumerate(raw_rows, start=1):
        def get(name: str) -> str:
            return raw[positions[name]]

        source_entity_id = _required(get("label_entity_id"), "label_entity_id")
        label_asym_id = _required(get("label_asym_id"), "label_asym_id")
        auth_asym_id = _required(get("auth_asym_id"), "auth_asym_id")
        context = context_by_output.get((source_entity_id, label_asym_id, auth_asym_id))
        if context is None:
            # Complete-complex mmCIF is allowed. Non-authorized partner proteins,
            # ligands, ions, waters, and nucleic acids are intentionally not
            # projected into the single-chain FrustraMPNN PDB boundary.
            continue
        source_id = _required(get("id"), "id")
        if source_id in source_ids:
            raise StructureMapError(f"duplicate atom_site.id is ambiguous: {source_id!r}")
        source_ids.add(source_id)
        record_type = _required(get("group_PDB"), "group_PDB").upper()
        if record_type not in {"ATOM", "HETATM"}:
            raise StructureMapError(f"unsupported atom_site.group_PDB on row {row_number}: {record_type!r}")
        atom_name = _required(get("label_atom_id"), "label_atom_id")
        auth_atom_name = _required(get("auth_atom_id"), "auth_atom_id")
        residue_name = _required(get("label_comp_id"), "label_comp_id").upper()
        auth_residue_name = _required(get("auth_comp_id"), "auth_comp_id").upper()
        if residue_name != auth_residue_name:
            raise StructureMapError(
                f"label/auth residue names disagree for atom_site.id {source_id}: "
                f"{residue_name!r} != {auth_residue_name!r}"
            )
        element = _required(get("type_symbol"), "type_symbol").upper()
        if len(element) > 2:
            raise StructureMapError(f"PDB element field cannot represent {element!r}")
        if len(atom_name) > 4:
            raise StructureMapError(f"PDB atom name cannot represent {atom_name!r}")
        if len(residue_name) > 3:
            raise StructureMapError(f"PDB residue name cannot represent {residue_name!r}")
        occupancy = _finite_float(get("occupancy"), "occupancy")
        if not 0.0 <= occupancy <= 1.0:
            raise StructureMapError(f"atom_site.occupancy is outside [0, 1]: {occupancy}")
        atoms.append(
            _AtomSite(
                source_id=source_id,
                record_type=record_type,
                element=element,
                atom_name=atom_name,
                auth_atom_name=auth_atom_name,
                altloc=_optional_code(get("label_alt_id"), "label_alt_id"),
                residue_name=residue_name,
                auth_residue_name=auth_residue_name,
                label_asym_id=label_asym_id,
                auth_asym_id=auth_asym_id,
                source_entity_id=source_entity_id,
                label_seq_id=_integer(get("label_seq_id"), "label_seq_id", minimum=1),
                auth_seq_id=_integer(get("auth_seq_id"), "auth_seq_id"),
                insertion_code=_optional_code(get("pdbx_PDB_ins_code"), "pdbx_PDB_ins_code"),
                model=_integer(get("pdbx_PDB_model_num"), "pdbx_PDB_model_num", minimum=1),
                x=_finite_float(get("Cartn_x"), "Cartn_x"),
                y=_finite_float(get("Cartn_y"), "Cartn_y"),
                z=_finite_float(get("Cartn_z"), "Cartn_z"),
                occupancy=occupancy,
                b_factor=_finite_float(get("B_iso_or_equiv"), "B_iso_or_equiv"),
                entity_instance_id=context.entity_instance_id,
                instance_order=context.order,
                sequence=context.sequence,
                authoritative_residue_names=context.authoritative_residue_names,
            )
        )
    if not atoms:
        raise StructureMapError("atom_site loop contains no coordinate rows")
    return atoms


def _select_model(atoms: list[_AtomSite], source_model: int | None) -> tuple[int, str, list[_AtomSite]]:
    models = sorted({atom.model for atom in atoms})
    if source_model is None:
        if len(models) != 1:
            raise StructureMapError(
                f"multiple source models {models} require explicit source_model selection"
            )
        selected = models[0]
        decision = f"only_source_model:{selected}"
    else:
        if isinstance(source_model, bool) or not isinstance(source_model, int) or source_model < 1:
            raise StructureMapError("source_model must be an integer >= 1")
        if source_model not in models:
            raise StructureMapError(f"requested source model {source_model} is absent; available={models}")
        selected = source_model
        decision = f"explicit_source_model:{selected}"
    return selected, decision, [atom for atom in atoms if atom.model == selected]


def _residues(atoms: Iterable[_AtomSite]) -> list[_Residue]:
    grouped: dict[tuple[str, int], list[_AtomSite]] = {}
    for atom in atoms:
        grouped.setdefault((atom.label_asym_id, atom.label_seq_id), []).append(atom)
    residues: list[_Residue] = []
    asym_identity: dict[str, tuple[str, str]] = {}
    for (label_asym_id, label_seq_id), members in grouped.items():
        identity_values = {
            (
                atom.auth_asym_id,
                atom.source_entity_id,
                atom.auth_seq_id,
                atom.insertion_code,
                atom.residue_name,
                atom.entity_instance_id,
                atom.instance_order,
                atom.sequence,
                atom.authoritative_residue_names,
            )
            for atom in members
        }
        if len(identity_values) != 1:
            raise StructureMapError(
                f"ambiguous residue identity for label_asym_id={label_asym_id!r}, "
                f"label_seq_id={label_seq_id}"
            )
        (
            auth_asym_id, source_entity_id, auth_seq_id, insertion_code, residue_name,
            entity_instance_id, instance_order, sequence,
            authoritative_residue_names,
        ) = next(
            iter(identity_values)
        )
        chain_identity = (auth_asym_id, source_entity_id)
        previous = asym_identity.setdefault(label_asym_id, chain_identity)
        if previous != chain_identity:
            raise StructureMapError(f"label_asym_id {label_asym_id!r} has conflicting chain identity")
        residues.append(
            _Residue(
                label_asym_id=label_asym_id,
                auth_asym_id=auth_asym_id,
                source_entity_id=source_entity_id,
                label_seq_id=label_seq_id,
                auth_seq_id=auth_seq_id,
                insertion_code=insertion_code,
                residue_name=residue_name,
                entity_instance_id=entity_instance_id,
                instance_order=instance_order,
                sequence=sequence,
                authoritative_residue_names=authoritative_residue_names,
                atoms=tuple(members),
            )
        )
    return sorted(
        residues,
        key=lambda residue: (
            residue.instance_order,
            residue.label_seq_id,
            residue.auth_seq_id,
            residue.insertion_code,
        ),
    )


def _chain_map(residues: Iterable[_Residue]) -> dict[str, str]:
    source_chains = list(
        dict.fromkeys(
            residue.label_asym_id
            for residue in sorted(residues, key=lambda residue: residue.instance_order)
        )
    )
    if len(source_chains) > len(PDB_CHAIN_IDS):
        raise StructureMapError(
            f"PDB one-character chain field supports {len(PDB_CHAIN_IDS)} instances, "
            f"source has {len(source_chains)}"
        )
    return {source: PDB_CHAIN_IDS[index] for index, source in enumerate(source_chains)}


def _select_atoms(residue: _Residue, selected_altloc: str) -> tuple[list[_AtomSite], str]:
    by_name: dict[str, list[_AtomSite]] = {}
    for atom in residue.atoms:
        by_name.setdefault(atom.atom_name, []).append(atom)
    selected: list[_AtomSite] = []
    has_alternates = any(atom.altloc for atom in residue.atoms)
    for atom_name, candidates in by_name.items():
        preferred = [atom for atom in candidates if atom.altloc == selected_altloc and selected_altloc]
        blank = [atom for atom in candidates if not atom.altloc]
        chosen = preferred if preferred else blank
        if len(chosen) > 1:
            ids = sorted(atom.source_id for atom in chosen)
            raise StructureMapError(
                f"ambiguous atom identity for {residue.label_asym_id}:{residue.label_seq_id}:"
                f"{atom_name}; atom_site.ids={ids}"
            )
        if chosen:
            selected.append(chosen[0])
    backbone_order = {name: index for index, name in enumerate(BACKBONE_ATOMS)}
    selected.sort(
        key=lambda atom: (
            backbone_order.get(atom.atom_name, len(BACKBONE_ATOMS)),
            atom.atom_name,
            atom.source_id,
        )
    )
    return selected, selected_altloc if has_alternates else ""


def _pdb_field(value: float, width: int, precision: int, field: str) -> str:
    rendered = f"{value:{width}.{precision}f}"
    if len(rendered) > width:
        raise StructureMapError(f"PDB {field} field overflow: {value}")
    return rendered


def _pdb_atom_name(atom_name: str, element: str) -> str:
    if len(atom_name) > 4:
        raise StructureMapError(f"PDB atom name cannot represent {atom_name!r}")
    if len(atom_name) == 4:
        return atom_name
    if len(element) == 1 and atom_name and atom_name[0].isalpha():
        return f" {atom_name:<3}"
    return f"{atom_name:>4}"


def _pdb_atom_line(
    atom: _AtomSite, *, serial: int, chain_id: str, residue_id: int, insertion_code: str
) -> str:
    if not 1 <= serial <= 99999:
        raise StructureMapError(f"PDB atom serial field overflow: {serial}")
    if not -999 <= residue_id <= 9999:
        raise StructureMapError(f"PDB residue number field cannot represent {residue_id}")
    record = "HETATM" if atom.record_type == "HETATM" else "ATOM  "
    line = (
        f"{record}{serial:5d} "
        f"{_pdb_atom_name(atom.atom_name, atom.element)} "
        f"{atom.residue_name:>3} {chain_id}{residue_id:4d}{insertion_code or ' '}"
        f"   {_pdb_field(atom.x, 8, 3, 'x coordinate')}"
        f"{_pdb_field(atom.y, 8, 3, 'y coordinate')}"
        f"{_pdb_field(atom.z, 8, 3, 'z coordinate')}"
        f"{_pdb_field(atom.occupancy, 6, 2, 'occupancy')}"
        f"{_pdb_field(atom.b_factor, 6, 2, 'B factor')}"
        f"          {atom.element:>2}  "
    )
    if len(line) != 80:
        raise StructureMapError(f"internal PDB field-width error produced {len(line)} columns")
    return line + "\n"


def _render(
    residues: list[_Residue],
    *,
    model: int,
    model_decision: str,
    selected_altloc: str,
) -> tuple[bytes, list[dict[str, Any]]]:
    chains = _chain_map(residues)
    serial = 1
    lines: list[str] = []
    rows: list[dict[str, Any]] = []
    for residue in residues:
        if not 1 <= residue.label_seq_id <= len(residue.sequence):
            raise StructureMapError(
                f"label_seq_id {residue.label_seq_id} exceeds authoritative instance sequence"
            )
        if not -999 <= residue.auth_seq_id <= 9999:
            raise StructureMapError(
                f"PDB residue number field cannot represent auth_seq_id {residue.auth_seq_id}"
            )
        atoms, altloc_decision = _select_atoms(residue, selected_altloc)
        atom_names = [atom.atom_name for atom in atoms]
        if len(set(atom_names)) != len(atom_names):
            raise StructureMapError(
                f"normalized PDB atom-name collision for {residue.label_asym_id}:{residue.label_seq_id}"
            )
        backbone = {
            name: next((atom.source_id for atom in atoms if atom.atom_name == name), None)
            for name in BACKBONE_ATOMS
        }
        missing = [name for name in BACKBONE_ATOMS if backbone[name] is None]
        expected_residue_name = residue.authoritative_residue_names[residue.label_seq_id - 1]
        if residue.residue_name != expected_residue_name:
            raise StructureMapError(
                "mmCIF residue identity disagrees with authoritative protein sequence/identity"
            )
        if residue.residue_name not in STANDARD_PROTEIN_RESIDUES:
            status = "nonstandard_residue"
            reason = f"nonstandard protein residue: {residue.residue_name}"
        elif missing:
            status = "missing_backbone"
            reason = f"missing required backbone atoms: {', '.join(missing)}"
        else:
            status = "mapped"
            reason = None
        chain_id = chains[residue.label_asym_id]
        for atom in atoms:
            lines.append(
                _pdb_atom_line(
                    atom,
                    serial=serial,
                    chain_id=chain_id,
                    residue_id=residue.auth_seq_id,
                    insertion_code=residue.insertion_code,
                )
            )
            serial += 1
        rows.append(
            {
                "entity_instance_id": residue.entity_instance_id,
                "source_entity_id": residue.source_entity_id,
                "source_model": model,
                "label_asym_id": residue.label_asym_id,
                "auth_asym_id": residue.auth_asym_id,
                "label_seq_id": residue.label_seq_id,
                "auth_seq_id": residue.auth_seq_id,
                "insertion_code": residue.insertion_code,
                "residue_name": residue.residue_name,
                "sequence_index": residue.label_seq_id,
                "pdb_chain_id": chain_id,
                "pdb_residue_id": residue.auth_seq_id,
                "pdb_insertion_code": residue.insertion_code,
                "backbone_atoms": backbone,
                "selected_altloc": altloc_decision,
                "model_decision": model_decision,
                "status": status,
                "reason": reason,
            }
        )
    if not lines:
        raise StructureMapError("normalization selected no coordinate atoms")
    lines.append("END\n")
    return "".join(lines).encode("ascii"), rows


def _validate_authoritative_residue_coverage(
    residues: Sequence[_Residue], contexts: Sequence[_AuthorizedInstance]
) -> None:
    observed: dict[str, set[int]] = {}
    for residue in residues:
        positions = observed.setdefault(residue.entity_instance_id, set())
        if residue.label_seq_id in positions:
            raise StructureMapError(
                "authoritative sequence position appears more than once within an instance"
            )
        positions.add(residue.label_seq_id)
    missing = {
        context.entity_instance_id: sorted(
            set(range(1, len(context.authoritative_residue_names) + 1))
            - observed.get(context.entity_instance_id, set())
        )
        for context in contexts
        if set(range(1, len(context.authoritative_residue_names) + 1))
        != observed.get(context.entity_instance_id, set())
    }
    if missing:
        raise StructureMapError(f"authoritative residues are absent from selected source model: {missing}")


def validate_rendered_pdb_mapping(
    pdb_bytes: bytes, rows: Sequence[Mapping[str, Any]]
) -> None:
    """Independently parse PDB columns and prove residue/backbone bijection."""

    try:
        text = pdb_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StructureMapError("rendered PDB is not ASCII") from exc
    observed: dict[tuple[str, int, str], Counter[str]] = {}
    saw_end = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line == "END":
            saw_end = True
            continue
        if not line.startswith(("ATOM  ", "HETATM")):
            raise StructureMapError(f"rendered PDB has unexpected record on line {line_number}")
        if len(line) != 80:
            raise StructureMapError("rendered PDB fixed-width record is not 80 columns")
        try:
            residue_id = int(line[22:26])
        except ValueError as exc:
            raise StructureMapError("rendered PDB residue number is malformed") from exc
        key = (line[21], residue_id, line[26].strip())
        atom_name = line[12:16].strip()
        observed.setdefault(key, Counter())[atom_name] += 1
    if not saw_end:
        raise StructureMapError("rendered PDB is missing END")
    expected_keys: set[tuple[str, int, str]] = set()
    for row in rows:
        key = (row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"])
        if key in expected_keys:
            raise StructureMapError("rendered PDB mapping bijection has duplicate row identity")
        expected_keys.add(key)
        atom_counts = observed.get(key)
        if atom_counts is None:
            raise StructureMapError("rendered PDB mapping bijection is missing a mapped residue")
        for atom_name, source_id in row["backbone_atoms"].items():
            expected_count = 1 if source_id is not None else 0
            if atom_counts[atom_name] != expected_count:
                raise StructureMapError(
                    "rendered PDB mapping bijection disagrees with backbone source identities"
                )
    if set(observed) != expected_keys:
        raise StructureMapError("rendered PDB mapping bijection contains an unreferenced residue")


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                raise StructureMapError(f"input symlink is forbidden: {current}")
        except OSError as exc:
            raise StructureMapError(f"cannot inspect input path component {current}: {exc}") from exc


def _read_regular_file_no_follow(path: Path, *, purpose: str) -> bytes:
    _reject_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StructureMapError(f"cannot open {purpose} without following links: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StructureMapError(f"{purpose} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_authoritative_complex_snapshot(path: Path | str) -> dict[str, Any]:
    payload = canonical_json_loads(
        _read_regular_file_no_follow(Path(path), purpose="authoritative complex snapshot")
    )
    if not isinstance(payload, dict):
        raise StructureMapError("authoritative complex snapshot must be a JSON object")
    return payload


def _stage_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _write_outputs(output_pdb_path: Path, pdb_bytes: bytes, map_path: Path, map_bytes: bytes) -> None:
    staged: list[Path] = []
    previous: dict[Path, bytes | None] = {}
    published: list[Path] = []
    try:
        for destination in (output_pdb_path, map_path):
            if destination.is_symlink():
                raise StructureMapError(f"output symlink is forbidden: {destination}")
            previous[destination] = destination.read_bytes() if destination.exists() else None
        staged_pdb = _stage_file(output_pdb_path, pdb_bytes)
        staged.append(staged_pdb)
        staged_map = _stage_file(map_path, map_bytes)
        staged.append(staged_map)
        os.replace(staged_pdb, output_pdb_path)
        staged.remove(staged_pdb)
        published.append(output_pdb_path)
        os.replace(staged_map, map_path)
        staged.remove(staged_map)
        published.append(map_path)
    except Exception:
        rollback_errors: list[str] = []
        for destination in reversed(published):
            try:
                old_bytes = previous[destination]
                if old_bytes is None:
                    destination.unlink(missing_ok=True)
                else:
                    restore = _stage_file(destination, old_bytes)
                    try:
                        os.replace(restore, destination)
                    finally:
                        restore.unlink(missing_ok=True)
            except Exception as exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{destination}: {exc}")
        if rollback_errors:
            raise StructureMapError(
                f"paired publication failed and rollback was incomplete: {rollback_errors}"
            )
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def normalize_conformational_mapping_structure(
    *,
    input_path: Path | str,
    output_pdb_path: Path | str,
    map_path: Path | str,
    target_id: str,
    candidate_id: str,
    complex_snapshot: Mapping[str, Any] | None = None,
    source_model: int | None = None,
    selected_altloc: str = "A",
) -> dict[str, Any]:
    """Normalize authorized protein instances from one authoritative complex mmCIF."""

    source = Path(input_path)
    output = Path(output_pdb_path)
    sidecar = Path(map_path)
    if not isinstance(target_id, str) or not target_id:
        raise StructureMapError("target_id must be a nonempty string")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise StructureMapError("candidate_id must be a nonempty string")
    if complex_snapshot is None:
        raise StructureMapError("authoritative complex snapshot is required")
    if not isinstance(selected_altloc, str) or len(selected_altloc) > 1:
        raise StructureMapError("selected_altloc must be blank or one character")
    absolute = [Path(os.path.abspath(path)) for path in (source, output, sidecar)]
    if len(set(absolute)) != 3:
        raise StructureMapError("source, normalized PDB, and structure-map paths must be distinct")
    suffix = source.suffix.lower()
    if suffix in {".pdb", ".ent"}:
        raise StructureMapError(
            "PDB input requires explicit label/auth/entity identity metadata; refusing to guess"
        )
    if suffix not in {".cif", ".mmcif"}:
        raise StructureMapError(f"unsupported source structure format: {suffix or '<none>'}")

    contexts = _authorized_protein_instances(
        complex_snapshot, target_id=target_id, candidate_id=candidate_id
    )
    source_bytes = _read_regular_file_no_follow(source, purpose="source structure")
    source_sha256 = _sha256(source_bytes)
    atoms = _parse_atom_sites(source_bytes, contexts)
    selected_model, model_decision, selected_atoms = _select_model(atoms, source_model)
    residues = _residues(selected_atoms)
    pdb_bytes, rows = _render(
        residues,
        model=selected_model,
        model_decision=model_decision,
        selected_altloc=selected_altloc,
    )
    # Explicit representability/identity failures in observed source rows are
    # more specific than the later whole-sequence coverage contract.  In
    # particular, a source auth_seq_id that cannot fit the PDB field must not
    # be masked by other authoritative residues being absent from that source.
    _validate_authoritative_residue_coverage(residues, contexts)
    structure_map: dict[str, Any] = {
        "schema_name": "cm_structure_map",
        "schema_version": 1,
        "target_id": target_id,
        "candidate_id": candidate_id,
        "source_format": "mmcif",
        "source_sha256": source_sha256,
        "original_cif_sha256": source_sha256,
        "source_bytes": len(source_bytes),
        "normalized_pdb_sha256": _sha256(pdb_bytes),
        "selected_source_model": selected_model,
        "altloc_policy": f"blank_or_explicit:{selected_altloc or '<blank>'}",
        "normalizer_version": NORMALIZER_VERSION,
        "rows": rows,
    }
    validate_schema("cm_structure_map_v1", structure_map)
    validate_structure_map_snapshot_binding(structure_map, complex_snapshot)
    validate_rendered_pdb_mapping(pdb_bytes, rows)
    map_bytes = canonical_json_bytes(structure_map) + b"\n"
    _write_outputs(output, pdb_bytes, sidecar, map_bytes)
    return structure_map


__all__ = [
    "NORMALIZER_VERSION",
    "StructureMapError",
    "load_authoritative_complex_snapshot",
    "normalize_conformational_mapping_structure",
    "validate_coordinate_mmcif",
    "validate_rendered_pdb_mapping",
]
