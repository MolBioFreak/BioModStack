from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from services.conformational_mapping.contracts import (
    AA_ORDER,
    canonical_json_bytes,
    canonical_sha256,
)
from services.conformational_mapping.frustration import finalize_landscape, score_class
from services.conformational_mapping.structure_normalizer import (
    normalize_conformational_mapping_structure,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "frustrampnn" / "phase0"
CHARACTERIZATION = FIXTURE_ROOT / "cm_characterization_v1.json"
OWNERSHIP = FIXTURE_ROOT / "ownership_inventory_v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_analysis_plane() -> Any:
    path = REPO_ROOT / "scripts" / "run_conformational_mapping_analysis_plane.py"
    spec = importlib.util.spec_from_file_location("phase0_cm_analysis_plane", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_characterization(tmp_path: Path) -> tuple[bytes, bytes, dict[str, Any]]:
    frozen = _load(CHARACTERIZATION)
    source = REPO_ROOT / frozen["source_fixture"]
    pdb = tmp_path / "normalized.pdb"
    map_path = tmp_path / "cm_structure_map_v1.json"
    structure_map = normalize_conformational_mapping_structure(
        input_path=source,
        output_pdb_path=pdb,
        map_path=map_path,
        target_id=frozen["expected_structure_map"]["target_id"],
        candidate_id=frozen["expected_structure_map"]["candidate_id"],
        complex_snapshot=frozen["complex_snapshot"],
    )
    return pdb.read_bytes(), map_path.read_bytes(), structure_map


def _finalize_csv(tmp_path: Path, raw_csv: str) -> dict[str, Any]:
    frozen = _load(CHARACTERIZATION)
    _, _, structure_map = _normalize_characterization(tmp_path)
    raw = tmp_path / "frustrampnn.csv"
    raw.write_text(raw_csv, encoding="utf-8", newline="")
    return finalize_landscape(
        raw,
        structure_map,
        checkpoint_id="megascale.ckpt",
        checkpoint_sha256="a" * 64,
        tool_id="frustrampnn",
        tool_sha256="b" * 64,
        container_sha256="c" * 64,
    )


def _raw_rows(raw_csv: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(raw_csv.splitlines())
    assert reader.fieldnames is not None
    return list(reader.fieldnames), list(reader)


def _policy_neutral_summary_characterization(
    structure_map: dict[str, Any], landscape: dict[str, Any]
) -> dict[str, Any]:
    map_rows = structure_map["rows"]
    residues = landscape["residues"]
    slots = [slot for residue in residues for slot in residue["slots"]]
    observed = [slot for slot in slots if slot["status"] != "missing_row"]
    scoreable = [slot for slot in slots if slot["scoreable"]]
    native = [slot for slot in scoreable if slot["native"]]
    missingness: dict[str, int] = {}
    for slot in slots:
        if slot["status"] != "ok":
            missingness[slot["status"]] = missingness.get(slot["status"], 0) + 1

    def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            name: sum(row["class"] == name for row in rows)
            for name in ("high", "neutral", "minimally_frustrated")
        }

    def fractions(class_counts: dict[str, int], total: int) -> dict[str, float]:
        return {name: class_counts[name] / total if total else 0.0 for name in class_counts}

    native_counts = counts(native)
    landscape_counts = counts(scoreable)
    mapped = [row for row in map_rows if row["status"] == "mapped"]
    support = []
    for residue in residues:
        residue_slots = residue["slots"]
        support.append(
            {
                "entity_instance_id": residue["entity_instance_id"],
                "auth_asym_id": residue["auth_asym_id"],
                "expected_residues": 1,
                "mapped_residues": 1,
                "scoreable_residues": 1,
                "expected_slots": len(AA_ORDER),
                "observed_slots": sum(slot["status"] != "missing_row" for slot in residue_slots),
                "scoreable_slots": sum(slot["scoreable"] for slot in residue_slots),
            }
        )
    return {
        "schema_name": "frustrampnn_summary",
        "schema_version": 1,
        "target_id": landscape["target_id"],
        "candidate_id": landscape["candidate_id"],
        "landscape_sha256": canonical_sha256(landscape),
        "residue_support": {
            "expected": len(map_rows),
            "mapped": len(mapped),
            "scoreable": len(residues),
            "excluded": sum(row["status"] not in {"mapped", "ambiguous"} for row in map_rows),
            "ambiguous": sum(row["status"] == "ambiguous" for row in map_rows),
        },
        "slot_support": {
            "expected": len(residues) * len(AA_ORDER),
            "observed": len(observed),
            "scoreable": len(scoreable),
        },
        "missingness_by_reason": missingness,
        "native_slot_counts": native_counts,
        "native_slot_fractions": fractions(native_counts, len(native)),
        "complete_landscape_counts": landscape_counts,
        "complete_landscape_fractions": fractions(landscape_counts, len(scoreable)),
        "support_by_entity_chain": support,
        "threshold_policy": {
            "id": landscape["threshold_policy_id"],
            "sha256": landscape["threshold_policy_sha256"],
            "high_max": -1.0,
            "minimal_min": 0.58,
        },
    }


def _render_rows(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    from io import StringIO

    handle = StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


def test_phase0_ownership_inventory_reconfirms_current_head_surfaces() -> None:
    inventory = _load(OWNERSHIP)
    assert inventory["base_head"] == "f8135c00ec160e4c40b321dd031615321e5aa352"
    assert inventory["spec_sha256"] == "9359c25994a99de00a738ea4658daeccc8adf5e2d730eafafc2220998b8e4305"

    for category in (
        "execution_strings",
        "module_includes",
        "selectors",
        "api_routes",
        "ui_calls",
        "parsers",
        "cm_compatibility_owners",
    ):
        for record in inventory[category]:
            text = (REPO_ROOT / record["path"]).read_text(encoding="utf-8")
            assert all(anchor in text for anchor in record["anchors"]), record

    for field in inventory["db_fields"]:
        assert field["field"] in (REPO_ROOT / field["model_path"]).read_text(encoding="utf-8")
        assert field["field"] in (REPO_ROOT / field["read_projection_path"]).read_text(encoding="utf-8")
        for writer in field["writers"]:
            assert field["field"] in (REPO_ROOT / writer).read_text(encoding="utf-8")

    base_head = inventory["base_head"]
    base_tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_head],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    pattern = inventory["scan"]["production_reference_regex"]

    def base_matches(*paths: str) -> list[str]:
        completed = subprocess.run(
            ["git", "grep", "-I", "-i", "-l", "-E", pattern, base_head, "--", *paths],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode in {0, 1}, completed.stderr
        return sorted(line.split(":", 1)[1] for line in completed.stdout.splitlines())

    production_roots = (
        "scripts", "modules", "workflows", "platform/api",
        "platform/frontend/src", "schemas", "apptainer", "nextflow.config",
    )
    production_paths = sorted(
        relative
        for relative in base_matches(*production_roots)
        if "/tests/" not in relative
    )
    pre_phase0_tests = sorted(
        relative
        for relative in base_matches("platform/api/tests", "platform/frontend/tests")
        if "test_frustrampnn_phase0_" not in relative
        and (
            (
                relative.startswith("platform/api/tests/")
                and Path(relative).name.startswith("test")
                and relative.endswith(".py")
            )
            or (
                relative.startswith("platform/frontend/tests/")
                and ".test." in Path(relative).name
            )
        )
    )
    assert production_paths == inventory["scan"]["production_reference_paths"]
    assert len(base_tracked) == inventory["scan"]["tracked_files"]
    assert pre_phase0_tests == inventory["scan"]["pre_phase0_test_reference_paths"]


def test_phase0_runtime_readiness_snapshot_and_optional_live_preflight() -> None:
    runtime = _load(OWNERSHIP)["runtime_identity"]
    assert runtime["readiness_note"].startswith("snapshot evidence only")
    assert runtime["analysis_plane_help_exit"] == 0
    assert runtime["sif_regular"] is True and runtime["sif_symlink"] is False
    assert runtime["ambient_adapter_import_root"].endswith("/shape-blueprint-v1/platform/api")
    if os.environ.get("BMS_FRUSTRAMPNN_PHASE0_PREFLIGHT") != "1":
        return

    adapter = Path(runtime["adapter_python"])
    sif = Path(runtime["sif_path"])
    analysis_plane = REPO_ROOT / "scripts/run_conformational_mapping_analysis_plane.py"
    assert adapter.is_file()
    assert _sha256_file(adapter) == runtime["adapter_python_sha256"]
    assert _sha256_file(analysis_plane) == runtime["analysis_plane_sha256"]
    help_run = subprocess.run(
        [str(adapter), str(analysis_plane), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert help_run.returncode == runtime["analysis_plane_help_exit"]
    assert sif.is_file() and not sif.is_symlink()
    assert sif.stat().st_size == runtime["sif_bytes"]
    assert _sha256_file(sif) == runtime["sif_sha256"]


def test_phase0_cm_structure_map_identity_is_byte_and_hash_frozen(tmp_path: Path) -> None:
    frozen = _load(CHARACTERIZATION)
    pdb_bytes, map_bytes, structure_map = _normalize_characterization(tmp_path)

    assert structure_map == frozen["expected_structure_map"]
    assert hashlib.sha256(pdb_bytes).hexdigest() == frozen["expected_normalized_pdb_sha256"]
    assert map_bytes == canonical_json_bytes(structure_map) + b"\n"
    assert hashlib.sha256(map_bytes).hexdigest() == frozen["expected_structure_map_file_sha256"]
    assert canonical_sha256(structure_map) == frozen["expected_structure_map_sha256"]


def test_phase0_cm_landscape_finalization_is_exact(tmp_path: Path) -> None:
    frozen = _load(CHARACTERIZATION)
    landscape = _finalize_csv(tmp_path, frozen["raw_csv"])

    assert landscape == frozen["expected_landscape"]
    assert canonical_sha256(landscape) == frozen["expected_landscape_sha256"]
    assert [slot["mutation_aa"] for slot in landscape["residues"][0]["slots"]] == list(AA_ORDER)


def test_phase0_policy_neutral_summary_contract_is_frozen(tmp_path: Path) -> None:
    frozen = _load(CHARACTERIZATION)
    _, _, structure_map = _normalize_characterization(tmp_path)
    landscape = _finalize_csv(tmp_path, frozen["raw_csv"])
    summary = _policy_neutral_summary_characterization(structure_map, landscape)

    assert summary == frozen["expected_policy_neutral_summary_contract"]
    assert not {"candidate_rank", "ranking", "pass", "decision", "presentation"}.intersection(summary)


def test_phase0_cm_canonical_json_profile_is_frozen() -> None:
    frozen = _load(CHARACTERIZATION)
    assert canonical_json_bytes(frozen["canonical_sample"]) == frozen["canonical_sample_utf8"].encode()
    assert canonical_sha256(frozen["canonical_sample"]) == frozen["canonical_sample_sha256"]


def test_phase0_historical_cm_frustrampnn_command_argv_is_frozen() -> None:
    frozen = _load(CHARACTERIZATION)
    module = _load_analysis_plane()
    command = module._frustrampnn_command(
        apptainer="/usr/bin/apptainer",
        container=Path("/proc/self/fd/9"),
        tool="/opt/venv/bin/frustrampnn",
        normalized=Path("/phase0/output/normalized_input.pdb"),
        checkpoint=Path("/opt/frustrampnn_weights/megascale.ckpt"),
        raw=Path("/phase0/output/frustrampnn.csv"),
        output_root=Path("/phase0/output"),
        gpu_id=3,
    )
    assert command == frozen["historical_cm_command_argv"]
    assert frozen["historical_cm_command_limitations"] == [
        "missing_explicit_device_cuda",
        "missing_explicit_gpu_id_argument",
    ]


def test_phase0_cm_missingness_remains_explicit_and_unscored(tmp_path: Path) -> None:
    frozen = _load(CHARACTERIZATION)
    fieldnames, rows = _raw_rows(frozen["raw_csv"])
    landscape = _finalize_csv(tmp_path, _render_rows(fieldnames, rows[:1]))
    slots = landscape["residues"][0]["slots"]

    assert slots[0]["status"] == "ok"
    assert sum(slot["status"] == "missing_row" for slot in slots) == 19
    assert all(
        slot["score"] is None and slot["class"] is None and not slot["scoreable"]
        for slot in slots[1:]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        (
            "duplicate",
            _load(CHARACTERIZATION)["cm_compatibility_semantics"]["duplicate_slot_status"],
        ),
        (
            "nonfinite",
            _load(CHARACTERIZATION)["cm_compatibility_semantics"]["nonfinite_slot_status"],
        ),
        (
            "wt_mismatch",
            _load(CHARACTERIZATION)["cm_compatibility_semantics"]["wt_mismatch_slot_status"],
        ),
    ],
    ids=["duplicate", "nonfinite", "wt-mismatch"],
)
def test_phase0_cm_invalid_slots_fail_closed_without_a_score(
    tmp_path: Path,
    mutation: str,
    expected_status: str,
) -> None:
    frozen = _load(CHARACTERIZATION)
    fieldnames, rows = _raw_rows(frozen["raw_csv"])
    if mutation == "duplicate":
        rows.append(dict(rows[0]))
    elif mutation == "nonfinite":
        rows[0]["score"] = "NaN"
    else:
        rows[0]["wt"] = "V"

    landscape = _finalize_csv(tmp_path, _render_rows(fieldnames, rows))
    slot = landscape["residues"][0]["slots"][0]
    assert slot["status"] == expected_status
    assert slot["score"] is None
    assert slot["class"] is None
    assert slot["scoreable"] is False


def test_phase0_cm_compatibility_semantics_remain_cm_owned_not_product_policy() -> None:
    frozen = _load(CHARACTERIZATION)
    expected = frozen["cm_compatibility_semantics"]
    landscape = frozen["expected_landscape"]

    assert landscape["schema_name"] == expected["landscape_schema_name"]
    assert landscape["threshold_policy_id"] == expected["threshold_policy_id"]
    assert score_class(0.58) == expected["minimal_class_literal"]
    assert not set(expected["excluded_neutral_product_keys"]).intersection(landscape)
