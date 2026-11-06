#!/usr/bin/env python3
"""
Extract target-chain structural templates from design PDBs.

Each input design PDB becomes one target-only mmCIF template that preserves the
original chain IDs and residue numbering. The output manifest can be consumed by
validator prep scripts to anchor only the experimental target chains while
leaving binder chains flexible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from collections import OrderedDict


AA_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "MSE": "M",
}


# Biopython's curated modified amino-acid parent table is also available in
# the installed Protenix image. Retain the actual CCD identity in coordinates.
from Bio.Data.PDBData import protein_letters_3to1_extended

CANONICAL_NAMES = {code: name for name, code in AA_CODES.items() if name != "MSE"}
AA_CODES.update(protein_letters_3to1_extended)


def parse_chain_csv(raw: str) -> list[str]:
    chains = [token.strip() for token in (raw or "").split(",") if token.strip()]
    if not chains:
        raise ValueError("At least one target chain ID is required")
    return chains


def _parse_int(raw: str, default: int = 0) -> int:
    try:
        return int(raw.strip())
    except Exception:
        return default


def _parse_float(raw: str, default: float = 0.0) -> float:
    try:
        return float(raw.strip())
    except Exception:
        return default


def _guess_element(atom_name: str, element_field: str) -> str:
    token = (element_field or "").strip()
    if token:
        return token
    letters = "".join(ch for ch in atom_name if ch.isalpha())
    if not letters:
        return "X"
    if len(letters) >= 2 and letters[:2].title() in {"Br", "Cl", "Fe", "Mg", "Mn", "Na", "Ni", "Zn", "Cu", "Ca", "Co"}:
        return letters[:2].title()
    return letters[0].upper()


def _cif_value(value: object) -> str:
    if value is None:
        return "."
    text = str(value)
    if text == "":
        return "."
    if any(ch.isspace() for ch in text) or any(ch in text for ch in ("'", '"', "#", ";")):
        return "'" + text.replace("'", "''") + "'"
    return text


def _iter_selected_pdb_atoms(
    pdb_path: Path,
    requested_chains: set[str],
    model_number: int | None,
) -> tuple[list[dict[str, object]], list[str]]:
    atoms: list[dict[str, object]] = []
    present_chains: list[str] = []
    saw_model_records = False
    selected_model_found = model_number is None
    current_model: int | None = None
    keep_current_model = model_number is None
    kept_first_model = False

    with pdb_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            record_type = line[:6].strip()

            if record_type == "MODEL":
                saw_model_records = True
                current_model = _parse_int(line[10:], default=0)
                if model_number is not None:
                    keep_current_model = current_model == model_number
                    if keep_current_model:
                        selected_model_found = True
                else:
                    keep_current_model = not kept_first_model
                    if keep_current_model:
                        kept_first_model = True
                continue

            if record_type == "ENDMDL":
                if model_number is None and kept_first_model:
                    break
                keep_current_model = model_number is None
                current_model = None
                continue

            if saw_model_records and not keep_current_model:
                continue

            if record_type not in {"ATOM", "HETATM"}:
                continue
            if record_type == "HETATM" and line[17:20].strip() not in AA_CODES:
                continue  # Protein templates; nonpolymer chemistry remains in native input.

            chain_id = line[21].strip()
            if chain_id not in requested_chains:
                continue

            if chain_id not in present_chains:
                present_chains.append(chain_id)

            atoms.append(
                {
                    "group_PDB": record_type,
                    "atom_id": line[6:11].strip(),
                    "type_symbol": _guess_element(line[12:16].strip(), line[76:78].strip()),
                    "label_atom_id": line[12:16].strip() or "?",
                    "label_alt_id": line[16].strip() or ".",
                    "label_comp_id": line[17:20].strip() or "UNK",
                    "auth_asym_id": chain_id,
                    "auth_seq_id": _parse_int(line[22:26], default=0),
                    "ins_code": line[26].strip() or "?",
                    "x": _parse_float(line[30:38]),
                    "y": _parse_float(line[38:46]),
                    "z": _parse_float(line[46:54]),
                    "occupancy": _parse_float(line[54:60], default=1.0),
                    "b_iso": _parse_float(line[60:66], default=0.0),
                    "model_num": model_number or current_model or 1,
                }
            )

    if model_number is not None and saw_model_records and not selected_model_found:
        raise ValueError(f"Requested model {model_number} not found in {pdb_path}")

    return atoms, present_chains


def _build_polymer_residue_map(
    atoms: list[dict[str, object]]
) -> tuple[OrderedDict[str, OrderedDict[tuple[int, str], str]], dict[str, str]]:
    residues_by_chain: OrderedDict[str, OrderedDict[tuple[int, str], str]] = OrderedDict()
    entity_ids: dict[str, str] = {}

    for atom in atoms:
        if atom["group_PDB"] != "ATOM" and atom["label_comp_id"] not in AA_CODES:
            continue
        chain_id = str(atom["auth_asym_id"])
        residue_key = (int(atom["auth_seq_id"]), str(atom["ins_code"]))
        residues = residues_by_chain.setdefault(chain_id, OrderedDict())
        residues.setdefault(residue_key, str(atom["label_comp_id"]))

    for index, chain_id in enumerate(residues_by_chain.keys(), start=1):
        entity_ids[chain_id] = str(index)

    return residues_by_chain, entity_ids


def _write_simple_mmcif(
    structure_id: str,
    atoms: list[dict[str, object]],
    out_path: Path,
) -> None:
    residues_by_chain, entity_ids = _build_polymer_residue_map(atoms)
    residue_index_maps: dict[str, dict[tuple[int, str], int]] = {}
    for chain_id, residues in residues_by_chain.items():
        residue_index_maps[chain_id] = {
            residue_key: index
            for index, residue_key in enumerate(residues.keys(), start=1)
        }

    lines: list[str] = [
        f"data_{structure_id}",
        "_entry.id model",
        "#",
    ]

    if entity_ids:
        lines.extend(
            [
                "loop_",
                "_entity.id",
                "_entity.type",
                "_entity.src_method",
                "_entity.pdbx_description",
                "_entity.formula_weight",
                "_entity.pdbx_number_of_molecules",
                "_entity.details",
            ]
        )
        for chain_id, entity_id in entity_ids.items():
            lines.append(f"{entity_id} polymer man {_cif_value(f'Chain {chain_id}')} . 1 .")
        lines.append("#")

        lines.extend(
            [
                "loop_",
                "_struct_asym.id",
                "_struct_asym.entity_id",
                "_struct_asym.details",
            ]
        )
        for chain_id, entity_id in entity_ids.items():
            lines.append(f"{_cif_value(chain_id)} {entity_id} .")
        lines.append("#")

        lines.extend(
            [
                "loop_",
                "_entity_poly.entity_id",
                "_entity_poly.type",
                "_entity_poly.nstd_linkage",
                "_entity_poly.nstd_monomer",
                "_entity_poly.pdbx_strand_id",
                "_entity_poly.pdbx_seq_one_letter_code",
                "_entity_poly.pdbx_seq_one_letter_code_can",
            ]
        )
        for chain_id, residues in residues_by_chain.items():
            sequence = "".join(AA_CODES.get(res_name, "X") for res_name in residues.values()) or "X"
            modified = any(name not in CANONICAL_NAMES.values() for name in residues.values())
            original = "".join(AA_CODES[name] if name in CANONICAL_NAMES.values() else f"({name})"
                               for name in residues.values())
            lines.append(
                f"{entity_ids[chain_id]} polypeptide(L) no {'yes' if modified else 'no'} {_cif_value(chain_id)} "
                f"{_cif_value(original)} {_cif_value(sequence)}"
            )
        lines.append("#")

        lines.extend(
            [
                "loop_",
                "_entity_poly_seq.entity_id",
                "_entity_poly_seq.num",
                "_entity_poly_seq.mon_id",
                "_entity_poly_seq.hetero",
            ]
        )
        for chain_id, residues in residues_by_chain.items():
            for index, res_name in enumerate(residues.values(), start=1):
                lines.append(f"{entity_ids[chain_id]} {index} {_cif_value(res_name)} .")
        lines.append("#")

    # Native template readers use chemical-component types to recognize
    # polymer chains. Coordinate-only MMCIFIO output is insufficient.
    lines.extend(["loop_", "_chem_comp.id", "_chem_comp.type",
                  "_chem_comp.mon_nstd_parent_comp_id", "_chem_comp.one_letter_code"])
    for name in dict.fromkeys(str(atom["label_comp_id"]) for atom in atoms):
        kind = "L-peptide linking" if name in AA_CODES else "non-polymer"
        code = AA_CODES.get(name, "X")
        parent = CANONICAL_NAMES.get(code, "?") if name not in CANONICAL_NAMES.values() else "?"
        lines.append(f"{_cif_value(name)} {_cif_value(kind)} {parent} {code}")
    lines.append("#")
    lines.extend(
        [
            "loop_",
            "_atom_site.group_PDB",
            "_atom_site.id",
            "_atom_site.type_symbol",
            "_atom_site.label_atom_id",
            "_atom_site.label_alt_id",
            "_atom_site.label_comp_id",
            "_atom_site.label_seq_id",
            "_atom_site.auth_seq_id",
            "_atom_site.pdbx_PDB_ins_code",
            "_atom_site.label_asym_id",
            "_atom_site.Cartn_x",
            "_atom_site.Cartn_y",
            "_atom_site.Cartn_z",
            "_atom_site.occupancy",
            "_atom_site.label_entity_id",
            "_atom_site.auth_asym_id",
            "_atom_site.auth_comp_id",
            "_atom_site.B_iso_or_equiv",
            "_atom_site.pdbx_PDB_model_num",
        ]
    )

    for index, atom in enumerate(atoms, start=1):
        chain_id = str(atom["auth_asym_id"])
        residue_key = (int(atom["auth_seq_id"]), str(atom["ins_code"]))
        label_seq_id = residue_index_maps.get(chain_id, {}).get(residue_key, ".")
        label_entity_id = entity_ids.get(chain_id, "1")
        lines.append(
            " ".join(
                [
                    _cif_value(atom["group_PDB"]),
                    str(index),
                    _cif_value(atom["type_symbol"]),
                    _cif_value(atom["label_atom_id"]),
                    _cif_value(atom["label_alt_id"]),
                    _cif_value(atom["label_comp_id"]),
                    _cif_value(label_seq_id),
                    _cif_value(atom["auth_seq_id"]),
                    _cif_value(atom["ins_code"]),
                    _cif_value(chain_id),
                    f"{float(atom['x']):.3f}",
                    f"{float(atom['y']):.3f}",
                    f"{float(atom['z']):.3f}",
                    f"{float(atom['occupancy']):.3f}",
                    _cif_value(label_entity_id),
                    _cif_value(chain_id),
                    _cif_value(atom["label_comp_id"]),
                    f"{float(atom['b_iso']):.3f}",
                    "1",  # The selected source model is the sole native model.
                ]
            )
        )
    lines.append("#")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_templates(
    pdb_paths: list[Path],
    target_chains: list[str],
    out_dir: Path,
    model_number: int | None = None,
    native_template_ids: bool = False,
) -> dict[str, dict[str, object]]:
    """Stage the selected source bytes, never reacquire a name-matched PDB.

    A private four-character native identifier addresses each target molecule;
    the manifest retains its full source identity and explicit chain mapping.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    requested = set(target_chains)
    identities = {}
    for pdb_path in pdb_paths:
        atoms, present = _iter_selected_pdb_atoms(pdb_path, requested, model_number)
        if not present:
            raise ValueError(f"No requested target chains {sorted(requested)} were found in {pdb_path}")
        payload = json.dumps(atoms, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        salt = 0
        template_id = digest[:4] if native_template_ids else pdb_path.stem.lower()
        corpus = os.environ.get("BMS_PROTENIX_GENERIC_MMCIF_DIR")
        while native_template_ids and (
            (template_id in identities and identities[template_id] != digest)
            or (corpus and (Path(corpus) / f"{template_id}.cif").exists())
        ):
            salt += 1
            template_id = hashlib.sha256(payload + str(salt).encode()).hexdigest()[:4]
        identities[template_id] = digest
        out_path = out_dir / f"{template_id}.cif"
        _write_simple_mmcif(template_id, atoms, out_path)
        residues, _ = _build_polymer_residue_map(atoms)
        manifest[pdb_path.stem] = {
            "cif": str(out_path.resolve()), "template_id": template_id,
            "source_sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
            "chains": present, "model_number": model_number, "writer": "source",
            "sequences": {chain: "".join(AA_CODES.get(name, "X") for name in row.values())
                          for chain, row in residues.items()},
        }
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract target-only mmCIF templates from design PDBs")
    parser.add_argument("--pdb_files", nargs="+", required=True, help="Input design PDB files")
    parser.add_argument("--target_chains", required=True, help="Comma-separated target chain IDs")
    parser.add_argument("--out_dir", required=True, help="Directory for extracted target templates")
    parser.add_argument("--manifest", required=True, help="Output JSON manifest path")
    parser.add_argument("--model_number", type=int, default=None, help="Specific PDB MODEL number to extract before saving templates")
    parser.add_argument("--native-template-ids", action="store_true", help="Use source-bound four-character IDs for native Protenix hits")
    args = parser.parse_args()

    pdb_paths = [Path(path).expanduser() for path in args.pdb_files]
    target_chains = parse_chain_csv(args.target_chains)
    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()

    manifest = extract_templates(
        pdb_paths,
        target_chains,
        out_dir,
        model_number=args.model_number,
        native_template_ids=args.native_template_ids,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        f"[extract_target_templates] Wrote {len(manifest)} target template(s) to {out_dir}"
    )


if __name__ == "__main__":
    main()
