from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PREP_BOLTZGEN = SCRIPTS_DIR / "prep_boltzgen.py"
FILTER_BOLTZGEN = SCRIPTS_DIR / "filter_boltzgen.py"
RUN_BOLTZGEN_WRAPPER = SCRIPTS_DIR / "run_boltzgen_wrapper.py"


def _write_pdb(path: Path, chain_id: str, residues: list[int]) -> None:
    lines: list[str] = []
    atom_index = 1
    for residue_number in residues:
        lines.append(
            f"ATOM  {atom_index:5d}  CA  ALA {chain_id}{residue_number:4d}    "
            f"{float(atom_index):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 10.00           C"
        )
        atom_index += 1
    lines.extend(["TER", "END", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_design_pdb(path: Path, sequence: str) -> None:
    lines: list[str] = []
    atom_index = 1
    for residue_number, _ in enumerate(sequence, start=1):
        lines.append(
            f"ATOM  {atom_index:5d}  CA  ALA A{residue_number:4d}    "
            f"{float(atom_index):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 10.00           C"
        )
        atom_index += 1
    lines.extend(["TER", "END", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_metrics_json(path: Path, *, design_id: str, sequence: str, affinity: float, design_ptm: float, rmsd: float) -> None:
    path.write_text(
        json.dumps(
            {
                "design_id": design_id,
                "designed_sequence": sequence,
                "affinity_probability": affinity,
                "design_ptm": design_ptm,
                "filter_rmsd": rmsd,
                "source": "boltzgen",
            }
        ),
        encoding="utf-8",
    )


def _run_python(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _load_run_boltzgen_wrapper_module() -> object:
    spec = importlib.util.spec_from_file_location("run_boltzgen_wrapper", RUN_BOLTZGEN_WRAPPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prep_boltzgen_maps_target_binding_site_to_binding_types(tmp_path: Path) -> None:
    target_pdb = tmp_path / "target.pdb"
    output_yaml = tmp_path / "boltzgen.yaml"

    _write_pdb(target_pdb, "T", [10, 11, 13])

    _run_python(
        PREP_BOLTZGEN,
        "--protocol",
        "nanobody-anything",
        "--nanobody_framework",
        "QVQLVESGGGLVQAGGSLRLSCAAS",
        "--target_pdb",
        str(target_pdb),
        "--binding_site_residues",
        "T10,T13",
        "--output_yaml",
        str(output_yaml),
    )

    payload = yaml.safe_load(output_yaml.read_text(encoding="utf-8"))
    proteins = [entry["protein"] for entry in payload["entities"] if "protein" in entry]
    target = next(protein for protein in proteins if protein["id"] == "T")
    binder = next(protein for protein in proteins if protein["id"] == "H")

    assert target["binding_types"] == {"binding": "1,3"}
    assert "include_proximity" not in binder


def test_prep_boltzgen_emits_scaffold_backed_nanobody_specs(tmp_path: Path) -> None:
    target_pdb = tmp_path / "target.pdb"
    scaffold_pdb = tmp_path / "scaffold.pdb"
    output_yaml = tmp_path / "boltzgen.yaml"

    _write_pdb(target_pdb, "T", [1, 2, 3, 4])
    _write_pdb(scaffold_pdb, "H", list(range(1, 131)))

    scaffold_specs = json.dumps(
        [
            {
                "name": "test_scaffold",
                "path": str(scaffold_pdb),
                "chain_id": "H",
                "spec": {
                    "path": str(scaffold_pdb),
                    "include": [{"chain": {"id": "H"}}],
                    "design": [{"chain": {"id": "H", "res_index": "27..35,56..64,105..117"}}],
                    "structure_groups": [
                        {"group": {"id": "H", "visibility": 2}},
                        {"group": {"id": "H", "visibility": 0, "res_index": "27..35,56..64,105..117"}},
                    ],
                    "exclude": [
                        {"chain": {"id": "H", "res_index": "27..35"}},
                        {"chain": {"id": "H", "res_index": "56..64"}},
                        {"chain": {"id": "H", "res_index": "105..117"}},
                    ],
                    "design_insertions": [
                        {"insertion": {"id": "H", "res_index": 27, "num_residues": "5..8"}},
                        {"insertion": {"id": "H", "res_index": 56, "num_residues": "6..10"}},
                        {"insertion": {"id": "H", "res_index": 105, "num_residues": "12..18"}},
                    ],
                    "reset_res_index": [{"chain": {"id": "H"}}],
                },
            }
        ]
    )

    _run_python(
        PREP_BOLTZGEN,
        "--protocol",
        "nanobody-anything",
        "--target_pdb",
        str(target_pdb),
        "--binding_site_residues",
        "T2,T3",
        "--nanobody_scaffold_specs",
        scaffold_specs,
        "--output_yaml",
        str(output_yaml),
    )

    payload = yaml.safe_load(output_yaml.read_text(encoding="utf-8"))

    assert payload["entities"][0]["file"]["binding_types"] == [{"chain": {"id": "T", "binding": "2..3"}}]
    scaffold_entry = payload["entities"][1]["file"]["path"]
    assert scaffold_entry == "test_scaffold_1.yaml"
    emitted_scaffold_yaml = yaml.safe_load((tmp_path / scaffold_entry).read_text(encoding="utf-8"))
    assert emitted_scaffold_yaml["design_insertions"][0]["insertion"]["num_residues"] == "5..8"


def test_run_boltzgen_wrapper_uses_diffusion_batch_size_flag(tmp_path: Path, monkeypatch) -> None:
    module = _load_run_boltzgen_wrapper_module()
    config_path = tmp_path / "config.yaml"
    out_dir = tmp_path / "out"
    config_path.write_text("entities: []\n", encoding="utf-8")

    seen_commands: list[str] = []

    monkeypatch.setattr(module, "report_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.os, "system", lambda command: seen_commands.append(command) or 0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_boltzgen_wrapper.py",
            "--config",
            str(config_path),
            "--out_dir",
            str(out_dir),
            "--num_designs",
            "5",
            "--diffusion_batch_size",
            "4",
        ],
    )

    module.main()

    assert seen_commands
    assert "--diffusion_batch_size 4" in seen_commands[0]
    assert "--batch_size" not in seen_commands[0]


def test_filter_boltzgen_applies_additional_filters_and_size_buckets(tmp_path: Path) -> None:
    designs = {
        "alpha": ("MKTW", 0.91, 0.90, 1.0),
        "beta": ("QHNSY", 0.85, 0.89, 1.2),
        "gamma": ("QHNSYWF", 0.92, 0.87, 1.1),
        "delta": ("QHNSYWFA", 0.40, 0.95, 1.0),
    }

    pdb_paths: list[str] = []
    json_paths: list[str] = []
    for design_id, (sequence, affinity, design_ptm, rmsd) in designs.items():
        pdb_path = tmp_path / f"{design_id}.pdb"
        json_path = tmp_path / f"confidence_{design_id}.json"
        _write_design_pdb(pdb_path, sequence)
        _write_metrics_json(
            json_path,
            design_id=design_id,
            sequence=sequence,
            affinity=affinity,
            design_ptm=design_ptm,
            rmsd=rmsd,
        )
        pdb_paths.append(str(pdb_path))
        json_paths.append(str(json_path))

    out_dir = tmp_path / "filtered"
    _run_python(
        FILTER_BOLTZGEN,
        "--pdbs",
        *pdb_paths,
        "--jsons",
        *json_paths,
        "--out_dir",
        str(out_dir),
        "--budget",
        "3",
        "--filter-biased",
        "false",
        "--additional-filters",
        "affinity_probability>0.8",
        "--size-buckets",
        "1-5:1 6-10:1",
    )

    kept = sorted(path.stem for path in out_dir.glob("*.pdb"))
    summary = json.loads((out_dir / "filter_summary.json").read_text(encoding="utf-8"))

    assert "gamma" in kept
    assert any(candidate in kept for candidate in ("alpha", "beta"))
    assert "delta" not in kept
    assert summary["final_count"] == 2


def test_filter_boltzgen_metrics_override_changes_selection(tmp_path: Path) -> None:
    designs = {
        "alpha": ("MKTWY", 0.20, 0.95, 0.6),
        "beta": ("QHNSY", 0.95, 0.70, 2.0),
    }

    pdb_paths: list[str] = []
    json_paths: list[str] = []
    for design_id, (sequence, affinity, design_ptm, rmsd) in designs.items():
        pdb_path = tmp_path / f"{design_id}.pdb"
        json_path = tmp_path / f"confidence_{design_id}.json"
        _write_design_pdb(pdb_path, sequence)
        _write_metrics_json(
            json_path,
            design_id=design_id,
            sequence=sequence,
            affinity=affinity,
            design_ptm=design_ptm,
            rmsd=rmsd,
        )
        pdb_paths.append(str(pdb_path))
        json_paths.append(str(json_path))

    default_dir = tmp_path / "default"
    override_dir = tmp_path / "override"

    _run_python(
        FILTER_BOLTZGEN,
        "--pdbs",
        *pdb_paths,
        "--jsons",
        *json_paths,
        "--out_dir",
        str(default_dir),
        "--budget",
        "1",
        "--filter-biased",
        "false",
    )
    _run_python(
        FILTER_BOLTZGEN,
        "--pdbs",
        *pdb_paths,
        "--jsons",
        *json_paths,
        "--out_dir",
        str(override_dir),
        "--budget",
        "1",
        "--filter-biased",
        "false",
        "--metrics-override",
        "plddt=none filter_rmsd=none",
    )

    default_kept = sorted(path.stem for path in default_dir.glob("*.pdb"))
    override_kept = sorted(path.stem for path in override_dir.glob("*.pdb"))

    assert default_kept == ["alpha"]
    assert override_kept == ["beta"]
