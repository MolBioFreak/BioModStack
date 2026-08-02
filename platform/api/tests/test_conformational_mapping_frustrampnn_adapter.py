from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import canonical_sha256, validate_schema
from services.frustrampnn.analysis import finalize_landscape as finalize_neutral_landscape
from services.frustrampnn.contracts import AA_ORDER


def _pdb_atom(serial: int, atom: str, residue: str, chain: str, number: int) -> str:
    element = next(character for character in atom if character.isalpha())
    atom_field = atom if len(atom) == 4 else f" {atom:<3}"
    return (
        f"ATOM  {serial:5d} {atom_field} {residue:>3} {chain}{number:4d} "
        f"   {float(serial):8.3f}{float(serial + 1):8.3f}{float(serial + 2):8.3f}"
        f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
    )


def _pdb() -> bytes:
    lines: list[str] = []
    serial = 1
    for residue, number in (("GLY", 10), ("ALA", 12)):
        for atom in ("N", "CA", "C", "O"):
            lines.append(_pdb_atom(serial, atom, residue, "X", number))
            serial += 1
    return ("".join(lines) + "END\n").encode("ascii")


def _snapshot(source_sha256: str) -> dict:
    snapshot = {
        "schema_name": "cm_complex_snapshot", "schema_version": 1,
        "target_id": "t", "target_order": 0,
        "original_source_path": "inputs/source.pdb",
        "original_source_sha256": source_sha256,
        "normalized_source_sha256": "0" * 64,
        "entities": [{
            "entity_type": "protein", "source_entity_id": "source-protein", "count": 1,
            "ordered_instance_ids": ["protein-1"], "sequence": "GA",
        }],
        "bonds": [],
        "instance_mappings": [{
            "source_entity_id": "source-protein", "source_instance_id": "protein-1",
            "runtime_target_id": "t", "runtime_entity_id": "runtime-1",
            "runtime_instance_id": "runtime-protein-1", "runtime_order": 0,
            "candidate_id": "c", "output_entity_id": "output-protein",
            "output_label_asym_id": "AA", "output_auth_asym_id": "X",
            "output_entity_order": 0,
        }],
        "admission": {"token_count": 2, "atom_count": 8, "token_limit": 100,
                      "conversion_omissions": []},
        "unsupported_fields": [],
    }
    snapshot["normalized_source_sha256"] = canonical_sha256({
        key: value for key, value in snapshot.items() if key != "normalized_source_sha256"
    })
    return snapshot


