"""Read-only native ONT scientific barriers, independent of presentation stores.

Current finishable slice: unmodified simplex DNA/RNA with optional reference
alignment and capability-bound summary; DNA inline demux with sample-sheet aliases.
Unbarcoded DNA duplex validates native classes and selected pairs; DNA HAC
simplex validates the two locked modified-base models. Modkit remains pending.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
import json
import os
import runpy
from pathlib import Path
from typing import Any, BinaryIO

import pysam
import rfc8785

from services import ngs_alignment_sessions
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ont_ngs_completion import OntNgsCompletionError, _read_manifest, _resolve_terminal_output

_BASECALL_OUTPUTS = (
    "basecall/calls.bam", "basecall/basecall.log", "basecall/dorado_preflight.json",
    "basecall/dorado_runtime_provenance.json",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OntNgsCompletionError(message)


def _digest(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def _identity(path: Path) -> tuple[str, int]:
    with ngs_alignment_sessions._open_regular_file_no_symlinks(path) as handle:
        before = os.fstat(handle.fileno())
        digest = _digest(handle)
        after = os.fstat(handle.fileno())
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "native file changed while reading")
        return digest, after.st_size


def _validate_producer(text: str, sources: tuple[str, ...], tools: tuple[str, ...]) -> dict[str, str]:
    """Consume a task's implementation receipt, not a new science authority.

    Runtime images/models and scientific inputs retain their existing validators.
    Executable digests identify the actual remote binaries (not API-host tools).
    """
    receipt = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if key.startswith("bms_producer_"):
            _require(separator == "=" and key not in receipt, "malformed/duplicate producer identity")
            receipt[key] = value
    code = DORADO_LOCK_PATH.parents[2]
    expected = {"bms_producer_source:" + name: _identity(code / name)[0]
                for name in ("scripts/ngs_producer_identity.sh", *sources)}
    runtime_keys = {"bms_producer_tool:" + name for name in tools} | {"bms_producer_command"}
    _require(set(receipt) == set(expected) | runtime_keys
             and all(receipt[key] == value for key, value in expected.items())
             and all(re.fullmatch(r"[0-9a-f]{64}", receipt[key]) for key in runtime_keys),
             "native producer source/runtime identity mismatch")
    return receipt


def _document(path: Path) -> tuple[dict[str, Any], str]:
    raw, digest = _read_manifest(path)
    value = json.loads(raw)
    _require(isinstance(value, dict), "native provenance must be a JSON object")
    return value, digest


def _validate_sample_sheet(params: dict[str, Any], preflight: dict[str, Any]) -> str | None:
    receipt = preflight["barcoding"]["sample_sheet"]
    selected = params.get("sample_sheet")
    if not selected:
        _require(receipt is None, "unselected sample sheet has preflight authority")
        return None
    _require(bool(params.get("barcode_kit")) and isinstance(receipt, dict),
             "selected sample sheet has no barcode/preflight authority")
    path = Path(selected)
    root = Path(params["pod5_dir"])
    _require(path.is_absolute() and root.is_absolute(), "sample sheet path is not absolute")
    # Parse the exact safely opened bytes using the producer, not a second CSV policy.
    with ngs_alignment_sessions._open_regular_file_no_symlinks(path) as handle:
        sha = _digest(handle)
        raw = handle.read()
        parser = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/dorado_p4_preflight.py"))["_validate_sample_sheet"]
        observed = parser(path, root, params["barcode_kit"], preflight["inputs"], raw_bytes=raw)
        _require(observed == receipt and type(receipt["rows"]) is int and receipt["sha256"] == sha,
                 "sample sheet bytes or assignments disagree with preflight")
        _require(_digest(handle) == sha and _identity(path)[0] == sha,
                 "sample sheet changed during validation")
    return sha


def _validate_pairs(params: dict[str, Any], preflight: dict[str, Any]) -> str | None:
    receipt = preflight["pairs"]
    if params["dorado_basecall_mode"] != "duplex":
        _require(receipt is None and not params.get("duplex_pairs"), "simplex has duplex pairs authority")
        return None
    _require(isinstance(receipt, dict) and bool(params.get("duplex_pairs")),
             "duplex requires selected preflight pairs authority")
    path = Path(params["duplex_pairs"])
    root = Path(params["pod5_dir"])
    _require(path.is_absolute() and root.is_absolute(), "duplex pairs path is not absolute")
    with ngs_alignment_sessions._open_regular_file_no_symlinks(path) as handle:
        sha = _digest(handle)
        parser = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/dorado_p4_preflight.py"))["_validate_pairs"]
        # Membership in POD5 was checked by the original preflight; retain its
        # exact input and pairs bytes, without pretending output names equal inputs.
        observed = parser(path, root, None, raw_bytes=handle.read())
        _require(observed == receipt and all(type(receipt[key]) is int for key in ("pair_count", "read_count"))
                 and receipt["sha256"] == sha, "duplex pairs bytes or counts disagree with preflight")
        _require(_digest(handle) == sha and _identity(path)[0] == sha, "duplex pairs changed during validation")
    return sha


# Locked model configs: 5mC_5hmC@v2 motif C, codes h,m; 6mA@v1 motif A,
# code a. Both single-base contexts. Dorado 1.3.1 messages.cpp:259-336 emits
# separate +/. groups in alphabet order, MN:i and ML:B:C, including empty
# position lists. ModBaseChunkCallerNode:310-325,720-729 initializes mod info
# before the no-motif bypass; absence of sites is not absence of model tags.
_MODIFIED_GROUPS = {"5mC_5hmC": ("C+h.", "C+m."), "6mA": ("A+a.",)}


def _validate_modified_tags(read: pysam.AlignedSegment, selection: str) -> int:
    tags = read.get_tags(with_value_type=True)
    chosen = {tag: [(value, kind) for name, value, kind in tags if name == tag]
              for tag in ("MM", "ML", "MN")}
    _require(all(len(values) == 1 for values in chosen.values()), "modified call requires unique MM/ML/MN tags")
    mm, mm_type = chosen["MM"][0]
    ml, ml_type = chosen["ML"][0]
    mn, mn_type = chosen["MN"][0]
    _require(mm_type == "Z" and ml_type == "B" and getattr(ml, "typecode", None) == "B"
             and mn_type in "cCsSiI" and type(mn) is int and mn == read.query_length,
             "modified tag types or MN read length are invalid")
    groups = mm.split(";")
    expected = _MODIFIED_GROUPS[selection]
    _require(groups[-1] == "" and len(groups) == len(expected) + 1, "modified MM group cardinality is invalid")
    previous = None
    cardinal_count = read.get_forward_sequence().count(expected[0][0])
    probabilities = 0
    for group, prefix in zip(groups[:-1], expected, strict=True):
        _require(re.fullmatch(re.escape(prefix) + r"(?:,(?:0|[1-9][0-9]*))*", group) is not None,
                 "modified MM group differs from locked model format")
        deltas = group[len(prefix):]
        # A shared canonical-base mask is used for all channels by the emitter.
        _require(previous is None or previous == deltas, "modified channels disagree on native positions")
        previous = deltas
        position = -1
        for delta in deltas.split(",")[1:]:
            position += int(delta) + 1
            _require(position < cardinal_count, "modified MM delta exceeds read coordinates")
            probabilities += 1
    _require(len(ml) == probabilities, "modified ML probability cardinality disagrees with MM")
    return probabilities


def _modified_record(read: pysam.AlignedSegment, selection: str) -> tuple:
    _validate_modified_tags(read, selection)
    return (read.query_name, read.get_forward_sequence(), read.get_tag("MM"),
            tuple(read.get_tag("ML")), read.get_tag("MN"))


def _validate_inputs(params: dict[str, Any], preflight: dict[str, Any]) -> str:
    inputs = preflight["inputs"]
    root = Path(params["pod5_dir"])
    _require(root.is_absolute() and not root.is_symlink(), "POD5 input root is unsafe")
    _require(str(root.resolve(strict=True)) == inputs["root"], "POD5 input root disagrees with accepted request")
    files = inputs["files"]
    _require(isinstance(files, list) and bool(files), "POD5 input inventory is empty")
    declared: set[str] = set()
    for record in files:
        relative = record["relative_path"]
        parts = Path(relative).parts
        _require(isinstance(relative, str) and bool(parts) and not Path(relative).is_absolute()
                 and ".." not in parts and Path(relative).suffix.lower() == ".pod5" and relative not in declared,
                 "POD5 input inventory has an unsafe or duplicate path")
        digest, size = _identity(root / relative)
        _require(digest == record["sha256"] and type(record["bytes"]) is int and size == record["bytes"],
                 "POD5 input identity changed after preflight")
        declared.add(relative)
    observed: set[str] = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            candidate = Path(directory) / name
            _require(not candidate.is_symlink(), "POD5 inventory contains a symlink")
        for name in names:
            candidate = Path(directory) / name
            if params.get("sample_sheet") and candidate == Path(params["sample_sheet"]):
                continue  # Exact selected ancillary bytes validated separately.
            if params.get("duplex_pairs") and candidate == Path(params["duplex_pairs"]):
                continue  # Only the exact validated selected pairs file is ancillary.
            _require(candidate.suffix.lower() == ".pod5", "unexpected file in POD5 input inventory")
            observed.add(candidate.relative_to(root).as_posix())
    _require(observed == declared, "POD5 input inventory denominator changed")
    return hashlib.sha256(rfc8785.dumps(inputs)).hexdigest()


# Dorado v1.3.1 7c84b01de1e46d4c5b2d5208fc430f27579a6c22:
# SummaryFileWriter.cpp general + BASECALLING + EXPERIMENT fields. No align,
# barcoding or poly(A) flags are selected by this bounded emitter branch.
_SUMMARY_COLUMNS = (
    "input_filename batch_id parent_read_id read_id run_id channel mux "
    "minknow_events start_time duration passes_filtering template_start "
    "num_events_template template_duration sequence_length_template "
    "mean_qscore_template pore_type experiment_id sample_id end_reason"
).split()
_SUMMARY_PATH = "basecall/sequencing_summary.tsv"


def _validate_summary(root: Path, params: dict[str, Any], runtime: dict[str, Any],
                      bam_records: Counter) -> tuple[str, dict[str, Any] | None]:
    requested = params.get("emit_summary", True)
    _require(type(requested) is bool, "native summary request must be boolean")
    evidence = runtime.get("summary")
    path = root / _SUMMARY_PATH
    if evidence is None and not requested:
        # Previously accepted emit_summary=false receipts remain readable.
        _require(not path.exists() and not path.is_symlink(), "unrequested native summary exists")
        return "not_requested", None
    _require(isinstance(evidence, dict) and evidence["requested"] is requested,
             "native summary request disagrees with runtime evidence")
    capability = evidence["capability"]
    if params["dorado_basecall_mode"] == "duplex":
        # duplex.cpp constructs only the HTS writer, not SummaryFileWriter;
        # the module deliberately performs no summary capability probe here.
        _require(capability is None and evidence["executed"] is False and evidence["output"] is None
                 and not path.exists() and not path.is_symlink(), "duplex cannot claim native summary output or probe")
        return ("not_applicable" if requested else "not_requested"), None
    if requested:
        _require(isinstance(capability, dict) and type(capability["supported"]) is bool
                 and isinstance(capability["help_sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", capability["help_sha256"]) is not None,
                 "native summary capability evidence is invalid")
        executed = capability["supported"]
        # StreamHtsFileWriter opens stdout BAM only on its first record. With
        # zero reads it emits no BAM header; the module's quickcheck fails.
        _require(bool(bam_records), "zero-read stdout cannot complete the native summary emitter")
    else:
        _require(capability is None, "disabled summary must not claim a capability probe")
        executed = False
    _require(evidence["executed"] is executed, "native summary execution disagrees with capability/request")
    if not executed:
        _require(evidence["output"] is None and not path.exists() and not path.is_symlink(),
                 "non-emitted native summary has output evidence")
        return ("unsupported" if requested else "not_requested"), None
    output = evidence["output"]
    _require(isinstance(output, dict) and output["path"] == _SUMMARY_PATH,
             "native summary output path is invalid")
    with ngs_alignment_sessions._open_regular_file_no_symlinks(path) as handle:
        sha = _digest(handle)
        size = os.fstat(handle.fileno()).st_size
        _require(sha == output["sha256"] and type(output["size_bytes"]) is int
                 and size == output["size_bytes"], "native summary output identity mismatch")
        lines = handle.read().decode("utf-8").splitlines(keepends=True)
        _require(bool(lines) and all(line.endswith("\n") for line in lines),
                 "native summary is empty or truncated")
        _require(lines[0].removesuffix("\n").split("\t") == _SUMMARY_COLUMNS,
                 "native summary header disagrees with pinned emitter")
        observed: Counter = Counter()
        for line in lines[1:]:
            fields = line.removesuffix("\n").split("\t")
            _require(len(fields) == len(_SUMMARY_COLUMNS), "native summary row width is invalid")
            row = dict(zip(_SUMMARY_COLUMNS, fields))
            for name in ("batch_id", "channel", "mux", "minknow_events", "num_events_template", "sequence_length_template"):
                _require(re.fullmatch(r"[0-9]+", row[name]) is not None,
                         "native summary integer field is invalid")
            for name in ("start_time", "duration", "template_start", "template_duration", "mean_qscore_template"):
                value = float(row[name])
                _require(math.isfinite(value) and value >= 0, "native summary numeric field is invalid")
            _require(row["passes_filtering"] == "TRUE" and row["batch_id"] == "0",
                     "native stdout summary must contain passing reads and native batch identity")
            observed[(row["read_id"], row["parent_read_id"],
                      int(row["sequence_length_template"]), row["mean_qscore_template"])] += 1
        _require(observed == bam_records, "native summary disagrees with BAM read/parent/length/qscore inventory")
        _require(_digest(handle) == sha, "native summary changed during semantic validation")
    return "validated", {"path": _SUMMARY_PATH, "sha256": sha, "size_bytes": size}


def _validate_basecall(root: Path, persisted: Path, job: Any) -> dict[str, Any]:
    params = job.params
    workflow = params["ont_workflow_id"]
    molecule = "rna" if workflow == "ont_basecall_rna" else "dna"
    duplex = params["dorado_basecall_mode"] == "duplex"
    terminal = job.provenance["stage_terminal_states"]["dorado_basecall"]
    _require(terminal["status"] == "complete", "native basecalling stage is not complete")
    outputs = terminal["outputs"]
    _require(isinstance(outputs, list), "native stage output authority is malformed")
    suffixes = [_resolve_terminal_output(value, root, persisted, stage="dorado_basecall")[1] for value in outputs]
    _require(tuple(suffixes) == _BASECALL_OUTPUTS, "native basecall terminal output contract mismatch")
    preflight, preflight_sha = _document(root / "basecall/dorado_preflight.json")
    runtime, runtime_sha = _document(root / "basecall/dorado_runtime_provenance.json")
    _validate_producer(preflight["producer_receipt"],
                       ("modules/ngs/dorado_basecall.nf", "scripts/dorado_p4_preflight.py"), ("python", "apptainer"))
    with ngs_alignment_sessions._open_regular_file_no_symlinks(root / "basecall/basecall.log") as log:
        producer_log_sha = _digest(log)
        _validate_producer(log.read().decode("utf-8"),
                           ("modules/ngs/dorado_basecall.nf", "scripts/dorado_supports_option.sh"),
                           ("dorado", "samtools"))
        _require(_digest(log) == producer_log_sha, "basecall producer log changed while reading")
    lock_bytes = DORADO_LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    _require(hashlib.sha256(lock_bytes).hexdigest() == params["dorado_lock_sha256"] == preflight["lock"]["sha256"],
             "native Dorado lock disagrees with accepted authority")
    model = lock["models"][molecule][params["dorado_quality_mode"]]
    selection = preflight["selection"]
    assets = preflight["runtime"]["assets"]
    modification = params["modified_bases"]
    mod_model = lock["models"]["modified_bases"][modification] if modification != "none" else None
    _require(preflight["schema"] == "biomodstack.dorado_preflight.v1"
             and runtime["schema"] == "biomodstack.dorado_runtime_provenance.v1"
             and runtime["preflight_sha256"] == preflight_sha,
             "native Dorado provenance schema or linkage is invalid")
    _require(selection["molecule"] == params["ont_molecule_type"] == molecule
             and selection["quality"] == params["dorado_quality_mode"]
             and selection["model_id"] == runtime["model_id"] == params["dorado_resolved_model_id"] == model["id"]
             and selection["model_aggregate_sha256"] == model["aggregate_sha256"]
             and selection["mode"] == runtime["mode"] == params["dorado_basecall_mode"]
             and selection["mode"] in {"simplex", "duplex"}
             and selection["modified_bases"] == modification
             and selection["modified_bases_model_id"] == (mod_model["id"] if mod_model else None)
             and selection["stereo_model_id"] == (lock["models"]["stereo"]["id"] if duplex else None),
             "native Dorado selection disagrees with accepted settings")
    if mod_model:
        _require(molecule == "dna" and not duplex and not params.get("barcode_kit")
                 and not params.get("sample_sheet") and params["dorado_quality_mode"] == mod_model["base_quality"]
                 and runtime.get("modified_bases_model_id") == mod_model["id"],
                 "modified model invocation disagrees with accepted settings")
        _require(all(assets["models"]["modified_bases"][key] == mod_model[key]
                     and type(assets["models"]["modified_bases"][key]) is type(mod_model[key])
                     for key in ("aggregate_sha256", "files", "bytes")), "modified model asset identity mismatch")
    if duplex:
        stereo = lock["models"]["stereo"]
        _require(molecule == "dna" and not params.get("barcode_kit") and not params.get("sample_sheet")
                 and params["emit_moves"] is False and params.get("trim_adapters", True) is True,
                 "duplex settings disagree with supported native producer")
        _require(all(assets["models"]["stereo"][key] == stereo[key]
                     for key in ("aggregate_sha256", "files", "bytes")), "duplex stereo model identity mismatch")
    _require(assets["verified"] is True
             and all(assets["models"]["base"][key] == model[key] for key in ("aggregate_sha256", "files", "bytes"))
             and assets["runtime_sif"]["sha256"] == runtime["runtime_sha256"] == preflight["runtime"]["sif_sha256"] == lock["dorado"]["sif_sha256"]
             and preflight["runtime"]["version"] == lock["dorado"]["version"],
             "native Dorado runtime/model evidence disagrees with locked assets")
    _require(all(preflight["execution_policy"][native] == params[key]
                 for native, key in (("batch_size", "dorado_batch_size"), ("device", "dorado_device"), ("min_qscore", "min_qscore")))
             and runtime["emit_moves"] is params["emit_moves"] and type(params["emit_moves"]) is bool
             and preflight["barcoding"]["kit"] == params.get("barcode_kit")
             and (not params.get("barcode_kit") or (molecule == "dna" and params["barcode_kit"] in lock["barcoding"]["accepted_kits"])),
             "native execution policy disagrees with accepted settings")
    pairs_sha = _validate_pairs(params, preflight)
    sheet_sha = _validate_sample_sheet(params, preflight)
    input_sha = _validate_inputs(params, preflight)
    calls = runtime["calls_bam"]
    with ngs_alignment_sessions._open_regular_file_no_symlinks(root / "basecall/calls.bam") as handle:
        bam_sha = _digest(handle)
        _require(bam_sha == calls["sha256"], "native calls BAM digest mismatch")
        names: list[bytes] = []
        summary_records: Counter = Counter()
        tags = {"mv": 0, "ts": 0, "ns": 0}
        duplex_counts = {"simplex": 0, "duplex_parent": 0, "duplex": 0}
        modification_probabilities = 0
        with pysam.AlignmentFile(handle, "rb", check_sq=False) as bam:
            _require(bam.is_bam, "native calls must be BAM, not SAM or CRAM")
            for read in bam.fetch(until_eof=True):
                _require(bool(read.query_name) and bool(read.query_sequence) and read.is_unmapped,
                         "native unreferenced call has no sequence/name or is aligned")
                names.append(read.query_name.encode("utf-8") + b"\n")
                if mod_model:
                    modification_probabilities += _validate_modified_tags(read, modification)
                if duplex:
                    dx = read.get_tag("dx") if read.has_tag("dx") else None
                    _require(type(dx) is int and dx in {-1, 0, 1}, "invalid native duplex dx classification")
                    duplex_counts[{-1: "duplex_parent", 0: "simplex", 1: "duplex"}[dx]] += 1
                if not duplex and params.get("emit_summary", True) is not False:
                    _require(not read.is_secondary and not read.is_supplementary,
                             "native simplex unreferenced summary requires primary calls")
                    parent = read.get_tag("pi") if read.has_tag("pi") else read.query_name
                    qscore = float(read.get_tag("qs")) if read.has_tag("qs") else 0.0
                    summary_records[(read.query_name, parent, read.query_length, f"{qscore:.6f}")] += 1
                for tag in tags:
                    tags[tag] += int(read.has_tag(tag))
        _require(_digest(handle) == bam_sha, "native calls BAM changed during semantic validation")
    read_count = len(names)
    if mod_model:
        _require(read_count > 0, "modified native stdout has no calls")
    _require(type(calls["read_count"]) is int and calls["read_count"] == read_count,
             "native calls read count disagrees with runtime provenance")
    _require(hashlib.sha256(b"".join(sorted(names))).hexdigest() == calls["read_inventory_sha256"],
             "native calls read inventory disagrees with runtime provenance")
    if duplex:
        counts = calls.get("duplex_read_counts")
        _require(read_count > 0 and isinstance(counts, dict) and counts == duplex_counts
                 and all(type(value) is int for value in counts.values()), "native duplex class counts disagree with BAM")
    _require(calls["move_tags"] == tags and type(calls["duplex_dx1"]) is int
             and calls["duplex_dx1"] == (duplex_counts["duplex"] if duplex else 0),
             "native calls tag counts disagree with runtime provenance")
    if params["emit_moves"]:
        _require(all(count == read_count for count in tags.values()), "native calls lack requested move tags")
    summary_state, summary_artifact = _validate_summary(root, params, runtime, summary_records)
    artifacts = []
    for relative in _BASECALL_OUTPUTS:
        digest, size = _identity(root / relative)
        artifacts.append({"path": relative, "sha256": digest, "size_bytes": size})
    _require(artifacts[0]["sha256"] == bam_sha and artifacts[1]["sha256"] == producer_log_sha
             and artifacts[2]["sha256"] == preflight_sha
             and artifacts[3]["sha256"] == runtime_sha, "native products changed during validation")
    if summary_artifact is not None:
        _require(_identity(root / _SUMMARY_PATH) == (summary_artifact["sha256"], summary_artifact["size_bytes"]),
                 "native summary changed before artifact publication")
        artifacts.append(summary_artifact)
    return {
        "state": "validated", "partial": False, "result_kind": "ont_native_basecall",
        "workflow_id": workflow, "input_mode": "pod5", "read_count": read_count,
        "calls_bam_sha256": bam_sha, "preflight_sha256": preflight_sha,
        "runtime_provenance_sha256": runtime_sha, "input_inventory_sha256": input_sha,
        "sample_sheet_sha256": sheet_sha,
        **({"modified_bases": {"selection": modification, "model_id": mod_model["id"],
                               "model_aggregate_sha256": mod_model["aggregate_sha256"],
                               "probability_count": modification_probabilities}} if mod_model else {}),
        **({"pairs_sha256": pairs_sha, "duplex_read_counts": duplex_counts} if duplex else {}),
        "effective_params_sha256": hashlib.sha256(rfc8785.dumps(params)).hexdigest(),
        "summary_state": summary_state, "summary_read_count": read_count if summary_artifact else None,
        "artifacts": artifacts,
        "artifact_set_sha256": hashlib.sha256(rfc8785.dumps(artifacts)).hexdigest(),
    }


_ALIGN_OUTPUTS = (
    "align/aligned.bam", "align/aligned.bam.bai", "align/reference.fasta",
    "align/reference.fasta.fai", "align/align.log",
)


def _validate_native_reference(handles: dict, params: dict, reference_identity: tuple) -> tuple:
    """Shared published-reference authority for native reference-owning stages."""
    expected_reference = params["reference_sequence_sha256"]
    ref = handles["align/reference.fasta"]
    contigs, sequence = ngs_alignment_sessions._fasta_contigs_from_handle(ref)
    ref.seek(0)
    _require(sum(line.lstrip().startswith(b">") for line in ref) == 1 and len(contigs) == 1
             and bool(sequence) and re.fullmatch(b"[ACGTN]+", sequence) is not None,
             "native reference must match the emitter's single-record alphabet")
    _require(hashlib.sha256(sequence).hexdigest() == expected_reference,
             "native reference sequence differs from accepted authority")
    selected = Path(params["reference_fasta"])
    _require(selected.is_absolute() and _identity(selected) == reference_identity,
             "native published reference differs from selected input")
    with pysam.FastaFile(f"/proc/self/fd/{handles['align/reference.fasta'].fileno()}", filepath_index=f"/proc/self/fd/{handles['align/reference.fasta.fai'].fileno()}") as fasta:
        _require(tuple(fasta.references) == tuple(contigs)
                 and tuple(fasta.lengths) == tuple(length for length, _ in contigs.values())
                 and fasta.fetch(next(iter(contigs))).upper().encode("ascii") == sequence,
                 "native FASTA index disagrees with reference")
    return contigs, sequence


def _alignment_read_identity(read: pysam.AlignedSegment) -> tuple:
    # Locked Minimap2Aligner restores forward sequence/qualities and copies
    # caller aux tags onto primary alignments. Alignment-derived tags are not
    # immutable; these basecall/move/duplex/modification fields are.
    retained = {"MM", "ML", "MN", "mv", "ts", "ns", "dx", "qs", "pi", "ch", "st", "du", "mx", "sm", "sd", "sv"}
    qualities = read.get_forward_qualities()
    return (read.query_name, read.get_forward_sequence(),
            tuple(qualities) if qualities is not None else None,
            tuple(sorted((tag, repr(value), kind) for tag, value, kind in read.get_tags(with_value_type=True) if tag in retained)))


def _validate_reference_alignment(root: Path, persisted: Path, job: Any,
                                  basecall: dict[str, Any], *, source_path: Path | None = None) -> dict[str, Any]:
    """Consume DoradoAlign's existing snapshot receipt, not presentation readiness.

    dorado_align.nf:73-143 authenticates reference/source snapshots and records
    the effective MAPQ filter and record counts. POD5 does not emit qc_manifest.
    """
    from contextlib import ExitStack

    params = job.params
    terminal = job.provenance["stage_terminal_states"]["dorado_align"]
    _require(terminal["status"] == "complete", "native alignment stage is not complete")
    outputs = terminal["outputs"]
    _require(isinstance(outputs, list) and tuple(
        _resolve_terminal_output(value, root, persisted, stage="dorado_align")[1]
        for value in outputs) == _ALIGN_OUTPUTS, "native alignment output contract mismatch")
    min_mapq = params.get("bam_min_mapq", 0)
    _require(type(min_mapq) is int and 0 <= min_mapq <= 255, "invalid native MAPQ setting")
    expected_reference = params["reference_sequence_sha256"]
    _require(isinstance(expected_reference, str) and re.fullmatch(r"[0-9a-f]{64}", expected_reference) is not None,
             "native reference authority is invalid")
    with ExitStack() as stack:
        handles = {relative: stack.enter_context(ngs_alignment_sessions._open_regular_file_no_symlinks(root / relative))
                   for relative in _ALIGN_OUTPUTS}
        identities = {relative: (_digest(handle), os.fstat(handle.fileno()).st_size)
                      for relative, handle in handles.items()}
        contigs, sequence = _validate_native_reference(handles, params, identities["align/reference.fasta"])
        keys = {
            "source_sha256_before", "source_sha256_after", "source_immutable",
            "reference_raw_sha256_before", "reference_raw_sha256_after", "reference_sequence_sha256",
            "reference_immutable", "bam_min_mapq", "input_records", "output_records",
        }
        receipt: dict[str, str] = {}
        log_text = handles["align/align.log"].read().decode("utf-8")
        _validate_producer(log_text, ("modules/ngs/dorado_align.nf",), ("dorado", "samtools"))
        for line in log_text.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in keys:
                _require(key not in receipt, "duplicate native alignment receipt field")
                receipt[key] = value
        _require(set(receipt) == keys, "native alignment snapshot receipt is incomplete")
        _require(receipt["source_sha256_before"] == receipt["source_sha256_after"] == basecall["calls_bam_sha256"]
                 and receipt["source_immutable"] == receipt["reference_immutable"] == "true"
                 and receipt["reference_raw_sha256_before"] == receipt["reference_raw_sha256_after"] == identities["align/reference.fasta"][0]
                 and receipt["reference_sequence_sha256"] == expected_reference
                 and receipt["bam_min_mapq"] == str(min_mapq)
                 and receipt["input_records"] == str(basecall["read_count"]),
                 "native alignment receipt disagrees with source/reference/settings")
        fdpath = lambda relative: f"/proc/self/fd/{handles[relative].fileno()}"
        external = source_path is not None
        with ngs_alignment_sessions._open_regular_file_no_symlinks(source_path if external else root / "basecall/calls.bam") as source:
            _require(_digest(source) == basecall["calls_bam_sha256"], "basecall source changed before alignment validation")
            source_modifications: Counter = Counter()
            modification = params.get("modified_bases", "none")
            with pysam.AlignmentFile(source, "rb", check_sq=False) as calls:
                source_reads: Counter = Counter()
                source_identities: Counter = Counter()
                source_records = 0
                for read in calls.fetch(until_eof=True):
                    source_records += 1
                    # Locked aligner defaults to skipping input secondary/supplementary
                    # records and recovers original orientation before realignment.
                    if external and (read.is_secondary or read.is_supplementary):
                        continue
                    source_reads[(read.query_name, read.get_forward_sequence())] += 1
                    source_identities[_alignment_read_identity(read)] += 1
                    if modification != "none":
                        source_modifications[_modified_record(read, modification)] += 1
            _require(source_records == basecall["read_count"], "alignment source record count differs from receipt")
            _require(_digest(source) == basecall["calls_bam_sha256"], "basecall source changed during alignment validation")
        source_names = {key[0] for key in source_reads}
        primary: Counter = Counter()
        primary_modifications: Counter = Counter()
        primary_identities: Counter = Counter()
        sequential: Counter = Counter()
        index_counts: Counter = Counter()
        mapped = count = no_coordinate = 0
        with pysam.AlignmentFile(fdpath("align/aligned.bam"), "rb", index_filename=fdpath("align/aligned.bam.bai"), require_index=True) as bam:
            _require(bam.is_bam and bam.check_index(), "native aligned BAM/index is invalid")
            sq = bam.header.to_dict().get("SQ", [])
            _require(len(sq) == len(contigs) and all(
                item["SN"] in contigs and item["LN"] == contigs[item["SN"]][0]
                and ("M5" not in item or item["M5"].lower() == contigs[item["SN"]][1]) for item in sq),
                "native alignment/reference contigs disagree")
            for read in bam.fetch(until_eof=True):
                count += 1
                _require(read.query_name in source_names and read.mapping_quality >= min_mapq,
                         "native aligned read is foreign or violates MAPQ filter")
                if read.reference_id < 0:
                    no_coordinate += 1
                else:
                    index_counts[(read.reference_name, read.is_unmapped)] += 1
                if not read.is_secondary and not read.is_supplementary:
                    primary[(read.query_name, read.get_forward_sequence())] += 1
                    primary_identities[_alignment_read_identity(read)] += 1
                    if modification != "none":
                        # Minimap2Aligner.cpp:409-432 copies input aux tags even
                        # on reverse primary alignments. Non-primary hard-clipped
                        # records may lose MM/ML/MN; do not demand tags on those.
                        primary_modifications[_modified_record(read, modification)] += 1
                elif modification != "none" and any(read.has_tag(tag) for tag in ("MM", "ML", "MN")):
                    # With soft clipping enabled non-primary records retain the
                    # same source tags. Partial, malformed or altered tags are
                    # not the source-permitted all-three-removed outcome.
                    _require(_modified_record(read, modification) in source_modifications,
                             "native non-primary modified tags disagree with basecalls")
                if not read.is_unmapped:
                    _require(read.reference_start >= 0 and read.reference_end is not None
                             and read.reference_end <= bam.lengths[read.reference_id],
                             "native alignment coordinates are invalid")
                    mapped += 1
                    sequential[read.to_string()] += 1
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name)
                              if not read.is_unmapped)
            _require(indexed == sequential and bam.nocoordinate == no_coordinate and all(
                item.mapped == index_counts[(item.contig, False)]
                and item.unmapped == index_counts[(item.contig, True)] for item in bam.get_index_statistics()),
                "native BAM index disagrees with decoded records")
        _require(not (primary - source_reads) and (min_mapq > 0 or primary == source_reads),
                 "native primary alignment inventory disagrees with basecalls")
        _require(not (primary_identities - source_identities) and (min_mapq > 0 or primary_identities == source_identities),
                 "native primary alignment quality/caller-tag inventory differs from source")
        if modification != "none":
            _require(not (primary_modifications - source_modifications)
                     and (min_mapq > 0 or primary_modifications == source_modifications),
                     "native primary alignment modified tags disagree with basecalls")
        _require(receipt["output_records"] == str(count), "native alignment output count mismatch")
        for relative, handle in handles.items():
            _require((_digest(handle), os.fstat(handle.fileno()).st_size) == identities[relative]
                     and _identity(root / relative) == identities[relative],
                     "native alignment artifact changed during validation")
    artifacts = [{"path": relative, "sha256": identities[relative][0], "size_bytes": identities[relative][1]}
                 for relative in _ALIGN_OUTPUTS]
    return {"reference_sequence_sha256": expected_reference,
            "source_bam_sha256": basecall["calls_bam_sha256"], "bam_min_mapq": min_mapq,
            "record_count": count, "mapped_records": mapped, "artifacts": artifacts}


_DEMUX_OUTPUTS = (
    "demux/demux_manifest.json", "demux/per_barcode_units.json", "demux/demux/units",
)


def _demux_record(read: pysam.AlignedSegment) -> tuple:
    # --no-classify forwards the BAM record; samtools merge may remap RG/PG.
    # Compare scientific tags, sequence, qualities and flags, not header IDs.
    return (read.query_name, read.query_sequence, tuple(read.query_qualities or ()),
            read.flag, read.reference_id, read.reference_start, read.mapping_quality,
            read.cigarstring, read.next_reference_id, read.next_reference_start,
            read.template_length, tuple(sorted((tag, repr(value), kind)
                for tag, value, kind in read.get_tags(with_value_type=True) if tag not in {"RG", "PG"})))


def _demux_label(read: pysam.AlignedSegment, kit: str, aliases: dict[str, str]) -> str:
    # Dorado 1.3.1 Structure.cpp:152-182, BC then al then SM, absent -> unclassified.
    label = next((read.get_tag(tag) for tag in ("BC", "al", "SM")
                  if read.has_tag(tag) and read.get_tag(tag)), "unclassified")
    _require(isinstance(label, str), "native barcode tag is not a string")
    if label.startswith(kit + "_"):
        label = label[len(kit) + 1:]
    label = aliases.get(label, label)
    _require(re.fullmatch(r"unclassified|barcode(?:0[1-9]|[1-8][0-9]|9[0-6])", label) is not None,
             "native barcode tag disagrees with accepted kit/labels")
    return label


def _validate_demux(root: Path, persisted: Path, job: Any,
                    basecall: dict[str, Any]) -> dict[str, Any]:
    """Validate the producer's unit receipts and exact native read partition."""
    stages = job.provenance["stage_terminal_states"]
    terminal = stages["dorado_demux"]
    _require("dorado_align" not in stages, "barcoded DNA does not execute reference alignment")
    outputs = terminal["outputs"]
    _require(terminal["status"] == "complete" and isinstance(outputs, list) and len(outputs) == 3,
             "native demux terminal output contract mismatch")
    _require(tuple(_resolve_terminal_output(value, root, persisted, stage="dorado_demux")[1]
                   for value in outputs[:2]) == _DEMUX_OUTPUTS[:2]
             and outputs[2] in {str(persisted / _DEMUX_OUTPUTS[2]),
                                f"bms_results/{persisted.name}/{_DEMUX_OUTPUTS[2]}"},
             "native demux terminal output contract mismatch")
    manifest, manifest_sha = _document(root / _DEMUX_OUTPUTS[0])
    units_doc, units_sha = _document(root / _DEMUX_OUTPUTS[1])
    _validate_producer(manifest["producer_receipt"], ("modules/ngs/dorado_basecall.nf",), ("dorado", "samtools"))
    units = manifest["units"]
    _require(manifest["schema"] == "biomodstack.dorado_demux.v1"
             and manifest["barcode_classification_source"] == "dorado_basecaller_inline"
             and manifest["preflight_sha256"] == basecall["preflight_sha256"]
             and manifest["source_calls"]["sha256"] == basecall["calls_bam_sha256"]
             and type(manifest["source_calls"]["read_count"]) is int
             and manifest["source_calls"]["read_count"] == basecall["read_count"]
             and type(manifest["total_reads"]) is int
             and manifest["total_reads"] == basecall["read_count"], "native demux source receipt mismatch")
    _require(isinstance(units, list) and bool(units)
             and units_doc == {"schema": "biomodstack.dorado_barcode_units.v1", "units": units},
             "native demux unit inventories disagree")
    identities = {}
    artifacts = []
    def artifact(relative: str, expected: str) -> None:
        identity = _identity(root / relative)
        _require(identity[0] == expected, "native demux artifact identity mismatch")
        identities[relative] = identity
        artifacts.append({"path": relative, "sha256": identity[0], "size_bytes": identity[1]})
    artifact(_DEMUX_OUTPUTS[0], manifest_sha)
    artifact(_DEMUX_OUTPUTS[1], units_sha)
    preflight, preflight_sha = _document(root / "basecall/dorado_preflight.json")
    _require(preflight_sha == basecall["preflight_sha256"], "native demux preflight changed")
    sheet = preflight["barcoding"]["sample_sheet"]
    barcode_aliases = {item["barcode"]: item["alias"] for item in sheet["assignments"]} if sheet else {}
    aliases = {alias: barcode for barcode, alias in barcode_aliases.items()}
    with ngs_alignment_sessions._open_regular_file_no_symlinks(root / "basecall/calls.bam") as handle:
        _require(_digest(handle) == basecall["calls_bam_sha256"], "native demux source changed")
        with pysam.AlignmentFile(handle, "rb", check_sq=False) as bam:
            expected = Counter((_demux_label(read, job.params["barcode_kit"], aliases), _demux_record(read))
                               for read in bam.fetch(until_eof=True))
        _require(_digest(handle) == basecall["calls_bam_sha256"], "native demux source changed")
    observed = Counter()
    labels = set()
    for unit in units:
        label = unit["unit_id"]
        _require(isinstance(label, str) and re.fullmatch(r"unclassified|barcode(?:0[1-9]|[1-8][0-9]|9[0-6])", label) is not None
                 and label not in labels, "native demux unit label invalid or duplicate")
        labels.add(label)
        bam_path = f"demux/units/{label}.bam"
        receipt_path = f"demux/manifests/{label}.json"
        _require(unit["bam_path"] == bam_path and unit["unit_manifest_path"] == receipt_path
                 and unit["sample_alias"] == barcode_aliases.get(label)
                 and unit["source_calls_sha256"] == basecall["calls_bam_sha256"]
                 and unit["preflight_sha256"] == basecall["preflight_sha256"]
                 and type(unit["read_count"]) is int and unit["read_count"] >= 0
                 and unit["resubmission_params"] == {"bam_path": bam_path, "barcode_unit": label, "sample_alias": barcode_aliases.get(label)},
                 "native demux unit authority mismatch")
        receipt, receipt_sha = _document(root / "demux" / receipt_path)
        expected_receipt = {key: unit[key] for key in ("unit_id", "sample_alias", "bam_path", "bam_sha256",
                            "read_count", "source_calls_sha256", "preflight_sha256")}
        _require(receipt == {"schema": "biomodstack.dorado_barcode_unit.v1", **expected_receipt}
                 and type(receipt["read_count"]) is int and receipt_sha == unit["unit_manifest_sha256"],
                 "native demux unit receipt mismatch")
        artifact("demux/" + receipt_path, receipt_sha)
        artifact("demux/" + bam_path, unit["bam_sha256"])
        with ngs_alignment_sessions._open_regular_file_no_symlinks(root / "demux" / bam_path) as handle:
            _require(_digest(handle) == unit["bam_sha256"], "native demux BAM changed")
            with pysam.AlignmentFile(handle, "rb", check_sq=False) as bam:
                _require(bam.is_bam, "native demux unit is not BAM")
                records = Counter((label, _demux_record(read)) for read in bam.fetch(until_eof=True))
            _require(sum(records.values()) == unit["read_count"], "native demux unit read count mismatch")
            observed.update(records)
            _require(_digest(handle) == unit["bam_sha256"], "native demux BAM changed")
    _require(observed == expected, "native demux partition disagrees with source reads/classification")
    for directory, suffix in (("units", "bam"), ("manifests", "json")):
        path = root / "demux/demux" / directory
        _require(not path.is_symlink() and {entry.name for entry in path.iterdir()} ==
                 {f"{label}.{suffix}" for label in labels}, "native demux directory coverage mismatch")
    for relative, identity in identities.items():
        _require(_identity(root / relative) == identity, "native demux product changed during validation")
    _require(_identity(root / "basecall/calls.bam")[0] == basecall["calls_bam_sha256"], "native demux source changed")
    return {"read_count": basecall["read_count"], "unit_count": len(units),
            "demux_manifest_sha256": manifest_sha, "per_barcode_units_sha256": units_sha,
            "artifacts": artifacts}


