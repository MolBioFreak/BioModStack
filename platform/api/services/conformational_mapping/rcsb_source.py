"""Bounded, server-authoritative RCSB context discovery and materialization."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import ContractValidationError
from .import_snapshot import (
    ImportSnapshotError,
    _ATOM_SITE_FIELDS,
    _category_rows,
    _parse_document,
    build_import_snapshot_from_mmcif,
)


class RcsbSourceError(ValueError):
    """The deposited RCSB mmCIF cannot satisfy the CM selection contract."""


_SAMPLE_ID = "asymmetric-unit"
_SAMPLE = {"sample_id": _SAMPLE_ID, "label": "Deposited asymmetric unit"}
_REQUIRED_SELECTION = ["model_id", "sample_id", "chain_ids", "entity_ids"]
_STANDARD_PROTEIN = frozenset("ACDEFGHIKLMNPQRSTVWY")


def _sort_model_id(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _cif_token(value: str) -> str:
    if value in {".", "?"}:
        return value
    if (
        value
        and re.fullmatch(r"[A-Za-z0-9_.+?\-]+", value)
        and value.casefold() not in {"loop_", "stop_", "global_"}
        and not value.casefold().startswith(("data_", "save_"))
        and not value.startswith("_")
    ):
        return value
    if "\n" in value or "\r" in value:
        raise RcsbSourceError("selected RCSB context contains an unsupported multiline token")
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    raise RcsbSourceError("selected RCSB context contains an unrepresentable quoted token")


def _loop(category: str, fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> list[str]:
    lines = ["loop_", *(f"_{category}.{field}" for field in fields)]
    for row in rows:
        lines.append(" ".join(_cif_token(str(row[field])) for field in fields))
    return lines


def _tables(source_bytes: bytes, *, require_materializable: bool = True):
    try:
        document = _parse_document(source_bytes)
        if require_materializable and ("struct_conn" in document.loops or any(
            key.startswith("_struct_conn.") for key in document.scalars
        )):
            raise RcsbSourceError(
                "RCSB entry contains covalent struct_conn authority unsupported by CM materialization"
            )
        entry_id = str(document.scalars.get("_entry.id") or "").strip().upper()
        entities = _category_rows(document, "entity", {"id", "type"})
        asym = _category_rows(document, "struct_asym", {"id", "entity_id"})
        polymers = _category_rows(
            document,
            "entity_poly",
            {"entity_id", "type", "pdbx_seq_one_letter_code_can"},
        )
        sequences = _category_rows(
            document,
            "entity_poly_seq",
            {"entity_id", "num", "mon_id"},
        )
        atoms = _category_rows(document, "atom_site", _ATOM_SITE_FIELDS)
        atom_fields, _ = document.loops["atom_site"]
    except (ImportSnapshotError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RcsbSourceError):
            raise
        raise RcsbSourceError(f"RCSB mmCIF authority is invalid: {exc}") from exc
    return entry_id, entities, asym, polymers, sequences, atoms, atom_fields


def _protein_candidates(
    entities: Sequence[Mapping[str, str]],
    asym: Sequence[Mapping[str, str]],
    polymers: Sequence[Mapping[str, str]],
    sequences: Sequence[Mapping[str, str]],
    atoms: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    entity_types = {str(row["id"]): str(row["type"]).casefold() for row in entities}
    polymers_by_entity = {str(row["entity_id"]): row for row in polymers}
    candidates: list[dict[str, Any]] = []
    for asym_row in asym:
        label_chain = str(asym_row["id"])
        entity_id = str(asym_row["entity_id"])
        polymer = polymers_by_entity.get(entity_id)
        if entity_types.get(entity_id) != "polymer" or polymer is None:
            continue
        if str(polymer["type"]).casefold() != "polypeptide(l)":
            continue
        sequence = "".join(str(polymer["pdbx_seq_one_letter_code_can"]).split()).upper()
        if not sequence or not set(sequence).issubset(_STANDARD_PROTEIN):
            continue
        entity_sequence_rows = [row for row in sequences if str(row["entity_id"]) == entity_id]
        atom_rows = [
            row
            for row in atoms
            if str(row["label_asym_id"]) == label_chain
            and str(row["label_entity_id"]) == entity_id
            and str(row["group_PDB"]).upper() == "ATOM"
        ]
        author_chains = {str(row["auth_asym_id"]).strip() for row in atom_rows}
        models = {str(row["pdbx_PDB_model_num"]).strip() for row in atom_rows}
        if len(author_chains) != 1 or not models:
            continue
        author_chain = next(iter(author_chains))
        if not author_chain or any(not model for model in models):
            continue
        candidates.append(
            {
                "chain_id": author_chain,
                "label_chain_id": label_chain,
                "entity_id": entity_id,
                "sequence": sequence,
                "residue_count": len(sequence),
                "sequence_rows": entity_sequence_rows,
                "models": models,
            }
        )
    duplicate_author_ids = {
        chain_id
        for chain_id in {str(item["chain_id"]) for item in candidates}
        if sum(str(item["chain_id"]) == chain_id for item in candidates) > 1
    }
    return [item for item in candidates if item["chain_id"] not in duplicate_author_ids]


def _materialize(
    accession: str,
    *,
    entity_rows: Sequence[Mapping[str, str]],
    asym_rows: Sequence[Mapping[str, str]],
    polymer_rows: Sequence[Mapping[str, str]],
    atom_fields: Sequence[str],
    atoms: Sequence[Mapping[str, str]],
    candidate: Mapping[str, Any],
    model_id: str,
) -> bytes:
    entity_id = str(candidate["entity_id"])
    label_chain = str(candidate["label_chain_id"])
    author_chain = str(candidate["chain_id"])
    selected_atoms = [
        row
        for row in atoms
        if str(row["label_asym_id"]) == label_chain
        and str(row["label_entity_id"]) == entity_id
        and str(row["auth_asym_id"]) == author_chain
        and str(row["pdbx_PDB_model_num"]) == model_id
        and str(row["group_PDB"]).upper() == "ATOM"
    ]
    if not selected_atoms:
        raise RcsbSourceError("selected RCSB model/chain/entity context has no protein coordinates")
    selected_entities = [row for row in entity_rows if str(row["id"]) == entity_id]
    selected_asym = [
        row
        for row in asym_rows
        if str(row["id"]) == label_chain and str(row["entity_id"]) == entity_id
    ]
    selected_polymer = [row for row in polymer_rows if str(row["entity_id"]) == entity_id]
    selected_sequence = list(candidate["sequence_rows"])
    if not all(len(rows) == 1 for rows in (selected_entities, selected_asym, selected_polymer)):
        raise RcsbSourceError("selected RCSB chain/entity authority is ambiguous")
    canonical_polymer = dict(selected_polymer[0])
    canonical_polymer["pdbx_seq_one_letter_code_can"] = str(candidate["sequence"])
    canonical_sequence = [
        {
            "entity_id": entity_id,
            "num": str(row["num"]),
            "mon_id": str(row["mon_id"]),
            "hetero": str(row.get("hetero", "n")),
        }
        for row in selected_sequence
    ]
    lines = [f"data_{accession}", f"_entry.id {_cif_token(accession)}"]
    lines.extend(_loop("entity", ("id", "type"), selected_entities))
    lines.extend(_loop("struct_asym", ("id", "entity_id"), selected_asym))
    lines.extend(
        _loop(
            "entity_poly",
            ("entity_id", "type", "pdbx_seq_one_letter_code_can"),
            [canonical_polymer],
        )
    )
    lines.extend(
        _loop(
            "entity_poly_seq",
            ("entity_id", "num", "mon_id", "hetero"),
            canonical_sequence,
        )
    )
    lines.extend(_loop("atom_site", atom_fields, selected_atoms))
    materialized = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        build_import_snapshot_from_mmcif(
            materialized,
            target_id=accession,
            candidate_id=f"rcsb-{accession}-{model_id}-{author_chain}",
            original_source_path=(
                f"rcsb/{accession}/asymmetric-unit/model/{model_id}/chain/{author_chain}/entity/{entity_id}"
            ),
        )
    except (ImportSnapshotError, ContractValidationError) as exc:
        raise RcsbSourceError(
            f"selected RCSB context cannot be materialized for CM import: {exc}"
        ) from exc
    return materialized


def discover_rcsb_contexts(
    accession: str,
    source_bytes: bytes,
    *,
    require_materializable: bool = True,
) -> dict[str, Any]:
    """Enumerate exact model/chain/entity contexts for the requested consumer."""

    normalized = accession.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", normalized):
        raise RcsbSourceError("RCSB accession must be exactly four letters or digits")
    entry_id, entities, asym, polymers, sequences, atoms, atom_fields = _tables(
        source_bytes,
        require_materializable=require_materializable,
    )
    if entry_id != normalized:
        raise RcsbSourceError("RCSB mmCIF entry identity does not match the requested accession")
    candidates = _protein_candidates(entities, asym, polymers, sequences, atoms)
    valid_pairs: set[tuple[str, str]] = set()
    for candidate in candidates:
        for model_id in sorted(candidate["models"], key=_sort_model_id):
            if require_materializable:
                try:
                    _materialize(
                        normalized,
                        entity_rows=entities,
                        asym_rows=asym,
                        polymer_rows=polymers,
                        atom_fields=atom_fields,
                        atoms=atoms,
                        candidate=candidate,
                        model_id=model_id,
                    )
                except RcsbSourceError:
                    continue
            valid_pairs.add((model_id, str(candidate["chain_id"])))
    valid_models = sorted({model for model, _ in valid_pairs}, key=_sort_model_id)
    valid_chains = [
        candidate
        for candidate in candidates
        if valid_models
        and all((model_id, str(candidate["chain_id"])) in valid_pairs for model_id in valid_models)
    ]
    if not valid_chains and valid_models:
        valid_models = valid_models[:1]
        valid_chains = [
            candidate
            for candidate in candidates
            if (valid_models[0], str(candidate["chain_id"])) in valid_pairs
        ]
    if not valid_models or not valid_chains:
        suffix = " the CM server can materialize" if require_materializable else ""
        raise RcsbSourceError(f"RCSB entry has no protein context{suffix}")
    valid_chains.sort(key=lambda item: (str(item["chain_id"]), str(item["entity_id"])))
    entity_by_id = {str(item["entity_id"]): item for item in valid_chains}
    return {
        "models": [
            {"model_id": model_id, "label": f"Model {model_id}"}
            for model_id in valid_models
        ],
        "samples": [dict(_SAMPLE)],
        "chains": [
            {
                "chain_id": str(item["chain_id"]),
                "label": (
                    f"Author chain {item['chain_id']} (label asym {item['label_chain_id']})"
                ),
                "entity_id": str(item["entity_id"]),
                "entity_type": "protein",
                "residue_count": int(item["residue_count"]),
            }
            for item in valid_chains
        ],
        "entities": [
            {
                "entity_id": entity_id,
                "label": f"Protein entity {entity_id}",
                "entity_type": "protein",
                "residue_count": int(entity_by_id[entity_id]["residue_count"]),
            }
            for entity_id in sorted(entity_by_id)
        ],
        "required_selection": list(_REQUIRED_SELECTION),
    }


def resolve_and_materialize_rcsb_selection(
    accession: str,
    source_bytes: bytes,
    supplied: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Resolve unambiguous omissions, validate identity pairs, and emit exact bytes."""

    normalized = accession.strip().upper()
    discovery = discover_rcsb_contexts(normalized, source_bytes)

    def resolve_scalar(field: str, choices: Sequence[str]) -> str:
        raw = supplied.get(field)
        value = str(raw).strip() if raw is not None else ""
        if value:
            if value not in choices:
                raise RcsbSourceError(f"selected RCSB {field.removesuffix('_id')} is unsupported")
            return value
        if len(choices) != 1:
            raise RcsbSourceError(f"RCSB selection is ambiguous without {field}")
        return choices[0]

    model_id = resolve_scalar("model_id", [item["model_id"] for item in discovery["models"]])
    sample_id = resolve_scalar("sample_id", [item["sample_id"] for item in discovery["samples"]])
    supplied_chains = [str(value).strip() for value in supplied.get("chain_ids", [])]
    supplied_entities = [str(value).strip() for value in supplied.get("entity_ids", [])]
    if len(supplied_chains) > 1 or len(supplied_entities) > 1:
        raise RcsbSourceError("CM RCSB materialization supports exactly one chain/entity context")
    chain_id = resolve_scalar(
        "chain_id",
        [item["chain_id"] for item in discovery["chains"]],
    ) if not supplied_chains else supplied_chains[0]
    entity_id = resolve_scalar(
        "entity_id",
        [item["entity_id"] for item in discovery["entities"]],
    ) if not supplied_entities else supplied_entities[0]
    chain = next(
        (item for item in discovery["chains"] if item["chain_id"] == chain_id),
        None,
    )
    if chain is None:
        raise RcsbSourceError("selected RCSB chain is unsupported")
    if entity_id not in {item["entity_id"] for item in discovery["entities"]}:
        raise RcsbSourceError("selected RCSB entity is unsupported")
    if chain["entity_id"] != entity_id:
        raise RcsbSourceError("selected RCSB chain/entity pair is inconsistent")

    _, entities, asym, polymers, sequences, atoms, atom_fields = _tables(source_bytes)
    candidates = _protein_candidates(entities, asym, polymers, sequences, atoms)
    candidate = next(
        (
            item
            for item in candidates
            if item["chain_id"] == chain_id and item["entity_id"] == entity_id
        ),
        None,
    )
    if candidate is None:
        raise RcsbSourceError("selected RCSB chain/entity context is unavailable")
    materialized = _materialize(
        normalized,
        entity_rows=entities,
        asym_rows=asym,
        polymer_rows=polymers,
        atom_fields=atom_fields,
        atoms=atoms,
        candidate=candidate,
        model_id=model_id,
    )
    resolved = {
        "accession": normalized,
        "model_id": model_id,
        "sample_id": sample_id,
        "chain_ids": [chain_id],
        "entity_ids": [entity_id],
    }
    return discovery, resolved, materialized


__all__ = [
    "RcsbSourceError",
    "discover_rcsb_contexts",
    "resolve_and_materialize_rcsb_selection",
]
