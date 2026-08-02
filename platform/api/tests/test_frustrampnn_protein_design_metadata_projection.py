from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECTOR = REPO_ROOT / "scripts" / "project_protein_design_metadata.py"
WORKFLOW = REPO_ROOT / "workflows" / "protein_design.nf"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from metadata_converter import MetadataConverter  # noqa: E402
from project_protein_design_metadata import ProjectionError, _merge_lineage  # noqa: E402
from services.frustrampnn.identity import deterministic_candidate_id  # noqa: E402
from services.result_ingester import _prevalidate_protein_design_metadata  # noqa: E402


def _terminal_manifest(
    *,
    job_id: str,
    producer_stage: str,
    producer_candidate_key: str,
    producer_output_key: str,
    description: str = "candidate",
    producer_rank: int | None = None,
) -> dict[str, object]:
    candidate_id = deterministic_candidate_id(
        parent_job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage=producer_stage,
        producer_candidate_key=producer_candidate_key,
    )
    return {
        "schema_name": "protein_design_terminal_candidate",
        "schema_version": 1,
        "candidate_id": candidate_id,
        "parent_job_id": job_id,
        "parent_workflow_id": "protein_design",
        "producer_stage": producer_stage,
        "producer_candidate_key": producer_candidate_key,
        "producer_method": "af2",
        "producer_sample": description,
        "producer_rank": producer_rank,
        "producer_output_key": producer_output_key,
        "producer_identity_sha256": "a" * 64,
        "producer_artifact_sha256": "b" * 64,
        "source_format": "pdb",
    }


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return path


def _run_projector(
    tmp_path: Path,
    csv_path: Path,
    manifests: list[Path],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PROJECTOR),
            "project-csv",
            "--metadata-csv",
            str(csv_path),
            "--output",
            str(tmp_path / "all_designs.csv"),
            *[
                argument
                for manifest in manifests
                for argument in ("--terminal-manifest", str(manifest))
            ],
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_combine_metadata_preserves_two_candidate_bound_rows_with_same_ordinary_ids(
    tmp_path: Path,
) -> None:
    first = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/identity-a/source.pdb",
        producer_output_key="producer-a/candidate.pdb",
    )
    second = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/identity-b/source.pdb",
        producer_output_key="producer-b/candidate.pdb",
    )
    fold = tmp_path / "fold.jsonl"
    fold.write_text("", encoding="utf-8")
    fold_seq = tmp_path / "fold-seq.jsonl"
    rows = []
    for index, manifest in enumerate((first, second), start=1):
        rows.append(
            {
                "description": "candidate",
                "fold_id": 7,
                "seq_id": 3,
                "pr_RoG": 10 + index,
                **manifest,
            }
        )
    fold_seq.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = tmp_path / "combined_metadata.csv"

    assert MetadataConverter().merge_all(str(fold), str(fold_seq), str(output))

    with output.open(newline="", encoding="utf-8") as handle:
        combined = list(csv.DictReader(handle))
    assert {row["candidate_id"] for row in combined} == {
        str(first["candidate_id"]),
        str(second["candidate_id"]),
    }
    assert [row["description"] for row in combined] == ["candidate", "candidate"]
    assert {row["pr_RoG"] for row in combined} == {"11", "12"}


def test_projector_joins_reordered_duplicate_basenames_by_candidate_id_and_feeds_ingester(
    tmp_path: Path,
) -> None:
    first = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/identity-a/source.pdb",
        producer_output_key="producer-a/candidate.pdb",
    )
    second = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/identity-b/source.pdb",
        producer_output_key="producer-b/candidate.pdb",
    )
    manifest_paths = [
        _write_manifest(tmp_path / "second.json", second),
        _write_manifest(tmp_path / "first.json", first),
    ]
    metadata_csv = tmp_path / "combined_metadata.csv"
    metadata_csv.write_text(
        "candidate_id,description,fold_id,seq_id,pr_plddt\n"
        f"{first['candidate_id']},candidate,7,3,91.25\n"
        f"{second['candidate_id']},candidate,7,3,88.50\n",
        encoding="utf-8",
    )

    completed = _run_projector(tmp_path, metadata_csv, manifest_paths)

    assert completed.returncode == 0, completed.stderr
    published = tmp_path / "all_designs.csv"
    with published.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["candidate_id"] for row in rows] == [
        str(first["candidate_id"]),
        str(second["candidate_id"]),
    ]
    assert [row["pr_plddt"] for row in rows] == ["91.25", "88.50"]
    assert [row["producer_output_key"] for row in rows] == [
        "producer-a/candidate.pdb",
        "producer-b/candidate.pdb",
    ]
    assert all(row["parent_workflow_id"] == "protein_design" for row in rows)

    result_dir = tmp_path / "job-output" / "results"
    result_dir.mkdir(parents=True)
    (result_dir / "all_designs.csv").write_bytes(published.read_bytes())
    keyed = _prevalidate_protein_design_metadata(
        tmp_path / "job-output",
        {str(first["candidate_id"]), str(second["candidate_id"])},
    )
    assert keyed[str(first["candidate_id"])]["pr_plddt"] == 91.25
    assert keyed[str(second["candidate_id"])]["producer_output_key"] == (
        "producer-b/candidate.pdb"
    )


