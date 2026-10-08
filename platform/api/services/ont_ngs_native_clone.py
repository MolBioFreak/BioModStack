"""Read-only native clone assembly report/runtime authority.

Source: locked wf-clone-validation v1.8.4-bms.1, commit 7e6b7f0dfe31ee855ec1342c5ea8c5a73021d5a4.
The assembly-report parser and all-input adapter/ConstructVerify parser remain
separate. Scientific status is retained, not promoted to construct PASS.
"""
import csv
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
from types import SimpleNamespace

import pysam
from services import verified_native_reads as native
from collections import Counter
from contextlib import ExitStack

from services import ngs_alignment_sessions as files
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ont_ngs_native_completion import _document, _identity, _require, _resolve_terminal_output


def _sample(job):
    params = job.params
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(params.get("wf_clone_sample") or params.get("name") or
                  (f"nanopore_{job.id}" if job.id else "nanopore")))


def validate_clone_assembly(root, persisted, job):
    params = job.params
    sample = _sample(job)
    stage = job.provenance["stage_terminal_states"]["wf_clone_validation"]
    outputs = stage["outputs"]
    suffixes = ("assembly/wf_clone.log", "assembly/execution_receipt.json", "assembly/runtime_provenance.json",
                "assembly/wf_clone_out/wf-clone-validation-report.html", "assembly/wf_clone_out/sample_status.txt")
    if params["ont_workflow_id"] == "wf_clone_validation":
        suffixes += ("assembly/adapter/adapter_manifest.json", "verification/qc_manifest.json", "verification/verification_summary.tsv")
    _require(stage["status"] == "complete" and isinstance(outputs, list) and len(outputs) == len(suffixes) + 1
             and outputs[0] in {str(persisted / "assembly/wf_clone_out"), f"bms_results/{persisted.name}/assembly/wf_clone_out"}
             and tuple(_resolve_terminal_output(value, root, persisted, stage="wf_clone_validation")[1]
                       for value in outputs[1:]) == suffixes, "native assembly stage output contract mismatch")
    runtime, runtime_sha = _document(root / "assembly/runtime_provenance.json")
    lock_path = DORADO_LOCK_PATH.parents[2] / "config/ngs/wf_clone_validation_v1.8.4.lock.json"
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    _require(runtime["schema"] == "biomodstack.wf_clone_validation_runtime_provenance.v1"
             and runtime["validation_status"] == "valid"
             and runtime["lock"]["sha256"] == hashlib.sha256(lock_bytes).hexdigest()
             and runtime["upstream"] == lock["upstream"]
             and runtime["patched_source"] == {**lock["patched_source"], "status": "clean"}
             and runtime["compatibility_patch"]["sha256"] == lock["compatibility_patch"]["sha256"]
             and runtime["nextflow"] == lock["nextflow"]
             and runtime["selected_model_id"] == params.get("wf_clone_basecaller_model", lock["models"]["default"])
             and runtime["selected_model_id"] in lock["models"]["accepted_upstream_ids"]
             and runtime["network_policy"] == "forbidden" and runtime["nxf_offline"] is True,
             "native assembly runtime identity differs from lock/request")
    expected_images = [{"uri": image["uri"], "path": str(Path(lock["containers"]["cache_dir"]) / image["cache_file"]),
                        "sha256": image["sha256"]} for image in lock["containers"]["images"]]
    _require(runtime["images"] == expected_images
             and runtime["selected_model_path"] == str(Path(lock["models"]["store"]) / runtime["selected_model_id"]),
             "native assembly runtime image/model inventory mismatch")
    medaka = lock["models"]["medaka_consensus"]
    image_sha = next(image["sha256"] for image in expected_images if image["uri"] == medaka["image_uri"])
    _require(runtime["medaka_model"] == {**medaka, "image_sha256": image_sha,
             "selection_policy": "explicit_consensus_selector"}
             and medaka["selector"] == runtime["selected_model_id"] + ":consensus",
             "native polishing model content/selector identity mismatch")
    execution, execution_sha = _document(root / "assembly/execution_receipt.json")
    _require(execution["schema"] == "bms.ngs.clone-execution.v1" and execution["state"] == "succeeded"
             and type(execution["exit_code"]) is int and execution["exit_code"] == 0,
             "native nested assembly did not complete execution")
    _require(runtime["runtime_files"] == lock["runtime_files"]
             and execution["runtime_provenance_sha256"] == runtime_sha,
             "clone execution does not bind its validated runtime")
    source_names = [f"workflows/ngs/{params['ont_workflow_id']}.nf", "modules/ngs/clone_validation.nf",
                    "scripts/validate_wf_clone_runtime.py", "scripts/record_wf_clone_execution.py"]
    code = DORADO_LOCK_PATH.parents[2]
    _require(execution["executed_sources"] == {name: dict(zip(("sha256", "size_bytes"), _identity(code / name)))
                                              for name in source_names},
             "clone executed wrapper source differs from completion consumer")
    runtime_files = execution["runtime_files"]
    expected_runtime = {}
    for item in [runtime["lock"], runtime["compatibility_patch"], *runtime["images"],
                 *runtime["runtime_files"], *runtime["source_closure"]]:
        expected_runtime[item["path"]] = item["sha256"]
        _require(runtime_files[item["path"]]["sha256"] == item["sha256"]
                 and ("size_bytes" not in item or runtime_files[item["path"]]["size_bytes"] == item["size_bytes"]),
                 "clone runtime file is not execution-bound")
    extra = set(runtime_files) - set(expected_runtime)
    _require(len(extra) == 1 and runtime_files[next(iter(extra))] ==
             {"sha256": runtime_sha, "size_bytes": _identity(root / "assembly/runtime_provenance.json")[1]},
             "clone execution runtime file denominator mismatch")
    argv = execution["argv"]
    prefix = [lock["nextflow"]["executable"], "-log", "wf_clone.log", "run", lock["patched_source"]["path"],
              "-offline", "--disable_ping", "-profile", "singularity", "-w", "wf_clone_work"]
    _require(isinstance(argv, list) and argv[:len(prefix)] == prefix, "native assembly command/runtime mismatch")
    strings = {"--sample": sample, "--out_dir": "wf_clone_out",
               "--assembly_tool": params.get("wf_clone_assembly_tool", "flye"),
               "--flye_quality": params.get("wf_clone_flye_quality", "nano-hq"),
               "--override_basecaller_cfg": runtime["selected_model_id"]}
    numbers = {"--approx_size": ("wf_clone_approx_size", 7000), "--assm_coverage": ("wf_clone_assm_coverage", 60),
               "--min_quality": ("wf_clone_min_quality", 9), "--trim_length": ("wf_clone_trim_length", 0),
               "--cutsite_mismatch": ("wf_clone_cutsite_mismatch", 1), "--primer_mismatch": ("wf_clone_primer_mismatch", 2),
               "--expected_coverage": ("wf_clone_expected_coverage", 95), "--expected_identity": ("wf_clone_expected_identity", 99)}
    flags = {"--large_construct": "wf_clone_large_construct", "--non_uniform_coverage": "wf_clone_non_uniform_coverage",
             "--canu_fast": "wf_clone_canu_fast"}
    inputs = {"--bam": root / "align/aligned.bam", "--full_reference": Path(params["reference_fasta"])}
    for flag, key in (("--primers", "wf_clone_primers"), ("--insert_reference", "wf_clone_insert_reference"),
                      ("--host_reference", "wf_clone_host_reference"), ("--regions_bedfile", "wf_clone_regions_bedfile")):
        if params.get(key):
            inputs[flag] = Path(params[key])
    seen = set()
    tokens = iter(argv[len(prefix):])
    for flag in tokens:
        _require(flag not in seen, "duplicate native assembly command setting")
        seen.add(flag)
        if flag in flags:
            _require(params.get(flags[flag], False) is True, "unrequested assembly flag")
            continue
        value = next(tokens, None)
        _require(isinstance(value, str), "native assembly command is truncated")
        if flag in strings:
            _require(value == strings[flag], "native assembly string setting mismatch")
        elif flag in numbers:
            key, default = numbers[flag]
            _require(Decimal(value) == Decimal(str(params.get(key, default))), "native assembly numeric setting mismatch")
        elif flag in inputs:
            evidence = execution["inputs"][flag]
            identity = _identity(inputs[flag])
            _require((evidence["sha256"], evidence["size_bytes"]) == identity, "native assembly input identity mismatch")
            # Staged BAM paths belong to the task, never to the API filesystem.
            # Non-staged references must be the exact accepted selected input.
            if flag != "--bam":
                _require(Path(value) == inputs[flag], "native assembly selected file differs from request")
        else:
            _require(False, "unknown native assembly command argument")
    _require(seen == set(strings) | set(numbers) | set(inputs) | {flag for flag, key in flags.items() if params.get(key, False)}
             and set(execution["inputs"]) == set(inputs), "native assembly setting/input denominator mismatch")
    with files._open_regular_file_no_symlinks(root / "assembly/wf_clone_out/sample_status.txt") as handle:
        reader = csv.DictReader(io.StringIO(handle.read().decode("utf-8")))
        _require(reader.fieldnames and {"Sample", "Assembly completed / failed reason", "Length"} <= set(reader.fieldnames),
                 "native sample status columns mismatch")
        rows = list(reader)
    _require(len(rows) == 1 and None not in rows[0] and all(value is not None for value in rows[0].values())
             and rows[0]["Sample"] == sample and bool(rows[0]["Assembly completed / failed reason"]),
             "native sample status identity/row mismatch")
    status = rows[0]["Assembly completed / failed reason"]
    completed_statuses = {
        "Completed successfully", "Completed but failed to reconcile",
        "Completed but no annotations found in the database",
        "Insert found but does not align with provided reference",
    }
    failed_statuses = {
        "Failed to trim reads", "Failed to downsample reads", "Failed to Subset reads",
        "Failed to assemble using Flye", "Failed to assemble using Canu",
        "Failed to trim Assembly", "Failed to reconcile assemblies",
        "Failed due to filtered host reads", "Failed due to insufficient reads",
        "Failed to polish assembly with Medaka",
    }
    _require(status in completed_statuses | failed_statuses, "unknown pinned vendor sample status")
    if status in completed_statuses:
        validate_clone_products(root, sample)
        # normalize is a pure source parser, not the adapter writer. Construct
        # screening must validate the same native completed assembly products.
        producer = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/adapt_wf_clone_validation.py"))
        try:
            producer["normalize"](SimpleNamespace(
                result_root=root / "assembly/wf_clone_out", runtime_provenance=root / "assembly/runtime_provenance.json",
                sample=sample, execution_exit_code=0, full_reference_provided=True,
                source_bam=root / "align/aligned.bam", source_bai=root / "align/aligned.bam.bai"))
        except producer["AdapterFailure"] as exc:
            raise ValueError("completed assembly products contradict vendor status") from exc
    validate_clone_supporting_formats(root, sample, params)

    # Vendor status text records assembly, annotation and insert outcomes. A
    # successful execution with negative sample status is still a native result.
    with files._open_regular_file_no_symlinks(root / "assembly/wf_clone_out/wf-clone-validation-report.html") as handle:
        html = handle.read().decode("utf-8").lower()
        _require("<html" in html and "</html>" in html, "native assembly report is malformed HTML")
    artifact_paths = list(suffixes)
    tree = root / "assembly/wf_clone_out"
    _require(not tree.is_symlink() and tree.is_dir(), "unsafe native assembly output directory")
    for directory, dirs, names in os.walk(tree, followlinks=False):
        for entry in dirs + names:
            _require(not (Path(directory) / entry).is_symlink(), "native assembly output symlink")
        for entry in names:
            artifact_paths.append((Path(directory) / entry).relative_to(root).as_posix())
    artifacts = []
    for relative in sorted(set(artifact_paths)):
        digest, size = _identity(root / relative)
        artifacts.append({"path": relative, "sha256": digest, "size_bytes": size})
    _require(next(item["sha256"] for item in artifacts if item["path"] == "assembly/runtime_provenance.json") == runtime_sha
             and next(item["sha256"] for item in artifacts if item["path"] == "assembly/execution_receipt.json") == execution_sha,
             "native assembly receipt changed during parsing")
    return {"state": "validated", "sample_id": sample, "sample_status": status,
            "scientific_verdict": "REVIEW", "reason": "assembly_report_is_not_construct_verification",
            "runtime_provenance_sha256": runtime_sha, "execution_receipt_sha256": execution_sha,
            "artifacts": artifacts}





