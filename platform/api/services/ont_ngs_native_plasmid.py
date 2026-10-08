"""Pure native alignment/QC barriers for plasmid and construct screening.

No catalog, preview, signal store or subprocess is used here. The caller owns
terminal CAS publication; the shared clone parser owns assembly/adapter products.
"""
from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re

import pysam
import rfc8785

from services import ngs_alignment_sessions as files
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_completion import OntNgsCompletionError, _REQUIRED_STAGE_OUTPUT_SUFFIXES, _read_manifest
from services.ont_ngs_native_completion import (
    _digest, _document, _identity, _require, _resolve_terminal_output, _validate_producer,
    _validate_basecall, _validate_native_reference, _validate_reference_alignment,
)
from services.ont_ngs_native_external_bam import validate_external_bam, validate_realigned_external_bam
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.sequence_qc_manifest import SequenceQcManifestError, load_sequence_qc_manifest


def _stage(root, persisted, job, name, expected):
    stage = job.provenance["stage_terminal_states"][name]
    _require(stage["status"] == "complete" and isinstance(stage["outputs"], list)
             and tuple(_resolve_terminal_output(value, root, persisted, stage=name)[1]
                       for value in stage["outputs"]) == tuple(expected),
             f"native {name} terminal output contract mismatch")


def _producer_log(root, relative, sources, tools):
    with files._open_regular_file_no_symlinks(root / relative) as handle:
        identity = (_digest(handle), os.fstat(handle.fileno()).st_size)
        _validate_producer(handle.read().decode("utf-8"), sources, tools)
        _require((_digest(handle), os.fstat(handle.fileno()).st_size) == identity
                 and _identity(root / relative) == identity, "producer log changed during validation")
    return {"path": relative, "sha256": identity[0], "size_bytes": identity[1]}


def _fastq(path):
    with files._open_regular_file_no_symlinks(path) as handle:
        identity = (_digest(handle), os.fstat(handle.fileno()).st_size)
        records = Counter()
        with pysam.FastxFile(f"/proc/self/fd/{handle.fileno()}", persist=False) as fastq:
            for read in fastq:
                _require(bool(read.name) and bool(read.sequence) and read.quality is not None
                         and len(read.sequence) == len(read.quality), "malformed native FASTQ record")
                records[(read.name, read.sequence.upper())] += 1
        _require((_digest(handle), os.fstat(handle.fileno()).st_size) == identity
                 and _identity(path) == identity, "native FASTQ changed while reading")
    return records, identity


def _table(path, columns=()):
    with files._open_regular_file_no_symlinks(path) as handle:
        raw = handle.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw), delimiter="\t")
    header = reader.fieldnames
    _require(header and len(set(header)) == len(header) and all(header)
             and set(columns) <= set(header), "native table has invalid columns")
    rows = list(reader)
    _require(all(None not in row and all(value is not None for value in row.values()) for row in rows),
             "native table has a truncated or over-wide row")
    return rows


def _metrics(path):
    rows = _table(path, ("metric", "value"))
    result = {row["metric"]: row["value"] for row in rows}
    _require(len(result) == len(rows), "native metrics contain duplicate keys")
    return result


def _artifact(root, relative):
    digest, size = _identity(root / relative)
    return {"path": relative, "sha256": digest, "size_bytes": size}


