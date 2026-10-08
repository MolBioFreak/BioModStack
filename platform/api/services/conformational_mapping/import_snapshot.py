"""Fail-closed automatic snapshot construction for imported protein mmCIF files."""

from __future__ import annotations

import hashlib
import math
import os
import shlex
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import candidate_id as canonical_candidate_id
from .contracts import canonical_sha256, validate_schema
from .structure_normalizer import (
    BACKBONE_ATOMS,
    THREE_TO_ONE,
    StructureMapError,
    validate_pdb_atom_representability,
)


class ImportSnapshotError(ValueError):
    """The imported mmCIF cannot produce a complete snapshot without guessing."""


MAX_IMPORT_MMCIF_BYTES = 64 * 1024 * 1024
MAX_IMPORT_MMCIF_TOKENS = 500_000
MAX_IMPORT_MMCIF_TOKEN_CHARS = 1 * 1024 * 1024
MAX_IMPORT_ATOM_ROWS = 20_000
_READ_CHUNK_BYTES = 1024 * 1024
_ATOM_SITE_FIELDS = {
    "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
    "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
    "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
    "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
    "auth_atom_id", "pdbx_PDB_model_num",
}
_RELEVANT_CATEGORIES = {
    "entry", "entity", "struct_asym", "entity_poly", "entity_poly_seq",
    "atom_site", "struct_conn",
}


def _required(value: str, field: str) -> str:
    if value in {"", ".", "?"}:
        raise ImportSnapshotError(f"{field} is required for automatic import identity")
    return value


