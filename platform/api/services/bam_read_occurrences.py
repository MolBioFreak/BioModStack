"""Link reads to the BAM records they came from, by position in the file.

A barcode's reads are assigned to one of several references as FASTQ, but the
modification calls and signal tags live only in the BAM. Read names may repeat,
so the link is the record's position: FASTQ record N is written from primary
BAM record N, and record N is later copied whole into its reference's BAM.
Records are never re-encoded, so every tag keeps its exact bytes.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import struct
import zlib
from pathlib import Path
from typing import Collection, Iterator, Mapping

_NOT_PRIMARY = 0x900
_REVERSE = 0x10
_BASES = str.maketrans("0123456789abcdef", "=ACMGRSVTWYHKDBN")
_COMPLEMENT = str.maketrans("ACGTMRWSYKVHDBN=", "TGCAKYWSRMBDHVN=")
_PHRED33 = bytes(min(value + 33, 126) for value in range(256))
_BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
_BLOCK = 0xFF00


class BamRecordError(ValueError):
    """The file is not a readable BAM."""


class _Hashing(io.RawIOBase):
    """Hash the compressed file while it is read once, front to back."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self.sha256 = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        count = self._handle.readinto(buffer)
        if count:
            self.sha256.update(memoryview(buffer)[:count])
        return count or 0


def _exact(stream, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise BamRecordError("BAM ends inside a record")
    return data


def _records(path: Path) -> Iterator[tuple[str, bytes, object]]:
    """Yield ('header', bytes) once, then ('record', bytes) per record, then ('sha256', hex)."""
    with path.open("rb") as raw:
        hashing = _Hashing(raw)
        stream = io.BufferedReader(gzip.GzipFile(fileobj=hashing), 1 << 20)
        try:
            head = _exact(stream, 8)
            if head[:4] != b"BAM\x01":
                raise BamRecordError("file is not a BAM")
            parts = [head, _exact(stream, struct.unpack("<i", head[4:])[0])]
            count = _exact(stream, 4)
            parts.append(count)
            for _ in range(struct.unpack("<i", count)[0]):
                length = _exact(stream, 4)
                parts.append(length)
                parts.append(_exact(stream, struct.unpack("<i", length)[0] + 4))
            yield "header", b"".join(parts)
            while True:
                size = stream.read(4)
                if not size:
                    break
                if len(size) != 4:
                    raise BamRecordError("BAM ends inside a record")
                yield "record", size + _exact(stream, struct.unpack("<i", size)[0])
        except (OSError, EOFError, zlib.error, struct.error) as exc:
            raise BamRecordError("BAM could not be read") from exc
        while hashing.readinto(bytearray(1 << 20)):
            pass
        yield "sha256", hashing.sha256.hexdigest().encode("ascii")


def _flag(record: bytes) -> int:
    return struct.unpack_from("<H", record, 18)[0]


def _fastq_lines(record: bytes) -> str:
    name_length = record[12]
    cigar_count = struct.unpack_from("<H", record, 16)[0]
    length = struct.unpack_from("<i", record, 20)[0]
    name = record[36:36 + name_length - 1].decode("ascii")
    start = 36 + name_length + 4 * cigar_count
    packed = (length + 1) // 2
    sequence = record[start:start + packed].hex().translate(_BASES)[:length]
    quality_bytes = record[start + packed:start + packed + length]
    quality = "!" * length if length and quality_bytes[0] == 0xFF else quality_bytes.translate(_PHRED33).decode("ascii")
    if _flag(record) & _REVERSE:
        sequence = sequence.translate(_COMPLEMENT)[::-1]
        quality = quality[::-1]
    return f"@{name}\n{sequence}\n+\n{quality}\n"


def bam_to_fastq(bam: Path, fastq: Path) -> dict[str, object]:
    """Write one FASTQ record per primary BAM record, in file order."""
    records = other = 0
    digest = ""
    with fastq.open("w", encoding="ascii", newline="\n") as out:
        for kind, data in _records(bam):
            if kind == "record":
                if _flag(data) & _NOT_PRIMARY:
                    other += 1
                    continue
                records += 1
                out.write(_fastq_lines(data))
            elif kind == "sha256":
                digest = data.decode("ascii")
        out.flush()
        os.fsync(out.fileno())
    return {"records": records, "not_primary": other, "bam_sha256": digest}


class _BamWriter:
    def __init__(self, path: Path, header: bytes) -> None:
        self.path = path
        self.records = 0
        self.sha256 = hashlib.sha256()
        self._out = path.open("xb")
        self._pending = bytearray(header)

    def _write(self, data: bytes) -> None:
        self.sha256.update(data)
        self._out.write(data)

    def _block(self, data: bytes) -> None:
        deflate = zlib.compressobj(6, zlib.DEFLATED, -15)
        body = deflate.compress(data) + deflate.flush()
        self._write(
            b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00"
            + struct.pack("<H", len(body) + 25) + body
            + struct.pack("<II", zlib.crc32(data), len(data))
        )

    def add(self, record: bytes) -> None:
        self.records += 1
        self._pending += record
        while len(self._pending) >= _BLOCK:
            self._block(bytes(self._pending[:_BLOCK]))
            del self._pending[:_BLOCK]

    def close(self) -> None:
        if self._pending:
            self._block(bytes(self._pending))
        self._write(_BGZF_EOF)
        self._out.flush()
        os.fsync(self._out.fileno())
        self._out.close()


def split_bam(bam: Path, outputs: Mapping[Path, Collection[int]]) -> dict[str, object]:
    """Copy primary record N (counted from 1) whole into the output that lists N."""
    wanted: dict[int, Path] = {}
    for path, ordinals in outputs.items():
        for ordinal in ordinals:
            if wanted.setdefault(int(ordinal), path) != path:
                raise BamRecordError("one read is listed for two outputs")
    writers: dict[Path, _BamWriter] = {}
    records = 0
    digest = ""
    try:
        for kind, data in _records(bam):
            if kind == "header":
                writers = {path: _BamWriter(path, data) for path in outputs}
            elif kind == "record":
                if _flag(data) & _NOT_PRIMARY:
                    continue
                records += 1
                path = wanted.get(records)
                if path is not None:
                    writers[path].add(data)
            else:
                digest = data.decode("ascii")
        for writer in writers.values():
            writer.close()
    except BaseException:
        for writer in writers.values():
            writer._out.close()
            writer.path.unlink(missing_ok=True)
        raise
    missing = sorted(ordinal for ordinal in wanted if ordinal > records)
    if missing:
        for writer in writers.values():
            writer.path.unlink(missing_ok=True)
        raise BamRecordError("the BAM has fewer reads than the assignment lists")
    return {
        "records": records,
        "bam_sha256": digest,
        "written": {path: {"records": writer.records, "sha256": writer.sha256.hexdigest()} for path, writer in writers.items()},
    }
