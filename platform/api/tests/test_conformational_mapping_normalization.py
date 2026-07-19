from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import validate_schema
from services.conformational_mapping.structure_normalizer import (
    StructureMapError,
    normalize_conformational_mapping_structure,
    validate_rendered_pdb_mapping,
)


FIXTURES = (
    Path(__file__).parent
    / "fixtures"
    / "conformational_mapping"
    / "normalization"
)

TARGET_ID = "target-phase2"
CANDIDATE_ID = "candidate-phase2"
CASE_INSTANCES = {
    "authority": [("7", "authority-copy", "CHAIN_NATIVE", "AUTH_NATIVE", "A")],
    "repeated_copies": [
        ("1", "repeat-copy-left", "COPY_ALPHA", "LEFT", "G"),
        ("1", "repeat-copy-right", "COPY_BETA", "RIGHT", "G"),
    ],
    "multichar_insertion": [("3", "insertion-copy", "CHAIN_LONG", "AUTHOR_LONG", "ST")],
    "altloc": [("2", "altloc-copy", "ALT_CHAIN", "V", "V")],
    "multimodel": [("4", "model-copy", "MODEL_CHAIN", "M", "A")],
    "overflow": [("5", "overflow-copy", "OVERFLOW_CHAIN", "O", "A" * 10000)],
    "missing_backbone": [("6", "missing-copy", "MISSING_CHAIN", "G", "G")],
    "nonstandard": [("8", "modified-copy", "MOD_CHAIN", "Z", "M")],
    "ambiguous": [("9", "ambiguous-copy", "AMBIG_CHAIN", "A", "A")],
    "pdb_without_identity": [("7", "authority-copy", "CHAIN_NATIVE", "AUTH_NATIVE", "A")],
}


def _context_for(case: str) -> dict[str, object]:
    grouped: dict[str, list[tuple[str, str, str, str]]] = {}
    order: list[str] = []
    for source_entity_id, instance_id, label_asym_id, auth_asym_id, sequence in CASE_INSTANCES[case]:
        if source_entity_id not in grouped:
            grouped[source_entity_id] = []
            order.append(source_entity_id)
        grouped[source_entity_id].append((instance_id, label_asym_id, auth_asym_id, sequence))
    entities = []
    mappings = []
    output_order = 0
    for source_entity_id in order:
        members = grouped[source_entity_id]
        sequences = {member[3] for member in members}
        assert len(sequences) == 1
        entities.append(
            {
                "entity_type": "protein",
                "source_entity_id": source_entity_id,
                "count": len(members),
                "ordered_instance_ids": [member[0] for member in members],
                "sequence": next(iter(sequences)),
                **(
                    {"modifications": [{"position": 1, "modification": "MSE"}]}
                    if case == "nonstandard"
                    else {}
                ),
            }
        )
        for instance_id, label_asym_id, auth_asym_id, _sequence in members:
            mappings.append(
                {
                    "source_entity_id": source_entity_id,
                    "source_instance_id": instance_id,
                    "runtime_target_id": TARGET_ID,
                    "runtime_entity_id": f"runtime-{source_entity_id}",
                    "runtime_instance_id": f"runtime-{instance_id}",
                    "runtime_order": output_order,
                    "candidate_id": CANDIDATE_ID,
                    "output_entity_id": f"output-{source_entity_id}",
                    "output_label_asym_id": label_asym_id,
                    "output_auth_asym_id": auth_asym_id,
                    "output_entity_order": output_order,
                }
            )
            output_order += 1
    return {
        "schema_name": "cm_complex_snapshot",
        "schema_version": 1,
        "target_id": TARGET_ID,
        "target_order": 0,
        "original_source_path": f"inputs/{case}.json",
        "original_source_sha256": "a" * 64,
        "normalized_source_sha256": "b" * 64,
        "entities": entities,
        "bonds": [],
        "instance_mappings": mappings,
        "admission": {
            "token_count": sum(len(entity["sequence"]) for entity in entities),
            "atom_count": 1,
            "token_limit": 20000,
            "conversion_omissions": [],
        },
        "unsupported_fields": [],
    }


def _normalize_with_context(**kwargs: object) -> dict[str, object]:
    return normalize_conformational_mapping_structure(
        complex_snapshot=kwargs.pop("context"), **kwargs
    )


def _complete_complex_context() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "complete_complex" / "cm_complex_snapshot_v1.json").read_text(
            encoding="utf-8"
        )
    )


