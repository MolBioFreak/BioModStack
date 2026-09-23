"""Selected binder inputs cannot cross a scientific lineage at launch."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routers.jobs import _validate_selected_design_owners


def job(job_id: str, parent: str | None = None):
    return SimpleNamespace(
        id=job_id,
        parent_job_id=parent,
        model_id="template_antibody_denovo",
        mode="antibody_refinement_pipeline",
        params={},
    )


def design(owner: str, root: str | None = None):
    return SimpleNamespace(job_id=owner, lineage_root_job_id=root)


@pytest.mark.asyncio
async def test_selected_designs_accept_only_real_owners_from_same_root():
    root, source, sibling = job("root"), job("source", "root"), job("sibling", "root")
    owners = {item.id: item for item in (root, source, sibling)}
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda _type, item_id: owners.get(item_id)))

    await _validate_selected_design_owners(session, source, root, [
        design("source", "root"), design("sibling", "root"), design("root", "root"),
    ])
    assert {call.args[1] for call in session.get.await_args_list} == {"root", "source", "sibling"}


@pytest.mark.asyncio
async def test_selected_designs_reject_foreign_existing_id_before_materialization():
    root, source, foreign, foreign_root = (
        job("root"), job("source", "root"), job("foreign", "other-root"), job("other-root")
    )
    owners = {item.id: item for item in (root, source, foreign, foreign_root)}
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda _type, item_id: owners.get(item_id)))
    with pytest.raises(HTTPException, match="another lineage root") as exc:
        await _validate_selected_design_owners(session, source, root, [
            design("source", "root"), design("foreign")
        ])
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,root", [("missing", "root"), ("source", "other-root")])
async def test_selected_designs_reject_missing_owners_or_conflicting_root(owner, root):
    source, lineage = job("source"), job("root")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda _type, item_id: {
        "source": source, "root": lineage
    }.get(item_id)))
    with pytest.raises(HTTPException) as exc:
        await _validate_selected_design_owners(session, source, lineage, [design(owner, root)])
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_generic_mutagenesis_does_not_use_unrelated_global_design_id():
    source, foreign = job("source"), job("other")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda _type, item_id: {
        "source": source, "other": foreign
    }.get(item_id)))
    with pytest.raises(HTTPException, match="another job") as exc:
        await _validate_selected_design_owners(session, source, None, [design("other")])
    assert exc.value.status_code == 422
