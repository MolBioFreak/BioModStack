#!/usr/bin/env python3
"""Closed, offline Squigualiser runtime entrypoint used only by the BMS leased worker."""
from __future__ import annotations

import argparse
import array
from contextlib import contextmanager
import hashlib
import html
import json
import math
import os
import re
import selectors
import signal
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pysam
import pyslow5

MAX_STDERR = 64 * 1024
MAX_COMMAND_LOG_BYTES = 8 * 1024 * 1024
DEFAULT_COMMAND_DEADLINE_SECONDS = 4 * 60 * 60
MAX_PAF_LINE_BYTES = 16 * 1024 * 1024
MAX_HTML_BYTES = 48 * 1024 * 1024
MAX_SVG_BYTES = 4 * 1024 * 1024
MAX_RENDER_TOTAL_BYTES = 64 * 1024 * 1024
CSS_EXTERNAL_RESOURCE = re.compile(r"(?:@import\s+(?:url\()?|url\()\s*['\"]?(?:https?:)?//", re.IGNORECASE)
MAX_BROKER_MESSAGE_BYTES = 256 * 1024
MAX_BROKER_PARENTS = 128
_ACTIVE_BROKER_REQUEST: Any = None


class ActiveResourceAudit(HTMLParser):
    """Reject network-active markup while allowing inert URLs inside bundled JavaScript."""

    RESOURCE_ATTRIBUTES = {
        "script": ("src",),
        "link": ("href",),
        "img": ("src", "srcset"),
        "iframe": ("src",),
        "object": ("data",),
        "embed": ("src",),
        "source": ("src", "srcset"),
        "video": ("src", "poster"),
        "audio": ("src",),
        "track": ("src",),
        "form": ("action",),
        "base": ("href",),
        "image": ("href", "xlink:href"),
        "use": ("href", "xlink:href"),
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []
        self._in_style = False

    @staticmethod
    def _local_resource(value: str) -> bool:
        normalized = value.strip().lower()
        return not normalized or normalized.startswith(("data:", "blob:", "#"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        values = {name.lower(): value or "" for name, value in attrs}
        if lowered == "style":
            self._in_style = True
        if lowered == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.violations.append("meta-refresh")
        for attribute in self.RESOURCE_ATTRIBUTES.get(lowered, ()):
            value = values.get(attribute)
            if value is not None and not self._local_resource(value):
                self.violations.append(f"{lowered}[{attribute}]")
        inline_style = values.get("style")
        if inline_style and CSS_EXTERNAL_RESOURCE.search(inline_style):
            self.violations.append(f"{lowered}[style]")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and CSS_EXTERNAL_RESOURCE.search(data):
            self.violations.append("style-resource")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor_identity(descriptor: int) -> tuple[str, int, tuple[int, int, int, int]]:
    before = os.fstat(descriptor)
    if not os.path.exists(f"/proc/self/fd/{descriptor}") or not stat_is_regular(before.st_mode):
        raise ValueError("broker descriptor is not a retained regular file")
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("broker descriptor changed while hashing")
    return digest.hexdigest(), offset, identity


def stat_is_regular(mode: int) -> bool:
    import stat

    return stat.S_ISREG(mode)


class BrokerRequest:
    def __init__(self, metadata: dict[str, Any], fds: list[int]) -> None:
        self.fds = fds
        self.operation_argv = list(metadata["operation_argv"])
        self._temporary = tempfile.TemporaryDirectory(prefix="bms-ont-parent-aliases-")
        self._root = Path(self._temporary.name)
        self._aliases: dict[str, tuple[Path, int, tuple[int, int, int, int]]] = {}
        try:
            for item, descriptor in zip(metadata["parents"], fds, strict=True):
                alias = item["alias"]
                if (
                    not isinstance(alias, str)
                    or not alias
                    or alias in {".", ".."}
                    or "/" in alias
                    or "\\" in alias
                    or len(alias) > 128
                ):
                    raise ValueError("broker parent alias is invalid")
                actual_sha256, actual_size, identity = _descriptor_identity(descriptor)
                if actual_sha256 != item.get("sha256") or actual_size != item.get("size_bytes"):
                    raise ValueError("broker parent digest or size mismatch")
                alias_path = self._root / alias
                alias_path.symlink_to(f"/proc/self/fd/{descriptor}")
                self._aliases[alias] = (alias_path, descriptor, identity)
            rewritten: list[str] = []
            for value in self.operation_argv:
                if not isinstance(value, str) or "\x00" in value:
                    raise ValueError("broker operation argument is invalid")
                if value.startswith("/parents/"):
                    alias = value.removeprefix("/parents/")
                    if alias not in self._aliases:
                        raise ValueError("broker operation references an unknown parent alias")
                    value = str(self._aliases[alias][0])
                rewritten.append(value)
            self.operation_argv = rewritten
            self.verify_aliases()
        except BaseException:
            self.close()
            raise

    def alias_path(self, alias: str) -> Path:
        return self._aliases[alias][0]

    def owns_alias_path(self, path: Path) -> bool:
        self.verify_aliases()
        return any(alias_path == path for alias_path, _descriptor, _identity in self._aliases.values())

    def read_alias(self, alias: str) -> bytes:
        self.verify_aliases()
        descriptor = self._aliases[alias][1]
        size = self._aliases[alias][2][2]
        return os.pread(descriptor, size, 0)

    def verify_aliases(self) -> None:
        for alias_path, descriptor, expected_identity in self._aliases.values():
            try:
                if not alias_path.is_symlink() or os.readlink(alias_path) != f"/proc/self/fd/{descriptor}":
                    raise ValueError("broker parent alias was substituted")
                current = os.fstat(descriptor)
            except OSError as exc:
                raise ValueError("broker parent alias or descriptor disappeared") from exc
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != expected_identity:
                raise ValueError("broker parent descriptor identity changed")

    def close(self) -> None:
        for descriptor in self.fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.fds = []
        self._temporary.cleanup()

    def __enter__(self) -> "BrokerRequest":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


@contextmanager
def receive_fd_request(connection: socket.socket, *, timeout_seconds: float):
    connection.settimeout(timeout_seconds)
    try:
        payload, ancillary, flags, _address = connection.recvmsg(
            MAX_BROKER_MESSAGE_BYTES + 1,
            socket.CMSG_SPACE(MAX_BROKER_PARENTS * array.array("i").itemsize),
        )
    except socket.timeout as exc:
        raise TimeoutError("broker descriptor request timed out") from exc
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(payload) > MAX_BROKER_MESSAGE_BYTES:
        raise ValueError("broker descriptor request exceeds its bound")
    received: list[int] = []
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(data[: len(data) - (len(data) % values.itemsize)])
            received.extend(values.tolist())
    try:
        metadata = json.loads(payload)
        parents = metadata.get("parents") if isinstance(metadata, dict) else None
        argv = metadata.get("operation_argv") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema") != "bms.ont-signal-fd-broker.v1"
            or not isinstance(parents, list)
            or not isinstance(argv, list)
            or not 1 <= len(parents) <= MAX_BROKER_PARENTS
            or len(received) != len(parents)
        ):
            raise ValueError("broker descriptor count or request schema is invalid")
        request = BrokerRequest(metadata, received)
        received = []
        with request:
            yield request
    finally:
        for descriptor in received:
            try:
                os.close(descriptor)
            except OSError:
                pass


def verify_active_broker_aliases() -> None:
    if _ACTIVE_BROKER_REQUEST is not None:
        _ACTIVE_BROKER_REQUEST.verify_aliases()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def bounded_binary_lines(handle: Any):
    while line := handle.readline(MAX_PAF_LINE_BYTES + 1):
        if len(line) > MAX_PAF_LINE_BYTES or not line.endswith(b"\n"):
            raise ValueError("PAF record exceeds the bounded line policy")
        yield line


def run(
    command: list[str],
    *,
    deadline_seconds: float = DEFAULT_COMMAND_DEADLINE_SECONDS,
    max_log_bytes: int = MAX_COMMAND_LOG_BYTES,
) -> dict[str, Any]:
    """Run one pinned tool with bounded incremental pipe consumption."""
    if deadline_seconds <= 0 or max_log_bytes <= 0:
        raise ValueError("runtime command bounds must be positive")
    verify_active_broker_aliases()
    pass_fds = () if _ACTIVE_BROKER_REQUEST is None else tuple(_ACTIVE_BROKER_REQUEST.fds)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        pass_fds=pass_fds,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    digests = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
    tails = {"stdout": bytearray(), "stderr": bytearray()}
    counts = {"stdout": 0, "stderr": 0}
    deadline = time.monotonic() + deadline_seconds
    failure: str | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "runtime command deadline exceeded"
                break
            for key, _mask in selector.select(timeout=min(0.25, remaining)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stream = str(key.data)
                counts[stream] += len(chunk)
                digests[stream].update(chunk)
                tails[stream].extend(chunk)
                if len(tails[stream]) > MAX_STDERR:
                    del tails[stream][:-MAX_STDERR]
                if counts[stream] > max_log_bytes:
                    failure = f"runtime command {stream} log output limit exceeded"
                    break
            if failure is not None:
                break
        if failure is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            returncode = process.wait(timeout=5)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    receipt = {
        "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
        "returncode": returncode,
        "stdout_sha256": digests["stdout"].hexdigest(),
        "stdout_size_bytes": counts["stdout"],
        "stdout_tail": bytes(tails["stdout"]).decode("utf-8", "replace"),
        "stderr_sha256": digests["stderr"].hexdigest(),
        "stderr_size_bytes": counts["stderr"],
        "stderr_tail": bytes(tails["stderr"]).decode("utf-8", "replace"),
    }
    if failure is not None:
        raise RuntimeError(failure)
    if returncode != 0:
        raise RuntimeError(receipt["stderr_tail"] or "runtime command failed")
    return receipt


def selected_read_fasta(bam_path: Path, read_id: str, output_path: Path) -> None:
    """Materialize one governed basecalled sequence without invented quality data."""
    found = 0
    with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as bam, output_path.open("w", encoding="utf-8") as handle:
        for record in bam.fetch(until_eof=True):
            if record.query_name != read_id:
                continue
            sequence = record.query_sequence or ""
            if not sequence:
                raise ValueError(f"selected read has no basecalled sequence: {read_id}")
            handle.write(f">{read_id}\n{sequence}\n")
            found += 1
    if found != 1:
        raise ValueError(f"selected read must resolve to one governed basecall record: {read_id}")


def reads_overlapping_region(
    mapping: Path,
    region: str,
    limit: int,
    strand: str,
    molecule_type: str = "dna",
) -> list[str]:
    match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", region)
    if match is None:
        raise ValueError("reference region must be contig:start-end")
    contig, start_text, end_text = match.groups()
    start, end = int(start_text), int(end_text)
    if start < 1 or end < start:
        raise ValueError("reference region is invalid")
    selected: list[str] = []
    seen: set[str] = set()
    expected_strand = "-" if strand == "reverse" else "+"
    if not mapping.name.endswith(".gz") or not Path(f"{mapping}.tbi").is_file():
        raise ValueError("reference mapping requires its governed tabix index")
    with pysam.TabixFile(str(mapping)) as tabix:
        for line in tabix.fetch(contig, start - 1, end):
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12 or fields[5] != contig or fields[4] != expected_strand:
                continue
            try:
                first, second = int(fields[7]), int(fields[8])
                target_start, target_end = (first + 1, second) if molecule_type == "dna" else (second + 1, first)
            except ValueError as exc:
                raise ValueError("indexed mapping contains non-numeric coordinates") from exc
            if target_end < start or target_start > end or fields[0] in seen:
                continue
            selected.append(fields[0])
            seen.add(fields[0])
            if len(selected) >= limit:
                break
    if not selected:
        raise ValueError("no governed signal-aligned reads overlap the requested region")
    return selected


def bounded_blow5(parents: list[Path], read_ids: list[str], work_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Extract only bounded requested reads, then merge the non-empty partitions for one plot input."""
    read_list = work_dir / "bounded_read_ids.txt"
    read_list.write_text("".join(f"{read_id}\n" for read_id in read_ids), encoding="utf-8")
    subsets: list[Path] = []
    receipts: list[dict[str, Any]] = []
    for index, parent in enumerate(parents):
        subset = work_dir / f"subset-{index}.blow5"
        receipt = run([
            "slow5tools", "get", "--skip", "--to", "blow5", "-l", str(read_list),
            "-o", str(subset), str(parent),
        ])
        slow5 = pyslow5.Open(str(subset), "r")
        values, count = slow5.get_read_ids()
        slow5.close()
        if not isinstance(values, list) or not isinstance(count, int) or count != len(values):
            raise ValueError("bounded BLOW5 subset returned an invalid read inventory")
        receipt["selected_read_count"] = count
        receipts.append(receipt)
        if count:
            subsets.append(subset)
    if not subsets:
        raise ValueError("bounded BLOW5 extraction found none of the requested reads")
    merged = work_dir / "bounded.blow5"
    if len(subsets) == 1:
        subsets[0].replace(merged)
        receipts.append({"operation": "single_nonempty_partition", "source_partition_count": len(parents)})
    else:
        receipts.append(run(["slow5tools", "merge", "-o", str(merged), *map(str, subsets)]))
    receipts.append(run(["slow5tools", "index", str(merged)]))
    available = set(blow5_ids([merged])[0])
    if available != set(read_ids):
        raise ValueError("bounded BLOW5 extraction did not preserve the exact requested read set")
    slow5 = pyslow5.Open(str(merged), "r")
    lookup_missing = [read_id for read_id in read_ids if slow5.get_read(read_id) is None]
    slow5.close()
    if lookup_missing:
        raise ValueError("bounded BLOW5 index cannot resolve every requested read")
    return merged, receipts


def bounded_reference_mapping(
    parent: Path,
    read_ids: list[str],
    work_dir: Path,
    molecule_type: str,
    region: str,
) -> tuple[Path, list[dict[str, Any]]]:
    """Materialize only the coordinate-sorted PAF records visible to one bounded render."""
    wanted = set(read_ids)
    observed: set[str] = set()
    plain = work_dir / "bounded_mapping.paf"
    match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", region)
    if match is None or not Path(f"{parent}.tbi").is_file():
        raise ValueError("bounded reference mapping requires an indexed region")
    contig, start_text, end_text = match.groups()
    with pysam.TabixFile(str(parent)) as source, plain.open("w", encoding="utf-8") as target:
        for line in source.fetch(contig, int(start_text) - 1, int(end_text)):
            line = f"{line}\n"
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12 or fields[0] not in wanted:
                continue
            if fields[0] in observed:
                raise ValueError("reference mapping contains duplicate requested read IDs")
            target.write(line)
            observed.add(fields[0])
    if observed != wanted:
        raise ValueError("bounded reference mapping did not preserve the requested read set")
    compressed = Path(f"{plain}.gz")
    bgzip_receipt = run(["bgzip", "-f", str(plain)])
    tabix_columns = ("8", "9") if molecule_type == "dna" else ("9", "8")
    tabix_receipt = run([
        "tabix", "-f", "-0", "-b", tabix_columns[0], "-e", tabix_columns[1], "-s", "6", str(compressed),
    ])
    index = Path(f"{compressed}.tbi")
    if not compressed.is_file() or not index.is_file():
        raise ValueError("bounded reference mapping lacks its bgzip/tabix artifact pair")
    return compressed, [{
        "kind": "bounded_reference_mapping",
        "parent_sha256": sha(parent),
        "read_count": len(observed),
        "output_sha256": sha(compressed),
        "index_sha256": sha(index),
        "commands": {"bgzip": bgzip_receipt, "tabix": tabix_receipt},
    }]


def raw_parent_hashes(paths: list[Path]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for path in paths:
        index = Path(f"{path}.idx")
        broker_owns_index = (
            _ACTIVE_BROKER_REQUEST is not None
            and _ACTIVE_BROKER_REQUEST.owns_alias_path(index)
        )
        if not index.is_file() or (index.is_symlink() and not broker_owns_index):
            raise ValueError("BLOW5 parent lacks its adjacent governed index")
        result.append({"sha256": sha(path), "index_sha256": sha(index)})
    return result


def blow5_ids(paths: list[Path]) -> tuple[list[str], dict[str, str], dict[str, Path]]:
    identities: dict[str, str] = {}
    read_ids: list[str] = []
    partition_by_read_id: dict[str, Path] = {}
    for path in paths:
        identities[str(path)] = sha(path)
        slow5 = pyslow5.Open(str(path), "r")
        values, count = slow5.get_read_ids()
        slow5.close()
        if not isinstance(values, list) or not isinstance(count, int) or count != len(values):
            raise ValueError("BLOW5 read-ID index returned an invalid inventory")
        for value in values:
            read_id = str(value)
            if read_id in partition_by_read_id:
                raise ValueError("BLOW5 routing parents contain duplicate read IDs")
            partition_by_read_id[read_id] = path
            read_ids.append(read_id)
    if not read_ids:
        raise ValueError("BLOW5 routing parents contain no reads or duplicate read IDs")
    return sorted(read_ids), identities, partition_by_read_id


_BASECALL_MODEL_TOKEN = re.compile(
    r"(?:basecall_model|model)[=: ]+([A-Za-z0-9_.@-]+)", re.IGNORECASE
)
_RECOGNIZED_BASECALL_MODEL = re.compile(
    r"^(?:dna|rna)[A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$", re.IGNORECASE
)


def models_from_header(header: dict[str, Any]) -> tuple[str, dict[str, str]]:
    read_groups = header.get("RG")
    if not isinstance(read_groups, list) or not read_groups:
        raise ValueError("BAM @RG metadata is missing")
    models_by_read_group: dict[str, str] = {}
    for read_group in read_groups:
        if not isinstance(read_group, dict):
            raise ValueError("BAM @RG metadata is malformed")
        read_group_id = read_group.get("ID")
        if not isinstance(read_group_id, str) or not read_group_id or read_group_id in models_by_read_group:
            raise ValueError("BAM @RG metadata lacks unique exact IDs")
        text = " ".join(str(read_group.get(key, "")) for key in ("DS", "PM", "PU"))
        matches = _BASECALL_MODEL_TOKEN.findall(text)
        if len(matches) != 1 or _RECOGNIZED_BASECALL_MODEL.fullmatch(matches[0]) is None:
            raise ValueError("BAM @RG metadata does not carry one exact recognized basecall model")
        models_by_read_group[read_group_id] = matches[0]
    models = set(models_by_read_group.values())
    if len(models) != 1:
        raise ValueError("BAM @RG metadata does not identify one coherent basecall model")
    return next(iter(models)), models_by_read_group


def model_from_header(header: dict[str, Any]) -> str:
    return models_from_header(header)[0]


def require_record_read_group(
    record: Any,
    models_by_read_group: dict[str, str],
    basecall_model_id: str,
) -> None:
    read_id = record.query_name or "<unknown>"
    if not record.has_tag("RG"):
        raise ValueError(f"move BAM read lacks read group provenance: {read_id}")
    read_group_id = str(record.get_tag("RG"))
    if models_by_read_group.get(read_group_id) != basecall_model_id:
        raise ValueError(f"move BAM read group is unbound or model-incoherent: {read_id}")


def validate_move_record(
    record: Any,
    *,
    raw_signal_samples: int,
    molecule_type: str,
    basecall_model_id: str,
) -> dict[str, int]:
    """Validate Dorado move evidence using its partial-final-block contract."""
    read_id = record.query_name
    sequence = record.query_sequence or ""
    if not read_id or not sequence:
        raise ValueError("move BAM read lacks an exact query sequence")
    normalized_model = basecall_model_id.lower()
    if molecule_type not in {"dna", "rna"} or not normalized_model.startswith(f"{molecule_type}_"):
        raise ValueError("basecall model/molecule evidence is inconsistent")
    tags = dict(record.get_tags())
    if any(name not in tags for name in ("mv", "ts", "ns")):
        raise ValueError(f"move BAM read lacks required signal tags: {read_id}")
    try:
        moves = [int(value) for value in tags["mv"]]
        ts, ns = int(tags["ts"]), int(tags["ns"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"move BAM read has non-integer signal tags: {read_id}") from exc
    if len(moves) < 2 or moves[0] <= 0:
        raise ValueError(f"move BAM read has an invalid stride: {read_id}")
    stride = moves[0]
    move_values = moves[1:]
    if any(value not in {0, 1} for value in move_values):
        raise ValueError(f"move BAM read does not contain legal 0/1 moves: {read_id}")
    if sum(move_values) != len(sequence):
        raise ValueError(f"move BAM query sequence and move count diverge: {read_id}")
    if ts < 0 or ns <= ts or ns != raw_signal_samples:
        raise ValueError(f"move BAM ns does not equal raw-signal sample length: {read_id}")
    remainder = ns - ts - stride * len(move_values)
    if remainder < 0 or remainder >= stride:
        raise ValueError(f"move BAM ts/ns violate the partial-final-block rule: {read_id}")
    return {"stride": stride, "move_count": sum(move_values), "final_block_remainder": remainder}


def cmd_validate_moves(args: argparse.Namespace) -> None:
    raw_ids, blow5_sha, raw_partition_by_read_id = blow5_ids(args.blow5)
    raw_set = set(raw_ids)
    input_bam = pysam.AlignmentFile(str(args.bam), "rb", check_sq=False)
    header = input_bam.header.to_dict()
    header_sha = hashlib.sha256(str(input_bam.header).encode()).hexdigest()
    model_id, models_by_read_group = models_from_header(header)
    if not model_id.lower().startswith(f"{args.molecule_type}_"):
        raise ValueError("BAM basecall model does not match the declared molecule type")
    raw_handles = {path: pyslow5.Open(str(path), "r") for path in args.blow5}
    output_bam = pysam.AlignmentFile(str(args.filtered_bam), "wb", header=input_bam.header)
    seen: set[str] = set()
    included: set[str] = set()
    counts = {"records": 0, "mv": 0, "ts": 0, "ns": 0, "excluded_bam_only": 0}
    observed_stride: int | None = None
    try:
        for record in input_bam.fetch(until_eof=True):
            counts["records"] += 1
            read_id = record.query_name
            if not read_id or read_id in seen:
                raise ValueError("move BAM contains an empty or duplicate read ID")
            require_record_read_group(record, models_by_read_group, model_id)
            seen.add(read_id)
            if read_id in raw_set:
                raw_read = raw_handles[raw_partition_by_read_id[read_id]].get_read(read_id, pA=False)
                if raw_read is None or "signal" not in raw_read:
                    raise ValueError(f"governed raw signal cannot resolve move read: {read_id}")
                signal = raw_read["signal"]
                sample_count = len(signal)
                declared_samples = raw_read.get("len_raw_signal", sample_count)
                if int(declared_samples) != sample_count:
                    raise ValueError(f"BLOW5 raw-signal length evidence is incoherent: {read_id}")
                evidence = validate_move_record(
                    record,
                    raw_signal_samples=sample_count,
                    molecule_type=args.molecule_type,
                    basecall_model_id=model_id,
                )
                if observed_stride is None:
                    observed_stride = evidence["stride"]
                elif evidence["stride"] != observed_stride:
                    raise ValueError("move BAM contains incoherent stride evidence")
                tags = dict(record.get_tags())
                for name in ("mv", "ts", "ns"):
                    counts[name] += 1
                included.add(read_id)
                output_bam.write(record)
            else:
                tags = dict(record.get_tags())
                if "ns" not in tags:
                    raise ValueError(f"move BAM read lacks ns: {read_id}")
                evidence = validate_move_record(
                    record,
                    raw_signal_samples=int(tags["ns"]),
                    molecule_type=args.molecule_type,
                    basecall_model_id=model_id,
                )
                if observed_stride is None:
                    observed_stride = evidence["stride"]
                elif evidence["stride"] != observed_stride:
                    raise ValueError("move BAM contains incoherent stride evidence")
                for name in ("mv", "ts", "ns"):
                    counts[name] += 1
                counts["excluded_bam_only"] += 1
    finally:
        output_bam.close()
        input_bam.close()
        for slow5 in raw_handles.values():
            slow5.close()
    missing = raw_set - included
    if missing:
        raise ValueError(f"BLOW5 reads missing from move BAM: {len(missing)}")
    inventory_bytes = "".join(f"{read_id}\n" for read_id in raw_ids).encode()
    args.inventory.write_bytes(inventory_bytes)
    result = {
        "schema": "bms.ont-move-source-validation.v1",
        "move_bam_sha256": sha(args.bam),
        "move_bam_header_sha256": header_sha,
        "basecall_model_id": model_id,
        "molecule_type": args.molecule_type,
        "record_count": counts["records"],
        "unique_read_count": len(seen),
        "tag_counts": {key: counts[key] for key in ("mv", "ts", "ns")},
        "move_stride": observed_stride,
        "included_read_count": len(included),
        "missing_blow5_read_count": 0,
        "excluded_bam_only_read_count": counts["excluded_bam_only"],
        "read_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "filtered_move_bam": {"sha256": sha(args.filtered_bam), "size_bytes": args.filtered_bam.stat().st_size},
        "parent_sha256s": {
            "original_move_bam_sha256": sha(args.bam),
            "blow5": raw_parent_hashes(args.blow5),
        },
        "blow5_parents": blow5_sha,
    }
    result["content_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    write_json(args.report, result)


def _calibration_score(sequence: str, signal: list[float], moves: list[int], kmer_length: int, move_offset: int) -> tuple[int, float]:
    if len(sequence) < kmer_length + move_offset or len(moves) < len(sequence):
        raise ValueError("calibration read is too short for the fixed candidate bound")
    start_raw = sum(moves[:move_offset])
    model: dict[str, float] = {}
    for index in range(0, len(sequence) - kmer_length + 1 - move_offset):
        end_raw = start_raw + moves[index + move_offset]
        kmer = sequence[index:index + kmer_length].upper()
        values = signal[start_raw:end_raw]
        if len(values) == 0 or not set(kmer) <= {"A", "C", "G", "T"}:
            raise ValueError("calibration sequence or move span is not closed DNA evidence")
        model[kmer] = float(statistics.median(values))
        start_raw = end_raw
    scores: list[float] = []
    for base_offset in range(kmer_length):
        groups = [[value for kmer, value in model.items() if kmer[base_offset] == base] for base in "ACGT"]
        if any(not group for group in groups):
            raise ValueError("calibration candidate lacks complete A/C/G/T score evidence")
        medians = [float(statistics.median(group)) for group in groups]
        score = max(medians) - min(medians)
        if not math.isfinite(score):
            raise ValueError("calibration score is non-finite")
        scores.append(score)
    best = max(range(kmer_length), key=lambda offset: scores[offset])
    return best, scores[best]


def calibration_sequence(sequence: str, molecule_type: str) -> str:
    if "T" not in sequence and all(base in sequence for base in "ACG"):
        if "N" in sequence:
            sequence = sequence.replace("N", "T")
        elif "U" in sequence:
            sequence = sequence.replace("U", "T")
    if molecule_type == "dna":
        return sequence
    if molecule_type == "rna":
        return sequence[::-1]
    raise ValueError("calibration molecule type is unsupported")


def calculate_offsets_command(
    baseline: Path,
    sequence_fasta: Path,
    slow5: Path,
    sample_count: int,
    molecule_type: str,
) -> list[str]:
    command = [
        "squigualiser", "calculate_offsets", "-k", "9", "-p", str(baseline),
        "-f", str(sequence_fasta), "-s", str(slow5), "--read_limit", str(sample_count),
    ]
    if molecule_type == "rna":
        command.append("--rna")
    elif molecule_type != "dna":
        raise ValueError("calibration molecule type is unsupported")
    return command


def remove_calibration_fasta_index(sequence_fasta: Path) -> None:
    index = Path(f"{sequence_fasta}.fai")
    if not index.exists() and not index.is_symlink():
        return
    if index.is_symlink() or not index.is_file():
        raise ValueError("unsafe calibration FASTA index")
    index.unlink()


def cmd_calibrate(args: argparse.Namespace) -> None:
    if not 1 <= args.sample_count <= 100:
        raise ValueError("calibration sample count is outside bounded policy")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_ids, blow5_hashes, _raw_partition_by_read_id = blow5_ids(args.blow5)
    raw_set = set(raw_ids)
    records: dict[str, pysam.AlignedSegment] = {}
    sequences: dict[str, str] = {}
    with pysam.AlignmentFile(str(args.filtered_bam), "rb", check_sq=False) as bam:
        header = bam.header
        filtered_model_id = model_from_header(header.to_dict())
        if filtered_model_id != args.basecall_model_id or not filtered_model_id.lower().startswith(f"{args.molecule_type}_"):
            raise ValueError("filtered move BAM model/molecule evidence diverges from calibration authority")
        for record in bam.fetch(until_eof=True):
            read_id = record.query_name
            if not read_id or read_id in records or not record.query_sequence:
                raise ValueError("filtered move BAM contains missing or duplicate calibration reads")
            records[read_id] = record
            sequences[read_id] = record.query_sequence
    if set(records) != raw_set:
        raise ValueError("filtered move BAM and governed BLOW5 intersection is incomplete")
    inventory_sha256 = hashlib.sha256("".join(f"{read_id}\n" for read_id in sorted(raw_set)).encode()).hexdigest()
    if (
        inventory_sha256 != args.move_inventory_sha256
        or sha(args.inventory) != args.move_inventory_sha256
        or sha(args.original_bam) != args.move_artifact_sha256
    ):
        raise ValueError("exact signal/move intersection digest diverges from the governed inventory")
    ranked = sorted(raw_ids, key=lambda read_id: (hashlib.sha256(read_id.encode()).hexdigest(), read_id))
    if len(ranked) < args.sample_count:
        raise ValueError("exact signal/move intersection is smaller than requested calibration sample")
    selected = ranked[:args.sample_count]
    selection_digest = hashlib.sha256(canonical(selected)).hexdigest()
    sample_bam = args.output_dir / "sample.bam"
    sequence_fasta = args.output_dir / "sample.fasta"
    with pysam.AlignmentFile(str(sample_bam), "wb", header=header) as output_bam, sequence_fasta.open("w", encoding="utf-8") as fasta:
        for read_id in selected:
            record = records[read_id]
            output_bam.write(record)
            fasta.write(f">{read_id}\n{sequences[read_id]}\n")
    with tempfile.TemporaryDirectory(prefix="bms-calibration-") as temporary:
        bounded, extraction = bounded_blow5(args.blow5, selected, Path(temporary))
        bounded_output = args.output_dir / "sample.blow5"
        shutil.copyfile(bounded, bounded_output)
        shutil.copyfile(Path(f"{bounded}.idx"), Path(f"{bounded_output}.idx"))
    baseline = args.output_dir / "baseline.paf"
    baseline_command = ["squigualiser", "reform", "--bam", str(sample_bam), "--output", str(baseline), "-c", "--kmer_length", "1", "--sig_move_offset", "0"]
    if args.molecule_type == "rna": baseline_command.append("--rna")
    reform_receipt = run(baseline_command)
    baseline_authority = reform_parent_authority(
        sample_bam, [bounded_output], kmer_length=1, signal_move_offset=0
    )
    validate_paf(
        baseline, (b"ss:Z:",), set(selected),
        molecule_type=args.molecule_type, paf_kind="reform",
        reform_authority=baseline_authority,
    )
    paf_rows: dict[str, tuple[int, list[int]]] = {}
    with baseline.open("rb") as handle:
        for raw_line in bounded_binary_lines(handle):
            fields = raw_line.decode("utf-8").rstrip("\n").split("\t")
            tags = [field[5:] for field in fields[12:] if field.startswith("ss:Z:")]
            if len(fields) < 12 or len(tags) != 1 or fields[0] in paf_rows:
                raise ValueError("baseline PAF calibration evidence is ambiguous")
            tokens = [token for token in re.split(r",+", tags[0]) if token]
            if not tokens or any(not token.isdigit() for token in tokens):
                raise ValueError("baseline PAF move evidence is unparseable")
            paf_rows[fields[0]] = (int(fields[2]), [int(token) for token in tokens])
    slow5 = pyslow5.Open(str(bounded_output), "r")
    evidence: list[dict[str, Any]] = []
    try:
        for move_offset in range(9):
            per_read: list[tuple[str, int, float]] = []
            for read_id in selected:
                read = slow5.get_read(read_id, pA=True)
                if read is None or read_id not in paf_rows:
                    raise ValueError("selected calibration read is missing signal or baseline mapping")
                start_raw, moves = paf_rows[read_id]
                signal = list(read["signal"])[start_raw:]
                best_offset, score = _calibration_score(
                    calibration_sequence(sequences[read_id], args.molecule_type),
                    signal,
                    moves,
                    9,
                    move_offset,
                )
                per_read.append((read_id, best_offset, score))
            median_index = sorted(range(len(per_read)), key=lambda index: per_read[index][1])[len(per_read) // 2]
            median_read, best_offset, score = per_read[median_index]
            evidence.append({"candidate_signal_move_offset": move_offset, "candidate_kmer_bound": 9, "median_best_base_offset": best_offset, "score": score, "median_read_id": median_read, "read_count": len(per_read)})
    finally:
        slow5.close()
    best_candidate = max(evidence, key=lambda item: item["score"])
    zero_candidates = [item for item in evidence if item["median_best_base_offset"] == 0]
    if not zero_candidates:
        raise ValueError("calibration score evidence has no zero-base-offset candidate")
    zero_candidate = zero_candidates[-1]
    if zero_candidate["candidate_signal_move_offset"] != best_candidate["candidate_signal_move_offset"] - best_candidate["median_best_base_offset"]:
        raise ValueError("calibration candidate evidence does not yield an unambiguous recommendation")
    independent_m = int(zero_candidate["candidate_signal_move_offset"])
    independent_k = independent_m + 1
    try:
        offset_receipt = run(calculate_offsets_command(
            baseline,
            sequence_fasta,
            bounded_output,
            args.sample_count,
            args.molecule_type,
        ))
    finally:
        remove_calibration_fasta_index(sequence_fasta)
    stdout = offset_receipt["stdout_tail"]
    matches = re.findall(r"recommended kmer_length:(\d+) recommended sig_move_offset:(\d+)", stdout)
    if len(matches) != 1 or "please refer" in stdout.lower():
        raise ValueError("pinned calculate_offsets recommendation is ambiguous or unparseable")
    upstream_k, upstream_m = map(int, matches[0])
    if (upstream_k, upstream_m) != (independent_k, independent_m):
        raise ValueError("pinned and independently calculated recommendations are inconsistent")
    parent_hashes = {
        "raw_manifest_sha256": args.raw_manifest_sha256,
        "move_bam_sha256": sha(args.original_bam),
        "move_read_inventory_sha256": args.move_inventory_sha256,
        "filtered_move_bam_sha256": sha(args.filtered_bam),
        "move_inventory_actual_sha256": sha(args.inventory),
        "blow5": raw_parent_hashes(args.blow5),
        "blow5_partitions": blow5_hashes,
        "sample_bam_sha256": sha(sample_bam),
        "sample_fasta_sha256": sha(sequence_fasta),
        "sample_blow5_sha256": sha(bounded_output),
        "baseline_paf_sha256": sha(baseline),
    }
    report = {
        "schema": "bms.ont-signal-calibration.v1",
        "basecall_model_id": args.basecall_model_id,
        "sample_selection": {"method": "sha256_read_id_rank_v1", "requested_count": args.sample_count, "selected_count": len(selected), "intersection_count": len(raw_set), "read_ids": selected, "selection_sha256": selection_digest},
        "parent_sha256s": parent_hashes,
        "baseline_paf_sha256": parent_hashes["baseline_paf_sha256"],
        "tool_identity": {"name": "squigualiser", "version": "0.7.0", "commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e", "candidate_kmer_bound": 9},
        "recommendation": {"kmer_length": upstream_k, "signal_move_offset": upstream_m},
        "score_evidence": evidence,
        "validation": {"exact_intersection": True, "independent_recommendation_equal": True, "assumption_unambiguous": True},
        "commands": {"bounded_blow5": extraction, "baseline_reform": reform_receipt, "calculate_offsets": offset_receipt},
    }
    write_json(args.report, report)
    if args.report.stat().st_size > 1024 * 1024:
        args.report.unlink()
        raise ValueError("calibration report exceeds bounded JSON policy")


def _ss_evidence(value: str) -> tuple[int, int]:
    tokens = re.findall(r"([0-9]+)([,DI])", value)
    if not tokens or "".join(number + operation for number, operation in tokens) != value:
        raise ValueError("Squigualiser ss evidence is unparseable")
    signal_span = 0
    target_span = 0
    for number_text, operation in tokens:
        number = int(number_text)
        if number <= 0:
            raise ValueError("Squigualiser ss evidence contains a non-positive span")
        if operation in {",", "I"}:
            signal_span += number
        if operation == ",":
            target_span += 1
        elif operation == "D":
            target_span += number
    return signal_span, target_span


def _append_topology(
    topology: list[tuple[str, int | None]], operation: str, count: int | None
) -> None:
    if topology and topology[-1][0] == operation:
        if count is None or topology[-1][1] is None:
            topology[-1] = (operation, None)
        else:
            topology[-1] = (operation, int(topology[-1][1]) + count)
    else:
        topology.append((operation, count))


def _ss_topology(value: str) -> list[tuple[str, int | None]]:
    topology: list[tuple[str, int | None]] = []
    for number_text, operation in re.findall(r"([0-9]+)([,DI])", value):
        if operation == ",":
            _append_topology(topology, "M", 1)
        elif operation == "D":
            _append_topology(topology, "D", int(number_text))
        else:
            _append_topology(topology, "I", None)
    return topology


def _cigar_topology(
    cigar: Any,
    molecule_type: str,
    reference_span: int | None = None,
    alignment_strand: str = "+",
) -> list[tuple[str, int | None]]:
    topology: list[tuple[str, int | None]] = []
    if not isinstance(cigar, list):
        raise ValueError("primary alignment authority lacks CIGAR topology")
    if reference_span is not None and reference_span <= 0:
        raise ValueError("primary alignment authority lacks CIGAR topology")
    for item in cigar:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("primary alignment authority lacks CIGAR topology")
        operation, count = item
        if isinstance(operation, bool) or isinstance(count, bool) or not isinstance(operation, int) or not isinstance(count, int) or count <= 0:
            raise ValueError("primary alignment authority lacks CIGAR topology")
        if operation == 0:
            _append_topology(topology, "M", count)
        elif operation == 1:
            _append_topology(topology, "I", None)
        elif operation == 2:
            _append_topology(topology, "D", count)
        elif operation == 3:
            continue
        elif operation not in {4, 5}:
            raise ValueError("primary alignment authority lacks CIGAR topology")
    if alignment_strand not in {"+", "-"}:
        raise ValueError("primary alignment authority lacks CIGAR orientation")
    if (alignment_strand == "-") != (molecule_type == "rna"):
        topology.reverse()
    if reference_span is not None:
        remaining = reference_span
        clipped: list[tuple[str, int | None]] = []
        for operation, count in topology:
            if remaining == 0:
                break
            if operation == "I":
                _append_topology(clipped, operation, None)
                continue
            if count is None:
                raise ValueError("primary alignment authority lacks CIGAR topology")
            emitted = min(count, remaining)
            _append_topology(clipped, operation, emitted)
            remaining -= emitted
        if remaining != 0:
            raise ValueError("primary alignment authority CIGAR does not cover emitted reference span")
        topology = clipped
    if not topology:
        raise ValueError("primary alignment authority lacks CIGAR topology")
    return topology


def realign_record_authority(
    reform: dict[str, Any],
    cigar: Any,
    *,
    molecule_type: str,
    alignment_strand: str,
    reference_start: int,
) -> dict[str, Any]:
    """Reproduce pinned v0.7.0 realign's CIGAR-to-ss transformation."""
    if molecule_type not in {"dna", "rna"} or alignment_strand not in {"+", "-"}:
        raise ValueError("realign authority lacks a pinned orientation")
    try:
        query_length = int(reform["signal_length"])
        query_start = int(reform["signal_start"])
        query_end = int(reform["signal_end"])
        reform_ss = str(reform["ss"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("realign authority lacks exact parent reform evidence") from exc
    if re.fullmatch(r"(?:[1-9][0-9]*,)+", reform_ss) is None:
        raise ValueError("realign authority lacks exact parent reform durations")
    durations = [int(value) for value in reform_ss.rstrip(",").split(",")]
    if not isinstance(cigar, list):
        raise ValueError("realign authority lacks pinned CIGAR evidence")
    oriented: list[tuple[int, int]] = []
    for item in cigar:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or isinstance(item[0], bool)
            or isinstance(item[1], bool)
            or not isinstance(item[0], int)
            or not isinstance(item[1], int)
            or item[1] <= 0
            or item[0] not in {0, 1, 2, 3, 4, 5}
        ):
            raise ValueError("realign authority lacks pinned CIGAR evidence")
        oriented.append((item[0], item[1]))
    if (alignment_strand == "-") != (molecule_type == "rna"):
        oriented.reverse()

    duration_index = 0
    consumed_query = 0
    count_bases = 0
    operation_count = 0
    output_ss: list[str] = []
    for operation, original_count in oriented:
        count = original_count
        if operation in {0, 1, 4}:
            count = min(count, len(durations) - consumed_query)
            consumed_query += count
        if operation == 0:
            for _ in range(count):
                output_ss.append(f"{durations[duration_index]},")
                duration_index += 1
            count_bases += count
        elif operation == 2:
            output_ss.append(f"{count}D")
            count_bases += count
        elif operation == 1:
            signal_skip = sum(durations[duration_index:duration_index + count])
            duration_index += count
            if signal_skip <= 0:
                raise ValueError("pinned realign produced an empty insertion span")
            output_ss.append(f"{signal_skip}I")
        elif operation == 4:
            signal_skip = sum(durations[duration_index:duration_index + count])
            duration_index += count
            if operation_count == 0:
                query_start += signal_skip
            else:
                query_end -= signal_skip
        elif operation in {3, 5}:
            continue
        operation_count += 1
    if count_bases <= 0 or not 0 <= query_start < query_end <= query_length:
        raise ValueError("pinned realign authority is empty or out of bounds")
    target_start = reference_start + count_bases if molecule_type == "rna" else reference_start
    target_end = reference_start if molecule_type == "rna" else reference_start + count_bases
    return {
        "query_length": query_length,
        "query_start": query_start,
        "query_end": query_end,
        "target_length": count_bases,
        "target_start": target_start,
        "target_end": target_end,
        "ss": "".join(output_ss),
    }


def reform_coordinate_authority(
    *,
    sequence_length: int,
    ts: int,
    ns: int,
    moves: list[int],
    kmer_length: int,
    signal_move_offset: int,
) -> dict[str, Any]:
    """Reproduce pinned Squigualiser v0.7.0 reform PAF coordinates and ss."""
    if (
        sequence_length < kmer_length
        or kmer_length < 1
        or signal_move_offset < 0
        or signal_move_offset >= kmer_length
        or len(moves) < 2
        or moves[0] <= 0
        or any(value not in {0, 1} for value in moves[1:])
        or ts < 0
        or ns <= ts
    ):
        raise ValueError("move evidence cannot derive pinned reform coordinates")
    stride = moves[0]
    kmer_count = sequence_length - kmer_length + 1
    move_count = 0
    index = 1
    start_index = 0
    while move_count < signal_move_offset + 1:
        if index >= len(moves):
            raise ValueError("move evidence ends before the pinned signal offset")
        if moves[index] == 1:
            move_count += 1
            start_index = index
        index += 1
    signal_start = ts + (index - 2) * stride

    remaining = kmer_count + signal_move_offset + 1
    move_index = 1
    end_index = 2
    while move_index < len(moves):
        if remaining > 0 and moves[move_index] == 1:
            remaining -= 1
            end_index = move_index
        move_index += 1
    signal_end = ns if remaining > 0 else ts + (end_index - 1) * stride

    durations: list[int] = []
    remaining_kmers = kmer_count
    while index < len(moves):
        if remaining_kmers > 0 and moves[index] == 1:
            durations.append((index - start_index) * stride)
            start_index = index
            remaining_kmers -= 1
        if remaining_kmers > 0 and index == len(moves) - 1:
            terminal_remainder = ns - ((index - 1) * stride + ts)
            if terminal_remainder < 0:
                raise ValueError("move evidence cannot derive pinned reform terminal duration")
            durations.append((index - start_index) * stride + terminal_remainder)
            remaining_kmers -= 1
        index += 1
    if remaining_kmers != 0 or len(durations) != kmer_count or any(value <= 0 for value in durations):
        raise ValueError("move evidence cannot derive every pinned reform k-mer duration")
    if not ts <= signal_start < signal_end <= ns or sum(durations) != signal_end - signal_start:
        raise ValueError("pinned reform coordinates escape move signal authority")
    return {
        "signal_length": ns,
        "signal_start": signal_start,
        "signal_end": signal_end,
        "sequence_length": kmer_count,
        "ss": "".join(f"{duration}," for duration in durations),
    }


def reform_parent_authority(
    filtered_bam: Path,
    blow5_paths: list[Path],
    *,
    kmer_length: int,
    signal_move_offset: int,
) -> dict[str, dict[str, Any]]:
    raw_ids, _hashes, partition_by_read_id = blow5_ids(blow5_paths)
    expected = set(raw_ids)
    handles = {path: pyslow5.Open(str(path), "r") for path in blow5_paths}
    authority: dict[str, dict[str, Any]] = {}
    try:
        with pysam.AlignmentFile(str(filtered_bam), "rb", check_sq=False) as bam:
            for record in bam.fetch(until_eof=True):
                read_id = record.query_name
                sequence = record.query_sequence or ""
                if not read_id or read_id in authority or read_id not in expected or not sequence:
                    raise ValueError("filtered move BAM cannot bind exact reform parent authority")
                tags = dict(record.get_tags())
                try:
                    ts, ns = int(tags["ts"]), int(tags["ns"])
                    moves = [int(value) for value in tags["mv"]]
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("filtered move BAM lacks exact reform signal coordinates") from exc
                raw_read = handles[partition_by_read_id[read_id]].get_read(read_id, pA=False)
                if raw_read is None or "signal" not in raw_read:
                    raise ValueError("BLOW5 cannot resolve exact reform parent authority")
                signal_length = len(raw_read["signal"])
                declared_length = int(raw_read.get("len_raw_signal", signal_length))
                if signal_length != declared_length or ns != signal_length or not 0 <= ts < ns:
                    raise ValueError("BLOW5 and move BAM reform signal authority diverge")
                authority[read_id] = reform_coordinate_authority(
                    sequence_length=len(sequence),
                    ts=ts,
                    ns=ns,
                    moves=moves,
                    kmer_length=kmer_length,
                    signal_move_offset=signal_move_offset,
                )
    finally:
        for handle in handles.values():
            handle.close()
    if set(authority) != expected:
        raise ValueError("filtered move BAM and BLOW5 reform parent inventories diverge")
    return authority


def validate_paf(
    path: Path,
    required_tags: tuple[bytes, ...],
    expected_ids: set[str] | None = None,
    *,
    molecule_type: str = "dna",
    paf_kind: str = "reform",
    reference_lengths: dict[str, int] | None = None,
    alignment_authority: dict[str, dict[str, Any]] | None = None,
    reform_authority: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    observed: set[str] = set()
    count = 0
    with path.open("rb") as handle:
        for line in bounded_binary_lines(handle):
            fields = line.rstrip(b"\n").split(b"\t")
            if len(fields) < 12 or any(not any(field.startswith(tag) for field in fields[12:]) for tag in required_tags):
                raise ValueError("Squigualiser output PAF structure or required tags are invalid")
            read_id = fields[0].decode("utf-8")
            if read_id in observed:
                raise ValueError("Squigualiser output contains duplicate read IDs")
            try:
                qlen, qstart, qend = (int(fields[index]) for index in (1, 2, 3))
                target_length, target_start, target_end = (int(fields[index]) for index in (6, 7, 8))
                matches, block_length, mapq = (int(fields[index]) for index in (9, 10, 11))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Squigualiser output PAF contains non-numeric core fields") from exc
            strand = fields[4].decode("ascii", "strict")
            target_name = fields[5].decode("utf-8", "strict")
            if qlen <= 0 or not 0 <= qstart < qend <= qlen:
                raise ValueError("Squigualiser output PAF query coordinates are out of bounds")
            if strand not in {"+", "-"} or not target_name:
                raise ValueError("Squigualiser output PAF strand or target is invalid")
            ss_values = [field[5:].decode("ascii", "strict") for field in fields[12:] if field.startswith(b"ss:Z:")]
            if len(ss_values) != 1:
                raise ValueError("Squigualiser output PAF must contain exactly one ss tag")
            signal_span, ss_target_span = _ss_evidence(ss_values[0])
            coordinate_span = abs(target_end - target_start)
            if signal_span != qend - qstart:
                raise ValueError("Squigualiser output PAF ss signal span is incoherent")
            if matches != block_length or matches != coordinate_span or ss_target_span != coordinate_span or mapq != 255:
                raise ValueError("Squigualiser output PAF alignment spans are incoherent")
            if paf_kind == "reform":
                expected_direction = target_start < target_end if molecule_type == "dna" else target_start > target_end
                authority = None if reform_authority is None else reform_authority.get(read_id)
                if authority is not None and ss_values[0] != authority.get("ss"):
                    raise ValueError("Squigualiser reform PAF per-k-mer durations diverge from move authority")
                if (
                    strand != "+"
                    or target_name != read_id
                    or authority is None
                    or qlen != authority.get("signal_length")
                    or qstart != authority.get("signal_start")
                    or qend != authority.get("signal_end")
                    or target_length != authority.get("sequence_length")
                    or coordinate_span != authority.get("sequence_length")
                    or min(target_start, target_end) != 0
                    or max(target_start, target_end) != authority.get("sequence_length")
                    or not expected_direction
                ):
                    raise ValueError("Squigualiser reform PAF diverges from parent signal/move/sequence authority")
            elif paf_kind == "realign":
                if reference_lengths is None or target_name not in reference_lengths:
                    raise ValueError("Squigualiser realign PAF target lacks managed reference authority")
                low_coordinate, high_coordinate = min(target_start, target_end), max(target_start, target_end)
                expected_direction = target_start < target_end if molecule_type == "dna" else target_start > target_end
                if (
                    target_length != coordinate_span
                    or not expected_direction
                    or not 0 <= low_coordinate < high_coordinate <= reference_lengths[target_name]
                ):
                    raise ValueError("Squigualiser realign PAF coordinates are out of bounds")
                authority = None if alignment_authority is None else alignment_authority.get(read_id)
                parent_reform = None if reform_authority is None else reform_authority.get(read_id)
                if authority is None or parent_reform is None:
                    raise ValueError("realigned PAF lacks parent reform or primary alignment authority")
                expected = realign_record_authority(
                    parent_reform,
                    authority.get("cigar"),
                    molecule_type=molecule_type,
                    alignment_strand=str(authority.get("strand")),
                    reference_start=int(authority.get("reference_start", -1)),
                )
                if (
                    target_name != authority.get("contig")
                    or strand != authority.get("strand")
                    or qlen != expected["query_length"]
                    or qstart != expected["query_start"]
                    or qend != expected["query_end"]
                    or target_length != expected["target_length"]
                    or target_start != expected["target_start"]
                    or target_end != expected["target_end"]
                    or ss_values[0] != expected["ss"]
                ):
                    raise ValueError("realigned PAF diverges from parent reform and primary alignment authority")
            else:
                raise ValueError("unsupported PAF validation kind")
            observed.add(read_id)
            count += 1
    if not observed or (expected_ids is not None and observed != expected_ids):
        raise ValueError("Squigualiser output read inventory diverges from governed parents")
    inventory = "".join(f"{value}\n" for value in sorted(observed)).encode()
    return {
        "record_count": count,
        "read_ids": sorted(observed),
        "read_inventory_sha256": hashlib.sha256(inventory).hexdigest(),
    }


def paf_receipt(validation: dict[str, Any]) -> dict[str, Any]:
    """Keep the exact inventory digest without publishing an unbounded ID list."""
    return {key: value for key, value in validation.items() if key != "read_ids"}


def fasta_contig_lengths(path: Path) -> dict[str, int]:
    lengths: dict[str, int] = {}
    current: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                current = line[1:].split()[0] if line[1:].split() else ""
                if not current or current in lengths:
                    raise ValueError("managed reference has an empty or duplicate contig")
                lengths[current] = 0
                continue
            if current is None or not set(line.upper()) <= set("ACGTN"):
                raise ValueError("managed reference FASTA is malformed or contains unsupported bases")
            lengths[current] += len(line)
    if not lengths or any(length <= 0 for length in lengths.values()):
        raise ValueError("managed reference FASTA contains no complete contigs")
    return lengths


def cmd_reform(args: argparse.Namespace) -> None:
    inventory_ids = args.inventory.read_text().splitlines()
    expected = set(inventory_ids)
    if not expected or len(expected) != len(inventory_ids) or inventory_ids != sorted(inventory_ids):
        raise ValueError("move inventory must be non-empty, unique, and canonically sorted")
    command = [
        "squigualiser", "reform", "--bam", str(args.filtered_bam), "--output", str(args.output),
        "-c", "--kmer_length", str(args.kmer_length), "--sig_move_offset", str(args.signal_move_offset),
    ]
    if args.molecule_type == "rna":
        command.append("--rna")
    receipt = run(command)
    authority = reform_parent_authority(
        args.filtered_bam,
        args.blow5,
        kmer_length=args.kmer_length,
        signal_move_offset=args.signal_move_offset,
    )
    validation = validate_paf(
        args.output, (b"ss:Z:",), expected,
        molecule_type=args.molecule_type, paf_kind="reform",
        reform_authority=authority,
    )
    write_json(args.report, {
        "schema": "bms.ont-signal-reform.v1",
        **paf_receipt(validation),
        "output_sha256": sha(args.output),
        "parent_sha256s": {
            "original_move_bam_sha256": sha(args.original_bam),
            "filtered_move_bam_sha256": sha(args.filtered_bam),
            "move_inventory_sha256": sha(args.inventory),
            "blow5": raw_parent_hashes(args.blow5),
        },
        "command": receipt,
    })


def cmd_realign(args: argparse.Namespace) -> None:
    inventory_ids = args.inventory.read_text().splitlines()
    inventory = set(inventory_ids)
    if not inventory or len(inventory) != len(inventory_ids) or inventory_ids != sorted(inventory_ids):
        raise ValueError("move inventory must be non-empty, unique, and canonically sorted")
    reform_authority = reform_parent_authority(
        args.filtered_bam,
        args.blow5,
        kmer_length=args.kmer_length,
        signal_move_offset=args.signal_move_offset,
    )
    reform_validation = validate_paf(
        args.reform_paf, (b"ss:Z:",), inventory,
        molecule_type=args.molecule_type, paf_kind="reform",
        reform_authority=reform_authority,
    )
    reform_ids = set(reform_validation["read_ids"])
    reference_lengths = fasta_contig_lengths(args.reference_fasta)
    primary_ids: set[str] = set()
    primary_authority: dict[str, dict[str, Any]] = {}
    with pysam.AlignmentFile(str(args.alignment_bam), "rb", check_sq=False) as alignment:
        header_lengths = {
            str(item.get("SN")): int(item.get("LN"))
            for item in alignment.header.to_dict().get("SQ", [])
            if item.get("SN") is not None and item.get("LN") is not None
        }
        if header_lengths != reference_lengths:
            raise ValueError("alignment BAM reference dictionary does not equal the managed reference")
        for record in alignment.fetch(until_eof=True):
            if record.is_unmapped or record.is_secondary or record.is_supplementary:
                continue
            if record.has_tag("sp") or record.has_tag("pi") or (record.has_tag("dx") and int(record.get_tag("dx")) == 1):
                continue
            read_id = record.query_name
            if not read_id or read_id in primary_ids:
                raise ValueError("alignment BAM contains an empty or duplicate primary read ID")
            if record.reference_name not in reference_lengths or record.reference_start is None or record.reference_end is None:
                raise ValueError("primary alignment lacks complete managed reference coordinates")
            cigar = record.cigartuples
            if not cigar or any(operation not in {0, 1, 2, 3, 4, 5} or count <= 0 for operation, count in cigar):
                raise ValueError("primary alignment CIGAR is outside pinned Squigualiser v0.7.0 transformations")
            primary_ids.add(read_id)
            primary_authority[read_id] = {
                "contig": record.reference_name,
                "strand": "-" if record.is_reverse else "+",
                "reference_start": int(record.reference_start),
                "reference_end": int(record.reference_end),
                "cigar": [[int(operation), int(count)] for operation, count in cigar],
            }
    expected_ids = reform_ids & primary_ids
    if not expected_ids:
        raise ValueError("reference alignment has no primary reads in the governed signal mapping")
    realign_command = [
        "squigualiser", "realign", "-c", "--paf", str(args.reform_paf), "--bam", str(args.alignment_bam),
        "--output", str(args.output),
    ]
    if args.molecule_type == "rna":
        realign_command.append("--rna")
    realign_receipt = run(realign_command)
    validation = validate_paf(
        args.output, (b"ss:Z:",), expected_ids,
        molecule_type=args.molecule_type, paf_kind="realign",
        reference_lengths=reference_lengths,
        alignment_authority={read_id: primary_authority[read_id] for read_id in expected_ids},
        reform_authority={read_id: reform_authority[read_id] for read_id in expected_ids},
    )
    previous_key: tuple[bytes, int] | None = None
    with args.output.open("rb") as handle:
        for line in bounded_binary_lines(handle):
            fields = line.rstrip(b"\n").split(b"\t")
            coordinate = int(fields[7] if args.molecule_type == "dna" else fields[8])
            key = (fields[5], coordinate)
            if previous_key is not None and key < previous_key:
                raise ValueError("realigned PAF is not in deterministic reference-coordinate order")
            previous_key = key
    raw_output_sha256 = str(validation.pop("output_sha256", sha(args.output)))
    compressed = Path(f"{args.output}.gz")
    bgzip_receipt = run(["bgzip", "-f", str(args.output)])
    tabix_columns = ("8", "9") if args.molecule_type == "dna" else ("9", "8")
    tabix_receipt = run([
        "tabix", "-f", "-0", "-b", tabix_columns[0], "-e", tabix_columns[1], "-s", "6", str(compressed),
    ])
    index = Path(f"{compressed}.tbi")
    if not compressed.is_file() or compressed.is_symlink() or not index.is_file() or index.is_symlink():
        raise ValueError("bgzip/tabix did not produce the governed realignment artifact pair")
    write_json(args.report, {
        "schema": "bms.ont-signal-realign.v1",
        **paf_receipt(validation),
        "molecule_type": args.molecule_type,
        "raw_output_sha256": raw_output_sha256,
        "output_sha256": sha(compressed),
        "index_sha256": sha(index),
        "source_reform_sha256": sha(args.reform_paf),
        "alignment_bam_sha256": sha(args.alignment_bam),
        "alignment_index_sha256": sha(args.alignment_index),
        "reference_fasta_sha256": sha(args.reference_fasta),
        "parent_sha256s": {
            "original_move_bam_sha256": sha(args.original_bam),
            "filtered_move_bam_sha256": sha(args.filtered_bam),
            "move_inventory_sha256": sha(args.inventory),
            "blow5": raw_parent_hashes(args.blow5),
            "parent_reform_sha256": sha(args.reform_paf),
            "managed_reference_sha256": sha(args.reference_fasta),
            "alignment_bam_sha256": sha(args.alignment_bam),
            "alignment_index_sha256": sha(args.alignment_index),
        },
        "reference_contig_lengths": reference_lengths,
        "governed_reform_read_count": len(reform_ids),
        "primary_aligned_read_count": len(primary_ids),
        "realigned_intersection_read_count": len(expected_ids),
        "commands": {"realign": realign_receipt, "bgzip": bgzip_receipt, "tabix": tabix_receipt},
    })


def safe_render_artifacts(output_dir: Path, report: Path, command_receipt: dict[str, Any]) -> None:
    artifacts = []
    total_size = 0
    for path in sorted(output_dir.iterdir()):
        if path == report or path.name == ".owner":
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Squigualiser produced unexpected output: {path.name}")
        suffix = path.suffix.lower()
        if suffix not in {".html", ".svg"}:
            raise ValueError(f"Squigualiser produced unexpected output: {path.name}")
        limit = MAX_HTML_BYTES if suffix == ".html" else MAX_SVG_BYTES
        raw = path.read_bytes()
        if suffix == ".html":
            csp = (
                b'<meta http-equiv="Content-Security-Policy" '
                b'content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; '
                b'img-src data: blob:; font-src data:; connect-src \'none\'; object-src \'none\'; '
                b'base-uri \'none\'; form-action \'none\'">'
            )
            head = raw.lower().find(b"<head>")
            if head < 0:
                raise ValueError("rendered HTML lacks a head element for the enforced CSP")
            raw = raw[:head + len(b"<head>")] + csp + raw[head + len(b"<head>"):]
            path.write_bytes(raw)
        if not raw or len(raw) > limit or b"file://" in raw.lower():
            raise ValueError("render artifact violates bounded no-network sandbox policy")
        total_size += len(raw)
        if total_size > MAX_RENDER_TOTAL_BYTES:
            raise ValueError("render artifacts exceed the bounded total output policy")
        audit = ActiveResourceAudit()
        try:
            audit.feed(raw.decode("utf-8"))
            audit.close()
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("render artifact is not valid bounded UTF-8 markup") from exc
        if audit.violations:
            raise ValueError(f"render artifact contains active external resources: {sorted(set(audit.violations))}")
        artifacts.append({
            "artifact_id": hashlib.sha256(raw).hexdigest()[:32], "filename": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "media_type": "text/html" if suffix == ".html" else "image/svg+xml",
        })
    if not artifacts:
        raise ValueError("Squigualiser produced no bounded view artifact")
    write_json(report, {"schema": "bms.ont-squigualiser-render.v1", "artifacts": artifacts, "command": command_receipt, "network": "denied"})


def apply_mapping_render_options(
    command: list[str],
    *,
    kmer_length: int,
    molecule_type: str,
) -> None:
    if not 1 <= kmer_length <= 9:
        raise ValueError("render k-mer length is outside the approved mapping-profile bound")
    command.extend(["--kmer_length", str(kmer_length)])
    if molecule_type == "rna":
        command.append("--rna")
    elif molecule_type != "dna":
        raise ValueError("render molecule type is unsupported")


def apply_mode_specific_render_options(
    command: list[str],
    *,
    mode: str,
    fixed_width: bool,
    base_width: int,
    loose_bound: bool,
    show_samples: bool,
) -> None:
    if mode == "pileup":
        if fixed_width:
            command.extend(["--base_width", str(base_width)])
        if loose_bound:
            raise ValueError("Squigualiser v0.7.0 pileup does not support loose bounds")
        if show_samples:
            command.append("--plot_num_samples")
        return
    if fixed_width:
        command.extend(["--fixed_width", "--base_width", str(base_width)])
    if loose_bound:
        command.append("--loose_bound")
    if not show_samples:
        command.append("--no_samples")


def validate_render_scale(scale: str) -> None:
    if scale == "scaledpA":
        raise ValueError("scaledpA rendering is unavailable without exact mapping sc/sh authority")
    if scale not in {"none", "medmad", "znorm"}:
        raise ValueError("render scale is unsupported")


def cmd_render(args: argparse.Namespace) -> None:
    validate_render_scale(args.scale)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    extraction_receipts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bms-squigualiser-") as temporary:
        work_dir = Path(temporary)
        if args.mode == "read":
            if not args.read_id or args.sequence_bam is None or len(args.blow5) != 1:
                raise ValueError("read view requires one read ID, one routed BLOW5 partition, and its move BAM")
            sequence_file = work_dir / "selected.fasta"
            selected_read_fasta(args.sequence_bam, args.read_id, sequence_file)
            signal_file = args.blow5[0]
            mapping_file = args.mapping
            slow5 = pyslow5.Open(str(signal_file), "r")
            try:
                if slow5.get_read(args.read_id) is None:
                    raise ValueError("selected read is absent from the routed BLOW5 partition")
            finally:
                slow5.close()
            read_ids = [args.read_id]
        else:
            if args.reference_fasta is None or not args.region:
                raise ValueError("reference views require one managed reference and a bounded region")
            sequence_file = work_dir / "reference.fasta"
            sequence_file.symlink_to(args.reference_fasta)
            fai_receipt = run(["samtools", "faidx", str(sequence_file)])
            fai_path = Path(f"{sequence_file}.fai")
            if not fai_path.is_file() or fai_path.is_symlink():
                raise ValueError("managed reference indexing did not produce a bounded FAI")
            candidate_limit = min(args.pileup_read_limit, 5) if args.mode == "reference" else args.pileup_read_limit
            read_ids = reads_overlapping_region(args.mapping, args.region, candidate_limit, args.strand, args.molecule_type)
            if args.selected_read_id is None or read_ids != args.selected_read_id:
                raise ValueError("runtime-selected read IDs diverge from the worker routing selection")
            mapping_file, mapping_receipts = bounded_reference_mapping(
                args.mapping, read_ids, work_dir, args.molecule_type, args.region,
            )
            signal_file, blow5_receipts = bounded_blow5(args.blow5, read_ids, work_dir)
            extraction_receipts = [{
                "kind": "reference_fai",
                "reference_fasta_sha256": sha(args.reference_fasta),
                "index_sha256": sha(fai_path),
                "command": fai_receipt,
            }, *mapping_receipts, *blow5_receipts]
        common = ["--file", str(sequence_file), "--slow5", str(signal_file), "--alignment", str(mapping_file), "--output_dir", str(args.output_dir)]
        if args.mode == "read":
            command = ["squigualiser", "plot", *common, "--read_id", args.read_id, "--plot_limit", "1"]
        elif args.mode == "reference":
            command = ["squigualiser", "plot", *common, "--region", args.region, "--plot_limit", "1", "--sig_ref"]
        else:
            command = ["squigualiser", "plot_pileup", *common, "--region", args.region, "--plot_limit", str(args.pileup_read_limit)]
        apply_mapping_render_options(
            command,
            kmer_length=args.kmer_length,
            molecule_type=args.molecule_type,
        )
        if args.strand == "reverse": command.append("--plot_reverse")
        if args.signal_units == "raw_adc": command.append("--no_pa")
        if args.scale != "none": command.extend(["--sig_scale", args.scale])
        command.extend(["--base_shift", str(args.base_shift), "--base_limit", str(args.base_limit), "--sig_plot_limit", str(args.signal_sample_limit)])
        if args.point_size != 0.5:
            if not args.point_size.is_integer():
                raise ValueError("Squigualiser v0.7.0 accepts only its 0.5 default or an integer point size")
            command.extend(["--point_size", str(int(args.point_size))])
        apply_mode_specific_render_options(
            command,
            mode=args.mode,
            fixed_width=args.fixed_width,
            base_width=args.base_width,
            loose_bound=args.loose_bound,
            show_samples=args.show_samples,
        )
        if not args.show_base_colours: command.append("--no_colours")
        if args.remove_signal_outliers: command.append("--remove_signal_outliers")
        if args.bed is not None: command.extend(["--bed", str(args.bed)])
        receipt = run(command)
        plot_count_match = re.search(r"Number of plots:\s*(\d+)", receipt["stdout_tail"])
        maximum_plot_count = args.pileup_read_limit if args.mode == "pileup" else 1
        if plot_count_match is None or not 1 <= int(plot_count_match.group(1)) <= maximum_plot_count:
            raise ValueError("Squigualiser did not report a bounded positive plot count")
        receipt["rendered_plot_count"] = int(plot_count_match.group(1))
    receipt["bounded_blow5_extraction"] = extraction_receipts
    receipt["selected_read_ids"] = read_ids
    receipt["parent_sha256s"] = {
        "mapping_sha256": sha(args.mapping),
        "mapping_index_sha256": sha(Path(f"{args.mapping}.tbi")) if args.mapping.name.endswith(".gz") else None,
        "blow5": raw_parent_hashes(args.blow5),
        "sequence_bam_sha256": sha(args.sequence_bam) if args.sequence_bam is not None else None,
        "managed_reference_sha256": sha(args.reference_fasta) if args.reference_fasta is not None else None,
        "managed_bed": None if args.bed is None else {
            "sha256": sha(args.bed),
            "size_bytes": args.bed.stat().st_size,
        },
    }
    safe_render_artifacts(args.output_dir, args.report, receipt)


def cmd_select_region(args: argparse.Namespace) -> None:
    read_ids = reads_overlapping_region(
        args.mapping,
        args.region,
        args.limit,
        args.strand,
        args.molecule_type,
    )
    write_json(args.report, {
        "schema": "bms.ont-signal-region-selection.v1",
        "selected_read_ids": read_ids,
        "parent_sha256s": {
            "mapping_sha256": sha(args.mapping),
            "mapping_index_sha256": sha(Path(f"{args.mapping}.tbi")),
        },
    })


def cmd_broker(args: argparse.Namespace) -> None:
    global _ACTIVE_BROKER_REQUEST

    socket_path = args.socket
    if socket_path.exists() or socket_path.is_symlink():
        raise ValueError("broker socket path must not already exist")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(1)
        listener.settimeout(args.timeout_seconds)
        try:
            connection, _address = listener.accept()
        except socket.timeout as exc:
            raise TimeoutError("broker connection timed out") from exc
        with connection:
            with receive_fd_request(connection, timeout_seconds=args.timeout_seconds) as request:
                _ACTIVE_BROKER_REQUEST = request
                try:
                    operation_args = parser().parse_args(request.operation_argv)
                    if operation_args.operation == "broker":
                        raise ValueError("nested broker operation is forbidden")
                    verify_active_broker_aliases()
                    _dispatch(operation_args)
                    verify_active_broker_aliases()
                finally:
                    _ACTIVE_BROKER_REQUEST = None
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="operation", required=True)
    moves = sub.add_parser("validate-moves")
    moves.add_argument("--bam", type=Path, required=True); moves.add_argument("--blow5", type=Path, action="append", required=True)
    moves.add_argument("--molecule-type", choices=("dna", "rna"), required=True)
    moves.add_argument("--filtered-bam", type=Path, required=True); moves.add_argument("--inventory", type=Path, required=True); moves.add_argument("--report", type=Path, required=True)
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--original-bam", type=Path, required=True); calibrate.add_argument("--filtered-bam", type=Path, required=True); calibrate.add_argument("--inventory", type=Path, required=True); calibrate.add_argument("--blow5", type=Path, action="append", required=True)
    calibrate.add_argument("--molecule-type", choices=("dna", "rna"), required=True)
    calibrate.add_argument("--sample-count", type=int, required=True); calibrate.add_argument("--raw-manifest-sha256", required=True); calibrate.add_argument("--move-artifact-sha256", required=True); calibrate.add_argument("--move-inventory-sha256", required=True); calibrate.add_argument("--basecall-model-id", required=True)
    calibrate.add_argument("--output-dir", type=Path, required=True); calibrate.add_argument("--report", type=Path, required=True)
    reform = sub.add_parser("reform")
    reform.add_argument("--original-bam", type=Path, required=True); reform.add_argument("--filtered-bam", type=Path, required=True); reform.add_argument("--inventory", type=Path, required=True); reform.add_argument("--blow5", type=Path, action="append", required=True); reform.add_argument("--output", type=Path, required=True); reform.add_argument("--report", type=Path, required=True)
    reform.add_argument("--molecule-type", choices=("dna", "rna"), required=True)
    reform.add_argument("--kmer-length", type=int, required=True); reform.add_argument("--signal-move-offset", type=int, required=True)
    realign = sub.add_parser("realign")
    realign.add_argument("--original-bam", type=Path, required=True); realign.add_argument("--filtered-bam", type=Path, required=True); realign.add_argument("--inventory", type=Path, required=True); realign.add_argument("--blow5", type=Path, action="append", required=True); realign.add_argument("--reform-paf", type=Path, required=True); realign.add_argument("--alignment-bam", type=Path, required=True); realign.add_argument("--alignment-index", type=Path, required=True); realign.add_argument("--reference-fasta", type=Path, required=True); realign.add_argument("--molecule-type", choices=("dna", "rna"), required=True); realign.add_argument("--kmer-length", type=int, required=True); realign.add_argument("--signal-move-offset", type=int, required=True); realign.add_argument("--output", type=Path, required=True); realign.add_argument("--report", type=Path, required=True)
    render = sub.add_parser("render")
    render.add_argument("--mode", choices=("read", "reference", "pileup"), required=True); render.add_argument("--blow5", type=Path, action="append", required=True); render.add_argument("--mapping", type=Path, required=True); render.add_argument("--output-dir", type=Path, required=True); render.add_argument("--report", type=Path, required=True)
    render.add_argument("--read-id"); render.add_argument("--selected-read-id", action="append"); render.add_argument("--region"); render.add_argument("--sequence-bam", type=Path); render.add_argument("--reference-fasta", type=Path); render.add_argument("--bed", type=Path); render.add_argument("--molecule-type", choices=("dna", "rna"), required=True); render.add_argument("--kmer-length", type=int, required=True)
    render.add_argument("--strand", choices=("forward", "reverse"), default="forward"); render.add_argument("--signal-units", choices=("pA", "raw_adc"), default="pA"); render.add_argument("--scale", choices=("none", "medmad", "znorm", "scaledpA"), default="none")
    render.add_argument("--base-shift", type=int, default=0); render.add_argument("--point-size", type=float, default=0.5)
    render.add_argument("--fixed-width", action="store_true"); render.add_argument("--base-width", type=int, default=10); render.add_argument("--base-limit", type=int, default=1000); render.add_argument("--signal-sample-limit", type=int, default=100000); render.add_argument("--pileup-read-limit", type=int, default=20)
    render.add_argument("--loose-bound", action="store_true"); render.add_argument("--show-samples", action="store_true"); render.add_argument("--show-base-colours", action="store_true"); render.add_argument("--remove-signal-outliers", action="store_true")
    select_region = sub.add_parser("select-region")
    select_region.add_argument("--mapping", type=Path, required=True); select_region.add_argument("--region", required=True); select_region.add_argument("--limit", type=int, required=True); select_region.add_argument("--strand", choices=("forward", "reverse"), required=True); select_region.add_argument("--molecule-type", choices=("dna", "rna"), required=True); select_region.add_argument("--report", type=Path, required=True)
    broker = sub.add_parser("broker")
    broker.add_argument("--socket", type=Path, default=Path("/broker/parents.sock"))
    broker.add_argument("--timeout-seconds", type=float, default=30.0)
    return root


def _dispatch(args: argparse.Namespace) -> None:
    {
        "validate-moves": cmd_validate_moves,
        "calibrate": cmd_calibrate,
        "reform": cmd_reform,
        "realign": cmd_realign,
        "render": cmd_render,
        "select-region": cmd_select_region,
        "broker": cmd_broker,
    }[args.operation](args)


def main() -> int:
    args = parser().parse_args()
    _dispatch(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
