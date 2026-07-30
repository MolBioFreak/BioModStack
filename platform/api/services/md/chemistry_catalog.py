from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

CATALOG_SCHEMA = "bms.md.chemistry-profile.v1"
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{2,95}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_PROFILES = 64
MAX_PROFILE_BYTES = 128 * 1024
MAX_PROBE_ASSETS = 128
DEFAULT_GROMACS_IMAGE = "gromacs-md-2025.3.sif"
DEFAULT_PREPARATION_IMAGE = "md-preparation-v1.sif"
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RuntimeIdentity:
    runtime_id: str
    runtime_version: str | None
    sif_sha256: str | None

    def as_public_dict(self) -> dict[str, str | None]:
        return {
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "sif_sha256": self.sif_sha256,
        }


@dataclass(frozen=True)
class RuntimeProbeResult:
    runtime_id: str
    runtime_version: str | None
    available: bool
    asset_ids: frozenset[str]
    checked_at: str
    error_code: str | None = None
    sif_sha256: str | None = None

    @property
    def runtime_identity(self) -> RuntimeIdentity:
        return RuntimeIdentity(
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            sif_sha256=self.sif_sha256,
        )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(_thaw(item) for item in value)
    return copy.deepcopy(value)


@dataclass(frozen=True)
class CatalogView:
    """Deeply immutable public projection of one catalog generation."""

    profiles: tuple[Mapping[str, Any], ...]
    profile_index: Mapping[str, Mapping[str, Any]]
    catalog_digest: str
    probe_summary: Mapping[str, Any]
    runtime_identity: Mapping[str, Any]
    generation: int
    loaded_at: str

    def list_profiles(self) -> list[dict[str, Any]]:
        return _thaw(self.profiles)

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.profile_index.get(str(profile_id or "").strip())
        return _thaw(profile) if profile is not None else None

    def public_probe_summary(self) -> dict[str, Any]:
        return _thaw(self.probe_summary)


@dataclass(frozen=True)
class _CatalogSnapshot:
    """One atomically published catalog/probe generation.

    Resolved profiles are retained only in this private tuple and every public
    caller receives a deep copy, so no caller can mutate a published generation.
    """

    view: CatalogView
    probe_results: tuple[RuntimeProbeResult, ...]
    loaded_monotonic: float


class ChemistryCatalogError(RuntimeError):
    """Raised when the checked-in catalog is malformed or exceeds its bounds."""


class ChemistryProfileSelectionError(ValueError):
    """Stable fail-closed error for an invalid v1 chemistry selection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_image_path() -> Path | None:
    direct = str(os.getenv("BMS_MD_GROMACS_SIF") or "").strip()
    candidates: list[Path] = []
    if direct:
        candidates.append(Path(direct))
    container_dir = str(os.getenv("BMS_CONTAINER_DIR") or "").strip()
    if container_dir:
        candidates.append(Path(container_dir) / DEFAULT_GROMACS_IMAGE)
    data_root = str(os.getenv("BMS_DATA") or "").strip()
    if data_root:
        candidates.append(Path(data_root) / "apptainer" / DEFAULT_GROMACS_IMAGE)
    candidates.append(Path("/mnt/BioModStack/apptainer") / DEFAULT_GROMACS_IMAGE)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _preparation_image_path() -> Path | None:
    direct = str(os.getenv("BMS_MD_PREPARATION_SIF") or "").strip()
    candidates: list[Path] = [Path(direct)] if direct else []
    data_root = str(os.getenv("BMS_DATA") or "").strip()
    if data_root:
        candidates.append(Path(data_root) / "apptainer" / DEFAULT_PREPARATION_IMAGE)
    candidates.append(Path("/mnt/BioModStack/apptainer") / DEFAULT_PREPARATION_IMAGE)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


_SIF_DIGEST_LOCK = threading.Lock()
_SIF_DIGEST_MEMO: dict[str, tuple[tuple[int, int, int, int, int], str]] = {}


def _stat_fingerprint(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns)


def _hash_sif_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _memoized_sif_sha256(path: Path) -> str:
    """Hash a SIF once per stable stat fingerprint across concurrent probes."""

    memo_key = os.fspath(path.resolve())
    with _SIF_DIGEST_LOCK:
        for _attempt in range(3):
            before = path.stat()
            fingerprint = _stat_fingerprint(before)
            cached = _SIF_DIGEST_MEMO.get(memo_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            digest = _hash_sif_file(path)
            after = path.stat()
            if _stat_fingerprint(after) == fingerprint:
                _SIF_DIGEST_MEMO[memo_key] = (fingerprint, digest)
                return digest
        raise OSError("runtime image changed while its identity was being calculated")


def _clear_sif_digest_memo_for_tests() -> None:
    with _SIF_DIGEST_LOCK:
        _SIF_DIGEST_MEMO.clear()


def probe_deployed_gromacs_assets(
    *,
    image_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RuntimeProbeResult:
    """Probe the deployed image once with a fixed, bounded, read-only command.

    The returned record contains only logical asset IDs. Host paths and command
    output are intentionally retained inside this function and never serialized.
    """

    image = image_path or _runtime_image_path()
    if image is None or not image.is_file():
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at=_utc_now(),
            error_code="runtime_image_missing",
        )

    try:
        sif_sha256 = _memoized_sif_sha256(image)
    except OSError:
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at=_utc_now(),
            error_code="runtime_identity_failed",
        )

    probe_script = """
