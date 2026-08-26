from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

ONE_AKI_SHA256 = "c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb"


def test_managed_fixture_is_product_owned_inspected_and_path_free() -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")

    source_ref = starting_structures.StartingStructureSourceRef(
        kind="managed_fixture", id="1aki-admitted-v1"
    )
    resolved = starting_structures.resolve_product_source(source_ref)
    inspection = starting_structures.inspect_resolved_structure(resolved)

    assert resolved.path == API_ROOT / "assets" / "md" / "admitted_structures" / "1AKI.pdb"
    assert resolved.path.read_bytes() == (API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb").read_bytes()
    assert inspection.identity.format == "pdb"
    assert inspection.identity.size_bytes == 116397
    assert inspection.identity.sha256 == ONE_AKI_SHA256
    assert inspection.identity.pdb_id == "1AKI"
    assert inspection.inspection.model_count == 1
    assert inspection.inspection.atom_count > 0
    assert inspection.inspection.chains == ["A"]
    assert inspection.admission.state == "profile_required"
    payload = inspection.model_dump(mode="json", by_alias=True)
    assert payload["schema_version"] == "bms.md.starting-structure-inspection.v1"
    assert "schema" not in payload
    assert payload["source_ref"] == {"kind": "managed_fixture", "id": "1aki-admitted-v1"}
    assert "path" not in json.dumps(payload).lower()
    assert payload["viewer"]["url"] == (
        "/api/molecular-dynamics/starting-structures/managed_fixture/"
        f"1aki-admitted-v1/content?expected_sha256={ONE_AKI_SHA256}"
    )


def test_esmf_generated_cif_without_occupancy_is_inspected_without_byte_mutation(
    tmp_path: Path,
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    source_bytes = b"""data_esmfold2
#
loop_
_atom_site.group_PDB
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.B_iso_or_equiv
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
_atom_site.id
ATOM N N  . GLY A 1 1 . 1 GLY A N  80.0 0.0 0.0 0.0 1 1
ATOM C CA . GLY A 1 1 . 1 GLY A CA 80.0 1.0 0.0 0.0 1 2
#
"""
    source = tmp_path / "esmfold2-result.cif"
    source.write_bytes(source_bytes)
    resolved = starting_structures.ResolvedStartingStructure(
        source_ref=starting_structures.StartingStructureSourceRef(
            kind="design", id="ae5baf3f-e456-4e22-ae30-f488f5a801f4"
        ),
        path=source,
        label="ESMFold2 candidate",
        producer_job_id="80136598-ca8a-59f6-a9ec-e351b172c5c2",
        design_id="ae5baf3f-e456-4e22-ae30-f488f5a801f4",
    )

    inspection = starting_structures.inspect_resolved_structure(resolved)

    assert source.read_bytes() == source_bytes
    assert inspection.identity.format == "cif"
    assert inspection.identity.sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert inspection.inspection.model_count == 1
    assert inspection.inspection.chains == ["A"]
    assert inspection.inspection.atom_count == 2
    assert inspection.inspection.hetero_components == []
    assert inspection.inspection.parser.name == "biopython"


def test_esmf_generated_cif_without_occupancy_still_rejects_invalid_coordinates(
    tmp_path: Path,
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    source = tmp_path / "invalid-esmfold2-result.cif"
    source.write_bytes(b"""data_esmfold2_invalid
#
loop_
_atom_site.group_PDB
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.B_iso_or_equiv
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
_atom_site.id
ATOM N N . GLY A 1 1 . 1 GLY A N 80.0 invalid 0.0 0.0 1 1
#
""")
    resolved = starting_structures.ResolvedStartingStructure(
        source_ref=starting_structures.StartingStructureSourceRef(
            kind="design", id="ae5baf3f-e456-4e22-ae30-f488f5a801f4"
        ),
        path=source,
        label="Invalid ESMFold2 candidate",
    )

    with pytest.raises(starting_structures.StartingStructureError) as rejected:
        starting_structures.inspect_resolved_structure(resolved)

    assert rejected.value.code == "MD_STARTING_STRUCTURE_PARSE_FAILED"


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_occupancy_present_cif_uses_direct_full_parser_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    line_ending: bytes,
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    source = tmp_path / "occupancy-present.cif"
    source.write_bytes(b"""data_occupancy_present
#
loop_
_atom_site.group_PDB
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
_atom_site.id
ATOM N N . GLY A 1 1 . 1 GLY A N 1.0 80.0 0.0 0.0 0.0 1 1
#
""".replace(b"\n", line_ending))
    resolved = starting_structures.ResolvedStartingStructure(
        source_ref=starting_structures.StartingStructureSourceRef(
            kind="design", id="ae5baf3f-e456-4e22-ae30-f488f5a801f4"
        ),
        path=source,
        label="Occupancy-present candidate",
    )

    def reject_extra_dictionary(*_args, **_kwargs):
        raise AssertionError("occupancy-present CIF must use the direct one-pass parser")

    monkeypatch.setattr(starting_structures, "MMCIF2Dict", reject_extra_dictionary)

    inspection = starting_structures.inspect_resolved_structure(resolved)

    assert inspection.inspection.model_count == 1
    assert inspection.inspection.chains == ["A"]
    assert inspection.inspection.atom_count == 1


def test_missing_occupancy_releases_first_pass_atom_ids_before_full_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    released = False
    real_parser = starting_structures.MMCIFParser

    class TrackedAtomIds(list):
        def __del__(self):
            nonlocal released
            released = True

    class TrackingWriter:
        def set_dict(self, value):
            self.value = value

        def save(self, output):
            output.write("""data_normalized
#
loop_
_atom_site.group_PDB
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
_atom_site.id
ATOM N N . GLY A 1 1 . 1 GLY A N 1.0 80.0 0.0 0.0 0.0 1 1
#
""")

    class ReleaseCheckingParser:
        def __init__(self, **kwargs):
            self.parser = real_parser(**kwargs)

        def get_structure(self, structure_id, stream):
            assert released, "first-pass atom IDs must be released before full parsing"
            return self.parser.get_structure(structure_id, stream)

    monkeypatch.setattr(
        starting_structures,
        "MMCIF2Dict",
        lambda _stream: {"_atom_site.id": TrackedAtomIds(["1"])},
    )
    monkeypatch.setattr(starting_structures, "MMCIFIO", TrackingWriter)
    monkeypatch.setattr(starting_structures, "MMCIFParser", ReleaseCheckingParser)

    structure = starting_structures._parse_mmcif_structure(
        b"data_missing_occupancy\n_atom_site.id 1\n"
    )

    assert len(list(structure.get_atoms())) == 1


def test_source_reference_is_closed_and_bounded() -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")

    with pytest.raises(ValidationError):
        starting_structures.StartingStructureSourceRef(kind="path", id="anything")
    with pytest.raises(ValidationError):
        starting_structures.StartingStructureSourceRef(
            kind="upload", id="x", path="/tmp/secret.pdb"
        )
    with pytest.raises(ValidationError):
        starting_structures.StartingStructureSourceRef(kind="upload", id="x" * 257)


def test_format_detection_rejects_suffix_mismatch_ambiguity_and_archives(tmp_path: Path) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    pdb_bytes = (API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb").read_bytes()

    mismatched = tmp_path / "wrong.cif"
    mismatched.write_bytes(pdb_bytes)
    with pytest.raises(starting_structures.StartingStructureError) as mismatch:
        starting_structures.read_structure_file(mismatched)
    assert mismatch.value.code == "MD_STARTING_STRUCTURE_FORMAT_MISMATCH"

    ambiguous = tmp_path / "ambiguous.pdb"
    ambiguous.write_bytes(b"data_test\n_atom_site.id 1\nATOM      1  N   GLY A   1      0.0 0.0 0.0\n")
    with pytest.raises(starting_structures.StartingStructureError) as ambiguity:
        starting_structures.read_structure_file(ambiguous)
    assert ambiguity.value.code == "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED"

    archive = tmp_path / "archive.pdb"
    archive.write_bytes(b"PK\x03\x04" + b"x" * 32)
    with pytest.raises(starting_structures.StartingStructureError) as archived:
        starting_structures.read_structure_file(archive)
    assert archived.value.code == "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED"


def test_exact_profile_admission_is_digest_bound() -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    source_ref = starting_structures.StartingStructureSourceRef(
        kind="managed_fixture", id="1aki-admitted-v1"
    )
    resolved = starting_structures.resolve_product_source(source_ref)
    profile = {
        "id": "profile-v1",
        "profile_sha256": "a" * 64,
        "states": {"selectable": True},
        "launch_constraints": {"structure_sha256": ONE_AKI_SHA256},
    }

    admitted = starting_structures.inspect_resolved_structure(
        resolved, chemistry_profile_id="profile-v1", profile=profile
    )
    assert admitted.admission.model_dump() == {
        "state": "admitted",
        "profile_id": "profile-v1",
        "code": None,
        "message": "The exact starting-structure bytes are admitted by the selected chemistry profile.",
    }

    blocked_profile = {**profile, "launch_constraints": {"structure_sha256": hashlib.sha256(b"other").hexdigest()}}
    blocked = starting_structures.inspect_resolved_structure(
        resolved, chemistry_profile_id="profile-v1", profile=blocked_profile
    )
    assert blocked.admission.state == "blocked"
    assert blocked.admission.code == "MD_STARTING_STRUCTURE_NOT_ADMITTED"


def test_server_file_inventory_is_metadata_only_incremental_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    root = tmp_path / "server-files"
    root.mkdir()
    pdb_bytes = (API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb").read_bytes()
    padding = (b"REMARK   metadata-only inventory chunk".ljust(79, b" ") + b"\n") * 14000
    expected_sizes: dict[str, int] = {}
    for index in range(3):
        candidate = root / f"multi-chunk-{index}.pdb"
        content = padding + pdb_bytes
        candidate.write_bytes(content)
        expected_sizes[candidate.name] = len(content)

    (root / "unsupported.pdb").write_bytes(b"not structure content\n")
    (root / "unsupported.txt").write_bytes(pdb_bytes)
    outside = tmp_path / "outside.pdb"
    outside.write_bytes(pdb_bytes)
    (root / "symlinked.pdb").symlink_to(outside)
    symlinked_directory = root / "symlinked-directory"
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "escaped.pdb").write_bytes(pdb_bytes)
    symlinked_directory.symlink_to(outside_directory, target_is_directory=True)
    oversized = root / "oversized.pdb"
    with oversized.open("wb") as handle:
        handle.write(b"HEADER\n")
        handle.truncate(starting_structures.MAX_STARTING_STRUCTURE_BYTES + 1)

    monkeypatch.setenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "1")
    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"inputs": root})

    def forbidden_full_body_helper(*_args, **_kwargs):
        raise AssertionError("inventory must not use the full-body structure helper")

    with monkeypatch.context() as inventory_patch:
        inventory_patch.setattr(
            starting_structures,
            "_read_structure_descriptor",
            forbidden_full_body_helper,
        )
        first_page = starting_structures.list_server_files(search="", cursor=None, limit=2)
        assert len(first_page.items) == 2
        assert first_page.next_cursor is not None
        second_page = starting_structures.list_server_files(
            search="", cursor=first_page.next_cursor, limit=2
        )
        assert len(second_page.items) == 1
        assert second_page.next_cursor is None
        inventory = starting_structures._server_file_inventory()

    entries = [*first_page.items, *second_page.items]
    assert {entry.label for entry in entries} == set(expected_sizes)
    assert all(entry.format == "pdb" for entry in entries)
    assert {entry.label: entry.size_bytes for entry in entries} == expected_sizes
    assert len(inventory) == 3
    for entry in inventory:
        assert set(vars(entry)) == {"handle", "label", "format", "size_bytes"}
        assert not any("path" in field or "data" in field for field in vars(entry))

    async def resolve_every_handle() -> None:
        for entry in entries:
            resolved = await starting_structures.resolve_source(
                starting_structures.StartingStructureSourceRef(
                    kind="server_file", id=entry.id
                ),
                object(),
            )
            try:
                structure = starting_structures.read_resolved_structure(resolved)
                assert structure.size_bytes == expected_sizes[entry.label]
            finally:
                resolved.close()

    asyncio.run(resolve_every_handle())
