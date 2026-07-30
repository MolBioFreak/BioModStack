from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 100 * 1024 * 1024
REQUIRED_OUTPUTS = (
    "source.pdb",
    "system.gro",
    "system.inpcrd",
    "system.pdb",
    "system.prmtop",
    "system.top",
    "posre.itp",
)


class PreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparationProfile:
    id: str
    leaprc: tuple[str, ...]
    solvent_box: str = "OPCBOX"
    gromacs_gpu_offload: str = "full_forces"


_PROFILES = {
    "amber_ff19sb_opc_protein_v1": PreparationProfile(
        "amber_ff19sb_opc_protein_v1", ("leaprc.protein.ff19SB", "leaprc.water.opc")),
    "amber_ff19sb_ol15_opc_protein_dna_v1": PreparationProfile(
        "amber_ff19sb_ol15_opc_protein_dna_v1",
        ("leaprc.protein.ff19SB", "leaprc.DNA.OL15", "leaprc.water.opc")),
    "amber_ff19sb_bsc1_opc_protein_dna_v1": PreparationProfile(
        "amber_ff19sb_bsc1_opc_protein_dna_v1",
        ("leaprc.protein.ff19SB", "leaprc.DNA.bsc1", "leaprc.water.opc")),
    "amber_ff19sb_ol21_opc_protein_dna_v1": PreparationProfile(
        "amber_ff19sb_ol21_opc_protein_dna_v1",
        ("leaprc.protein.ff19SB", "leaprc.DNA.OL21", "leaprc.water.opc")),
}


def preparation_profile(profile_id: str) -> PreparationProfile:
    try:
        return _PROFILES[str(profile_id).strip()]
    except KeyError as exc:
        raise ValueError(f"unsupported preparation profile: {profile_id}") from exc


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path, *, maximum: int | None = None) -> str:
    digest = hashlib.sha256(); consumed = 0
    with path.open("rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise PreparationError("preparation input must be a non-symlink regular file")
        if maximum is not None and metadata.st_size > maximum:
            raise PreparationError("preparation input exceeds its bounded size")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            consumed += len(chunk)
            if maximum is not None and consumed > maximum:
                raise PreparationError("preparation input exceeds its bounded size")
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise PreparationError(f"required preparation artifact is absent: {relative}")
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def build_preparation_bundle(
    *, source_structure: Path, destination: Path, profile_id: str, profile_sha256: str,
    runtime_lock: Path, padding_nm: float = 1.0, salt_molar: float = 0.15,
    neutralize: bool = True, python_executable: str = sys.executable,
    worker_command: Sequence[str] | None = None, runtime_image: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    profile = preparation_profile(profile_id)
    source_structure = Path(source_structure); destination = Path(destination); runtime_lock = Path(runtime_lock)
    runtime_image = Path(runtime_image) if runtime_image is not None else None
    if len(profile_sha256) != 64 or any(char not in "0123456789abcdef" for char in profile_sha256):
        raise ValueError("profile_sha256 must be a lowercase SHA-256")
    if padding_nm <= 0 or not 0 <= salt_molar <= 2:
        raise ValueError("preparation solvent parameters are outside supported bounds")
    if not neutralize:
        raise ValueError("curated preparation profiles require charge neutralization")
    if destination.exists():
        raise PreparationError("preparation bundle destination already exists")
    source_digest = _sha256(source_structure, maximum=MAX_SOURCE_BYTES)
    runtime_digest = _sha256(runtime_lock, maximum=10 * 1024 * 1024)
    runtime_image_digest = _sha256(runtime_image) if runtime_image is not None else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    published = False
    try:
        shutil.copyfile(source_structure, temporary / "source.pdb")
        request = {
            "schema": "bms.md.preparation-request.v1", "profile": asdict(profile),
            "padding_nm": float(padding_nm), "salt_molar": float(salt_molar), "neutralize": bool(neutralize),
        }
        request_path = temporary / "request.json"
        request_path.write_text(json.dumps(request, sort_keys=True) + "\n", encoding="utf-8")
        try:
            prefix = list(worker_command) if worker_command is not None else [python_executable]
            if not prefix or not all(isinstance(item, str) and item for item in prefix):
                raise ValueError("worker_command must contain non-empty command arguments")
            completed = runner([*prefix, str(Path(__file__).with_name("amber_prepare_worker.py")),
                                "--request", "request.json"], cwd=temporary, check=False,
                               capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreparationError("chemistry preparation runtime failed") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
            raise PreparationError(
                "chemistry preparation runtime failed" + (f": {detail}" if detail else "")
            )
        report_path = temporary / "preparation_report.json"
        if not report_path.is_file():
            raise PreparationError("preparation worker produced no report")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for relative in REQUIRED_OUTPUTS:
            path = temporary / relative
            if not path.is_file() or path.is_symlink():
                raise PreparationError(f"required preparation artifact is absent: {relative}")
            os.chmod(path, 0o444)
        manifest = {
            "schema": "bms.md.preparation-bundle.v1",
            "profile": {"id": profile.id, "sha256": profile_sha256},
            "source": {"sha256": source_digest, "bytes": source_structure.stat().st_size},
            "runtime": {"lock_sha256": runtime_digest, "image_sha256": runtime_image_digest},
            "preparation": report,
            "files": [_artifact(temporary, name) for name in REQUIRED_OUTPUTS],
        }
        manifest["bundle_sha256"] = _canonical_digest(manifest)
        (temporary / "preparation_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        )
        os.chmod(temporary / "preparation_manifest.json", 0o444)
        request_path.unlink(); report_path.unlink()
        _fsync_directory(temporary); os.replace(temporary, destination); published = True
        _fsync_directory(destination.parent)
        return manifest
    except Exception:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_preparation_bundle(
    bundle: Path,
    *,
    expected_profile_id: str | None = None,
    expected_profile_sha256: str | None = None,
) -> dict[str, Any]:
    root = Path(bundle).expanduser().resolve()
    manifest_path = root / "preparation_manifest.json"
    if not root.is_dir() or root.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise PreparationError("preparation bundle root or manifest is unavailable")
    if manifest_path.stat().st_size > MAX_SOURCE_BYTES:
        raise PreparationError("preparation manifest exceeds the admission bound")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreparationError("preparation manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "bms.md.preparation-bundle.v1":
        raise PreparationError("preparation manifest schema is unsupported")
    claimed = manifest.get("bundle_sha256")
    unsigned = dict(manifest)
    unsigned.pop("bundle_sha256", None)
    if not isinstance(claimed, str) or claimed != _canonical_digest(unsigned):
        raise PreparationError("preparation bundle digest mismatch")
    profile = manifest.get("profile")
    if not isinstance(profile, dict):
        raise PreparationError("preparation profile identity is absent")
    if expected_profile_id is not None and profile.get("id") != expected_profile_id:
        raise PreparationError("preparation profile ID mismatch")
    if expected_profile_sha256 is not None and profile.get("sha256") != expected_profile_sha256:
        raise PreparationError("preparation profile digest mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or {record.get("path") for record in files if isinstance(record, dict)} != set(REQUIRED_OUTPUTS):
        raise PreparationError("preparation bundle file inventory is incomplete")
    for record in files:
        name = record.get("path") if isinstance(record, dict) else None
        if not isinstance(name, str) or Path(name).name != name:
            raise PreparationError("preparation bundle contains an invalid file record")
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise PreparationError(f"preparation bundle artifact is unavailable: {name}")
        if path.stat().st_size != record.get("bytes") or _sha256(path) != record.get("sha256"):
            raise PreparationError(f"preparation bundle artifact digest mismatch: {name}")
    return manifest
