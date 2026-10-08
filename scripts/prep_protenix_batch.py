#!/usr/bin/env python3
"""
Prepare Protenix batch inputs from binder design PDBs.

By default, each input PDB becomes one Protenix job entry with one
``proteinChain`` entity per observed chain, preserving chain order from the
source structure.

When ``--external-target-as-target`` is enabled, the builder instead composes
each entry from:
1. binder chains taken from the source PDB
2. target chains taken from the external ``--target_pdb``
3. optional target ions/cofactors from the external ``--target_pdb``

This mode is what we want for validation against an experimental target, where
the source PDB may contain a stale or surrogate target chain that should not be
fed back into Protenix.
"""

from __future__ import annotations

import argparse
import json
import string
from collections import OrderedDict
from pathlib import Path

from protenix_constraint_utils import infer_target_pocket_residues


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
}

ION_CODES = {"ZN", "MG", "CA", "NA", "K", "MN", "FE", "CO", "NI", "CU", "CL"}


def _model_allowed(line: str, *, saw_model_records: bool, current_model: int | None, model_number: int | None) -> bool:
    if not saw_model_records:
        return True
    if model_number is None:
        return current_model == 1
    return current_model == model_number


def extract_chain_sequences(
    pdb_path: Path,
    *,
    chain_filter: set[str] | None = None,
    model_number: int | None = None,
) -> list[tuple[str, str]]:
    chain_sequences: "OrderedDict[str, list[str]]" = OrderedDict()
    seen_residues: dict[str, set[tuple[int, str]]] = {}
    current_model: int | None = None
    saw_model_records = False

    with pdb_path.open() as handle:
        for line in handle:
            record = line[:6].strip().upper()
            if record == "MODEL":
                saw_model_records = True
                try:
                    current_model = int(line[10:14].strip())
                except Exception:
                    current_model = None
                continue
            if record == "ENDMDL":
                current_model = None
                continue
            if record != "ATOM" or len(line) < 27:
                continue
            if not _model_allowed(line, saw_model_records=saw_model_records, current_model=current_model, model_number=model_number):
                continue
            if line[12:16].strip() != "CA":
                continue

            res_name = line[17:20].strip()
            chain_id = line[21].strip() or "_"
            if chain_filter and chain_id not in chain_filter:
                continue
            try:
                res_num = int(line[22:26].strip())
            except ValueError:
                continue
            insertion_code = line[26].strip()

            aa = AA_CODES.get(res_name)
            if aa is None:
                continue

            if chain_id not in chain_sequences:
                chain_sequences[chain_id] = []
                seen_residues[chain_id] = set()

            residue_key = (res_num, insertion_code)
            if residue_key in seen_residues[chain_id]:
                continue

            seen_residues[chain_id].add(residue_key)
            chain_sequences[chain_id].append(aa)

    return [(chain_id, "".join(seq)) for chain_id, seq in chain_sequences.items() if seq]


def _iter_filtered_pdb_records(
    pdb_path: Path,
    *,
    chain_filter: set[str] | None = None,
    model_number: int | None = None,
    include_ions: bool = False,
):
    current_model: int | None = None
    saw_model_records = False

    with pdb_path.open() as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            record = line[:6].strip().upper()
            if record == "MODEL":
                saw_model_records = True
                try:
                    current_model = int(line[10:14].strip())
                except Exception:
                    current_model = None
                continue
            if record == "ENDMDL":
                current_model = None
                continue
            if not _model_allowed(line, saw_model_records=saw_model_records, current_model=current_model, model_number=model_number):
                continue

            if record == "ATOM":
                chain_id = line[21].strip() or "_"
                if chain_filter and chain_id not in chain_filter:
                    continue
                yield line
                continue

            if include_ions and record == "HETATM":
                residue_name = line[17:20].strip().upper()
                element = line[76:78].strip().upper() if len(line) >= 78 else ""
                ion_code = residue_name if residue_name in ION_CODES else element if element in ION_CODES else None
                if not ion_code:
                    continue
                chain_id = line[21].strip() or "_"
                if chain_filter and chain_id not in chain_filter:
                    continue
                yield line


