from __future__ import annotations

import base64
import codecs
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import quote

import Bio
import httpx
import rfc8785
from Bio.PDB import MMCIFIO, MMCIFParser, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from database import Design, Job, MdRun
from paths import get_allowed_roots, get_inputs_dir, to_allowed_relative

MAX_STARTING_STRUCTURE_BYTES = 100 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
MAX_STRUCTURE_LINE_CHARS = 1024 * 1024
API_ROOT = Path(__file__).resolve().parents[2]
MANAGED_FIXTURE_CATALOG = API_ROOT / "config" / "md_admitted_structures_v1.json"
SOURCE_KINDS = Literal[
    "rcsb",
    "design",
    "upload",
    "managed_fixture",
    "prior_md_input",
    "server_file",
]
PUBLIC_STRUCTURE_FORMAT = Literal["pdb", "cif"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARCHIVE_MAGICS = (b"PK\x03\x04", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"Rar!")
_MAX_SERVER_FILE_INVENTORY_ENTRIES = 5000


class StartingStructureError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


class StartingStructureSourceRef(_ClosedModel):
    kind: SOURCE_KINDS
    id: str = Field(min_length=1, max_length=256)


class StartingStructureInspectRequest(_ClosedModel):
    source_ref: StartingStructureSourceRef
    chemistry_profile_id: str | None = Field(default=None, min_length=1, max_length=96)


class StartingStructureIdentity(_ClosedModel):
    label: str
    format: PUBLIC_STRUCTURE_FORMAT
    size_bytes: int = Field(ge=1, le=MAX_STARTING_STRUCTURE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdb_id: str | None
    producer_job_id: str | None
    design_id: str | None


class StartingStructureViewer(_ClosedModel):
    url: str
    format: PUBLIC_STRUCTURE_FORMAT
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StartingStructureParser(_ClosedModel):
    name: Literal["biopython"] = "biopython"
    version: str


class StartingStructureInspectionSummary(_ClosedModel):
    model_count: int = Field(ge=1)
    chains: list[str]
    atom_count: int = Field(ge=1)
    hetero_components: list[str]
    parser: StartingStructureParser


class StartingStructureAdmission(_ClosedModel):
    state: Literal["profile_required", "admitted", "blocked"]
    profile_id: str | None
    code: str | None
    message: str


class StartingStructureInspection(_ClosedModel):
    schema_version: Literal["bms.md.starting-structure-inspection.v1"]
    source_ref: StartingStructureSourceRef
    identity: StartingStructureIdentity
    viewer: StartingStructureViewer
    inspection: StartingStructureInspectionSummary
    admission: StartingStructureAdmission


class StartingStructureServerFile(_ClosedModel):
    id: str = Field(min_length=1, max_length=256)
    label: str
    format: PUBLIC_STRUCTURE_FORMAT
    size_bytes: int = Field(
        ge=1,
        le=MAX_STARTING_STRUCTURE_BYTES,
        alias="bytes",
        serialization_alias="bytes",
    )


class StartingStructureServerFilePage(_ClosedModel):
    items: list[StartingStructureServerFile]
    next_cursor: str | None
    count: int = Field(ge=0)


class PredictionSourceFailure(_ClosedModel):
    code: str
    message: str


class PredictionSourceJob(_ClosedModel):
    id: str
    name: str
    status: Literal[
        "queued", "preparing", "running", "awaiting_input", "completed", "failed", "cancelled"
    ]
    model_id: str
    mode: str
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failure: PredictionSourceFailure | None


class PredictionSourceMetrics(_ClosedModel):
    plddt: float | None
    ptm: float | None
    iptm: float | None
    confidence: float | None


class PredictionSourceCandidate(_ClosedModel):
    source_ref: StartingStructureSourceRef
    name: str
    format: PUBLIC_STRUCTURE_FORMAT
    eligible: bool
    blocker_code: str | None
    metrics: PredictionSourceMetrics
    created_at: datetime | None


class PredictionSourceCandidatePage(_ClosedModel):
    schema_version: Literal["bms.md.prediction-source-candidates.v1"]
    job: PredictionSourceJob
    candidates: list[PredictionSourceCandidate]
    next_cursor: str | None


class MdRequestedSettings(_ClosedModel):
    replicas: int = Field(ge=1, le=8)
    random_seed: int = Field(ge=1, le=2147483647)
    padding_nm: float = Field(gt=0)
    salt_molar: float = Field(ge=0)
    neutralize: bool
    temperature_k: float = Field(gt=0)
    pressure_bar: float = Field(gt=0)
    timestep_fs: float = Field(gt=0, le=4)
    minimization_steps: int = Field(ge=1, le=5_000_000)
    nvt_ps: float = Field(gt=0)
    npt_ps: float = Field(gt=0)
    production_ns: float = Field(gt=0)
    trajectory_interval_ps: float = Field(gt=0)
    energy_interval_ps: float = Field(gt=0)
    checkpoint_interval_minutes: float = Field(ge=1.0, le=1440)
    ntomp: int = Field(ge=1, le=128)

    @model_validator(mode="after")
    def intervals_fit_production(self) -> "MdRequestedSettings":
        production_ps = self.production_ns * 1000.0
        if self.trajectory_interval_ps > production_ps:
            raise ValueError("trajectory_interval_ps cannot exceed production duration")
        if self.energy_interval_ps > production_ps:
            raise ValueError("energy_interval_ps cannot exceed production duration")
        return self


class MdLaunchIntent(_ClosedModel):
    schema_version: Literal["bms.md.launch-intent.v1"]
    name: str = Field(min_length=1, max_length=255)
    source_ref: StartingStructureSourceRef
    expected_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chemistry_profile_id: str = Field(min_length=1, max_length=96)
    chemistry_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_settings: MdRequestedSettings
    launch_context_id: str | None = Field(default=None, min_length=1, max_length=128)


class MdLaunchPreviewRequest(_ClosedModel):
    schema_version: Literal["bms.md.launch-preview-request.v1"]
    intent: MdLaunchIntent


class MdLaunchRequest(_ClosedModel):
    schema_version: Literal["bms.md.launch-request.v1"]
    intent: MdLaunchIntent
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MdPublicPreparation(_ClosedModel):
    box_type: Literal["dodecahedron"] = "dodecahedron"
    padding_nm: float
    salt_molar: float
    neutralize: bool


class MdPublicMinimization(_ClosedModel):
    enabled: Literal[True] = True
    steps: int
    force_tolerance_kj_mol_nm: float = 1000.0


class MdPublicNvt(_ClosedModel):
    enabled: Literal[True] = True
    steps: int
    temperature_k: float


class MdPublicNpt(_ClosedModel):
    enabled: Literal[True] = True
    steps: int
    temperature_k: float
    pressure_bar: float


class MdPublicProduction(_ClosedModel):
    enabled: Literal[True] = True
    steps: int
    timestep_fs: float
    temperature_k: float
    pressure_bar: float
    checkpoint_interval_minutes: float = Field(ge=1.0, le=1440)
    trajectory_interval_steps: int
    energy_interval_steps: int


class MdPublicStages(_ClosedModel):
    minimization: MdPublicMinimization
    nvt: MdPublicNvt
    npt: MdPublicNpt
    production: MdPublicProduction


class MdPublicExecution(_ClosedModel):
    ntmpi: Literal[1] = 1
    ntomp: int
    gpu_offload: Literal["full"] = "full"
    pin: Literal["on"] = "on"
    placement_authority: Literal["global_scheduler"] = "global_scheduler"


class MdPublicEffectiveRequest(_ClosedModel):
    engine: Literal["gromacs"] = "gromacs"
    replicas: int
    random_seed: int
    preparation: MdPublicPreparation
    stages: MdPublicStages
    execution: MdPublicExecution


class MdLaunchSourceIdentity(_ClosedModel):
    source_ref: StartingStructureSourceRef
    label: str
    format: PUBLIC_STRUCTURE_FORMAT
    size_bytes: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdb_id: str | None
    producer_job_id: str | None
    design_id: str | None


class MdLaunchChemistryIdentity(_ClosedModel):
    profile_id: str
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    admitted: bool


class MdLaunchNotice(_ClosedModel):
    code: str
    message: str


class MdLaunchPreview(_ClosedModel):
    schema_version: Literal["bms.md.launch-preview.v1"]
    source: MdLaunchSourceIdentity
    chemistry: MdLaunchChemistryIdentity
    requested_settings: MdRequestedSettings
    effective_request: MdPublicEffectiveRequest
    warnings: list[MdLaunchNotice]
    blockers: list[MdLaunchNotice]
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StructureFile:
    data: bytes
    format: PUBLIC_STRUCTURE_FORMAT
    media_type: str
    canonical_suffix: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _ServerFileInventoryEntry:
    handle: str
    label: str
    format: PUBLIC_STRUCTURE_FORMAT
    size_bytes: int


@dataclass(frozen=True)
class _StructureMetadata:
    format: PUBLIC_STRUCTURE_FORMAT
    size_bytes: int


@dataclass
class VerifiedStructureSnapshot:
    handle: Any
    format: PUBLIC_STRUCTURE_FORMAT
    media_type: str
    canonical_suffix: str
    sha256: str
    size_bytes: int

    def iter_chunks(self):
        try:
            self.handle.seek(0)
            while True:
                chunk = self.handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            self.handle.close()


@dataclass
class ResolvedStartingStructure:
    source_ref: StartingStructureSourceRef
    path: Path | None
    label: str
    pdb_id: str | None = None
    producer_job_id: str | None = None
    design_id: str | None = None
    descriptor: int | None = None
    suffix: str | None = None

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def _error(code: str, message: str, status_code: int = 422) -> StartingStructureError:
    return StartingStructureError(code, message, status_code=status_code)


def _detect_structure_format(data: bytes) -> PUBLIC_STRUCTURE_FORMAT:
    if not data:
        raise _error("MD_STARTING_STRUCTURE_EMPTY", "The starting structure is empty.")
    if data.startswith(_ARCHIVE_MAGICS):
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "Archives and compressed starting structures are not supported.",
        )
    if b"\x00" in data:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure is not supported safe text content.",
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure is not valid supported text content.",
        ) from exc
    lines = text.splitlines()
    pdb_content = any(line.startswith(("ATOM  ", "HETATM")) for line in lines)
    non_comment = next((line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")), "")
    cif_content = non_comment.lower().startswith("data_") and any(
        line.lstrip().lower().startswith("_atom_site.") for line in lines
    )
    if pdb_content == cif_content:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting-structure format is empty, ambiguous, or unsupported.",
        )
    return "pdb" if pdb_content else "cif"


def _suffix_structure_format(suffix: str) -> PUBLIC_STRUCTURE_FORMAT | None:
    normalized = suffix.lower()
    return (
        "pdb"
        if normalized == ".pdb"
        else "cif"
        if normalized in {".cif", ".mmcif"}
        else None
    )


def _read_structure_metadata_descriptor(
    descriptor: int, *, suffix: str
) -> _StructureMetadata:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "The starting structure is not a regular file.",
            409,
        )
    if info.st_size > MAX_STARTING_STRUCTURE_BYTES:
        raise _error(
            "MD_STARTING_STRUCTURE_TOO_LARGE",
            "The starting structure exceeds 100 MiB.",
            413,
        )
    suffix_format = _suffix_structure_format(suffix)
    if suffix_format is None:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure must use .pdb, .cif, or .mmcif.",
        )
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending_text = ""
    first_non_comment = ""
    pdb_content = False
    cif_atom_content = False
    leading_bytes = b""
    consumed = 0
    offset = 0

    def observe_line(line: str) -> None:
        nonlocal first_non_comment, pdb_content, cif_atom_content
        logical = line.rstrip("\r\n")
        if logical.startswith(("ATOM  ", "HETATM")):
            pdb_content = True
        stripped = logical.strip()
        if not first_non_comment and stripped and not logical.lstrip().startswith("#"):
            first_non_comment = stripped
        if logical.lstrip().lower().startswith("_atom_site."):
            cif_atom_content = True

    while True:
        chunk = os.pread(descriptor, HASH_CHUNK_BYTES, offset)
        if not chunk:
            break
        offset += len(chunk)
        consumed += len(chunk)
        if consumed > MAX_STARTING_STRUCTURE_BYTES:
            raise _error(
                "MD_STARTING_STRUCTURE_TOO_LARGE",
                "The starting structure exceeds 100 MiB.",
                413,
            )
        if len(leading_bytes) < 8:
            leading_bytes = (leading_bytes + chunk)[:8]
        if b"\x00" in chunk:
            raise _error(
                "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                "The starting structure is not supported safe text content.",
            )
        try:
            decoded = decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise _error(
                "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                "The starting structure is not valid supported text content.",
            ) from exc
        pending_text += decoded
        lines = pending_text.splitlines(keepends=True)
        pending_text = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            pending_text = lines.pop()
            if len(pending_text) > MAX_STRUCTURE_LINE_CHARS:
                raise _error(
                    "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                    "The starting structure contains an overlong text record.",
                )
        for line in lines:
            observe_line(line)
    try:
        pending_text += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure is not valid supported text content.",
        ) from exc
    if pending_text:
        observe_line(pending_text)
    if consumed != info.st_size:
        raise _error(
            "MD_STARTING_STRUCTURE_CHANGED",
            "The starting-structure bytes changed during verification.",
            409,
        )
    if leading_bytes.startswith(_ARCHIVE_MAGICS):
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "Archives and compressed starting structures are not supported.",
        )
    cif_content = first_non_comment.lower().startswith("data_") and cif_atom_content
    if pdb_content == cif_content:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting-structure format is empty, ambiguous, or unsupported.",
        )
    detected: PUBLIC_STRUCTURE_FORMAT = "pdb" if pdb_content else "cif"
    if suffix_format != detected:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_MISMATCH",
            "The starting-structure suffix does not match its actual content.",
        )
    return _StructureMetadata(format=detected, size_bytes=consumed)


