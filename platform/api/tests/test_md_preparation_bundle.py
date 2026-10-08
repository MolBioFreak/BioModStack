from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_profiles_are_exact_and_reject_free_form_chemistry() -> None:
    from scripts.bms_md.chemistry.prepare import preparation_profile
    assert preparation_profile("amber_ff19sb_opc_protein_v1").leaprc == (
        "leaprc.protein.ff19SB", "leaprc.water.opc")
    protein_dna = preparation_profile("amber_ff19sb_ol15_opc_protein_dna_v1")
    assert protein_dna.leaprc == (
        "leaprc.protein.ff19SB", "leaprc.DNA.OL15", "leaprc.water.opc")
    assert protein_dna.gromacs_gpu_offload == "full_forces"
    lifecycle = preparation_profile("amber_ff19sb_opc_lifecycle_v1")
    assert lifecycle.leaprc == ("leaprc.protein.ff19SB", "leaprc.water.opc")
    assert lifecycle.solvent_box == "OPCBOX"
    assert lifecycle.gromacs_gpu_offload == "full_forces"
    with pytest.raises(ValueError, match="unsupported preparation profile"):
        preparation_profile("operator_supplied_force_field")


def test_preprocessing_preserves_chain_boundaries_while_removing_source_water(tmp_path: Path) -> None:
    from scripts.bms_md.chemistry.amber_prepare_worker import _preprocess_pdb

    source = tmp_path / "source.pdb"
    prepared = tmp_path / "prepared.pdb"
    source.write_text(
        "ATOM      1  P    DA 1   1      10.000  10.000  10.000  1.00 20.00           P  \n"
        "TER       2       DA 1   1                                                      \n"
        "ATOM      3  N   ALA 3   1      20.000  20.000  20.000  1.00 20.00           N  \n"
        "HETATM    4  O   HOH 3 101      21.000  21.000  21.000  1.00 20.00           O  \n"
        "END\n",
        encoding="utf-8",
    )

    _preprocess_pdb(source, prepared)

    records = prepared.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("ATOM") for line in records) == 2
    assert sum(line.startswith("TER") for line in records) == 1
    assert not any("HOH" in line for line in records)


