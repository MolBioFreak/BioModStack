"""FA-MPNN pSCE numerical authority, shared by producer and global analysis.

Only BioPython (already in both runtime manifests) and the standard library are
required. pSCE is an Angstrom error, never pLDDT. Sequence probabilities have a
separate native authority. Policy v1 preserves the original producer population:
all models, all residues and all alternate atoms; residue means have equal weight.
"""
import argparse
import json
import math
from pathlib import Path

from Bio.PDB import MMCIFParser, PDBParser
from Bio.PDB.Polypeptide import is_aa
from Bio.SeqUtils import seq1


class NoScoredSidechainError(ValueError):
    """Valid structure/scope without sidechain observations."""


def psce_policy(chain_id="all_chains", ignore_cbeta=True):
    if not isinstance(chain_id, str) or not chain_id.strip():
        raise ValueError("pSCE chain_id must be an explicit chain or all_chains")
    if type(ignore_cbeta) is not bool:
        raise ValueError("pSCE ignore_cbeta must be boolean")
    return {"version": 1, "chain_id": chain_id, "ignore_cbeta": ignore_cbeta,
            "models": "all", "residues": "all", "altloc": "all",
            "aggregation": "residue_mean", "unit": "angstrom"}


def validate_psce_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("Missing pSCE policy")
    expected = psce_policy(policy.get("chain_id"), policy.get("ignore_cbeta"))
    if policy != expected:
        raise ValueError("Unsupported pSCE policy")
    return expected


def compute_psce_profile(path, policy):
    policy = validate_psce_policy(policy)
    path = Path(path)
    parser = MMCIFParser(QUIET=True) if path.suffix.lower() in {".cif", ".mmcif"} else PDBParser(QUIET=True, PERMISSIVE=False)
    structure = parser.get_structure("fampnn", str(path))
    if len(structure) == 0:
        raise ValueError("pSCE structure has no model")
    excluded = {"C", "N", "O", "CA"} | ({"CB"} if policy["ignore_cbeta"] else set())
    chains, sequences, all_scores = {}, {}, []
    for model_index, model in enumerate(structure):
        for chain in model:
            if policy["chain_id"] != "all_chains" and chain.id != policy["chain_id"]:
                continue
            sequence = []
            profile = chains.setdefault(chain.id, {"type": "protein", "psce": [],
                "residue_numbers": [], "insertion_codes": [], "residue_names": [], "model_indices": []})
            # Gemmi's original producer walked every residue/alternate atom.
            # BioPython's default iterators select conformers; unpack both levels.
            for residue in chain.get_unpacked_list():
                if is_aa(residue, standard=False):
                    sequence.append(seq1(residue.resname, custom_map={"MSE": "M"}))
                values = [float(atom.bfactor) for atom in residue.get_unpacked_list() if atom.name not in excluded]
                if any(not math.isfinite(value) or value < 0 for value in values):
                    raise ValueError("pSCE requires finite nonnegative atom errors")
                if not values:
                    continue
                score = sum(values) / len(values)
                profile["psce"].append(score)
                all_scores.append(score)
                profile["residue_numbers"].append(int(residue.id[1]))
                profile["insertion_codes"].append(residue.id[2].strip())
                profile["residue_names"].append(residue.resname)
                profile["model_indices"].append(model_index)
            # Preserve the producer's last-model sequence representation.
            sequences[chain.id] = "".join(sequence)
    chains = {c: v for c, v in chains.items() if v["psce"]}
    for profile in chains.values():
        scores = profile["psce"]
        profile.update(length=len(scores), avg_psce=sum(scores) / len(scores),
                       max_psce=max(scores), min_psce=min(scores))
    if not all_scores:
        raise NoScoredSidechainError("No scored sidechain atoms in requested pSCE scope")
    summary = {"chain_count": len(chains), "residue_count": len(all_scores),
               "avg_psce": sum(all_scores) / len(all_scores),
               "max_psce": max(all_scores), "min_psce": min(all_scores)}
    return {"policy": policy, "chains": chains, "sequences": sequences, "summary": summary}


def average_per_residue_bfactor(input_dir, chain_id, ignore_cbeta, out_dir):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    policy = psce_policy(chain_id, ignore_cbeta)
    results = {}
    for path in sorted(Path(input_dir).glob("*.pdb")):
        try:
            profile = compute_psce_profile(path, policy)
        except NoScoredSidechainError:
            print(f"No scored sidechain atoms in {path.name}; skipping")
            continue
        summary, sequences = profile["summary"], profile["sequences"]
        output = {"design": path.stem, "psce_policy": policy,
                  "sequence": "|".join(f"{c}:{s}" for c, s in sequences.items()) if chain_id == "all_chains" else sequences[chain_id],
                  "chain_avg_psce": {c: round(v["avg_psce"], 2) for c, v in profile["chains"].items()},
                  "fampnn_avg_psce": round(summary["avg_psce"], 2),
                  "fampnn_max_residue_psce": round(summary["max_psce"], 2),
                  "fampnn_min_residue_psce": round(summary["min_psce"], 2)}
        (Path(out_dir) / f"{path.stem}.json").write_text(json.dumps(output, allow_nan=False) + "\n")
        results[path.name] = summary["avg_psce"]
    return results


def main():
    parser = argparse.ArgumentParser(description="FA-MPNN predicted sidechain error (Angstrom; lower is better)")
    parser.add_argument("--input_dir", required=True)
    parser.add_argument("--chain_id", default="all_chains")
    parser.add_argument("--ignore_cbeta", action="store_true")
    parser.add_argument("--out_dir", default="./averagePSCE")
    args = parser.parse_args()
    average_per_residue_bfactor(args.input_dir, args.chain_id, args.ignore_cbeta, args.out_dir)


if __name__ == "__main__":
    main()
