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


@dataclass(frozen=True)
class NativeDocument:
    path: str  # relative to the sealed publication root; NOT a subject join
    sha256: str
    format: str


@dataclass(frozen=True)
class NativeArm:
    name: str | None
    claimed_attempts: int | None
    trajectories: tuple[NativeRow, ...]
    draws: tuple[NativeRow, ...]
    retained: tuple[NativeRow, ...]
    documents: tuple[NativeDocument, ...]
    metadata: dict[str, Any] | None
    # The producer has not supplied per-attempt settings or candidate->seq joins.
    # Do not synthesize either from recipe hashes, labels, sequence or rank.

    @property
    def accounting(self) -> dict[str, int | None]:
        return {
            "claimed_attempts": self.claimed_attempts,
            "emitted_trajectories": len(self.trajectories),
            "scored_draws": len(self.draws),
            "passing_draws": sum(row.outcome == "passed" for row in self.draws),
            "rejected_draws": sum(row.outcome == "rejected" for row in self.draws),
            "retained_sequences": len(self.retained),
            "unresolved_retained_draw_joins": len(self.retained),
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


def _documents(root: Path, arm_root: Path) -> tuple[NativeDocument, ...]:
    documents = []
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
                documents.append(NativeDocument(str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest(), path.suffix.lower().lstrip(".")))
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
    retained = _rows(table("3_Ranked", "ranked.csv", "!_Ranked.csv"), name, "retained")
    trajectory_hashes: dict[str, list[str]] = {}
    for row in trajectories:
        if row.recipe_hash:
            trajectory_hashes.setdefault(row.recipe_hash, []).append(row.design)
    # Native docs explicitly designate hash as the trajectory-table join key.
    # Collisions/absent rows are unresolved, never arbitrarily selected.
    def attach(rows: tuple[NativeRow, ...]) -> tuple[NativeRow, ...]:
        return tuple(replace(row, trajectory_design=trajectory_hashes[row.recipe_hash][0])
                     if row.recipe_hash and len(trajectory_hashes.get(row.recipe_hash, ())) == 1 else row for row in rows)

    state = _json(folder / ".campaign_state.json")
    claimed = state.get("trajectories") if state is not None else None
    if claimed is not None and (type(claimed) is not int or claimed < 0 or claimed < len(trajectories)):
        raise NativeResultError(f"invalid claimed trajectory count: {folder}")
    return NativeArm(name, claimed, trajectories, attach(draws), attach(retained), _documents(root, folder), _json(folder / "campaign_metadata.json"))


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
