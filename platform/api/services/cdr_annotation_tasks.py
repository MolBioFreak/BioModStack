from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Dict, List, Optional

from sqlalchemy import bindparam, update
from sqlalchemy import select

from database import async_session, Design
from services.cdr_annotator import batch_annotate_pdbs

logger = logging.getLogger(__name__)


def _preferred_chain_map(raw_chain_ids: Optional[str]) -> Dict[str, str]:
    chain_ids = [chain.strip() for chain in str(raw_chain_ids or "").split(",") if chain and chain.strip()]
    if not chain_ids:
        return {}

    preferred: Dict[str, str] = {}
    remaining: List[str] = []
    for chain_id in chain_ids:
        chain_upper = chain_id.upper()
        if chain_upper == "H" and "H" not in preferred:
            preferred["H"] = chain_id
        elif chain_upper in {"L", "K"} and "L" not in preferred:
            preferred["L"] = chain_id
        else:
            remaining.append(chain_id)

    if "H" not in preferred and remaining:
        preferred["H"] = remaining.pop(0)
    if "L" not in preferred and remaining:
        preferred["L"] = remaining.pop(0)
    return preferred


def _build_updates(path_to_design_ids: Dict[str, List[str]], annotations) -> List[dict]:
    updates: List[dict] = []
    for pdb_path, design_ids in path_to_design_ids.items():
        annot = annotations.get(pdb_path)
        if not annot:
            continue
        for design_id in design_ids:
            updates.append({
                "design_id": design_id,
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

    path_to_design_ids: Dict[str, List[str]] = OrderedDict()
    for pdb_path, design_id in zip(pdb_paths, design_ids):
        if not pdb_path:
            continue
        path_to_design_ids.setdefault(pdb_path, []).append(design_id)

    design_preferred_chains: Dict[str, Dict[str, str]] = {}
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Design.id, Design.pdb_path, Design.detected_antibody_chains).where(
                    Design.id.in_(design_ids)
                )
            )
        ).all()
        for design_id, pdb_path, detected_antibody_chains in rows:
            preferred = _preferred_chain_map(detected_antibody_chains)
            if preferred and pdb_path and pdb_path not in design_preferred_chains:
                design_preferred_chains[str(pdb_path)] = preferred

    unique_pdb_paths = list(path_to_design_ids.keys())
    annotations = await asyncio.to_thread(
        batch_annotate_pdbs,
        unique_pdb_paths,
        batch_size=500,
        preferred_chains_by_path=design_preferred_chains,
    )
    updates = _build_updates(path_to_design_ids, annotations)

    if not updates:
        logger.info("[CDR ANNOTATE] No annotations to apply%s", f" (job {job_id})" if job_id else "")
        return 0

    stmt = (
        update(Design.__table__)
        .where(Design.__table__.c.id == bindparam("design_id"))
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
        "[CDR ANNOTATE] Applied %s/%s annotations from %s unique structures%s",
        len(updates),
        len(pdb_paths),
        len(unique_pdb_paths),
        f" (job {job_id})" if job_id else "",
    )
    return len(updates)