def validate_native_basecall(job: Any) -> dict[str, Any]:
    """Use the shared persisted-root authority, pinned against pathname ABA."""
    descriptor = None
    try:
        persisted = resolve_persisted_job_result_root(job)
        descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        root = Path(f"/proc/self/fd/{descriptor}")
        result = _validate_basecall(root, persisted, job)
        if job.params.get("barcode_kit"):
            demux = _validate_demux(root, persisted, job, result)
            result["artifacts"].extend(demux.pop("artifacts"))
            result["demux"] = demux
            result["artifact_set_sha256"] = hashlib.sha256(rfc8785.dumps(result["artifacts"])).hexdigest()
        elif job.params.get("reference_fasta"):
            alignment = _validate_reference_alignment(root, persisted, job, result)
            result["artifacts"].extend(alignment.pop("artifacts"))
            result["alignment"] = alignment
            result["artifact_set_sha256"] = hashlib.sha256(rfc8785.dumps(result["artifacts"])).hexdigest()
        if result.get("pairs_sha256") is not None:
            _require(_identity(Path(job.params["duplex_pairs"]))[0] == result["pairs_sha256"],
                     "duplex pairs changed before publication")
        if result["sample_sheet_sha256"] is not None:
            _require(_identity(Path(job.params["sample_sheet"]))[0] == result["sample_sheet_sha256"],
                     "sample sheet changed before publication")
        return result
    except OntNgsCompletionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ngs_alignment_sessions.AlignmentSessionError) as exc:
        raise OntNgsCompletionError("native basecall package is missing, corrupt, or inconsistent") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_native_pod5_alignment_only(job: Any) -> dict[str, Any]:
    """Native alignment without an unexecuted QC or assembly verdict.

    ont_plasmid_qc.nf:108-161 runs QC only when requested; construct screening
    :113-124,199-259 runs optional assembly and FASTQ-only QC. Reuse the same
    Dorado/reference parsers without changing scientific producer behavior.
    This preparation is pure; only the caller's terminal CAS publishes it.
    """
    from services.ont_ngs_completion import ont_completion_lane

    descriptor = None
    try:
        _require(ont_completion_lane(job) == "native_pod5_alignment_only",
                 "job is outside the native POD5 alignment-only branches")
        persisted = resolve_persisted_job_result_root(job)
        descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        pinned_identity = os.fstat(descriptor)
        root = Path(f"/proc/self/fd/{descriptor}")
        stages = job.provenance["stage_terminal_states"]
        _require(set(stages) == {"dorado_basecall", "dorado_align"},
                 "alignment-only branch has missing or unexpected terminal stages")
        # Reject stale native products, not optional derived catalog/preview or
        # signal products. No derived store is consulted or made a prerequisite.
        from services.ont_ngs_completion import _REQUIRED_STAGE_OUTPUT_SUFFIXES
        unexecuted_products = [
            relative for stage in ("dimer_qc", "fastq_qc", "construct_verification")
            for relative in _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]
        ]
        unexecuted_products.extend((
            "fastq_qc/reads_for_qc.fastq", "fastq_qc/bam_to_fastq_for_qc.log",
            "assembly/wf_clone_out", "assembly/wf_clone.log",
            "comparison_panel/comparison_panel_summary.json",
            "comparison_panel/comparison_panel.bam", "comparison_panel/comparison_panel.bam.bai",
        ))
        for relative in unexecuted_products:
            path = root / relative
            _require(not path.exists() and not path.is_symlink(),
                     "alignment-only branch has unexecuted QC/assembly products")
        result = _validate_basecall(root, persisted, job)
        alignment = _validate_reference_alignment(root, persisted, job, result)
        result["artifacts"].extend(alignment.pop("artifacts"))
        construct = job.params["ont_workflow_id"] == "ont_construct_screening"
        qc_state = "not_applicable" if construct else "not_requested"
        reason = "construct_screening_qc_requires_fastq_input" if construct else "run_fastq_qc_disabled"
        result.update(
            result_kind="ont_native_alignment",
            alignment=alignment,
            alignment_state="validated",
            fastq_qc={"state": qc_state, "reason": reason},
            verification={"state": qc_state, "reason": reason},
            assembly={"state": "not_requested" if construct else "not_applicable"},
        )
        for artifact in result["artifacts"]:
            _require(_identity(root / artifact["path"]) == (artifact["sha256"], artifact["size_bytes"]),
                     "native alignment predecessor changed before publication")
        reference = next(item for item in result["artifacts"] if item["path"] == "align/reference.fasta")
        _require(_identity(Path(job.params["reference_fasta"])) == (reference["sha256"], reference["size_bytes"]),
                 "native selected reference changed before publication")
        current_identity = os.stat(persisted, follow_symlinks=False)
        _require((current_identity.st_dev, current_identity.st_ino) ==
                 (pinned_identity.st_dev, pinned_identity.st_ino),
                 "native alignment result root changed before publication")
        result["artifact_set_sha256"] = hashlib.sha256(rfc8785.dumps(result["artifacts"])).hexdigest()
        return result
    except OntNgsCompletionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ngs_alignment_sessions.AlignmentSessionError) as exc:
        raise OntNgsCompletionError("native POD5 alignment package is missing, corrupt, or inconsistent") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_pooled_producer(job: Any) -> dict[str, Any]:
    """Bind competitive-assignment execution to its native artifact manifest.

    This is producer evidence only, not a replacement for the pooled scientific
    occurrence/partition validator or authority to release any assigned reads.
    """
    persisted = resolve_persisted_job_result_root(job)
    descriptor = os.open(persisted, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        root = Path(f"/proc/self/fd/{descriptor}") / "pooled_reference_assignment"
        summary, summary_sha = _document(root / "assignment_summary.json")
        manifest, manifest_sha = _document(root / "intended_pool.igv_session.json")
        log_name = "pooled_reference_assignment.minimap2.log"
        log_id = _identity(root / log_name)
        with ngs_alignment_sessions._open_regular_file_no_symlinks(root / log_name) as handle:
            _require(_digest(handle) == log_id[0], "pooled producer log changed")
            producer = _validate_producer(handle.read().decode("utf-8"),
                ("workflows/ngs/ont_pooled_reference_assignment.nf", "scripts/pooled_ont_reference_assignment.py"),
                ("python3", "minimap2", "samtools"))
            _require(_digest(handle) == log_id[0], "pooled producer log changed during validation")
        _require(summary["artifacts"]["alignment_log"] == log_name
                 and summary["manifest_sha256"] == manifest["manifest_sha256"] == job.params["reference_set_manifest_sha256"]
                 and summary["scientific_status"] == manifest["scientific_status"] == "REVIEW"
                 and summary["release_state"] == manifest["release_state"] == "awaiting_operator_release",
                 "pooled producer reference/review authority mismatch")
        for kind, name, identity in (("alignment_log", log_name, log_id),
                                     ("assignment_summary", "assignment_summary.json", (summary_sha, _identity(root / "assignment_summary.json")[1]))):
            declared = [item for item in manifest["artifacts"] if item["kind"] == kind]
            _require(len(declared) == 1 and declared[0]["path"] == name
                     and declared[0]["sha256"] == identity[0], "pooled producer artifact binding mismatch")
        _require(_identity(root / log_name) == log_id
                 and _identity(root / "assignment_summary.json")[0] == summary_sha
                 and _identity(root / "intended_pool.igv_session.json")[0] == manifest_sha,
                 "pooled producer artifacts changed during validation")
        return {"state": "producer_identity_bound", "producer": producer,
                "assignment_summary_sha256": summary_sha, "igv_session_sha256": manifest_sha,
                "alignment_log_sha256": log_id[0]}
    finally:
        os.close(descriptor)
