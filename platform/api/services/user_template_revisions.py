"""Revision support for the existing UserTemplate owner, not another CRUD store."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database import UserTemplateRevision

REVISIONED_SCHEMAS = {
    "bms.bioxp-method.v1", "bms.bioxp-liquid-class.v1", "bms.bioxp-method-preset.v1",
}


def revisioned(template):
    return template.mode == "bioxp_workflow" and template.params.get("schema") in REVISIONED_SCHEMAS


def snapshot(template):
    return deepcopy({"id": template.id, "name": template.name,
                     "description": template.description, "method": template.params})


async def current_revision(session, template_id):
    return await session.scalar(select(func.max(UserTemplateRevision.revision)).where(
        UserTemplateRevision.template_id == template_id)) or 0


async def append_revision(session, template, revision):
    session.add(UserTemplateRevision(template_id=template.id, revision=revision,
                                    snapshot=snapshot(template)))
    try:
        # Flush snapshot before mutable head; the unique key chooses one writer.
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, {"code": "revision_conflict", "category": "persistence",
                                  "message": "The base revision changed; retain your draft and read the current revision",
                                  "path": "/expected_base_revision"}) from None


async def exact_revision(session, template, revision=None):
    current = await current_revision(session, template.id)
    wanted = current if revision is None else revision
    # Legacy rows are revision zero until the first explicit revisioned update.
    if wanted == 0 and current == 0:
        return {**snapshot(template), "revision": 0}
    row = await session.get(UserTemplateRevision, (template.id, wanted))
    if row is None:
        raise HTTPException(404, "Template revision not found")
    return {**deepcopy(row.snapshot), "revision": wanted}
