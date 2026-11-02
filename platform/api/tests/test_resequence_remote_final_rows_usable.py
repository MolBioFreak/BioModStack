"""Final rows of a remote Resequence continuation live in the reviewed selection folder, above child_output_dir."""
import uuid

import pytest

from database import Design, Job
from services.result_state_integrity import _existing_designs_are_usable
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)

PDB = "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n"


async def _usable(store, tmp_path, *, final_dir_param, pdb_location):
    root = tmp_path / "result"
    selection = root / ".bms-review" / "selections" / "abc"
    continuation = selection / "continuation"
    continuation.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    where = {"selection": selection, "continuation": continuation, "outside": outside}[pdb_location]
    pdb = where / "final_0.pdb"
    pdb.write_text(PDB)
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir, job.child_output_dir = str(root), str(continuation)
        job.params = {"plr_final_candidate_dir": str(final_dir_param(selection, outside))} if final_dir_param else {}
        session.add(Design(id=str(uuid.uuid4()), job_id="job", name="final_0", pdb_path=str(pdb)))
        await session.commit()
        return await _existing_designs_are_usable(session, "job", str(root))


@pytest.mark.asyncio
async def test_finals_in_the_reviewed_selection_are_usable(store, tmp_path):
    assert await _usable(store, tmp_path, final_dir_param=lambda s, o: s, pdb_location="selection") is True


@pytest.mark.asyncio
async def test_finals_in_the_continuation_folder_stay_usable(store, tmp_path):
    assert await _usable(store, tmp_path, final_dir_param=None, pdb_location="continuation") is True


@pytest.mark.asyncio
async def test_a_file_above_the_continuation_without_a_named_final_dir_is_still_rejected(store, tmp_path):
    assert await _usable(store, tmp_path, final_dir_param=None, pdb_location="selection") is False


@pytest.mark.asyncio
async def test_a_final_dir_outside_the_result_root_is_not_trusted(store, tmp_path):
    assert await _usable(store, tmp_path, final_dir_param=lambda s, o: o, pdb_location="outside") is False