def validate_clone_supporting_formats(root, sample, params):
    # Native plannotate BED is five columns and can cross the circular origin;
    # do not reinterpret it as ordinary six-column, ordered-interval BED.
    from Bio import AlignIO, SeqIO
    from Bio.Seq import Seq
    from services.ont_ngs_native_plasmid import _table
    tree = root / "assembly/wf_clone_out"
    for directory, dirs, names in os.walk(tree, followlinks=False):
        for name in dirs + names:
            _require(not (Path(directory) / name).is_symlink(), "unsafe clone supporting output")
        for name in names:
            path = Path(directory) / name
            if name == f"{sample}.host.bam":
                _require(bool(params.get("wf_clone_host_reference")), "unrequested host alignment")
                validate_host_alignment(path, Path(params["wf_clone_host_reference"]))
            elif name.endswith(".host.bam.bai"):
                _require(path.with_suffix("").is_file(), "orphan host alignment index")
            elif name == f"{sample}.assembly.maf":
                with files._open_regular_file_no_symlinks(tree / (sample + ".final.fasta")) as handle:
                    contigs, sequence = files._fasta_contigs_from_handle(handle)
                _require(list(contigs) == [sample], "MAF assembly identity mismatch")
                with files._open_regular_file_no_symlinks(path) as handle:
                    for block in AlignIO.parse(io.StringIO(handle.read().decode("ascii")), "maf"):
                        _require(len(block) == 2, "native self-alignment MAF must have two sequences")
                        for record in block:
                            a = record.annotations
                            start, size, length, strand = a["start"], a["size"], a["srcSize"], a["strand"]
                            _require(record.id == sample and length == len(sequence) and strand in {-1, 1}
                                     and 0 <= start <= start + size <= length, "MAF assembly coordinates mismatch")
                            oriented = sequence.decode("ascii") if strand == 1 else str(Seq(sequence.decode("ascii")).reverse_complement())
                            _require(str(record.seq).replace("-", "").upper() == oriented[start:start + size],
                                     "MAF aligned bases differ from final assembly")
            elif name.endswith((".full_construct.stats", ".insert.stats")):
                key = "wf_clone_insert_reference" if name.endswith(".insert.stats") else "reference_fasta"
                _require(bool(params.get(key)), "unrequested clone variant statistics")
                validate_variant_stats(path, Path(params[key]))
            elif name.endswith(".bam.stats"):
                rows = _table(path, ("sample_name", "ref_coverage", "coverage", "acc"))
                for row in rows:
                    _require(row["sample_name"] == sample, "foreign native alignment statistics sample")
                    for column in ("ref_coverage", "coverage", "acc"):
                        value = Decimal(row[column])
                        _require(value.is_finite() and 0 <= value <= 100,
                                 "invalid native alignment percentage")
            elif name.endswith(".json"):
                with files._open_regular_file_no_symlinks(path) as handle:
                    json.load(handle)
            elif name.endswith((".fasta", ".fa", ".fastq", ".fq")):
                with files._open_regular_file_no_symlinks(path) as handle:
                    with native.fastx(handle) as records:
                        for record in records:
                            _require(bool(record.name) and bool(record.sequence), "empty clone sequence record")
                            if name.endswith((".fastq", ".fq")):
                                _require(record.quality is not None and len(record.quality) == len(record.sequence),
                                         "invalid clone FASTQ qualities")
            elif name.endswith(".tsv"):
                _table(path)
            elif name == "feature_table.txt":
                with files._open_regular_file_no_symlinks(path) as handle:
                    reader = csv.DictReader(io.StringIO(handle.read().decode("utf-8")))
                    _require(reader.fieldnames and {"Sample_name", "Feature", "Start Location", "End Location"} <= set(reader.fieldnames),
                             "invalid clone annotation feature table")
                    _require(all(None not in row and all(value is not None for value in row.values()) for row in reader),
                             "truncated clone annotation feature table")
            elif name.endswith(".annotations.bed"):
                with files._open_regular_file_no_symlinks(path) as handle:
                    for line in handle.read().decode("utf-8").splitlines():
                        fields = line.split("\t")
                        _require(len(fields) == 5 and fields[0] == sample and fields[4] in {"+", "-"}
                                 and int(fields[1]) >= 0 and int(fields[2]) >= 0, "invalid source-native annotation BED")
            elif name.endswith(".gbk"):
                with files._open_regular_file_no_symlinks(path) as handle:
                    records = list(SeqIO.parse(io.StringIO(handle.read().decode("utf-8")), "genbank"))
                _require(len(records) == 1 and bool(records[0].seq), "invalid clone annotation GenBank")
                with files._open_regular_file_no_symlinks(tree / (sample + ".final.fasta")) as handle:
                    _, sequence = files._fasta_contigs_from_handle(handle)
                _require(str(records[0].seq).upper().encode("ascii") == sequence,
                         "clone GenBank sequence differs from final assembly")