def test_bundle_is_atomic_immutable_and_checksum_bound(tmp_path: Path) -> None:
    from scripts.bms_md.chemistry.prepare import build_preparation_bundle
    source = tmp_path / "tiny.pdb"; source.write_text("END\n", encoding="utf-8")
    lock = tmp_path / "lock.txt"; lock.write_text("ambertools=24.8\nparmed=4.3.0\n", encoding="utf-8")
    calls: list[list[str]] = []
    def runner(command: list[str], **kwargs):
        calls.append(command); cwd = Path(kwargs["cwd"])
        for name, content in {
            "system.top": "[ system ]\nTiny\n", "system.pdb": "END\n",
            "system.gro": "Tiny\n0\n1 1 1\n", "system.prmtop": "prmtop\n",
            "system.inpcrd": "inpcrd\n", "posre.itp": "[ position_restraints ]\n",
        }.items():
            (cwd / name).write_text(content)
        (cwd / "preparation_report.json").write_text(json.dumps({
            "atom_count": 1, "residue_count": 1, "water_count": 0,
            "ionic_strength_molar": 0.15, "neutralize": True,
        }))
        return subprocess.CompletedProcess(command, 0, "", "")
    destination = tmp_path / "bundle"
    manifest = build_preparation_bundle(source_structure=source, destination=destination,
        profile_id="amber_ff19sb_opc_protein_v1", profile_sha256="a" * 64, runtime_lock=lock, runner=runner)
    assert len(calls) == 1 and Path(calls[0][0]).name.startswith("python")
    assert manifest["schema"] == "bms.md.preparation-bundle.v1"
    assert manifest["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["runtime"]["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert {item["path"] for item in manifest["files"]} == {
        "source.pdb", "system.gro", "system.inpcrd", "system.pdb", "system.prmtop", "system.top",
        "posre.itp"}
    for item in manifest["files"]:
        artifact = destination / item["path"]
        assert item["bytes"] == artifact.stat().st_size
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert artifact.stat().st_mode & 0o222 == 0
    assert json.loads((destination / "preparation_manifest.json").read_text()) == manifest
    from scripts.bms_md.chemistry.prepare import verify_preparation_bundle
    assert verify_preparation_bundle(
        destination,
        expected_profile_id="amber_ff19sb_opc_protein_v1",
        expected_profile_sha256="a" * 64,
    )["bundle_sha256"] == manifest["bundle_sha256"]


def test_bundle_verification_rejects_tampered_artifact(tmp_path: Path) -> None:
    from scripts.bms_md.chemistry.prepare import (
        PreparationError, build_preparation_bundle, verify_preparation_bundle,
    )
    source = tmp_path / "input.pdb"; source.write_text("ATOM\n")
    lock = tmp_path / "runtime.lock"; lock.write_text("pinned\n")
    def runner(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        request = json.loads((cwd / Path(command[-1]).name).read_text())
        for name, content in {
            "source.pdb": "ATOM\n", "system.gro": "Tiny\n0\n1 1 1\n",
            "system.inpcrd": "coords\n", "system.pdb": "END\n",
            "system.prmtop": "top\n", "system.top": "[ system ]\nTiny\n",
            "posre.itp": "[ position_restraints ]\n",
        }.items(): (cwd / name).write_text(content)
        (cwd / "preparation_report.json").write_text(json.dumps({"profile_id": request["profile"]["id"]}))
        return subprocess.CompletedProcess(command, 0, "", "")
    destination = tmp_path / "bundle"
    build_preparation_bundle(source_structure=source, destination=destination,
        profile_id="amber_ff19sb_opc_protein_v1", profile_sha256="a" * 64,
        runtime_lock=lock, runner=runner)
    (destination / "system.top").chmod(0o644); (destination / "system.top").write_text("tampered\n")
    with pytest.raises(PreparationError, match="digest mismatch"):
        verify_preparation_bundle(destination)


def test_bundle_fails_closed_on_worker_error(tmp_path: Path) -> None:
    from scripts.bms_md.chemistry.prepare import PreparationError, build_preparation_bundle
    source = tmp_path / "tiny.pdb"; source.write_text("END\n")
    lock = tmp_path / "lock.txt"; lock.write_text("locked\n")
    def failing_runner(command: list[str], **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "unmatched residue")
    destination = tmp_path / "bundle"
    with pytest.raises(PreparationError, match="chemistry preparation runtime failed"):
        build_preparation_bundle(source_structure=source, destination=destination,
            profile_id="amber_ff19sb_opc_protein_v1", profile_sha256="b" * 64,
            runtime_lock=lock, runner=failing_runner)
    assert not destination.exists()


def test_nextflow_preparation_runs_directly_in_pinned_preparation_image() -> None:
    module = (REPO_ROOT / "modules/experimental/molecular_dynamics/prepare.nf").read_text()
    config = (REPO_ROOT / "nextflow.config").read_text()

    assert "label 'MolecularDynamicsPreparation'" in module
    assert "worker_command=['apptainer', 'exec'" not in module
    assert "withLabel: MolecularDynamicsPreparation" in config
    label_start = config.index("withLabel: MolecularDynamicsPreparation")
    label_end = config.index("withLabel: MolecularDynamicsCpu", label_start)
    label = config[label_start:label_end]
    assert "md_preparation_container" in label
    assert "BMS_MD_PREPARATION_SIF=/opt/bms-md-preparation-runtime.sif" in label
    assert "BMS_MD_PREPARATION_RUNTIME_LOCK=/opt/bms-md-preparation-runtime.lock" in label


def test_position_restraints_are_bound_to_each_solute_molecule_type(tmp_path: Path, monkeypatch) -> None:
    from scripts.bms_md.chemistry.amber_prepare_worker import _install_position_restraints

    topology = tmp_path / "system.top"
    topology.write_text(
        "[ moleculetype ]\nProtein 3\n[ atoms ]\n"
        "1 C 1 ALA CA 1 0.0 12.01\n2 H 1 ALA HA 1 0.0 1.008\n"
        "[ bonds ]\n\n"
        "[ moleculetype ]\nDNA 3\n[ atoms ]\n"
        "1 P 1 DA P 1 0.0 30.97\n2 O 1 DA O1P 1 0.0 16.00\n"
        "[ bonds ]\n\n"
        "[ moleculetype ]\nSOL 2\n[ atoms ]\n"
        "1 OW 1 WAT O 1 0.0 16.00\n2 HW 1 WAT H1 1 0.0 1.008\n"
        "[ system ]\nPrepared system\n[ molecules ]\nProtein 1\nDNA 1\nSOL 10\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    count = _install_position_restraints(SimpleNamespace(), topology)

    assert count == 3
    assert (tmp_path / "posre.itp").read_text(encoding="utf-8").count("1000.0") == 3
    second = (tmp_path / "posre_1.itp").read_text(encoding="utf-8")
    assert second.count("1000.0") == 6
    assert "       1" in second and "       2" in second
    rendered = topology.read_text(encoding="utf-8")
    dna_header = rendered.rfind("[ moleculetype ]", 0, rendered.index("DNA 3"))
    solvent_header = rendered.rfind("[ moleculetype ]", 0, rendered.index("SOL 2"))
    assert rendered.index('#include "posre.itp"') < dna_header
    assert rendered.index('#include "posre_1.itp"') < solvent_header
    assert rendered.count("#ifdef POSRES") == 2


def test_preparation_validation_supplies_restraint_reference_coordinates() -> None:
    from scripts.bms_md.chemistry.amber_prepare_worker import _grompp_validation_command

    command = _grompp_validation_command(Path("/runtime/bin/gmx"))

    assert command[command.index("-r") + 1] == "system.gro"
    assert command[command.index("-c") + 1] == "system.gro"
    assert command[command.index("-maxwarn") + 1] == "0"


def test_gromacs_runner_stages_position_restraint_include_from_bundle(
    tmp_path: Path, monkeypatch,
) -> None:
    from scripts.bms_md.chemistry import prepare as preparation_module
    from scripts.bms_md.gromacs_pipeline import _consume_preparation_bundle

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("system.gro", "system.top", "posre.itp"):
        (bundle / name).write_text(name + "\n", encoding="utf-8")
    monkeypatch.setattr(
        preparation_module,
        "verify_preparation_bundle",
        lambda *args, **kwargs: {
            "bundle_sha256": "b" * 64,
            "files": [
                {"path": "system.gro"},
                {"path": "system.top"},
                {"path": "posre.itp"},
            ],
        },
    )

    class Ledger:
        def is_complete(self, stage, required):
            return False

        def mark_running(self, stage, command):
            pass

        def mark_completed(self, stage, required, **kwargs):
            self.required = required

    ledger = Ledger()
    output = tmp_path / "replica"
    _consume_preparation_bundle(
        {"chemistry": {"profile_id": "fixture", "profile_sha256": "a" * 64}},
        bundle,
        output,
        ledger,
    )

    assert (output / "system" / "posre.itp").read_text(encoding="utf-8") == "posre.itp\n"
    assert output / "system" / "posre.itp" in ledger.required
