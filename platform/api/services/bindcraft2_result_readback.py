"""Model-owned bounded BC2 readback for the authenticated jobs router.

Both local and returned remote output are reopened through the same verified
JobArtifact inventory. This module does not create Designs or trust worker paths.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from services.bindcraft2_native_results import (
    NativeResultError, TRACE_INTERPRETATION, campaign_analytics, native_loss_trace,
    native_result_page, trajectory_analytics,
)
from services.bindcraft2_publication import (
    PublicationError, _regular, read_published_native_results, native_workbench_page,
)


def _selected_arm(publication, arm):
    if arm is None and publication.arms and all(item.name is not None for item in publication.arms):
        arm = publication.arms[0].name
    matches = [item for item in publication.arms if item.name == arm]
    if len(matches) != 1:
        raise NativeResultError("unknown native arm")
    return matches[0]


def _trace(receipt, row):
    # Only an exact table identity in the already verified publication can name
    # its pinned native output family. No caller path is opened or globbed.
    if row.design in (".", "..") or any(c in row.design for c in ("/", "\\", "\0")):
        return [], ["Unsupported native trajectory design path"]
    prefix = f"{row.arm}/" if row.arm is not None else ""
    name = f"{prefix}1_Trajectories/{row.design}/{row.design}_losses.csv"
    entry = receipt["files"].get(name)
    if entry is None:
        return [], ["Trajectory trace not present in verified publication"]
    root = Path(receipt["root"]) / receipt.get("campaign_root", ".")
    _, data = _regular(root, name)
    # Reuse publication containment and digest semantics on the bytes parsed,
    # retaining authenticity even if the file changes after the inventory read.
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise PublicationError("BC2 published trace bytes changed")
    return native_loss_trace(data)


async def read_bindcraft2_result_page(
    job, session, *, arm: str | None = None,
    stage: Literal["trajectory", "draw", "retained", "attempt", "document"] = "trajectory",
    offset: int = 0, limit: int = 50,
) -> dict:
    """Caller must authorize the Job before invoking this adapter."""
    publication, receipt = await read_published_native_results(job, session)
    selected = _selected_arm(publication, arm)
    page = native_result_page(publication, arm=selected.name, stage=stage, offset=offset, limit=limit)
    if stage == "trajectory":
        analytics, warnings = {}, []
        for row in selected.trajectories:
            trace, notes = _trace(receipt, row)
            analytics[row.design] = trajectory_analytics(row, trace)
            warnings.extend(f"{row.design}: {note}" for note in notes)
            if analytics[row.design]["duration_seconds"] is None:
                warnings.append(f"{row.design}: Native design duration unavailable")
        for row in page["rows"]:
            row["analytics"] = analytics[row["design"]]
        page["analytics"] = campaign_analytics(selected.trajectories, analytics, warnings)
    return native_workbench_page(page, receipt)


async def read_bindcraft2_trajectory(
    job, session, *, design: str, arm: str | None = None, offset: int = 0, limit: int = 1000,
) -> dict:
    """Read-only detail of recorded updates, never a native filter verdict."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise NativeResultError("invalid trajectory page bounds")
    publication, receipt = await read_published_native_results(job, session)
    selected = _selected_arm(publication, arm)
    matches = [row for row in selected.trajectories if row.design == design]
    if len(matches) != 1:
        raise NativeResultError("unknown native trajectory design")
    rows, warnings = _trace(receipt, matches[0])
    return {"design": design, "arm": selected.name, "offset": offset, "limit": limit,
            "total": len(rows), "rows": rows[offset:offset + limit],
            "available": bool(rows), "warnings": [TRACE_INTERPRETATION, *warnings]}
