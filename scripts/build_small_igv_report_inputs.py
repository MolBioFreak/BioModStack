#!/usr/bin/env python3
"""Build bounded IGV report inputs with governed artifact URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,255}$")
SESSION_MODES = {"primary", "dimer_candidates"}


def _safe_file(value: str | Path) -> Path:
    path = Path(value)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe or missing report artifact: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_url(job_id: str, mode: str, role: str, path: Path) -> str:
    return (
        f"/api/jobs/{quote(job_id, safe='')}/alignment-session-artifacts/"
        f"{mode}/{role}/{_sha256(path)}"
    )


def build_report_inputs(
    *,
    job_id: str,
    mode: str,
    reference_fasta: str | Path,
    reference_index: str | Path,
    alignment_bam: str | Path,
    alignment_bai: str | Path,
    coverage_depth: str | Path,
    position_gradient: str | Path,
    gc_content: str | Path,
    gc_zscore: str | Path,
    split_read_density: str | Path,
    soft_clip_density: str | Path,
    junction_hotspots: str | Path,
    out_track_config: str | Path,
    out_reference_config: str | Path,
) -> None:
    normalized_job_id = job_id.strip()
    if (
        not normalized_job_id
        or "/" in normalized_job_id
        or "\\" in normalized_job_id
        or ".." in normalized_job_id
        or SAFE_JOB_ID_RE.fullmatch(normalized_job_id) is None
    ):
        raise ValueError(f"unsafe job_id: {job_id!r}")
    if mode not in SESSION_MODES:
        raise ValueError(f"unsupported alignment session mode: {mode!r}")

    reference = _safe_file(reference_fasta)
    reference_fai = _safe_file(reference_index)
    files = {
        "alignment": _safe_file(alignment_bam),
        "alignment_index": _safe_file(alignment_bai),
        "coverage_depth": _safe_file(coverage_depth),
        "position_gradient": _safe_file(position_gradient),
        "gc_content": _safe_file(gc_content),
        "gc_zscore": _safe_file(gc_zscore),
        "split_read_density": _safe_file(split_read_density),
        "soft_clip_density": _safe_file(soft_clip_density),
        "junction_hotspots": _safe_file(junction_hotspots),
    }
    urls = {
        role: _artifact_url(normalized_job_id, mode, role, path)
        for role, path in files.items()
    }
    tracks = [
        {
            "name": "Aligned Reads",
            "type": "alignment",
            "format": "bam",
            "url": urls["alignment"],
            "indexURL": urls["alignment_index"],
            "showCoverage": True,
            "showSoftClips": True,
            "showMismatches": True,
            "showAllBases": True,
            "showInsertionText": True,
            "displayMode": "EXPANDED",
            "visibilityWindow": -1,
        },
        {
            "name": "Coverage Depth",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["coverage_depth"],
            "graphType": "bar",
            "autoscale": True,
            "color": "#4ea6ff",
        },
        {
            "name": "Position Gradient",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["position_gradient"],
            "graphType": "heatmap",
            "min": 0,
            "max": 1,
            "autoscale": False,
        },
        {
            "name": "GC Content (%)",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["gc_content"],
            "graphType": "line",
            "autoscale": True,
            "color": "#2ec27e",
        },
        {
            "name": "GC Z-score",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["gc_zscore"],
            "graphType": "line",
            "autoscale": True,
            "color": "#f6d32d",
        },
        {
            "name": "Split-read Density",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["split_read_density"],
            "graphType": "bar",
            "autoscale": True,
            "color": "#ff7800",
        },
        {
            "name": "Soft-clip Density",
            "type": "wig",
            "format": "bedgraph",
            "url": urls["soft_clip_density"],
            "graphType": "bar",
            "autoscale": True,
            "color": "#e01b24",
        },
        {
            "name": "Junction Hotspots",
            "type": "annotation",
            "format": "bed",
            "url": urls["junction_hotspots"],
            "displayMode": "EXPANDED",
            "color": "#ffbe6f",
        },
    ]

    reference_config = {
        "fastaURL": _artifact_url(normalized_job_id, mode, "reference", reference),
        "indexURL": _artifact_url(normalized_job_id, mode, "reference_index", reference_fai),
    }
    Path(out_track_config).write_text(json.dumps(tracks, indent=2) + "\n", encoding="utf-8")
    Path(out_reference_config).write_text(
        json.dumps(reference_config, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(SESSION_MODES))
    parser.add_argument("--reference-fasta", required=True)
    parser.add_argument("--reference-index", required=True)
    parser.add_argument("--alignment-bam", required=True)
    parser.add_argument("--alignment-bai", required=True)
    parser.add_argument("--coverage-depth", required=True)
    parser.add_argument("--position-gradient", required=True)
    parser.add_argument("--gc-content", required=True)
    parser.add_argument("--gc-zscore", required=True)
    parser.add_argument("--split-read-density", required=True)
    parser.add_argument("--soft-clip-density", required=True)
    parser.add_argument("--junction-hotspots", required=True)
    parser.add_argument("--out-track-config", required=True)
    parser.add_argument("--out-reference-config", required=True)
    return parser


def main() -> int:
    args = vars(_parser().parse_args())
    build_report_inputs(**args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
