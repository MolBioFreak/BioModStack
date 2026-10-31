#!/usr/bin/env python3
"""Run one sequence designer over every input design for the resequence loop.

For each input PDB the designer's own native runner is called with a request built from the
caller's template (model-owned defaults plus caller edits). Position selection is explicit:
``--fixed-positions`` keeps residues, ``--designable-positions`` frees only those residues.
The native outputs are then converted to one PDB per design by designer_outputs_to_pdb.py.
Nothing here chooses scientific settings; ProtonPottsMPNN has no position control and ignores them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = re.compile(r"^([A-Za-z]+)(-?\d+)([A-Za-z]?)(?:-(-?\d+)([A-Za-z]?))?$")


def expand(spec: str) -> list[str]:
    """'A1-3,B5' -> ['A1','A2','A3','B5'] (author ids; insertion codes only on single residues)."""
    out: list[str] = []
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        m = SPEC.match(token)
        if not m:
            raise ValueError(f"unreadable position {token!r}; use e.g. A1-20,B5")
        chain, start, icode, end, _ = m.groups()
        if end is None:
            out.append(f"{chain}{start}{icode}")
        else:
            out.extend(f"{chain}{n}" for n in range(min(int(start), int(end)), max(int(start), int(end)) + 1))
    return out


def residues_of(pdb: Path) -> list[str]:
    seen: dict[str, None] = {}
    for line in pdb.read_text().splitlines():
        if line.startswith("ATOM"):
            seen[f"{line[21].strip()}{line[22:26].strip()}{line[26].strip()}"] = None
    return list(seen)


def complement(pdb: Path, designable: list[str]) -> list[str]:
    free = set(designable)
    return [r for r in residues_of(pdb) if r not in free]


def spec_of(residues: list[str]) -> str:
    return ",".join(residues)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def ligandmpnn(template: dict, pdb: Path, work: Path, fixed: str, designable: str, args) -> Path:
    request = copy.deepcopy(template)
    options = request["options"]
    if fixed and designable:
        raise ValueError("choose fixed or designable positions, not both")
    if fixed:
        options["fixed_residues"] = expand(fixed)
    if designable:
        options["designed_residues"] = expand(designable)
    request["write_structures"] = True
    (work / "request.json").write_text(json.dumps(request))
    run([sys.executable, str(HERE / "run_ligandmpnn_design.py"), "--request", str(work / "request.json"),
         "--input", str(pdb), "--output", str(work / "native")])
    return work / "native"


def caliby(template: dict, pdb: Path, work: Path, fixed: str, designable: str, args) -> Path:
    sys.path.insert(0, str(HERE))
    from prep_caliby_request import prepare_request  # noqa: E402

    state = {"state_id": "input", "path": str(pdb), "fixed_pos_seq": "", "fixed_pos_scn": "",
             "fixed_pos_override_seq": "", "pos_restrict_aatype": "", "symmetry_pos": ""}
    if fixed:
        state["fixed_pos_seq"] = spec_of(expand(fixed))
    elif designable:
        state["fixed_pos_seq"] = spec_of(complement(pdb, expand(designable)))
    request = copy.deepcopy(template)
    request["ensembles"] = [{"ensemble_id": pdb.stem, "states": [state]}]
    prepare_request(request, work / "request")
    run([sys.executable, str(HERE / "run_caliby_experimental.py"), "--request-dir", str(work / "request"),
         "--output-dir", str(work / "native")])
    return work / "native"


def protonpotts(template: dict, pdb: Path, work: Path, fixed: str, designable: str, args) -> Path:
    request = copy.deepcopy(template)
    request["source"] = {"requested_path": pdb.name, "sha256": sha256(pdb)}
    (work / "request.json").write_text(json.dumps(request))
    run([sys.executable, str(HERE / "run_protonpottsmpnn_design.py"), "--request", str(work / "request.json"),
         "--input", str(pdb), "--out", str(work / "native"), "--n-jobs", str(args.n_jobs), "--device", args.device])
    return work / "native" / "protonpottsmpnn_design"


RUNNERS = {"ligandmpnn": ligandmpnn, "caliby": caliby, "protonpottsmpnn": protonpotts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=sorted(RUNNERS))
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--fixed-positions", default="")
    parser.add_argument("--designable-positions", default="")
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    if args.engine == "protonpottsmpnn" and (args.fixed_positions or args.designable_positions):
        print("note: ProtonPottsMPNN chooses its own sites; fixed/designable positions are not applied", file=sys.stderr)
    template = json.loads(args.template.read_text())
    inputs = sorted(args.input_dir.glob("*.pdb"))
    if not inputs:
        raise SystemExit(f"no input PDB files in {args.input_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    import designer_outputs_to_pdb as convert  # noqa: E402

    for index, pdb in enumerate(inputs):
        work = Path(f"work_{index:03d}")
        work.mkdir()
        native = RUNNERS[args.engine](template, pdb, work, args.fixed_positions, args.designable_positions, args)
        staging = work / "pdbs"
        staging.mkdir()
        if args.engine == "ligandmpnn":
            written = convert.ligandmpnn(native, staging)
        elif args.engine == "caliby":
            written = convert.caliby(native, staging)
        else:
            written = convert.protonpotts(native, pdb, staging)
        for produced in written:
            shutil.copyfile(produced, args.out_dir / f"{pdb.stem}__{args.engine}__{produced.name}")
    print(json.dumps({"engine": args.engine, "inputs": len(inputs), "designs": len(list(args.out_dir.glob('*.pdb')))}))


if __name__ == "__main__":
    main()