def test_cm_candidate_binding_parses_the_exact_supplied_source_bytes() -> None:
    from services.conformational_mapping.frustrampnn_adapter import (
        bind_cm_candidate_snapshot_bytes,
    )

    payload = _pdb()
    bound = bind_cm_candidate_snapshot_bytes(
        _snapshot("0" * 64),
        candidate_id="c",
        source_bytes=payload,
        source_suffix=".pdb",
        source_relative_path="native/candidate.pdb",
    )
    mapping = bound["instance_mappings"][0]
    assert mapping["candidate_id"] == "c"
    assert mapping["output_auth_asym_id"] == "X"
    assert mapping["output_label_asym_id"] == "X"
    assert mapping["output_entity_id"] == "source-protein"
    assert bound["original_source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert bound["original_source_path"] == "native/candidate.pdb"
    assert bound["normalized_source_sha256"] == canonical_sha256({
        key: value for key, value in bound.items()
        if key != "normalized_source_sha256"
    })


def test_cm_producer_projection_binds_exact_candidate_snapshot_digest() -> None:
    from services.conformational_mapping.frustrampnn_adapter import derive_producer_authority

    payload = _pdb()
    snapshot = _snapshot(hashlib.sha256(payload).hexdigest())
    authority = derive_producer_authority(
        snapshot,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        target_id="t",
        candidate_id="c",
    )
    assert authority["cm_complex_snapshot_sha256"] == canonical_sha256(snapshot)

    mutated = copy.deepcopy(snapshot)
    mutated["target_order"] = 1
    mutated["normalized_source_sha256"] = canonical_sha256({
        key: value for key, value in mutated.items() if key != "normalized_source_sha256"
    })
    mutated_authority = derive_producer_authority(
        mutated,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        target_id="t",
        candidate_id="c",
    )
    assert mutated_authority["cm_complex_snapshot_sha256"] != authority["cm_complex_snapshot_sha256"]


def test_cm_adapter_materializes_exact_output_identity_and_neutral_normalizer_consumes_it(
    tmp_path: Path,
) -> None:
    from services.conformational_mapping.frustrampnn_adapter import normalize_cm_structure

    payload = _pdb()
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    authority_path = tmp_path / "producer_manifest_v1.json"
    result = normalize_cm_structure(
        input_path=source,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "map.json",
        authority_artifact_path=authority_path,
        target_id="t",
        parent_job_id="j",
        candidate_id="c",
        complex_snapshot=_snapshot(hashlib.sha256(payload).hexdigest()),
        selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    assert result["identity_authority"] == "producer_manifest_v1"
    assert [(row["entity_instance_id"], row["source_entity_id"], row["label_asym_id"],
             row["auth_asym_id"], row["label_seq_id"], row["sequence_index"])
            for row in result["rows"]] == [
        ("protein-1", "output-protein", "AA", "X", None, 1),
        ("protein-1", "output-protein", "AA", "X", None, 2),
    ]


@pytest.mark.parametrize(
    ("field", "value", "expect_rejection"),
    [
        ("output_entity_id", "mutated-output", False),
        ("output_label_asym_id", "ZZ", False),
        ("output_auth_asym_id", "Y", True),
    ],
)
def test_cm_output_mapping_mutation_changes_derived_identity_or_is_rejected(
    tmp_path: Path, field: str, value: str, expect_rejection: bool,
) -> None:
    from services.conformational_mapping.frustrampnn_adapter import normalize_cm_structure

    payload = _pdb()
    source = tmp_path / f"{field}.pdb"
    source.write_bytes(payload)
    snapshot = _snapshot(hashlib.sha256(payload).hexdigest())
    snapshot["instance_mappings"][0][field] = value
    snapshot["normalized_source_sha256"] = canonical_sha256({
        key: item for key, item in snapshot.items() if key != "normalized_source_sha256"
    })
    authority_path = tmp_path / f"{field}.authority.json"
    kwargs = dict(
        input_path=source,
        output_pdb_path=tmp_path / f"{field}.normalized.pdb",
        map_path=tmp_path / f"{field}.map.json",
        authority_artifact_path=authority_path,
        target_id="t", parent_job_id="j", candidate_id="c",
        complex_snapshot=snapshot, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    if expect_rejection:
        with pytest.raises(ValueError, match="chain|authority|identity|absent"):
            normalize_cm_structure(**kwargs)
        assert not authority_path.exists()
        return
    result = normalize_cm_structure(**kwargs)
    expected = value
    identity_field = "source_entity_id" if field == "output_entity_id" else "label_asym_id"
    assert {row[identity_field] for row in result["rows"]} == {expected}


def test_cm_adapter_rejects_noncanonical_snapshot_identity_and_source_binding(tmp_path: Path) -> None:
    from services.conformational_mapping.frustrampnn_adapter import normalize_cm_structure

    payload = _pdb()
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    snapshot = _snapshot(hashlib.sha256(payload).hexdigest())
    base = dict(
        input_path=source, output_pdb_path=tmp_path / "out.pdb",
        map_path=tmp_path / "map.json", authority_artifact_path=tmp_path / "authority.json",
        target_id="t", parent_job_id="j", candidate_id="c", selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    stale_normalized = copy.deepcopy(snapshot)
    stale_normalized["normalized_source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="normalized|canonical|hash"):
        normalize_cm_structure(complex_snapshot=stale_normalized, **base)

    wrong_source = copy.deepcopy(snapshot)
    wrong_source["original_source_sha256"] = "f" * 64
    wrong_source["normalized_source_sha256"] = canonical_sha256({
        key: value for key, value in wrong_source.items() if key != "normalized_source_sha256"
    })
    with pytest.raises(ValueError, match="source|hash|binding"):
        normalize_cm_structure(complex_snapshot=wrong_source, **base)


def test_cm_adapter_projects_neutral_contracts_to_existing_cm_presentations(
    tmp_path: Path,
) -> None:
    from services.conformational_mapping.frustrampnn_adapter import (
        project_cm_landscape,
        project_cm_structure_map,
        normalize_cm_structure,
    )

    payload = _pdb()
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    snapshot = _snapshot(hashlib.sha256(payload).hexdigest())
    neutral_map = normalize_cm_structure(
        input_path=source,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "neutral-map.json",
        authority_artifact_path=tmp_path / "authority.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        complex_snapshot=snapshot, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    neutral_map["source_format"] = "mmcif"
    for row in neutral_map["rows"]:
        row["label_seq_id"] = row["sequence_index"]
        row["backbone_atoms"] = {
            name: f"cif:{(row['sequence_index'] - 1) * 4 + index}"
            for index, name in enumerate(("N", "CA", "C", "O"), start=1)
        }
    cm_map = project_cm_structure_map(neutral_map, snapshot)
    validate_schema("cm_structure_map_v1", cm_map)
    assert cm_map["schema_name"] == "cm_structure_map"
    assert {row["source_entity_id"] for row in cm_map["rows"]} == {"source-protein"}
    assert cm_map["rows"][0]["backbone_atoms"]["N"] == "1"

    header = "frustration_pred,position,wildtype,mutation,chain,pdb\n"
    rows = []
    for position, wt in enumerate("GA"):
        rows.extend(
            f"{-2.0 + AA_ORDER.index(mutation) / 5},{position},{wt},{mutation},X,normalized.pdb\n"
            for mutation in AA_ORDER
        )
    raw = tmp_path / "raw.csv"
    raw.write_text(header + "".join(rows), encoding="utf-8")
    neutral_landscape = finalize_neutral_landscape(
        raw, neutral_map,
        expected_normalized_pdb_sha256=neutral_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=neutral_map["model_ready_sequence_sha256"],
    )
    cm_landscape = project_cm_landscape(
        neutral_landscape,
        checkpoint_id="checkpoint", checkpoint_sha256="a" * 64,
        tool_id="frustrampnn", tool_sha256="b" * 64,
        container_sha256="c" * 64,
    )
    validate_schema("cm_frustration_landscape_v1", cm_landscape)
    assert cm_landscape["schema_name"] == "cm_frustration_landscape"
    assert cm_landscape["threshold_policy_id"] == "frustrampnn_class_v1"
    assert {slot["class"] for residue in cm_landscape["residues"] for slot in residue["slots"]} >= {
        "high", "neutral", "minimally_frustrated",
    }


def test_cm_adapter_rejects_symlink_source_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import frustrampnn_adapter as adapter

    payload = _pdb()
    target = tmp_path / "target.pdb"
    target.write_bytes(payload)
    source = tmp_path / "source.pdb"
    source.symlink_to(target)
    path_reads: list[Path] = []
    real_read_bytes = Path.read_bytes

    def count_path_read(path: Path) -> bytes:
        if path == source:
            path_reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_path_read)
    with pytest.raises(ValueError, match="symlink|no-follow|regular"):
        adapter.normalize_cm_structure(
            input_path=source,
            output_pdb_path=tmp_path / "normalized.pdb",
            map_path=tmp_path / "map.json",
            authority_artifact_path=tmp_path / "authority.json",
            target_id="t", parent_job_id="j", candidate_id="c",
            complex_snapshot=_snapshot(hashlib.sha256(payload).hexdigest()),
            selected_model=1, altloc_policy="blank_or_explicit:A",
        )
    assert path_reads == []


def test_cm_adapter_reads_source_generation_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import frustrampnn_adapter as adapter

    payload = _pdb()
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    source_reads = 0
    real_open = adapter.os.open
    real_read_bytes = Path.read_bytes

    def count_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal source_reads
        if os.fspath(path) == source.name and dir_fd is not None:
            source_reads += 1
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def count_path_read(path: Path) -> bytes:
        nonlocal source_reads
        if path == source:
            source_reads += 1
        return real_read_bytes(path)

    monkeypatch.setattr(adapter.os, "open", count_open)
    monkeypatch.setattr(Path, "read_bytes", count_path_read)
    adapter.normalize_cm_structure(
        input_path=source,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "map.json",
        authority_artifact_path=tmp_path / "authority.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        complex_snapshot=_snapshot(hashlib.sha256(payload).hexdigest()),
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert source_reads == 1


def test_cm_adapter_normalizes_pinned_generation_when_path_is_replaced_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import frustrampnn_adapter as adapter

    payload = _pdb()
    replacement = payload.replace(b" GLY ", b" ALA ", 1)
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    real_derive = adapter.derive_producer_authority

    def replace_path_after_read(*args, **kwargs):
        replacement_path = tmp_path / "replacement.pdb"
        replacement_path.write_bytes(replacement)
        os.replace(replacement_path, source)
        return real_derive(*args, **kwargs)

    monkeypatch.setattr(adapter, "derive_producer_authority", replace_path_after_read)
    result = adapter.normalize_cm_structure(
        input_path=source,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "map.json",
        authority_artifact_path=tmp_path / "authority.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        complex_snapshot=_snapshot(hashlib.sha256(payload).hexdigest()),
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert result["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert source.read_bytes() == replacement


def test_cm_adapter_rejects_symlinked_authority_output_parent(tmp_path: Path) -> None:
    from services.conformational_mapping.frustrampnn_adapter import normalize_cm_structure

    payload = _pdb()
    source = tmp_path / "source.pdb"
    source.write_bytes(payload)
    real_parent = tmp_path / "real-authority"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-authority"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="output parent|no-follow|symlink"):
        normalize_cm_structure(
            input_path=source,
            output_pdb_path=tmp_path / "normalized.pdb",
            map_path=tmp_path / "map.json",
            authority_artifact_path=linked_parent / "authority.json",
            target_id="t", parent_job_id="j", candidate_id="c",
            complex_snapshot=_snapshot(hashlib.sha256(payload).hexdigest()),
            selected_model=1, altloc_policy="blank_or_explicit:A",
        )
    assert not (real_parent / "authority.json").exists()


def test_authority_publication_rolls_back_when_directory_fsync_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    from services.conformational_mapping import frustrampnn_adapter as adapter

    output = tmp_path / "authority.json"
    real_fsync = adapter.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(adapter.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync"):
        adapter._materialize_canonical(output, b"{}\n")

    assert not output.exists()
