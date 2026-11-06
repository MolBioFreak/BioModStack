#!/usr/bin/env python3
"""Build Protein Local Redesign validator inputs from the selected candidate.

The candidate supplies its redesigned sequence and retained molecular context.
Do not substitute an ancestor sequence or reduce its noncanonical chemistry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path

AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
MODIFIED_PROTEIN = {"MSE": "M", "SEC": "C", "PYL": "K", "HYP": "P",
                    "SEP": "S", "TPO": "T", "PTR": "Y", "CSO": "C"}
DNA = {"DA": "A", "DC": "C", "DG": "G", "DT": "T", "DI": "I", "DN": "N", "DU": "U"}
RNA = {"A": "A", "C": "C", "G": "G", "U": "U", "I": "I", "N": "N"}
ION_CODES = {"ZN", "MG", "CA", "NA", "K", "MN", "FE", "CO", "NI", "CU", "CL"}


def candidate_id(path: Path) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")
    return normalized or "candidate"


def _molecules(path: Path):
    """Read one selected PDB model; retain explicit chemistry and bond identity.

    CCD names are source residue names, never identities inferred from atom
    coordinates. MODRES supplies a parent letter for MSA while the native CCD
    override retains the actual residue. Explicit inter-residue LINK, SSBOND
    and CONECT records are projected through this same emitted entity order.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    parents = {}
    for line in lines:
        if line.startswith("MODRES"):
            parents[(line[16:17].strip(), line[18:22].strip(), line[22:23].strip(), line[12:15].strip())] = line[24:27].strip()
    residues = OrderedDict()
    serials = {}
    saw_model = False
    in_model = True
    for line in lines:
        record = line[:6].strip()
        if record == "MODEL":
            if saw_model:
                in_model = False
            else:
                saw_model = True
            continue
        if record == "ENDMDL":
            in_model = False
            continue
        if not in_model or record not in {"ATOM", "HETATM"}:
            continue
        name, chain = line[17:20].strip().upper(), line[21:22].strip()
        key = (chain, line[22:26].strip(), line[26:27].strip(), name)
        parent = parents.get(key, name)
        if parent in DNA:
            kind, letter = "dna", DNA[parent]
        elif parent in RNA:
            kind, letter = "rna", RNA[parent]
        elif parent in AA3_TO_1 or name in MODIFIED_PROTEIN or record == "ATOM":
            kind, letter = "protein", AA3_TO_1.get(parent, MODIFIED_PROTEIN.get(name, "X"))
        else:
            kind, letter = ("ion" if name in ION_CODES else "ligand"), None
        residues.setdefault(key, {"kind": kind, "letter": letter, "record": record})
        serials[line[6:11].strip()] = (key, line[12:16].strip())
    if not any(r["kind"] == "protein" for r in residues.values()):
        raise ValueError(f"PDB contains no supported protein residues: {path}")

    groups = OrderedDict()
    for key, residue in residues.items():
        # Nonpolymer residues are distinct molecules, even on a polymer's
        # author chain. Do not collapse counts or discard source order.
        group = (key[0], residue["kind"]) if residue["kind"] in {"protein", "dna", "rna"} else key
        groups.setdefault(group, []).append(key)
    reserved = {keys[0][0] or "A" for keys in groups.values() if residues[keys[0]]["kind"] in {"protein", "dna", "rna"}}
    used = set()
    sequences, components, endpoints, mapping = [], [], {}, []
    for entity_index, keys in enumerate(groups.values(), 1):
        chain = keys[0][0] or "A"
        kind = residues[keys[0]]["kind"]
        label = chain
        if label in used or (kind in {"ligand", "ion"} and label in reserved):
            label = f"{chain}_{entity_index}"
            while label in used or label in reserved:
                label += "_"
        used.add(label)
        native = {"id": [label], "count": 1}
        component = {"chain_id": label, "molecule_type": kind}
        if kind in {"protein", "dna", "rna"}:
            sequence = "".join(residues[k]["letter"] for k in keys)
            native["sequence"] = component["sequence"] = sequence
            standard = AA3_TO_1 if kind == "protein" else DNA if kind == "dna" else RNA
            modifications = []
            for position, key in enumerate(keys, 1):
                if key[3] not in standard:
                    modifications.append({"ptmType": f"CCD_{key[3]}", "ptmPosition": position} if kind == "protein" else
                                         {"modificationType": f"CCD_{key[3]}", "basePosition": position})
            if modifications:
                native["modifications"] = component["modifications"] = modifications
            native_key = {"protein": "proteinChain", "dna": "dnaSequence", "rna": "rnaSequence"}[kind]
        else:
            name = keys[0][3]
            native_key = kind
            native[kind] = name if kind == "ion" else f"CCD_{name}"
            component["ccd"] = name
        sequences.append({native_key: native})
        components.append(component)
        for position, key in enumerate(keys, 1):
            endpoints[key] = (entity_index, position)
            mapping.append({"source_chain": key[0], "source_residue_number": key[1], "source_insertion_code": key[2],
                            "source_residue_name": key[3], "entity": entity_index, "copy": 1, "position": position, "id": label})

    links = []
    model_index = 0
    inside_model = False
    for line in lines:
        record = line[:6].strip()
        if record == "MODEL":
            model_index += 1
            inside_model = True
            continue
        if record == "ENDMDL":
            inside_model = False
            continue
        # Global connectivity following ENDMDL applies to the selected model;
        # connectivity inside another model must not alias its reused serials.
        if inside_model and model_index != 1:
            continue
        if record == "CONECT":
            left = serials.get(line[6:11].strip())
            for start in range(11, len(line), 5):
                right = serials.get(line[start:start + 5].strip())
                if left and right:
                    links.append((left, right))
        elif record == "LINK":
            links.append((((line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip()), line[12:16].strip()),
                          ((line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip()), line[42:46].strip())))
        elif record == "SSBOND":
            links.append((((line[15:16].strip(), line[17:21].strip(), line[21:22].strip(), "CYS"), "SG"),
                          ((line[29:30].strip(), line[31:35].strip(), line[35:36].strip(), "CYS"), "SG")))
    bonds, seen = [], set()
    for (left, atom1), (right, atom2) in links:
        if left == right or left not in endpoints or right not in endpoints:
            continue  # Intra-residue CCD bonds are native-owned.
        e1, p1 = endpoints[left]
        e2, p2 = endpoints[right]
        # Only the forward backbone edge is native-owned. An explicit reverse
        # edge (e.g. a cyclic dipeptide closure) is distinct, not a duplicate.
        earlier, later = (atom1, atom2) if p1 < p2 else (atom2, atom1)
        if e1 == e2 and abs(p1 - p2) == 1 and (earlier, later) in (("C", "N"), ("O3'", "P")):
            continue
        identity = tuple(sorted(((e1, p1, atom1), (e2, p2, atom2))))
        if identity in seen:
            continue
        seen.add(identity)
        bonds.append({"entity1": str(e1), "copy1": 1, "position1": str(p1), "atom1": atom1,
                      "entity2": str(e2), "copy2": 1, "position2": str(p2), "atom2": atom2})
    return components, sequences, bonds, mapping


