"""Advisory FrustraMPNN on resequence-loop candidates: hydrogens dropped from a copy only."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.frustrampnn as fr

PDB = (
    "ATOM      1  N   MET A   1      -2.969  -4.375 -12.750  1.00  0.00           N  \n"
    "ATOM      2  H   MET A   1      -3.000  -4.000 -12.000  1.00  0.00           H  \n"
    "ATOM      3  CA  MET A   1      -2.000  -4.000 -12.000  1.00  0.00           C  \n"
)


def test_hydrogens_are_dropped_from_the_copy_only():
    out = fr._heavy_atoms_only(PDB.encode()).decode()
    assert out.count("ATOM") == 2 and " H  " not in out.replace("MET", "")


def _job(tmp_path):
    review = tmp_path / "validation" / "review_candidates"
    review.mkdir(parents=True)
    (review / "c0.pdb").write_text(PDB)
    return SimpleNamespace(output_dir=str(tmp_path)), review


class FakeSession:
    def __init__(self, job): self.job = job
    async def get(self, model, key): return self.job
    async def rollback(self): pass


def test_selected_candidates_are_queued_with_hydrogens_stripped(tmp_path, monkeypatch):
    job, review = _job(tmp_path)
    seen = []
    async def fake_create(session, **kw): seen.append(kw); return SimpleNamespace(id="child")
    async def fake_receipt(session, child): return {"job_id": child.id}
    monkeypatch.setattr(fr, "create_child_job", fake_create)
    monkeypatch.setattr(fr, "_child_job_receipt", fake_receipt)
    body = fr.AnalyzeLoopCandidatesRequest(names=["c0.pdb"])
    result = asyncio.run(fr.analyze_loop_candidates("j", body, FakeSession(job)))
    assert result["analyses"][0]["candidate"] == "c0.pdb"
    assert seen[0]["trigger"] == "resequence_loop_candidate"
    assert b" H  " not in seen[0]["selections"][0].source_bytes.replace(b"MET", b"")
    assert (review / "c0.pdb").read_text() == PDB  # stored design untouched


def test_unknown_or_escaping_names_are_refused(tmp_path, monkeypatch):
    job, _ = _job(tmp_path)
    (tmp_path / "secret.pdb").write_text(PDB)
    for name in ("missing.pdb", "../../secret.pdb"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fr.analyze_loop_candidates("j", fr.AnalyzeLoopCandidatesRequest(names=[name]), FakeSession(job)))
        assert exc.value.status_code == 422


def test_remote_gate_candidates_come_from_the_fetched_review_set(tmp_path, monkeypatch):
    """A remote loop job has no validation/review_candidates; its gate payload names the fetched set."""
    fetched = tmp_path / ".bms-review-import" / "abc" / ".bms-review" / "def" / "candidate"
    fetched.mkdir(parents=True)
    (fetched / "c0.pdb").write_text(PDB)
    job = SimpleNamespace(output_dir=str(tmp_path), awaiting_payload={"candidate_dir": str(fetched)})
    seen = []
    async def fake_create(session, **kw): seen.append(kw); return SimpleNamespace(id="child")
    async def fake_receipt(session, child): return {"job_id": child.id}
    monkeypatch.setattr(fr, "create_child_job", fake_create)
    monkeypatch.setattr(fr, "_child_job_receipt", fake_receipt)
    result = asyncio.run(fr.analyze_loop_candidates("j", fr.AnalyzeLoopCandidatesRequest(names=["c0.pdb"]), FakeSession(job)))
    assert result["analyses"][0]["candidate"] == "c0.pdb"
    assert b" H  " not in seen[0]["selections"][0].source_bytes.replace(b"MET", b"")
