#!/usr/bin/env python3
"""Bounded one-record Squigulator ideal-signal producer."""
from __future__ import annotations

import argparse
import array
from contextlib import contextmanager
import hashlib
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_FILE_BYTES = 16 * 1024 * 1024
_ACTIVE_BROKER_PARENTS: "BrokerParents | None" = None
MAX_TOTAL_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_BROKER_MESSAGE_BYTES = 256 * 1024
MAX_BROKER_PARENTS = 32
MAX_COMBINED_LOG_BYTES = 4 * 1024 * 1024
PROFILE_KMER_LENGTH = {
    "dna-r9-min": 6, "dna-r9-prom": 6,
    "rna-r9-min": 5, "rna-r9-prom": 5,
    "dna-r10-min": 9, "dna-r10-prom": 9,
    "rna004-min": 9, "rna004-prom": 9,
}
PROFILE_CALIBRATION = {
    "dna-r9-min": (8192, 13.7222605, 1443.030273, 4000),
    "dna-r9-prom": (2048, -237.4102, 748.5801, 4000),
    "rna-r9-min": (8192, 4.65491888, 1126.47, 3012),
    "rna-r9-prom": (2048, -231.9440589, 548.788269, 3000),
    "dna-r10-min": (8192, 13.380569389019, 1536.598389, 5000),
    "dna-r10-prom": (2048, -127.5655735, 281.345551, 5000),
    "rna004-min": (8192, 12.47686423863, 1437.976685, 4000),
    "rna004-prom": (2048, -259.421128, 299.432068, 4000),
}
OUTPUT_NAMES = frozenset({
    "simulation_input.fasta", "simulation_coordinate_map.json",
    "simulated.blow5", "simulated.blow5.idx", "simulated_reads.fasta",
    "simulated_source.paf", "simulated_normalized.paf", "simulated_source.sam",
    "simulated_normalized.sam", "simulated_read_id_map.json", "producer_manifest.json",
})
OWNER_MARKER_NAME = ".owner"
MAX_OWNER_MARKER_BYTES = 128
KIND_BY_NAME = {
    "simulation_input.fasta": "simulation_input_fasta",
    "simulation_coordinate_map.json": "simulation_coordinate_map",
    "simulated.blow5": "simulated_blow5",
    "simulated.blow5.idx": "simulated_blow5_index",
    "simulated_reads.fasta": "simulated_read_fasta",
    "simulated_read_id_map.json": "simulated_read_id_map",
    "simulated_source.paf": "simulated_source_paf",
    "simulated_normalized.paf": "simulated_normalized_paf",
    "simulated_source.sam": "simulated_source_sam",
    "simulated_normalized.sam": "simulated_normalized_sam",
    "producer_manifest.json": "producer_manifest",
}
MEDIA_BY_NAME = {
    ".fasta": "text/x-fasta", ".paf": "text/plain", ".sam": "text/x-sam",
    ".blow5": "application/octet-stream", ".idx": "application/octet-stream",
    ".json": "application/json",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def reverse_complement(sequence: str) -> str:
    sequence = sequence.upper()
    if not sequence or re.fullmatch(r"[ACGTN]+", sequence) is None:
        raise ValueError("simulation sequence must contain only A, C, G, T, or N")
    return sequence.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]


def virtual_sequence_id(reference_digest: str, contig: str, start: int, end: int, orientation: str) -> str:
    if HEX64.fullmatch(reference_digest) is None or not 1 <= start <= end or orientation not in {"forward", "reverse"}:
        raise ValueError("simulation coordinate authority is invalid")
    payload = f"{reference_digest}\0{contig}\0{start}\0{end}\0{orientation}".encode()
    return f"bms-sim-{hashlib.sha256(payload).hexdigest()[:32]}"


def build_squigulator_argv(
    *, profile_id: str, seed: int, input_fasta: str = "/parents/simulation_input.fasta",
    output_root: str = "/output",
) -> list[str]:
    if not re.fullmatch(r"(?:dna-r9-(?:min|prom)|rna-r9-(?:min|prom)|dna-r10-(?:min|prom)|rna004-(?:min|prom))", profile_id):
        raise ValueError("unsupported Squigulator profile")
    if isinstance(seed, bool) or not 1 <= seed <= 2_147_483_647:
        raise ValueError("seed must be between 1 and 2147483647")
    return [
        "squigulator", "-x", profile_id, "--full-contigs", "--ideal",
        "--seed", str(seed), "-t", "1", "-K", "1",
        "-q", f"{output_root}/simulated_reads.fasta",
        "-c", f"{output_root}/simulated_source.paf", "--paf-ref",
        "-a", f"{output_root}/simulated_source.sam",
        input_fasta, "-o", f"{output_root}/simulated.blow5",
    ]