def validate_fastq_alignment(root, persisted, job):
    """Minimap2 baseline: source occurrences, reference, index, settings receipt."""
    names = _REQUIRED_STAGE_OUTPUT_SUFFIXES["fastq_align"]
    _stage(root, persisted, job, "fastq_align", names)
    params = job.params
    source = Path(params["fastq_path"])
    _require(source.is_absolute(), "native FASTQ source must be absolute")
    expected, source_identity = _fastq(source)
    preset = params.get("fastq_minimap2_preset", "map-ont")
    secondary = params.get("fastq_minimap2_allow_secondary", False)
    _require(preset in {"map-ont", "map-hifi", "map-pb", "sr"} and type(secondary) is bool,
             "invalid native minimap2 settings")
    with ExitStack() as stack:
        handles = {name: stack.enter_context(files._open_regular_file_no_symlinks(root / name)) for name in names}
        ids = {name: (_digest(handle), os.fstat(handle.fileno()).st_size) for name, handle in handles.items()}
        contigs, _ = _validate_native_reference(handles, params, ids["align/reference.fasta"])
        receipt_expected = {
            "source_sha256_before": source_identity[0], "source_sha256_after": source_identity[0],
            "source_immutable": "true", "reference_immutable": "true",
            "reference_raw_sha256_before": ids["align/reference.fasta"][0],
            "reference_raw_sha256_after": ids["align/reference.fasta"][0],
            "reference_sequence_sha256": params["reference_sequence_sha256"],
            "fastq_minimap2_preset": preset, "fastq_minimap2_allow_secondary": str(secondary).lower(),
        }
        receipt = {}
        log_text = handles["align/fastq_align.log"].read().decode("utf-8")
        _validate_producer(log_text, ("modules/ngs/fastq_align.nf",), ("minimap2", "samtools"))
        for line in log_text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in receipt_expected:
                _require(key not in receipt, "duplicate native FASTQ alignment receipt field")
                receipt[key] = value
        _require(receipt == receipt_expected, "native FASTQ alignment source/settings receipt mismatch")
        fd = lambda name: f"/proc/self/fd/{handles[name].fileno()}"
        primary, sequential, index_counts = Counter(), Counter(), Counter()
        count = mapped = no_coordinate = 0
        with pysam.AlignmentFile(fd("align/aligned.bam"), "rb", index_filename=fd("align/aligned.bam.bai"), require_index=True) as bam:
            sq = bam.header.to_dict().get("SQ", [])
            _require(bam.is_bam and bam.check_index() and len(sq) == len(contigs)
                     and {item["SN"] for item in sq} == set(contigs) and all(
                         item["LN"] == contigs[item["SN"]][0]
                         and (not item.get("M5") or item["M5"].lower() == contigs[item["SN"]][1]) for item in sq),
                     "native FASTQ alignment/reference dictionary mismatch")
            source_names = {name for name, _ in expected}
            for read in bam.fetch(until_eof=True):
                count += 1
                _require(read.query_name in source_names and (secondary or not read.is_secondary),
                         "foreign FASTQ alignment or unrequested secondary record")
                if not read.is_secondary and not read.is_supplementary:
                    primary[(read.query_name, (read.get_forward_sequence() or "").upper())] += 1
                if read.reference_id < 0:
                    no_coordinate += 1
                else:
                    index_counts[(read.reference_name, read.is_unmapped)] += 1
                if not read.is_unmapped:
                    _require(read.reference_start >= 0 and read.reference_end is not None
                             and read.reference_end <= bam.lengths[read.reference_id], "native FASTQ alignment coordinates invalid")
                    sequential[read.to_string()] += 1
                    mapped += 1
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name) if not read.is_unmapped)
            _require(indexed == sequential and bam.nocoordinate == no_coordinate and all(
                item.mapped == index_counts[(item.contig, False)] and item.unmapped == index_counts[(item.contig, True)]
                for item in bam.get_index_statistics()), "native FASTQ BAM index disagrees with decoded records")
        _require(primary == expected, "native FASTQ primary occurrence inventory differs from source")
        for name, handle in handles.items():
            _require((_digest(handle), os.fstat(handle.fileno()).st_size) == ids[name]
                     and _identity(root / name) == ids[name], "native FASTQ alignment changed while reading")
    return {"state": "validated", "partial": False, "workflow_id": params["ont_workflow_id"],
            "input_mode": "fastq", "read_count": sum(expected.values()),
            "source_fastq_sha256": source_identity[0],
            "alignment": {"record_count": count, "mapped_records": mapped,
                          "reference_sequence_sha256": params["reference_sequence_sha256"]},
            "artifacts": [{"path": name, "sha256": ids[name][0], "size_bytes": ids[name][1]} for name in names]}, source_identity


def _manifest(root, relative, job=None):
    raw, digest = _read_manifest(root / relative)
    # The shared parser retains scientific FAIL/REVIEW separately from byte integrity.
    doc = load_sequence_qc_manifest(root / relative, raw_bytes=raw, **({
        "expected_job_id": str(job.id), "expected_workflow_id": job.params["ont_workflow_id"],
        "expected_input_mode": job.params["ont_input_mode"], "expected_analysis_status": "completed",
    } if job is not None else {}))
    _require("MALFORMED_VERIFICATION_MANIFEST" not in doc.get("reason_codes", []), "malformed native verification manifest")
    artifacts = [_artifact(root, relative)]
    _require(artifacts[0]["sha256"] == digest, "native manifest changed during parsing")
    declared = set()
    for item in doc["artifacts"]:
        if item["state"] != "present":
            _require(not item["required"], "required native manifest artifact is unavailable")
            continue
        _require(item["integrity_valid"] is True, "native manifest artifact integrity mismatch")
        path = Path(item["path"])
        _require(not path.is_absolute() and ".." not in path.parts and path.parts, "unsafe native manifest artifact path")
        name = (Path(relative).parent / path).as_posix()
        _require(name not in declared, "duplicate native manifest artifact path")
        declared.add(name)
        artifact = _artifact(root, name)
        _require((artifact["sha256"], artifact["size_bytes"]) == (item["declared_sha256"], item["declared_size_bytes"]),
                 "native artifact changed after manifest parsing")
        artifacts.append(artifact)
    return doc, artifacts