def _read_structure_descriptor(descriptor: int, *, suffix: str) -> StructureFile:
    digest = hashlib.sha256()
    consumed = 0
    chunks: list[bytes] = []
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "The starting structure is not a regular file.",
            409,
        )
    if info.st_size > MAX_STARTING_STRUCTURE_BYTES:
        raise _error(
            "MD_STARTING_STRUCTURE_TOO_LARGE",
            "The starting structure exceeds 100 MiB.",
            413,
        )
    offset = 0
    while True:
        chunk = os.pread(descriptor, HASH_CHUNK_BYTES, offset)
        if not chunk:
            break
        offset += len(chunk)
        consumed += len(chunk)
        if consumed > MAX_STARTING_STRUCTURE_BYTES:
            raise _error(
                "MD_STARTING_STRUCTURE_TOO_LARGE",
                "The starting structure exceeds 100 MiB.",
                413,
            )
        digest.update(chunk)
        chunks.append(chunk)
    if consumed != info.st_size:
        raise _error(
            "MD_STARTING_STRUCTURE_CHANGED",
            "The starting-structure bytes changed during verification.",
            409,
        )
    data = b"".join(chunks)
    detected = _detect_structure_format(data)
    suffix_format = _suffix_structure_format(suffix)
    if suffix_format is None:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure must use .pdb, .cif, or .mmcif.",
        )
    if suffix_format != detected:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_MISMATCH",
            "The starting-structure suffix does not match its actual content.",
        )
    return StructureFile(
        data=data,
        format=detected,
        media_type="chemical/x-pdb" if detected == "pdb" else "chemical/x-mmcif",
        canonical_suffix=".pdb" if detected == "pdb" else ".cif",
        sha256=digest.hexdigest(),
        size_bytes=consumed,
    )