def _read_reference_window(path: Path, contig: str, start: int, end: int) -> tuple[str, int]:
    info = path.stat()
    broker_owned_alias = _ACTIVE_BROKER_PARENTS is not None and _ACTIVE_BROKER_PARENTS.owns_alias_path(path)
    if not path.is_file() or (path.is_symlink() and not broker_owned_alias) or not 1 <= info.st_size <= MAX_INPUT_BYTES:
        raise ValueError("managed reference violates bounded regular-file policy")
    found: list[str] = []
    current: str | None = None
    chunks: list[str] = []
    length = 0
    for raw in path.read_text(encoding="ascii").splitlines():
        if raw.startswith(">"):
            if current == contig:
                found.append("".join(chunks))
            current = raw[1:].split()[0] if raw[1:].strip() else None
            chunks = []
        elif current == contig:
            value = raw.strip().upper()
            if re.fullmatch(r"[ACGTN]*", value) is None:
                raise ValueError("managed reference contains unsupported sequence symbols")
            chunks.append(value)
        if current is not None:
            length += 0
    if current == contig:
        found.append("".join(chunks))
    if len(found) != 1 or end > len(found[0]):
        raise ValueError("managed reference contig interval does not resolve exactly once")
    return found[0][start - 1:end], len(found[0])


def _single_fasta(path: Path) -> tuple[str, str]:
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) < 2 or not lines[0].startswith(">") or any(line.startswith(">") for line in lines[1:]):
        raise ValueError("simulated read FASTA must contain exactly one record")
    read_id = lines[0][1:].split()[0]
    sequence = "".join(lines[1:]).upper()
    if not read_id or re.fullmatch(r"[ACGTN]+", sequence) is None:
        raise ValueError("simulated read FASTA is malformed")
    return read_id, sequence


def _parse_paf(
    path: Path, generated_id: str, input_id: str, length: int, kmer_length: int
) -> list[str]:
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) != 1:
        raise ValueError("Squigulator PAF must contain exactly one record")
    fields = lines[0].split("\t")
    if len(fields) < 12 or fields[0] != generated_id or fields[5] != input_id:
        raise ValueError("Squigulator PAF identity binding is invalid")
    signal_length, query_start, query_end = map(int, fields[1:4])
    target_length, target_start, target_end = map(int, fields[6:9])
    expected_target_length = length - kmer_length + 1
    if (
        signal_length <= 0 or query_start != 0 or query_end != signal_length
        or expected_target_length <= 0
        or (target_length, target_start, target_end) != (expected_target_length, 0, expected_target_length)
        or tuple(map(int, fields[9:11])) != (expected_target_length, expected_target_length)
    ):
        raise ValueError("Squigulator PAF coordinate span is invalid")
    tags = {value[:2]: value for value in fields[12:] if len(value) >= 5 and value[2] == ":"}
    if not {"ss", "sc", "sh"} <= set(tags):
        raise ValueError("Squigulator PAF lacks ss, sc, or sh truth")
    dwell = tags["ss"].removeprefix("ss:Z:").rstrip(",").split(",")
    if len(dwell) != expected_target_length or sum(map(int, dwell)) != signal_length:
        raise ValueError("Squigulator PAF dwell truth diverges from signal coordinates")
    return fields


def _normalize_paf(fields: list[str], *, contig: str, contig_length: int, start: int, end: int, orientation: str) -> str:
    normalized = list(fields)
    normalized[4] = "+" if orientation == "forward" else "-"
    target_span = int(fields[8]) - int(fields[7])
    if target_span <= 0 or start - 1 + target_span > end:
        raise ValueError("Squigulator normalized PAF span is invalid")
    normalized[5:9] = [contig, str(contig_length), str(start - 1), str(start - 1 + target_span)]
    normalized.extend([f"or:Z:{orientation}", f"vw:i:{start - 1}"])
    return "\t".join(normalized) + "\n"