def _normalize(
    tmp_path: Path,
    case: str,
    *,
    source_model: int | None = None,
) -> tuple[bytes, dict[str, object]]:
    source = FIXTURES / case / "input.cif"
    output = tmp_path / f"{case}.pdb"
    map_path = tmp_path / f"{case}.cm_structure_map_v1.json"
    result = normalize_conformational_mapping_structure(
        input_path=source,
        output_pdb_path=output,
        map_path=map_path,
        target_id=TARGET_ID,
        candidate_id=CANDIDATE_ID,
        complex_snapshot=_context_for(case),
        source_model=source_model,
    )
    assert result == json.loads(map_path.read_text(encoding="utf-8"))
    validate_schema("cm_structure_map_v1", result)
    return output.read_bytes(), result


def _atom_lines(payload: bytes) -> list[str]:
    return [
        line
        for line in payload.decode("ascii").splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
    ]


def test_cm2_001_original_cif_is_authority(tmp_path: Path) -> None:
    source = FIXTURES / "authority" / "input.cif"
    before = source.read_bytes()

    pdb_bytes, structure_map = _normalize(tmp_path, "authority")

    assert source.read_bytes() == before
    source_sha256 = hashlib.sha256(before).hexdigest()
    assert structure_map["source_format"] == "mmcif"
    assert structure_map["source_sha256"] == source_sha256
    assert structure_map["original_cif_sha256"] == source_sha256
    assert structure_map["source_bytes"] == len(before)
    assert structure_map["normalized_pdb_sha256"] == hashlib.sha256(pdb_bytes).hexdigest()


def test_cm2_002_roundtrip_atom_residue_map(tmp_path: Path) -> None:
    pdb_bytes, structure_map = _normalize(tmp_path, "authority")
    row = structure_map["rows"][0]

    assert row == {
        "entity_instance_id": "authority-copy",
        "source_entity_id": "7",
        "source_model": 1,
        "label_asym_id": "CHAIN_NATIVE",
        "auth_asym_id": "AUTH_NATIVE",
        "label_seq_id": 1,
        "auth_seq_id": 42,
        "insertion_code": "",
        "residue_name": "ALA",
        "sequence_index": 1,
        "pdb_chain_id": "A",
        "pdb_residue_id": 42,
        "pdb_insertion_code": "",
        "backbone_atoms": {"N": "101", "CA": "102", "C": "103", "O": "104"},
        "selected_altloc": "",
        "model_decision": "only_source_model:1",
        "status": "mapped",
        "reason": None,
    }
    lines = _atom_lines(pdb_bytes)
    assert [line[12:16].strip() for line in lines] == ["N", "CA", "C", "O", "CB"]
    assert {(line[21], int(line[22:26])) for line in lines} == {("A", 42)}


def test_cm2_003_repeated_chain_instances(tmp_path: Path) -> None:
    pdb_bytes, structure_map = _normalize(tmp_path, "repeated_copies")
    rows = structure_map["rows"]

    assert [row["source_entity_id"] for row in rows] == ["1", "1"]
    assert [row["entity_instance_id"] for row in rows] == [
        "repeat-copy-left",
        "repeat-copy-right",
    ]
    assert [row["pdb_chain_id"] for row in rows] == ["A", "B"]
    assert [row["auth_asym_id"] for row in rows] == ["LEFT", "RIGHT"]
    assert len({row["entity_instance_id"] for row in rows}) == 2
    assert {line[21] for line in _atom_lines(pdb_bytes)} == {"A", "B"}


def test_cm2_004_multichar_asym_and_insertion(tmp_path: Path) -> None:
    _pdb_bytes, structure_map = _normalize(tmp_path, "multichar_insertion")
    rows = structure_map["rows"]

    assert [row["label_asym_id"] for row in rows] == ["CHAIN_LONG", "CHAIN_LONG"]
    assert [row["auth_asym_id"] for row in rows] == ["AUTHOR_LONG", "AUTHOR_LONG"]
    assert [row["auth_seq_id"] for row in rows] == [10, 10]
    assert [row["insertion_code"] for row in rows] == ["A", "B"]
    assert [row["pdb_chain_id"] for row in rows] == ["A", "A"]
    assert [row["pdb_residue_id"] for row in rows] == [10, 10]
    assert [row["pdb_insertion_code"] for row in rows] == ["A", "B"]


