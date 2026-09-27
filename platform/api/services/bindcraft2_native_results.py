"""Read pinned BindCraft2 native campaign output without manufacturing candidate joins.

Source: PacesaLab/BindCraft2 d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f,
``campaign_output.py``, ``campaign.py``, ``MPNN_stage.py`` and ``docs/outputs.md``.
This pure reader accepts the same sealed campaign root after local or remote return.
It never decides which structure becomes a BMS Design: that requires producer
identity, verified artifact bytes and the shared publication owner.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal


class NativeResultError(ValueError):
    """Malformed native output or unsafe artifact in a sealed campaign."""


@dataclass(frozen=True)
class NativeRow:
    stage: Literal["trajectory", "draw", "retained"]
    arm: str | None
    design: str
    recipe_hash: str | None
    values: dict[str, str | None]
    targets: tuple[tuple[str, float], ...]
    target_readings: dict[str, dict[str, str | None]]
    sequence: str | None = None  # '/'-separated multichain sequence is preserved
    outcome: Literal["passed", "rejected"] | None = None
    failed_filters: tuple[str, ...] = ()
    rank: int | None = None  # snapshot position, never candidate identity
    terminated: str | None = None
    # A recipe hash is a native trajectory join key, NOT a complete settings digest.
    trajectory_design: str | None = None
    scored_design: str | None = None  # explicit producer association only
    attempt_sha256: str | None = None  # qualified full effective-settings snapshot


@dataclass(frozen=True)
class NativeDocument:
    path: str  # relative to the sealed publication root; NOT a subject join
    sha256: str
    format: str
    retained_design: str | None = None  # only verified native CIF metadata can bind a document
    attempt_sha256: str | None = None
    target_state: str | None = None  # producer-stamped prediction key, never filename inference
    primary_target_state: str | None = None
    structure_variant: str | None = None  # native or relaxed
    binder_chains: str | None = None
    target_chains: str | None = None


@dataclass(frozen=True)
class NativeAttempt:
    design: str
    trajectory: int
    recipe_hash: str
    effective_settings: dict[str, Any]
    drawn: dict[str, Any]
    sha256: str

@dataclass(frozen=True)
class NativeArm:
    name: str | None
    claimed_attempts: int | None
    trajectories: tuple[NativeRow, ...]
    draws: tuple[NativeRow, ...]
    retained: tuple[NativeRow, ...]
    documents: tuple[NativeDocument, ...]
    metadata: dict[str, Any] | None
    attempts: tuple[NativeAttempt, ...] = ()

    @property
    def accounting(self) -> dict[str, int | None]:
        return {
            "claimed_attempts": self.claimed_attempts,
            "emitted_trajectories": len(self.trajectories),
            "scored_draws": len(self.draws),
            "passing_draws": sum(row.outcome == "passed" for row in self.draws),
            "rejected_draws": sum(row.outcome == "rejected" for row in self.draws),
            "retained_sequences": len(self.retained),
            "unresolved_retained_draw_joins": sum(row.scored_design is None for row in self.retained),
        }


@dataclass(frozen=True)
class NativePublication:
    """Model-owned read result to hand to C; not an alternate persistence store."""

    arms: tuple[NativeArm, ...]


def _json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise NativeResultError(f"unsafe metadata: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise NativeResultError(f"metadata must be an object: {path}")
    return value


def _rows(path: Path, arm: str | None, stage: Literal["trajectory", "draw", "retained"]) -> tuple[NativeRow, ...]:
    if not path.exists():
        return ()
    if path.is_symlink() or not path.is_file():
        raise NativeResultError(f"unsafe native table: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "design" not in reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise NativeResultError(f"invalid native table header: {path}")
        rows = []
        names = set()
        for row in reader:
            design = row.get("design")
            if not design or None in row or design in names:
                raise NativeResultError(f"invalid/duplicate design in {path}: {design!r}")
            names.add(design)
            outcome = row.get("outcome") or None
            if stage == "draw" and outcome not in ("passed", "rejected"):
                raise NativeResultError(f"invalid scored-draw outcome in {path}: {design}")
            rank = row.get("rank") or None
            if rank is not None and (not rank.isdecimal() or int(rank) < 1):
                raise NativeResultError(f"invalid publication rank in {path}: {design}")
            target_names = row.get("targets", "") or ""
            target_weights = row.get("target_weights", "") or ""
            targets: tuple[tuple[str, float], ...] = ()
            if target_names or target_weights:
                names_list, weights_list = target_names.split(";"), target_weights.split(";")
                if len(names_list) != len(weights_list) or not all(names_list) or len(set(names_list)) != len(names_list):
                    raise NativeResultError(f"invalid target order/weights in {path}: {design}")
                try:
                    targets = tuple((name, float(weight)) for name, weight in zip(names_list, weights_list))
                except ValueError as exc:
                    raise NativeResultError(f"invalid target weight in {path}: {design}") from exc
                if any(not math.isfinite(weight) for _, weight in targets):
                    raise NativeResultError(f"invalid target weight in {path}: {design}")
            readings = {}
            if targets:
                for key, value in row.items():
                    if value is not None and ";" in value and key not in ("targets", "target_weights", "Timing", "failed_filters", "autotuned", "settings_overrides"):
                        parts = value.split(";")
                        if len(parts) == len(targets) and all(not part or _is_number(part) for part in parts):
                            readings[key] = {name: reading or None for (name, _), reading in zip(targets, parts)}
            rows.append(NativeRow(stage, arm, design, row.get("hash") or None, row, targets, readings,
                                  sequence=row.get("Binder_Sequence") or None,
                                  outcome=outcome if outcome in ("passed", "rejected") else None,
                                  failed_filters=tuple(part.strip() for part in (row.get("failed_filters") or "").split(",") if part.strip()),
                                  rank=int(rank) if rank else None,
                                  terminated=row.get("terminated") or None))
        return tuple(rows)


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE = re.compile(r"[1-9][0-9]*\Z")


def _attempts(folder: Path) -> dict[str, NativeAttempt]:
    directory = folder / "1_Trajectories" / "!_BMS_Attempts"
    if not directory.exists():
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise NativeResultError(f"unsafe attempt directory: {directory}")
    attempts = {}
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise NativeResultError(f"unsafe attempt sidecar: {path}")
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (ValueError, UnicodeError, TypeError) as exc:
            raise NativeResultError(f"invalid attempt sidecar: {path}") from exc
        if raw != canonical + b"\n" or not isinstance(payload, dict) or set(payload) != {"schema_version", "design", "trajectory", "recipe_hash", "effective_settings", "drawn"}:
            raise NativeResultError(f"noncanonical attempt sidecar: {path}")
        design = payload["design"]
        trajectory = payload["trajectory"]
        if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(design) is not str or design != path.stem
                or type(trajectory) is not int or trajectory < 1
                or not isinstance(payload["recipe_hash"], str) or not payload["recipe_hash"]
                or not isinstance(payload["effective_settings"], dict) or not isinstance(payload["drawn"], dict)):
            raise NativeResultError(f"invalid attempt identity/settings: {path}")
        attempts[design] = NativeAttempt(design, trajectory, payload["recipe_hash"],
                                         payload["effective_settings"], payload["drawn"],
                                         hashlib.sha256(canonical).hexdigest())
    return attempts


def _cif_metadata(path: Path) -> dict[str, str]:
    # Native write_structure emits a scalar _bindcraft category in the model block.
    # Only producer-stamped fields are interpreted; other mmCIF data is untouched.
    metadata = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("_bindcraft."):
            parts = shlex.split(line, comments=False)
            if len(parts) != 2 or parts[0] in metadata:
                raise NativeResultError(f"invalid BindCraft CIF metadata: {path}")
            metadata[parts[0]] = parts[1]
    return {key.removeprefix("_bindcraft."): value for key, value in metadata.items()}


def _documents(root: Path, arm_root: Path, retained: tuple[NativeRow, ...]) -> tuple[NativeDocument, ...]:
    documents = []
    retained_by_name = {row.design: row for row in retained}
    for stage in ("1_Trajectories", "2_Refolded", "3_Ranked", "trajectories", "refolded", "accepted"):
        folder = arm_root / stage
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise NativeResultError(f"unsafe native artifact directory: {folder}")
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise NativeResultError(f"unsafe native artifact: {path}")
            if path.is_file() and path.suffix.lower() in (".cif", ".mmcif", ".pdb", ".ent"):
                retained_design = attempt_sha = state = primary = variant = binder = target = None
                if stage in ("3_Ranked", "accepted", "2_Refolded") and path.suffix.lower() in (".cif", ".mmcif"):
                    stamp = _cif_metadata(path)
                    # State/chain annotations are useful even when the retained
                    # association is absent; they alone never create a Design.
                    state = stamp.get("bms_target_state") or None
                    primary = stamp.get("bms_primary_target_state") or None
                    variant = stamp.get("bms_structure_variant") or None
                    binder = stamp.get("binder_chains") or None
                    target = stamp.get("target_chains") or None
                    design = stamp.get("design")
                    if design in retained_by_name:
                        row = retained_by_name[design]
                        stamped_candidate = stamp.get("bms_scored_candidate")
                        stamped_digest = stamp.get("bms_attempt_sha256")
                        if stamped_candidate or stamped_digest:
                            if (stamped_candidate != row.values.get("bms_scored_candidate")
                                    or stamped_digest != row.values.get("bms_attempt_sha256")
                                    or (row.values.get("bms_scored_design") and stamp.get("bms_scored_design")
                                        and stamp["bms_scored_design"] != row.values["bms_scored_design"])
                                    or (row.values.get("bms_trajectory_design") and stamp.get("bms_trajectory_design")
                                        and stamp["bms_trajectory_design"] != row.values["bms_trajectory_design"])):
                                raise NativeResultError(f"contradictory retained CIF metadata: {path}")
                            if row.scored_design is not None and row.attempt_sha256 is not None:
                                retained_design, attempt_sha = design, row.attempt_sha256
                                if (state or primary or variant) and not (state and primary and variant in ("native", "relaxed")):
                                    raise NativeResultError(f"incomplete retained CIF state identity: {path}")
                                if variant == "relaxed" and stage != "2_Refolded" and "relaxed" not in path.relative_to(folder).parts[:-1]:
                                    raise NativeResultError(f"contradictory relaxed CIF location: {path}")
                                if variant == "native" and stage == "2_Refolded":
                                    raise NativeResultError(f"contradictory native CIF location: {path}")
                    elif stage in ("3_Ranked", "accepted") and retained and (stamp.get("bms_scored_candidate") or stamp.get("bms_attempt_sha256")):
                        raise NativeResultError(f"orphan producer-stamped CIF: {path}")
                documents.append(NativeDocument(str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest(),
                                                path.suffix.lower().lstrip("."), retained_design, attempt_sha,
                                                state, primary, variant, binder, target))
    return tuple(documents)

def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _arm(root: Path, folder: Path, name: str | None) -> NativeArm:
    modern = any((folder / stage).exists() for stage in ("1_Trajectories", "2_Refolded", "3_Ranked"))
    def table(stage: str, legacy: str, filename: str) -> Path:
        return folder / stage / filename if modern else folder / legacy

    trajectories = _rows(table("1_Trajectories", "trajectories.csv", "!_Trajectories.csv"), name, "trajectory")
    draws = _rows(table("2_Refolded", "candidates.csv", "!_Refolded.csv"), name, "draw")
    retained_path = table("3_Ranked", "ranked.csv", "!_Ranked.csv")
    if not modern and not retained_path.exists():
        retained_path = folder / "accepted.csv"
    retained = _rows(retained_path, name, "retained")
    trajectory_hashes: dict[str, list[str]] = {}
    for row in trajectories:
        if row.recipe_hash:
            trajectory_hashes.setdefault(row.recipe_hash, []).append(row.design)
    # Native docs explicitly designate hash as the trajectory-table join key.
    # Collisions/absent rows are unresolved, never arbitrarily selected.
    def attach(rows: tuple[NativeRow, ...]) -> tuple[NativeRow, ...]:
        return tuple(replace(row, trajectory_design=trajectory_hashes[row.recipe_hash][0])
                     if row.recipe_hash and len(trajectory_hashes.get(row.recipe_hash, ())) == 1 else row for row in rows)

    attempts = _attempts(folder)
    trajectory_by_name = {row.design: row for row in trajectories}
    qualified_trajectories = []
    for row in trajectories:
        digest = row.values.get("bms_attempt_sha256")
        attempt = attempts.get(row.design)
        if digest and (not _DIGEST.fullmatch(digest) or
                       (attempt is not None and (digest != attempt.sha256 or row.recipe_hash != attempt.recipe_hash
                                                 or row.values.get("trajectory") != str(attempt.trajectory)))):
            raise NativeResultError(f"contradictory trajectory attempt: {row.design}")
        qualified_trajectories.append(replace(row, attempt_sha256=digest) if attempt and digest else row)
    # Sidecars for interrupted claims have no trajectory row; they do not change accounting.
    trajectories = tuple(qualified_trajectories)
    trajectory_by_name = {row.design: row for row in trajectories}
    draws = attach(draws)
    qualified_draws = []
    for row in draws:
        source = trajectory_by_name.get(row.values.get("bms_trajectory_design") or "")
        digest = row.values.get("bms_attempt_sha256")
        if digest and (not _DIGEST.fullmatch(digest) or
                       (source is not None and source.attempt_sha256 is not None and digest != source.attempt_sha256)):
            raise NativeResultError(f"contradictory scored attempt: {row.design}")
        if source and source.recipe_hash == row.recipe_hash and source.attempt_sha256 == digest:
            qualified_draws.append(replace(row, trajectory_design=source.design,
                                           attempt_sha256=digest))
        else:
            qualified_draws.append(row)
    draws = tuple(qualified_draws)
    draw_by_name = {row.design: row for row in draws}
    qualified_retained = []
    used_draws = set()
    for row in attach(retained):
        candidate = row.values.get("bms_scored_candidate")
        digest = row.values.get("bms_attempt_sha256")
        if candidate or digest:
            if not candidate or not _CANDIDATE.fullmatch(candidate) or not digest or not _DIGEST.fullmatch(digest):
                raise NativeResultError(f"incomplete retained producer identity: {row.design}")
            # Only explicit native design identities may establish a join, never rank,
            # sequence, recipe hash, or _seqN position.
            trajectory = row.values.get("bms_trajectory_design")
            scored = row.values.get("bms_scored_design")
            if not trajectory or not scored:
                qualified_retained.append(row)
                continue
            if scored != f"{trajectory}_candidate{candidate}":
                raise NativeResultError(f"contradictory scored/retained producer identity: {row.design}")
            draw = draw_by_name.get(scored)
            source = trajectory_by_name.get(trajectory)
            if draw is None or source is None or not draw.trajectory_design or not draw.attempt_sha256:
                qualified_retained.append(row)
                continue
            if draw is not None and draw.attempt_sha256 != digest:
                raise NativeResultError(f"contradictory retained attempt: {row.design}")
            if (draw is None or draw.outcome != "passed" or scored in used_draws
                    or source is None or draw.trajectory_design != trajectory
                    or draw.attempt_sha256 != digest or draw.recipe_hash != row.recipe_hash
                    or source.recipe_hash != row.recipe_hash):
                raise NativeResultError(f"contradictory scored/retained join: {row.design}")
            used_draws.add(scored)
            attempt = attempts.get(trajectory)
            if attempt and source.attempt_sha256 and digest != attempt.sha256:
                raise NativeResultError(f"contradictory retained attempt: {row.design}")
            qualified_retained.append(replace(row, trajectory_design=trajectory, scored_design=scored,
                                              attempt_sha256=digest if attempt and source.attempt_sha256 else None))
        else:
            qualified_retained.append(row)
    retained = tuple(qualified_retained)
    state = _json(folder / ".campaign_state.json")
    claimed = state.get("trajectories") if state is not None else None
    if claimed is not None and (type(claimed) is not int or claimed < 0 or claimed < len(trajectories)):
        raise NativeResultError(f"invalid claimed trajectory count: {folder}")
    return NativeArm(name, claimed, trajectories, draws, retained,
                     _documents(root, folder, retained), _json(folder / "campaign_metadata.json"),
                     tuple(attempts.values()))

def read_native_publication(root: Path) -> NativePublication:
    """Read a stable/sealed native output directory; caller verifies ownership.

    Sweep arms are identified by the emitted sweep.csv `arm` column, not by
    scanning arbitrary directory names. No rows or artifact association is
    invented when an optional native table or structure was suppressed.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise NativeResultError(f"invalid native campaign root: {root}")
    sweep = root / "sweep.csv"
    if not sweep.exists():
        return NativePublication((_arm(root, root, None),))
    if sweep.is_symlink():
        raise NativeResultError(f"unsafe sweep table: {sweep}")
    with sweep.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "arm" not in reader.fieldnames:
            raise NativeResultError("sweep.csv has no arm column")
        arms = [row.get("arm") for row in reader]
    if not arms or any(not arm or arm in (".", "..") or "/" in arm or "\\" in arm for arm in arms) or len(arms) != len(set(arms)):
        raise NativeResultError("invalid sweep arm identities")
    if any((root / arm).is_symlink() or not (root / arm).is_dir() for arm in arms):
        raise NativeResultError("missing or unsafe sweep arm directory")
    return NativePublication(tuple(_arm(root, root / arm, arm) for arm in arms if arm is not None))


