from __future__ import annotations

import builtins
import importlib.util
import json
import math
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_confornets_inference.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_confornets_inference_science", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdb_atom(serial: int, chain: str, residue_name: str, residue_number: int, xyz: tuple[float, float, float]) -> str:
    x, y, z = xyz
    return (
        f"ATOM  {serial:5d}  CA  {residue_name:>3s} {chain:1s}{residue_number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00           C"
    )


def _write_pdb(path: Path, atoms: list[tuple[str, str, int, tuple[float, float, float]]]) -> None:
    path.write_text(
        "\n".join(
            _pdb_atom(index, chain, residue_name, residue_number, xyz)
            for index, (chain, residue_name, residue_number, xyz) in enumerate(atoms, start=1)
        )
        + "\nEND\n",
        encoding="utf-8",
    )


def _base_atoms() -> list[tuple[str, str, int, tuple[float, float, float]]]:
    return [
        ("A", "ALA", 1, (0.0, 0.0, 0.0)),
        ("A", "GLY", 2, (2.0, 0.0, 0.0)),
        ("A", "SER", 3, (0.0, 3.0, 0.0)),
        ("A", "THR", 4, (0.0, 0.0, 4.0)),
    ]


def _write_mmcif(
    path: Path,
    atoms: list[tuple[str, str, int, tuple[float, float, float]]],
    *,
    reordered_columns: bool,
) -> None:
    columns = [
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
        "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
        "B_iso_or_equiv", "pdbx_formal_charge", "auth_seq_id", "auth_comp_id",
        "auth_asym_id", "auth_atom_id", "pdbx_PDB_model_num",
    ]
    if reordered_columns:
        columns = [
            "auth_asym_id", "Cartn_z", "group_PDB", "auth_seq_id", "label_atom_id",
            "Cartn_x", "auth_comp_id", "pdbx_PDB_ins_code", "Cartn_y", "auth_atom_id",
            "label_alt_id", "id", "type_symbol", "label_comp_id", "label_asym_id",
            "label_entity_id", "label_seq_id", "occupancy", "B_iso_or_equiv",
            "pdbx_formal_charge", "pdbx_PDB_model_num",
        ]
    rows = []
    for index, (chain, residue_name, residue_number, (x, y, z)) in enumerate(atoms, start=1):
        values = {
            "group_PDB": "ATOM", "id": str(index), "type_symbol": "C",
            "label_atom_id": "CA", "label_alt_id": ".", "label_comp_id": residue_name,
            "label_asym_id": chain, "label_entity_id": "1", "label_seq_id": str(residue_number),
            "pdbx_PDB_ins_code": ".", "Cartn_x": str(x), "Cartn_y": str(y),
            "Cartn_z": str(z), "occupancy": "1.0", "B_iso_or_equiv": "20.0",
            "pdbx_formal_charge": "?", "auth_seq_id": str(residue_number),
            "auth_comp_id": residue_name, "auth_asym_id": chain, "auth_atom_id": "CA",
            "pdbx_PDB_model_num": "1",
        }
        rows.append(" ".join(values[column] for column in columns))
    path.write_text(
        "data_test\n#\nloop_\n"
        + "\n".join(f"_atom_site.{column}" for column in columns)
        + "\n"
        + "\n".join(rows)
        + "\n#\n",
        encoding="utf-8",
    )


def test_ca_rmsd_matches_authoritative_identities_not_file_order(tmp_path: Path) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    reordered = tmp_path / "reordered.pdb"
    atoms = _base_atoms()
    _write_pdb(first, atoms)
    _write_pdb(reordered, [atoms[2], atoms[0], atoms[3], atoms[1]])

    assert runner._ca_rmsd(first, reordered) == pytest.approx(0.0, abs=1e-6)


def test_ca_rmsd_performs_kabsch_for_rigid_rotation_and_translation(tmp_path: Path) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    transformed = tmp_path / "transformed.pdb"
    atoms = _base_atoms()
    rotated = []
    for chain, residue_name, residue_number, (x, y, z) in atoms:
        # Proper 90-degree rotation about Z, then translation.
        rotated.append((chain, residue_name, residue_number, (-y + 11.0, x - 7.0, z + 3.5)))
    _write_pdb(first, atoms)
    _write_pdb(transformed, rotated)

    assert runner._ca_rmsd(first, transformed) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    "atoms",
    [
        _base_atoms()[:1],
        _base_atoms()[:2],
        [
            ("A", "ALA", 1, (0.0, 0.0, 0.0)),
            ("A", "GLY", 2, (1.0, 1.0, 1.0)),
            ("A", "SER", 3, (2.0, 2.0, 2.0)),
        ],
    ],
    ids=["one-point", "two-points", "collinear-points"],
)
def test_ca_rmsd_rejects_underdetermined_kabsch_geometry(tmp_path: Path, atoms) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    second = tmp_path / "second.pdb"
    _write_pdb(first, atoms)
    _write_pdb(second, atoms)

    with pytest.raises(runner.CoordinateIdentityError, match="at least three non-collinear"):
        runner._ca_rmsd(first, second)


