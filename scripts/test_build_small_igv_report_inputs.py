from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT = Path(__file__).with_name("build_small_igv_report_inputs.py")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_small_igv_report_inputs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    reference = _write(tmp_path / "reference.fasta", b">plasmid\nACGTACGT\n")
    reference_index = _write(tmp_path / "reference.fasta.fai", b"plasmid\t8\t9\t8\t9\n")
    artifacts = {
        "alignment_bam": _write(tmp_path / "aligned.bam", b"BAM\x01alignment"),
        "alignment_bai": _write(tmp_path / "aligned.bam.bai", b"index"),
        "coverage_depth": _write(tmp_path / "coverage.bedgraph", b"plasmid\t0\t8\t12\n"),
        "position_gradient": _write(tmp_path / "gradient.bedgraph", b"plasmid\t0\t8\t0.5\n"),
        "gc_content": _write(tmp_path / "gc.bedgraph", b"plasmid\t0\t8\t50\n"),
        "gc_zscore": _write(tmp_path / "gcz.bedgraph", b"plasmid\t0\t8\t0\n"),
        "split_read_density": _write(tmp_path / "split.bedgraph", b"plasmid\t0\t8\t1\n"),
        "soft_clip_density": _write(tmp_path / "soft.bedgraph", b"plasmid\t0\t8\t2\n"),
        "junction_hotspots": _write(tmp_path / "hotspots.bed", b"plasmid\t0\t8\thotspot\n"),
    }
    return reference, reference_index, artifacts


def test_builds_digest_bound_governed_tracks_and_external_reference(tmp_path: Path) -> None:
    module = _load_module()
    reference, reference_index, artifacts = _inputs(tmp_path)
    track_config = tmp_path / "igv_track_config.json"
    standalone_track_config = tmp_path / "igv_standalone_track_config.json"
    reference_config = tmp_path / "igv_reference_config.json"

    module.build_report_inputs(
        job_id="job-123",
        mode="primary",
        reference_fasta=reference,
        reference_index=reference_index,
        out_track_config=track_config,
        out_standalone_track_config=standalone_track_config,
        out_reference_config=reference_config,
        **artifacts,
    )

    tracks = json.loads(track_config.read_text(encoding="utf-8"))
    standalone_tracks = json.loads(standalone_track_config.read_text(encoding="utf-8"))
    reference_urls = json.loads(reference_config.read_text(encoding="utf-8"))
    assert len(tracks) == 8
    assert len(standalone_tracks) == 8
    expected_roles = {
        "alignment": artifacts["alignment_bam"],
        "alignment_index": artifacts["alignment_bai"],
        "coverage_depth": artifacts["coverage_depth"],
        "position_gradient": artifacts["position_gradient"],
        "gc_content": artifacts["gc_content"],
        "gc_zscore": artifacts["gc_zscore"],
        "split_read_density": artifacts["split_read_density"],
        "soft_clip_density": artifacts["soft_clip_density"],
        "junction_hotspots": artifacts["junction_hotspots"],
    }
    serialized = json.dumps({"reference": reference_urls, "tracks": tracks})
    assert str(tmp_path) not in serialized
    assert "/api/files/" not in serialized
    assert "data:" not in serialized
    for role, path in expected_roles.items():
        expected_url = (
            f"/api/jobs/job-123/alignment-session-artifacts/primary/{role}/"
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}"
        )
        if role == "alignment_index":
            assert tracks[0]["indexURL"] == expected_url
        else:
            assert any(track.get("url") == expected_url for track in tracks)

    assert reference_urls == {
        "fastaURL": (
            "/api/jobs/job-123/alignment-session-artifacts/primary/reference/"
            + hashlib.sha256(reference.read_bytes()).hexdigest()
        ),
        "indexURL": (
            "/api/jobs/job-123/alignment-session-artifacts/primary/reference_index/"
            + hashlib.sha256(reference_index.read_bytes()).hexdigest()
        ),
    }
    assert standalone_tracks[0]["url"] == str(artifacts["alignment_bam"])
    assert standalone_tracks[0]["indexURL"] == str(artifacts["alignment_bai"])
    assert [track["url"] for track in standalone_tracks[1:]] == [
        str(artifacts[role])
        for role in (
            "coverage_depth",
            "position_gradient",
            "gc_content",
            "gc_zscore",
            "split_read_density",
            "soft_clip_density",
            "junction_hotspots",
        )
    ]


def test_materialized_nextflow_bam_and_bai_are_accepted(tmp_path: Path) -> None:
    module = _load_module()
    reference, reference_index, artifacts = _inputs(tmp_path)
    for key, staged_name in (
        ("alignment_bam", "source-aligned.bam"),
        ("alignment_bai", "source-aligned.bam.bai"),
    ):
        destination = artifacts[key]
        upstream = tmp_path / f"upstream-{destination.name}"
        destination.rename(upstream)
        staged = tmp_path / staged_name
        staged.symlink_to(upstream)
        shutil.copyfile(staged, destination)
        assert staged.is_symlink()
        assert destination.is_file() and not destination.is_symlink()

    module.build_report_inputs(
        job_id="job-nextflow-stage",
        mode="primary",
        reference_fasta=reference,
        reference_index=reference_index,
        out_track_config=tmp_path / "tracks.json",
        out_reference_config=tmp_path / "reference.json",
        **artifacts,
    )

    tracks = json.loads((tmp_path / "tracks.json").read_text(encoding="utf-8"))
    assert tracks[0]["url"].endswith(hashlib.sha256(artifacts["alignment_bam"].read_bytes()).hexdigest())
    assert tracks[0]["indexURL"].endswith(hashlib.sha256(artifacts["alignment_bai"].read_bytes()).hexdigest())


def test_rejects_unsafe_job_or_unknown_mode(tmp_path: Path) -> None:
    module = _load_module()
    reference, reference_index, artifacts = _inputs(tmp_path)
    common = {
        "reference_fasta": reference,
        "reference_index": reference_index,
        "out_track_config": tmp_path / "tracks.json",
        "out_reference_config": tmp_path / "reference.json",
        **artifacts,
    }

    with pytest.raises(ValueError, match="unsafe job_id"):
        module.build_report_inputs(job_id="../job", mode="primary", **common)
    with pytest.raises(ValueError, match="unsupported alignment session mode"):
        module.build_report_inputs(job_id="job-1", mode="other", **common)
