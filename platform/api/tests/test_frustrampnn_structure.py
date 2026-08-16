from __future__ import annotations

import ast
import hashlib
import importlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _structure():
    path = REPO_ROOT / "platform/api/services/frustrampnn/structure.py"
    assert path.is_file(), "neutral FrustraMPNN structure normalizer is missing"
    return importlib.import_module("services.frustrampnn.structure")


def _pdb_atom(serial: int, atom: str, residue: str, chain: str, number: int, *, insertion: str = "", altloc: str = "", x: float = 1.0, occupancy: float = 1.0, record: str = "ATOM") -> str:
    element = next(character for character in atom if character.isalpha())
    atom_field = atom if len(atom) == 4 else f" {atom:<3}"
    return (
        f"{record:<6}{serial:5d} {atom_field}{altloc or ' '}{residue:>3} {chain}{number:4d}{insertion or ' '}"
        f"   {x:8.3f}{(x+1):8.3f}{(x+2):8.3f}{occupancy:6.2f}{20.0:6.2f}          {element:>2}  \n"
    )


def _pdb() -> bytes:
    lines = ["MODEL        1\n"]
    serial = 1
    for atom in ("N", "CA", "C", "O"):
        lines.append(_pdb_atom(serial, atom, "GLY", "A", 10, insertion="A", x=float(serial))); serial += 1
    # deterministic alternate: A must win over B regardless of source order
    lines.append(_pdb_atom(serial, "CB", "ALA", "A", 12, altloc="B", x=90.0, occupancy=.9)); serial += 1
    for atom in ("N", "CA", "C", "O", "CB"):
        lines.append(_pdb_atom(serial, atom, "ALA", "A", 12, altloc="A" if atom == "CB" else "", x=float(serial), occupancy=.4 if atom == "CB" else 1.0)); serial += 1
    for atom in ("N", "CA", "C"):
        lines.append(_pdb_atom(serial, atom, "SER", "B", 5, x=float(serial))); serial += 1
    lines.append(_pdb_atom(serial, "C1", "LIG", "Z", 1, x=1.0, record="HETATM")); serial += 1
    lines.extend(["ENDMDL\n", "MODEL        2\n"])
    for atom in ("N", "CA", "C", "O"):
        lines.append(_pdb_atom(serial, atom, "GLY", "A", 10, insertion="A", x=50.0)); serial += 1
    lines.extend(["ENDMDL\n", "END\n"])
    return "".join(lines).encode("ascii")


def _cif(sequence_second: str = "ALA", *, include_occupancy: bool = True) -> bytes:
    columns = [
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
        "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
        "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
        "auth_atom_id", "pdbx_PDB_model_num",
    ]
    if not include_occupancy:
        columns.remove("occupancy")
    lines = ["data_fixture\n", "loop_\n", *[f"_atom_site.{name}\n" for name in columns]]
    atom_id = 1
    for label_seq, auth_seq, residue, insertion in ((1, 10, "GLY", "A"), (2, 12, sequence_second, "?")):
        for atom, element in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            row_by_column = {
                "group_PDB": "ATOM", "id": str(atom_id), "type_symbol": element,
                "label_atom_id": atom, "label_alt_id": ".", "label_comp_id": residue,
                "label_asym_id": "AA", "label_entity_id": "1", "label_seq_id": str(label_seq),
                "pdbx_PDB_ins_code": insertion, "Cartn_x": str(atom_id),
                "Cartn_y": str(atom_id + 1), "Cartn_z": str(atom_id + 2),
                "occupancy": "1.0", "B_iso_or_equiv": "20.0", "auth_seq_id": str(auth_seq),
                "auth_comp_id": residue, "auth_asym_id": "X", "auth_atom_id": atom,
                "pdbx_PDB_model_num": "1",
            }
            row = [row_by_column[column] for column in columns]
            lines.append(" ".join(row) + "\n")
            atom_id += 1
    # non-protein entity remains source-only and is excluded
    nonprotein_by_column = {
        "group_PDB": "HETATM", "id": str(atom_id), "type_symbol": "C",
        "label_atom_id": "C1", "label_alt_id": ".", "label_comp_id": "LIG",
        "label_asym_id": "L", "label_entity_id": "2", "label_seq_id": ".",
        "pdbx_PDB_ins_code": "?", "Cartn_x": "1", "Cartn_y": "2", "Cartn_z": "3",
        "occupancy": "1", "B_iso_or_equiv": "20", "auth_seq_id": "1",
        "auth_comp_id": "LIG", "auth_asym_id": "Z", "auth_atom_id": "C1",
        "pdbx_PDB_model_num": "1",
    }
    lines.append(" ".join(nonprotein_by_column[column] for column in columns) + "\n#\n")
    return "".join(lines).encode("utf-8")