def read_structure_file(path: Path) -> StructureFile:
    lexical = Path(path)
    if lexical.is_symlink():
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "Symlinked starting structures are not supported.",
            409,
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The starting structure is unavailable.",
            404,
        ) from exc
    except OSError as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "The starting structure cannot be opened safely.",
            409,
        ) from exc
    try:
        return _read_structure_descriptor(descriptor, suffix=lexical.suffix)
    finally:
        os.close(descriptor)


def open_verified_structure_snapshot(
    path: Path, *, expected_sha256: str
) -> VerifiedStructureSnapshot:
    lexical = Path(path)
    if lexical.is_symlink():
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "Symlinked starting structures are not supported.",
            409,
        )
    suffix = lexical.suffix.lower()
    suffix_format: PUBLIC_STRUCTURE_FORMAT | None = (
        "pdb" if suffix == ".pdb" else "cif" if suffix in {".cif", ".mmcif"} else None
    )
    if suffix_format is None:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "The starting structure must use .pdb, .cif, or .mmcif.",
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The starting structure is unavailable.",
            404,
        ) from exc
    except OSError as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_UNSAFE",
            "The starting structure cannot be opened safely.",
            409,
        ) from exc
    snapshot = tempfile.TemporaryFile(mode="w+b")
    digest = hashlib.sha256()
    consumed = 0
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending_text = ""
    pdb_content = False
    cif_atom_content = False
    first_non_comment = ""
    leading_bytes = b""

    def observe_line(line: str) -> None:
        nonlocal pdb_content, cif_atom_content, first_non_comment
        logical = line.rstrip("\r\n")
        if logical.startswith(("ATOM  ", "HETATM")):
            pdb_content = True
        stripped = logical.strip()
        if not first_non_comment and stripped and not logical.lstrip().startswith("#"):
            first_non_comment = stripped
        if logical.lstrip().lower().startswith("_atom_site."):
            cif_atom_content = True

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise _error(
                "MD_STARTING_STRUCTURE_UNSAFE",
                "The starting structure is not a regular file.",
                409,
            )
        if info.st_size > MAX_STARTING_STRUCTURE_BYTES:
            raise _error(
                "MD_STARTING_STRUCTURE_TOO_LARGE",
                "The starting structure exceeds 100 MiB.",
                413,
            )
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            while True:
                chunk = source.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > MAX_STARTING_STRUCTURE_BYTES:
                    raise _error(
                        "MD_STARTING_STRUCTURE_TOO_LARGE",
                        "The starting structure exceeds 100 MiB.",
                        413,
                    )
                if len(leading_bytes) < 8:
                    leading_bytes = (leading_bytes + chunk)[:8]
                if b"\x00" in chunk:
                    raise _error(
                        "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                        "The starting structure is not supported safe text content.",
                    )
                try:
                    decoded = decoder.decode(chunk)
                except UnicodeDecodeError as exc:
                    raise _error(
                        "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                        "The starting structure is not valid supported text content.",
                    ) from exc
                pending_text += decoded
                lines = pending_text.splitlines(keepends=True)
                pending_text = ""
                if lines and not lines[-1].endswith(("\n", "\r")):
                    pending_text = lines.pop()
                    if len(pending_text) > MAX_STRUCTURE_LINE_CHARS:
                        raise _error(
                            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                            "The starting structure contains an overlong text record.",
                        )
                for line in lines:
                    observe_line(line)
                digest.update(chunk)
                snapshot.write(chunk)
            try:
                pending_text += decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise _error(
                    "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                    "The starting structure is not valid supported text content.",
                ) from exc
            if pending_text:
                observe_line(pending_text)
        if consumed != info.st_size:
            raise _error(
                "MD_STARTING_STRUCTURE_CHANGED",
                "The starting-structure bytes changed during verification.",
                409,
            )
        if leading_bytes.startswith(_ARCHIVE_MAGICS):
            raise _error(
                "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                "Archives and compressed starting structures are not supported.",
            )
        cif_content = first_non_comment.lower().startswith("data_") and cif_atom_content
        if pdb_content == cif_content:
            raise _error(
                "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
                "The starting-structure format is empty, ambiguous, or unsupported.",
            )
        detected: PUBLIC_STRUCTURE_FORMAT = "pdb" if pdb_content else "cif"
        if suffix_format != detected:
            raise _error(
                "MD_STARTING_STRUCTURE_FORMAT_MISMATCH",
                "The starting-structure suffix does not match its actual content.",
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise _error(
                "MD_STARTING_STRUCTURE_CHANGED",
                "The starting-structure bytes changed after inspection.",
                409,
            )
        snapshot.flush()
        snapshot.seek(0)
        return VerifiedStructureSnapshot(
            handle=snapshot,
            format=detected,
            media_type="chemical/x-pdb" if detected == "pdb" else "chemical/x-mmcif",
            canonical_suffix=".pdb" if detected == "pdb" else ".cif",
            sha256=actual_sha256,
            size_bytes=consumed,
        )
    except Exception:
        snapshot.close()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_resolved_structure(resolved: ResolvedStartingStructure) -> StructureFile:
    if resolved.descriptor is not None:
        return _read_structure_descriptor(
            resolved.descriptor,
            suffix=str(resolved.suffix or Path(resolved.label).suffix),
        )
    if resolved.path is None:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The starting structure is unavailable.",
            404,
        )
    return read_structure_file(resolved.path)


def open_resolved_structure_snapshot(
    resolved: ResolvedStartingStructure, *, expected_sha256: str
) -> VerifiedStructureSnapshot:
    if resolved.descriptor is None:
        if resolved.path is None:
            raise _error(
                "MD_STARTING_STRUCTURE_NOT_FOUND",
                "The starting structure is unavailable.",
                404,
            )
        return open_verified_structure_snapshot(
            resolved.path, expected_sha256=expected_sha256
        )
    structure = read_resolved_structure(resolved)
    if structure.sha256 != expected_sha256:
        raise _error(
            "MD_STARTING_STRUCTURE_CHANGED",
            "The starting-structure bytes changed after inspection.",
            409,
        )
    snapshot = tempfile.TemporaryFile(mode="w+b")
    try:
        snapshot.write(structure.data)
        snapshot.flush()
        snapshot.seek(0)
        return VerifiedStructureSnapshot(
            handle=snapshot,
            format=structure.format,
            media_type=structure.media_type,
            canonical_suffix=structure.canonical_suffix,
            sha256=structure.sha256,
            size_bytes=structure.size_bytes,
        )
    except Exception:
        snapshot.close()
        raise