def validate_host_alignment(path, reference):
    """The vendor publishes mapped host records, not the retained read pool."""
    with ExitStack() as stack:
        ref = stack.enter_context(files._open_regular_file_no_symlinks(reference))
        contigs, _ = files._fasta_contigs_from_handle(ref)
        handle = stack.enter_context(files._open_regular_file_no_symlinks(path))
        index = stack.enter_context(files._open_regular_file_no_symlinks(Path(str(path) + ".bai")))
        with native.alignment(handle, index) as bam:
            _require(bam.is_bam and bam.check_index() and tuple(bam.references) == tuple(contigs)
                     and tuple(bam.lengths) == tuple(item[0] for item in contigs.values()),
                     "host BAM/reference dictionary mismatch")
            for sq in bam.header.to_dict().get("SQ", []):
                _require("M5" not in sq or sq["M5"].lower() == contigs[sq["SN"]][1],
                         "host BAM reference digest mismatch")
            records, counts = Counter(), Counter()
            previous = (-1, -1)
            for read in bam.fetch(until_eof=True):
                _require(not read.is_unmapped and read.reference_name in contigs and bool(read.query_name)
                         and read.reference_end is not None
                         and 0 <= read.reference_start < read.reference_end <= contigs[read.reference_name][0],
                         "invalid mapped host BAM record")
                coordinate = (read.reference_id, read.reference_start)
                _require(coordinate >= previous, "host BAM is not coordinate sorted")
                previous = coordinate
                records[read.to_string()] += 1
                counts[read.reference_name] += 1
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name))
            _require(indexed == records and bam.nocoordinate == 0 and all(
                item.mapped == counts[item.contig] and item.unmapped == 0 for item in bam.get_index_statistics()),
                "host BAM/index record mismatch")