def native_result_page(publication: NativePublication, *, arm: str | None = None,
                       stage: Literal["trajectory", "draw", "retained", "attempt", "document"] = "trajectory",
                       offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Bounded JSON projection of the verified native reader, not a score store."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise NativeResultError("invalid native page bounds")
    if stage not in ("trajectory", "draw", "retained", "attempt", "document"):
        raise NativeResultError("invalid native stage")
    matches = [item for item in publication.arms if item.name == arm]
    if len(matches) != 1:
        raise NativeResultError("unknown native arm")
    selected = matches[0]
    rows = {"trajectory": selected.trajectories, "draw": selected.draws,
            "retained": selected.retained, "attempt": selected.attempts,
            "document": selected.documents}[stage]

    def project(item: NativeRow | NativeAttempt | NativeDocument) -> dict[str, Any]:
        if isinstance(item, NativeRow):
            return {"design": item.design, "stage": item.stage, "arm": item.arm,
                    "trajectory_design": item.trajectory_design, "scored_design": item.scored_design,
                    "attempt_sha256": item.attempt_sha256, "recipe_hash": item.recipe_hash,
                    "values": item.values, "targets": [{"name": n, "weight": w} for n, w in item.targets],
                    "target_readings": item.target_readings, "sequence": item.sequence,
                    "outcome": item.outcome, "failed_filters": list(item.failed_filters),
                    "terminated": item.terminated, "rank": item.rank}
        if isinstance(item, NativeAttempt):
            return {"design": item.design, "trajectory": item.trajectory,
                    "recipe_hash": item.recipe_hash, "effective_settings": item.effective_settings,
                    "drawn": item.drawn, "sha256": item.sha256}
        return {"path": item.path, "sha256": item.sha256, "format": item.format,
                "retained_design": item.retained_design, "attempt_sha256": item.attempt_sha256,
                "target_state": item.target_state, "primary_target_state": item.primary_target_state,
                "structure_variant": item.structure_variant, "binder_chains": item.binder_chains,
                "target_chains": item.target_chains}

    return {"schema": "bindcraft2.native-readback.v1", "arm": arm, "stage": stage,
            "offset": offset, "limit": limit, "total": len(rows),
            "accounting": selected.accounting,
            "arms": [{"name": item.name, "accounting": item.accounting} for item in publication.arms],
            "metadata": selected.metadata,
            "rows": [project(item) for item in rows[offset:offset + limit]]}
