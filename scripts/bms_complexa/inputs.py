"""Lossless source custody and an explicit AME PDB representation.

Coordinates/serials/elements/charges/bonds are not regenerated. The original file
remains authoritative for chemical identity. Only requested atoms enter the
AME derived PDB; CCD-completed atoms must not be compared as selected atoms.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
from scripts.lib.complexa_contract import Source


def prepare_source(source: Source, source_path: Path, directory: Path, *, ame: bool) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / ("original" + "".join(source_path.suffixes))
    shutil.copyfile(source_path, original)
    manifest = {"document_id": source.document_id, "original": str(original),
                "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                "atom_map": [], "comparison_scope": "requested_atoms_only"}
    if not ame and not source.motif_atoms:
        manifest["native_path"] = str(original)
        manifest["motif_atom_spec"] = "; ".join(
            f"{s.chain}{s.residue}: [{', '.join(s.atoms)}]" for s in source.motif_atoms)
        manifest["ligand"] = source.ligand
    else:
        # Author selectors retain insertion codes; native numeric residues are assigned
        # distinctly so an insertion never aliases another selected residue.
        selected = {}
        residues = {}
        for kind, selections in (("ligand", source.ligand_atoms), ("motif", source.motif_atoms)):
            for selection in selections:
                residue = (kind, selection.chain, selection.residue, selection.insertion_code)
                if residue not in residues:
                    residues[residue] = 1 + sum(k[0] == kind for k in residues)
                for atom in selection.atoms:
                    selected[(selection.chain, selection.residue, selection.insertion_code, atom)] = (kind, residues[residue])
        output, serials, found = [], set(), set()
        for line in original.read_text().splitlines(keepends=True):
            if line[:6].strip() not in ("ATOM", "HETATM"):
                continue
            key = (line[21], int(line[22:26]), line[26].strip(), line[12:16].strip())
            if key not in selected:
                continue
            kind, number = selected[key]
            chain = "A" if kind == "ligand" else "B"
            native_name = "L:0" if kind == "ligand" else line[17:20]
            rewritten = line[:17] + native_name + line[20:21] + chain + f"{number:4d}" + " " + line[27:]
            output.append(rewritten)
            found.add(key)
            serials.add(int(line[6:11]))
            manifest["atom_map"].append({
                "source": {"chain": key[0], "residue": key[1], "insertion_code": key[2], "atom": key[3],
                           "component": line[17:20].strip(), "altloc": line[16]},
                "native": {"chain": chain, "residue": number, "atom": key[3], "component": native_name.strip()},
                "serial": int(line[6:11]), "coordinate_fields": line[30:54],
                "element": line[76:78].strip(), "charge": line[78:80].strip(), "role": kind})
        # A source binding error, not a scientific quality/admission condition.
        if found != set(selected):
            raise ValueError(f"Selected source atoms absent: {sorted(set(selected) - found)}")
        for line in original.read_text().splitlines(keepends=True):
            if line.startswith("CONECT"):
                ids = [int(line[i:i+5]) for i in range(6, len(line.rstrip()), 5) if line[i:i+5].strip()]
                if ids and ids[0] in serials:
                    kept = [i for i in ids[1:] if i in serials]
                    if kept:
                        output.append("CONECT" + "".join(f"{i:5d}" for i in [ids[0], *kept]) + "\n")
        native = directory / ("ame.pdb" if ame else "motif.pdb")
        native.write_text("".join(output) + "END\n")
        manifest["native_path"] = str(native)
        specs = {}
        for row in manifest["atom_map"]:
            if row["role"] == "motif":
                specs.setdefault(row["native"]["residue"], []).append(row["native"]["atom"])
        manifest["motif_atom_spec"] = "; ".join(f"B{r}: [{', '.join(dict.fromkeys(atoms))}]" for r, atoms in specs.items())
        manifest["ligand"] = "L:0"
    (directory / "source-map.json").write_text(json.dumps(manifest, indent=2))
    return manifest