# Exact regular-file outputs declared by FastqPlasmidQC; the verification-input
# directory is handled through its source-owned observed-state manifest below.
_QC_FILES = (
    "aligned.bam", "aligned.bam.bai", "read_lengths.tsv", "fastq_qc_summary.tsv",
    "fastq_alignment_stats.tsv", "fastq_coverage.tsv", "per_base_support.tsv", "qc_manifest.json",
    "reference_qc.fasta", "reference_qc.fasta.fai", "igv_coverage_depth.bedgraph",
    "igv_position_gradient.bedgraph", "igv_gc_content.bedgraph", "igv_gc_zscore.bedgraph",
    "igv_split_read_density.bedgraph", "igv_softclip_density.bedgraph", "igv_junction_hotspots.bed",
    "igv_report_sites.bed", "igv_report_sites.tsv", "igv_track_config.json", "igv_report.html",
    "igv_report.log", "fastq_consensus.fasta", "fastq_consensus.fasta.fai", "fastq_consensus.log", "fastq_qc.log",
)


def _indexed_fasta(root, fasta_relative, index_relative):
    with files._open_regular_file_no_symlinks(root / fasta_relative) as fasta_handle, files._open_regular_file_no_symlinks(root / index_relative) as index_handle:
        contigs, sequence = files._fasta_contigs_from_handle(fasta_handle)
        _require(len(contigs) == 1 and bool(sequence), "native indexed FASTA is not a single nonempty record")
        with pysam.FastaFile(f"/proc/self/fd/{fasta_handle.fileno()}", filepath_index=f"/proc/self/fd/{index_handle.fileno()}") as fasta:
            _require(tuple(fasta.references) == tuple(contigs) and all(
                fasta.get_reference_length(name) == length and
                hashlib.md5(fasta.fetch(name).upper().encode("ascii"), usedforsecurity=False).hexdigest() == md5
                for name, (length, md5) in contigs.items()), "native FASTA/index disagreement")
    return contigs, sequence


def _qc_formats(root, reference_name, reference_length):
    for filename in _QC_FILES:
        path = root / "fastq_qc" / filename
        if filename.endswith((".bedgraph", ".bed")):
            with files._open_regular_file_no_symlinks(path) as handle:
                for line in handle.read().decode("utf-8").splitlines():
                    fields = line.split("\t")
                    _require(len(fields) == (4 if filename.endswith(".bedgraph") else 6)
                             and fields[0] == reference_name and 0 <= int(fields[1]) < int(fields[2]) <= reference_length,
                             "native QC track coordinates/row width invalid")
                    if filename.endswith(".bedgraph"):
                        _require(math.isfinite(float(fields[3])), "native QC track has nonfinite value")
        elif filename == "igv_track_config.json":
            with files._open_regular_file_no_symlinks(path) as handle:
                tracks = json.load(handle)
            _require(isinstance(tracks, list) and len(tracks) == 8
                     and all(isinstance(track, dict) and isinstance(track.get("url"), str) for track in tracks),
                     "native QC track configuration is malformed")
        elif filename == "igv_report.html":
            with files._open_regular_file_no_symlinks(path) as handle:
                html = handle.read().decode("utf-8").lower()
            _require("<html" in html and "</html>" in html, "native QC report is malformed HTML")
    _table(root / "fastq_qc/igv_report_sites.tsv")
    _indexed_fasta(root, "fastq_qc/reference_qc.fasta", "fastq_qc/reference_qc.fasta.fai")
    _, consensus = _indexed_fasta(root, "fastq_qc/fastq_consensus.fasta", "fastq_qc/fastq_consensus.fasta.fai")
    _require(any(base in consensus for base in b"ACGT"), "native QC producer requires a called consensus base")