def _normalize_sam(
    source: Path, destination: Path, *, generated_id: str, contig: str,
    contig_length: int, start: int, orientation: str, sequence: str,
    paf_fields: list[str] | None = None,
) -> None:
    lines = [line for line in source.read_text(encoding="ascii").splitlines() if line]
    sequence_headers = [line.split("\t") for line in lines if line.startswith("@SQ\t")]
    records = [line for line in lines if not line.startswith("@")]
    if len(records) != 1:
        raise ValueError("Squigulator SAM must contain exactly one alignment record")
    fields = records[0].split("\t")
    if len(sequence_headers) != 1 or len(sequence_headers[0]) != 3:
        raise ValueError("Squigulator SAM header identity is invalid")
    header = {item[:2]: item[3:] for item in sequence_headers[0][1:] if len(item) > 3 and item[2] == ":"}
    if header.get("LN") != str(len(sequence)) or not header.get("SN"):
        raise ValueError("Squigulator SAM header identity is invalid")
    if len(fields) < 13 or fields[0] != generated_id or fields[9].upper() != sequence:
        raise ValueError("Squigulator SAM sequence identity is invalid")
    try:
        flag, position, mapq, mate_position, template_length = map(
            int, (fields[1], fields[3], fields[4], fields[7], fields[8])
        )
    except ValueError as exc:
        raise ValueError("Squigulator SAM alignment shape is invalid") from exc
    if (
        flag != 0 or fields[2] != header["SN"] or position != 1 or mapq != 255
        or fields[5] != f"{len(sequence)}M" or fields[6] != "*"
        or mate_position != 0 or template_length != 0 or fields[10] != "*"
    ):
        raise ValueError("Squigulator SAM alignment shape is invalid")
    tags: dict[str, str] = {}
    for tag in fields[11:]:
        parts = tag.split(":", 2)
        if len(parts) != 3 or parts[0] in tags:
            raise ValueError("Squigulator SAM truth tags are invalid")
        tags[parts[0]] = f"{parts[1]}:{parts[2]}"
    if set(tags) != {"si", "ss"} or not tags["si"].startswith("Z:") or not tags["ss"].startswith("Z:"):
        raise ValueError("Squigulator SAM truth tags are invalid")
    try:
        signal_interval = [int(value) for value in tags["si"][2:].split(",")]
        dwell = [int(value) for value in tags["ss"][2:].rstrip(",").split(",")]
    except ValueError as exc:
        raise ValueError("Squigulator SAM truth tags are invalid") from exc
    if len(signal_interval) != 4 or any(value < 0 for value in signal_interval) or not dwell or any(value <= 0 for value in dwell):
        raise ValueError("Squigulator SAM truth tags are invalid")
    if paf_fields is not None:
        paf_tags = {tag[:2]: tag[5:] for tag in paf_fields[12:] if len(tag) > 5 and tag[2:5] == ":Z:"}
        expected_interval = [int(paf_fields[2]), int(paf_fields[3]), int(paf_fields[7]), int(paf_fields[8])]
        if signal_interval != expected_interval or tags["ss"] != f"Z:{paf_tags.get('ss', '')}":
            raise ValueError("Squigulator SAM truth diverges from validated PAF")
    fields[1] = str((flag | 16) if orientation == "reverse" else (flag & ~16))
    fields[2], fields[3] = contig, str(start)
    destination.write_text(
        f"@HD\tVN:1.6\n@SQ\tSN:{contig}\tLN:{contig_length}\n" + "\t".join(fields) + "\n",
        encoding="ascii",
    )