def test_cm2_005_altloc_policy(tmp_path: Path) -> None:
    pdb_bytes, structure_map = _normalize(tmp_path, "altloc")
    row = structure_map["rows"][0]
    ca_line = next(line for line in _atom_lines(pdb_bytes) if line[12:16].strip() == "CA")

    assert row["selected_altloc"] == "A"
    assert row["backbone_atoms"]["CA"] == "202"
    assert ca_line[16] == " "
    assert float(ca_line[30:38]) == pytest.approx(1.0)
    assert all("203" not in atom_id for atom_id in row["backbone_atoms"].values())


def test_cm2_006_multiple_model_policy(tmp_path: Path) -> None:
    source = FIXTURES / "multimodel" / "input.cif"
    with pytest.raises(StructureMapError, match="multiple source models.*explicit"):
        normalize_conformational_mapping_structure(
            input_path=source,
            output_pdb_path=tmp_path / "rejected.pdb",
            map_path=tmp_path / "rejected.json",
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("multimodel"),
        )
    assert not (tmp_path / "rejected.pdb").exists()
    assert not (tmp_path / "rejected.json").exists()

    pdb_bytes, structure_map = _normalize(tmp_path, "multimodel", source_model=2)
    assert structure_map["selected_source_model"] == 2
    assert structure_map["rows"][0]["source_model"] == 2
    assert structure_map["rows"][0]["model_decision"] == "explicit_source_model:2"
    n_line = next(line for line in _atom_lines(pdb_bytes) if line[12:16].strip() == "N")
    assert float(n_line[30:38]) == pytest.approx(20.0)


def test_cm2_007_numbering_overflow_fails(tmp_path: Path) -> None:
    source = FIXTURES / "overflow" / "input.cif"
    with pytest.raises(StructureMapError, match="PDB residue number.*10000"):
        normalize_conformational_mapping_structure(
            input_path=source,
            output_pdb_path=tmp_path / "overflow.pdb",
            map_path=tmp_path / "overflow.json",
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("overflow"),
        )
    assert not (tmp_path / "overflow.pdb").exists()
    assert not (tmp_path / "overflow.json").exists()


def test_cm2_008_missing_backbone_is_explicit(tmp_path: Path) -> None:
    _pdb_bytes, structure_map = _normalize(tmp_path, "missing_backbone")
    row = structure_map["rows"][0]

    assert row["status"] == "missing_backbone"
    assert row["backbone_atoms"] == {"N": "401", "CA": "402", "C": "403", "O": None}
    assert row["reason"] == "missing required backbone atoms: O"


def test_cm2_009_nonstandard_residue_status(tmp_path: Path) -> None:
    pdb_bytes, structure_map = _normalize(tmp_path, "nonstandard")
    row = structure_map["rows"][0]

    assert row["residue_name"] == "MSE"
    assert row["status"] == "nonstandard_residue"
    assert row["reason"] == "nonstandard protein residue: MSE"
    assert all(row["backbone_atoms"].values())
    assert all(line.startswith("HETATM") for line in _atom_lines(pdb_bytes))