def validate_variant_stats(path, reference):
    """Read bcftools' native sections; bind its basic counts to the paired BCF.

    These are format/identity checks, not a rerun of variant calling or a
    replacement for the vendor's variant classification/statistical analysis.
    """
    summary = {}
    identifiers = []
    transitions = []
    widths = {}
    with files._open_regular_file_no_symlinks(path) as handle:
        for line in handle.read().decode("utf-8").splitlines():
            if not line:
                continue
            if line.startswith("#"):
                definition = re.match(r"^# ([A-Z][A-Z0-9]*)\t", line)
                columns = [int(value) for value in re.findall(r"\[(\d+)\]", line)]
                if definition and columns:
                    widths[definition.group(1)] = max(columns)
                continue
            fields = line.split("\t")
            _require(len(fields) >= 3 and fields[1] == "0" and widths.get(fields[0]) == len(fields),
                     "invalid single-input bcftools statistics row")
            if fields[0] == "ID":
                _require(len(fields) == 3, "invalid bcftools input identity")
                identifiers.append(Path(fields[2]).name)
            elif fields[0] == "SN":
                _require(len(fields) == 4 and fields[2] not in summary and int(fields[3]) >= 0,
                         "invalid bcftools summary statistic")
                summary[fields[2]] = int(fields[3])
            elif fields[0] == "TSTV":
                _require(len(fields) == 8, "invalid bcftools transition statistics")
                values = [Decimal(value) for value in fields[2:]]
                _require(all(value.is_finite() and value >= 0 for value in values), "invalid bcftools transition values")
                transitions.append(values)
    bcf_path = path.with_suffix(".calls.bcf")
    _require(identifiers == [bcf_path.name] and len(transitions) == 1,
             "native variant statistics source/section mismatch")
    with ExitStack() as stack:
        handle = stack.enter_context(files._open_regular_file_no_symlinks(bcf_path))
        index = stack.enter_context(files._open_regular_file_no_symlinks(Path(str(bcf_path) + ".csi")))
        with native.variant(handle, index) as bcf:
            _require(bcf.is_bcf, "native statistics source is not BCF")
            ref = stack.enter_context(files._open_regular_file_no_symlinks(reference))
            contigs, sequence = files._fasta_contigs_from_handle(ref)
            offsets, offset = {}, 0
            for name, (length, _) in contigs.items():
                offsets[name] = offset
                offset += length
            _require(set(bcf.header.contigs) == set(contigs) and all(
                bcf.header.contigs[name].length == contigs[name][0] for name in contigs),
                "native calls/reference dictionary mismatch")
            records = Counter()
            for record in bcf:
                _require(record.contig in contigs and 0 <= record.start < record.stop <= contigs[record.contig][0],
                         "native calls/reference coordinate mismatch")
                start = offsets[record.contig] + record.start
                _require(record.ref.upper().encode("ascii") == sequence[start:start + len(record.ref)],
                         "native calls REF allele differs from selected reference")
                records[str(record)] += 1
            indexed = Counter(str(record) for name in bcf.header.contigs for record in bcf.fetch(name))
            _require(records == indexed and summary.get("number of records:") == sum(records.values())
                     and summary.get("number of samples:") == len(bcf.header.samples),
                     "native variant statistics/BCF/index count mismatch")