def validate_qc(root, persisted, job, alignment_result, *, verify=True):
    """Source-owned FastqPlasmidQC + canonical multimers + ConstructVerify."""
    params = job.params
    mode = params["ont_input_mode"]
    expected_stage = list(_REQUIRED_STAGE_OUTPUT_SUFFIXES["fastq_qc"])
    if params["ont_workflow_id"] == "ont_plasmid_qc":
        if mode != "fastq":
            expected_stage = ["fastq_qc/reads_for_qc.fastq", *expected_stage[1:3], *expected_stage[4:]]
        expected_stage.append("multimer_qc/dimer_breakpoint_call.tsv")
    _stage(root, persisted, job, "fastq_qc", expected_stage)
    for stage in (("dimer_qc", "construct_verification") if verify else ("dimer_qc",)):
        _stage(root, persisted, job, stage, _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage])
    qc, artifacts = _manifest(root, "fastq_qc/qc_manifest.json", job)
    _producer_log(root, "fastq_qc/fastq_qc.log", ('modules/ngs/fastq_plasmid_qc.nf', 'scripts/build_fastq_igv_tracks.py', 'scripts/build_fastq_support_tables.py', 'scripts/build_small_igv_report_inputs.py', 'scripts/validate_standalone_igv_report.py', 'scripts/build_construct_verification_input.py', 'scripts/build_sequence_qc_manifest.py'), ("python3", "samtools", "create_report"))
    artifacts.append(_producer_log(root, "multimer_qc/dimer_producer.log",
                     ("modules/ngs/fastq_dimer_qc.nf", "scripts/build_dimer_canonical_outputs.py"), ("python3",)))
    artifacts.append(_producer_log(root, "multimer_qc/dimer_analysis.log",
                     ('modules/ngs/fastq_dimer_qc.nf', 'scripts/init_fastq_dimer_outputs.sh', 'scripts/dimer_single_ref_split_events.awk', 'scripts/dominant_dimer_consensus.sh', 'scripts/build_alignment_session_manifest.sh'), ("samtools", "minimap2", "awk")))
    artifacts.extend(_artifact(root, "fastq_qc/" + filename) for filename in _QC_FILES)
    ids = {item["path"]: (item["sha256"], item["size_bytes"]) for item in alignment_result["artifacts"] + artifacts}
    for qc_name, align_name in (("aligned.bam", "aligned.bam"), ("aligned.bam.bai", "aligned.bam.bai"), ("reference_qc.fasta", "reference.fasta")):
        _require(ids.get("fastq_qc/" + qc_name) == ids["align/" + align_name], "QC copies differ from native alignment/reference")
    reference = qc["reference"]
    _require(reference["expected_sha256"] == params["reference_sequence_sha256"], "QC reference differs from request")
    with files._open_regular_file_no_symlinks(root / "align/reference.fasta") as handle:
        contigs, sequence = files._fasta_contigs_from_handle(handle)
    name = next(iter(contigs))
    length = len(sequence)
    _qc_formats(root, name, length)
    _require(reference["name"] == name and reference["length"] == length, "QC reference name/length mismatch")
    reads_path = Path(params["fastq_path"]) if mode == "fastq" else root / "fastq_qc/reads_for_qc.fastq"
    reads, reads_identity = _fastq(reads_path)
    if mode != "fastq":
        _producer_log(root, "fastq_qc/bam_to_fastq_for_qc.log", ("modules/ngs/bam_prepare.nf",), ("samtools",))
        # samtools fastq excludes secondary/supplementary by default. The QC
        # producer itself requires one FASTQ occurrence per primary BAM record.
        with files._open_regular_file_no_symlinks(root / "align/aligned.bam") as handle:
            with pysam.AlignmentFile(handle, "rb") as bam:
                primary = Counter((read.query_name, (read.get_forward_sequence() or "").upper())
                                  for read in bam.fetch(until_eof=True) if not read.is_secondary and not read.is_supplementary)
        _require(reads == primary, "BAM-to-QC FASTQ inventory differs from primary alignments")
        artifacts.extend(_artifact(root, relative) for relative in ("fastq_qc/reads_for_qc.fastq", "fastq_qc/bam_to_fastq_for_qc.log"))
    stats = _metrics(root / "fastq_qc/fastq_alignment_stats.tsv")
    settings = {"expected_plasmid_size": params.get("expected_plasmid_size", 7000),
                "min_fastq_read_length": params.get("min_fastq_read_length", 0),
                "fastq_minimap2_preset": params.get("fastq_minimap2_preset", "map-ont"),
                "fastq_minimap2_allow_secondary": str(params.get("fastq_minimap2_allow_secondary", False)).lower(),
                "igv_track_window_bp": params.get("igv_track_window_bp", 100),
                "igv_report_max_sites": params.get("igv_report_max_sites", 40),
                "igv_report_flanking_bp": params.get("igv_report_flanking_bp", 200)}
    _require(all(stats.get(key) == str(value) for key, value in settings.items())
             and stats["total_reads"] == str(sum(reads.values())), "QC effective settings/read count mismatch")
    length_rows = _table(root / "fastq_qc/read_lengths.tsv", ("read_id", "length_bp"))
    observed_lengths = Counter((row["read_id"], int(row["length_bp"])) for row in length_rows)
    expected_lengths = Counter()
    for (read_name, seq), count in reads.items():
        if len(seq) >= settings["min_fastq_read_length"]:
            expected_lengths[(read_name, len(seq))] += count
    _require(observed_lengths == expected_lengths, "QC length-filter occurrence inventory mismatch")
    _metrics(root / "fastq_qc/fastq_qc_summary.tsv")
    coverage = _table(root / "fastq_qc/fastq_coverage.tsv", ("reference", "position", "depth"))
    _require([(row["reference"], int(row["position"])) for row in coverage] == [(name, pos) for pos in range(1, length + 1)]
             and all(int(row["depth"]) >= 0 for row in coverage), "QC coverage coordinate denominator mismatch")
    support = _table(root / "fastq_qc/per_base_support.tsv", ("chrom", "position_1based", "reference_base", "depth"))
    _require([(row["chrom"], int(row["position_1based"]), row["reference_base"]) for row in support] ==
             [(name, pos, chr(base)) for pos, base in enumerate(sequence, 1)], "QC support reference coordinates mismatch")
    for row in support:
        depth = int(row["depth"])
        _require(all(int(row[key]) >= 0 for key in ("depth", "forward_depth", "reverse_depth", "a_count", "c_count", "g_count", "t_count", "n_count", "deletion_count", "insertion_count"))
                 and depth == int(row["forward_depth"]) + int(row["reverse_depth"])
                 and depth == sum(int(row[key]) for key in ("a_count", "c_count", "g_count", "t_count", "n_count", "deletion_count")),
                 "QC support depth accounting mismatch")
    dimer_columns = {
        "dimer_breakpoint_call.tsv": ("call_status", "call_confidence", "primary_position_mod_ref"),
        "dimer_evidence_by_position.tsv": ("position_mod_ref", "support_reads", "split_support_reads"),
        "dimer_read_events.tsv": ("read_id", "start", "end", "position_mod_ref", "event_type"),
        "dimer_breakpoint_sequences.tsv": ("reference_name", "reference_length", "junction_window_seq"),
        "dimer_secondary_anomalies.tsv": ("rank", "anomaly_type", "anomaly_score", "position_mod_ref"),
        "dimer_secondary_summary.tsv": ("secondary_signal_status", "candidate_count", "non_boundary_split_reads"),
    }
    for relative in _REQUIRED_STAGE_OUTPUT_SUFFIXES["dimer_qc"]:
        filename = Path(relative).name
        rows = _table(root / relative, dimer_columns[filename])
        if filename in {"dimer_breakpoint_call.tsv", "dimer_secondary_summary.tsv"}:
            _require(len(rows) == 1, "native canonical multimer summary row denominator mismatch")
        for row in rows:
            for key, value in row.items():
                if key.endswith("position_mod_ref") and value:
                    _require(0 <= int(value) <= length, "multimer position exceeds source reference")
        artifacts.append(_artifact(root, relative))
    if verify:
        verification = validate_verification(
            root, persisted, job, alignment_result, reads_identity=reads_identity,
            input_dir="fastq_qc/construct_verification_input", method="samtools_consensus",
            consensus_relative="fastq_qc/fastq_consensus.fasta", support_relative="fastq_qc/per_base_support.tsv",
            stats_relative="fastq_qc/fastq_alignment_stats.tsv", breakpoint_relative="multimer_qc/dimer_breakpoint_call.tsv",
            secondary_relative="multimer_qc/dimer_secondary_summary.tsv")
        artifacts.extend(verification.pop("artifacts"))
        return {**verification, "artifacts": artifacts}
    return {"state": "validated", "verification_state": "owned_by_clone_adapter", "artifacts": artifacts}


