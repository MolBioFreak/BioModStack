from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from sqlalchemy import bindparam, update

from database import async_session, Design
from services.cdr_annotator import batch_annotate_pdbs

logger = logging.getLogger(__name__)


def _build_updates(pdb_paths: List[str], design_ids: List[str], annotations) -> List[dict]:
    updates: List[dict] = []
    for pdb_path, design_id in zip(pdb_paths, design_ids):
        annot = annotations.get(pdb_path)
        if not annot:
            continue
        updates.append({
            "id": design_id,
            "antibody_type": annot.antibody_type,
            "binder_length": annot.binder_length,
            "cdr_h1": annot.cdr_h1,
            "cdr_h2": annot.cdr_h2,
            "cdr_h3": annot.cdr_h3,
            "cdr_l1": annot.cdr_l1,
            "cdr_l2": annot.cdr_l2,
            "cdr_l3": annot.cdr_l3,
            "cdr_h1_length": annot.cdr_h1_length,
            "cdr_h2_length": annot.cdr_h2_length,
            "cdr_h3_length": annot.cdr_h3_length,
            "cdr_l1_length": annot.cdr_l1_length,
            "cdr_l2_length": annot.cdr_l2_length,
            "cdr_l3_length": annot.cdr_l3_length,
            "fr2_contacts": annot.fr2_contacts,
            "de_loop": annot.de_loop,
            "fr3_contacts": annot.fr3_contacts,
            "fr4_contacts": annot.fr4_contacts,
        })
    return updates


async def annotate_and_update_designs(
    pdb_paths: List[str],
    design_ids: List[str],
    job_id: Optional[str] = None,
) -> int:
    if not pdb_paths:
        return 0

    annotations = await asyncio.to_thread(batch_annotate_pdbs, pdb_paths, batch_size=500)
    updates = _build_updates(pdb_paths, design_ids, annotations)

    if not updates:
        logger.info("[CDR ANNOTATE] No annotations to apply%s", f" (job {job_id})" if job_id else "")
        return 0

    stmt = (
        update(Design)
        .where(Design.id == bindparam("id"))
        .values(
            antibody_type=bindparam("antibody_type"),
            binder_length=bindparam("binder_length"),
            cdr_h1=bindparam("cdr_h1"),
            cdr_h2=bindparam("cdr_h2"),
            cdr_h3=bindparam("cdr_h3"),
            cdr_l1=bindparam("cdr_l1"),
            cdr_l2=bindparam("cdr_l2"),
            cdr_l3=bindparam("cdr_l3"),
            cdr_h1_length=bindparam("cdr_h1_length"),
            cdr_h2_length=bindparam("cdr_h2_length"),
            cdr_h3_length=bindparam("cdr_h3_length"),
            cdr_l1_length=bindparam("cdr_l1_length"),
            cdr_l2_length=bindparam("cdr_l2_length"),
            cdr_l3_length=bindparam("cdr_l3_length"),
            fr2_contacts=bindparam("fr2_contacts"),
            de_loop=bindparam("de_loop"),
            fr3_contacts=bindparam("fr3_contacts"),
            fr4_contacts=bindparam("fr4_contacts"),
        )
    )

    async with async_session() as session:
        await session.execute(stmt, updates)
        await session.commit()

    logger.info(
        "[CDR ANNOTATE] Applied %s/%s annotations%s",
        len(updates),
        len(pdb_paths),
        f" (job {job_id})" if job_id else "",
    )
    return len(updates)