def validate_clone_products(root, sample):
    """Parse final sequence/alignment/calls for both assembly-consuming workflows."""
    from services.ont_ngs_native_plasmid import _fastq
    final_fasta = root / f"assembly/wf_clone_out/{sample}.final.fasta"
    final_fastq = root / f"assembly/wf_clone_out/{sample}.final.fastq"
    with files._open_regular_file_no_symlinks(final_fasta) as handle:
        contigs, sequence = files._fasta_contigs_from_handle(handle)
    fastq, _ = _fastq(final_fastq)
    _require(fastq == Counter({(sample, sequence.decode("ascii")): 1}), "clone final FASTQ differs from final FASTA")
    with files._open_regular_file_no_symlinks(root / "align/reference.fasta") as handle:
        reference_contigs, reference_sequence = files._fasta_contigs_from_handle(handle)
    names = [f"assembly/wf_clone_out/{sample}.bam", f"assembly/wf_clone_out/{sample}.bam.bai",
             f"assembly/wf_clone_out/{sample}.full_construct.calls.bcf", f"assembly/wf_clone_out/{sample}.full_construct.calls.bcf.csi"]
    with ExitStack() as stack:
        handles = {name: stack.enter_context(files._open_regular_file_no_symlinks(root / name)) for name in names}
        fd = lambda name: handles[name]
        with native.alignment(fd(names[0]), fd(names[1])) as bam:
            _require(bam.is_bam and bam.check_index() and tuple(bam.references) == tuple(reference_contigs)
                     and tuple(bam.lengths) == tuple(value[0] for value in reference_contigs.values()), "clone assembly BAM/reference dictionary mismatch")
            sequential, index_counts, primary_assembly = Counter(), Counter(), Counter()
            no_coordinate = 0
            for read in bam.fetch(until_eof=True):
                _require(read.query_name == sample, "foreign assembly alignment record")
                if not read.is_secondary and not read.is_supplementary:
                    primary_assembly[(read.query_name, (read.get_forward_sequence() or "").upper())] += 1
                if read.reference_id < 0:
                    no_coordinate += 1
                else:
                    index_counts[(read.reference_name, read.is_unmapped)] += 1
                if not read.is_unmapped:
                    _require(read.reference_start >= 0 and read.reference_end is not None and read.reference_end <= reference_contigs[read.reference_name][0],
                             "clone assembly BAM coordinate mismatch")
                    sequential[read.to_string()] += 1
            _require(primary_assembly == Counter({(sample, sequence.decode("ascii")): 1}), "assembly alignment differs from final consensus")
            indexed = Counter(read.to_string() for name in bam.references for read in bam.fetch(name) if not read.is_unmapped)
            _require(indexed == sequential and bam.nocoordinate == no_coordinate and all(
                item.mapped == index_counts[(item.contig, False)] and item.unmapped == index_counts[(item.contig, True)]
                for item in bam.get_index_statistics()), "clone assembly BAM/index record mismatch")
        with files._open_regular_file_no_symlinks(root / "align/reference.fasta") as handle:
            reference_contigs, reference_sequence = files._fasta_contigs_from_handle(handle)
        with native.variant(fd(names[2]), fd(names[3])) as bcf:
            _require(bcf.is_bcf, "clone full-reference calls are not BCF")
            records = Counter()
            for record in bcf:
                _require(record.contig in reference_contigs and 0 <= record.start < record.stop <= reference_contigs[record.contig][0],
                         "clone full-reference BCF coordinate mismatch")
                records[str(record)] += 1
            indexed = Counter(str(record) for name in bcf.header.contigs for record in bcf.fetch(name))
            _require(indexed == records, "clone full-reference BCF/CSI mismatch")
    return reference_contigs, reference_sequence