def resolved_runtime_path(resolved: ResolvedStartingStructure) -> str:
    if resolved.descriptor is not None:
        return f"/proc/self/fd/{resolved.descriptor}"
    if resolved.path is None:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The starting structure is unavailable.",
            404,
        )
    return str(resolved.path)


def _load_managed_fixture(fixture_id: str) -> tuple[dict[str, Any], Path]:
    try:
        if MANAGED_FIXTURE_CATALOG.is_symlink():
            raise OSError("catalog is symlinked")
        document = json.loads(MANAGED_FIXTURE_CATALOG.read_text(encoding="utf-8"))
        records = document.get("fixtures") if isinstance(document, Mapping) else None
        record = next(
            (item for item in records if isinstance(item, Mapping) and item.get("id") == fixture_id),
            None,
        ) if isinstance(records, list) else None
        if not isinstance(record, Mapping):
            raise KeyError(fixture_id)
        relative = Path(str(record["product_relative_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise OSError("unsafe fixture path")
        path = API_ROOT / relative
        path.resolve(strict=False).relative_to(API_ROOT.resolve())
        structure = read_structure_file(path)
        if (
            structure.sha256 != record.get("sha256")
            or structure.size_bytes != record.get("size_bytes")
            or structure.format != record.get("format")
        ):
            raise OSError("fixture identity drift")
        return dict(record), path
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, StopIteration, ValueError) as exc:
        raise _error(
            "MD_MANAGED_FIXTURE_UNAVAILABLE",
            "The requested managed starting-structure fixture is unavailable.",
            404,
        ) from exc


def resolve_product_source(source_ref: StartingStructureSourceRef) -> ResolvedStartingStructure:
    if source_ref.kind != "managed_fixture":
        raise _error(
            "MD_STARTING_STRUCTURE_SOURCE_UNSUPPORTED",
            "This source kind requires server resolution.",
            404,
        )
    record, path = _load_managed_fixture(source_ref.id)
    return ResolvedStartingStructure(
        source_ref=source_ref,
        path=path,
        label=str(record["label"]),
        pdb_id=str(record.get("accession") or "") or None,
    )


def _safe_label(value: str, *, fallback: str = "starting-structure") -> str:
    label = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(value or "")).strip(" .-_")
    return label[:180] or fallback


def inline_filename(label: str, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")[:180] or "starting-structure"
    return f"{stem}{suffix}"


def _require_uuid(value: str, *, code: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise _error(code, "The requested starting-structure source identifier is invalid.", 404) from exc
    if str(parsed) != value.lower():
        raise _error(code, "The requested starting-structure source identifier is invalid.", 404)
    return str(parsed)


def _require_allowed_source_path(path: Path) -> Path:
    lexical = Path(path).expanduser()
    if lexical.is_symlink():
        raise _error("MD_STARTING_STRUCTURE_UNSAFE", "The starting structure is symlinked.", 409)
    try:
        resolved = lexical.resolve(strict=True)
        if not any(
            resolved.is_relative_to(root.expanduser().resolve(strict=False))
            for root in get_allowed_roots().values()
        ):
            raise ValueError("outside roots")
    except (OSError, ValueError) as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_FORBIDDEN",
            "The starting structure is outside the current allowed-root policy.",
            404,
        ) from exc
    return resolved


async def fetch_rcsb_entry(accession: str) -> Path:
    normalized = accession.strip().upper()
    if re.fullmatch(r"[A-Z0-9]{4}", normalized) is None:
        raise _error("MD_RCSB_ACCESSION_INVALID", "RCSB accession must be four letters or digits.")
    from routers import rcsb

    cache_path = rcsb.RCSB_CACHE_DIR / f"{normalized.lower()}.pdb"
    if cache_path.is_file() and not cache_path.is_symlink():
        read_structure_file(cache_path)
        rcsb._touch_last_used(cache_path)
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{normalized.lower()}.", suffix=".pdb", dir=cache_path.parent
    )
    temporary = Path(temporary_name)
    try:
        size = 0
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream(
                "GET", f"{rcsb.RCSB_BASE_URL}/{normalized}.pdb"
            ) as response:
                if response.status_code == 404:
                    raise _error("MD_RCSB_NOT_FOUND", "The requested RCSB entry was not found.", 404)
                if response.status_code != 200:
                    raise _error(
                        "MD_RCSB_UNAVAILABLE", "RCSB retrieval is temporarily unavailable.", 502
                    )
                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_STARTING_STRUCTURE_BYTES:
                    raise _error(
                        "MD_STARTING_STRUCTURE_TOO_LARGE", "The RCSB entry exceeds 100 MiB.", 413
                    )
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    async for chunk in response.aiter_bytes(HASH_CHUNK_BYTES):
                        size += len(chunk)
                        if size > MAX_STARTING_STRUCTURE_BYTES:
                            raise _error(
                                "MD_STARTING_STRUCTURE_TOO_LARGE",
                                "The RCSB entry exceeds 100 MiB.",
                                413,
                            )
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
        read_structure_file(temporary)
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, cache_path, follow_symlinks=False)
        except FileExistsError:
            pass
        now_iso = rcsb._isoformat_timestamp(time.time())
        rcsb._write_cache_metadata(cache_path, cached_at=now_iso, last_used_at=now_iso)
        return cache_path
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, StartingStructureError):
            raise
        raise _error(
            "MD_RCSB_UNAVAILABLE", "RCSB retrieval is temporarily unavailable.", 502
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _upload_root() -> Path:
    return get_inputs_dir() / "md_starting_structures" / "uploads"


def publish_upload(*, filename: str, file_object: Any) -> ResolvedStartingStructure:
    plain_name = Path(str(filename or ""))
    if (
        not plain_name.name
        or plain_name.name != str(filename)
        or plain_name.name in {".", ".."}
    ):
        raise _error(
            "MD_STARTING_STRUCTURE_UPLOAD_INVALID",
            "Upload filename must be one plain basename.",
        )
    suffix = plain_name.suffix.lower()
    if suffix not in {".pdb", ".cif", ".mmcif"}:
        raise _error(
            "MD_STARTING_STRUCTURE_FORMAT_UNSUPPORTED",
            "Upload must use .pdb, .cif, or .mmcif.",
        )
    root = _upload_root()
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".upload.", suffix=suffix, dir=root)
    temporary = Path(temporary_name)
    try:
        size = 0
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            while True:
                chunk = file_object.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_STARTING_STRUCTURE_BYTES:
                    raise _error(
                        "MD_STARTING_STRUCTURE_TOO_LARGE", "The upload exceeds 100 MiB.", 413
                    )
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        structure = read_structure_file(temporary)
        source_id = str(uuid.uuid4())
        label = _safe_label(plain_name.stem)
        destination = root / f"{source_id}--{label}{structure.canonical_suffix}"
        os.chmod(temporary, 0o444)
        os.link(temporary, destination, follow_symlinks=False)
        directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return ResolvedStartingStructure(
            source_ref=StartingStructureSourceRef(kind="upload", id=source_id),
            path=destination,
            label=label,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _resolve_upload(source_ref: StartingStructureSourceRef) -> ResolvedStartingStructure:
    source_id = _require_uuid(source_ref.id, code="MD_STARTING_STRUCTURE_NOT_FOUND")
    root = _upload_root()
    candidates = list(root.glob(f"{source_id}--*")) if root.is_dir() and not root.is_symlink() else []
    if len(candidates) != 1:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND", "The immutable upload is unavailable.", 404
        )
    path = candidates[0]
    marker = path.name.find("--")
    label = path.name[marker + 2 : -len(path.suffix)]
    return ResolvedStartingStructure(source_ref=source_ref, path=path, label=label)