def validate_verification(root, persisted, job, alignment_result, *, reads_identity, input_dir, method,
                          consensus_relative, support_relative, stats_relative, breakpoint_relative, secondary_relative):
    """Shared ConstructVerify parser, bound to the actual branch-owned inputs."""
    params = job.params
    with files._open_regular_file_no_symlinks(root / "align/reference.fasta") as handle:
        contigs, sequence = files._fasta_contigs_from_handle(handle)
    name, length = next(iter(contigs)), len(sequence)
    ids = {item["path"]: (item["sha256"], item["size_bytes"]) for item in alignment_result["artifacts"]}
    verification, artifacts = _manifest(root, "verification/qc_manifest.json")
    _stage(root, persisted, job, "construct_verification", _REQUIRED_STAGE_OUTPUT_SUFFIXES["construct_verification"])
    state_relative = input_dir + "/observed_state.json"
    state, _ = _document(root / state_relative)
    _require(state["schema"] == "biomodstack.observed_sequence_state.v1"
             and state["method"] == method
             and state["reference_sequence_sha256_actual"] == state["reference_sequence_sha256_declared"] == params["reference_sequence_sha256"]
             and state["source_reads_sha256"] == reads_identity[0], "QC observed-state source/reference mismatch")
    artifacts.append(_artifact(root, state_relative))
    retained = state["source_reads_path"]
    _require(retained in {"source_reads.fastq", "source_reads.fastq.gz"}, "unsafe retained QC reads path")
    retained_relative = input_dir + "/" + retained
    _require(_identity(root / retained_relative) == reads_identity, "retained QC source differs from actual reads")
    artifacts.append(_artifact(root, retained_relative))
    if state["state"] == "present":
        _require(state["observed_fasta"] == "observed_consensus.fasta", "invalid observed consensus path")
        observed_relative = input_dir + "/observed_consensus.fasta"
        observed_identity = _identity(root / observed_relative)
        _require(observed_identity[0] == state["observed_sha256"]
                 and observed_identity == _identity(root / consensus_relative), "QC observed consensus copy mismatch")
        artifacts.append(_artifact(root, observed_relative))
    else:
        _require(state["state"] == "missing" and state["reason"] == "CONSENSUS_NOT_PRODUCED", "invalid missing consensus state")
    expected_inputs = {
        "reference": ids["align/reference.fasta"], "alignment": ids["align/aligned.bam"],
        "alignment_index": ids["align/aligned.bam.bai"], "source_reads": reads_identity,
        "support": _identity(root / support_relative),
        "alignment_stats": _identity(root / stats_relative),
        "topology": _identity(root / "verification/topology_evidence.json"),
    }
    for role, identity in expected_inputs.items():
        evidence = verification["inputs"][role]
        _require((evidence["sha256"], evidence["size_bytes"]) == identity, f"verification {role} source identity mismatch")
    execution = dict(verification["execution"])
    producer_text = execution.pop("producer_receipt")
    producer_lines = producer_text.splitlines()
    tool_names = tuple(line.split("=", 1)[0].split(":", 1)[1]
                       for line in producer_lines if line.startswith("bms_producer_tool:"))
    _require(set(tool_names) in ({"python3", "samtools"}, {"python", "samtools"},
                                {"python3", "apptainer"}, {"python", "apptainer"}),
             "verification used an unsupported runtime command")
    if "apptainer" in tool_names:
        image_lines = [line for line in producer_lines if line.startswith("bms_producer_fallback_image=")]
        _require(len(image_lines) == 1 and re.fullmatch(r"bms_producer_fallback_image=[0-9a-f]{64}", image_lines[0]),
                 "verification fallback image identity is missing")
        producer_text = "\n".join(line for line in producer_lines if line not in image_lines)
    _validate_producer(producer_text, ("modules/ngs/construct_verify.nf", "scripts/build_construct_topology_evidence.py",
                       "scripts/verify_construct.py", "config/ngs/construct_verify_profiles.json"), tool_names)
    _require(execution == {"status": "SUCCEEDED", "exit_code": 0, "reason_codes": []}
             and verification["summary"]["reference_name"] == name
             and verification["summary"]["reference_length"] == length, "verification execution/reference mismatch")
    profiles = json.loads((DORADO_LOCK_PATH.parents[2] / "config/ngs/construct_verify_profiles.json").read_bytes())["profiles"]
    selected_profile = params.get("construct_verify_profile", "plasmid_strict_v1")
    _require(verification["threshold_profile"]["id"] == selected_profile
             and verification["threshold_profile"]["values"] == profiles[selected_profile], "verification profile differs from accepted settings")
    summary = _metrics(root / "verification/verification_summary.tsv")
    _require(summary["verdict"] == verification["verdict"]
             and summary["variant_count"] == str(verification["summary"]["variant_count"]), "verification summary contradicts manifest")
    with files._open_regular_file_no_symlinks(root / "verification/variants.vcf") as handle:
        with pysam.VariantFile(handle) as vcf:
            variants = list(vcf)
            _require(all(record.contig == name and 0 <= record.start < record.stop <= length for record in variants)
                     and len(variants) == verification["summary"]["variant_count"], "verification VCF coordinate/count mismatch")
    _table(root / "verification/per_base_metrics.tsv")
    with files._open_regular_file_no_symlinks(root / "verification/evidence.html") as handle:
        html = handle.read().decode("utf-8").lower()
    _require("<html" in html and "</html>" in html, "native verification report is malformed HTML")
    topology, _ = _document(root / "verification/topology_evidence.json")
    _require(topology["schema"] == "biomodstack.construct_topology_evidence.v1", "invalid topology schema")
    for key, relative in (("reference_sha256", "align/reference.fasta"), ("alignment_bam_sha256", "align/aligned.bam"),
                          ("breakpoint_call_sha256", breakpoint_relative),
                          ("secondary_summary_sha256", secondary_relative)):
        _require(topology["provenance"][key] == _identity(root / relative)[0], "topology source binding mismatch")
    artifacts.append(_artifact(root, "verification/topology_evidence.json"))
    # Preserve negative scientific outcomes. Only malformed execution/identity is
    # a terminal error; no threshold or scientific verdict is recomputed here.
    return {"state": "validated", "verification_verdict": verification["verdict"],
            "verification_reason_codes": verification["reason_codes"], "artifacts": artifacts}