def test_cm2_009b_completely_absent_authoritative_residue_fails_closed(
    tmp_path: Path,
) -> None:
    source = FIXTURES / "multichar_insertion" / "input.cif"
    missing_residue = tmp_path / "missing-entire-residue.cif"
    lines = source.read_text(encoding="utf-8").splitlines()
    missing_residue.write_text(
        "\n".join(
            line
            for line in lines
            if not line.startswith(("ATOM 15 ", "ATOM 16 ", "ATOM 17 ", "ATOM 18 "))
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.pdb"
    map_path = tmp_path / "map.json"

    with pytest.raises(StructureMapError, match="authoritative residues are absent.*2"):
        normalize_conformational_mapping_structure(
            input_path=missing_residue,
            output_pdb_path=output,
            map_path=map_path,
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("multichar_insertion"),
        )
    assert not output.exists()
    assert not map_path.exists()


def test_cm2_010_deterministic_mapping(tmp_path: Path) -> None:
    first_pdb, first_map = _normalize(tmp_path / "first", "altloc")
    second_pdb, second_map = _normalize(tmp_path / "second", "altloc")
    assert first_pdb == second_pdb
    assert first_map == second_map

    ambiguous = FIXTURES / "ambiguous" / "input.cif"
    with pytest.raises(StructureMapError, match="ambiguous atom identity"):
        normalize_conformational_mapping_structure(
            input_path=ambiguous,
            output_pdb_path=tmp_path / "ambiguous.pdb",
            map_path=tmp_path / "ambiguous.json",
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("ambiguous"),
        )


def test_cm2_011_requires_authoritative_instance_context(tmp_path: Path) -> None:
    source = FIXTURES / "repeated_copies" / "input.cif"
    with pytest.raises(StructureMapError, match="complex snapshot"):
        normalize_conformational_mapping_structure(
            input_path=source,
            output_pdb_path=tmp_path / "out.pdb",
            map_path=tmp_path / "map.json",
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
        )


def test_cm2_012_rejects_input_symlink(tmp_path: Path) -> None:
    source = FIXTURES / "authority" / "input.cif"
    link = tmp_path / "input.cif"
    link.symlink_to(source)
    with pytest.raises(StructureMapError, match="symlink"):
        normalize_conformational_mapping_structure(
            input_path=link,
            output_pdb_path=tmp_path / "out.pdb",
            map_path=tmp_path / "map.json",
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("authority"),
        )


def test_cm2_013_publication_rolls_back_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out.pdb"
    sidecar = tmp_path / "map.json"
    old_pdb = (FIXTURES / "rollback" / "previous.pdb").read_bytes()
    old_map = (FIXTURES / "rollback" / "previous_map.json").read_bytes()
    output.write_bytes(old_pdb)
    sidecar.write_bytes(old_map)
    real_replace = os.replace
    failed = False

    def fail_second_publication(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal failed
        if Path(destination) == sidecar and not failed:
            failed = True
            raise OSError("injected map publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_publication)
    with pytest.raises(OSError, match="injected"):
        _normalize_with_context(
            input_path=FIXTURES / "authority" / "input.cif",
            output_pdb_path=output,
            map_path=sidecar,
            target_id="target-phase2",
            candidate_id="candidate-phase2",
            context=_context_for("authority"),
        )
    assert output.read_bytes() == old_pdb
    assert sidecar.read_bytes() == old_map


def test_cm2_013b_publication_failure_leaves_no_new_partial_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "new.pdb"
    sidecar = tmp_path / "new.json"
    real_replace = os.replace
    failed = False

    def fail_map_once(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal failed
        if Path(destination) == sidecar and not failed:
            failed = True
            raise OSError("injected new-pair failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_map_once)
    with pytest.raises(OSError, match="new-pair"):
        normalize_conformational_mapping_structure(
            input_path=FIXTURES / "authority" / "input.cif",
            output_pdb_path=output,
            map_path=sidecar,
            target_id=TARGET_ID,
            candidate_id=CANDIDATE_ID,
            complex_snapshot=_context_for("authority"),
        )
    assert not output.exists()
    assert not sidecar.exists()


def test_cm2_014_rendered_pdb_is_independently_bijective(tmp_path: Path) -> None:
    pdb_bytes, structure_map = _normalize(tmp_path, "repeated_copies")
    validate_rendered_pdb_mapping(pdb_bytes, structure_map["rows"])
    tampered = pdb_bytes.replace(b" CA ", b" XX ", 1)
    with pytest.raises(StructureMapError, match="bijection"):
        validate_rendered_pdb_mapping(tampered, structure_map["rows"])

    pdb_source = FIXTURES / "pdb_without_identity" / "input.pdb"
    with pytest.raises(StructureMapError, match="PDB.*identity metadata"):
        normalize_conformational_mapping_structure(
            input_path=pdb_source,
            output_pdb_path=tmp_path / "pdb.pdb",
            map_path=tmp_path / "pdb.json",
            target_id="target-phase2",
            candidate_id="candidate-phase2",
            complex_snapshot=_context_for("pdb_without_identity"),
        )


def test_cm2_015_complete_complex_selects_authorized_proteins_in_snapshot_order(
    tmp_path: Path,
) -> None:
    output = tmp_path / "complete.pdb"
    result = normalize_conformational_mapping_structure(
        input_path=FIXTURES / "complete_complex" / "input.cif",
        output_pdb_path=output,
        map_path=tmp_path / "complete.json",
        target_id=TARGET_ID,
        candidate_id=CANDIDATE_ID,
        complex_snapshot=_complete_complex_context(),
    )
    assert [row["entity_instance_id"] for row in result["rows"]] == [
        "repeat-copy-left",
        "repeat-copy-right",
    ]
    assert [row["pdb_chain_id"] for row in result["rows"]] == ["A", "B"]
    assert {line[17:20].strip() for line in _atom_lines(output.read_bytes())} == {"GLY"}