def _encode_server_file(root_name: str, relative_parts: tuple[str, ...]) -> str:
    relative = "/".join(relative_parts)
    digest = hashlib.sha256(
        b"bms.md.server-file-handle.v1\0"
        + root_name.encode("utf-8")
        + b"\0"
        + relative.encode("utf-8")
    ).hexdigest()
    return f"sf1_{digest}"


def server_file_browser_enabled() -> bool:
    return os.getenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_server_file_capability() -> None:
    if not server_file_browser_enabled():
        raise _error(
            "MD_SERVER_FILE_BROWSER_DISABLED",
            "Starting-structure server browsing is disabled.",
            404,
        )


def _open_pinned_directory(path: Path) -> int:
    absolute = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise NotADirectoryError(os.fspath(path))
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _scan_server_files(
    *,
    search: str = "",
    target_handle: str | None = None,
) -> tuple[list[_ServerFileInventoryEntry], ResolvedStartingStructure | None]:
    query = search.strip().casefold()
    candidates: list[_ServerFileInventoryEntry] = []
    examined = 0
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

    def visit(
        root_name: str,
        directory_descriptor: int,
        relative_directory: tuple[str, ...],
    ) -> ResolvedStartingStructure | None:
        nonlocal examined
        try:
            names = sorted(os.listdir(directory_descriptor), key=lambda value: value.casefold())
        except OSError:
            return None
        for name in names:
            examined += 1
            if examined > _MAX_SERVER_FILE_INVENTORY_ENTRIES:
                return None
            try:
                info = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                continue
            relative_parts = (*relative_directory, name)
            if stat.S_ISDIR(info.st_mode):
                try:
                    child_descriptor = os.open(
                        name,
                        directory_flags,
                        dir_fd=directory_descriptor,
                    )
                except OSError:
                    continue
                try:
                    resolved = visit(root_name, child_descriptor, relative_parts)
                finally:
                    os.close(child_descriptor)
                if resolved is not None:
                    return resolved
                if examined >= _MAX_SERVER_FILE_INVENTORY_ENTRIES:
                    return None
                continue
            suffix = Path(name).suffix.lower()
            if (
                not stat.S_ISREG(info.st_mode)
                or suffix not in {".pdb", ".cif", ".mmcif"}
                or (query and query not in name.casefold())
            ):
                continue
            handle = _encode_server_file(root_name, relative_parts)
            if target_handle is not None and handle != target_handle:
                continue
            leaf_descriptor = -1
            try:
                leaf_descriptor = os.open(
                    name,
                    file_flags,
                    dir_fd=directory_descriptor,
                )
                opened = os.fstat(leaf_descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    continue
                metadata = _read_structure_metadata_descriptor(
                    leaf_descriptor, suffix=suffix
                )
                if target_handle is not None:
                    resolved = ResolvedStartingStructure(
                        source_ref=StartingStructureSourceRef(
                            kind="server_file", id=target_handle
                        ),
                        path=None,
                        label=name,
                        descriptor=leaf_descriptor,
                        suffix=suffix,
                    )
                    leaf_descriptor = -1
                    return resolved
                candidates.append(
                    _ServerFileInventoryEntry(
                        handle=handle,
                        label=name,
                        format=metadata.format,
                        size_bytes=metadata.size_bytes,
                    )
                )
            except StartingStructureError:
                if target_handle is not None:
                    raise
            except OSError:
                continue
            finally:
                if leaf_descriptor >= 0:
                    os.close(leaf_descriptor)
        return None

    for root_name, root in sorted(get_allowed_roots().items()):
        if examined >= _MAX_SERVER_FILE_INVENTORY_ENTRIES:
            break
        try:
            root_descriptor = _open_pinned_directory(root)
        except OSError:
            continue
        try:
            resolved = visit(str(root_name), root_descriptor, ())
        finally:
            os.close(root_descriptor)
        if resolved is not None:
            return candidates, resolved
    candidates.sort(key=lambda item: (item.label.casefold(), item.handle))
    return candidates, None


def _resolve_server_file(handle: str) -> ResolvedStartingStructure:
    _require_server_file_capability()
    if re.fullmatch(r"sf1_[0-9a-f]{64}", handle) is None:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The opaque server-file handle is unavailable.",
            404,
        )
    _candidates, resolved = _scan_server_files(target_handle=handle)
    if resolved is None:
        raise _error(
            "MD_STARTING_STRUCTURE_NOT_FOUND",
            "The opaque server-file handle is unavailable.",
            404,
        )
    return resolved


def _server_file_inventory(
    *, search: str = ""
) -> list[_ServerFileInventoryEntry]:
    candidates, _resolved = _scan_server_files(search=search)
    return candidates


