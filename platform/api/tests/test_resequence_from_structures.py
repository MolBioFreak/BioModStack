"""POST /api/jobs/resequence/from-structures stages exact structures and launches the loop."""
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

PDB = "".join(
    f"ATOM  {i:5d}  CA  ALA A{i:4d}    {3.8 * i:8.3f}   0.000   0.000  1.00 20.00           C\n" for i in (1, 2, 3)
) + "END\n"


def test_request_requires_sources_and_chains():
    from routers.jobs import ResequenceLaunchRequest
    with pytest.raises(Exception):
        ResequenceLaunchRequest(sources=[], design_chains="A")
    with pytest.raises(Exception):
        ResequenceLaunchRequest(sources=[{"path": "x.pdb"}], design_chains="")
    ok = ResequenceLaunchRequest(sources=[{"path": "x.pdb"}], design_chains="A", seq_method="mpnn",
                                 fixed_positions="A1-2")
    assert ok.seq_method == "mpnn" and ok.fixed_positions == "A1-2"


def test_launch_stages_pdb_and_builds_loop_job(tmp_path, monkeypatch):
    import routers.jobs as jobs
    import routers.files as files
    import services.binder_source_materialization as bsm

    src = tmp_path / "design.cif"
    src.write_text(PDB)
    sel = tmp_path / "sel"
    sel.mkdir()
    monkeypatch.setattr(jobs, "_create_generic_selection_dir", lambda ns, action: sel)
    monkeypatch.setattr(files, "is_path_allowed", lambda p: True)

    async def fake_resolve(request, session):
        return src, {}
    monkeypatch.setattr(bsm, "resolve_structure_source", fake_resolve)
    produced = tmp_path / "out.pdb"
    produced.write_text(PDB)
    monkeypatch.setattr(bsm, "materialize_source_bytes",
                        lambda raw, suffix, dest, **kw: {"path": str(produced)})
    monkeypatch.setattr(jobs, "resolve_allowed_path", lambda value: Path(value))
    seen = {}

    async def fake_create(launch, background, session):
        seen["launch"] = launch
        return {"ok": True}
    monkeypatch.setattr(jobs, "create_job", fake_create)

    req = jobs.ResequenceLaunchRequest(sources=[{"path": "design.cif"}], design_chains="A",
                                       fixed_positions="A1-2", seq_method="mpnn")
    out = asyncio.run(jobs.launch_resequence_from_structures(req, None, None))
    launch = seen["launch"]
    assert out == {"ok": True}
    assert (launch.model_id, launch.mode) == ("protein_modification_experimental", "region_redesign")
    assert launch.params["backbone_input_pdbs"] == str(sel)
    assert launch.params["fixed_positions"] == "A1-2" and launch.params["seq_method"] == "mpnn"
    assert "designable_positions" not in launch.params
    assert [p.name for p in sel.glob("*.pdb")] == ["001_design.pdb"]


def test_staging_failure_removes_the_selection_dir(tmp_path, monkeypatch):
    import routers.jobs as jobs
    import services.binder_source_materialization as bsm
    sel = tmp_path / "sel"
    sel.mkdir()
    monkeypatch.setattr(jobs, "_create_generic_selection_dir", lambda ns, action: sel)

    async def bad(request, session):
        raise ValueError("no such structure")
    monkeypatch.setattr(bsm, "resolve_structure_source", bad)
    req = jobs.ResequenceLaunchRequest(sources=[{"path": "x.pdb"}], design_chains="A")
    with pytest.raises(HTTPException) as err:
        asyncio.run(jobs.launch_resequence_from_structures(req, None, None))
    assert err.value.status_code == 422 and not sel.exists()
