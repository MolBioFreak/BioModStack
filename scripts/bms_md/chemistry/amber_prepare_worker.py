from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

WATER_NAMES = {"HOH", "WAT", "OPC"}
ION_NAMES = {"NA", "CL", "Na+", "Cl-"}


def _preprocess_pdb(source: Path, destination: Path) -> list[str]:
    records: list[str] = []
    residue_order: list[tuple[str, str, str]] = []
    sulfurs: dict[tuple[str, str, str], tuple[float, float, float]] = {}
    for raw in source.read_text(encoding="utf-8", errors="strict").splitlines():
        if raw.startswith("TER"):
            if records and not records[-1].startswith("TER"):
                records.append(raw.ljust(80))
            continue
        if not raw.startswith(("ATOM  ", "HETATM")):
            continue
        line = raw.ljust(80)
        residue_name = line[17:20].strip()
        if residue_name in WATER_NAMES:
            continue
        residue = (line[21], line[22:26], line[26])
        if residue not in residue_order:
            residue_order.append(residue)
        if line[12:16].strip() == "SG" and residue_name in {"CYS", "CYX"}:
            sulfurs[residue] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        records.append(line)
    disulfides: list[tuple[tuple[str, str, str], tuple[str, str, str]]] = []
    claimed: set[tuple[str, str, str]] = set()
    keys = list(sulfurs)
    for index, left in enumerate(keys):
        if left in claimed:
            continue
        for right in keys[index + 1 :]:
            if right in claimed:
                continue
            distance = math.dist(sulfurs[left], sulfurs[right])
            if distance <= 2.5:
                disulfides.append((left, right)); claimed.update((left, right)); break
    renamed = []
    for line in records:
        residue = (line[21], line[22:26], line[26])
        if residue in claimed:
            line = line[:17] + "CYX" + line[20:]
        renamed.append(line.rstrip())
    destination.write_text("\n".join(renamed + ["END", ""]), encoding="utf-8")
    ordinal = {residue: index + 1 for index, residue in enumerate(residue_order)}
    return [f"bond system.{ordinal[left]}.SG system.{ordinal[right]}.SG" for left, right in disulfides]


def _leap_script(
    profile: dict[str, Any], padding_nm: float, positive_ions: int, negative_ions: int,
    prefix: str, disulfide_bonds: list[str]
) -> str:
    lines = [*(f"source {name}" for name in profile["leaprc"]), "system = loadPdb prepared_input.pdb", *disulfide_bonds, "check system"]
    lines.extend(
        (
            f"solvateOct system {profile['solvent_box']} {padding_nm * 10.0:.6f}",
        )
    )
    if positive_ions:
        lines.append(f"addIonsRand system Na+ {positive_ions}")
    if negative_ions:
        lines.append(f"addIonsRand system Cl- {negative_ions}")
    lines.extend((f"saveAmberParm system {prefix}.prmtop {prefix}.inpcrd", f"savePdb system {prefix}.pdb", "quit", ""))
    return "\n".join(lines)


def _run_tleap(script_name: str) -> str:
    tleap = Path(sys.executable).resolve().parent / "tleap"
    completed = subprocess.run([str(tleap), "-f", script_name], check=False, capture_output=True, text=True, timeout=600)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    summary = re.search(r"Exiting LEaP: Errors = (\d+); Warnings = (\d+); Notes = (\d+)", output)
    warning_bodies = re.findall(r"teLeap: Warning!\n([^\n]+)", output)
    allowed_warning_starts = (
        "The unperturbed charge of the unit",
        "Close contact of",
        " Converting N-terminal residue name",
        " Converting C-terminal residue name",
    )
    unexplained = [body for body in warning_bodies if not body.startswith(allowed_warning_starts)]
    if completed.returncode != 0 or summary is None or int(summary.group(1)) or unexplained:
        raise RuntimeError("tleap failed closed: " + output.strip()[-1200:])
    return output


def _composition(structure: Any) -> tuple[int, int, float]:
    waters = sum(1 for residue in structure.residues if residue.name in WATER_NAMES)
    ions = sum(1 for residue in structure.residues if residue.name in ION_NAMES)
    charge = float(sum(float(atom.charge) for atom in structure.atoms))
    return waters, ions, charge


def _install_position_restraints(structure: Any, topology_path: Path) -> int:
    restrained = [
        atom.idx + 1
        for atom in structure.atoms
        if atom.residue.name not in WATER_NAMES | ION_NAMES and int(atom.atomic_number or 0) > 1
    ]
    if not restrained:
        raise RuntimeError("prepared solute has no heavy atoms for position restraints")
    Path("posre.itp").write_text(
        "[ position_restraints ]\n; atom  type      fx      fy      fz\n"
        + "".join(f"{index:8d}     1  1000.0  1000.0  1000.0\n" for index in restrained),
        encoding="utf-8",
    )
    lines = topology_path.read_text(encoding="utf-8").splitlines()
    molecule_headers = [index for index, line in enumerate(lines) if line.strip() == "[ moleculetype ]"]
    if len(molecule_headers) < 2:
        raise RuntimeError("prepared topology does not expose a bounded solute molecule type")
    insertion = molecule_headers[1]
    include = ["#ifdef POSRES", '#include "posre.itp"', "#endif", ""]
    topology_path.write_text("\n".join(lines[:insertion] + include + lines[insertion:]) + "\n", encoding="utf-8")
    return len(restrained)