def _integer(value: str, field: str, *, minimum: int | None = None) -> int:
    try:
        parsed = int(_required(value, field))
    except ValueError as exc:
        raise ImportSnapshotError(f"{field} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ImportSnapshotError(f"{field} must be >= {minimum}")
    return parsed


def _finite_float(value: str, field: str) -> float:
    try:
        parsed = float(_required(value, field))
    except ValueError as exc:
        raise ImportSnapshotError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ImportSnapshotError(f"{field} must be finite")
    return parsed


def _optional_code(value: str, field: str) -> str:
    if value in {"", ".", "?"}:
        return ""
    if len(value) != 1:
        raise ImportSnapshotError(f"{field} cannot be represented without truncation")
    return value


def _bounded_tokens(source_bytes: bytes) -> Iterator[str]:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportSnapshotError("import mmCIF must be UTF-8") from exc
    count = 0
    lines = text.splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if line.startswith(";"):
            block = [line[1:]]
            block_chars = len(block[0])
            line_index += 1
            while line_index < len(lines) and not lines[line_index].startswith(";"):
                block_chars += len(lines[line_index]) + 1
                if block_chars > MAX_IMPORT_MMCIF_TOKEN_CHARS:
                    raise ImportSnapshotError("import mmCIF contains an oversized text token")
                block.append(lines[line_index])
                line_index += 1
            if line_index == len(lines):
                raise ImportSnapshotError("import mmCIF has an unterminated text field")
            values = ["\n".join(block)]
            line_index += 1
        else:
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            try:
                values = list(lexer)
            except ValueError as exc:
                raise ImportSnapshotError(f"import mmCIF has invalid quoting on line {line_index + 1}") from exc
            line_index += 1
        for value in values:
            if len(value) > MAX_IMPORT_MMCIF_TOKEN_CHARS:
                raise ImportSnapshotError("import mmCIF contains an oversized token")
            count += 1
            if count > MAX_IMPORT_MMCIF_TOKENS:
                raise ImportSnapshotError("import mmCIF exceeds the bounded parser token limit")
            yield value


def _is_control(token: str) -> bool:
    lowered = token.lower()
    return (
        lowered in {"loop_", "stop_", "global_"}
        or lowered.startswith("data_")
        or lowered.startswith("save_")
        or token.startswith("_")
    )


class _TokenStream:
    def __init__(self, values: Iterator[str]) -> None:
        self._values = values
        self._buffer: str | None = None

    def peek(self) -> str | None:
        if self._buffer is None:
            self._buffer = next(self._values, None)
        return self._buffer

    def pop(self) -> str | None:
        value = self.peek()
        self._buffer = None
        return value


class _CifDocument:
    def __init__(self) -> None:
        self.data_block: str | None = None
        self.scalars: dict[str, str] = {}
        self.loops: dict[str, tuple[list[str], list[list[str]]]] = {}
        self.seen_tags: set[str] = set()


def _tag_parts(tag: str) -> tuple[str, str]:
    if not tag.startswith("_") or "." not in tag:
        raise ImportSnapshotError(f"invalid mmCIF tag {tag!r}")
    category, field = tag[1:].split(".", 1)
    if not category or not field:
        raise ImportSnapshotError(f"invalid mmCIF tag {tag!r}")
    return category.lower(), field


def _parse_document(source_bytes: bytes) -> _CifDocument:
    stream = _TokenStream(_bounded_tokens(source_bytes))
    document = _CifDocument()
    while (token := stream.pop()) is not None:
        lowered = token.lower()
        if lowered.startswith("data_"):
            if document.data_block is not None or len(token) == 5:
                raise ImportSnapshotError("automatic import requires exactly one named mmCIF data block")
            document.data_block = token[5:]
            continue
        if lowered in {"global_"} or lowered.startswith("save_"):
            raise ImportSnapshotError("global and save frames are unsupported for automatic import")
        if lowered == "stop_":
            continue
        if lowered == "loop_":
            tags: list[str] = []
            while (candidate := stream.peek()) is not None and candidate.startswith("_"):
                tags.append(str(stream.pop()))
            if not tags:
                raise ImportSnapshotError("mmCIF loop has no tags")
            parts = [_tag_parts(tag) for tag in tags]
            categories = {category for category, _ in parts}
            if len(categories) != 1:
                raise ImportSnapshotError("mixed-category mmCIF loops are unsupported")
            category = next(iter(categories))
            lowered_tags = [tag.lower() for tag in tags]
            if len(set(lowered_tags)) != len(lowered_tags):
                raise ImportSnapshotError(f"duplicate {category} loop tags")
            overlap = document.seen_tags.intersection(lowered_tags)
            if overlap:
                raise ImportSnapshotError(f"mmCIF tags are defined more than once: {sorted(overlap)}")
            document.seen_tags.update(lowered_tags)
            if category in document.loops or any(key.startswith(f"_{category}.") for key in document.scalars):
                raise ImportSnapshotError(f"mmCIF category {category} has ambiguous multiple definitions")
            rows: list[list[str]] = []
            row: list[str] = []
            # shlex removes quote delimiters.  A quoted mmCIF value may begin
            # with "_" (for example an audit item name), so preserve it while
            # completing an already-started row; at a row boundary, leading
            # underscores still introduce the next tag/category.
            while (
                (candidate := stream.peek()) is not None
                and (
                    not _is_control(candidate)
                    or (bool(row) and candidate.startswith("_"))
                )
            ):
                row.append(str(stream.pop()))
                if len(row) == len(tags):
                    rows.append(row)
                    row = []
                    if category == "atom_site" and len(rows) > MAX_IMPORT_ATOM_ROWS:
                        raise ImportSnapshotError("automatic import exceeds the atom-row limit")
            if row:
                raise ImportSnapshotError(f"mmCIF {category} loop value cardinality is invalid")
            if category in _RELEVANT_CATEGORIES:
                document.loops[category] = ([field for _, field in parts], rows)
            continue
        if token.startswith("_"):
            category, _ = _tag_parts(token)
            key = token.lower()
            if key in document.seen_tags:
                raise ImportSnapshotError(f"mmCIF tag is defined more than once: {token}")
            if category in document.loops:
                raise ImportSnapshotError(f"mmCIF category {category} mixes scalar and loop authority")
            value = stream.pop()
            if value is None or _is_control(value):
                raise ImportSnapshotError(f"mmCIF scalar {token} has no value")
            document.seen_tags.add(key)
            if category in _RELEVANT_CATEGORIES:
                document.scalars[key] = value
            continue
        raise ImportSnapshotError(f"unexpected mmCIF token outside a category: {token!r}")
    if document.data_block is None:
        raise ImportSnapshotError("automatic import requires exactly one named mmCIF data block")
    return document


def _category_rows(
    document: _CifDocument,
    category: str,
    required_fields: set[str],
) -> list[dict[str, str]]:
    if category in document.loops:
        fields, raw_rows = document.loops[category]
        missing = sorted(required_fields - set(fields))
        if missing:
            raise ImportSnapshotError(f"mmCIF {category} authority is missing: {', '.join(missing)}")
        return [dict(zip(fields, row, strict=True)) for row in raw_rows]
    prefix = f"_{category}."
    row = {key.removeprefix(prefix): value for key, value in document.scalars.items() if key.startswith(prefix)}
    missing = sorted(required_fields - set(row))
    if missing:
        raise ImportSnapshotError(f"mmCIF {category} authority is missing: {', '.join(missing)}")
    return [row]


def _authority(document: _CifDocument) -> tuple[str, str, str, str, list[dict[str, str]]]:
    entry_id = _required(document.scalars.get("_entry.id", ""), "entry.id")
    if "struct_conn" in document.loops or any(key.startswith("_struct_conn.") for key in document.scalars):
        raise ImportSnapshotError("covalent struct_conn records are unsupported by protein-only automatic import")
    entities = _category_rows(document, "entity", {"id", "type"})
    if len(entities) != 1 or entities[0]["type"].casefold() != "polymer":
        raise ImportSnapshotError("automatic import requires exactly one protein polymer entity")
    entity_id = _required(entities[0]["id"], "entity.id")
    asym_rows = _category_rows(document, "struct_asym", {"id", "entity_id"})
    if len(asym_rows) != 1 or asym_rows[0]["entity_id"] != entity_id:
        raise ImportSnapshotError("automatic import requires exactly one asym instance for the protein entity")
    label_chain = _required(asym_rows[0]["id"], "struct_asym.id")
    poly_rows = _category_rows(
        document,
        "entity_poly",
        {"entity_id", "type", "pdbx_seq_one_letter_code_can"},
    )
    if (
        len(poly_rows) != 1
        or poly_rows[0]["entity_id"] != entity_id
        or poly_rows[0]["type"].casefold() != "polypeptide(l)"
    ):
        raise ImportSnapshotError("automatic import requires one canonical polypeptide(L) authority")
    sequence = "".join(poly_rows[0]["pdbx_seq_one_letter_code_can"].split()).upper()
    if not sequence or not set(sequence).issubset(set(THREE_TO_ONE.values())):
        raise ImportSnapshotError("entity_poly canonical sequence is not standard protein sequence")
    seq_rows = _category_rows(document, "entity_poly_seq", {"entity_id", "num", "mon_id"})
    if len(seq_rows) != len(sequence):
        raise ImportSnapshotError("entity_poly_seq does not cover the complete canonical sequence")
    observed_sequence: list[str] = []
    for expected, row in enumerate(seq_rows, start=1):
        if row["entity_id"] != entity_id or _integer(row["num"], "entity_poly_seq.num", minimum=1) != expected:
            raise ImportSnapshotError("entity_poly_seq identity/order is not canonical")
        residue_name = _required(row["mon_id"], "entity_poly_seq.mon_id").upper()
        if residue_name not in THREE_TO_ONE:
            raise ImportSnapshotError("entity_poly_seq contains a non-standard protein residue")
        if row.get("hetero", "n").casefold() not in {"n", "no"}:
            raise ImportSnapshotError("entity_poly_seq heterogeneous residues are unsupported")
        observed_sequence.append(THREE_TO_ONE[residue_name])
    if "".join(observed_sequence) != sequence:
        raise ImportSnapshotError("entity_poly_seq disagrees with the canonical polymer sequence")
    return entry_id, entity_id, label_chain, sequence, _category_rows(document, "atom_site", _ATOM_SITE_FIELDS)


def build_import_snapshot_from_mmcif(
    source_bytes: bytes,
    *,
    target_id: str,
    candidate_id: str,
    original_source_path: str,
    target_order: int = 0,
) -> dict[str, Any]:
    """Derive a v1 snapshot from complete explicit protein/mmCIF authority."""

    if not source_bytes:
        raise ImportSnapshotError("import mmCIF is empty")
    if len(source_bytes) > MAX_IMPORT_MMCIF_BYTES:
        raise ImportSnapshotError("automatic import mmCIF exceeds the 64 MiB limit")
    if not target_id.strip() or not candidate_id.strip():
        raise ImportSnapshotError("target and candidate identities are required")
    document = _parse_document(source_bytes)
    _, entity_id, label_chain, sequence, atom_rows = _authority(document)
    if not atom_rows:
        raise ImportSnapshotError("automatic import requires coordinate-bearing atom_site rows")

    auth_chain: str | None = None
    models: set[int] = set()
    atom_ids: set[str] = set()
    author_residues: set[tuple[str, int, str]] = set()
    residues: dict[int, tuple[str, int, str, set[str]]] = {}
    for row_number, row in enumerate(atom_rows, start=1):
        if _required(row["group_PDB"], "atom_site.group_PDB").upper() != "ATOM":
            raise ImportSnapshotError(
                "automatic import is protein-only; HETATM and other non-protein rows are not omitted"
            )
        atom_id = _required(row["id"], "atom_site.id")
        if atom_id in atom_ids:
            raise ImportSnapshotError("mmCIF atom_site.id is duplicated")
        atom_ids.add(atom_id)
        if row["label_entity_id"] != entity_id or row["label_asym_id"] != label_chain:
            raise ImportSnapshotError("atom_site includes an entity/asym outside declared protein authority")
        current_auth_chain = _required(row["auth_asym_id"], "atom_site.auth_asym_id")
        if auth_chain is None:
            auth_chain = current_auth_chain
        elif auth_chain != current_auth_chain:
            raise ImportSnapshotError("mmCIF chain maps ambiguously to author identity")
        label_seq_id = _integer(row["label_seq_id"], "atom_site.label_seq_id", minimum=1)
        if label_seq_id > len(sequence):
            raise ImportSnapshotError("atom_site residue exceeds canonical polymer sequence")
        residue_name = _required(row["label_comp_id"], "atom_site.label_comp_id").upper()
        auth_residue_name = _required(row["auth_comp_id"], "atom_site.auth_comp_id").upper()
        expected_residue = next(name for name, letter in THREE_TO_ONE.items() if letter == sequence[label_seq_id - 1])
        if residue_name != auth_residue_name or residue_name != expected_residue:
            raise ImportSnapshotError("atom_site label/auth residue identity disagrees with polymer authority")
        auth_seq_id = _integer(row["auth_seq_id"], "atom_site.auth_seq_id")
        insertion_code = _optional_code(row["pdbx_PDB_ins_code"], "atom_site.pdbx_PDB_ins_code")
        author_identity = (current_auth_chain, auth_seq_id, insertion_code)
        atom_name = _required(row["label_atom_id"], "atom_site.label_atom_id")
        auth_atom_name = _required(row["auth_atom_id"], "atom_site.auth_atom_id")
        if not atom_name.isascii() or atom_name != atom_name.upper():
            raise ImportSnapshotError("atom_site atom identity must be uppercase ASCII")
        if auth_atom_name != atom_name:
            raise ImportSnapshotError("atom_site label/auth atom identity disagrees")
        if _optional_code(row["label_alt_id"], "atom_site.label_alt_id"):
            raise ImportSnapshotError("automatic import rejects alternate conformations instead of selecting one")
        element = _required(row["type_symbol"], "atom_site.type_symbol").upper()
        if len(element) > 2:
            raise ImportSnapshotError(f"PDB element field cannot represent {element!r}")
        if len(atom_name) > 4:
            raise ImportSnapshotError(f"PDB atom name cannot represent {atom_name!r}")
        expected_element = next((character for character in atom_name if character.isalpha()), "")
        if expected_element not in {"C", "N", "O", "S", "H"}:
            raise ImportSnapshotError("atom_site atom identity is unsupported for a standard protein")
        if element != expected_element:
            raise ImportSnapshotError("atom_site atom/element identity disagrees")
        x = _finite_float(row["Cartn_x"], "atom_site.Cartn_x")
        y = _finite_float(row["Cartn_y"], "atom_site.Cartn_y")
        z = _finite_float(row["Cartn_z"], "atom_site.Cartn_z")
        occupancy = _finite_float(row["occupancy"], "atom_site.occupancy")
        if not 0.0 <= occupancy <= 1.0:
            raise ImportSnapshotError("atom_site occupancy is outside [0, 1]")
        b_factor = _finite_float(row["B_iso_or_equiv"], "atom_site.B_iso_or_equiv")
        try:
            validate_pdb_atom_representability(
                atom_name=atom_name,
                element=element,
                residue_name=residue_name,
                residue_id=auth_seq_id,
                insertion_code=insertion_code,
                x=x,
                y=y,
                z=z,
                occupancy=occupancy,
                b_factor=b_factor,
            )
        except StructureMapError as exc:
            raise ImportSnapshotError(str(exc)) from exc
        models.add(_integer(row["pdbx_PDB_model_num"], "atom_site.pdbx_PDB_model_num", minimum=1))
        existing = residues.get(label_seq_id)
        if existing is None:
            if author_identity in author_residues:
                raise ImportSnapshotError("multiple label residues map to one author residue identity")
            author_residues.add(author_identity)
            residues[label_seq_id] = (residue_name, auth_seq_id, insertion_code, {atom_name})
        else:
            if existing[:3] != (residue_name, auth_seq_id, insertion_code):
                raise ImportSnapshotError("mmCIF author residue identity is ambiguous")
            if atom_name in existing[3]:
                raise ImportSnapshotError("mmCIF residue has duplicate atom identity")
            existing[3].add(atom_name)
    if len(models) != 1:
        raise ImportSnapshotError("automatic import requires exactly one explicit source model")
    expected_positions = list(range(1, len(sequence) + 1))
    if sorted(residues) != expected_positions:
        raise ImportSnapshotError("atom_site does not cover every canonical polymer residue")
    for position in expected_positions:
        missing = sorted(set(BACKBONE_ATOMS) - residues[position][3])
        if missing:
            raise ImportSnapshotError(
                f"automatic import requires complete protein backbone; residue {position} is missing {', '.join(missing)}"
            )
    assert auth_chain is not None
    entity = {
        "entity_type": "protein",
        "source_entity_id": entity_id,
        "count": 1,
        "ordered_instance_ids": [label_chain],
        "sequence": sequence,
    }
    mapping = {
        "source_entity_id": entity_id,
        "source_instance_id": label_chain,
        "runtime_target_id": target_id.strip(),
        "runtime_entity_id": entity_id,
        "runtime_instance_id": label_chain,
        "runtime_order": 0,
        "candidate_id": candidate_id.strip(),
        "output_entity_id": entity_id,
        "output_label_asym_id": label_chain,
        "output_auth_asym_id": auth_chain,
        "output_entity_order": 0,
    }
    snapshot: dict[str, Any] = {
        "schema_name": "cm_complex_snapshot",
        "schema_version": 1,
        "target_id": target_id.strip(),
        "target_order": target_order,
        "original_source_path": original_source_path,
        "original_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "normalized_source_sha256": "0" * 64,
        "source_model_id": str(next(iter(models))),
        "entities": [entity],
        "bonds": [],
        "instance_mappings": [mapping],
        "admission": {
            "token_count": len(sequence),
            "atom_count": len(atom_rows),
            "token_limit": 20000,
            "conversion_omissions": [],
        },
        "unsupported_fields": [],
    }
    if len(sequence) > snapshot["admission"]["token_limit"]:
        raise ImportSnapshotError("automatic import exceeds the conformational-mapping token limit")
    snapshot["normalized_source_sha256"] = normalized_import_snapshot_sha256(snapshot)
    validate_schema("cm_complex_snapshot_v1", snapshot)
    return snapshot


def normalized_import_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return canonical_sha256({
        key: value for key, value in snapshot.items() if key != "normalized_source_sha256"
    })


def read_staged_import_file_at(
    root_descriptor: int,
    relative: PurePosixPath,
    *,
    maximum_bytes: int = MAX_IMPORT_MMCIF_BYTES,
) -> bytes:
    limit_label = "64 MiB" if maximum_bytes == MAX_IMPORT_MMCIF_BYTES else f"{maximum_bytes} bytes"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptors: list[int] = []
    try:
        current = os.dup(root_descriptor)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(relative.parts[-1], leaf_flags, dir_fd=current)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ImportSnapshotError("staged import mmCIF must be a regular file")
        if before.st_size > maximum_bytes:
            raise ImportSnapshotError(f"staged import exceeds its {limit_label} byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ImportSnapshotError(f"staged import exceeds its {limit_label} byte limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ) or total != after.st_size:
            raise ImportSnapshotError("staged import mmCIF changed while it was read")
        return b"".join(chunks)
    except OSError as exc:
        raise ImportSnapshotError(f"staged import mmCIF cannot be opened safely: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_staged_import_file(
    root: Path,
    relative: PurePosixPath,
    *,
    maximum_bytes: int = MAX_IMPORT_MMCIF_BYTES,
) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise ImportSnapshotError(f"staged import root cannot be opened safely: {exc}") from exc
    try:
        return read_staged_import_file_at(
            root_descriptor, relative, maximum_bytes=maximum_bytes,
        )
    finally:
        os.close(root_descriptor)


def build_staged_import_snapshots(
    *,
    staged_root: Path,
    entries: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
    coordinates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind request-owned staged bytes to exact ordered import coordinates."""

    if not entries or len(entries) != len(targets) or len(entries) != len(coordinates):
        raise ImportSnapshotError("staged import, target, and coordinate cardinality must match")
    snapshots: list[dict[str, Any]] = []
    for index, (entry, target, coordinate) in enumerate(zip(entries, targets, coordinates, strict=True)):
        relative_text = str(entry.get("destination_relative_path") or "")
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ImportSnapshotError("staged import path is not canonical relative identity")
        source_bytes = read_staged_import_file(staged_root, relative)
        digest = hashlib.sha256(source_bytes).hexdigest()
        if digest != entry.get("source_content_sha256") or digest != coordinate.get("source_content_sha256"):
            raise ImportSnapshotError("staged import identity does not match its receipt and coordinate")
        target_id = str(target.get("target_id") or "")
        if target_id != str(coordinate.get("target_id") or ""):
            raise ImportSnapshotError("staged import target identity does not match its coordinate")
        target_order = target.get("target_order")
        if isinstance(target_order, bool) or target_order != index:
            raise ImportSnapshotError("staged import target order is not canonical")
        snapshots.append(build_import_snapshot_from_mmcif(
            source_bytes,
            target_id=target_id,
            candidate_id=canonical_candidate_id(coordinate),
            original_source_path=f"registered_import/{relative.as_posix()}",
            target_order=index,
        ))
    return snapshots
