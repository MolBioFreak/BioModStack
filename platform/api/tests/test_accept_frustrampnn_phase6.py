from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_PATH = REPO_ROOT / "scripts" / "accept_frustrampnn_phase6.py"
AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"


def _load_harness():
    spec = importlib.util.spec_from_file_location("accept_frustrampnn_phase6_test", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _entry(relative_path: str, payload: bytes, *, role: str = "test") -> dict[str, object]:
    return {
        "role": role,
        "path": relative_path,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _landscape(residue_count: int = 76) -> dict[str, object]:
    residues = []
    expected_wt = []
    for sequence_index in range(1, residue_count + 1):
        wt = AA_ORDER[(sequence_index - 1) % len(AA_ORDER)]
        expected_wt.append(wt)
        residues.append(
            {
                "entity_instance_id": "entity-1",
                "auth_asym_id": "A",
                "auth_seq_id": sequence_index,
                "insertion_code": "",
                "sequence_index": sequence_index,
                "wt": wt,
                "slots": [
                    {
                        "mutation_aa": aa,
                        "score": float(sequence_index) / 100.0,
                        "native": aa == wt,
                    }
                    for aa in AA_ORDER
                ],
            }
        )
    return {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 1,
        "residues": residues,
        "expected_wt_sequence": "".join(expected_wt),
    }


def test_cli_requires_explicit_no_resume_and_normalizes_repeatable_cases(tmp_path: Path) -> None:
    harness = _load_harness()
    parsed = harness.parse_cli(
        [
            "--cases",
            "scheduler_gpu,one_ubq_1520",
            "--cases",
            "exact_multichain_map",
            "--output-root",
            str(tmp_path / "evidence"),
            "--no-resume",
        ]
    )
    assert parsed.cases == (
        "scheduler_gpu",
        "one_ubq_1520",
        "exact_multichain_map",
    )
    assert parsed.no_resume is True

    with pytest.raises(SystemExit):
        harness.parse_cli(["--cases", "scheduler_gpu", "--output-root", str(tmp_path / "a")])
    with pytest.raises(SystemExit):
        harness.parse_cli(
            ["--cases", "scheduler_gpu", "--output-root", str(tmp_path / "a"), "--resume"]
        )


@pytest.mark.parametrize(
    "case_list",
    ["unknown", "scheduler_gpu,scheduler_gpu", "scheduler_gpu,,one_ubq_1520"],
)
def test_cli_rejects_unknown_duplicate_and_empty_cases(tmp_path: Path, case_list: str) -> None:
    harness = _load_harness()
    with pytest.raises(SystemExit):
        harness.parse_cli(
            ["--cases", case_list, "--output-root", str(tmp_path / "evidence"), "--no-resume"]
        )


def test_dirty_tree_rejection_includes_untracked_and_staged(tmp_path: Path) -> None:
    harness = _load_harness()

    def git_for(status: str):
        values = {
            ("rev-parse", "HEAD"): "a" * 40 + "\n",
            ("branch", "--show-current"): "test-branch\n",
            ("status", "--porcelain=v1", "--untracked-files=all"): status,
            ("diff", "--no-ext-diff", "--binary", "HEAD"): "",
            ("diff", "--cached", "--no-ext-diff", "--binary", "HEAD"): "",
            ("write-tree",): "b" * 40 + "\n",
        }

        def fake_git(_repo: Path, *args: str) -> str:
            return values[args]

        return fake_git

    clean = harness.require_clean_committed_tree(tmp_path, git=git_for(""))
    assert clean["head"] == "a" * 40
    with pytest.raises(harness.AcceptanceFailure, match="untracked"):
        harness.require_clean_committed_tree(tmp_path, git=git_for("?? untracked.txt\n"))
    with pytest.raises(harness.AcceptanceFailure, match="staged"):
        harness.require_clean_committed_tree(tmp_path, git=git_for("M  tracked.txt\n"))


def test_packet_inventory_rejects_traversal_stale_extra_symlink_and_hash_mismatch(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    root = tmp_path / "packet"
    root.mkdir()
    payload = _canonical({"schema_name": "fixture", "schema_version": 1})
    artifact = root / "artifact.json"
    artifact.write_bytes(payload)
    valid = _entry("artifact.json", payload)
    harness.validate_packet_inventory(root, [valid], expected_paths={"artifact.json"})

    with pytest.raises(harness.AcceptanceFailure, match="unsafe"):
        harness.validate_packet_inventory(root, [{**valid, "path": "../artifact.json"}])

    stale = root / "stale.txt"
    stale.write_text("stale", encoding="utf-8")
    with pytest.raises(harness.AcceptanceFailure, match="unmanifested"):
        harness.validate_packet_inventory(root, [valid])
    stale.unlink()

    artifact.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(payload)
    artifact.symlink_to(outside)
    with pytest.raises(harness.AcceptanceFailure, match="symlink"):
        harness.validate_packet_inventory(root, [valid])
    artifact.unlink()

    artifact.write_bytes(payload + b"\n")
    with pytest.raises(harness.AcceptanceFailure, match="size|SHA-256"):
        harness.validate_packet_inventory(root, [valid])


def test_one_ubq_requires_exact_76_by_20_finite_native_order_and_wt() -> None:
    harness = _load_harness()
    landscape = _landscape()
    summary = harness.validate_one_ubq_1520(landscape)
    assert summary == {"residues": 76, "rows": 1520, "native_slots": 76}

    duplicate = json.loads(json.dumps(landscape))
    duplicate["residues"][0]["slots"][1]["mutation_aa"] = "A"
    with pytest.raises(harness.AcceptanceFailure, match="canonical AA order"):
        harness.validate_one_ubq_1520(duplicate)

    nonfinite = json.loads(json.dumps(landscape))
    nonfinite["residues"][0]["slots"][0]["score"] = float("inf")
    with pytest.raises(harness.AcceptanceFailure, match="finite"):
        harness.validate_one_ubq_1520(nonfinite)


def test_exact_multichain_map_rejects_duplicate_identity_and_preserves_gaps_insertions() -> None:
    harness = _load_harness()
    rows = [
        {
            "entity_instance_id": "entity-a",
            "auth_asym_id": "A",
            "auth_seq_id": 10,
            "insertion_code": "",
            "sequence_index": 1,
            "wt": "G",
        },
        {
            "entity_instance_id": "entity-a",
            "auth_asym_id": "A",
            "auth_seq_id": 12,
            "insertion_code": "A",
            "sequence_index": 2,
            "wt": "S",
        },
        {
            "entity_instance_id": "entity-b",
            "auth_asym_id": "B",
            "auth_seq_id": 1,
            "insertion_code": "",
            "sequence_index": 1,
            "wt": "M",
        },
    ]
    expected = [dict(row) for row in rows]
    assert harness.validate_exact_multichain_map({"rows": rows}, expected) == {
        "rows": 3,
        "chains": 2,
    }
    with pytest.raises(harness.AcceptanceFailure, match="duplicate"):
        harness.validate_exact_multichain_map({"rows": rows + [dict(rows[1])]}, expected)


def test_intentional_failure_requires_classified_failure_and_forbids_success_evidence() -> None:
    harness = _load_harness()
    evidence = {
        "parent": {"status": "failed"},
        "child": {"status": "failed", "queue_status": "failed"},
        "terminal_result": {
            "status": "failed",
            "failure_class": "runtime_unavailable",
            "diagnostic": "qualified intentional runtime failure",
            "artifacts": [],
        },
        "success_markers": [],
        "persisted_results": [],
    }
    harness.validate_intentional_runtime_failure(evidence)
    evidence["success_markers"] = ["frustrampnn_complete.reported"]
    with pytest.raises(harness.AcceptanceFailure, match="success"):
        harness.validate_intentional_runtime_failure(evidence)


def test_disabled_case_requires_durable_not_requested_and_no_model_task_or_output() -> None:
    harness = _load_harness()
    evidence = {
        "parent": {
            "status": "completed",
            "provenance": {
                "stage_terminal_states": {
                    "frustrampnn": {"status": "not_requested", "outputs": []}
                }
            },
        },
        "terminal_manifest": {
            "status": "not_requested",
            "requiredness": "not_requested",
            "candidate_count": 0,
            "candidates": [],
            "reported_outputs": [],
        },
        "model_tasks": [],
        "result_bundles": [],
    }
    harness.validate_disabled_not_requested(evidence)
    evidence["model_tasks"] = ["CanonicalFrustraMPNNTask"]
    with pytest.raises(harness.AcceptanceFailure, match="model task"):
        harness.validate_disabled_not_requested(evidence)


def test_submission_contract_allows_only_managed_api_and_never_direct_execution() -> None:
    harness = _load_harness()
    harness.validate_managed_submission(
        "structure_prediction",
        {"method": "POST", "path": "/api/jobs", "json": {"model_id": "esmfold2"}},
    )
    harness.validate_managed_submission(
        "one_ubq_1520",
        {
            "method": "POST",
            "path": "/api/frustrampnn/jobs/uploads/analyze",
            "multipart": {"field": "pdb_file", "source": "1UBQ.cif"},
        },
    )
    for malicious in (
        {"method": "POST", "path": "/api/jobs", "command": ["nextflow", "run"]},
        {"method": "POST", "path": "/api/jobs", "json": {}, "executable": "apptainer"},
        {"method": "POST", "path": "/api/frustrampnn/analyze", "json": {}},
        {"method": "GET", "path": "/api/jobs", "json": {}},
    ):
        with pytest.raises(harness.AcceptanceFailure):
            harness.validate_managed_submission("structure_prediction", malicious)

    source = HARNESS_PATH.read_text(encoding="utf-8")
    assert "subprocess.Popen" not in source
    assert "os.system(" not in source
    assert "shell=True" not in source
