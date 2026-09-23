"""Model-owned bounded BC2 readback for the authenticated jobs router.

Both local and returned remote output are reopened through the same verified
JobArtifact inventory. This module does not create Designs or trust worker paths.
"""
from __future__ import annotations

from typing import Literal

from services.bindcraft2_native_results import native_result_page
from services.bindcraft2_publication import read_published_native_results


async def read_bindcraft2_result_page(
    job, session, *, arm: str | None = None,
    stage: Literal["trajectory", "draw", "retained", "attempt", "document"] = "trajectory",
    offset: int = 0, limit: int = 50,
) -> dict:
    """Caller must authorize the Job before invoking this adapter."""
    publication, _receipt = await read_published_native_results(job, session)
    # A first-page request has no arm selection yet. Use the first native arm
    # only for display; the returned page states its actual arm identity.
    if arm is None and publication.arms and all(item.name is not None for item in publication.arms):
        arm = publication.arms[0].name
    return native_result_page(publication, arm=arm, stage=stage, offset=offset, limit=limit)
