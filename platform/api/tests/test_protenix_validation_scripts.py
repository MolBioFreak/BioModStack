from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _write_pdb(path: Path, chains: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atom_index = 1
    x_offset = 0.0
    lines: list[str] = []
    for chain_id, sequence in chains.items():
        residue_index = 1
        for residue_name in ["ALA"] * len(sequence):
            x = x_offset + (residue_index * 1.5)
            y = x_offset
            z = 0.0
            lines.append(
                f"ATOM  {atom_index:5d}  CA  {residue_name:>3} {chain_id}{residue_index:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 10.00           C"
            )
            atom_index += 1
            residue_index += 1
        x_offset += 10.0
    lines.append("TER")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_python(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _load_prep_protenix_batch_module(monkeypatch) -> object:
    fake_constraints = types.ModuleType("protenix_constraint_utils")
    fake_constraints.infer_target_pocket_residues = lambda **kwargs: []
    monkeypatch.setitem(sys.modules, "protenix_constraint_utils", fake_constraints)

    spec = importlib.util.spec_from_file_location("prep_protenix_batch", SCRIPTS_DIR / "prep_protenix_batch.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_batch_antiberty_module(monkeypatch) -> object:
    fake_antiberty = types.ModuleType("antiberty")
    fake_torch = types.ModuleType("torch")

    class _Runner:
        def __init__(self):
            self.model = types.SimpleNamespace(parameters=lambda: iter(()))

        def pseudo_log_likelihood(self, sequences, batch_size=32):
            return [float(len(sequence)) for sequence in sequences]

    fake_antiberty.AntiBERTyRunner = _Runner
    monkeypatch.setitem(sys.modules, "antiberty", fake_antiberty)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    spec = importlib.util.spec_from_file_location("batch_antiberty", SCRIPTS_DIR / "batch_antiberty.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_align_protenix_module(monkeypatch) -> object:
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    fake_bio = types.ModuleType("Bio")
    fake_bio_pdb = types.ModuleType("Bio.PDB")

    class _PlaceholderParser:
        def __init__(self, QUIET=True):
            pass

        def get_structure(self, name, path):
            return types.SimpleNamespace(path=Path(path), chain_ids=["A", "B", "C"])

    class _PlaceholderIO:
        def set_structure(self, structure):
            self.structure = structure

        def save(self, path):
            Path(path).write_text("MODEL\nEND\n", encoding="utf-8")

    class _PlaceholderSuperimposer:
        def __init__(self):
            self.rms = 0.0

        def set_atoms(self, ref_atoms, mobile_atoms):
            self.rms = 0.0

        def apply(self, atoms):
            return None

    fake_bio_pdb.MMCIFParser = _PlaceholderParser
    fake_bio_pdb.PDBParser = _PlaceholderParser
    fake_bio_pdb.PDBIO = _PlaceholderIO
    fake_bio_pdb.Superimposer = _PlaceholderSuperimposer
    monkeypatch.setitem(sys.modules, "Bio", fake_bio)
    monkeypatch.setitem(sys.modules, "Bio.PDB", fake_bio_pdb)

    spec = importlib.util.spec_from_file_location("align_protenix", SCRIPTS_DIR / "align_protenix.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prep_protenix_batch_emits_chain_roles_with_resolved_target_alias(tmp_path: Path, monkeypatch) -> None:
    module = _load_prep_protenix_batch_module(monkeypatch)
    design_pdb = tmp_path / "design.pdb"
    target_pdb = tmp_path / "target.pdb"
    output_json = tmp_path / "input.json"
    chain_roles_json = tmp_path / "chain_roles.json"
    reference_dir = tmp_path / "validation_designs"

    _write_pdb(design_pdb, {"A": "AAA", "B": "BBB"})
    _write_pdb(target_pdb, {"T": "CCC"})

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prep_protenix_batch.py",
            "--pdb_files",
            str(design_pdb),
            "--out_json",
            str(output_json),
            "--chain_roles_json",
            str(chain_roles_json),
            "--out_pdb_dir",
            str(reference_dir),
            "--target_pdb",
            str(target_pdb),
            "--target_chains",
            "T",
            "--external-target-as-target",
        ],
    )

    module.main()

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert [entry["proteinChain"]["id"][0] for entry in payload[0]["sequences"] if "proteinChain" in entry] == ["A", "B", "C"]

    chain_roles = json.loads(chain_roles_json.read_text(encoding="utf-8"))
    assert chain_roles["all_binder_chain_ids"] == ["A", "B"]
    assert chain_roles["all_target_chain_ids"] == ["C"]
    assert chain_roles["entries"][0]["binder_chain_ids"] == ["A", "B"]
    assert chain_roles["entries"][0]["target_chain_ids"] == ["C"]

    rewritten_pdb = reference_dir / "design.pdb"
    assert rewritten_pdb.exists()
    rewritten_chain_ids = {
        line[21].strip()
        for line in rewritten_pdb.read_text(encoding="utf-8").splitlines()
        if line.startswith(("ATOM", "HETATM"))
    }
    assert rewritten_chain_ids == {"A", "B", "C"}


def test_align_protenix_main_uses_chain_roles_manifest_for_task_args(tmp_path: Path, monkeypatch) -> None:
    module = _load_align_protenix_module(monkeypatch)
    design_dir = tmp_path / "designs"
    raw_predictions = tmp_path / "raw_predictions"
    output_dir = tmp_path / "predictions"
    chain_roles_json = tmp_path / "chain_roles.json"

    design_dir.mkdir(parents=True, exist_ok=True)
    raw_predictions.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "candidate.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    (raw_predictions / "candidate_summary_confidence_sample_0.json").write_text(json.dumps({"ptm": 0.9}), encoding="utf-8")
    chain_roles_json.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "name": "candidate",
                        "binder_chain_ids": ["A", "B"],
                        "target_chain_ids": ["C"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    seen_tasks = []

    def _fake_align_structure(task):
        seen_tasks.append(task)
        return (task[1].name, None)

    monkeypatch.setattr(module, "align_structure", _fake_align_structure)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "align_protenix.py",
            "--design_dir",
            str(design_dir),
            "--protenix_dir",
            str(raw_predictions),
            "--output_dir",
            str(output_dir),
            "--design_type",
            "binder",
            "--binder_chains",
            "X",
            "--target_chains",
            "Y",
            "--chain_roles_json",
            str(chain_roles_json),
            "--ncpus",
            "1",
        ],
    )

    module.main()

    assert len(seen_tasks) == 1
    assert seen_tasks[0][4] == "A,B"
    assert seen_tasks[0][5] == "C"


def test_align_protenix_align_structure_separates_metrics_from_aligned_error_sidecars(tmp_path: Path, monkeypatch) -> None:
    module = _load_align_protenix_module(monkeypatch)
    design_path = tmp_path / "candidate.pdb"
    output_dir = tmp_path / "predictions"
    protenix_dir = tmp_path / "raw_predictions"
    output_dir.mkdir(parents=True, exist_ok=True)
    protenix_dir.mkdir(parents=True, exist_ok=True)
    design_path.write_text("MODEL\nEND\n", encoding="utf-8")

    summary_json = protenix_dir / "candidate_summary_confidence_sample_0.json"
    summary_json.write_text(json.dumps({"ptm": 0.9}), encoding="utf-8")
    cif_path = protenix_dir / "candidate_sample_0.cif"
    cif_path.write_text("data_candidate\n#\n", encoding="utf-8")
    full_data_json = protenix_dir / "candidate_full_data_sample_0.json"
    full_data_json.write_text(json.dumps({"pae": [[0.0]]}), encoding="utf-8")

    class _FakeStructure:
        def __init__(self, chain_ids):
            self.chain_ids = chain_ids

        def get_atoms(self):
            return []

        def get_models(self):
            return iter(())

    class _FakeParser:
        def __init__(self, QUIET=True):
            pass

        def get_structure(self, name, path):
            return _FakeStructure(["A", "B", "C"])

    class _FakeIO:
        def set_structure(self, structure):
            self.structure = structure

        def save(self, path):
            Path(path).write_text("MODEL\nEND\n", encoding="utf-8")

    class _FakeSuperimposer:
        def __init__(self):
            self.rms = 0.0

        def set_atoms(self, ref_atoms, mobile_atoms):
            self.rms = 0.0

        def apply(self, atoms):
            return None

    class _FakeAtom:
        def __init__(self):
            self.coord = [0.0, 0.0, 0.0]

    monkeypatch.setattr(module, "PDBParser", _FakeParser)
    monkeypatch.setattr(module, "MMCIFParser", _FakeParser)
    monkeypatch.setattr(module, "PDBIO", _FakeIO)
    monkeypatch.setattr(module, "Superimposer", _FakeSuperimposer)
    monkeypatch.setattr(module, "get_chain_ids", lambda structure: list(structure.chain_ids))
    monkeypatch.setattr(module, "renumber_mobile_residues_by_order", lambda *args, **kwargs: False)
    monkeypatch.setattr(module, "remap_mobile_chain_ids_by_order", lambda *args, **kwargs: False)
    monkeypatch.setattr(module, "replace_target_chains", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "rmsd_without_refit", lambda ref_atoms, mobile_atoms: 0.0)
    monkeypatch.setattr(
        module,
        "get_matched_ca_atoms",
        lambda ref_structure, mobile_structure, chains=None: ([_FakeAtom(), _FakeAtom(), _FakeAtom()], [_FakeAtom(), _FakeAtom(), _FakeAtom()]),
    )

    design_name, error = module.align_structure(
        (
            design_path,
            summary_json,
            output_dir,
            "binder",
            "A,B",
            "C",
            "flexible",
            None,
        )
    )

    assert design_name == "candidate_sample_0"
    assert error is None

    metrics_path = output_dir / "candidate_summary_confidence_sample_0.json"
    aligned_error_path = output_dir / "aligned_error" / "candidate_full_data_sample_0.json"
    assert (output_dir / "candidate_sample_0.pdb").exists()
    assert (output_dir / "candidate_sample_0.cif").exists()
    assert metrics_path.exists()
    assert aligned_error_path.exists()
    assert sorted(path.name for path in output_dir.glob("*.json")) == ["candidate_summary_confidence_sample_0.json"]

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["validator"] == "protenix"
    assert metrics["aligned_error_artifact"] == "aligned_error/candidate_full_data_sample_0.json"
    assert metrics["aligned_error_format"] == "protenix_full_json"


def test_batch_antiberty_load_sequences_filters_to_configured_chain_ids(tmp_path: Path, monkeypatch) -> None:
    module = _load_batch_antiberty_module(monkeypatch)
    module.SeqIO = None

    pdb_path = tmp_path / "validated_design.pdb"
    _write_pdb(
        pdb_path,
        {
            "A": "A" * 24,
            "T": "T" * 24,
        },
    )

    filtered = module.load_sequences([str(pdb_path)], allowed_chain_ids={"A"})
    assert [(record["file"], record["chain"]) for record in filtered] == [("validated_design.pdb", "A")]

    unfiltered = module.load_sequences([str(pdb_path)], allowed_chain_ids=set())
    assert sorted((record["file"], record["chain"]) for record in unfiltered) == [
        ("validated_design.pdb", "A"),
        ("validated_design.pdb", "T"),
    ]
