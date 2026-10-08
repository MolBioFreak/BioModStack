"""Pooled scientific admission, without operator release or derived writes."""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
import os
from pathlib import Path
import runpy

import pysam
import rfc8785
from starlette.concurrency import run_in_threadpool

from services import ngs_alignment_sessions
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_completion import OntNgsCompletionError
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ont_ngs_native_completion import _digest, _identity, _document, _require, validate_pooled_producer
from services.ont_pooled_reference_assignment import (
    PooledAssignmentError, _validate_assignment_evidence,
    validate_pooled_reference_set_for_job,
)


def _validate_native(job, manifest_row, targets):
    identities = {str(job.params[key]).strip() for key in
                  ("ont_workflow_id", "ont_request_workflow_id", "workflow_id")
                  if job.params.get(key) is not None and str(job.params[key]).strip()}
    inputs = {str(job.params[key]).strip() for key in ("ont_input_mode", "input_mode")
              if job.params.get(key) is not None and str(job.params[key]).strip()}
    _require(identities == {"ont_pooled_reference_assignment"} and inputs <= {"fastq"},
             "pooled workflow/input identities conflict")
    persisted = resolve_persisted_job_result_root(job)
    descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        pinned = os.fstat(descriptor)
        root = Path(f"/proc/self/fd/{descriptor}") / "pooled_reference_assignment"
        # Reuse the producer's pure reference parser and deterministic evidence
        # interpretation, never its preflight writers, subprocesses or CLI.
        producer = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/pooled_ont_reference_assignment.py"))
        reference_set = producer["validate_reference_set"](Path(manifest_row.manifest_path))
        igv, igv_sha = _document(root / "intended_pool.igv_session.json")
        expected = {
            "assignment_summary": "assignment_summary.json",
            "per_read_assignment": "per_read_assignment.tsv",
            "fastq_preflight": "fastq_preflight.json",
            "occurrence_map": "occurrence_map.json",
            "combined_reference": "combined_intended_reference.fasta",
            "combined_reference_index": "combined_intended_reference.fasta.fai",
            "alignment_bam": "pooled_assignment.bam",
            "alignment_bai": "pooled_assignment.bam.bai",
            "alignment_log": "pooled_reference_assignment.minimap2.log",
            "ambiguous_read_ids": "ambiguous.read_ids.txt",
            "ambiguous_fastq": "ambiguous.fastq",
            "unclassified_read_ids": "unclassified.read_ids.txt",
            "unclassified_fastq": "unclassified.fastq",
        }
        for target in targets:
            expected[f"{target.target_id}_read_ids"] = f"target_{target.target_id}.read_ids.txt"
            expected[f"{target.target_id}_fastq"] = f"target_{target.target_id}.fastq"
        with ExitStack() as stack:
            handles = {name: stack.enter_context(ngs_alignment_sessions._open_regular_file_no_symlinks(root / name))
                       for name in expected.values()}
            ids = {name: (_digest(handle), os.fstat(handle.fileno()).st_size) for name, handle in handles.items()}
            context = _validate_assignment_evidence(job, manifest_row, manifest_row.manifest_json, targets)
            summary = context["summary"]
            _require(context["summary_sha256"] == ids["assignment_summary.json"][0], "pooled summary changed")
            execution = validate_pooled_producer(job)
            _require(execution["assignment_summary_sha256"] == context["summary_sha256"]
                     and execution["igv_session_sha256"] == igv_sha
                     and execution["alignment_log_sha256"] == ids[expected["alignment_log"]][0],
                     "pooled producer identity changed")
            source_path = Path(job.params["fastq_path"])
            external = {
                "reference_set_manifest": (Path(manifest_row.manifest_path).name, reference_set.manifest_file_sha256),
                "input_fastq": (source_path.name, job.params["fastq_sha256"]),
            }
            declared = igv["artifacts"]
            _require(type(declared) is list and len(declared) == len(expected) + len(external),
                     "pooled artifact inventory is incomplete")
            seen = set()
            for item in declared:
                kind = item["kind"]
                _require(kind not in seen and kind in expected.keys() | external.keys(), "foreign or duplicate pooled artifact")
                seen.add(kind)
                name, digest = external[kind] if kind in external else (expected[kind], ids[expected[kind]][0])
                _require(item["path"] == name and item["sha256"] == digest, "pooled artifact digest mismatch")
                if kind == "reference_set_manifest":
                    _require(item["declared_manifest_sha256"] == manifest_row.manifest_sha256, "pooled frozen manifest mismatch")
                if kind == "occurrence_map":
                    _require(item["count"] == summary["occurrence_map_count"], "pooled occurrence count mismatch")
            _require(igv["schema"] == producer["IGV_SESSION_SCHEMA"]
                     and igv["workflow_id"] == producer["WORKFLOW_ID"]
                     and igv["manifest_id"] == manifest_row.id
                     and igv["manifest_sha256"] == manifest_row.manifest_sha256
                     and igv["reference_entries"] == [
                         {"target_id": entry.target_id, "revision_sha256": entry.revision_sha256,
                          "fasta_path": entry.fasta_path, "fasta_sha256": entry.fasta_sha256}
                         for entry in reference_set.entries]
                     and igv["reference"] == {"path": expected["combined_reference"],
                         "sha256": ids[expected["combined_reference"]][0], "index_path": expected["combined_reference_index"]}
                     and igv["tracks"] == [{"path": expected["alignment_bam"], "index_path": expected["alignment_bai"]}]
                     and all(igv[key] == summary[key] for key in
                             ("occurrence_map_path", "occurrence_map_sha256", "occurrence_map_count")),
                     "pooled viewer/reference authority mismatch")
            fasta_name = expected["combined_reference"]
            _require(handles[fasta_name].read() == producer["combined_reference_bytes"](reference_set),
                     "pooled combined FASTA differs from frozen targets")
            fdpath = lambda name: f"/proc/self/fd/{handles[name].fileno()}"
            with pysam.FastaFile(fdpath(fasta_name), filepath_index=fdpath(expected["combined_reference_index"])) as fasta:
                _require(list(fasta.references) == [entry.target_id for entry in reference_set.entries]
                         and all(fasta.fetch(entry.target_id) == entry.normalized_sequence for entry in reference_set.entries),
                         "pooled FASTA index disagrees with frozen targets")
            preflight, _ = _document(root / "fastq_preflight.json")
            _require(preflight == {
                "schema": producer["PREFLIGHT_SCHEMA"], "input_fastq_filename": source_path.name,
                "input_fastq_sha256": job.params["fastq_sha256"],
                "input_records": summary["counts"]["input_fastq_records"],
                "valid_fastq_reads": summary["counts"]["valid_fastq_reads"],
                "rejected_by_input_policy": 0, "rejected_reasons": {},
                "occurrence_map_path": "occurrence_map.json", "occurrence_map_sha256": summary["occurrence_map_sha256"],
                "occurrence_map_count": summary["occurrence_map_count"],
                "reference_set_manifest_id": manifest_row.id, "reference_set_manifest_sha256": manifest_row.manifest_sha256,
                "combined_reference_sha256": ids[fasta_name][0],
                "policy": "strict_four_line_fastq_dna_acgtn_occurrence_normalization",
            } and summary["counts"]["rejected_by_input_policy"] == 0, "pooled preflight binding mismatch")
            records = []
            with producer["_open_fastq"](source_path) as source:
                for ordinal, assignment in enumerate(summary["read_assignments"], 1):
                    _require(assignment["input_ordinal"] == ordinal, "pooled assignment occurrence order changed")
                    lines = [source.readline() for _ in range(4)]
                    _require(producer["_fastq_rejection_reason"](lines) is None, "pooled source FASTQ malformed")
                    header, sequence, _, quality = [producer["_strip_line"](line) for line in lines]
                    _require(header[1:] == assignment["source_header"], "pooled source occurrence order changed")
                    records.append(producer["FastqRecord"](assignment["input_ordinal"], assignment["occurrence_id"],
                        assignment["source_read_id"], assignment["source_header"], sequence.upper(), quality))
                _require(source.readline() == "", "pooled source has additional occurrences")
            by_id = {record.occurrence_id: record for record in records}
            primary = Counter()
            sequential = Counter()
            index_counts = Counter()
            no_coordinate = count = 0
            with pysam.AlignmentFile(fdpath(expected["alignment_bam"]), "rb",
                    index_filename=fdpath(expected["alignment_bai"]), require_index=True) as bam:
                _require(bam.is_bam and bam.check_index() and bam.header.to_dict().get("HD", {}).get("SO") == "coordinate",
                         "pooled BAM/index is invalid or not coordinate sorted")
                sq = bam.header.to_dict().get("SQ", [])
                _require(len(sq) == len(reference_set.entries) and all(
                    item["SN"] == entry.target_id and item["LN"] == len(entry.normalized_sequence)
                    and ("M5" not in item or item["M5"].lower() == hashlib.md5(entry.normalized_sequence.encode("ascii")).hexdigest())
                    for item, entry in zip(sq, reference_set.entries, strict=True)), "pooled BAM reference dictionary mismatch")
                # Feed the pure native classifier a streaming SAM view; no aligner
                # invocation and no artifact rewrite occurs during admission.
                def sam_lines():
                    nonlocal no_coordinate, count
                    yield from str(bam.header).splitlines()
                    previous = (-1, -1)
                    for read in bam.fetch(until_eof=True):
                        count += 1
                        _require(read.query_name in by_id, "pooled BAM contains foreign occurrence")
                        source = by_id[read.query_name]
                        if not read.is_secondary and not read.is_supplementary:
                            primary[read.query_name] += 1
                            _require(read.get_forward_sequence() == source.sequence
                                     and tuple(read.get_forward_qualities() or ()) == tuple(ord(c) - 33 for c in source.quality),
                                     "pooled primary BAM sequence/quality differs from source occurrence")
                        if read.reference_id < 0:
                            no_coordinate += 1
                        else:
                            _require(no_coordinate == 0 and (read.reference_id, read.reference_start) >= previous,
                                     "pooled BAM record order is invalid")
                            previous = (read.reference_id, read.reference_start)
                            index_counts[(read.reference_name, read.is_unmapped)] += 1
                        if not read.is_unmapped:
                            _require(read.reference_start >= 0 and read.reference_end is not None
                                     and read.reference_end <= bam.lengths[read.reference_id], "pooled alignment coordinates invalid")
                            sequential[read.to_string()] += 1
                        yield read.to_string()
                evidence = producer["parse_sam_evidence"](sam_lines(), reference_set, frozenset(by_id))
                indexed = Counter(read.to_string() for contig in bam.references for read in bam.fetch(contig) if not read.is_unmapped)
                _require(primary == Counter({name: 1 for name in by_id}) and indexed == sequential
                         and bam.nocoordinate == no_coordinate and all(
                            item.mapped == index_counts[(item.contig, False)] and item.unmapped == index_counts[(item.contig, True)]
                            for item in bam.get_index_statistics()), "pooled BAM occurrence/index closure failed")
            assignments = producer["classify_assignments"](records, evidence, reference_set,
                min_mapq=job.params["pooled_assignment_min_mapq"],
                min_alignment_score_margin=job.params["pooled_assignment_min_alignment_score_margin"])
            tsv = csv.DictReader(handles["per_read_assignment.tsv"].read().decode("utf-8").splitlines(), delimiter="\t")
            for native, declared, row in zip(assignments, summary["read_assignments"], tsv, strict=True):
                _require(all(declared[key] == getattr(native, key) for key in ("disposition", "target_id", "reason"))
                         and all(row[key] == ("" if getattr(native, attr) is None else str(getattr(native, attr)))
                             for key, attr in (("best_alignment_score", "best_score"), ("second_alignment_score", "second_score"),
                                               ("alignment_score_delta", "score_delta"), ("best_mapq", "best_mapq"))),
                         "pooled assignment differs from native competitive evidence")
            buckets = [(f"target_{target.target_id}", f"target:{target.target_id}") for target in targets]
            buckets.extend((("ambiguous", "ambiguous"), ("unclassified", "unclassified")))
            for prefix, disposition in buckets:
                selected = [a.record for a in assignments if a.disposition == disposition]
                _require(handles[prefix + ".read_ids.txt"].read().decode("ascii") ==
                         "".join(record.occurrence_id + "\n" for record in selected), "pooled disposition IDs mismatch")
                _require(handles[prefix + ".fastq"].read().decode("ascii") ==
                         "".join(f"@{record.occurrence_id}\n{record.sequence}\n+\n{record.quality}\n" for record in selected),
                         "pooled disposition FASTQ differs from source occurrences")
            for name, handle in handles.items():
                _require((_digest(handle), os.fstat(handle.fileno()).st_size) == ids[name] and _identity(root / name) == ids[name],
                         "pooled native artifact changed during admission")
            _require(_identity(source_path)[0] == job.params["fastq_sha256"]
                     and producer["validate_reference_set"](Path(manifest_row.manifest_path)) == reference_set
                     and _identity(root / "intended_pool.igv_session.json")[0] == igv_sha,
                     "pooled source/reference authority changed during admission")
            current = os.stat(persisted, follow_symlinks=False)
            _require((current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino), "pooled output root changed")
            artifacts = [{"path": "pooled_reference_assignment/" + name, "sha256": digest, "size_bytes": size}
                         for name, (digest, size) in sorted(ids.items())]
            artifacts.append({"path": "pooled_reference_assignment/intended_pool.igv_session.json", "sha256": igv_sha,
                              "size_bytes": _identity(root / "intended_pool.igv_session.json")[1]})
            return {"result_kind": "ont_native_pooled_assignment", "state": "validated",
                    "scientific_status": "REVIEW", "release_state": "awaiting_operator_release",
                    "reference_set_id": str(manifest_row.id), "manifest_sha256": manifest_row.manifest_sha256,
                    "assignment_summary_sha256": context["summary_sha256"], "record_count": count,
                    "counts": summary["counts"], "producer_execution": execution, "artifacts": artifacts,
                    "artifact_set_sha256": hashlib.sha256(rfc8785.dumps(artifacts)).hexdigest()}
    finally:
        os.close(descriptor)


async def validate_and_prepare_pooled_completion(job, session):
    try:
        # The caller's snapshot/CAS owns all durable publication. Prevent ORM
        # reads from flushing any staged terminal mutation before that CAS.
        with session.no_autoflush:
            row, targets = await validate_pooled_reference_set_for_job(session, job)
            result = await run_in_threadpool(_validate_native, job, row, targets)
    except OntNgsCompletionError:
        raise
    except (PooledAssignmentError, OSError, ValueError, KeyError, TypeError, AttributeError,
            ngs_alignment_sessions.AlignmentSessionError) as exc:
        raise OntNgsCompletionError("pooled native scientific evidence is missing, corrupt, or inconsistent") from exc
    job.provenance = {**dict(job.provenance or {}), "result_integrity": result,
                      "pooled_producer_execution": result["producer_execution"]}
    job.status = "completed"
    job.queue_status = "completed"
    job.paused = False
    job.current_stage = "Complete"
    job.stage_progress = None
    job.error_message = None
    return result
