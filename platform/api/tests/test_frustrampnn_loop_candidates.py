"""Advisory FrustraMPNN on resequence-loop candidates: hydrogens dropped from a copy only."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

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


# Real creation, remote review fallback, and lineage are exercised through
# HTTP/private SQLite in test_denovo_validator_closeout.py (no creator stub).


def test_unknown_or_escaping_names_are_refused(tmp_path, monkeypatch):
    job, _ = _job(tmp_path)
    (tmp_path / "secret.pdb").write_text(PDB)
    for name in ("missing.pdb", "../../secret.pdb"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fr.analyze_loop_candidates("j", fr.AnalyzeLoopCandidatesRequest(names=[name]), BackgroundTasks(), FakeSession(job)))
        assert exc.value.status_code == 422