def _remap_chain_id(record_line: str, new_chain_id: str) -> str:
    return f"{record_line[:21]}{new_chain_id[:1]}{record_line[22:]}"


def _choose_target_aliases(
    source_chain_ids: list[str],
    binder_chain_ids: list[str],
    target_chain_count: int,
) -> list[str]:
    aliases = [cid for cid in source_chain_ids if cid not in binder_chain_ids]
    if len(aliases) >= target_chain_count:
        return aliases[:target_chain_count]

    taken = set(source_chain_ids)
    for candidate in string.ascii_uppercase + string.ascii_lowercase + string.digits:
        if candidate in taken:
            continue
        aliases.append(candidate)
        taken.add(candidate)
        if len(aliases) >= target_chain_count:
            break
    if len(aliases) < target_chain_count:
        raise ValueError("Could not allocate enough target chain aliases for Protenix batch input")
    return aliases


def write_reference_pdb(
    out_path: Path,
    *,
    binder_source_pdb: Path,
    binder_source_chains: list[str],
    target_pdb: Path,
    target_source_chains: list[str],
    target_alias_map: dict[str, str],
    target_model_number: int | None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as handle:
        handle.write("MODEL        1\n")
        for line in _iter_filtered_pdb_records(
            binder_source_pdb,
            chain_filter=set(binder_source_chains),
            model_number=1,
            include_ions=False,
        ):
            handle.write(f"{line}\n")
        for line in _iter_filtered_pdb_records(
            target_pdb,
            chain_filter=set(target_source_chains),
            model_number=target_model_number,
            include_ions=False,
        ):
            src_chain = line[21].strip() or "_"
            handle.write(f"{_remap_chain_id(line, target_alias_map[src_chain])}\n")
        for line in _iter_filtered_pdb_records(
            target_pdb,
            chain_filter=set(target_source_chains),
            model_number=target_model_number,
            include_ions=True,
        ):
            if not line.startswith("HETATM"):
                continue
            src_chain = line[21].strip() or "_"
            handle.write(f"{_remap_chain_id(line, target_alias_map[src_chain])}\n")
        handle.write("ENDMDL\nEND\n")


def extract_target_ion_counts(
    target_pdb: Path | None,
    *,
    target_chains: set[str] | None = None,
    model_number: int | None = None,
) -> OrderedDict[str, int]:
    counts: "OrderedDict[str, int]" = OrderedDict()
    if target_pdb is None or not target_pdb.exists():
        return counts

    current_model: int | None = None
    saw_model_records = False
    seen_sites: set[tuple[str, str, str]] = set()

    with target_pdb.open() as handle:
        for line in handle:
            record = line[:6].strip().upper()
            if record == "MODEL":
                saw_model_records = True
                try:
                    current_model = int(line[10:14].strip())
                except Exception:
                    current_model = None
                continue
            if record == "ENDMDL":
                current_model = None
                continue
            if record != "HETATM":
                continue

            if saw_model_records and model_number is not None and current_model != model_number:
                continue

            residue_name = line[17:20].strip().upper()
            chain_id = line[21].strip() or "_"
            residue_number = line[22:26].strip()
            element = line[76:78].strip().upper() if len(line) >= 78 else ""
            ion_code = residue_name if residue_name in ION_CODES else element if element in ION_CODES else None
            if not ion_code:
                continue
            if target_chains and chain_id not in target_chains:
                continue

            site_key = (ion_code, chain_id, residue_number)
            if site_key in seen_sites:
                continue
            seen_sites.add(site_key)
            counts[ion_code] = counts.get(ion_code, 0) + 1

    return counts


def build_entry(
    name: str,
    chain_sequences: list[tuple[str, str]],
    seeds: list[int],
    target_ion_counts: OrderedDict[str, int] | None = None,
    binder_chain_id: str | None = None,
    target_chain_ids: set[str] | None = None,
    epitope_residues: list[tuple[str, int]] | None = None,
    pocket_max_distance: float = 8.0,
) -> dict:
    if not chain_sequences:
        raise ValueError(f"No amino-acid chain sequences found for {name}")

    sequences = [
        {
            "proteinChain": {
                "id": [chain_id],
                "sequence": sequence,
                "count": 1,
            }
        }
        for chain_id, sequence in chain_sequences
    ]
    for ion_code, count in (target_ion_counts or {}).items():
        sequences.append({"ion": {"ion": ion_code, "count": int(count)}})

    entry = {
        "name": name,
        "modelSeeds": seeds,
        "sequences": sequences,
    }
    if binder_chain_id and target_chain_ids and epitope_residues:
        entity_by_chain = {chain_id: index for index, (chain_id, _sequence) in enumerate(chain_sequences, start=1)}
        binder_entity = entity_by_chain.get(binder_chain_id)
        contact_residues = [
            {"entity": entity_by_chain[chain_id], "copy": 1, "position": position}
            for chain_id, position in epitope_residues
            if chain_id in target_chain_ids and chain_id in entity_by_chain
        ]
        if binder_entity and contact_residues:
            entry["constraint"] = [
                {
                    "pocket": {
                        "binder_chain": {"entity": binder_entity, "copy": 1},
                        "contact_residues": contact_residues,
                        "max_distance": float(pocket_max_distance),
                    }
                }
            ]
    return entry


def parse_seeds(raw: str) -> list[int]:
    seeds: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        seeds.append(int(token))
    return seeds or [42]


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Protenix batch input JSON from PDB files")
    parser.add_argument("--pdb_files", nargs="+", required=True, help="Input antibody design PDB files")
    parser.add_argument("--out_json", required=True, help="Output Protenix JSON path")
    parser.add_argument("--seeds", default="42", help="Comma-separated model seeds")
    parser.add_argument("--target_pdb", help="Optional target PDB to source ions/cofactors from")
    parser.add_argument("--target_chains", default="", help="Optional comma-separated target chain IDs in target_pdb")
    parser.add_argument("--target_model_number", type=int, default=None, help="Optional MODEL number to read from target_pdb")
    parser.add_argument("--out_pdb_dir", default="", help="Optional directory for rewritten per-entry reference PDBs")
    parser.add_argument(
        "--external-target-as-target",
        action="store_true",
        help="Use binder chains from each source PDB plus target chains from --target_pdb instead of preserving all source chains",
    )
    parser.add_argument(
        "--binder_source_chains",
        default="",
        help="Optional comma-separated chain IDs to take as binder chains from each source PDB when --external-target-as-target is enabled",
    )
    parser.add_argument("--epitope_residues", default="", help="Optional comma-separated residues like B:12,B:15 for soft pocket constraints")
    parser.add_argument("--auto_pocket_if_missing", action="store_true", help="Infer target pocket residues when no epitope residues are supplied")
    parser.add_argument("--auto_pocket_max_residues", type=int, default=24, help="Maximum number of auto-inferred target pocket residues")
    parser.add_argument("--pocket_max_distance", type=float, default=8.0, help="Pocket constraint max distance in angstroms")
    args = parser.parse_args()

    pdb_files = [Path(path).expanduser().resolve() for path in args.pdb_files]
    seeds = parse_seeds(args.seeds)
    target_pdb = Path(args.target_pdb).expanduser().resolve() if args.target_pdb else None
    target_chains = {token.strip() for token in args.target_chains.split(",") if token.strip()}
    binder_source_chains = [token.strip() for token in args.binder_source_chains.split(",") if token.strip()]
    epitope_residues: list[tuple[str, int]] = []
    for token in args.epitope_residues.split(","):
        raw = token.strip()
        if not raw or ":" not in raw:
            continue
        chain_id, position_raw = raw.split(":", 1)
        chain_id = chain_id.strip()
        try:
            position = int(position_raw.strip())
        except ValueError:
            continue
        if chain_id:
            epitope_residues.append((chain_id, position))
    target_ion_counts = extract_target_ion_counts(
        target_pdb,
        target_chains=target_chains or None,
        model_number=args.target_model_number,
    )
    auto_source_epitope_residues: list[tuple[str, int]] = []
    if args.auto_pocket_if_missing and not epitope_residues and target_pdb and target_chains:
        auto_source_epitope_residues = infer_target_pocket_residues(
            target_pdb=target_pdb,
            source_target_chains=sorted(target_chains),
            predicted_target_chains=sorted(target_chains),
            model_number=args.target_model_number,
            max_residues=int(args.auto_pocket_max_residues),
        )
    out_pdb_dir = Path(args.out_pdb_dir).expanduser().resolve() if args.out_pdb_dir else None

    entries = []
    for pdb_path in pdb_files:
        if args.external_target_as_target:
            if target_pdb is None:
                raise ValueError("--external-target-as-target requires --target_pdb")
            source_chain_sequences = extract_chain_sequences(pdb_path, model_number=1)
            if not source_chain_sequences:
                raise ValueError(f"No amino-acid binder chains found in {pdb_path}")
            source_chain_ids = [chain_id for chain_id, _seq in source_chain_sequences]
            if binder_source_chains:
                binder_chain_ids = [chain_id for chain_id in source_chain_ids if chain_id in binder_source_chains]
                if not binder_chain_ids:
                    raise ValueError(
                        f"Requested binder_source_chains {binder_source_chains} were not present in {pdb_path.name}; found {source_chain_ids}"
                    )
            else:
                binder_chain_ids = [source_chain_ids[0]]
            binder_sequences = [
                (chain_id, sequence)
                for chain_id, sequence in source_chain_sequences
                if chain_id in binder_chain_ids
            ]

            external_target_sequences = extract_chain_sequences(
                target_pdb,
                chain_filter=target_chains or None,
                model_number=args.target_model_number,
            )
            if not external_target_sequences:
                raise ValueError(
                    f"No external target chains found in {target_pdb} for chains={sorted(target_chains) if target_chains else 'ALL'}"
                )
            target_source_chain_ids = [chain_id for chain_id, _seq in external_target_sequences]
            target_alias_ids = _choose_target_aliases(
                source_chain_ids=source_chain_ids,
                binder_chain_ids=binder_chain_ids,
                target_chain_count=len(target_source_chain_ids),
            )
            target_alias_map = dict(zip(target_source_chain_ids, target_alias_ids))
            target_sequences = [
                (target_alias_map[chain_id], sequence)
                for chain_id, sequence in external_target_sequences
            ]
            entry_sequences = binder_sequences + target_sequences
            binder_chain_id = binder_sequences[0][0] if binder_sequences else None
            target_chain_ids_for_entry = {alias for alias, _sequence in target_sequences}
            mapped_epitope_residues = list(epitope_residues)
            if auto_source_epitope_residues:
                mapped_epitope_residues = [
                    (target_alias_map.get(chain_id, chain_id), position)
                    for chain_id, position in auto_source_epitope_residues
                ]
            if out_pdb_dir is not None:
                write_reference_pdb(
                    out_pdb_dir / f"{pdb_path.stem}.pdb",
                    binder_source_pdb=pdb_path,
                    binder_source_chains=binder_chain_ids,
                    target_pdb=target_pdb,
                    target_source_chains=target_source_chain_ids,
                    target_alias_map=target_alias_map,
                    target_model_number=args.target_model_number,
                )
        else:
            entry_sequences = extract_chain_sequences(pdb_path, model_number=1)
            binder_chain_id = entry_sequences[0][0] if entry_sequences else None
            target_chain_ids_for_entry = target_chains or set()
            mapped_epitope_residues = list(epitope_residues or auto_source_epitope_residues)
            if out_pdb_dir is not None:
                out_pdb_dir.mkdir(parents=True, exist_ok=True)
                out_target = out_pdb_dir / f"{pdb_path.stem}.pdb"
                out_target.write_text(pdb_path.read_text())

        entries.append(
            build_entry(
                pdb_path.stem,
                entry_sequences,
                seeds,
                target_ion_counts=target_ion_counts,
                binder_chain_id=binder_chain_id,
                target_chain_ids=target_chain_ids_for_entry,
                epitope_residues=mapped_epitope_residues,
                pocket_max_distance=args.pocket_max_distance,
            )
        )

    out_path = Path(args.out_json).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as handle:
        json.dump(entries, handle, indent=2)

    ion_note = f" with ions {dict(target_ion_counts)}" if target_ion_counts else ""
    target_note = " using external target chains" if args.external_target_as_target else ""
    print(f"[prep_protenix_batch] Wrote {len(entries)} entries to {out_path}{ion_note}{target_note}")


if __name__ == "__main__":
    main()