def list_server_files(
    *, search: str, cursor: str | None, limit: int
) -> StartingStructureServerFilePage:
    _require_server_file_capability()
    candidates = _server_file_inventory(search=search)
    offset = 0
    if cursor:
        try:
            offset = int(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
            )
        except (ValueError, UnicodeError) as exc:
            raise _error(
                "MD_SERVER_FILE_CURSOR_INVALID", "The server-file cursor is invalid."
            ) from exc
        if offset < 0 or offset > len(candidates):
            raise _error(
                "MD_SERVER_FILE_CURSOR_INVALID", "The server-file cursor is invalid."
            )
    page = candidates[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = (
        base64.urlsafe_b64encode(str(next_offset).encode("ascii"))
        .decode("ascii")
        .rstrip("=")
        if next_offset < len(candidates)
        else None
    )
    items = [
        StartingStructureServerFile(
            id=entry.handle,
            label=entry.label,
            format=entry.format,
            bytes=entry.size_bytes,
        )
        for entry in page
    ]
    return StartingStructureServerFilePage(
        items=items, next_cursor=next_cursor, count=len(items)
    )


def _declared_pdb_accession(structure: StructureFile) -> str | None:
    if structure.format != "pdb":
        return None
    for line in structure.data.decode("utf-8", errors="replace").splitlines():
        if line.startswith("HEADER") and len(line) >= 66:
            accession = line[62:66].strip().upper()
            if re.fullmatch(r"[A-Z0-9]{4}", accession):
                return accession
    return None


async def resolve_source(
    source_ref: StartingStructureSourceRef, session: Any
) -> ResolvedStartingStructure:
    if source_ref.kind == "managed_fixture":
        return resolve_product_source(source_ref)
    if source_ref.kind == "upload":
        return _resolve_upload(source_ref)
    if source_ref.kind == "rcsb":
        accession = source_ref.id.strip().upper()
        if re.fullmatch(r"[A-Z0-9]{4}", accession) is None:
            raise _error(
                "MD_RCSB_ACCESSION_INVALID", "RCSB accession must be four letters or digits."
            )
        normalized_ref = StartingStructureSourceRef(kind="rcsb", id=accession)
        path = await fetch_rcsb_entry(accession)
        structure = read_structure_file(path)
        if _declared_pdb_accession(structure) != accession:
            raise _error(
                "MD_STARTING_STRUCTURE_SOURCE_ID_MISMATCH",
                "The retrieved structure bytes do not declare the requested RCSB accession.",
                409,
            )
        return ResolvedStartingStructure(
            source_ref=normalized_ref,
            path=path,
            label=f"RCSB {accession}",
            pdb_id=accession,
        )
    if source_ref.kind == "server_file":
        return _resolve_server_file(source_ref.id)
    if source_ref.kind == "design":
        design_id = _require_uuid(source_ref.id, code="MD_STARTING_STRUCTURE_NOT_FOUND")
        design = await session.get(Design, design_id)
        job = await session.get(Job, design.job_id) if design is not None else None
        if design is None or job is None or job.status != "completed":
            raise _error(
                "MD_STARTING_STRUCTURE_NOT_FOUND", "The completed Design is unavailable.", 404
            )
        path = _require_allowed_source_path(Path(str(design.pdb_path)))
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=path,
            label=str(design.name),
            producer_job_id=str(job.id),
            design_id=str(design.id),
        )
    if source_ref.kind == "prior_md_input":
        job_id = _require_uuid(source_ref.id, code="MD_STARTING_STRUCTURE_NOT_FOUND")
        job = await session.get(Job, job_id)
        run = await session.get(MdRun, job_id)
        raw_input = (run.normalized_request or {}).get("input") if run is not None else None
        structure_path = raw_input.get("structure") if isinstance(raw_input, Mapping) else None
        if (
            job is None
            or run is None
            or job.model_id != "molecular_dynamics"
            or not structure_path
        ):
            raise _error(
                "MD_STARTING_STRUCTURE_NOT_FOUND", "The prior MD input is unavailable.", 404
            )
        path = Path(str(structure_path))
        try:
            output_root = Path(str(job.output_dir)).resolve(strict=True)
            resolved = path.resolve(strict=True)
            resolved.relative_to(output_root / "inputs")
        except (OSError, ValueError) as exc:
            raise _error(
                "MD_STARTING_STRUCTURE_FORBIDDEN", "The prior MD input is not job-owned.", 404
            ) from exc
        if path.is_symlink():
            raise _error(
                "MD_STARTING_STRUCTURE_UNSAFE", "The prior MD input is symlinked.", 409
            )
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=resolved,
            label=f"Prior MD input {job.name}",
            producer_job_id=str(job.id),
        )
    raise _error(
        "MD_STARTING_STRUCTURE_SOURCE_UNSUPPORTED", "The source kind is unsupported.", 404
    )


_ACCEPTED_PREDICTION_PRODUCERS = frozenset(
    {
        ("esmfold2", "predict"),
        ("boltz2", "predict"),
        ("boltz2", "complex"),
        ("rf3", "predict"),
        ("rf3", "complex"),
        ("protenix", "predict"),
        ("protenix", "complex"),
    }
)
_PUBLIC_JOB_STATUSES = frozenset(
    {"queued", "preparing", "running", "awaiting_input", "completed", "failed", "cancelled"}
)