def validate_clone_adapter(root, persisted, job, alignment_result):
    """Consume the existing adapter's exact artifact/input denominator, read-only."""
    from services.ont_ngs_native_plasmid import _artifact, _fastq, _metrics, _table, validate_verification
    sample = _sample(job)
    relative = "assembly/adapter/adapter_manifest.json"
    adapter, adapter_sha = _document(root / relative)
    producer = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / "scripts/adapt_wf_clone_validation.py"))
    try:
        # normalize is the producer's pure parser, not its CLI writer. Do not
        # invoke main(), rebuild the adapter, or publish anything before CAS.
        expected = producer["normalize"](SimpleNamespace(
            result_root=root / "assembly/wf_clone_out", runtime_provenance=root / "assembly/runtime_provenance.json",
            sample=sample, execution_exit_code=0, full_reference_provided=True,
            source_bam=root / "align/aligned.bam", source_bai=root / "align/aligned.bam.bai"))
    except producer["AdapterFailure"] as exc:
        raise ValueError("native clone adapter source products are inconsistent") from exc
    for key in ("schema", "adapter_version", "executed_sources", "execution", "upstream_sample_status", "scientific_verdict",
                "scientific_reason_codes", "sample", "runtime_provenance", "missing_evidence_reasons", "contradictory_evidence_reasons"):
        _require(adapter[key] == expected[key], "native clone adapter semantics disagree with producer/source")
    def identity_records(records):
        return sorted((item["kind"], item["sha256"], item["size_bytes"]) for item in records)
    _require(identity_records(adapter["authoritative_inputs"]) == identity_records(expected["authoritative_inputs"]),
             "clone adapter authoritative BAM/index identity mismatch")
    def artifact_records(records):
        return sorted((item["kind"], None if item["kind"] == "runtime_provenance" else item["path"],
                       item["required"], item["sha256"], item["size_bytes"]) for item in records)
    _require(artifact_records(adapter["artifacts"]) == artifact_records(expected["artifacts"]),
             "clone adapter upstream artifact denominator/identity mismatch")
    artifacts = [_artifact(root, relative)]
    _require(artifacts[0]["sha256"] == adapter_sha, "clone adapter changed during normalization")
    reference_contigs, reference_sequence = validate_clone_products(root, sample)
    # The adapter deliberately emits header-only dimer tables: do not invent
    # multimer support or use FASTQ-QC evidence in their place.
    for name, columns in (("dimer_breakpoint_call.tsv", ("breakpoint_status", "confidence", "primary_breakpoint_in_boundary_window")),
                          ("dimer_secondary_summary.tsv", ("aligned_dimer_reads", "non_boundary_split_reads"))):
        _require(not _table(root / "assembly/adapter" / name, columns), "clone adapter dimer placeholders contain invented evidence")
        artifacts.append(_artifact(root, "assembly/adapter/" + name))
    support_relative = "assembly/adapter/per_base_support.tsv"
    support = _table(root / support_relative, ("chrom", "position_1based", "reference_base", "depth"))
    reference_name = next(iter(reference_contigs))
    _require([(row["chrom"], int(row["position_1based"]), row["reference_base"]) for row in support] ==
             [(reference_name, pos, chr(base)) for pos, base in enumerate(reference_sequence, 1)], "clone support reference coordinates mismatch")
    primary = Counter()
    counts = Counter()
    with files._open_regular_file_no_symlinks(root / "align/aligned.bam") as handle:
        with native.alignment(handle) as bam:
            for read in bam.fetch(until_eof=True):
                if not read.is_secondary and not read.is_supplementary:
                    primary[(read.query_name, (read.get_forward_sequence() or "").upper())] += 1
                    counts["total_reads"] += 1
                    counts["unmapped_reads" if read.is_unmapped else "mapped_reads"] += 1
    input_dir = "assembly/adapter/verification_input"
    reads, reads_identity = _fastq(root / input_dir / "source_reads.fastq")
    _require(reads == primary, "clone adapter retained FASTQ differs from primary alignment occurrences")
    stats_relative = "assembly/adapter/alignment_stats.tsv"
    stats = _metrics(root / stats_relative)
    _require(stats == {key: str(counts[key]) for key in ("total_reads", "mapped_reads", "unmapped_reads")},
             "clone adapter alignment statistics mismatch")
    artifacts.extend(_artifact(root, name) for name in (support_relative, stats_relative))
    verification = validate_verification(
        root, persisted, job, alignment_result, reads_identity=reads_identity, input_dir=input_dir,
        method="wf_clone_validation_final_assembly", consensus_relative=f"assembly/wf_clone_out/{sample}.final.fasta",
        support_relative=support_relative, stats_relative=stats_relative,
        breakpoint_relative="assembly/adapter/dimer_breakpoint_call.tsv",
        secondary_relative="assembly/adapter/dimer_secondary_summary.tsv")
    artifacts.extend(verification.pop("artifacts"))
    return {**verification, "adapter_manifest_sha256": adapter_sha, "artifacts": artifacts}