def validate_blow5(path: Path, index: Path, generated_id: str) -> dict[str, Any]:
    try:
        import pyslow5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pyslow5 is required for governed BLOW5 validation") from exc
    if not index.is_file() or index.is_symlink() or index.stat().st_size <= 0:
        raise ValueError("simulated BLOW5 adjacent index is unavailable")
    handle = pyslow5.Open(str(path), "r")
    try:
        read_ids, count = handle.get_read_ids()
        record = handle.get_read(generated_id, pA=True, aux="all")
        header = handle.get_all_headers()
    finally:
        handle.close()
    if count != 1 or read_ids != [generated_id] or not isinstance(record, dict):
        raise ValueError("simulated BLOW5 must contain exactly the generated read")
    signal = record.get("signal")
    if signal is None or len(signal) <= 0 or record.get("len_raw_signal") != len(signal):
        raise ValueError("simulated BLOW5 signal is empty or length-divergent")
    required = {"digitisation", "offset", "range", "sampling_rate"}
    if not required <= set(record) or not isinstance(header, dict):
        raise ValueError("simulated BLOW5 calibration or run header identity is incomplete")
    if str(header.get("sample_frequency")) != str(int(float(record["sampling_rate"]))):
        raise ValueError("simulated BLOW5 sample-frequency authority diverges")
    return {
        "record_count": 1,
        "read_id": generated_id,
        "signal_length": len(signal),
        "calibration_fields": {name: record[name] for name in sorted(required)},
        "header_fields": header,
    }


def validate_profile_signal_receipt(
    receipt: dict[str, Any], paf_fields: list[str], profile_id: str,
) -> None:
    signal_span = int(paf_fields[3]) - int(paf_fields[2])
    if receipt.get("signal_length") != signal_span:
        raise ValueError("simulated BLOW5 signal length diverges from PAF/SAM truth")
    calibration = receipt.get("calibration_fields")
    expected_calibration = PROFILE_CALIBRATION[profile_id]
    try:
        calibration_matches = isinstance(calibration, dict) and all(
            abs(float(calibration[name]) - float(expected)) <= 1e-6
            for name, expected in zip(
                ("digitisation", "offset", "range", "sampling_rate"),
                expected_calibration,
                strict=True,
            )
        )
    except (KeyError, TypeError, ValueError):
        calibration_matches = False
    if not calibration_matches:
        raise ValueError("simulated BLOW5 calibration diverges from selected profile")


def validate_output_tree(output: Path) -> dict[str, dict[str, int | str]]:
    artifacts: dict[str, dict[str, int | str]] = {}
    total = 0
    for path in output.iterdir():
        info = path.lstat()
        if path.name == OWNER_MARKER_NAME:
            if path.is_symlink() or not path.is_file() or not (0 < info.st_size <= MAX_OWNER_MARKER_BYTES):
                raise RuntimeError("producer output ownership marker is invalid")
            continue
        if path.name not in OUTPUT_NAMES or path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unexpected producer output: {path.name}")
        if info.st_size <= 0 or info.st_size > MAX_OUTPUT_FILE_BYTES:
            raise RuntimeError(f"producer output violates file-size policy: {path.name}")
        total += info.st_size
        if total > MAX_TOTAL_OUTPUT_BYTES:
            raise RuntimeError("producer total output exceeds bounded policy")
        artifacts[path.name] = {"sha256": sha(path), "size_bytes": info.st_size}
    return artifacts


