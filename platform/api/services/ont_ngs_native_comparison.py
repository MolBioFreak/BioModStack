"""Pure comparison-panel completion, bound to the selected launch receipt.

The existing producer owns score-margin classification. We feed it decoded SAM
records rather than running samtools or rebuilding scientific products here.
"""
from collections import Counter
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import runpy
import uuid

import pysam

from paths import get_inputs_dir
from services import ngs_alignment_sessions as files
from services.molbio_ngs_workup import _panel_binding
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ont_ngs_native_completion import _document, _identity, _require
from services.ont_ngs_native_plasmid import _artifact, _stage


_OUTPUTS = (
    "comparison_panel_summary.json", "comparison_panel.bam", "comparison_panel.bam.bai",
    "comparison_panel.fasta", "comparison_panel_expected_reference.fasta",
    "comparison_panel_source.fastq", "comparison_panel_normalized.fastq",
    "comparison_panel_occurrence_map.json",
)


def validate_comparison(root, persisted, job):
    params = job.params
    binding = _panel_binding(params)
    _require(str(uuid.UUID(binding["panel_id"])) == binding["panel_id"], "invalid selected panel id")
    selected = Path(params["comparison_panel_snapshot"])
    _require(selected.is_absolute(), "comparison selection must be server-staged")
    snapshot, snapshot_sha = _document(selected)
    # The launch binding contains the consumed receipt manifest digest, not the
    # staging snapshot digest. Authenticate the former before trusting entries.
    approved_path = (get_inputs_dir() / "approved_ngs_comparison_panels" /
                     binding["panel_id"] / ("v" + str(binding["panel_version"])) / "manifest.json")
    approved, approved_sha = _document(approved_path)
    _require(approved_sha == binding["panel_snapshot_sha256"]
             and approved["schema"] == "bms.ngs.approved-comparison-panel.v1"
             and approved["status"] == "APPROVED" and approved["panel_id"] == binding["panel_id"]
             and approved["version"] == binding["panel_version"], "selected comparison receipt manifest mismatch")
    entries = []
    for item in approved["entries"]:
        filename = item["fasta_filename"]
        _require(isinstance(filename, str) and Path(filename).name == filename
                 and filename not in {"", ".", ".."}, "unsafe selected panel FASTA")
        entries.append({key: item[key] for key in ("id", "label", "role", "fasta_sha256")} | {"fasta_path": filename})
    _require(snapshot == {"schema": "bms.ngs.comparison-panel.v1", "panel_id": binding["panel_id"],
             "panel_version": binding["panel_version"], "panel_manifest_sha256": approved_sha, "entries": entries},
             "staged comparison panel differs from selected receipt")
    producer = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/build_comparison_panel_attribution.py"))
    selected_ids = {selected: _identity(selected), approved_path: _identity(approved_path)}
    _require(selected_ids[selected][0] == snapshot_sha and selected_ids[approved_path][0] == approved_sha,
             "selected comparison documents changed while parsing")
    for entry in entries:
        path = selected.parent / entry["fasta_path"]
        selected_ids[path] = _identity(path)
        _require(selected_ids[path][0] == entry["fasta_sha256"], "selected comparison FASTA digest mismatch")
    panel = producer["load_panel_snapshot"](selected)
    _stage(root, persisted, job, "comparison_panel", tuple("comparison_panel/" + name for name in _OUTPUTS[:3]))
    artifacts = [_artifact(root, "comparison_panel/" + name) for name in _OUTPUTS]
    ids = {Path(item["path"]).name: (item["sha256"], item["size_bytes"]) for item in artifacts}
    directory = root / "comparison_panel"
    summary, summary_sha = _document(directory / _OUTPUTS[0])
    code = DORADO_LOCK_PATH.parents[2]
    execution = summary["execution"]
    _require(execution["executed_sources"] == {
        name: dict(zip(("sha256", "size_bytes"), _identity(code / name))) for name in
        ("scripts/build_comparison_panel_attribution.py", "modules/ngs/comparison_panel_attribution.nf")},
        "comparison executing source differs from completion parser")
    _require(execution["minimap2_preset"] == params.get("fastq_minimap2_preset", "map-ont")
             and set(execution["tools"]) == {"samtools", "minimap2"}, "comparison runtime/setting inventory mismatch")
    for tool in execution["tools"].values():
        _require(Path(tool["path"]).is_absolute() and isinstance(tool["sha256"], str)
                 and len(tool["sha256"]) == 64 and all(c in "0123456789abcdef" for c in tool["sha256"])
                 and type(tool["size_bytes"]) is int and tool["size_bytes"] > 0,
                 "comparison executed binary identity malformed")
    _require(summary_sha == ids[_OUTPUTS[0]][0] and summary["schema"] == producer["SUMMARY_SCHEMA"]
             and summary["status"] == "review_required" and summary["panel"] == producer["_panel_summary"](panel),
             "comparison summary selection/schema mismatch")
    threshold, margin = params.get("comparison_panel_min_mapq", 20), params.get("comparison_panel_min_score_margin", 10)
    _require(type(threshold) is int and 0 <= threshold <= 60 and type(margin) is int and margin >= 0
             and summary["min_mapq"] == threshold and summary["min_score_margin"] == margin,
             "comparison effective thresholds mismatch")
    source_path = Path(params["fastq_path"]) if params["ont_input_mode"] == "fastq" else root / "fastq_qc/reads_for_qc.fastq"
    source_id = _identity(source_path)
    _require(ids["comparison_panel_source.fastq"] == source_id
             and ids["comparison_panel_expected_reference.fasta"] == _identity(Path(params["reference_fasta"])),
             "comparison source/reference copy differs from native inputs")
    revision = params["molbio_revision_binding"]
    _require(revision["reference_snapshot_sha256"] == ids["comparison_panel_expected_reference.fasta"][0],
             "comparison intended reference differs from selected molecular receipt")
    for key, filename in (("source_fastq", "comparison_panel_source.fastq"),
                          ("normalized_fastq", "comparison_panel_normalized.fastq"),
                          ("occurrence_map", "comparison_panel_occurrence_map.json")):
        _require(summary[key] == {"path": filename, "sha256": ids[filename][0], "size_bytes": ids[filename][1]},
                 "comparison source descriptor mismatch")
    _require(summary["source_fastq_sha256"] == source_id[0]
             and summary["occurrence_map_sha256"] == ids["comparison_panel_occurrence_map.json"][0],
             "comparison occurrence/source digest mismatch")
    ref_sha, ref_size = ids["comparison_panel_expected_reference.fasta"]
    _require(summary["reference"] == {"id": "expected_plasmid", "role": "intended",
             "path": "comparison_panel_expected_reference.fasta", "source_file_sha256": ref_sha,
             "sha256": ref_sha, "size_bytes": ref_size}, "comparison reference descriptor mismatch")
    descriptors = [{"kind": kind, "path": filename, "sha256": ids[filename][0], "size_bytes": ids[filename][1]}
                   for kind, filename in zip(producer["ARTIFACT_KINDS"],
                       ("comparison_panel.bam", "comparison_panel.bam.bai", "comparison_panel_occurrence_map.json"))]
    _require(summary["artifacts"] == descriptors and summary["category_closure"] == list(producer["CATEGORY_KEYS"]),
             "comparison artifact/category denominator mismatch")
    occurrence, _ = _document(directory / "comparison_panel_occurrence_map.json")
    original = producer["_read_fastq_records"](source_path)
    normalized = producer["_read_fastq_records"](directory / "comparison_panel_normalized.fastq")
    expected_occurrences = [{"occurrence_id": producer["_occurrence_id"](ordinal), "read_id": record["read_id"], "ordinal": ordinal}
                            for ordinal, record in enumerate(original, 1)]
    _require(occurrence["occurrences"] == expected_occurrences and occurrence["source_fastq_size_bytes"] == source_id[1]
             and normalized == [dict(record, read_id=row["occurrence_id"]) for record, row in zip(original, expected_occurrences)],
             "comparison normalized sequence/quality/occurrence mismatch")
    _, expected_sequence = producer["_single_fasta_record"](Path(params["reference_fasta"]))
    sequences = {"expected_plasmid": expected_sequence}
    for entry in panel["entries"]:
        sequences["panel__" + entry["id"]] = producer["_single_fasta_record"](Path(entry["fasta_path"]))[1]
    with files._open_regular_file_no_symlinks(directory / "comparison_panel.fasta") as handle:
        _require(handle.read() == "".join(">" + name + "\n" + sequence + "\n" for name, sequence in sequences.items()).encode("utf-8"),
                 "comparison combined reference differs from selected sequences")
    with ExitStack() as stack:
        handles = {name: stack.enter_context(files._open_regular_file_no_symlinks(directory / name))
                   for name in ("comparison_panel.bam", "comparison_panel.bam.bai")}
        fd = lambda name: "/proc/self/fd/" + str(handles[name].fileno())
        with pysam.AlignmentFile(fd("comparison_panel.bam"), "rb", index_filename=fd("comparison_panel.bam.bai"), require_index=True) as bam:
            _require(bam.is_bam and bam.check_index() and tuple(bam.references) == tuple(sequences)
                     and tuple(bam.lengths) == tuple(len(value) for value in sequences.values()), "comparison BAM dictionary/index mismatch")
            sequential, primary, index_counts = Counter(), Counter(), Counter()
            no_coordinate = 0
            def records():
                nonlocal no_coordinate
                for read in bam.fetch(until_eof=True):
                    if read.reference_id < 0:
                        no_coordinate += 1
                    else:
                        index_counts[(read.reference_name, read.is_unmapped)] += 1
                    if not read.is_unmapped:
                        _require(read.reference_end is not None and 0 <= read.reference_start < read.reference_end <= len(sequences[read.reference_name]),
                                 "comparison BAM coordinates invalid")
                        sequential[read.to_string()] += 1
                    if not read.is_secondary and not read.is_supplementary:
                        qualities = read.get_forward_qualities()
                        primary[(read.query_name, read.get_forward_sequence(), tuple(qualities) if qualities is not None else None)] += 1
                    yield read.to_string()
            evidence = producer["_summarize_sam"]("", directory / "comparison_panel.bam", threshold, margin, panel,
                directory / "comparison_panel_normalized.fastq", directory / "comparison_panel_occurrence_map.json",
                source_fastq_sha256=source_id[0], sam_lines=records())
            _require(primary == Counter((record["read_id"], record["sequence"].upper(), tuple(ord(char) - 33 for char in record["quality"])) for record in normalized),
                     "comparison BAM primary sequence/quality inventory mismatch")
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name) if not read.is_unmapped)
            _require(indexed == sequential and bam.nocoordinate == no_coordinate and all(
                item.mapped == index_counts[(item.contig, False)] and item.unmapped == index_counts[(item.contig, True)]
                for item in bam.get_index_statistics()), "comparison BAM/index mismatch")
    for key in ("input_read_count", "classified_read_count", "categories", "role_counts", "reference_counts"):
        _require(summary[key] == evidence[key], "comparison attribution count differs from native BAM")
    _require(summary["reads"] == evidence["rows"], "comparison attribution differs from producer score-margin semantics")
    for path, identity in selected_ids.items():
        _require(_identity(path) == identity, "selected comparison authority changed")
    _require(_identity(source_path) == source_id, "comparison source changed")
    for item in artifacts:
        _require(_identity(root / item["path"]) == (item["sha256"], item["size_bytes"]), "comparison output changed")
    return {"state": "validated", "status": "review_required", "receipt_id": binding["receipt_id"],
            "panel_id": binding["panel_id"], "panel_version": binding["panel_version"],
            "panel_snapshot_sha256": approved_sha, "selected_snapshot_sha256": snapshot_sha,
            "input_read_count": evidence["input_read_count"], "categories": evidence["categories"], "artifacts": artifacts}
