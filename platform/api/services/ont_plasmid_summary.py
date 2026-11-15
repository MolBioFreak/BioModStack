"""Plasmid result summary: which published files make up the result, and one zip of them.

Nothing here computes science. The summary numbers are written by the workflow
(scripts/build_plasmid_summary.py); this module only picks the already
published files out of the job's artifact catalog.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Iterable

SUMMARY_LEAF = "plasmid_summary/summary.json"
# Reads and alignments go into the zip only while the whole set stays under this.
ZIP_LARGE_FILE_LIMIT_BYTES = 2 * 1024 ** 3

# key, label, format, directory, leaf name or suffix, large
_FILES = (
    ("consensus", "Consensus", "FASTA", ("assembly/wf_clone_out",), ".final.fasta", False),
    ("consensus", "Consensus", "FASTA", ("fastq_qc",), "fastq_consensus.fasta", False),
    ("annotated_sequence", "Annotated sequence", "GenBank", ("assembly/wf_clone_out",), ".annotations.gbk", False),
    ("chromatogram", "Chromatogram", "AB1", ("assembly/adapter", "fastq_qc"), "consensus_pileup.ab1", False),
    ("per_base_table", "Per-base table", "TSV", ("assembly/adapter", "fastq_qc"), "per_base_support.tsv", False),
    ("assembly_report", "Assembly report", "HTML", ("assembly/wf_clone_out",), "wf-clone-validation-report.html", False),
    ("coverage_plot", "Coverage plot", "SVG", ("assembly/adapter/plasmid_summary", "fastq_qc/plasmid_summary"), "coverage.svg", False),
    ("read_length_histogram", "Read-length histogram", "SVG", ("assembly/adapter/plasmid_summary", "fastq_qc/plasmid_summary"), "read_length_histogram.svg", False),
    ("virtual_gel", "Virtual gel", "SVG", ("assembly/adapter/plasmid_summary", "fastq_qc/plasmid_summary"), "virtual_gel.svg", False),
    ("sample_summary", "Sample summary", "TXT", ("assembly/adapter/plasmid_summary", "fastq_qc/plasmid_summary"), "sample_summary.txt", False),
    ("run_summary", "Run summary", "CSV", ("assembly/adapter/plasmid_summary", "fastq_qc/plasmid_summary"), "run_summary.csv", False),
    ("host_composition", "Sample composition", "JSON", ("assembly/adapter/plasmid_summary",), "host_composition.json", False),
    ("alignments", "Alignments", "BAM", ("align", "fastq_qc"), "aligned.bam", True),
    ("alignments_index", "Alignments index", "BAI", ("align", "fastq_qc"), "aligned.bam.bai", True),
)


def _present(artifacts: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["relative_path"]): item for item in artifacts
            if item.get("state") == "present" and item.get("relative_path") and item.get("artifact_id")}


def select_summary_artifact(artifacts: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The workflow's summary document; the assembly's own summary wins over the read-QC one."""
    by_path = _present(artifacts)
    for directory in ("assembly/adapter", "fastq_qc"):
        found = by_path.get(f"{directory}/{SUMMARY_LEAF}")
        if found is not None:
            return found
    return None


def select_host_composition_artifact(artifacts: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The host share of the read population, present only when the run was given a host reference."""
    return _present(artifacts).get("assembly/adapter/plasmid_summary/host_composition.json")


def select_summary_files(artifacts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Published files of the plasmid result, one per kind, in display order."""
    by_path = _present(artifacts)
    chosen: dict[str, dict[str, Any]] = {}
    for key, label, file_format, directories, leaf, large in _FILES:
        if key in chosen:
            continue
        for directory in directories:
            if leaf.startswith("."):
                matches = sorted(path for path in by_path
                                 if path.startswith(directory + "/") and path.endswith(leaf)
                                 and "/" not in path[len(directory) + 1:])
                path = matches[0] if len(matches) == 1 else None
            else:
                path = f"{directory}/{leaf}" if f"{directory}/{leaf}" in by_path else None
            if path is None:
                continue
            item = by_path[path]
            chosen[key] = {
                "key": key, "label": label, "format": file_format, "large": large,
                "artifact_id": item["artifact_id"], "url": item.get("url"),
                "size_bytes": item.get("size_bytes"), "filename": Path(path).name, "relative_path": path,
            }
            break
    return list(chosen.values())


def plan_zip(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into what goes in the zip and what is left as a separate download, and say which."""
    total = sum(int(item.get("size_bytes") or 0) for item in files)
    if total <= ZIP_LARGE_FILE_LIMIT_BYTES:
        return files, []
    return [item for item in files if not item["large"]], [item for item in files if item["large"]]


def write_zip(destination: Path, members: list[tuple[str, Path]], left_out: list[dict[str, Any]], sample: str) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for name, path in members:
            # BAM and its index are already compressed.
            method = zipfile.ZIP_STORED if name.endswith((".bam", ".bai")) else zipfile.ZIP_DEFLATED
            archive.write(path, arcname=f"{sample}/{name}", compress_type=method)
        if left_out:
            lines = ["These files are part of the result but are not in this zip because the set is over 2 GB.",
                     "Download them from the result page.", ""]
            lines += [f"{item['label']} ({item['format']}): {item['filename']}, {int(item.get('size_bytes') or 0):,} bytes"
                      for item in left_out]
            archive.writestr(f"{sample}/NOT_IN_THIS_ZIP.txt", "\n".join(lines) + "\n")