set -eu
for root in \
  /usr/local/gromacs/avx2_256/share/gromacs/top \
  /usr/local/gromacs/share/gromacs/top \
  /usr/share/gromacs/top \
  /opt/gromacs/share/gromacs/top
do
  if [ -d "$root" ]; then
    for directory in "$root"/*.ff; do
      [ -d "$directory" ] && basename "$directory"
    done
    exit 0
  fi
done
exit 2
""".strip()
    try:
        completed = runner(
            ["apptainer", "exec", str(image), "sh", "-c", probe_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at=_utc_now(),
            error_code="runtime_probe_failed",
        )

    if completed.returncode != 0:
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at=_utc_now(),
            error_code="runtime_probe_failed",
        )

    assets: list[str] = []
    for line in completed.stdout.splitlines()[:MAX_PROBE_ASSETS]:
        asset_id = line.strip()
        if re.fullmatch(r"[A-Za-z0-9_.+-]{1,96}\.ff", asset_id):
            assets.append(asset_id)
    return RuntimeProbeResult(
        runtime_id="gromacs-2025.3",
        runtime_version="2025.3",
        available=True,
        asset_ids=frozenset(assets[:MAX_PROBE_ASSETS]),
        checked_at=_utc_now(),
        error_code=None,
        sif_sha256=sif_sha256,
    )


def probe_deployed_preparation_assets(
    *,
    image_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RuntimeProbeResult:
    image = Path(image_path) if image_path is not None else _preparation_image_path()
    unavailable = dict(
        runtime_id="md-preparation-v1",
        runtime_version=None,
        available=False,
        asset_ids=frozenset(),
        checked_at=_utc_now(),
    )
    if image is None or not image.is_file():
        return RuntimeProbeResult(**unavailable, error_code="runtime_image_missing")
    try:
        sif_sha256 = _memoized_sif_sha256(image)
    except OSError:
        return RuntimeProbeResult(**unavailable, error_code="runtime_identity_failed")
    probe_script = """
set -eu
root=/opt/md-preparation/dat/leap
[ -f "$root/cmd/leaprc.protein.ff19SB" ] && echo amber/ff19SB
[ -f "$root/cmd/leaprc.DNA.OL15" ] && echo amber/OL15
[ -f "$root/cmd/leaprc.DNA.OL21" ] && echo amber/OL21
[ -f "$root/cmd/leaprc.DNA.bsc1" ] && echo amber/parmbsc1
[ -f "$root/cmd/leaprc.water.opc" ] && echo water/opc
[ -f "$root/parm/frcmod.ionslm_126_opc" ] && echo ions/opc-monovalent-pinned
""".strip()
    try:
        completed = runner(
            ["apptainer", "exec", str(image), "sh", "-c", probe_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return RuntimeProbeResult(**unavailable, error_code="runtime_probe_failed")
    if completed.returncode != 0:
        return RuntimeProbeResult(**unavailable, error_code="runtime_probe_failed")
    assets = frozenset(
        line.strip() for line in completed.stdout.splitlines()[:MAX_PROBE_ASSETS]
        if re.fullmatch(r"[A-Za-z0-9_.+/-]{1,96}", line.strip())
    )
    return RuntimeProbeResult(
        runtime_id="md-preparation-v1",
        runtime_version="1",
        available=True,
        asset_ids=assets,
        checked_at=_utc_now(),
        sif_sha256=sif_sha256,
    )


def probe_deployed_md_assets() -> tuple[RuntimeProbeResult, ...]:
    return (probe_deployed_gromacs_assets(), probe_deployed_preparation_assets())


def _require_bool(record: Mapping[str, Any], field: str) -> bool:
    value = record.get(field)
    if not isinstance(value, bool):
        raise ChemistryCatalogError(f"{field} must be a boolean")
    return value


def _require_string(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ChemistryCatalogError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_digest(payload: Mapping[str, Any] | list[Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_launch_constraints(record: Mapping[str, Any], profile_id: str) -> dict[str, Any] | None:
    constraints = record.get("launch_constraints")
    inventory_class = _require_string(record, "inventory_class")
    if inventory_class not in {"candidate", "selectable"}:
        raise ChemistryCatalogError(f"inventory_class must be candidate or selectable: {profile_id}")
    if constraints is None:
        if inventory_class == "selectable":
            raise ChemistryCatalogError(f"launch_constraints are required for selectable profile: {profile_id}")
        return None
    if not isinstance(constraints, Mapping):
        raise ChemistryCatalogError(f"launch_constraints must be an object or null: {profile_id}")

    normalized = copy.deepcopy(dict(constraints))
    for field in ("input_mode", "structure_sha256", "engine", "force_field", "water_model"):
        _require_string(normalized, field)
    if normalized["input_mode"] != "structure":
        raise ChemistryCatalogError(f"launch_constraints.input_mode must be structure: {profile_id}")
    if not SHA256_PATTERN.fullmatch(normalized["structure_sha256"]):
        raise ChemistryCatalogError(f"launch_constraints.structure_sha256 must be lowercase SHA-256: {profile_id}")

    integer_fields = (
        "replicas",
        "max_production_steps",
        "max_minimization_steps",
        "max_nvt_steps",
        "max_npt_steps",
    )
    for field in integer_fields:
        value = normalized.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ChemistryCatalogError(f"launch_constraints.{field} must be a positive integer: {profile_id}")
    numeric_fields = ("timestep_fs", "temperature_k", "pressure_bar", "salt_molar", "padding_nm")
    for field in numeric_fields:
        value = normalized.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
            raise ChemistryCatalogError(f"launch_constraints.{field} must be positive: {profile_id}")
        normalized[field] = float(value)

    preparation = record["v1_preparation"]
    if normalized["force_field"] != preparation["force_field"] or normalized["water_model"] != preparation["water_model"]:
        raise ChemistryCatalogError(f"launch_constraints chemistry does not match v1_preparation: {profile_id}")
    if normalized["engine"] not in record["supported_engines"]:
        raise ChemistryCatalogError(f"launch_constraints engine is not supported: {profile_id}")
    return normalized


class ChemistryCatalog:
    """Versioned chemistry catalog with one bounded, cached deployment probe."""

    def __init__(
        self,
        *,
        config_dir: Path,
        probe: Callable[[], RuntimeProbeResult | Sequence[RuntimeProbeResult]] = probe_deployed_md_assets,
        max_profiles: int = MAX_PROFILES,
        monotonic_clock: Callable[[], float] = time.monotonic,
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        ttl = float(cache_ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("cache_ttl_seconds must be a positive finite number")
        self._config_dir = Path(config_dir)
        self._probe = probe
        self._max_profiles = min(MAX_PROFILES, max(1, int(max_profiles)))
        self._monotonic_clock = monotonic_clock
        self._cache_ttl_seconds = ttl
        self._lock = threading.Lock()
        self._snapshot: _CatalogSnapshot | None = None
        self._generation = 0

    def _load_records(self) -> list[dict[str, Any]]:
        try:
            paths = sorted(self._config_dir.glob("*.yaml"))
        except OSError as exc:
            raise ChemistryCatalogError("The molecular-dynamics chemistry catalog could not be read.") from exc
        if not paths:
            raise ChemistryCatalogError("chemistry catalog contains no YAML profiles")
        if len(paths) > self._max_profiles:
            raise ChemistryCatalogError(f"chemistry catalog exceeds {self._max_profiles} profiles")

        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in paths:
            try:
                size = path.stat().st_size
                raw_profile = path.read_text(encoding="utf-8")
                loaded = yaml.safe_load(raw_profile)
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ChemistryCatalogError(
                    "The molecular-dynamics chemistry catalog contains an unreadable profile."
                ) from exc
            if size > MAX_PROFILE_BYTES:
                raise ChemistryCatalogError(f"chemistry profile exceeds {MAX_PROFILE_BYTES} bytes: {path.name}")
            if not isinstance(loaded, Mapping):
                raise ChemistryCatalogError(f"chemistry profile must be an object: {path.name}")
            record = copy.deepcopy(dict(loaded))
            if record.get("schema") != CATALOG_SCHEMA:
                raise ChemistryCatalogError(f"unsupported chemistry profile schema: {path.name}")
            profile_id = _require_string(record, "id")
            if not PROFILE_ID_PATTERN.fullmatch(profile_id):
                raise ChemistryCatalogError(f"invalid chemistry profile ID: {profile_id}")
            if profile_id in seen:
                raise ChemistryCatalogError(f"duplicate chemistry profile ID: {profile_id}")
            seen.add(profile_id)
            for field in ("version", "display_name", "family", "assurance", "inventory_class"):
                _require_string(record, field)
            for field in ("legacy", "automatic_preparation", "installed", "runtime_validated", "operator_enabled"):
                _require_bool(record, field)

            engines = record.get("supported_engines")
            if not isinstance(engines, list) or not engines or not all(isinstance(item, str) for item in engines):
                raise ChemistryCatalogError(f"supported_engines must be a non-empty string list: {profile_id}")
            preparation = record.get("v1_preparation")
            if not isinstance(preparation, Mapping):
                raise ChemistryCatalogError(f"v1_preparation must be an object: {profile_id}")
            _require_string(preparation, "force_field")
            _require_string(preparation, "water_model")
            record["launch_constraints"] = _validated_launch_constraints(record, profile_id)
            asset_probe = record.get("asset_probe")
            if not isinstance(asset_probe, Mapping):
                raise ChemistryCatalogError(f"asset_probe must be an object: {profile_id}")
            normalized_asset_probe = copy.deepcopy(dict(asset_probe))
            _require_string(normalized_asset_probe, "runtime_id")
            asset_ids = asset_probe.get("required_asset_ids")
            if not isinstance(asset_ids, list) or not asset_ids or not all(isinstance(item, str) and item for item in asset_ids):
                raise ChemistryCatalogError(f"required_asset_ids must be a non-empty string list: {profile_id}")
            required_identity = normalized_asset_probe.get("required_runtime_identity")
            if required_identity is None and record["inventory_class"] == "selectable":
                raise ChemistryCatalogError(f"required_runtime_identity is required for selectable profile: {profile_id}")
            if required_identity is not None:
                if not isinstance(required_identity, Mapping):
                    raise ChemistryCatalogError(f"required_runtime_identity must be an object: {profile_id}")
                normalized_identity = copy.deepcopy(dict(required_identity))
                for field in ("runtime_id", "runtime_version", "sif_sha256"):
                    _require_string(normalized_identity, field)
                if normalized_identity["runtime_id"] != normalized_asset_probe["runtime_id"]:
                    raise ChemistryCatalogError(f"required runtime IDs do not match: {profile_id}")
                if not SHA256_PATTERN.fullmatch(normalized_identity["sif_sha256"]):
                    raise ChemistryCatalogError(f"required_runtime_identity.sif_sha256 must be lowercase SHA-256: {profile_id}")
                normalized_asset_probe["required_runtime_identity"] = normalized_identity
            record["asset_probe"] = normalized_asset_probe
            validation = record.get("scientific_validation")
            if not isinstance(validation, Mapping) or not isinstance(validation.get("validated"), bool):
                raise ChemistryCatalogError(f"scientific_validation.validated must be a boolean: {profile_id}")
            scope = validation.get("scope")
            if not isinstance(scope, Mapping):
                raise ChemistryCatalogError(f"scientific_validation.scope must be an object: {profile_id}")
            for field in ("launch_scope", "composition", "protocol", "ionic_conditions"):
                _require_string(scope, field)
            system_classes = scope.get("system_classes")
            observables = scope.get("observables")
            if not isinstance(system_classes, list) or not system_classes:
                raise ChemistryCatalogError(f"scientific_validation.scope.system_classes is required: {profile_id}")
            if not isinstance(observables, list) or not observables:
                raise ChemistryCatalogError(f"scientific_validation.scope.observables is required: {profile_id}")
            records.append(record)
        return records

    def _build_snapshot(self, *, generation: int) -> _CatalogSnapshot:
        try:
            raw_probe = self._probe()
        except ChemistryCatalogError:
            raise
        except Exception as exc:
            raise ChemistryCatalogError(
                "The molecular-dynamics chemistry runtime probe is unavailable."
            ) from exc
        if isinstance(raw_probe, RuntimeProbeResult):
            probes = (raw_probe,)
        elif isinstance(raw_probe, Sequence) and not isinstance(raw_probe, (str, bytes)):
            probes = tuple(raw_probe)
        else:
            raise ChemistryCatalogError("runtime probe returned an invalid result")
        if not probes or not all(isinstance(item, RuntimeProbeResult) for item in probes):
            raise ChemistryCatalogError("runtime probe returned an invalid result")
        probe_by_id = {item.runtime_id: item for item in probes}
        if len(probe_by_id) != len(probes):
            raise ChemistryCatalogError("runtime probe returned duplicate runtime IDs")
        primary_probe = probe_by_id.get("gromacs-2025.3", probes[0])
        resolved: list[dict[str, Any]] = []
        for record in self._load_records():
            runtime_id = record["asset_probe"]["runtime_id"]
            probe = probe_by_id.get(runtime_id) or RuntimeProbeResult(
                runtime_id=runtime_id,
                runtime_version=None,
                available=False,
                asset_ids=frozenset(),
                checked_at=primary_probe.checked_at,
                error_code="runtime_probe_missing",
            )
            runtime_identity = probe.runtime_identity.as_public_dict()
            required_assets = set(record["asset_probe"]["required_asset_ids"])
            required_identity = record["asset_probe"].get("required_runtime_identity")
            correct_runtime = (
                dict(required_identity) == runtime_identity
                if isinstance(required_identity, Mapping)
                else record["asset_probe"]["runtime_id"] == probe.runtime_id
            )
            asset_probe_success = probe.available and correct_runtime and required_assets.issubset(probe.asset_ids)
            installed = bool(record["installed"] and asset_probe_success)
            runtime_validated = bool(record["runtime_validated"] and asset_probe_success)
            scientific_validation = copy.deepcopy(dict(record["scientific_validation"]))
            scientifically_validated = bool(scientific_validation["validated"])
            operator_enabled = bool(record["operator_enabled"])
            selectable = bool(
                installed
                and asset_probe_success
                and runtime_validated
                and scientifically_validated
                and operator_enabled
            )
            states = {
                "installed": installed,
                "runtime_validated": runtime_validated,
                "scientifically_validated": scientifically_validated,
                "operator_enabled": operator_enabled,
                "asset_probe_success": asset_probe_success,
                "selectable": selectable,
            }
            if selectable:
                explanation = (
                    "Selectable only for "
                    f"{scientific_validation['scope']['launch_scope']} within the explicit validation scope."
                )
            else:
                blockers: list[str] = []
                if not installed:
                    blockers.append("deployed assets are absent")
                if not runtime_validated:
                    blockers.append("runtime validation has not passed")
                if not scientifically_validated:
                    blockers.append("scoped scientific validation has not passed")
                if not operator_enabled:
                    blockers.append("operator enablement is off")
                explanation = f"Candidate; not selectable because {', '.join(blockers)}."

            public_profile = {
                "schema": record["schema"],
                "id": record["id"],
                "version": record["version"],
                "display_name": record["display_name"],
                "family": record["family"],
                "assurance": record["assurance"],
                "legacy": record["legacy"],
                "automatic_preparation": record["automatic_preparation"],
                "inventory_class": "selectable" if selectable else "candidate",
                "availability_explanation": explanation,
                "supported_engines": list(record["supported_engines"]),
                "v1_preparation": copy.deepcopy(dict(record["v1_preparation"])),
                "launch_constraints": copy.deepcopy(record["launch_constraints"]),
                "scientific_validation": scientific_validation,
                "states": states,
                "explicit_exclusions": list(record.get("explicit_exclusions") or []),
                "runtime_identity": copy.deepcopy(runtime_identity),
            }
            public_profile["profile_sha256"] = _canonical_digest(public_profile)
            resolved.append(public_profile)

        resolved.sort(key=lambda profile: (not profile["states"]["selectable"], profile["id"]))
        catalog_digest = _canonical_digest(
            [{"id": profile["id"], "profile_sha256": profile["profile_sha256"]} for profile in resolved]
        )
        frozen_profiles = tuple(_freeze(profile) for profile in resolved)
        primary_runtime_identity = primary_probe.runtime_identity.as_public_dict()
        probe_summary = _freeze(
            {
                "runtime_id": primary_probe.runtime_id,
                "runtime_version": primary_probe.runtime_version,
                "available": primary_probe.available,
                "checked_at": primary_probe.checked_at,
                "discovered_asset_count": len(primary_probe.asset_ids),
                "error_code": primary_probe.error_code,
                "cached": True,
                "runtime_identity": primary_runtime_identity,
                "runtimes": [
                    {
                        "runtime_id": item.runtime_id,
                        "runtime_version": item.runtime_version,
                        "available": item.available,
                        "checked_at": item.checked_at,
                        "discovered_asset_count": len(item.asset_ids),
                        "error_code": item.error_code,
                        "runtime_identity": item.runtime_identity.as_public_dict(),
                    }
                    for item in probes
                ],
            }
        )
        view = CatalogView(
            profiles=frozen_profiles,
            profile_index=MappingProxyType({profile["id"]: profile for profile in frozen_profiles}),
            catalog_digest=catalog_digest,
            probe_summary=probe_summary,
            runtime_identity=_freeze(primary_runtime_identity),
            generation=generation,
            loaded_at=_utc_now(),
        )
        return _CatalogSnapshot(
            view=view,
            probe_results=probes,
            loaded_monotonic=float(self._monotonic_clock()),
        )

    def _current_snapshot(self) -> _CatalogSnapshot:
        snapshot = self._snapshot
        now = float(self._monotonic_clock())
        if snapshot is not None and now - snapshot.loaded_monotonic < self._cache_ttl_seconds:
            return snapshot
        with self._lock:
            snapshot = self._snapshot
            now = float(self._monotonic_clock())
            if snapshot is None or now - snapshot.loaded_monotonic >= self._cache_ttl_seconds:
                snapshot = self._build_snapshot(generation=self._generation + 1)
                self._snapshot = snapshot
                self._generation = snapshot.view.generation
            return snapshot

    def view(self) -> CatalogView:
        return self._current_snapshot().view

    def list_profiles(self) -> list[dict[str, Any]]:
        return self.view().list_profiles()

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        return self.view().get_profile(profile_id)

    def catalog_digest(self) -> str:
        return self.view().catalog_digest

    def probe_summary(self) -> dict[str, Any]:
        return self.view().public_probe_summary()

    def refresh(self) -> None:
        """Internal/operator refresh hook; deliberately not exposed by the router."""
        with self._lock:
            try:
                snapshot = self._build_snapshot(generation=self._generation + 1)
            except Exception:
                self._snapshot = None
                raise
            self._snapshot = snapshot
            self._generation = snapshot.view.generation

    def validate_v1_profile_selection(
        self,
        *,
        profile_id: str | None,
        profile_sha256: str | None,
        force_field: str,
        water_model: str,
        engine: str,
        requested_scope: str | None,
        view: CatalogView | None = None,
    ) -> dict[str, Any]:
        captured_view = view or self.view()
        normalized_id = str(profile_id or "").strip()
        supplied_digest = str(profile_sha256 or "").strip()
        normalized_scope = str(requested_scope or "").strip()
        missing_claims = [
            name
            for name, value in (
                ("chemistry_profile_id", normalized_id),
                ("chemistry_profile_sha256", supplied_digest),
                ("chemistry_profile_scope", normalized_scope),
            )
            if not value
        ]
        if missing_claims:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_REQUIRED",
                f"Exact v1 chemistry profile claims are required: {', '.join(missing_claims)}.",
            )
        profile = captured_view.profile_index.get(normalized_id)
        if profile is None:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_UNKNOWN",
                f"Unknown molecular-dynamics chemistry profile: {normalized_id}",
            )
        if supplied_digest != profile["profile_sha256"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_UNAVAILABLE",
                f"Chemistry profile {normalized_id} is stale; reload the deployed catalog before launch.",
            )

        states = profile["states"]
        if not states["installed"] or not states["asset_probe_success"] or not states["operator_enabled"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_UNAVAILABLE",
                f"Chemistry profile {profile['id']} is unavailable in the deployed runtime.",
            )
        if not states["runtime_validated"] or not states["scientifically_validated"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_NOT_VALIDATED",
                f"Chemistry profile {profile['id']} has not passed all scoped validation gates.",
            )
        if not states["selectable"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_PROFILE_UNAVAILABLE",
                f"Chemistry profile {profile['id']} is not selectable.",
            )
        expected = profile["v1_preparation"]
        if str(force_field).strip() != expected["force_field"] or str(water_model).strip() != expected["water_model"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_COMBINATION_UNSUPPORTED",
                f"Chemistry profile {profile['id']} does not match the submitted v1 force-field/water values.",
            )
        if str(engine).strip().lower() not in profile["supported_engines"]:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_COMBINATION_UNSUPPORTED",
                f"Chemistry profile {profile['id']} is not validated for engine {engine}.",
            )
        expected_scope = profile["scientific_validation"]["scope"]["launch_scope"]
        if normalized_scope != expected_scope:
            raise ChemistryProfileSelectionError(
                "MD_CHEMISTRY_COMBINATION_UNSUPPORTED",
                f"Chemistry profile {profile['id']} is restricted to {expected_scope}.",
            )
        return _thaw(profile)


DEFAULT_CATALOG_DIR = Path(__file__).resolve().parents[2] / "config" / "md_chemistry_profiles"


@lru_cache(maxsize=1)
def get_chemistry_catalog() -> ChemistryCatalog:
    return ChemistryCatalog(config_dir=DEFAULT_CATALOG_DIR)


def refresh_chemistry_catalog_for_operator() -> None:
    """Internal lifecycle hook for an authenticated operator process."""
    get_chemistry_catalog().refresh()
