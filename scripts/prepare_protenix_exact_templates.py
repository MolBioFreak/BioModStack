#!/usr/bin/env python3
"""Inject exact staged target templates into Protenix input JSON.

Protenix inference consumes per-chain ``proteinChain.templatesPath`` files
containing HHR/A3M template hits. For anchored target conditioning, the generic
template search can rank homologs ahead of the exact staged target template,
causing the exact target to be excluded within Protenix's internal candidate
cap. This helper bypasses that ambiguity by writing an exact single-hit A3M for
the target chain such as ``2lgv_A`` and attaching it directly to the target
protein chain(s) before inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_a3m(path: Path, records: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for header, sequence in records:
            handle.write(f">{header}\n{sequence}\n")


def _split_target_copies(task: dict, selected: set[str]) -> None:
    """Split entity-wide templates into copy scope, rebasing native endpoints."""
    import copy
    mapping, counts, sequences = {}, {}, []
    for entity, wrapper in enumerate(task.get("sequences", []), 1):
        chain = next(iter(wrapper.values()))
        count = int(chain.get("count", 1))
        counts[entity] = count
        ids = chain.get("id", [])
        ids = [ids] if isinstance(ids, str) else ids
        split = "proteinChain" in wrapper and count > 1 and bool(selected.intersection(ids))
        if split:
            for index in range(count):
                row = copy.deepcopy(wrapper)
                row["proteinChain"].update(count=1, id=[ids[index]])
                sequences.append(row)
                mapping[entity, index + 1] = (len(sequences), 1)
        else:
            sequences.append(wrapper)
            for index in range(count):
                mapping[entity, index + 1] = (len(sequences), index + 1)
    if all(key == value for key, value in mapping.items()):
        return

    def assign(row, ek, ck, endpoint):
        e, c = mapping[endpoint]
        row[ek] = str(e) if isinstance(row[ek], str) else e
        row[ck] = str(c) if isinstance(row.get(ck), str) else c

    bonds = []
    for bond in task.get("covalent_bonds", []):
        keys = [(f"{side}_entity", f"{side}_copy") if f"{side}_entity" in bond
                else (f"entity{i}", f"copy{i}") for i, side in enumerate(("left", "right"), 1)]
        endpoints = []
        for ek, ck in keys:
            entity = int(bond[ek])
            copies = [int(bond[ck])] if bond.get(ck) is not None else range(1, counts[entity] + 1)
            endpoints.append([(entity, c) for c in copies])
        if len(endpoints[0]) != len(endpoints[1]):
            # Preserve native-invalid input for the existing parser, not a
            # truncated bond set or a new launch refusal.
            import warnings
            warnings.warn("Unbalanced native bond copies; copy splitting was not applied")
            return
        # Native omitted copies are zipped, never a Cartesian product.
        for left, right in zip(*endpoints):
            row = copy.deepcopy(bond)
            for (ek, ck), endpoint in zip(keys, (left, right)):
                assign(row, ek, ck, endpoint)
            bonds.append(row)

    def rebase(value):
        if isinstance(value, list):
            for row in value:
                rebase(row)
        elif isinstance(value, dict):
            for ek, ck in (("entity", "copy"), ("entity1", "copy1"), ("entity2", "copy2")):
                if ek in value and ck in value:
                    assign(value, ek, ck, (int(value[ek]), int(value[ck])))
            for row in value.values():
                rebase(row)

    task["sequences"] = sequences
    if "covalent_bonds" in task:
        task["covalent_bonds"] = bonds
    rebase(task.get("constraint", {}))


def attach_manifest_templates(payload: list[dict], manifest: dict, out_dir: Path,
                              target_chains: list[str], generic_templates: bool = False) -> int:
    """Bind exact template hits to source-selected targets, preserving the input."""
    import warnings
    rewired = 0
    for task_index, task in enumerate(payload):
        record = manifest.get(task.get("name"))
        if record is None and len(manifest) == 1:
            record = next(iter(manifest.values()))
        if record is None:
            warnings.warn(f"No source template association for {task.get('name')!r}")
            continue
        source_sequences = record["sequences"]
        selected = set(target_chains or source_sequences)
        _split_target_copies(task, selected)
        for entity_index, wrapper in enumerate(task.get("sequences", [])):
            chain = wrapper.get("proteinChain")
            if not isinstance(chain, dict):
                continue
            ids = chain.get("id", [])
            ids = [ids] if isinstance(ids, str) else ids
            if not selected.intersection(ids):
                # Target anchoring must not start an unrelated binder template
                # search. An existing explicit per-chain hit file stays owned
                # by the caller; an empty native hit file means no templates.
                if not generic_templates and not chain.get("templatesPath"):
                    empty = out_dir / f"task{task_index:03d}_seq{entity_index:03d}_no_templates.a3m"
                    _write_a3m(empty, [])
                    chain["templatesPath"] = str(empty.resolve())
                continue
            source_ids = [i for i in ids if i in selected and i in source_sequences]
            if not source_ids and len(source_sequences) == 1:
                source_ids = list(source_sequences)  # Explicit single-source alias.
            if (len(ids) != 1 or len(source_ids) != 1 or
                    source_sequences[source_ids[0]] != chain["sequence"]):
                warnings.warn(f"Unresolved exact source association for {task.get('name')!r} chains {ids!r}; retaining native input")
                continue
            path = out_dir / f"task{task_index:03d}_seq{entity_index:03d}_exact.a3m"
            _write_a3m(path, [(f"{record['template_id']}_{source_id}/1-{len(chain['sequence'])} mol:protein length:{len(chain['sequence'])} exact_target_template", chain['sequence'])
                             for source_id in source_ids])
            chain["templatesPath"] = str(path.resolve())
            rewired += 1
    return rewired


def main() -> None:
    parser = argparse.ArgumentParser(description="Force exact staged target templates into Protenix input JSON")
    parser.add_argument("--input_json", required=True)
    parser.add_argument("--output_json", required=True)
    parser.add_argument("--manifest", help="Source-bound extract_target_templates manifest")
    parser.add_argument("--target_chains", default="", help="Native input target chain IDs")
    parser.add_argument("--generic-templates", action="store_true")
    parser.add_argument("--target_sequence")
    parser.add_argument("--template_pdb_id")
    parser.add_argument("--template_chains", help="Comma-separated target chain IDs, e.g. A or A,B")
    parser.add_argument("--out_dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.input_json).expanduser().resolve()
    output_path = Path(args.output_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    payload = _load_json(input_path)
    if not isinstance(payload, list):
        raise ValueError(f"Expected top-level list in {input_path}")

    if args.manifest:
        rewired = attach_manifest_templates(payload, _load_json(Path(args.manifest)), out_dir,
                                             [x.strip() for x in args.target_chains.split(",") if x.strip()],
                                             generic_templates=args.generic_templates)
        _write_json(output_path, payload)
        print(f"[prepare_protenix_exact_templates] Rewired {rewired} source-bound target chains")
        return
    if not all((args.target_sequence, args.template_pdb_id, args.template_chains)):
        parser.error("exact templates require --manifest or target_sequence/template_pdb_id/template_chains")
    target_sequence = args.target_sequence.strip()
    if not target_sequence:
        raise ValueError("target_sequence is required")

    template_pdb_id = args.template_pdb_id.strip().lower()
    template_chains = [token.strip() for token in args.template_chains.split(",") if token.strip()]
    if not template_chains:
        raise ValueError("At least one template chain ID is required")

    rewired = 0
    for task_idx, task in enumerate(payload):
        sequences = task.get("sequences", [])
        if not isinstance(sequences, list):
            continue
        for seq_idx, wrapper in enumerate(sequences):
            if not isinstance(wrapper, dict):
                continue
            chain = wrapper.get("proteinChain")
            if not isinstance(chain, dict):
                continue
            if str(chain.get("sequence", "") or "").strip() != target_sequence:
                continue

            exact_path = out_dir / f"task{task_idx:03d}_seq{seq_idx:03d}_{template_pdb_id}_exact.a3m"
            exact_records = [
                (
                    f"{template_pdb_id}_{chain_id}/1-{len(target_sequence)} mol:protein length:{len(target_sequence)} exact_target_template",
                    target_sequence,
                )
                for chain_id in template_chains
            ]
            _write_a3m(exact_path, exact_records)
            chain["templatesPath"] = str(exact_path)
            rewired += 1

    if rewired == 0:
        raise ValueError("No target protein chains matched target_sequence; nothing was rewired")

    _write_json(output_path, payload)
    print(
        f"[prepare_protenix_exact_templates] Rewired {rewired} target protein chains "
        f"to exact templates for {template_pdb_id}"
    )


if __name__ == "__main__":
    main()
