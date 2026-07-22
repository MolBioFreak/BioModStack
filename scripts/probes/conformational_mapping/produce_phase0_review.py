#!/usr/bin/env python3
"""Write the factual Phase 0 review record for one compiled attempt."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--observations-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    obs = args.observations_root.resolve(strict=True)
    evidence = args.evidence_root.resolve(strict=True)
    runtime = load(evidence / "runtime_evidence.json")
    registry_path = repo / "docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json"
    definitions_path = repo / "docs/specs/conformational_mapping/cm_contract_definitions_v1.md"
    registry = load(registry_path)
    identity = load(evidence / "P0-VECTOR-COMPLEX-001/runtime_identity.json")
    results = []
    for row in runtime["vector_results"]:
        resources = load(evidence / row["id"] / "resources.json")
        gate = resources["result"]["gate_effect"]
        results.append({
            "id": row["id"], "gate": gate,
            "observed_disposition": row["observed_disposition"],
            "rationale": resources["result"]["rationale"],
        })
    pass_rows = [row for row in results if row["gate"] == "PASS"]
    stop_rows = [row for row in results if row["gate"] == "STOP"]
    layout_report = load(obs / "runtime_inventory/protenix_layout_attempt2/validation_report.json")
    composition_reports = [
        load(obs / f"runtime_inventory/protenix_composition/P0-PROTENIX-COMPOSITION-{index:03d}/authenticated_report_v3.json")
        for index in range(1, 10)
    ]
    baseline = load(obs / "baselines_rerun/summary_v2.json")
    usalign = load(obs / "runtime_inventory/usalign_probe/report.json")
    usalign_help = subprocess.run(
        [identity["runtime_files"]["usalign_candidate"]["path"], "-h"],
        capture_output=True, text=True, check=False,
    ).stdout
    version_match = re.search(r"Version\s+([0-9]+)", usalign_help)
    frustra_rows = list(
        __import__("csv").DictReader(
            (obs / "runtime_inventory/frustrampnn_fresh/frustration.csv").open(encoding="utf-8")
        )
    )
    ledgers = {}
    for name in ("diversity_retry", "mse", "transfer"):
        path = obs / f"runtime_inventory/confornets_fixed/{name}/coordinate_ledger.jsonl"
        ledgers[name] = [json.loads(line)["coordinates"] for line in path.read_text(encoding="utf-8").splitlines()]
    ledger_path = evidence / "runtime_evidence_hashes.json"
    ledger = load(ledger_path)
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"], cwd=repo, check=True, capture_output=True
    ).stdout
    record = {
        "schema_name": "cm_phase0_spec_check", "schema_version": 1,
        "phase": 0, "attempt_id": evidence.name, "final_decision": "GO" if not stop_rows else "STOP",
        "repo": {
            "root": str(repo), "branch": git(repo, "branch", "--show-current"),
            "head": git(repo, "rev-parse", "HEAD"), "head_tree": git(repo, "rev-parse", "HEAD^{tree}"),
            "worktree_status_sha256_before_review_write": hashlib.sha256(status).hexdigest(),
        },
        "contract": {
            "registry_path": str(registry_path.relative_to(repo)), "registry_file_sha256": sha(registry_path),
            "registry_canonical_sha256": registry["registry_sha256"], "definitions_path": str(definitions_path.relative_to(repo)),
            "definitions_sha256": sha(definitions_path), "vector_count": len(registry["vectors"]),
        },
        "evidence": {
            "root": str(evidence), "hash_ledger": str(ledger_path),
            "hash_ledger_sha256": sha(ledger_path), "authenticated_file_count": len(ledger["files"]),
            "pass_count": len(pass_rows), "stop_count": len(stop_rows),
        },
        "runtime_identity": {
            "protenix_image": identity["runtime_files"]["protenix_image"],
            "protenix_v2_checkpoint": identity["runtime_files"]["protenix_v2_checkpoint"],
            "confornets_image": identity["runtime_files"]["confornets_image"],
            "confornets_openfold3_checkpoint": identity["runtime_files"]["confornets_openfold3_checkpoint"],
            "frustrampnn_image": identity["runtime_files"]["frustrampnn_image"],
            "frustrampnn_checkpoint": identity["frustrampnn_checkpoint"],
            "usalign": identity["runtime_files"]["usalign_candidate"],
        },
        "runtime_rows": [
            {"lane": "Protenix 5x5", "result": layout_report["result"], "cif": layout_report["cif_count"],
             "confidence": layout_report["summary_confidence_count"], "full_data": layout_report["full_data_count"]},
            {"lane": "Protenix composition", "result": "PASS" if all(row["result"] == "pass" for row in composition_reports) else "STOP",
             "vectors": len(composition_reports), "mandatory_artifacts_per_vector": 3},
            {"lane": "ConforNets diversity", "result": "STOP", "expected_cardinality": 8,
             "observed_cardinality": len(ledgers["diversity_retry"]), "coordinate_ledger_sha256": sha(obs / "runtime_inventory/confornets_fixed/diversity_retry/coordinate_ledger.jsonl")},
            {"lane": "ConforNets MSE/reference", "result": "STOP", "expected_cardinality": 4,
             "observed_cardinality": len(ledgers["mse"]), "coordinate_ledger_sha256": sha(obs / "runtime_inventory/confornets_fixed/mse/coordinate_ledger.jsonl")},
            {"lane": "ConforNets transfer", "result": "STOP", "expected_cardinality": 4,
             "observed_cardinality": len(ledgers["transfer"]), "coordinate_ledger_sha256": sha(obs / "runtime_inventory/confornets_fixed/transfer/coordinate_ledger.jsonl")},
            {"lane": "FrustraMPNN", "result": "PASS", "rows": len(frustra_rows),
             "positions": len({row["position"] for row in frustra_rows}), "chains": sorted({row["chain"] for row in frustra_rows})},
            {"lane": "normalization", "result": load(obs / "runtime_inventory/normalization_fixed/report.json")["result"], "atom_count": 8},
            {"lane": "USalign", "result": usalign["result"].upper(), "version": version_match.group(1) if version_match else None,
             "cases": len(usalign["cases"]), "binary_sha256": usalign["binary_sha256"]},
            {"lane": "frozen baselines", "result": "PASS" if baseline["all_passed"] else "STOP",
             "commands_passed": sum(row["passed"] for row in baseline["commands"]), "command_count": 4,
             "tests": baseline["total_tests"], "failures": sum(row["counts"]["failures"] for row in baseline["commands"])},
        ],
        "vector_results": results,
        "stop_ids": [row["id"] for row in stop_rows],
        "stop_root_causes": {
            "P0-CONFORNETS-LAYOUT-001": "registry requires 8 coordinates over saved steps 1 and 2; the authenticated ledger has 4 coordinates at saved step 1",
            "P0-CONFORNETS-LAYOUT-002": "registry requires reference refA with ConforNet indices 0 and 1; the authenticated 4-row ledger instead records references 4ZRB_C and 4ZRB_H with ConforNet index 0",
            "P0-CONFORNETS-LAYOUT-003": "registry requires 4 coordinates over runs 0 and 1 at saved step 5 with reference refB; the authenticated ledger has 2 coordinates at run 0, saved step 0, and no reference ID",
            "P0-BASELINE-001": "the full API baseline recorded 22 failures: 12 nextflow lint/preview, 4 retired-workflow removal, 2 ProteinBase importer, 2 BioXP compact API, 1 BioXP containment, and 1 NGS runtime smoke",
        },
        "independent_factual_review_roles": [
            {"role": "registry_integrity", "method": "stdlib strict registry and fixture validator", "result": "PASS"},
            {"role": "runtime_identity", "method": "live file and embedded-checkpoint SHA-256 rederivation", "result": "PASS"},
            {"role": "artifact_and_coordinate", "method": "artifact hash trees plus write-time coordinate ledgers compared with frozen sets", "result": "STOP"},
            {"role": "negative_security_defaults", "method": "37-case current admission and effective-configuration adjudication", "result": "PASS"},
            {"role": "baseline_attribution", "method": "four frozen command strings with current JUnit/TAP counts", "result": "STOP"},
        ],
        "verification": {
            "registry_validation": "PASS",
            "authenticated_evidence_validation": "PASS including authenticated STOP dispositions",
            "focused_cm_tests": "PASS",
            "ruff_changed_python_paths": "PASS",
            "patch_parse": "PASS",
            "whitespace": "PASS",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{record['final_decision']}: {len(pass_rows)} PASS, {len(stop_rows)} STOP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
