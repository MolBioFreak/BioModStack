#!/usr/bin/env python3
"""
Recover published PPIFlow outputs from surviving RunPartialFlow workdirs.

This is for historical runs where the model generated multiple partial-flow
samples but downstream scoring/filtering/publish only kept sample0. The script
re-scores every recovered sample, re-applies the configured filter per source
backbone, republishes the passing PDBs plus score/filter JSON sidecars, and
leaves anchor/interface helper files untouched.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional


def percentile_threshold(scores: list[float], percentile: Optional[float]) -> Optional[float]:
    if percentile is None or percentile <= 0 or not scores:
        return None
    ordered = sorted(scores)
    rank = max(int(len(ordered) * (percentile / 100.0)) - 1, 0)
    return ordered[rank]


def resolve_workdir(work_root: Path, token: str) -> Optional[Path]:
    short_dir, short_hash = token.split("/", 1)
    prefix_dir = work_root / short_dir
    if not prefix_dir.exists():
        return None
    matches = sorted(path for path in prefix_dir.glob(f"{short_hash}*") if path.is_dir())
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous workdir token {token}: {matches}")
    return matches[0]


def parse_runpartialflow_workdirs(nextflow_log: Path, work_root: Path) -> list[Path]:
    pattern = re.compile(r"\[([0-9a-f]{2}/[0-9a-f]+)\] Submitted process > MATURATION_CHILD_CORE:RunPartialFlow \((\d+)\)")
    indexed: list[tuple[int, Path]] = []
    for line in nextflow_log.read_text(errors="ignore").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        resolved = resolve_workdir(work_root, match.group(1))
        if resolved is not None:
            indexed.append((int(match.group(2)), resolved))
    indexed.sort(key=lambda item: item[0])
    return [path for _index, path in indexed]


def load_original_pdb(workdir: Path) -> Path:
    command_log = workdir / ".command.log"
    if command_log.exists():
        match = re.search(r"complex_pdb='([^']+\.pdb)'", command_log.read_text(errors="ignore"))
        if match:
            candidate = workdir / Path(match.group(1)).name
            if candidate.exists():
                return candidate
    candidates = sorted(
        path for path in workdir.glob("*.pdb")
        if path.is_file() and "_ppiflow_sample" not in path.name
    )
    if len(candidates) != 1:
        raise RuntimeError(f"Unable to identify original PDB in {workdir}")
    return candidates[0]


def clean_republished_outputs(directory: Path) -> None:
    if not directory.exists():
        return
    for pattern in (
        "*_ppiflow_sample*.pdb",
        "*_ppiflow_sample*_partial_flow_score.json",
        "*_ppiflow_sample*_maturation_filter.json",
        "*_rotamer_enrichment.json",
        "*_interface_score.json",
        "*_anchors.json",
        "*_enriched_complex.pdb",
        "*_ppiflow_positions.txt",
        "*_cdr_positions.txt",
    ):
        for path in directory.glob(pattern):
            if path.is_file():
                path.unlink()


def ensure_dir(path: Optional[Path]) -> None:
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2))


def publish_file(src: Path, destinations: Iterable[Path]) -> None:
    for destination in destinations:
        ensure_dir(destination.parent)
        shutil.copy2(src, destination)


def build_score_command(args: argparse.Namespace, score_script: Path, work_root: Path) -> list[str]:
    if not args.container_image:
        return [sys.executable, str(score_script)]

    runtime = shutil.which("apptainer") or shutil.which("singularity")
    if not runtime:
        raise SystemExit("Neither apptainer nor singularity is available for containerized scoring")

    bind_roots = {
        str(Path(args.code_root).resolve()),
        str(work_root.resolve()),
        str(Path(args.results_dir).resolve()),
    }
    if args.parent_collect_dir:
        bind_roots.add(str(Path(args.parent_collect_dir).resolve()))
    bind_roots.add("/mnt/BioModStack")
    bind_args: list[str] = []
    for bind_root in sorted(bind_roots):
        if Path(bind_root).exists():
            bind_args.extend(["--bind", bind_root])
    return [runtime, "exec", *bind_args, str(args.container_image), "python3", str(score_script)]


def load_inline_score_module(score_script: Path):
    spec = importlib.util.spec_from_file_location("score_maturation_module", score_script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load score module from {score_script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compute_score_payload_inline(
    score_module,
    *,
    original_pdb: Path,
    matured_pdb: Path,
    antibody_chains: str,
    antigen_chains: str,
    distance_cutoff: float,
) -> dict:
    pyrosetta = score_module.pyrosetta
    if not getattr(compute_score_payload_inline, "_pyrosetta_initialized", False):
        pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1")
        compute_score_payload_inline._pyrosetta_initialized = True

    pose_original = pyrosetta.pose_from_pdb(str(original_pdb))
    pose_matured = pyrosetta.pose_from_pdb(str(matured_pdb))
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose_original)
    scorefxn(pose_matured)

    requested_ab = score_module.parse_chain_list(antibody_chains)
    requested_ag = score_module.parse_chain_list(antigen_chains)
    antibody_chains_original, antigen_chains_original, original_detected_chains = score_module.resolve_chain_groups(
        pose_original,
        requested_ab,
        requested_ag,
    )
    antibody_chains_matured, antigen_chains_matured, matured_detected_chains = score_module.resolve_chain_groups(
        pose_matured,
        requested_ab,
        requested_ag,
        fallback_ab_count=len(antibody_chains_original),
        fallback_ag_count=len(antigen_chains_original),
    )
    matured_to_original_chain_map = {
        matured_chain: original_chain
        for original_chain, matured_chain in zip(antibody_chains_original, antibody_chains_matured)
    }

    interface_res_orig = score_module.detect_interface_residues(
        pose_original, antibody_chains_original, antigen_chains_original, distance_cutoff
    )
    interface_res_matured = score_module.detect_interface_residues(
        pose_matured, antibody_chains_matured, antigen_chains_matured, distance_cutoff
    )
    iface_orig = score_module.interface_score(pose_original, interface_res_orig)
    iface_matured = score_module.interface_score(pose_matured, interface_res_matured)

    coords_original = score_module.extract_chain_coords(
        pose_original, antibody_chains_original
    )
    coords_matured = score_module.extract_chain_coords(
        pose_matured, antibody_chains_matured, chain_remap=matured_to_original_chain_map
    )
    ordered_original = score_module.extract_chain_coords_by_order(
        pose_original, antibody_chains_original
    )
    ordered_matured = score_module.extract_chain_coords_by_order(
        pose_matured, antibody_chains_matured, chain_remap=matured_to_original_chain_map
    )

    return {
        "interface_score_original": iface_orig,
        "interface_score_matured": iface_matured,
        "delta_interface_score": iface_matured - iface_orig,
        "rmsd_backbone": score_module.rmsd(
            coords_original,
            coords_matured,
            ordered_a=ordered_original,
            ordered_b=ordered_matured,
        ),
        "sequence_identity": score_module.sequence_identity(
            pose_original,
            pose_matured,
            antibody_chains_original,
            antibody_chains_matured,
            chain_remap_b=matured_to_original_chain_map,
        ),
        "clash_count_ca": score_module.count_ca_clashes(pose_matured, cutoff=2.5),
        "antibody_chains_requested": requested_ab,
        "antigen_chains_requested": requested_ag,
        "antibody_chains_original": antibody_chains_original,
        "antibody_chains_matured": antibody_chains_matured,
        "antigen_chains_original": antigen_chains_original,
        "antigen_chains_matured": antigen_chains_matured,
        "detected_chains_original": original_detected_chains,
        "detected_chains_matured": matured_detected_chains,
        "matured_to_original_chain_map": matured_to_original_chain_map,
        "interface_residue_count_original": len(interface_res_orig),
        "interface_residue_count_matured": len(interface_res_matured),
        "distance_cutoff": distance_cutoff,
    }


def build_filter_report(
    score_payload: dict,
    threshold: Optional[float],
    min_improvement: Optional[float],
    percentile: Optional[float],
    passed: bool,
    score_json_path: Path,
    filter_disabled: bool,
) -> dict:
    return {
        "passed": passed,
        "delta_interface_score": score_payload.get("delta_interface_score"),
        "threshold": threshold,
        "min_improvement": min_improvement,
        "percentile": percentile,
        "filter_disabled": filter_disabled,
        "score_json": str(score_json_path.resolve()),
        "score_data": score_payload,
        "interface_score_original": score_payload.get("interface_score_original"),
        "interface_score_matured": score_payload.get("interface_score_matured"),
        "rmsd_backbone": score_payload.get("rmsd_backbone"),
        "sequence_identity": score_payload.get("sequence_identity"),
        "clash_count_ca": score_payload.get("clash_count_ca"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover missing published PPIFlow sample outputs")
    parser.add_argument("--nextflow_log", required=True, help="Child nextflow.log path")
    parser.add_argument("--work_root", required=True, help="Nextflow work root")
    parser.add_argument("--results_dir", required=True, help="Child run/ppiflow/results directory")
    parser.add_argument("--parent_collect_dir", default="", help="Optional parent collected/backbone_refine directory")
    parser.add_argument("--code_root", required=True, help="Repo root containing score_maturation.py")
    parser.add_argument("--container_image", default="", help="Optional container image for PyRosetta scoring")
    parser.add_argument("--antibody_chains", required=True, help="Comma-separated antibody chains")
    parser.add_argument("--antigen_chains", required=True, help="Comma-separated antigen chains")
    parser.add_argument("--distance_cutoff", type=float, default=8.0, help="Interface distance cutoff")
    parser.add_argument("--min_improvement", type=float, default=None, help="Minimum delta-interface threshold")
    parser.add_argument("--percentile", type=float, default=None, help="Per-source percentile threshold")
    parser.add_argument("--disable_filter", action="store_true", help="Pass all recovered samples through")
    args = parser.parse_args()

    nextflow_log = Path(args.nextflow_log)
    work_root = Path(args.work_root)
    results_dir = Path(args.results_dir)
    parent_collect_dir = Path(args.parent_collect_dir) if args.parent_collect_dir else None
    code_root = Path(args.code_root)
    score_script = code_root / "scripts" / "score_maturation.py"
    score_cmd_prefix = build_score_command(args, score_script, work_root)
    inline_score_module = None
    if not args.container_image:
        try:
            inline_score_module = load_inline_score_module(score_script)
        except Exception:
            inline_score_module = None

    if not nextflow_log.exists():
        raise SystemExit(f"Missing nextflow log: {nextflow_log}")
    if not score_script.exists():
        raise SystemExit(f"Missing score script: {score_script}")

    ensure_dir(results_dir)
    ensure_dir(parent_collect_dir)
    clean_republished_outputs(results_dir)
    if parent_collect_dir is not None:
        clean_republished_outputs(parent_collect_dir)

    total_scored = 0
    total_passed = 0
    total_workdirs = 0
    published_targets: list[Path] = [results_dir]
    if parent_collect_dir is not None:
        published_targets.append(parent_collect_dir)

    for workdir in parse_runpartialflow_workdirs(nextflow_log, work_root):
        if not workdir.exists():
            continue
        sample_pdbs = sorted((workdir / "ppiflow_backbones").glob("*_ppiflow_sample*.pdb"))
        if not sample_pdbs:
            continue
        total_workdirs += 1
        original_pdb = load_original_pdb(workdir)
        scores_dir = workdir / "recovered_scores"
        scores_dir.mkdir(exist_ok=True)
        scored_entries: list[tuple[Path, Path, dict]] = []
        for sample_pdb in sample_pdbs:
            score_json = scores_dir / f"{sample_pdb.stem}_partial_flow_score.json"
            if inline_score_module is not None:
                score_payload = compute_score_payload_inline(
                    inline_score_module,
                    original_pdb=original_pdb,
                    matured_pdb=sample_pdb,
                    antibody_chains=args.antibody_chains,
                    antigen_chains=args.antigen_chains,
                    distance_cutoff=args.distance_cutoff,
                )
                write_json(score_json, score_payload)
            else:
                subprocess.run(
                    [
                        *score_cmd_prefix,
                        "--original_pdb",
                        str(original_pdb),
                        "--matured_pdb",
                        str(sample_pdb),
                        "--antibody_chains",
                        args.antibody_chains,
                        "--antigen_chains",
                        args.antigen_chains,
                        "--distance_cutoff",
                        str(args.distance_cutoff),
                        "--output",
                        str(score_json),
                    ],
                    check=True,
                )
                score_payload = json.loads(score_json.read_text())
            scored_entries.append((sample_pdb, score_json, score_payload))
            total_scored += 1

        deltas = [
            float(entry[2]["delta_interface_score"])
            for entry in scored_entries
            if entry[2].get("delta_interface_score") is not None
        ]
        threshold = args.min_improvement
        percentile_cutoff = percentile_threshold(deltas, args.percentile)
        if not args.disable_filter and percentile_cutoff is not None:
            threshold = percentile_cutoff

        for sample_pdb, score_json, score_payload in scored_entries:
            delta = score_payload.get("delta_interface_score")
            if args.disable_filter:
                passed = True
            elif threshold is None:
                passed = True
            else:
                passed = delta is not None and float(delta) <= float(threshold)

            filter_report = build_filter_report(
                score_payload=score_payload,
                threshold=threshold,
                min_improvement=args.min_improvement,
                percentile=args.percentile,
                passed=passed,
                score_json_path=score_json,
                filter_disabled=args.disable_filter,
            )
            filter_json = score_json.with_name(score_json.name.replace("_partial_flow_score.json", "_maturation_filter.json"))
            write_json(filter_json, filter_report)

            publish_file(score_json, [dest / score_json.name for dest in published_targets])
            publish_file(filter_json, [dest / filter_json.name for dest in published_targets])
            if passed:
                publish_file(sample_pdb, [dest / sample_pdb.name for dest in published_targets])
                total_passed += 1

    print(
        json.dumps(
            {
                "workdirs_processed": total_workdirs,
                "samples_scored": total_scored,
                "samples_published": total_passed,
                "results_dir": str(results_dir),
                "parent_collect_dir": str(parent_collect_dir) if parent_collect_dir else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