def _candidate_cursor(*, job_id: str, limit: int, offset: int) -> str:
    raw = json.dumps(
        {"job_id": job_id, "limit": limit, "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _candidate_offset(cursor: str | None, *, job_id: str, limit: int) -> int:
    if cursor is None:
        return 0
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        )
        if set(payload) != {"job_id", "limit", "offset"}:
            raise ValueError("cursor shape")
        if payload["job_id"] != job_id or payload["limit"] != limit:
            raise ValueError("cursor scope")
        offset = payload["offset"]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("cursor offset")
        return offset
    except (UnicodeError, ValueError, json.JSONDecodeError, TypeError) as exc:
        raise _error(
            "MD_PREDICTION_SOURCE_CURSOR_INVALID",
            "The prediction-source cursor is invalid for this request.",
        ) from exc


async def prediction_source_candidates(
    session: Any,
    *,
    job_id: str,
    cursor: str | None,
    limit: int,
) -> PredictionSourceCandidatePage:
    normalized_job_id = _require_uuid(job_id, code="MD_PREDICTION_JOB_NOT_FOUND")
    job = await session.get(Job, normalized_job_id)
    if job is None:
        raise _error("MD_PREDICTION_JOB_NOT_FOUND", "The prediction Job was not found.", 404)
    producer = (str(job.model_id), str(job.mode))
    if producer not in _ACCEPTED_PREDICTION_PRODUCERS:
        raise _error(
            "MD_PREDICTION_PRODUCER_UNSUPPORTED",
            "The Job is not an accepted Structure Prediction producer.",
            409,
        )
    status = str(job.status)
    if status not in _PUBLIC_JOB_STATUSES:
        raise _error(
            "MD_PREDICTION_JOB_STATE_UNSUPPORTED",
            "The prediction Job has no supported public status.",
            409,
        )
    offset = _candidate_offset(cursor, job_id=normalized_job_id, limit=limit)
    rows = list(
        (
            await session.scalars(
                select(Design)
                .where(Design.job_id == normalized_job_id)
                .order_by(Design.name, Design.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    page_rows = rows[:limit]
    candidates: list[PredictionSourceCandidate] = []
    for design in page_rows:
        try:
            path = _require_allowed_source_path(Path(str(design.pdb_path)))
            structure = read_structure_file(path)
        except StartingStructureError:
            continue
        eligible = status == "completed"
        candidates.append(
            PredictionSourceCandidate(
                source_ref=StartingStructureSourceRef(kind="design", id=str(design.id)),
                name=str(design.name),
                format=structure.format,
                eligible=eligible,
                blocker_code=None if eligible else "MD_PREDICTION_JOB_NOT_COMPLETED",
                metrics=PredictionSourceMetrics(
                    plddt=design.plddt_overall,
                    ptm=design.ptm,
                    iptm=design.iptm,
                    confidence=design.conf_score,
                ),
                created_at=job.completed_at or job.created_at,
            )
        )
    failure = (
        PredictionSourceFailure(
            code="STRUCTURE_PREDICTION_FAILED",
            message="Structure prediction failed.",
        )
        if status == "failed"
        else None
    )
    next_cursor = (
        _candidate_cursor(job_id=normalized_job_id, limit=limit, offset=offset + limit)
        if len(rows) > limit
        else None
    )
    return PredictionSourceCandidatePage(
        schema_version="bms.md.prediction-source-candidates.v1",
        job=PredictionSourceJob(
            id=normalized_job_id,
            name=str(job.name),
            status=status,
            model_id=str(job.model_id),
            mode=str(job.mode),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            failure=failure,
        ),
        candidates=candidates,
        next_cursor=next_cursor,
    )


def _profile_number(
    constraints: Mapping[str, Any], field: str, requested: int | float
) -> int | float:
    value = constraints.get(field, requested)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(
            "MD_CHEMISTRY_PROFILE_UNAVAILABLE",
            "The selected chemistry profile has invalid launch constraints.",
            409,
        )
    return value


def _step_count(value: float, scale: float, timestep_fs: float) -> int:
    return round(value * scale / timestep_fs)


def compile_launch_preview(
    *,
    intent: MdLaunchIntent,
    resolved: ResolvedStartingStructure,
    profile: Mapping[str, Any] | None,
    current_catalog_digest: str,
) -> MdLaunchPreview:
    structure = read_resolved_structure(resolved)
    if structure.sha256 != intent.expected_source_sha256:
        raise _error(
            "MD_STARTING_STRUCTURE_CHANGED",
            "The starting-structure bytes changed after the caller selected them.",
            409,
        )
    if current_catalog_digest != intent.catalog_digest:
        raise _error(
            "MD_CHEMISTRY_CATALOG_STALE",
            "The selected molecular-dynamics chemistry catalog generation is stale.",
            409,
        )
    if (
        not isinstance(profile, Mapping)
        or profile.get("id") != intent.chemistry_profile_id
        or profile.get("profile_sha256") != intent.chemistry_profile_sha256
    ):
        raise _error(
            "MD_CHEMISTRY_PROFILE_STALE",
            "The selected molecular-dynamics chemistry profile identity is stale.",
            409,
        )
    states = profile.get("states")
    constraints = profile.get("launch_constraints")
    validation = profile.get("scientific_validation")
    scope = validation.get("scope") if isinstance(validation, Mapping) else None
    if (
        not isinstance(states, Mapping)
        or states.get("selectable") is not True
        or not isinstance(constraints, Mapping)
        or not isinstance(scope, Mapping)
        or not isinstance(scope.get("launch_scope"), str)
        or constraints.get("engine") != "gromacs"
    ):
        raise _error(
            "MD_CHEMISTRY_PROFILE_UNAVAILABLE",
            "The selected molecular-dynamics chemistry profile is unavailable for typed launch.",
            409,
        )

    requested = intent.requested_settings
    warnings: list[MdLaunchNotice] = []
    blockers: list[MdLaunchNotice] = []
    fixed_fields = {
        "replicas": int(_profile_number(constraints, "replicas", requested.replicas)),
        "padding_nm": float(_profile_number(constraints, "padding_nm", requested.padding_nm)),
        "salt_molar": float(_profile_number(constraints, "salt_molar", requested.salt_molar)),
        "temperature_k": float(
            _profile_number(constraints, "temperature_k", requested.temperature_k)
        ),
        "pressure_bar": float(
            _profile_number(constraints, "pressure_bar", requested.pressure_bar)
        ),
        "timestep_fs": float(
            _profile_number(constraints, "timestep_fs", requested.timestep_fs)
        ),
    }
    for field, effective_value in fixed_fields.items():
        if getattr(requested, field) != effective_value:
            raise _error(
                "MD_SETTING_FIXED_BY_PROFILE",
                f"{field} must equal the value fixed by the selected chemistry profile.",
                422,
            )

    nvt_steps = _step_count(requested.nvt_ps, 1000.0, fixed_fields["timestep_fs"])
    npt_steps = _step_count(requested.npt_ps, 1000.0, fixed_fields["timestep_fs"])
    production_steps = _step_count(
        requested.production_ns, 1_000_000.0, fixed_fields["timestep_fs"]
    )
    trajectory_steps = _step_count(
        requested.trajectory_interval_ps, 1000.0, fixed_fields["timestep_fs"]
    )
    energy_steps = _step_count(
        requested.energy_interval_ps, 1000.0, fixed_fields["timestep_fs"]
    )
    step_values = {
        "minimization": requested.minimization_steps,
        "nvt": nvt_steps,
        "npt": npt_steps,
        "production": production_steps,
    }
    current_maxima = {
        "minimization": 5_000_000,
        "nvt": 5_000_000,
        "npt": 5_000_000,
        "production": 50_000_000,
    }
    profile_maxima = {
        "minimization": constraints.get("max_minimization_steps"),
        "nvt": constraints.get("max_nvt_steps"),
        "npt": constraints.get("max_npt_steps"),
        "production": constraints.get("max_production_steps"),
    }
    for stage, steps in step_values.items():
        maximum = profile_maxima[stage]
        if (
            steps < 1
            or steps > current_maxima[stage]
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or steps > maximum
        ):
            blockers.append(
                MdLaunchNotice(
                    code="MD_RESOURCE_CONTRACT_VIOLATION",
                    message=f"The requested {stage} duration exceeds the current schema or profile maximum.",
                )
            )
    if (
        trajectory_steps < 1
        or energy_steps < 1
        or trajectory_steps > production_steps
        or energy_steps > production_steps
    ):
        blockers.append(
            MdLaunchNotice(
                code="MD_RESOURCE_CONTRACT_VIOLATION",
                message="Production output intervals must be positive and cannot exceed production.",
            )
        )
    admitted = constraints.get("structure_sha256") == structure.sha256
    if not admitted:
        blockers.append(
            MdLaunchNotice(
                code="MD_STARTING_STRUCTURE_NOT_ADMITTED",
                message="The selected profile does not admit these exact starting-structure bytes.",
            )
        )

    effective = MdPublicEffectiveRequest(
        replicas=fixed_fields["replicas"],
        random_seed=requested.random_seed,
        preparation=MdPublicPreparation(
            padding_nm=fixed_fields["padding_nm"],
            salt_molar=fixed_fields["salt_molar"],
            neutralize=requested.neutralize,
        ),
        stages=MdPublicStages(
            minimization=MdPublicMinimization(steps=requested.minimization_steps),
            nvt=MdPublicNvt(
                steps=nvt_steps,
                temperature_k=fixed_fields["temperature_k"],
            ),
            npt=MdPublicNpt(
                steps=npt_steps,
                temperature_k=fixed_fields["temperature_k"],
                pressure_bar=fixed_fields["pressure_bar"],
            ),
            production=MdPublicProduction(
                steps=production_steps,
                timestep_fs=fixed_fields["timestep_fs"],
                temperature_k=fixed_fields["temperature_k"],
                pressure_bar=fixed_fields["pressure_bar"],
                checkpoint_interval_minutes=requested.checkpoint_interval_minutes,
                trajectory_interval_steps=trajectory_steps,
                energy_interval_steps=energy_steps,
            ),
        ),
        execution=MdPublicExecution(ntomp=requested.ntomp),
    )
    source = MdLaunchSourceIdentity(
        source_ref=resolved.source_ref,
        label=resolved.label,
        format=structure.format,
        size_bytes=structure.size_bytes,
        sha256=structure.sha256,
        pdb_id=resolved.pdb_id,
        producer_job_id=resolved.producer_job_id,
        design_id=resolved.design_id,
    )
    chemistry = MdLaunchChemistryIdentity(
        profile_id=intent.chemistry_profile_id,
        profile_sha256=intent.chemistry_profile_sha256,
        catalog_digest=current_catalog_digest,
        admitted=admitted,
    )
    preimage = {
        "schema_version": "bms.md.launch-preview-preimage.v1",
        "source_ref": resolved.source_ref.model_dump(mode="json"),
        "source_sha256": structure.sha256,
        "source_size_bytes": structure.size_bytes,
        "chemistry": {
            "profile_id": intent.chemistry_profile_id,
            "profile_sha256": intent.chemistry_profile_sha256,
            "catalog_digest": current_catalog_digest,
        },
        "requested_settings": requested.model_dump(mode="json"),
        "effective_request": effective.model_dump(mode="json"),
    }
    try:
        digest = hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise _error(
            "MD_LAUNCH_PREVIEW_INVALID",
            "The launch preview could not be canonicalized.",
            422,
        ) from exc
    return MdLaunchPreview(
        schema_version="bms.md.launch-preview.v1",
        source=source,
        chemistry=chemistry,
        requested_settings=requested,
        effective_request=effective,
        warnings=warnings,
        blockers=blockers,
        preview_digest=digest,
    )


def compile_md_job_v2(
    *,
    preview: MdLaunchPreview,
    profile: Mapping[str, Any],
    job_id: str,
    source_token: str,
) -> dict[str, Any]:
    if preview.blockers:
        raise _error(
            "MD_LAUNCH_BLOCKED",
            "The molecular-dynamics launch preview contains blockers.",
            409,
        )
    scope = profile["scientific_validation"]["scope"]["launch_scope"]
    effective = preview.effective_request.model_dump(mode="json")
    execution = dict(effective["execution"])
    execution.pop("placement_authority", None)
    execution["gpu_id"] = "0"
    return {
        "schema": "bms.md.job.v2",
        "job_id": job_id,
        "engine": effective["engine"],
        "replicas": effective["replicas"],
        "random_seed": effective["random_seed"],
        "input": {"structure": source_token},
        "chemistry": {
            "profile_id": preview.chemistry.profile_id,
            "profile_sha256": preview.chemistry.profile_sha256,
            "catalog_digest": preview.chemistry.catalog_digest,
            "requested_scope": scope,
        },
        "preparation": effective["preparation"],
        "stages": effective["stages"],
        "execution": execution,
    }


def _parse_mmcif_structure(data: bytes):
    if re.search(rb"(?im)^[ \t]*_atom_site\.occupancy(?:[ \t\r]+|$)", data):
        return MMCIFParser(QUIET=True).get_structure(
            "starting-structure", io.StringIO(data.decode("utf-8"))
        )
    mmcif = MMCIF2Dict(io.StringIO(data.decode("utf-8")))
    atom_ids = mmcif.get("_atom_site.id")
    if not isinstance(atom_ids, list) or not atom_ids:
        raise ValueError("The mmCIF atom-site loop is incomplete.")
    # ESMFold2 omits occupancy. Supply the conventional fully occupied value
    # only to Biopython's in-memory parser representation; source bytes stay exact.
    mmcif["_atom_site.occupancy"] = ["1.0"] * len(atom_ids)
    normalized = io.StringIO()
    writer = MMCIFIO()
    writer.set_dict(mmcif)
    writer.save(normalized)
    del atom_ids, writer, mmcif
    normalized.seek(0)
    return MMCIFParser(QUIET=True).get_structure("starting-structure", normalized)


def _structure_summary(structure_file: StructureFile) -> StartingStructureInspectionSummary:
    try:
        structure = (
            _parse_mmcif_structure(structure_file.data)
            if structure_file.format == "cif"
            else PDBParser(QUIET=True).get_structure(
                "starting-structure", io.StringIO(structure_file.data.decode("utf-8"))
            )
        )
        models = list(structure.get_models())
        chains = sorted({str(chain.id) for chain in structure.get_chains()})
        atoms = list(structure.get_atoms())
        hetero = sorted({
            str(residue.resname).strip()
            for residue in structure.get_residues()
            if str(residue.id[0]).strip() not in {"", " "}
        })
    except Exception as exc:
        raise _error(
            "MD_STARTING_STRUCTURE_PARSE_FAILED",
            "The starting structure could not be parsed safely.",
        ) from exc
    if not models or not chains or not atoms:
        raise _error(
            "MD_STARTING_STRUCTURE_PARSE_FAILED",
            "The starting structure has no parseable models, chains, or atoms.",
        )
    return StartingStructureInspectionSummary(
        model_count=len(models),
        chains=chains,
        atom_count=len(atoms),
        hetero_components=hetero,
        parser=StartingStructureParser(version=str(Bio.__version__)),
    )


def _admission(
    *,
    structure_file: StructureFile,
    chemistry_profile_id: str | None,
    profile: Mapping[str, Any] | None,
) -> StartingStructureAdmission:
    if chemistry_profile_id is None:
        return StartingStructureAdmission(
            state="profile_required",
            profile_id=None,
            code="MD_CHEMISTRY_PROFILE_REQUIRED",
            message="Select a chemistry profile to evaluate exact-byte admission.",
        )
    constraints = profile.get("launch_constraints") if isinstance(profile, Mapping) else None
    states = profile.get("states") if isinstance(profile, Mapping) else None
    if (
        not isinstance(profile, Mapping)
        or profile.get("id") != chemistry_profile_id
        or not isinstance(constraints, Mapping)
        or not isinstance(states, Mapping)
        or states.get("selectable") is not True
    ):
        return StartingStructureAdmission(
            state="blocked",
            profile_id=chemistry_profile_id,
            code="MD_CHEMISTRY_PROFILE_UNAVAILABLE",
            message="The selected chemistry profile is unavailable for launch.",
        )
    if constraints.get("structure_sha256") != structure_file.sha256:
        return StartingStructureAdmission(
            state="blocked",
            profile_id=chemistry_profile_id,
            code="MD_STARTING_STRUCTURE_NOT_ADMITTED",
            message="The selected chemistry profile does not admit these exact starting-structure bytes.",
        )
    return StartingStructureAdmission(
        state="admitted",
        profile_id=chemistry_profile_id,
        code=None,
        message="The exact starting-structure bytes are admitted by the selected chemistry profile.",
    )


def inspect_resolved_structure(
    resolved: ResolvedStartingStructure,
    *,
    chemistry_profile_id: str | None = None,
    profile: Mapping[str, Any] | None = None,
) -> StartingStructureInspection:
    structure_file = read_resolved_structure(resolved)
    encoded_kind = quote(resolved.source_ref.kind, safe="")
    encoded_id = quote(resolved.source_ref.id, safe="")
    viewer_url = (
        f"/api/molecular-dynamics/starting-structures/{encoded_kind}/{encoded_id}/content"
        f"?expected_sha256={structure_file.sha256}"
    )
    return StartingStructureInspection(
        schema_version="bms.md.starting-structure-inspection.v1",
        source_ref=resolved.source_ref,
        identity=StartingStructureIdentity(
            label=resolved.label,
            format=structure_file.format,
            size_bytes=structure_file.size_bytes,
            sha256=structure_file.sha256,
            pdb_id=resolved.pdb_id,
            producer_job_id=resolved.producer_job_id,
            design_id=resolved.design_id,
        ),
        viewer=StartingStructureViewer(
            url=viewer_url,
            format=structure_file.format,
            sha256=structure_file.sha256,
        ),
        inspection=_structure_summary(structure_file),
        admission=_admission(
            structure_file=structure_file,
            chemistry_profile_id=chemistry_profile_id,
            profile=profile,
        ),
    )


def require_expected_sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise _error(
            "MD_STARTING_STRUCTURE_DIGEST_INVALID",
            "expected_sha256 must be 64 lowercase hexadecimal characters.",
        )
    return value


__all__ = [
    "MAX_STARTING_STRUCTURE_BYTES",
    "PUBLIC_STRUCTURE_FORMAT",
    "ResolvedStartingStructure",
    "StartingStructureAdmission",
    "StartingStructureError",
    "StartingStructureInspectRequest",
    "StartingStructureInspection",
    "StartingStructureSourceRef",
    "StructureFile",
    "inspect_resolved_structure",
    "read_structure_file",
    "require_expected_sha256",
    "resolve_product_source",
]