def test_ca_rmsd_parses_mmcif_atom_site_by_column_name_not_column_order(tmp_path: Path) -> None:
    runner = _load_runner()
    canonical = tmp_path / "canonical.cif"
    reordered = tmp_path / "reordered.cif"
    atoms = _base_atoms()
    _write_mmcif(canonical, atoms, reordered_columns=False)
    _write_mmcif(reordered, atoms, reordered_columns=True)

    assert runner._ca_rmsd(canonical, reordered) == pytest.approx(0.0, abs=1e-8)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda atoms: [("B", *atom[1:]) if index == 0 else atom for index, atom in enumerate(atoms)], "identity"),
        (lambda atoms: [(atom[0], "VAL", atom[2], atom[3]) if index == 1 else atom for index, atom in enumerate(atoms)], "identity"),
        (lambda atoms: atoms[:-1], "identity"),
    ],
)
def test_ca_rmsd_rejects_chain_residue_and_unequal_identity_sets(
    tmp_path: Path, mutation, message: str
) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    second = tmp_path / "second.pdb"
    atoms = _base_atoms()
    _write_pdb(first, atoms)
    _write_pdb(second, mutation(atoms))

    with pytest.raises(runner.CoordinateIdentityError, match=message):
        runner._ca_rmsd(first, second)


def test_ca_rmsd_rejects_duplicate_authoritative_identity(tmp_path: Path) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    duplicate = tmp_path / "duplicate.pdb"
    atoms = _base_atoms()
    _write_pdb(first, atoms)
    _write_pdb(duplicate, [*atoms, atoms[0]])

    with pytest.raises(runner.CoordinateIdentityError, match="duplicate"):
        runner._ca_rmsd(first, duplicate)


def test_ca_rmsd_fails_closed_when_numpy_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    second = tmp_path / "second.pdb"
    atoms = _base_atoms()
    _write_pdb(first, atoms)
    _write_pdb(second, atoms)
    real_import = builtins.__import__

    def deny_numpy(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "numpy":
            raise ImportError("numpy deliberately unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", deny_numpy)
    with pytest.raises(RuntimeError, match="Kabsch RMSD requires numpy"):
        runner._ca_rmsd(first, second)


def test_classical_mds_fails_closed_when_numpy_is_unavailable(monkeypatch) -> None:
    runner = _load_runner()
    real_import = builtins.__import__

    def deny_numpy(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "numpy":
            raise ImportError("numpy deliberately unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", deny_numpy)
    with pytest.raises(RuntimeError, match="classical metric MDS requires numpy"):
        runner._mds_coordinates(
            [[0.0, 1.0, 2.0], [1.0, 0.0, 1.5], [2.0, 1.5, 0.0]]
        )


def test_classical_mds_rejects_missing_distances_explicitly() -> None:
    runner = _load_runner()
    with pytest.raises(ValueError, match="missing"):
        runner._mds_coordinates([[0.0, None], [None, 0.0]])


def test_classical_mds_reconstructs_a_euclidean_three_four_five_triangle() -> None:
    runner = _load_runner()
    expected = [[0.0, 3.0, 4.0], [3.0, 0.0, 5.0], [4.0, 5.0, 0.0]]
    coordinates = runner._mds_coordinates(expected)
    assert sum(point["x"] for point in coordinates) == pytest.approx(0.0, abs=2e-6)
    assert sum(point["y"] for point in coordinates) == pytest.approx(0.0, abs=2e-6)
    for left in range(3):
        for right in range(left + 1, 3):
            observed = math.dist(
                (coordinates[left]["x"], coordinates[left]["y"]),
                (coordinates[right]["x"], coordinates[right]["y"]),
            )
            assert observed == pytest.approx(expected[left][right], abs=2e-6)


def test_single_sample_pairwise_diversity_is_typed_missing_not_zero(tmp_path: Path) -> None:
    runner = _load_runner()
    raw = tmp_path / "raw"
    output = tmp_path / "output"
    raw.mkdir()
    sample = raw / "sample.cif"
    reference = tmp_path / "reference.cif"
    _write_mmcif(sample, _base_atoms(), reordered_columns=False)
    _write_mmcif(reference, _base_atoms(), reordered_columns=True)
    request = {
        "task": "diversity",
        "query_id": "query",
        "test_case": "single",
        "input_hashes": {"sequence_sha256": "a" * 64},
        "params": {"compute_evaluation": True, "rmsd_threshold": 3.0},
        "references": [{"name": "reference", "staged_path": str(reference)}],
    }

    runner._normalize_outputs(request, raw, output)
    samples = json.loads((output / "samples.json").read_text(encoding="utf-8"))
    assert samples[0]["pairwise_diversity"] == {
        "status": "unavailable",
        "reason": "fewer_than_two_generated_samples",
        "min_pairwise_rmsd": None,
        "mean_pairwise_rmsd": None,
        "max_pairwise_rmsd": None,
    }


def test_ca_rmsd_preserves_angstrom_scale(tmp_path: Path) -> None:
    runner = _load_runner()
    first = tmp_path / "first.pdb"
    distorted = tmp_path / "distorted.pdb"
    atoms = _base_atoms()
    changed = list(atoms)
    chain, residue_name, residue_number, (x, y, z) = changed[-1]
    changed[-1] = (chain, residue_name, residue_number, (x, y, z + 2.0))
    _write_pdb(first, atoms)
    _write_pdb(distorted, changed)

    value = runner._ca_rmsd(first, distorted)
    assert math.isfinite(value)
    assert value > 0.1
    assert value < 2.0


def test_persisted_reporting_metadata_declares_exact_scientific_methods() -> None:
    from services.result_ingester import _CONFORNETS_REPORTING_SEMANTICS

    metadata = _CONFORNETS_REPORTING_SEMANTICS
    assert metadata["ca_identity"] == (
        "(model, auth_asym_id, auth_seq_id, insertion_code, "
        "auth_comp_id, auth_atom_id)"
    )
    assert metadata["ca_rmsd_method"] == "proper_rotation_kabsch_svd_in_angstroms"
    assert metadata["landscape_embedding_method"].startswith(
        "classical_metric_mds_double_centered_squared_distances"
    )
    assert "exact_identity_matched" in metadata["reference_evaluation_semantics"]
