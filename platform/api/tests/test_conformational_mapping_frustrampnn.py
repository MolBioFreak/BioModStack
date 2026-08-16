from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import validate_schema
from services.conformational_mapping.frustrampnn_adapter import (
    normalize_cm_structure,
    project_cm_landscape,
)
from services.conformational_mapping.import_snapshot import build_import_snapshot_from_mmcif
from services.frustrampnn.analysis import (
    LandscapeValidationError,
    THRESHOLD_POLICY,
    finalize_landscape as finalize_neutral_landscape,
    score_class,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq"
SOURCE = FIXTURE_ROOT / "1UBQ.protein-only-authoritative.cif"
RAW = FIXTURE_ROOT / "frustrampnn.csv"
CANDIDATE_ID = "cm_imp_1ubq_real_000000_deadbeef"


def _neutral_map(tmp_path: Path) -> dict:
    payload = SOURCE.read_bytes()
    snapshot = build_import_snapshot_from_mmcif(
        payload,
        target_id="1ubq-real",
        candidate_id=CANDIDATE_ID,
        original_source_path="registered_import/1UBQ.cif",
    )
    return normalize_cm_structure(
        input_path=SOURCE,
        output_pdb_path=tmp_path / "normalized.pdb",
        map_path=tmp_path / "frustrampnn_structure_map_v1.json",
        authority_artifact_path=tmp_path / "producer_manifest_v1.json",
        target_id="1ubq-real",
        parent_job_id="cm-parent",
        candidate_id=CANDIDATE_ID,
        complex_snapshot=snapshot,
        selected_model=None,
        altloc_policy="blank_or_explicit:A",
        source_bytes=payload,
    )


def test_real_1ubq_replays_through_shared_neutral_core_and_thin_cm_projection(
    tmp_path: Path,
) -> None:
    neutral_map = _neutral_map(tmp_path)
    neutral = finalize_neutral_landscape(
        RAW,
        neutral_map,
        expected_normalized_pdb_sha256=neutral_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=neutral_map["model_ready_sequence_sha256"],
    )
    projected = project_cm_landscape(
        neutral,
        checkpoint_id="megascale.ckpt",
        checkpoint_sha256="a" * 64,
        tool_id="FrustraMPNN",
        tool_sha256="b" * 64,
        container_sha256="c" * 64,
    )

    assert hashlib.sha256(RAW.read_bytes()).hexdigest() == (
        "2084353640cbe5f06847bc78c0787f1062edb2c891d3808adfe2d6aa57b0fa36"
    )
    assert len(projected["residues"]) == 76
    assert all(len(residue["slots"]) == 20 for residue in projected["residues"])
    assert all(
        sum(slot["native"] is True for slot in residue["slots"]) == 1
        for residue in projected["residues"]
    )
    assert projected["input_issues"] == []
    # The CM v1 wire identity remains backward-readable while its hash is the
    # canonical neutral policy hash.  The adapter owns no numerical policy.
    assert projected["threshold_policy_id"] == "frustrampnn_class_v1"
    assert projected["threshold_policy_sha256"] == neutral["threshold_policy_sha256"]
    validate_schema("cm_frustration_landscape_v1", projected)


def test_shared_core_rejects_incomplete_cm_matrix_instead_of_degraded_success(
    tmp_path: Path,
) -> None:
    neutral_map = _neutral_map(tmp_path)
    lines = RAW.read_bytes().splitlines(keepends=True)
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_bytes(b"".join(lines[:-1]))

    with pytest.raises(LandscapeValidationError, match="matrix|missing|complete|rows"):
        finalize_neutral_landscape(
            incomplete,
            neutral_map,
            expected_normalized_pdb_sha256=neutral_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=neutral_map[
                "model_ready_sequence_sha256"
            ],
        )


def test_cm_threshold_semantics_are_owned_by_shared_canonical_policy() -> None:
    assert score_class(THRESHOLD_POLICY["high_max"]) == "high"
    assert score_class(THRESHOLD_POLICY["minimal_min"]) == "minimal"


def test_cm_projection_rejects_bound_but_noncanonical_threshold_policy(
    tmp_path: Path,
) -> None:
    neutral_map = _neutral_map(tmp_path)
    neutral = finalize_neutral_landscape(
        RAW,
        neutral_map,
        expected_normalized_pdb_sha256=neutral_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=neutral_map["model_ready_sequence_sha256"],
    )
    neutral["threshold_policy"] = {"id": "other_policy", "high_max": 0, "minimal_min": 1}
    from services.frustrampnn.contracts import canonical_sha256

    neutral["threshold_policy_sha256"] = canonical_sha256(neutral["threshold_policy"])

    with pytest.raises(ValueError, match="canonical threshold policy"):
        project_cm_landscape(
            neutral,
            checkpoint_id="megascale.ckpt",
            checkpoint_sha256="a" * 64,
            tool_id="FrustraMPNN",
            tool_sha256="b" * 64,
            container_sha256="c" * 64,
        )
