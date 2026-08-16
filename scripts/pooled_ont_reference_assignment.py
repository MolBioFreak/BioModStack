#!/usr/bin/env python3
"""Strict pooled ONT reference assignment and review-artifact builder.

The command has two deliberately explicit stages:

``preflight``
    Verifies the immutable reference-set snapshot and strict FASTQ input
    policy, then writes the combined intended-reference FASTA and the valid
    FASTQ that will be aligned.

``classify``
    Re-verifies the snapshot and preflight evidence, reads the sorted
    minimap2 BAM through samtools, and emits exactly one disposition for each
    valid read.  It stops at operator review.  No downstream sequence
    inference is performed here.

The implementation does not provide a substitute aligner or a permissive
input path.  The workflow is responsible for requiring minimap2, samtools,
and a usable indexed BAM before this classifier is called.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
from typing import Any, Iterable, Iterator, Mapping, Sequence, TextIO


SCHEMA = "bms.ngs.reference-set.v1"
SUMMARY_SCHEMA = "bms.ngs.pooled-reference-assignment-summary.v1"
PREFLIGHT_SCHEMA = "bms.ngs.fastq-preflight.v1"
IGV_SESSION_SCHEMA = "bms.ngs.intended-pool-igv-session.v1"
OCCURRENCE_MAP_SCHEMA = "bms.ngs.fastq-occurrence-map.v1"
OCCURRENCE_MAP_FILENAME = "occurrence_map.json"
WORKFLOW_ID = "ont_pooled_reference_assignment"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IDENTITY_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,128}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OCCURRENCE_ID_RE = re.compile(r"^occurrence_[1-9][0-9]*$")
DNA_ALPHABET = frozenset("ACGTN")
FASTQ_QUALITY_MIN = 33
FASTQ_QUALITY_MAX = 126


class PooledAssignmentError(ValueError):
    """A fail-closed input, evidence, or accounting error."""


@dataclass(frozen=True)
class ReferenceEntry:
    target_id: str
    label: str
    molbio_sequence_id: str
    molbio_revision_id: str
    revision_sha256: str
    fasta_path: str
    fasta_sha256: str
    indistinguishable_group: str | None
    normalized_sequence: str


@dataclass(frozen=True)
class ReferenceSet:
    manifest_path: Path
    snapshot_root: Path
    manifest_id: str
    manifest_sha256: str
    manifest_file_sha256: str
    entries: tuple[ReferenceEntry, ...]

    @property
    def target_by_id(self) -> dict[str, ReferenceEntry]:
        return {entry.target_id: entry for entry in self.entries}

    @property
    def indistinguishable_target_ids(self) -> frozenset[str]:
        groups: dict[str, list[str]] = {}
        for entry in self.entries:
            if entry.indistinguishable_group:
                groups.setdefault(entry.indistinguishable_group, []).append(entry.target_id)
        return frozenset(
            target_id
            for target_ids in groups.values()
            if len(target_ids) > 1
            for target_id in target_ids
        )


@dataclass(frozen=True)
class FastqRecord:
    ordinal: int
    occurrence_id: str
    source_read_id: str
    source_header: str
    sequence: str
    quality: str

    @property
    def header(self) -> str:
        """Compatibility view of the original FASTQ header payload."""
        return self.source_header


@dataclass(frozen=True)
class FastqPreflight:
    source_sha256: str
    input_records: int
    valid_records: tuple[FastqRecord, ...]
    rejected_by_input_policy: int
    rejected_reasons: Mapping[str, int]


@dataclass(frozen=True)
class AlignmentEvidence:
    occurrence_id: str
    target_id: str
    mapq: int
    alignment_score: int
    secondary: bool


@dataclass(frozen=True)
class ReadAssignment:
    record: FastqRecord
    disposition: str
    target_id: str | None
    best_score: int | None
    second_score: int | None
    score_delta: int | None
    best_mapq: int | None
    reason: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def occurrence_id_for_ordinal(ordinal: int) -> str:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise PooledAssignmentError("FASTQ record ordinal must be a positive integer")
    return f"occurrence_{ordinal}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise PooledAssignmentError(f"cannot read required file for digest: {path}") from exc
    return digest.hexdigest()


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PooledAssignmentError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PooledAssignmentError(f"cannot parse JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise PooledAssignmentError(f"JSON input must be an object: {path}")
    return payload


def canonical_manifest_sha256(payload: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _require_string(payload: Mapping[str, Any], key: str, *, pattern: re.Pattern[str] | None = None) -> str:
    value = payload.get(key)
    if type(value) is not str or not value:
        raise PooledAssignmentError(f"{key} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise PooledAssignmentError(f"{key} has an invalid format")
    return value


def _assert_no_symlink_components(root: Path, relative: PurePosixPath) -> None:
    """Reject symlinks in a manifest-relative path, including the leaf."""
    cursor = root
    for component in relative.parts:
        if component in {"", ".", ".."}:
            raise PooledAssignmentError("reference paths must not contain empty, dot, or parent components")
        cursor = cursor / component
        try:
            if cursor.is_symlink():
                raise PooledAssignmentError(f"reference path traverses a symlink: {relative}")
        except OSError as exc:
            raise PooledAssignmentError(f"cannot inspect reference path: {relative}") from exc


def _safe_snapshot_relative_path(value: str) -> PurePosixPath:
    if "\\" in value or value.startswith("/"):
        raise PooledAssignmentError("fasta_path must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PooledAssignmentError("fasta_path must remain inside the manifest snapshot")
    return path


def _read_single_fasta(path: Path) -> str:
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError(f"cannot read FASTA entry: {path}") from exc

    record_count = 0
    saw_header = False
    sequence_chunks: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            record_count += 1
            if record_count > 1:
                raise PooledAssignmentError(f"FASTA entry must contain exactly one record: {path}")
            if len(line) == 1:
                raise PooledAssignmentError(f"FASTA header is empty: {path}:{line_number}")
            saw_header = True
            continue
        if not saw_header:
            raise PooledAssignmentError(f"FASTA sequence precedes its header: {path}:{line_number}")
        if any(character.isspace() for character in line):
            raise PooledAssignmentError(f"FASTA sequence contains internal whitespace: {path}:{line_number}")
        sequence_chunks.append(line.upper())

    if record_count != 1 or not sequence_chunks:
        raise PooledAssignmentError(f"FASTA entry must contain one non-empty record: {path}")
    sequence = "".join(sequence_chunks)
    invalid = sorted(set(sequence) - DNA_ALPHABET)
    if invalid:
        raise PooledAssignmentError(
            f"FASTA entry contains unsupported nucleotide symbols {''.join(invalid)}: {path}"
        )
    return sequence


def validate_reference_set(manifest_path: Path, snapshot_root: Path | None = None) -> ReferenceSet:
    """Validate the complete immutable manifest and every referenced FASTA."""
    manifest_path = manifest_path.absolute()
    if not manifest_path.exists() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise PooledAssignmentError("reference-set manifest must be an existing regular non-symlink file")
    root = (snapshot_root or manifest_path.parent).absolute()
    if not root.exists() or not root.is_dir() or root.is_symlink() or root.resolve() != root:
        raise PooledAssignmentError("reference-set snapshot root must be an existing directory")
    try:
        manifest_relative = PurePosixPath(manifest_path.relative_to(root).as_posix())
    except ValueError as exc:
        raise PooledAssignmentError("reference-set manifest must be inside its snapshot root") from exc
    if len(manifest_relative.parts) != 1:
        raise PooledAssignmentError("reference-set manifest must be at the snapshot root")

    payload = load_json_object(manifest_path)
    expected_keys = {"schema", "mode", "manifest_id", "manifest_sha256", "entries"}
    if set(payload) != expected_keys:
        unexpected = sorted(set(payload) - expected_keys)
        missing = sorted(expected_keys - set(payload))
        raise PooledAssignmentError(f"reference-set manifest keys are not exact; missing={missing}, unexpected={unexpected}")
    if payload.get("schema") != SCHEMA:
        raise PooledAssignmentError(f"unsupported reference-set schema: {payload.get('schema')!r}")
    if payload.get("mode") != "pooled":
        raise PooledAssignmentError("reference-set manifest mode must be pooled")

    manifest_id = _require_string(payload, "manifest_id", pattern=MANIFEST_ID_RE)
    manifest_sha256 = _require_string(payload, "manifest_sha256", pattern=SHA256_RE)
    observed_manifest_sha256 = canonical_manifest_sha256(payload)
    if manifest_sha256 != observed_manifest_sha256:
        raise PooledAssignmentError("reference-set manifest_sha256 does not match the immutable manifest payload")

    raw_entries = payload.get("entries")
    if type(raw_entries) is not list or not 2 <= len(raw_entries) <= 96:
        raise PooledAssignmentError("pooled reference-set entries must contain between 2 and 96 entries")

    entries: list[ReferenceEntry] = []
    target_ids: set[str] = set()
    revision_ids: set[str] = set()
    identity_pairs: set[tuple[str, str]] = set()
    sequence_groups: dict[str, list[ReferenceEntry]] = {}
    entry_keys = {
        "target_id",
        "label",
        "molbio_sequence_id",
        "molbio_revision_id",
        "revision_sha256",
        "fasta_path",
        "fasta_sha256",
        "indistinguishable_group",
    }

    for index, raw_entry in enumerate(raw_entries, start=1):
        if type(raw_entry) is not dict:
            raise PooledAssignmentError(f"reference-set entry {index} must be an object")
        if not set(raw_entry).issubset(entry_keys) or not {
            "target_id",
            "label",
            "molbio_sequence_id",
            "molbio_revision_id",
            "revision_sha256",
            "fasta_path",
            "fasta_sha256",
        }.issubset(raw_entry):
            unexpected = sorted(set(raw_entry) - entry_keys)
            missing = sorted(
                {
                    "target_id",
                    "label",
                    "molbio_sequence_id",
                    "molbio_revision_id",
                    "revision_sha256",
                    "fasta_path",
                    "fasta_sha256",
                }
                - set(raw_entry)
            )
            raise PooledAssignmentError(f"reference-set entry {index} keys are not exact; missing={missing}, unexpected={unexpected}")

        target_id = _require_string(raw_entry, "target_id", pattern=TARGET_ID_RE)
        label = _require_string(raw_entry, "label")
        if any(character in label for character in "\r\n") or any(ord(character) < 32 for character in label):
            raise PooledAssignmentError(f"reference-set entry {index} label contains control characters")
        sequence_id = _require_string(raw_entry, "molbio_sequence_id", pattern=IDENTITY_RE)
        revision_id = _require_string(raw_entry, "molbio_revision_id", pattern=IDENTITY_RE)
        revision_sha256 = _require_string(raw_entry, "revision_sha256", pattern=SHA256_RE)
        fasta_path_text = _require_string(raw_entry, "fasta_path")
        fasta_path = _safe_snapshot_relative_path(fasta_path_text)
        fasta_sha256 = _require_string(raw_entry, "fasta_sha256", pattern=SHA256_RE)
        group_value = raw_entry.get("indistinguishable_group")
        if group_value is not None:
            if type(group_value) is not str or GROUP_RE.fullmatch(group_value) is None:
                raise PooledAssignmentError(f"reference-set entry {index} indistinguishable_group is invalid")
            indistinguishable_group = group_value
        else:
            indistinguishable_group = None

        if target_id in target_ids:
            raise PooledAssignmentError(f"duplicate target_id: {target_id}")
        if revision_id in revision_ids:
            raise PooledAssignmentError(f"duplicate molbio_revision_id: {revision_id}")
        identity = (sequence_id, revision_id)
        if identity in identity_pairs:
            raise PooledAssignmentError(f"duplicate target/revision identity: {sequence_id}/{revision_id}")
        target_ids.add(target_id)
        revision_ids.add(revision_id)
        identity_pairs.add(identity)

        _assert_no_symlink_components(root, fasta_path)
        fasta_file = root.joinpath(*fasta_path.parts)
        try:
            resolved_fasta = fasta_file.resolve(strict=True)
            resolved_fasta.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise PooledAssignmentError(f"fasta_path escapes or is unavailable: {fasta_path_text}") from exc
        if not fasta_file.is_file() or fasta_file.is_symlink():
            raise PooledAssignmentError(f"fasta_path must be a regular non-symlink file: {fasta_path_text}")
        observed_fasta_sha256 = sha256_file(fasta_file)
        if observed_fasta_sha256 != fasta_sha256:
            raise PooledAssignmentError(f"FASTA file digest mismatch for target {target_id}")
        sequence = _read_single_fasta(fasta_file)
        observed_revision_sha256 = _sha256_bytes(sequence.encode("ascii"))
        if observed_revision_sha256 != revision_sha256:
            raise PooledAssignmentError(f"normalized sequence digest mismatch for target {target_id}")
        entry = ReferenceEntry(
            target_id=target_id,
            label=label,
            molbio_sequence_id=sequence_id,
            molbio_revision_id=revision_id,
            revision_sha256=revision_sha256,
            fasta_path=fasta_path.as_posix(),
            fasta_sha256=fasta_sha256,
            indistinguishable_group=indistinguishable_group,
            normalized_sequence=sequence,
        )
        entries.append(entry)
        sequence_groups.setdefault(sequence, []).append(entry)

    for sequence, identical_entries in sequence_groups.items():
        if len(identical_entries) < 2:
            continue
        groups = {entry.indistinguishable_group for entry in identical_entries}
        if None in groups or len(groups) != 1:
            targets = ", ".join(entry.target_id for entry in identical_entries)
            raise PooledAssignmentError(
                f"identical FASTA entries require one explicit common indistinguishable_group: {targets}"
            )

    return ReferenceSet(
        manifest_path=manifest_path,
        snapshot_root=root,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        manifest_file_sha256=sha256_file(manifest_path),
        entries=tuple(entries),
    )


def _wrap_sequence(sequence: str, width: int = 80) -> Iterator[str]:
    for start in range(0, len(sequence), width):
        yield sequence[start : start + width]


def combined_reference_bytes(reference_set: ReferenceSet) -> bytes:
    chunks: list[str] = []
    for entry in reference_set.entries:
        chunks.append(f">{entry.target_id}\n")
        chunks.extend(f"{line}\n" for line in _wrap_sequence(entry.normalized_sequence))
    return "".join(chunks).encode("ascii")


def build_combined_reference(reference_set: ReferenceSet, output_path: Path) -> str:
    content = combined_reference_bytes(reference_set)
    output_path.write_bytes(content)
    return _sha256_bytes(content)


def _open_fastq(path: Path) -> TextIO:
    try:
        if path.name.lower().endswith(".gz"):
            return gzip.open(path, "rt", encoding="ascii", newline="")
        return path.open("r", encoding="ascii", newline="")
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError(f"cannot open FASTQ input: {path}") from exc


def _strip_line(value: str) -> str:
    return value[:-2] if value.endswith("\r\n") else value.rstrip("\r\n")


def _fastq_rejection_reason(lines: Sequence[str]) -> str | None:
    if len(lines) != 4 or any(line == "" for line in lines[1:]):
        return "truncated_record"
    header, sequence, plus, quality = (_strip_line(line) for line in lines)
    if not header.startswith("@") or len(header) == 1 or not header[1:].split():
        return "invalid_header"
    if not plus.startswith("+"):
        return "invalid_plus_line"
    if not sequence or any(character not in DNA_ALPHABET for character in sequence.upper()):
        return "invalid_sequence"
    if len(sequence) != len(quality):
        return "sequence_quality_length_mismatch"
    if any(not FASTQ_QUALITY_MIN <= ord(character) <= FASTQ_QUALITY_MAX for character in quality):
        return "invalid_quality"
    return None


def strict_fastq_preflight(path: Path, valid_fastq_path: Path) -> FastqPreflight:
    """Keep valid four-line records and count every policy-rejected record."""
    records: list[FastqRecord] = []
    rejected_reasons: dict[str, int] = {}
    input_records = 0
    try:
        with _open_fastq(path) as handle, valid_fastq_path.open("w", encoding="ascii", newline="\n") as valid_handle:
            while True:
                first = handle.readline()
                if first == "":
                    break
                input_records += 1
                lines = [first, handle.readline(), handle.readline(), handle.readline()]
                reason = _fastq_rejection_reason(lines)
                if reason is not None:
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                    continue
                header = _strip_line(lines[0])[1:]
                sequence = _strip_line(lines[1]).upper()
                quality = _strip_line(lines[3])
                source_read_id = header.split()[0]
                occurrence_id = occurrence_id_for_ordinal(input_records)
                record = FastqRecord(
                    ordinal=input_records,
                    occurrence_id=occurrence_id,
                    source_read_id=source_read_id,
                    source_header=header,
                    sequence=sequence,
                    quality=quality,
                )
                records.append(record)
                valid_handle.write(f"@{occurrence_id}\n{sequence}\n+\n{quality}\n")
    except PooledAssignmentError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError(f"strict FASTQ preflight could not read or write records: {path}") from exc

    return FastqPreflight(
        source_sha256=sha256_file(path),
        input_records=input_records,
        valid_records=tuple(records),
        rejected_by_input_policy=sum(rejected_reasons.values()),
        rejected_reasons=dict(sorted(rejected_reasons.items())),
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _occurrence_map_payload(source_fastq: Path, preflight: FastqPreflight) -> dict[str, Any]:
    return {
        "schema": OCCURRENCE_MAP_SCHEMA,
        "input_fastq_filename": source_fastq.name,
        "input_fastq_sha256": preflight.source_sha256,
        "count": len(preflight.valid_records),
        "records": [
            {
                "occurrence_id": record.occurrence_id,
                "source_read_id": record.source_read_id,
                "source_header": record.source_header,
                "input_ordinal": record.ordinal,
            }
            for record in preflight.valid_records
        ],
    }


def run_preflight(manifest_path: Path, snapshot_root: Path, source_fastq: Path, out_dir: Path) -> None:
    reference_set = validate_reference_set(manifest_path, snapshot_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    combined_sha256 = build_combined_reference(reference_set, out_dir / "combined_intended_reference.fasta")
    preflight = strict_fastq_preflight(source_fastq, out_dir / "valid_reads.fastq")
    occurrence_map_path = out_dir / OCCURRENCE_MAP_FILENAME
    _write_json(occurrence_map_path, _occurrence_map_payload(source_fastq, preflight))
    occurrence_map_sha256 = sha256_file(occurrence_map_path)
    _write_json(
        out_dir / "fastq_preflight.json",
        {
            "schema": PREFLIGHT_SCHEMA,
            "input_fastq_filename": source_fastq.name,
            "input_fastq_sha256": preflight.source_sha256,
            "input_records": preflight.input_records,
            "valid_fastq_reads": len(preflight.valid_records),
            "rejected_by_input_policy": preflight.rejected_by_input_policy,
            "rejected_reasons": dict(preflight.rejected_reasons),
            "occurrence_map_path": occurrence_map_path.name,
            "occurrence_map_sha256": occurrence_map_sha256,
            "occurrence_map_count": len(preflight.valid_records),
            "reference_set_manifest_id": reference_set.manifest_id,
            "reference_set_manifest_sha256": reference_set.manifest_sha256,
            "combined_reference_sha256": combined_sha256,
            "policy": "strict_four_line_fastq_dna_acgtn_occurrence_normalization",
        },
    )


def _samtools_view(samtools_command: Sequence[str], bam_path: Path) -> list[str]:
    command = [*samtools_command, "view", "-h", str(bam_path)]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise PooledAssignmentError("samtools is required to inspect the pooled assignment BAM") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "no stderr"
        raise PooledAssignmentError(f"samtools view failed for pooled assignment BAM: {detail}")
    return completed.stdout.splitlines()


def parse_sam_evidence(
    sam_lines: Iterable[str],
    reference_set: ReferenceSet,
    occurrence_ids: frozenset[str],
) -> dict[str, list[AlignmentEvidence]]:
    """Parse minimap2 SAM evidence while retaining primary and secondary hits."""
    expected_lengths = {entry.target_id: len(entry.normalized_sequence) for entry in reference_set.entries}
    observed_contigs: dict[str, int] = {}
    evidence: dict[str, list[AlignmentEvidence]] = {}
    for line_number, line in enumerate(sam_lines, start=1):
        if not line:
            continue
        if line.startswith("@SQ"):
            tags = dict(
                field.split(":", 1)
                for field in line.split("\t")[1:]
                if ":" in field
            )
            name = tags.get("SN")
            length_text = tags.get("LN")
            if not name or length_text is None:
                raise PooledAssignmentError(f"SAM @SQ header is incomplete at line {line_number}")
            try:
                observed_contigs[name] = int(length_text)
            except ValueError as exc:
                raise PooledAssignmentError(f"SAM @SQ length is invalid at line {line_number}") from exc
            continue
        if line.startswith("@"):
            continue
        fields = line.split("\t")
        if len(fields) < 11:
            raise PooledAssignmentError(f"SAM alignment has fewer than 11 fields at line {line_number}")
        occurrence_id, flag_text, reference_name, _position, mapq_text, cigar = fields[:6]
        if occurrence_id not in occurrence_ids:
            raise PooledAssignmentError(
                "BAM contains an alignment for an occurrence rejected by FASTQ preflight: "
                f"{occurrence_id}"
            )
        try:
            flag = int(flag_text)
            mapq = int(mapq_text)
        except ValueError as exc:
            raise PooledAssignmentError(f"SAM flag or MAPQ is invalid at line {line_number}") from exc
        if not 0 <= mapq <= 255:
            raise PooledAssignmentError(f"SAM MAPQ is outside 0..255 at line {line_number}")
        tags: dict[str, str] = {}
        for raw_tag in fields[11:]:
            parts = raw_tag.split(":", 2)
            if len(parts) == 3:
                tags[parts[0]] = f"{parts[1]}:{parts[2]}"
        if flag & 0x4 or reference_name == "*":
            continue
        if reference_name not in expected_lengths:
            raise PooledAssignmentError(f"BAM contains an unexpected reference contig: {reference_name}")
        if cigar == "*":
            raise PooledAssignmentError(f"mapped SAM alignment lacks a CIGAR at line {line_number}")
        if "AS" not in tags or not tags["AS"].startswith("i:"):
            raise PooledAssignmentError(f"mapped SAM alignment lacks an integer AS tag at line {line_number}")
        try:
            alignment_score = int(tags["AS"][2:])
        except ValueError as exc:
            raise PooledAssignmentError(f"SAM AS tag is invalid at line {line_number}") from exc
        if not flag & 0x800:  # supplementary segments are not independent competitors
            evidence.setdefault(occurrence_id, []).append(
                AlignmentEvidence(
                    occurrence_id=occurrence_id,
                    target_id=reference_name,
                    mapq=mapq,
                    alignment_score=alignment_score,
                    secondary=bool(flag & 0x100),
                )
            )

    expected_contigs = set(expected_lengths)
    if set(observed_contigs) != expected_contigs:
        raise PooledAssignmentError(
            "BAM @SQ contigs do not exactly match the combined intended reference"
        )
    if any(observed_contigs[name] != expected_lengths[name] for name in expected_contigs):
        raise PooledAssignmentError("BAM @SQ contig lengths do not match the combined intended reference")
    return evidence


def classify_assignments(
    records: Sequence[FastqRecord],
    evidence_by_occurrence_id: Mapping[str, Sequence[AlignmentEvidence]],
    reference_set: ReferenceSet,
    *,
    min_mapq: int,
    min_alignment_score_margin: int,
) -> tuple[ReadAssignment, ...]:
    if isinstance(min_mapq, bool) or not isinstance(min_mapq, int) or not 0 <= min_mapq <= 255:
        raise PooledAssignmentError("min_mapq must be an integer from 0 through 255")
    if isinstance(min_alignment_score_margin, bool) or not isinstance(min_alignment_score_margin, int) or min_alignment_score_margin < 0:
        raise PooledAssignmentError("min_alignment_score_margin must be a non-negative integer")

    identical_targets = reference_set.indistinguishable_target_ids
    assignments: list[ReadAssignment] = []
    for record in records:
        all_evidence = list(evidence_by_occurrence_id.get(record.occurrence_id, ()))
        candidate_by_target: dict[str, AlignmentEvidence] = {}
        for evidence in all_evidence:
            if evidence.mapq < min_mapq:
                continue
            previous = candidate_by_target.get(evidence.target_id)
            if previous is None or (evidence.alignment_score, evidence.mapq) > (
                previous.alignment_score,
                previous.mapq,
            ):
                candidate_by_target[evidence.target_id] = evidence
        ranked = sorted(
            candidate_by_target.values(),
            key=lambda value: (-value.alignment_score, -value.mapq, value.target_id),
        )
        if not ranked:
            reason = "no_alignment_evidence" if not all_evidence else "no_alignment_at_min_mapq"
            assignments.append(
                ReadAssignment(record, "unclassified", None, None, None, None, None, reason)
            )
            continue

        best = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        score_delta = best.alignment_score - second.alignment_score if second else None
        if best.target_id in identical_targets:
            assignments.append(
                ReadAssignment(
                    record,
                    "ambiguous",
                    None,
                    best.alignment_score,
                    second.alignment_score if second else None,
                    score_delta,
                    best.mapq,
                    "identical_targets_require_indistinguishable_group",
                )
            )
        elif second is not None and score_delta is not None and score_delta <= min_alignment_score_margin:
            assignments.append(
                ReadAssignment(
                    record,
                    "ambiguous",
                    None,
                    best.alignment_score,
                    second.alignment_score,
                    score_delta,
                    best.mapq,
                    "near_tie_within_score_margin",
                )
            )
        else:
            assignments.append(
                ReadAssignment(
                    record,
                    f"target:{best.target_id}",
                    best.target_id,
                    best.alignment_score,
                    second.alignment_score if second else None,
                    score_delta,
                    best.mapq,
                    "unique_competitive_alignment",
                )
            )
    return tuple(assignments)


def _parse_valid_fastq(path: Path, occurrence_map: Sequence[FastqRecord]) -> tuple[FastqRecord, ...]:
    records: list[FastqRecord] = []
    expected_by_occurrence = {record.occurrence_id: record for record in occurrence_map}
    seen_occurrences: set[str] = set()
    try:
        with _open_fastq(path) as handle:
            while True:
                first = handle.readline()
                if first == "":
                    break
                lines = [first, handle.readline(), handle.readline(), handle.readline()]
                reason = _fastq_rejection_reason(lines)
                if reason is not None:
                    raise PooledAssignmentError(
                        "the workflow-produced valid FASTQ contains a policy-rejected record: "
                        f"{reason}"
                    )
                header = _strip_line(lines[0])[1:]
                occurrence_id = header.split()[0]
                if header != occurrence_id:
                    raise PooledAssignmentError(
                        "the workflow-produced valid FASTQ must contain only synthetic occurrence QNAMEs"
                    )
                source_record = expected_by_occurrence.get(occurrence_id)
                if source_record is None:
                    raise PooledAssignmentError(
                        f"the workflow-produced valid FASTQ contains an unknown occurrence_id: {occurrence_id}"
                    )
                if occurrence_id in seen_occurrences:
                    raise PooledAssignmentError(
                        f"the workflow-produced valid FASTQ contains a duplicate occurrence_id: {occurrence_id}"
                    )
                seen_occurrences.add(occurrence_id)
                records.append(
                    FastqRecord(
                        ordinal=source_record.ordinal,
                        occurrence_id=occurrence_id,
                        source_read_id=source_record.source_read_id,
                        source_header=source_record.source_header,
                        sequence=_strip_line(lines[1]).upper(),
                        quality=_strip_line(lines[3]),
                    )
                )
    except PooledAssignmentError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError(f"cannot read workflow-produced valid FASTQ: {path}") from exc
    if tuple(record.occurrence_id for record in records) != tuple(
        record.occurrence_id for record in occurrence_map
    ):
        raise PooledAssignmentError(
            "workflow-produced valid FASTQ occurrence order does not match the occurrence map"
        )
    return tuple(records)


def _ensure_regular_nonempty(path: Path, label: str) -> None:
    if not path.exists() or not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise PooledAssignmentError(f"required {label} evidence is missing or empty: {path}")


def _load_occurrence_map(
    path: Path,
    source_fastq: Path,
    expected_sha256: str,
) -> tuple[FastqRecord, ...]:
    _ensure_regular_nonempty(path, "FASTQ occurrence map")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != expected_sha256:
        raise PooledAssignmentError("FASTQ occurrence map digest does not match strict preflight evidence")
    payload = load_json_object(path)
    expected_keys = {
        "schema",
        "input_fastq_filename",
        "input_fastq_sha256",
        "count",
        "records",
    }
    if set(payload) != expected_keys:
        unexpected = sorted(set(payload) - expected_keys)
        missing = sorted(expected_keys - set(payload))
        raise PooledAssignmentError(
            f"FASTQ occurrence map keys are not exact; missing={missing}, unexpected={unexpected}"
        )
    if payload.get("schema") != OCCURRENCE_MAP_SCHEMA:
        raise PooledAssignmentError("FASTQ occurrence map schema is invalid")
    if payload.get("input_fastq_filename") != source_fastq.name:
        raise PooledAssignmentError("FASTQ occurrence map is bound to a different input filename")
    if payload.get("input_fastq_sha256") != sha256_file(source_fastq):
        raise PooledAssignmentError("FASTQ occurrence map is bound to a changed source FASTQ")
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise PooledAssignmentError("FASTQ occurrence map count is invalid")
    raw_records = payload.get("records")
    if type(raw_records) is not list or len(raw_records) != count:
        raise PooledAssignmentError("FASTQ occurrence map count does not match its records")

    records: list[FastqRecord] = []
    occurrence_ids: set[str] = set()
    ordinals: set[int] = set()
    previous_ordinal = 0
    expected_record_keys = {"occurrence_id", "source_read_id", "source_header", "input_ordinal"}
    for index, raw_record in enumerate(raw_records, start=1):
        if type(raw_record) is not dict or set(raw_record) != expected_record_keys:
            raise PooledAssignmentError(f"FASTQ occurrence map record {index} keys are invalid")
        occurrence_id = raw_record.get("occurrence_id")
        source_read_id = raw_record.get("source_read_id")
        source_header = raw_record.get("source_header")
        ordinal = raw_record.get("input_ordinal")
        if type(occurrence_id) is not str or OCCURRENCE_ID_RE.fullmatch(occurrence_id) is None:
            raise PooledAssignmentError(f"FASTQ occurrence map record {index} occurrence_id is invalid")
        if type(source_read_id) is not str or not source_read_id:
            raise PooledAssignmentError(f"FASTQ occurrence map record {index} source_read_id is invalid")
        if type(source_header) is not str or not source_header:
            raise PooledAssignmentError(f"FASTQ occurrence map record {index} source_header is invalid")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise PooledAssignmentError(f"FASTQ occurrence map record {index} input_ordinal is invalid")
        source_header_tokens = source_header.split()
        if not source_header_tokens or source_header_tokens[0] != source_read_id:
            raise PooledAssignmentError(
                f"FASTQ occurrence map record {index} source_read_id does not match source_header"
            )
        if occurrence_id != occurrence_id_for_ordinal(ordinal):
            raise PooledAssignmentError(
                f"FASTQ occurrence map record {index} occurrence_id is not derived from input_ordinal"
            )
        if occurrence_id in occurrence_ids or ordinal in ordinals:
            raise PooledAssignmentError("FASTQ occurrence map contains a duplicate occurrence or ordinal")
        if ordinal <= previous_ordinal:
            raise PooledAssignmentError("FASTQ occurrence map records are not in input order")
        occurrence_ids.add(occurrence_id)
        ordinals.add(ordinal)
        previous_ordinal = ordinal
        records.append(
            FastqRecord(
                ordinal=ordinal,
                occurrence_id=occurrence_id,
                source_read_id=source_read_id,
                source_header=source_header,
                sequence="",
                quality="",
            )
        )
    return tuple(records)


def _artifact_record(path: Path, kind: str, *, count: int | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise PooledAssignmentError(f"required {kind} evidence is missing: {path}")
    artifact: dict[str, Any] = {"kind": kind, "path": path.name, "sha256": sha256_file(path)}
    if count is not None:
        artifact["count"] = count
    return artifact


def _write_bucket_files(out_dir: Path, reference_set: ReferenceSet, assignments: Sequence[ReadAssignment]) -> list[dict[str, Any]]:
    buckets: dict[str, list[FastqRecord]] = {entry.target_id: [] for entry in reference_set.entries}
    buckets["ambiguous"] = []
    buckets["unclassified"] = []
    for assignment in assignments:
        bucket = assignment.target_id if assignment.target_id is not None else assignment.disposition
        if bucket not in buckets:
            bucket = assignment.disposition
        buckets[bucket].append(assignment.record)

    target_summaries: list[dict[str, Any]] = []
    for entry in reference_set.entries:
        bucket = buckets[entry.target_id]
        ids_path = out_dir / f"target_{entry.target_id}.read_ids.txt"
        fastq_path = out_dir / f"target_{entry.target_id}.fastq"
        ids_path.write_text("".join(f"{record.occurrence_id}\n" for record in bucket), encoding="utf-8")
        fastq_path.write_text(
            "".join(f"@{record.occurrence_id}\n{record.sequence}\n+\n{record.quality}\n" for record in bucket),
            encoding="ascii",
        )
        target_summaries.append(
            {
                "target_id": entry.target_id,
                "label": entry.label,
                "molbio_sequence_id": entry.molbio_sequence_id,
                "molbio_revision_id": entry.molbio_revision_id,
                "revision_sha256": entry.revision_sha256,
                "indistinguishable_group": entry.indistinguishable_group,
                "read_count": len(bucket),
                "read_ids_path": ids_path.name,
                "fastq_path": fastq_path.name,
            }
        )

    for bucket_name in ("ambiguous", "unclassified"):
        bucket = buckets[bucket_name]
        (out_dir / f"{bucket_name}.read_ids.txt").write_text(
            "".join(f"{record.occurrence_id}\n" for record in bucket), encoding="utf-8"
        )
        (out_dir / f"{bucket_name}.fastq").write_text(
            "".join(f"@{record.occurrence_id}\n{record.sequence}\n+\n{record.quality}\n" for record in bucket),
            encoding="ascii",
        )
    return target_summaries


def _write_per_read_tsv(path: Path, assignments: Sequence[ReadAssignment]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "occurrence_id",
                "source_read_id",
                "source_header",
                "input_ordinal",
                "disposition",
                "target_id",
                "best_alignment_score",
                "second_alignment_score",
                "alignment_score_delta",
                "best_mapq",
                "reason",
            ]
        )
        for assignment in assignments:
            writer.writerow(
                [
                    assignment.record.occurrence_id,
                    assignment.record.source_read_id,
                    assignment.record.source_header,
                    assignment.record.ordinal,
                    assignment.disposition,
                    assignment.target_id or "",
                    "" if assignment.best_score is None else assignment.best_score,
                    "" if assignment.second_score is None else assignment.second_score,
                    "" if assignment.score_delta is None else assignment.score_delta,
                    "" if assignment.best_mapq is None else assignment.best_mapq,
                    assignment.reason,
                ]
            )


def _count_dispositions(assignments: Sequence[ReadAssignment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for assignment in assignments:
        counts[assignment.disposition] = counts.get(assignment.disposition, 0) + 1
    return dict(sorted(counts.items()))


def _write_igv_session(
    out_dir: Path,
    reference_set: ReferenceSet,
    source_fastq: Path,
    occurrence_map_path: Path,
    occurrence_map_count: int,
    generated_paths: Sequence[tuple[str, str]],
    combined_reference_sha256: str,
) -> None:
    artifacts = [
        {
            "kind": "reference_set_manifest",
            "path": reference_set.manifest_path.name,
            "sha256": reference_set.manifest_file_sha256,
            "declared_manifest_sha256": reference_set.manifest_sha256,
        },
        {
            "kind": "input_fastq",
            "path": source_fastq.name,
            "sha256": sha256_file(source_fastq),
        },
    ]
    for kind, name in generated_paths:
        artifacts.append(_artifact_record(out_dir / name, kind))
    occurrence_map_artifact = _artifact_record(
        occurrence_map_path,
        "occurrence_map",
        count=occurrence_map_count,
    )
    artifacts.append(occurrence_map_artifact)
    artifacts.sort(key=lambda item: item["path"])
    occurrence_map_sha256 = occurrence_map_artifact["sha256"]
    _write_json(
        out_dir / "intended_pool.igv_session.json",
        {
            "schema": IGV_SESSION_SCHEMA,
            "workflow_id": WORKFLOW_ID,
            "manifest_id": reference_set.manifest_id,
            "manifest_sha256": reference_set.manifest_sha256,
            "scientific_status": "REVIEW",
            "release_state": "awaiting_operator_release",
            "reference": {
                "path": "combined_intended_reference.fasta",
                "sha256": combined_reference_sha256,
                "index_path": "combined_intended_reference.fasta.fai",
            },
            "occurrence_map_path": occurrence_map_path.name,
            "occurrence_map_sha256": occurrence_map_sha256,
            "occurrence_map_count": occurrence_map_count,
            "tracks": [
                {"path": "pooled_assignment.bam", "index_path": "pooled_assignment.bam.bai"},
            ],
            "reference_entries": [
                {
                    "target_id": entry.target_id,
                    "revision_sha256": entry.revision_sha256,
                    "fasta_path": entry.fasta_path,
                    "fasta_sha256": entry.fasta_sha256,
                }
                for entry in reference_set.entries
            ],
            "artifacts": artifacts,
        },
    )


def run_classify(
    manifest_path: Path,
    snapshot_root: Path,
    source_fastq: Path,
    valid_fastq: Path,
    preflight_path: Path,
    bam_path: Path,
    samtools_command: Sequence[str],
    combined_fasta: Path,
    out_dir: Path,
    min_mapq: int,
    min_alignment_score_margin: int,
) -> None:
    reference_set = validate_reference_set(manifest_path, snapshot_root)
    _ensure_regular_nonempty(combined_fasta, "combined intended-reference FASTA")
    expected_combined = combined_reference_bytes(reference_set)
    if combined_fasta.read_bytes() != expected_combined:
        raise PooledAssignmentError("combined intended-reference FASTA does not match the validated manifest")
    _ensure_regular_nonempty(bam_path, "sorted pooled assignment BAM")
    _ensure_regular_nonempty(bam_path.with_name(f"{bam_path.name}.bai"), "pooled assignment BAM index")
    _ensure_regular_nonempty(preflight_path, "FASTQ preflight manifest")
    preflight = load_json_object(preflight_path)
    if preflight.get("schema") != PREFLIGHT_SCHEMA:
        raise PooledAssignmentError("FASTQ preflight manifest schema is invalid")
    if preflight.get("reference_set_manifest_id") != reference_set.manifest_id or preflight.get("reference_set_manifest_sha256") != reference_set.manifest_sha256:
        raise PooledAssignmentError("FASTQ preflight manifest is bound to a different reference set")
    if preflight.get("input_fastq_sha256") != sha256_file(source_fastq):
        raise PooledAssignmentError("source FASTQ changed after strict preflight")
    if preflight.get("occurrence_map_path") != OCCURRENCE_MAP_FILENAME:
        raise PooledAssignmentError("FASTQ preflight occurrence-map path is invalid")
    occurrence_map_path = preflight_path.with_name(OCCURRENCE_MAP_FILENAME)
    occurrence_map_sha256 = preflight.get("occurrence_map_sha256")
    if type(occurrence_map_sha256) is not str or SHA256_RE.fullmatch(occurrence_map_sha256) is None:
        raise PooledAssignmentError("FASTQ preflight occurrence-map digest is invalid")
    occurrence_map = _load_occurrence_map(occurrence_map_path, source_fastq, occurrence_map_sha256)
    occurrence_map_count = len(occurrence_map)
    declared_occurrence_map_count = preflight.get("occurrence_map_count")
    if (
        isinstance(declared_occurrence_map_count, bool)
        or not isinstance(declared_occurrence_map_count, int)
        or declared_occurrence_map_count != occurrence_map_count
    ):
        raise PooledAssignmentError("FASTQ preflight occurrence-map count disagrees with the occurrence map")
    records = _parse_valid_fastq(valid_fastq, occurrence_map)
    if len(records) != preflight.get("valid_fastq_reads"):
        raise PooledAssignmentError("valid FASTQ count disagrees with strict preflight evidence")
    if len(records) != occurrence_map_count:
        raise PooledAssignmentError("valid FASTQ count disagrees with the occurrence map")

    sam_lines = _samtools_view(samtools_command, bam_path)
    evidence = parse_sam_evidence(
        sam_lines,
        reference_set,
        frozenset(record.occurrence_id for record in records),
    )
    assignments = classify_assignments(
        records,
        evidence,
        reference_set,
        min_mapq=min_mapq,
        min_alignment_score_margin=min_alignment_score_margin,
    )
    if len(assignments) != len(records) or len(
        {assignment.record.occurrence_id for assignment in assignments}
    ) != len(assignments):
        raise PooledAssignmentError("assignment output does not contain exactly one row per valid FASTQ read")

    out_dir.mkdir(parents=True, exist_ok=True)
    per_read_path = out_dir / "per_read_assignment.tsv"
    _write_per_read_tsv(per_read_path, assignments)
    target_summaries = _write_bucket_files(out_dir, reference_set, assignments)
    disposition_counts = _count_dispositions(assignments)
    valid_count = len(records)
    assigned_count = sum(value for key, value in disposition_counts.items() if key.startswith("target:"))
    ambiguous_count = disposition_counts.get("ambiguous", 0)
    unclassified_count = disposition_counts.get("unclassified", 0)
    rejected_count = int(preflight.get("rejected_by_input_policy", -1))
    input_count = int(preflight.get("input_records", -1))
    if rejected_count < 0 or input_count < 0:
        raise PooledAssignmentError("FASTQ preflight arithmetic fields are missing")
    if occurrence_map_count != valid_count:
        raise PooledAssignmentError("occurrence-map count does not close to the valid FASTQ count")
    if valid_count != assigned_count + ambiguous_count + unclassified_count:
        raise PooledAssignmentError("valid FASTQ assignment count does not close arithmetically")
    if input_count != valid_count + rejected_count:
        raise PooledAssignmentError("input FASTQ preflight count does not close arithmetically")

    read_assignments = [
        {
            "occurrence_id": assignment.record.occurrence_id,
            "source_read_id": assignment.record.source_read_id,
            "source_header": assignment.record.source_header,
            "input_ordinal": assignment.record.ordinal,
            "disposition": assignment.disposition,
            "target_id": assignment.target_id,
            "reason": assignment.reason,
        }
        for assignment in assignments
    ]

    summary = {
        "schema": SUMMARY_SCHEMA,
        "workflow_id": WORKFLOW_ID,
        "mode": "pooled",
        "manifest_id": reference_set.manifest_id,
        "manifest_sha256": reference_set.manifest_sha256,
        "scientific_status": "REVIEW",
        "release_state": "awaiting_operator_release",
        "policy": {
            "fastq_input_policy": "strict",
            "min_mapq": min_mapq,
            "min_alignment_score_margin": min_alignment_score_margin,
            "minimap2_preset": "map-ont",
            "secondary_alignments": "retained",
            "identical_targets": "ambiguous_at_individual_target_level",
            "occurrence_id_policy": "synthetic_occurrence_id_from_one_based_input_ordinal",
        },
        "counts": {
            "input_fastq_records": input_count,
            "valid_fastq_reads": valid_count,
            "occurrence_map_count": occurrence_map_count,
            "rejected_by_input_policy": rejected_count,
            "target_assigned_reads": assigned_count,
            "ambiguous_reads": ambiguous_count,
            "unclassified_reads": unclassified_count,
        },
        "disposition_counts": disposition_counts,
        "accounting": {
            "valid_fastq_reads": valid_count,
            "occurrence_map_count": occurrence_map_count,
            "sum_of_dispositions": assigned_count + ambiguous_count + unclassified_count,
            "input_fastq_records": input_count,
            "valid_plus_rejected": valid_count + rejected_count,
            "occurrence_map_matches_valid_fastq_reads": occurrence_map_count == valid_count,
            "closure": True,
        },
        "occurrence_map_path": occurrence_map_path.name,
        "occurrence_map_sha256": occurrence_map_sha256,
        "occurrence_map_count": occurrence_map_count,
        "read_assignments": read_assignments,
        "targets": target_summaries,
        "artifacts": {
            "per_read_assignment": per_read_path.name,
            "fastq_preflight": preflight_path.name,
            "occurrence_map": occurrence_map_path.name,
            "combined_reference": combined_fasta.name,
            "combined_reference_index": combined_fasta.name + ".fai",
            "alignment_bam": bam_path.name,
            "alignment_bai": bam_path.with_name(f"{bam_path.name}.bai").name,
            "alignment_log": "pooled_reference_assignment.minimap2.log",
            "ambiguous_read_ids": "ambiguous.read_ids.txt",
            "ambiguous_fastq": "ambiguous.fastq",
            "unclassified_read_ids": "unclassified.read_ids.txt",
            "unclassified_fastq": "unclassified.fastq",
            "igv_session": "intended_pool.igv_session.json",
        },
    }
    _write_json(out_dir / "assignment_summary.json", summary)

    generated_paths: list[tuple[str, str]] = [
        ("assignment_summary", "assignment_summary.json"),
        ("per_read_assignment", "per_read_assignment.tsv"),
        ("fastq_preflight", "fastq_preflight.json"),
        ("combined_reference", combined_fasta.name),
        ("combined_reference_index", combined_fasta.name + ".fai"),
        ("alignment_bam", bam_path.name),
        ("alignment_bai", bam_path.name + ".bai"),
        ("alignment_log", "pooled_reference_assignment.minimap2.log"),
        ("ambiguous_read_ids", "ambiguous.read_ids.txt"),
        ("ambiguous_fastq", "ambiguous.fastq"),
        ("unclassified_read_ids", "unclassified.read_ids.txt"),
        ("unclassified_fastq", "unclassified.fastq"),
    ]
    for target in target_summaries:
        generated_paths.extend(
            [
                (f"{target['target_id']}_read_ids", target["read_ids_path"]),
                (f"{target['target_id']}_fastq", target["fastq_path"]),
            ]
        )
    _write_igv_session(
        out_dir,
        reference_set,
        source_fastq,
        occurrence_map_path,
        occurrence_map_count,
        generated_paths,
        _sha256_bytes(expected_combined),
    )


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="validate the reference set and strict FASTQ policy")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--snapshot-root", type=Path, required=True)
    preflight.add_argument("--fastq", type=Path, required=True)
    preflight.add_argument("--out-dir", type=Path, required=True)

    classify = subparsers.add_parser("classify", help="classify reads from sorted minimap2 BAM evidence")
    classify.add_argument("--manifest", type=Path, required=True)
    classify.add_argument("--snapshot-root", type=Path, required=True)
    classify.add_argument("--source-fastq", type=Path, required=True)
    classify.add_argument("--fastq", type=Path, required=True, help="workflow-produced valid FASTQ")
    classify.add_argument("--preflight", type=Path, required=True)
    classify.add_argument("--bam", type=Path, required=True)
    classify.add_argument("--samtools", default="samtools")
    classify.add_argument("--combined-fasta", type=Path, required=True)
    classify.add_argument("--out-dir", type=Path, required=True)
    classify.add_argument("--min-mapq", type=int, required=True)
    classify.add_argument("--min-alignment-score-margin", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _command_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            run_preflight(args.manifest, args.snapshot_root, args.fastq, args.out_dir)
        elif args.command == "classify":
            samtools_command = shlex.split(args.samtools)
            if not samtools_command:
                raise PooledAssignmentError("samtools command is required; no fallback is available")
            run_classify(
                args.manifest,
                args.snapshot_root,
                args.source_fastq,
                args.fastq,
                args.preflight,
                args.bam,
                samtools_command,
                args.combined_fasta,
                args.out_dir,
                args.min_mapq,
                args.min_alignment_score_margin,
            )
        else:  # pragma: no cover - argparse enforces the subcommand
            raise PooledAssignmentError("unsupported pooled assignment command")
    except (PooledAssignmentError, OSError, ValueError) as exc:
        print(f"pooled ONT reference assignment failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