def test_mmcif_without_occupancy_normalizes_predicted_atoms_as_fully_occupied(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _cif(include_occupancy=False)
    source = tmp_path / "predicted.cif"
    source.write_bytes(payload)
    structure_map = module.normalize_structure(
        input_path=source,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "map.json",
        target_id="target-1",
        parent_job_id="job-1",
        candidate_id="candidate-1",
        identity_authority=module.derive_mmcif_atom_site_authority(payload),
        protein_selection={"mode": "all_protein_entities"},
        selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    assert structure_map["model_ready_sequence"] == "GA"
    atom_lines = (tmp_path / "normalized.pdb").read_text("ascii").splitlines()
    assert atom_lines
    assert all(line[54:60] == "  1.00" for line in atom_lines if line.startswith("ATOM"))


def test_pdb_only_normalization_is_candidate_local_and_never_invents_mmcif_identity(tmp_path: Path) -> None:
    module = _structure()
    source = tmp_path / "input.pdb"
    source.write_bytes(_pdb())
    output = tmp_path / "normalized.pdb"
    sidecar = tmp_path / "map.json"
    authority = {
        "kind": "pdb_self_identity_v1",
        "identity_domain": "candidate_local",
        "authority_artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    structure_map = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=sidecar,
        target_id="target-1", parent_job_id="job-1", candidate_id="candidate-1",
        identity_authority=authority, protein_selection={"mode": "all_protein_entities"},
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert structure_map["identity_authority"] == "pdb_self_identity_v1"
    assert structure_map["identity_domain"] == "candidate_local"
    assert structure_map["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert structure_map["normalized_pdb_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    mapped = [row for row in structure_map["rows"] if row["status"] == "mapped"]
    assert [(row["auth_asym_id"], row["auth_seq_id"], row["insertion_code"]) for row in mapped] == [("A", 10, "A"), ("A", 12, "")]
    assert [row["model_position"] for row in mapped] == [0, 1]
    assert all(row["source_entity_id"] is None and row["label_asym_id"] is None and row["label_seq_id"] is None for row in structure_map["rows"])
    excluded = structure_map["excluded_records"]
    assert any(record["reason_code"] == "missing_backbone" for record in excluded)
    assert any(record["reason_code"] == "non_protein_entity" for record in excluded)
    assert b" 90.000" not in output.read_bytes()
    assert b" SER " not in output.read_bytes(), "missing-backbone residue entered model PDB"
    pdb_residues = list(dict.fromkeys(
        (line[21:22], line[22:26], line[26:27])
        for line in output.read_text("ascii").splitlines() if line.startswith("ATOM")
    ))
    assert pdb_residues == [
        (row["pdb_chain_id"], f"{row['pdb_residue_id']:4d}", row["pdb_insertion_code"] or " ")
        for row in mapped
    ]


def test_mmcif_self_authority_is_derived_from_exact_atom_site_bytes() -> None:
    module = _structure()
    payload = _cif()
    authority = module.derive_mmcif_atom_site_authority(payload)
    assert authority == {
        "kind": "mmcif_atom_site_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "entities": [{
            "entity_instance_id": "mmcif:1:AA:X",
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "X",
            "sequence": "GA",
        }],
    }


def test_mmcif_normalization_preserves_authorized_entity_label_auth_and_sequence_identity(tmp_path: Path) -> None:
    module = _structure()
    source = tmp_path / "input.cif"
    source.write_bytes(_cif())
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    authority = {
        "kind": "mmcif_atom_site_v1", "identity_domain": "source_authoritative",
        "authority_artifact_sha256": source_hash,
        "entities": [{
            "entity_instance_id": "protein-1", "source_entity_id": "1",
            "label_asym_id": "AA", "auth_asym_id": "X", "sequence": "GA",
        }],
    }
    structure_map = module.normalize_structure(
        input_path=source, output_pdb_path=tmp_path / "out.pdb", map_path=tmp_path / "map.json",
        target_id="target-1", parent_job_id="job-1", candidate_id="candidate-1",
        identity_authority=authority, protein_selection={"mode": "all_protein_entities"},
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert [(row["source_entity_id"], row["label_asym_id"], row["auth_asym_id"], row["label_seq_id"], row["sequence_index"]) for row in structure_map["rows"]] == [("1", "AA", "X", 1, 1), ("1", "AA", "X", 2, 2)]
    assert structure_map["model_ready_sequence"] == "GA"
    assert structure_map["model_ready_sequence_sha256"] == hashlib.sha256(b"GA").hexdigest()


def test_explicit_protein_selection_limits_model_input_without_filename_inference(tmp_path: Path) -> None:
    module = _structure()
    source = tmp_path / "input.pdb"
    source.write_bytes(_pdb())
    authority = {
        "kind": "pdb_self_identity_v1", "identity_domain": "candidate_local",
        "authority_artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    structure_map = module.normalize_structure(
        input_path=source, output_pdb_path=tmp_path / "selected.pdb",
        map_path=tmp_path / "selected.json", target_id="t", parent_job_id="j",
        candidate_id="c", identity_authority=authority,
        protein_selection={"mode": "explicit", "entities": [{
            "entity_instance_id": "pdb:A", "source_entity_id": None,
            "label_asym_id": None, "auth_asym_id": "A", "sequence": "GA",
        }]}, selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert {row["entity_instance_id"] for row in structure_map["rows"]} == {"pdb:A"}
    assert any(record["reason_code"] == "not_selected" for record in structure_map["excluded_records"])


def test_normalization_fails_closed_for_implicit_model_stale_sequence_and_symlink(tmp_path: Path) -> None:
    module = _structure()
    source = tmp_path / "input.pdb"
    source.write_bytes(_pdb())
    authority = {"kind": "pdb_self_identity_v1", "identity_domain": "candidate_local", "authority_artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    kwargs = dict(input_path=source, output_pdb_path=tmp_path / "out.pdb", map_path=tmp_path / "map.json", target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority, protein_selection={"mode": "all_protein_entities"}, altloc_policy="blank_or_explicit:A")
    with pytest.raises(module.StructureNormalizationError, match="multiple.*model"):
        module.normalize_structure(selected_model=None, **kwargs)

    cif = tmp_path / "input.cif"
    cif.write_bytes(_cif())
    cif_hash = hashlib.sha256(cif.read_bytes()).hexdigest()
    stale = {"kind": "mmcif_atom_site_v1", "identity_domain": "source_authoritative", "authority_artifact_sha256": cif_hash,
             "entities": [{"entity_instance_id": "p", "source_entity_id": "1", "label_asym_id": "AA", "auth_asym_id": "X", "sequence": "GG"}]}
    with pytest.raises(module.StructureNormalizationError, match="sequence"):
        module.normalize_structure(input_path=cif, output_pdb_path=tmp_path / "c.pdb", map_path=tmp_path / "c.json", target_id="t", parent_job_id="j", candidate_id="c", identity_authority=stale, protein_selection={"mode": "all_protein_entities"}, selected_model=1, altloc_policy="blank_or_explicit:A")

    link = tmp_path / "linked.pdb"
    link.symlink_to(source)
    linked_authority = dict(authority, authority_artifact_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with pytest.raises(module.StructureNormalizationError, match="symlink"):
        module.normalize_structure(input_path=link, output_pdb_path=tmp_path / "l.pdb", map_path=tmp_path / "l.json", target_id="t", parent_job_id="j", candidate_id="c", identity_authority=linked_authority, protein_selection={"mode": "all_protein_entities"}, selected_model=1, altloc_policy="blank_or_explicit:A")


def _single_model_pdb(residues: list[tuple[str, str, int, str]]) -> bytes:
    lines: list[str] = []
    serial = 1
    for residue, chain, number, altloc in residues:
        for atom in ("N", "CA", "C", "O"):
            lines.append(_pdb_atom(serial, atom, residue, chain, number, altloc=altloc, x=float(serial)))
            serial += 1
    lines.append("END\n")
    return "".join(lines).encode("ascii")


def _pdb_authority(payload: bytes) -> dict:
    return {
        "kind": "pdb_self_identity_v1",
        "identity_domain": "candidate_local",
        "authority_artifact_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_nonstandard_atom_residue_is_explicitly_recorded_and_never_emitted_to_model_pdb(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _single_model_pdb([("MSE", "A", 1, ""), ("GLY", "A", 2, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    output = tmp_path / "normalized.pdb"
    result = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=tmp_path / "map.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        identity_authority=_pdb_authority(payload), protein_selection={"mode": "all_protein_entities"},
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert any(row["residue_name"] == "MSE" and row["status"] == "nonstandard_residue"
               for row in result["rows"])
    assert any(record["reason_code"] == "nonstandard_residue" and "MSE" in record["source_identity"]
               for record in result["excluded_records"])
    assert b" MSE " not in output.read_bytes()
    assert result["model_ready_sequence"] == "G"


def test_external_pdb_mse_is_nonstandard_and_clear_nonprotein_records_are_excluded(
    tmp_path: Path,
) -> None:
    module = _structure()
    lines: list[str] = []
    serial = 1
    for atom in ("N", "CA", "C", "O"):
        lines.append(_pdb_atom(serial, atom, "GLY", "X", 10, x=float(serial)))
        serial += 1
    for atom in ("N", "CA", "C", "O"):
        lines.append(_pdb_atom(
            serial, atom, "MSE", "X", 11, x=float(serial), record="HETATM",
        ))
        serial += 1
    for residue, number, record in (("DA", 20, "ATOM"), ("HOH", 21, "HETATM"), ("LIG", 22, "HETATM")):
        lines.append(_pdb_atom(serial, "C1", residue, "X", number, x=float(serial), record=record))
        serial += 1
    payload = ("".join(lines) + "END\n").encode("ascii")
    source = tmp_path / "external.pdb"; source.write_bytes(payload)
    source_hash = hashlib.sha256(payload).hexdigest()
    artifact_path = tmp_path / "producer_manifest_v1.json"
    _, authority_hash = _write_producer_authority(
        module, artifact_path, source_sha256=source_hash, sequence="GM",
    )
    output = tmp_path / "normalized.pdb"
    result = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=tmp_path / "map.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        identity_authority={
            "kind": "producer_manifest_v1", "identity_domain": "source_authoritative",
            "authority_artifact_sha256": authority_hash, "source_sha256": source_hash,
        },
        authority_artifact_path=artifact_path,
        protein_selection={"mode": "all_protein_entities"}, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    assert [(row["residue_name"], row["wt"], row["status"]) for row in result["rows"]] == [
        ("GLY", "G", "mapped"), ("MSE", None, "nonstandard_residue"),
    ]
    excluded = {(item["source_identity"], item["reason_code"])
                for item in result["excluded_records"]}
    assert ("X:11:MSE", "nonstandard_residue") in excluded
    assert {"X:20:DA", "X:21:HOH", "X:22:LIG"} <= {
        identity for identity, reason in excluded if reason == "non_protein_entity"
    }
    assert b" MSE " not in output.read_bytes()
    assert b" DA " not in output.read_bytes()
    assert result["model_ready_sequence"] == "G"


def test_mmcif_mse_atom_residue_is_recorded_with_exact_authorized_label_coverage(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _cif("MSE")
    source = tmp_path / "mse.cif"; source.write_bytes(payload)
    authority = {
        "kind": "mmcif_atom_site_v1", "identity_domain": "source_authoritative",
        "authority_artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "entities": [{"entity_instance_id": "protein-1", "source_entity_id": "1",
                      "label_asym_id": "AA", "auth_asym_id": "X", "sequence": "GM"}],
    }
    output = tmp_path / "mse.pdb"
    result = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=tmp_path / "mse.json",
        target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority,
        protein_selection={"mode": "all_protein_entities"}, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    assert [(row["label_seq_id"], row["residue_name"], row["wt"], row["status"])
            for row in result["rows"]] == [
                (1, "GLY", "G", "mapped"),
                (2, "MSE", None, "nonstandard_residue"),
            ]
    assert b" MSE " not in output.read_bytes()
    assert result["model_ready_sequence"] == "G"


def test_duplicate_author_residue_and_genuine_ambiguous_altloc_fail_closed(tmp_path: Path) -> None:
    module = _structure()
    duplicate = _single_model_pdb([("GLY", "A", 1, ""), ("ALA", "A", 1, "")])
    source = tmp_path / "duplicate.pdb"; source.write_bytes(duplicate)
    with pytest.raises(module.StructureNormalizationError, match="duplicate author"):
        module.normalize_structure(
            input_path=source, output_pdb_path=tmp_path / "d.pdb", map_path=tmp_path / "d.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=_pdb_authority(duplicate),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )

    lines = [_pdb_atom(1, "N", "GLY", "A", 1, altloc="A")]
    lines.append(_pdb_atom(2, "N", "GLY", "A", 1, altloc="A", x=2.0))
    for serial, atom in enumerate(("CA", "C", "O"), start=3):
        lines.append(_pdb_atom(serial, atom, "GLY", "A", 1, x=float(serial)))
    ambiguous = ("".join(lines) + "END\n").encode("ascii")
    source = tmp_path / "ambiguous.pdb"; source.write_bytes(ambiguous)
    with pytest.raises(module.StructureNormalizationError, match="ambiguous altloc"):
        module.normalize_structure(
            input_path=source, output_pdb_path=tmp_path / "a.pdb", map_path=tmp_path / "a.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=_pdb_authority(ambiguous),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )


def test_mmcif_requires_unique_exact_authorized_sequence_coverage_and_explicit_tuple_match(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _cif()
    lines = payload.decode().splitlines()
    row_indexes = [index for index, line in enumerate(lines) if line.startswith("ATOM ")]
    for index in row_indexes[4:8]:
        tokens = lines[index].split(); tokens[8] = "1"; lines[index] = " ".join(tokens)
    duplicate = ("\n".join(lines) + "\n").encode()
    source = tmp_path / "duplicate.cif"; source.write_bytes(duplicate)
    duplicate_hash = hashlib.sha256(duplicate).hexdigest()
    authority = {
        "kind": "mmcif_atom_site_v1", "identity_domain": "source_authoritative",
        "authority_artifact_sha256": duplicate_hash,
        "entities": [{"entity_instance_id": "protein-1", "source_entity_id": "1",
                      "label_asym_id": "AA", "auth_asym_id": "X", "sequence": "GA"}],
    }
    with pytest.raises(module.StructureNormalizationError, match="duplicate.*label|coverage"):
        module.normalize_structure(
            input_path=source, output_pdb_path=tmp_path / "d.pdb", map_path=tmp_path / "d.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority,
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )

    source = tmp_path / "valid.cif"; source.write_bytes(payload)
    source_hash = hashlib.sha256(payload).hexdigest()
    authority.update(authority_artifact_sha256=source_hash)
    wrong = {"mode": "explicit", "entities": [{"entity_instance_id": "protein-1",
             "source_entity_id": "1", "label_asym_id": "AA", "auth_asym_id": "WRONG",
             "sequence": "GA"}]}
    with pytest.raises(module.StructureNormalizationError, match="tuple|auth_asym|identity"):
        module.normalize_structure(
            input_path=source, output_pdb_path=tmp_path / "w.pdb", map_path=tmp_path / "w.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority,
            protein_selection=wrong, selected_model=1, altloc_policy="blank_or_explicit:A",
        )


def _write_producer_authority(
    module, path: Path, *, source_sha256: str, sequence: str = "GA",
) -> tuple[dict, str]:
    artifact = {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": source_sha256,
        "entities": [{
            "entity_type": "protein",
            "entity_instance_id": "protein-1",
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "X",
            "sequence": sequence,
        }],
    }
    payload = module.canonical_json_bytes(artifact)
    path.write_bytes(payload)
    return artifact, hashlib.sha256(payload).hexdigest()


def test_external_producer_authority_is_verified_and_preserves_pdb_auth_identity(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _single_model_pdb([("GLY", "X", 10, ""), ("ALA", "X", 12, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    source_hash = hashlib.sha256(payload).hexdigest()
    artifact_path = tmp_path / "producer_manifest_v1.json"
    _, authority_hash = _write_producer_authority(
        module, artifact_path, source_sha256=source_hash,
    )
    authority = {
        "kind": "producer_manifest_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": authority_hash,
        "source_sha256": source_hash,
    }
    output = tmp_path / "out.pdb"
    result = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=tmp_path / "map.json",
        target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority,
        authority_artifact_path=artifact_path,
        protein_selection={"mode": "all_protein_entities"}, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    assert result["source_sha256"] == source_hash
    assert result["authority_artifact_sha256"] == authority_hash
    assert result["identity_domain"] == "source_authoritative"
    assert [
        (row["entity_instance_id"], row["source_entity_id"], row["label_asym_id"],
         row["auth_asym_id"], row["label_seq_id"], row["sequence_index"])
        for row in result["rows"]
    ] == [
        ("protein-1", "1", "AA", "X", None, 1),
        ("protein-1", "1", "AA", "X", None, 2),
    ]
    assert {line[21] for line in output.read_text("ascii").splitlines()
            if line.startswith("ATOM")} == {"X"}


def test_external_authority_rejects_unverified_arbitrary_stale_or_wrong_typed_artifacts(
    tmp_path: Path,
) -> None:
    module = _structure()
    payload = _single_model_pdb([("GLY", "X", 10, ""), ("ALA", "X", 12, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    source_hash = hashlib.sha256(payload).hexdigest()
    artifact_path = tmp_path / "producer_manifest_v1.json"
    artifact, authority_hash = _write_producer_authority(
        module, artifact_path, source_sha256=source_hash,
    )
    base = {
        "kind": "producer_manifest_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": authority_hash,
        "source_sha256": source_hash,
    }
    kwargs = dict(
        input_path=source, output_pdb_path=tmp_path / "out.pdb", map_path=tmp_path / "map.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        protein_selection={"mode": "all_protein_entities"}, selected_model=1,
        altloc_policy="blank_or_explicit:A",
    )
    with pytest.raises(module.StructureNormalizationError, match="artifact.*path|required"):
        module.normalize_structure(identity_authority=base, authority_artifact_path=None, **kwargs)
    with pytest.raises(module.StructureNormalizationError, match="hash|digest"):
        module.normalize_structure(
            identity_authority=dict(base, authority_artifact_sha256="a" * 64),
            authority_artifact_path=artifact_path, **kwargs,
        )

    stale = dict(base, source_sha256="b" * 64)
    with pytest.raises(module.StructureNormalizationError, match="source.*hash|binding"):
        module.normalize_structure(
            identity_authority=stale, authority_artifact_path=artifact_path, **kwargs,
        )

    artifact["schema_version"] = 2
    typed_payload = module.canonical_json_bytes(artifact)
    artifact_path.write_bytes(typed_payload)
    with pytest.raises(module.StructureNormalizationError, match="schema|kind|version|typed"):
        module.normalize_structure(
            identity_authority=dict(base, authority_artifact_sha256=hashlib.sha256(typed_payload).hexdigest()),
            authority_artifact_path=artifact_path, **kwargs,
        )

    artifact["schema_version"] = 1
    artifact["entities"][0]["sequence"] = "AA"
    wrong_entity_payload = module.canonical_json_bytes(artifact)
    artifact_path.write_bytes(wrong_entity_payload)
    with pytest.raises(module.StructureNormalizationError, match="sequence|residue|identity"):
        module.normalize_structure(
            identity_authority=dict(
                base,
                authority_artifact_sha256=hashlib.sha256(wrong_entity_payload).hexdigest(),
            ),
            authority_artifact_path=artifact_path, **kwargs,
        )

    artifact["entities"][0]["sequence"] = "GA"
    valid_payload = module.canonical_json_bytes(artifact)
    artifact_path.write_bytes(valid_payload)
    link = tmp_path / "linked-producer-manifest.json"
    link.symlink_to(artifact_path)
    with pytest.raises(module.StructureNormalizationError, match="symlink|follow|open"):
        module.normalize_structure(
            identity_authority=dict(
                base, authority_artifact_sha256=hashlib.sha256(valid_payload).hexdigest(),
            ),
            authority_artifact_path=link, **kwargs,
        )


def test_neutral_structure_layer_has_no_cm_import_or_snapshot_parser() -> None:
    source_path = REPO_ROOT / "platform/api/services/frustrampnn/structure.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("services.conformational_mapping") for name in imported_modules)
    assert "cm_complex_snapshot_v1" not in source

    module = _structure()
    payload = _single_model_pdb([("GLY", "X", 10, "")])
    with pytest.raises(module.StructureNormalizationError, match="unsupported"):
        module.normalize_structure(
            input_path=Path("unused.pdb"), output_pdb_path=Path("unused-out.pdb"),
            map_path=Path("unused-map.json"), target_id="t", parent_job_id="j",
            candidate_id="c", identity_authority={
                "kind": "cm_complex_snapshot_v1",
                "identity_domain": "source_authoritative",
                "authority_artifact_sha256": hashlib.sha256(payload).hexdigest(),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
            },
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )


def test_output_parent_symlink_is_rejected_and_pair_publication_rolls_back(tmp_path: Path, monkeypatch) -> None:
    module = _structure()
    payload = _single_model_pdb([("GLY", "A", 1, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    real = tmp_path / "real"; real.mkdir()
    linked = tmp_path / "linked"; linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(module.StructureNormalizationError, match="symlink"):
        module.normalize_structure(
            input_path=source, output_pdb_path=linked / "out.pdb", map_path=linked / "map.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )

    lexical_source = linked / ".." / "source.pdb"
    with pytest.raises(module.StructureNormalizationError, match="lexical|unsafe"):
        module.normalize_structure(
            input_path=lexical_source, output_pdb_path=real / "lexical.pdb",
            map_path=real / "lexical.json", target_id="t", parent_job_id="j",
            candidate_id="c", identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )
    lexical_output = linked / ".." / "real" / "lexical.pdb"
    with pytest.raises(module.StructureNormalizationError, match="lexical|unsafe"):
        module.normalize_structure(
            input_path=source, output_pdb_path=lexical_output,
            map_path=real / "lexical.json", target_id="t", parent_job_id="j",
            candidate_id="c", identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )

    output = real / "out.pdb"; sidecar = real / "map.json"
    output.write_bytes(b"old-pdb"); sidecar.write_bytes(b"old-map")
    original_replace = module.os.replace
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second publication failure")
        return original_replace(*args, **kwargs)
    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(module.StructureNormalizationError, match="publication|injected|publish"):
        module.normalize_structure(
            input_path=source, output_pdb_path=output, map_path=sidecar,
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )
    assert output.read_bytes() == b"old-pdb"
    assert sidecar.read_bytes() == b"old-map"


def test_pair_publication_restore_failure_preserves_the_only_recoverable_original(
    tmp_path: Path, monkeypatch,
) -> None:
    module = _structure()
    payload = _single_model_pdb([("GLY", "A", 1, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    output = tmp_path / "out.pdb"; sidecar = tmp_path / "map.json"
    output.write_bytes(b"original-pdb-bytes")
    sidecar.write_bytes(b"original-map-bytes")
    original_replace = module.os.replace
    calls = 0

    def fail_publication_then_first_restore(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected publication failure")
        if calls == 6:
            raise OSError("injected original restore failure")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_publication_then_first_restore)
    with pytest.raises(module.StructureNormalizationError, match="publication|injected|publish"):
        module.normalize_structure(
            input_path=source, output_pdb_path=output, map_path=sidecar,
            target_id="t", parent_job_id="j", candidate_id="c",
            identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )
    recovery = list(tmp_path.glob(".out.pdb.backup.*"))
    assert len(recovery) == 1, "the only recoverable original backup was deleted"
    assert recovery[0].read_bytes() == b"original-pdb-bytes"
    assert sidecar.read_bytes() == b"original-map-bytes"


def test_pdb_authorized_chain_identity_is_preserved_and_explicit_sequence_covers_excluded_rows(
    tmp_path: Path,
) -> None:
    module = _structure()
    lines: list[str] = []
    serial = 1
    for atom in ("N", "CA", "C", "O"):
        lines.append(_pdb_atom(serial, atom, "GLY", "Z", 7, x=float(serial)))
        serial += 1
    for atom in ("N", "CA", "C"):
        lines.append(_pdb_atom(serial, atom, "SER", "Z", 8, x=float(serial)))
        serial += 1
    payload = ("".join(lines) + "END\n").encode("ascii")
    source = tmp_path / "identity.pdb"; source.write_bytes(payload)
    output = tmp_path / "identity.normalized.pdb"
    result = module.normalize_structure(
        input_path=source, output_pdb_path=output, map_path=tmp_path / "identity.map.json",
        target_id="t", parent_job_id="j", candidate_id="c",
        identity_authority=_pdb_authority(payload),
        protein_selection={"mode": "explicit", "entities": [{
            "entity_instance_id": "pdb:Z", "source_entity_id": None,
            "label_asym_id": None, "auth_asym_id": "Z", "sequence": "GS",
        }]},
        selected_model=1, altloc_policy="blank_or_explicit:A",
    )
    assert [row["wt"] for row in result["rows"]] == ["G", "S"]
    assert [row["status"] for row in result["rows"]] == ["mapped", "missing_backbone"]
    assert result["model_ready_sequence"] == "G"
    assert {line[21] for line in output.read_text("ascii").splitlines() if line.startswith("ATOM")} == {"Z"}
    assert result["rows"][0]["pdb_chain_id"] == "Z"


def test_mmcif_authority_rejects_missing_authorized_label_sequence_coverage(tmp_path: Path) -> None:
    module = _structure()
    lines = _cif().decode().splitlines()
    rows = [line for line in lines if line.startswith("ATOM ")]
    assert len(rows) == 8
    payload = ("\n".join(line for line in lines if line not in rows[4:8]) + "\n").encode()
    source = tmp_path / "missing-label.cif"; source.write_bytes(payload)
    authority = {
        "kind": "mmcif_atom_site_v1", "identity_domain": "source_authoritative",
        "authority_artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "entities": [{"entity_instance_id": "protein-1", "source_entity_id": "1",
                      "label_asym_id": "AA", "auth_asym_id": "X", "sequence": "GA"}],
    }
    with pytest.raises(module.StructureNormalizationError, match="coverage|sequence|label"):
        module.normalize_structure(
            input_path=source, output_pdb_path=tmp_path / "out.pdb", map_path=tmp_path / "map.json",
            target_id="t", parent_job_id="j", candidate_id="c", identity_authority=authority,
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )


def test_pair_publication_failure_leaves_no_half_pair_when_outputs_were_absent(
    tmp_path: Path, monkeypatch,
) -> None:
    module = _structure()
    payload = _single_model_pdb([("GLY", "A", 1, "")])
    source = tmp_path / "source.pdb"; source.write_bytes(payload)
    output = tmp_path / "new.pdb"; sidecar = tmp_path / "new.json"
    original_replace = module.os.replace
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected absent-pair failure")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(module.StructureNormalizationError, match="publication|failure"):
        module.normalize_structure(
            input_path=source, output_pdb_path=output, map_path=sidecar,
            target_id="t", parent_job_id="j", candidate_id="c",
            identity_authority=_pdb_authority(payload),
            protein_selection={"mode": "all_protein_entities"}, selected_model=1,
            altloc_policy="blank_or_explicit:A",
        )
    assert not output.exists()
    assert not sidecar.exists()