def test_lineage_merge_canonicalizes_csv_rank_to_typed_manifest_integer() -> None:
    manifest = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:boltz_terminal",
        producer_candidate_key="frustrampnn/sources/boltz/sample/model_0.pdb",
        producer_output_key="sample/model_0.pdb",
        producer_rank=0,
    )

    merged = _merge_lineage(
        {field: "" if value is None else str(value) for field, value in manifest.items()},
        manifest,
    )

    assert merged["producer_rank"] == 0
    assert isinstance(merged["producer_rank"], int)
    assert merged["producer_sample"] == "candidate"


@pytest.mark.parametrize("ordinary_rank", [True, 0.0, "00", "+0", "-0", "0.0", "1e0"])
def test_lineage_merge_rejects_noncanonical_rank_authority(ordinary_rank: object) -> None:
    manifest = _terminal_manifest(
        job_id="job-production",
        producer_stage="protein_design:boltz_terminal",
        producer_candidate_key="frustrampnn/sources/boltz/sample/model_0.pdb",
        producer_output_key="sample/model_0.pdb",
        producer_rank=0,
    )

    with pytest.raises(ProjectionError, match="producer_rank is not canonical"):
        _merge_lineage({"producer_rank": ordinary_rank}, manifest)


@pytest.mark.parametrize(
    ("rows", "manifest_indexes", "message"),
    [
        ([(0, "first")], [0, 1], "candidate set is incomplete"),
        ([(0, "first"), (0, "duplicate")], [0], "duplicate candidate_id"),
        ([(0, "first"), (1, "unmatched")], [0], "unmatched candidate_id"),
    ],
)
def test_projector_rejects_missing_duplicate_and_unmatched_metadata(
    tmp_path: Path,
    rows: list[tuple[int, str]],
    manifest_indexes: list[int],
    message: str,
) -> None:
    manifests = [
        _terminal_manifest(
            job_id="job-production",
            producer_stage="protein_design:af2_terminal",
            producer_candidate_key=f"frustrampnn/sources/af2/identity-{index}/source.pdb",
            producer_output_key=f"producer-{index}/candidate.pdb",
        )
        for index in range(2)
    ]
    manifest_paths = [
        _write_manifest(tmp_path / f"manifest-{index}.json", manifests[index])
        for index in manifest_indexes
    ]
    csv_path = tmp_path / "combined_metadata.csv"
    csv_path.write_text(
        "candidate_id,description,pr_plddt\n"
        + "".join(
            f"{manifests[index]['candidate_id']},{description},90\n"
            for index, description in rows
        ),
        encoding="utf-8",
    )

    completed = _run_projector(tmp_path, csv_path, manifest_paths)

    assert completed.returncode == 2
    assert message in completed.stderr
    assert not (tmp_path / "all_designs.csv").exists()


def test_workflow_schedules_terminal_metadata_binding_and_projection_before_publication() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "process BindProteinDesignTerminalMetadata" in workflow
    assert "project_protein_design_metadata.py' bind-jsonl" in workflow
    assert "process ProjectProteinDesignMetadata" in workflow
    assert "project_protein_design_metadata.py' project-csv" in workflow
    assert "BindProteinDesignTerminalMetadata(terminal_designs)" in workflow
    assert "ProjectProteinDesignMetadata(" in workflow
    publish_call = workflow.split("PublishResults(", 1)[1].split(")", 1)[0]
    assert "projected_design_metadata" in publish_call
    assert "all_designs_metadata," not in publish_call
    assert workflow.index("ProjectProteinDesignMetadata(") < workflow.index(
        "if (params.run_frustrampnn == true)"
    )
    assert ".subscribe" not in workflow