def validate_native_plasmid(job):
    descriptor = None
    try:
        from services.ont_ngs_completion import ont_completion_lane
        _require(ont_completion_lane(job) == "native_plasmid", "job is outside the implemented native plasmid/clone branches")
        params = job.params
        mode, workflow = params["ont_input_mode"], params["ont_workflow_id"]
        _require({kind for kind, key in (("pod5", "pod5_dir"), ("bam", "bam_path"), ("fastq", "fastq_path")) if params.get(key)} == {mode},
                 "native plasmid primary input selections conflict")
        _require(workflow != "wf_clone_validation" or params.get("run_assembly", True) is True,
                 "clone-validation assembly cannot be disabled")
        _require(workflow in {"ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation", "ont_fastq_qc"}, "native plasmid workflow mismatch")
        _require(workflow != "ont_fastq_qc" or mode == "fastq" and params.get("run_fastq_qc", True) is True,
                 "FASTQ-QC requires FASTQ input and QC")
        persisted = resolve_persisted_job_result_root(job)
        descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        root = Path(f"/proc/self/fd/{descriptor}")
        source_identity = None
        if mode == "pod5":
            result = _validate_basecall(root, persisted, job)
            alignment = _validate_reference_alignment(root, persisted, job, result)
            result["artifacts"].extend(alignment.pop("artifacts"))
            result["alignment"] = alignment
            expected_stages = {"dorado_basecall", "dorado_align"}
        elif mode == "bam":
            realign = params.get("bam_force_realign", False)
            _require(type(realign) is bool, "invalid BAM realignment selection")
            validator = validate_realigned_external_bam if realign else validate_external_bam
            result, source_identity, _ = validator(root, persisted, job)
            expected_stages = {"dorado_align" if realign else "bam_prepare"}
        else:
            _require(mode == "fastq", "unknown native plasmid input mode")
            result, source_identity = validate_fastq_alignment(root, persisted, job)
            expected_stages = {"fastq_align"}
        qc_applicable = workflow == "ont_plasmid_qc" or mode == "fastq"
        qc_enabled = qc_applicable and params.get("run_fastq_qc", True)
        _require(type(params.get("run_fastq_qc", True)) is bool, "invalid native QC selection")
        if qc_enabled:
            qc = validate_qc(root, persisted, job, result, verify=workflow != "wf_clone_validation")
            result["artifacts"].extend(qc.pop("artifacts"))
            result["fastq_qc"] = qc
            if workflow != "wf_clone_validation":
                result["verification"] = {"state": "validated", "verdict": qc["verification_verdict"]}
                expected_stages.add("construct_verification")
            expected_stages |= {"fastq_qc", "dimer_qc"}
        else:
            state = "not_requested" if qc_applicable else "not_applicable"
            result["fastq_qc"] = {"state": state}
            result["verification"] = {"state": state}
            unexecuted = ["fastq_qc/qc_manifest.json", "multimer_qc/dimer_breakpoint_call.tsv"]
            if workflow != "wf_clone_validation":
                unexecuted.append("verification/qc_manifest.json")
            for relative in unexecuted:
                _require(not (root / relative).exists() and not (root / relative).is_symlink(), "unexecuted native QC products exist")
        assembly = {"state": "not_requested" if workflow == "ont_construct_screening" else "not_applicable"}
        if workflow == "wf_clone_validation" or workflow == "ont_construct_screening" and params.get("run_assembly", False):
            from services.ont_ngs_native_clone import validate_clone_assembly
            assembly = validate_clone_assembly(root, persisted, job)
            result["artifacts"].extend(assembly.pop("artifacts"))
            expected_stages.add("wf_clone_validation")
            if workflow == "wf_clone_validation":
                from services.ont_ngs_native_clone import validate_clone_adapter
                verification = validate_clone_adapter(root, persisted, job, result)
                result["artifacts"].extend(verification.pop("artifacts"))
                result["verification"] = verification
                expected_stages.add("construct_verification")
        if params.get("comparison_panel_snapshot"):
            # These are the only entrypoints that actually invoke comparison.
            _require(workflow in {"ont_plasmid_qc", "ont_fastq_qc", "ont_construct_screening"} and qc_enabled,
                     "selected comparison has no executable stage in this workflow/settings branch")
            from services.ont_ngs_native_comparison import validate_comparison
            comparison = validate_comparison(root, persisted, job)
            result["artifacts"].extend(comparison.pop("artifacts"))
            result["comparison_panel"] = comparison
            expected_stages.add("comparison_panel")
        else:
            _require(not params.get("comparison_panel_binding"), "comparison receipt has no selected snapshot")
            result["comparison_panel"] = {"state": "not_requested"}
        _require(set(job.provenance["stage_terminal_states"]) == expected_stages, "native plasmid terminal stage denominator mismatch")
        unique = {}
        for item in result["artifacts"]:
            previous = unique.get(item["path"])
            _require(previous is None or previous == item, "native artifacts have contradictory identities")
            _require(_identity(root / item["path"]) == (item["sha256"], item["size_bytes"]), "native product changed before publication")
            unique[item["path"]] = item
        result["artifacts"] = [unique[key] for key in sorted(unique)]
        reference = unique["align/reference.fasta"]
        _require(_identity(Path(params["reference_fasta"])) == (reference["sha256"], reference["size_bytes"]), "selected reference changed before publication")
        if mode == "pod5" and result.get("pairs_sha256") is not None:
            _require(_identity(Path(params["duplex_pairs"]))[0] == result["pairs_sha256"], "duplex selection changed before publication")
        if source_identity is not None:
            _require(_identity(Path(params[mode + "_path"])) == source_identity, "selected source changed before publication")
        if assembly["state"] == "validated":
            execution, _ = _document(root / "assembly/execution_receipt.json")
            for flag, key in (("--primers", "wf_clone_primers"), ("--insert_reference", "wf_clone_insert_reference"),
                              ("--host_reference", "wf_clone_host_reference"), ("--regions_bedfile", "wf_clone_regions_bedfile")):
                if params.get(key):
                    evidence = execution["inputs"][flag]
                    _require(_identity(Path(params[key])) == (evidence["sha256"], evidence["size_bytes"]), "clone auxiliary input changed before publication")
        after = os.stat(persisted, follow_symlinks=False)
        _require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), "native result root changed before publication")
        result.update(result_kind="ont_native_clone_validation" if workflow == "wf_clone_validation" else "ont_native_plasmid", alignment_state="validated",
                      assembly=assembly,
                      effective_params_sha256=hashlib.sha256(rfc8785.dumps(params)).hexdigest(),
                      artifact_set_sha256=hashlib.sha256(rfc8785.dumps(result["artifacts"])).hexdigest())
        return result
    except OntNgsCompletionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ArithmeticError, csv.Error, SequenceQcManifestError, files.AlignmentSessionError) as exc:
        raise OntNgsCompletionError("native plasmid package is missing, corrupt, or inconsistent") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
