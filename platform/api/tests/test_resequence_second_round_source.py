"""A final design of a remote Resequence continuation can be the source of the next round.

The continuation publishes its finals in the reviewed selection folder, which sits above the continuation folder that
child_output_dir names. The shared source resolver measured every design against child_output_dir alone and refused
them ("... is not in the subpath of ...continuation"), so a second round could not start from a first round's result.
"""
import uuid

import pytest

from database import Design, Job
from services.binder_source_materialization import StructureSourceRequest, resolve_structure_source
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)

PDB = "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n"


async def _resolve(store, tmp_path, monkeypatch, *, final_dir_param, pdb_location):
    results = tmp_path / "results"
    root = results / "round1"
    selection = root / ".bms-review" / "selections" / "abc"
    continuation = selection / "continuation"
    continuation.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    where = {"selection": selection, "continuation": continuation, "outside": outside}[pdb_location]
    pdb = where / "final_0.pdb"
    pdb.write_text(PDB)
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results))
    design_id = str(uuid.uuid4())
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir, job.child_output_dir = str(root), str(continuation)
        job.params = {"plr_final_candidate_dir": str(final_dir_param(selection, outside))} if final_dir_param else {}
        session.add(Design(id=design_id, job_id="job", name="final_0", pdb_path=str(pdb)))
        await session.commit()
        path, _identity = await resolve_structure_source(StructureSourceRequest(design_id=design_id, job_id="job"), session)
        return path


@pytest.mark.asyncio
async def test_a_final_in_the_reviewed_selection_can_start_the_next_round(store, tmp_path, monkeypatch):
    path = await _resolve(store, tmp_path, monkeypatch, final_dir_param=lambda s, o: s, pdb_location="selection")
    assert path.name == "final_0.pdb"


@pytest.mark.asyncio
async def test_a_final_in_the_continuation_folder_still_resolves(store, tmp_path, monkeypatch):
    path = await _resolve(store, tmp_path, monkeypatch, final_dir_param=None, pdb_location="continuation")
    assert path.name == "final_0.pdb"


@pytest.mark.asyncio
async def test_a_file_above_the_continuation_with_no_named_final_folder_is_still_refused(store, tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        await _resolve(store, tmp_path, monkeypatch, final_dir_param=None, pdb_location="selection")


@pytest.mark.asyncio
async def test_a_named_final_folder_outside_the_result_root_is_still_refused(store, tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        await _resolve(store, tmp_path, monkeypatch, final_dir_param=lambda s, o: o, pdb_location="outside")