def _grompp_validation_command(gmx: Path) -> list[str]:
    return [
        str(gmx), "grompp", "-f", "bundle-validation.mdp", "-c", "system.gro",
        "-r", "system.gro", "-p", "system.top", "-o", "bundle-validation.tpr",
        "-maxwarn", "0",
    ]


def _validate_engine_consumption() -> dict[str, Any]:
    from openmm.app import AmberInpcrdFile, AmberPrmtopFile, HBonds, PME
    from openmm import unit

    top = AmberPrmtopFile("system.prmtop")
    coordinates = AmberInpcrdFile("system.inpcrd")
    system = top.createSystem(
        nonbondedMethod=PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=HBonds,
        rigidWater=True,
    )
    if coordinates.positions is None or system.getNumParticles() != len(coordinates.positions):
        raise RuntimeError("OpenMM prepared-bundle particle/coordinate mismatch")

    mdp = Path("bundle-validation.mdp")
    mdp.write_text(
        "integrator = steep\nnsteps = 500\nemtol = 1000.0\ndefine = -DPOSRES\ncutoff-scheme = Verlet\n"
        "nstlist = 20\nrlist = 1.0\ncoulombtype = PME\nrcoulomb = 1.0\n"
        "vdwtype = Cut-off\nrvdw = 1.0\nconstraints = none\npbc = xyz\n",
        encoding="utf-8",
    )
    gmx = Path(sys.executable).resolve().parent / "gmx"
    completed = subprocess.run(
        _grompp_validation_command(gmx),
        check=False, capture_output=True, text=True, timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("GROMACS prepared-bundle validation failed: " +
                           ((completed.stderr or completed.stdout or "").strip()[-1200:]))
    return {
        "openmm": {"particles": system.getNumParticles(), "status": "passed"},
        "gromacs": {"tpr_bytes": Path("bundle-validation.tpr").stat().st_size, "status": "passed"},
    }


def prepare(request_path: Path) -> dict[str, Any]:
    import parmed

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != "bms.md.preparation-request.v1":
        raise ValueError("unsupported preparation request")
    profile = request["profile"]
    padding_nm = float(request["padding_nm"])
    salt_molar = float(request["salt_molar"])
    disulfide_bonds = _preprocess_pdb(Path("source.pdb"), Path("prepared_input.pdb"))

    Path("prepass.leap").write_text(
        _leap_script(profile, padding_nm, 0, 0, "prepass", disulfide_bonds), encoding="utf-8"
    )
    _run_tleap("prepass.leap")
    prepass = parmed.load_file("prepass.prmtop", "prepass.inpcrd")
    prepass_waters, _, prepass_charge = _composition(prepass)
    salt_pairs = max(0, int(round(prepass_waters * salt_molar / 55.5)))
    neutral_positive = max(0, -int(round(prepass_charge)))
    neutral_negative = max(0, int(round(prepass_charge)))

    Path("final.leap").write_text(
        _leap_script(
            profile, padding_nm, neutral_positive + salt_pairs, neutral_negative + salt_pairs,
            "system", disulfide_bonds,
        ), encoding="utf-8"
    )
    _run_tleap("final.leap")
    structure = parmed.load_file("system.prmtop", "system.inpcrd")
    structure.save("system.top", overwrite=True)
    structure.save("system.gro", overwrite=True)
    restrained_heavy_atoms = _install_position_restraints(structure, Path("system.top"))
    waters, ions, net_charge = _composition(structure)
    realized_salt = (salt_pairs / waters * 55.5) if waters else 0.0
    engine_consumption = _validate_engine_consumption()

    report = {
        "schema": "bms.md.preparation-report.v1",
        "profile_id": profile["id"],
        "leaprc": list(profile["leaprc"]),
        "solvent_box": profile["solvent_box"],
        "gromacs_gpu_offload": profile["gromacs_gpu_offload"],
        "padding_nm": padding_nm,
        "requested_ionic_strength_molar": salt_molar,
        "realized_salt_pair_molar": round(realized_salt, 6),
        "neutralize": bool(request["neutralize"]),
        "added_salt_pairs": salt_pairs,
        "disulfide_bond_count": len(disulfide_bonds),
        "restrained_heavy_atom_count": restrained_heavy_atoms,
        "atom_count": len(structure.atoms),
        "residue_count": len(structure.residues),
        "water_count": waters,
        "ion_count": ions,
        "net_charge_e": round(net_charge, 6),
        "parmed_version": parmed.__version__,
        "engine_consumption": engine_consumption,
    }
    Path("preparation_report.json").write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    for name in (
        "prepared_input.pdb", "prepass.leap", "prepass.prmtop", "prepass.inpcrd", "prepass.pdb",
        "final.leap", "bundle-validation.mdp", "bundle-validation.tpr",
    ):
        Path(name).unlink(missing_ok=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