def _artifact(path: Path, kind: str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    suffix = ".blow5" if path.name.endswith(".blow5") else path.suffix
    return {"kind": kind, "filename": path.name, "media_type": MEDIA_BY_NAME[suffix],
            "sha256": sha(path), "size_bytes": path.stat().st_size,
            "validation_receipt": receipt or {}}


def run_bounded_command(
    command: list[str], *, timeout: float, log_limit_bytes: int
) -> subprocess.CompletedProcess[bytes]:
    if log_limit_bytes < 0:
        raise ValueError("producer log budget is invalid")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    observed = 0
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    getattr(key.fileobj, "close")()
                    continue
                observed += len(chunk)
                if observed > log_limit_bytes:
                    raise RuntimeError("Squigulator combined log limit exceeded")
                streams[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        returncode = process.wait(timeout=remaining)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            if not pipe.closed:
                pipe.close()
    return subprocess.CompletedProcess(
        command,
        returncode,
        bytes(streams["stdout"]),
        bytes(streams["stderr"]),
    )


def produce_comparison(*, reference_fasta: Path, output: Path, reference_sha256: str,
                       contig: str, window_start: int, window_end: int, orientation: str,
                       profile_id: str, seed: int) -> dict[str, Any]:
    if sha(reference_fasta) != reference_sha256 or HEX64.fullmatch(reference_sha256) is None:
        raise ValueError("managed reference digest authority diverged")
    sequence, contig_length = _read_reference_window(reference_fasta, contig, window_start, window_end)
    if orientation == "reverse":
        sequence = reverse_complement(sequence)
    elif orientation != "forward":
        raise ValueError("simulation orientation is invalid")
    output.mkdir(parents=True, exist_ok=True)
    input_id = virtual_sequence_id(reference_sha256, contig, window_start, window_end, orientation)
    input_path = output / "simulation_input.fasta"
    input_path.write_text(f">{input_id}\n{sequence}\n", encoding="ascii")
    coordinate = {
        "schema": "bms.ont-simulation-coordinate-map.v1",
        "virtual": {"sequence_id": input_id, "start": 1, "end": len(sequence)},
        "source": {"contig": contig, "start": window_start, "end": window_end,
                   "orientation": orientation, "reference_sha256": reference_sha256},
    }
    write_json(output / "simulation_coordinate_map.json", coordinate)
    command = build_squigulator_argv(
        profile_id=profile_id, seed=seed, input_fasta=str(input_path), output_root=str(output)
    )
    completed = run_bounded_command(
        command, timeout=300, log_limit_bytes=MAX_COMBINED_LOG_BYTES
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-4096:].decode("utf-8", "replace") or "Squigulator failed")
    blow5 = output / "simulated.blow5"
    index = output / "simulated.blow5.idx"
    if index.exists():
        index_receipt = {"created_by": "squigulator", "returncode": 0}
    else:
        index_command = ["bms-slow5-index", str(blow5)]
        indexed = run_bounded_command(
            index_command,
            timeout=60,
            log_limit_bytes=MAX_COMBINED_LOG_BYTES
            - len(completed.stdout)
            - len(completed.stderr),
        )
        if indexed.returncode != 0:
            raise RuntimeError(
                indexed.stderr[-4096:].decode("utf-8", "replace")
                or "BLOW5 index creation failed"
            )
        index_receipt = {
            "created_by": "bms-slow5-index",
            "argv": index_command,
            "returncode": indexed.returncode,
            "stdout_sha256": hashlib.sha256(indexed.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(indexed.stderr).hexdigest(),
        }
    generated_id, generated_sequence = _single_fasta(output / "simulated_reads.fasta")
    if generated_sequence != sequence:
        raise ValueError("Squigulator perfect-read sequence diverges from simulation input")
    paf_fields = _parse_paf(
        output / "simulated_source.paf", generated_id, input_id, len(sequence),
        PROFILE_KMER_LENGTH[profile_id],
    )
    (output / "simulated_normalized.paf").write_text(
        _normalize_paf(paf_fields, contig=contig, contig_length=contig_length,
                       start=window_start, end=window_end, orientation=orientation), encoding="ascii")
    _normalize_sam(output / "simulated_source.sam", output / "simulated_normalized.sam",
                   generated_id=generated_id, contig=contig, contig_length=contig_length,
                   start=window_start, orientation=orientation, sequence=sequence,
                   paf_fields=paf_fields)
    relation = {"input_sequence_id": input_id, "generated_read_id": generated_id}
    write_json(output / "simulated_read_id_map.json", {"schema": "bms.ont-simulated-read-id-map.v1", **relation})
    blow5_receipt = validate_blow5(blow5, index, generated_id)
    validate_profile_signal_receipt(blow5_receipt, paf_fields, profile_id)
    artifacts = [
        _artifact(input_path, "simulation_input_fasta", {"reference_sha256": reference_sha256}),
        _artifact(output / "simulation_coordinate_map.json", "simulation_coordinate_map", coordinate),
        _artifact(blow5, "simulated_blow5", blow5_receipt),
        _artifact(index, "simulated_blow5_index", {"adjacent_to": "simulated.blow5"}),
        _artifact(output / "simulated_reads.fasta", "simulated_read_fasta", relation),
        _artifact(output / "simulated_read_id_map.json", "simulated_read_id_map", relation),
        _artifact(output / "simulated_source.paf", "simulated_source_paf", {"preserved": True}),
        _artifact(output / "simulated_normalized.paf", "simulated_normalized_paf", coordinate),
        _artifact(output / "simulated_source.sam", "simulated_source_sam", {"preserved": True}),
        _artifact(output / "simulated_normalized.sam", "simulated_normalized_sam", coordinate),
    ]
    manifest = {
        "schema": "bms.ont-squigulator-producer-manifest.v1", "virtual_sequence_id": input_id,
        "generated_read_id_relation": relation, "coordinate_map": coordinate,
        "command": {"argv": command, "profile_id": profile_id, "seed": seed,
                    "mode": "ideal", "full_contigs": True, "threads": 1, "batch_size": 1,
                    "index": index_receipt},
        "parents": {"reference_fasta_sha256": reference_sha256}, "artifacts": artifacts,
    }
    write_json(output / "producer_manifest.json", manifest)
    validate_output_tree(output)
    return manifest


class BrokerParents:
    def __init__(self, metadata: dict[str, Any], descriptors: list[int]):
        self.metadata, self.descriptors = metadata, descriptors
        self.temp = tempfile.TemporaryDirectory(prefix="bms-squigulator-parents-")
        self.root = Path(self.temp.name)
        self.paths: dict[str, Path] = {}
        for item, descriptor in zip(metadata["parents"], descriptors, strict=True):
            alias = str(item.get("alias", ""))
            if not alias or Path(alias).name != alias:
                raise ValueError("broker parent alias is invalid")
            offset = 0
            digest = hashlib.sha256()
            while chunk := os.pread(descriptor, 1024 * 1024, offset):
                digest.update(chunk); offset += len(chunk)
            if digest.hexdigest() != item.get("sha256") or offset != item.get("size_bytes"):
                raise ValueError("broker parent digest authority diverged")
            path = self.root / alias
            path.symlink_to(f"/proc/self/fd/{descriptor}")
            self.paths[alias] = path

    def owns_alias_path(self, path: Path) -> bool:
        return path in self.paths.values()

    def close(self) -> None:
        for descriptor in self.descriptors:
            os.close(descriptor)
        self.temp.cleanup()


@contextmanager
def receive(socket_path: Path, timeout: float) -> Iterator[tuple[dict[str, Any], BrokerParents]]:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path)); server.listen(1); server.settimeout(timeout)
        connection, _ = server.accept()
        with connection:
            payload, ancillary, flags, _ = connection.recvmsg(
                MAX_BROKER_MESSAGE_BYTES + 1,
                socket.CMSG_SPACE(MAX_BROKER_PARENTS * array.array("i").itemsize),
            )
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("broker request was truncated")
        descriptors: list[int] = []
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i"); values.frombytes(data[:len(data) - len(data) % values.itemsize]); descriptors.extend(values)
        metadata = json.loads(payload)
        if metadata.get("schema") != "bms.ont-signal-fd-broker.v1" or len(descriptors) != len(metadata.get("parents", [])):
            raise ValueError("broker request schema or descriptor count is invalid")
        parents = BrokerParents(metadata, descriptors)
        try:
            yield metadata, parents
        finally:
            parents.close()
    finally:
        server.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation")
    produce = sub.add_parser("produce")
    produce.add_argument("--reference-fasta", type=Path, required=True)
    produce.add_argument("--reference-sha256", required=True)
    produce.add_argument("--contig", required=True); produce.add_argument("--window-start", type=int, required=True)
    produce.add_argument("--window-end", type=int, required=True); produce.add_argument("--orientation", choices=("forward", "reverse"), required=True)
    produce.add_argument("--profile-id", required=True); produce.add_argument("--seed", type=int, required=True)
    broker = sub.add_parser("broker"); broker.add_argument("--socket", type=Path, required=True); broker.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.operation == "produce":
        produce_comparison(reference_fasta=args.reference_fasta, output=Path("/output"),
                           reference_sha256=args.reference_sha256, contig=args.contig,
                           window_start=args.window_start, window_end=args.window_end,
                           orientation=args.orientation, profile_id=args.profile_id, seed=args.seed)
        return 0
    if args.operation == "broker":
        with receive(args.socket, args.timeout_seconds) as (metadata, parents):
            operation = list(metadata["operation_argv"])
            if not operation or operation[0] != "produce":
                raise ValueError("producer broker operation is invalid")
            translated = [str(parents.paths[value.removeprefix("/parents/")]) if isinstance(value, str) and value.startswith("/parents/") else value for value in operation]
            global _ACTIVE_BROKER_PARENTS
            _ACTIVE_BROKER_PARENTS = parents
            try:
                return main(translated)
            finally:
                _ACTIVE_BROKER_PARENTS = None
    parser.print_help(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