def extract_protein_components(path: Path) -> list[dict]:
    """Compatibility projection for protein-only callers; native uses all molecules."""
    return [component for component in _molecules(path)[0] if component["molecule_type"] == "protein"]


def build_contract(path: Path, model_seeds: list[int], requested_candidate_id: str | None = None) -> dict[str, object]:
    resolved = path.resolve()
    components, sequences, bonds, mapping = _molecules(resolved)
    name = candidate_id(Path(requested_candidate_id)) if requested_candidate_id else candidate_id(resolved)
    task = {"name": name, "modelSeeds": model_seeds, "sequences": sequences}
    if bonds:
        task["covalent_bonds"] = bonds
    return {"schema_version": 1, "candidate_id": name, "source_file": resolved.name,
            "source_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
            "components": components, "source_residue_map": mapping, "protenix_input": [task]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-pdb", required=True, type=Path)
    parser.add_argument("--candidate-id")
    parser.add_argument("--contract-out", required=True, type=Path)
    parser.add_argument("--protenix-out", required=True, type=Path)
    parser.add_argument("--model-seeds", default="42")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seeds = [int(value.strip()) for value in args.model_seeds.split(",") if value.strip()]
    if not seeds:
        raise ValueError("at least one Protenix model seed is required")
    contract = build_contract(args.input_pdb, seeds, args.candidate_id)
    args.contract_out.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    args.protenix_out.write_text(json.dumps(contract["protenix_input"], indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
