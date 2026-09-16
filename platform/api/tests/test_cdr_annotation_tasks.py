from __future__ import annotations

import asyncio
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.cdr_annotation_tasks import _preferred_chain_map, annotate_and_update_designs
from services.cdr_annotator import identify_binder_chains


def test_identify_binder_chains_detects_qlqlv_nanobody_in_mixed_complex() -> None:
    sequences = {
        "A": "GGGGTNSGAGKKRFEVKKSNASAQSAWDIVVDNCAICRNHIMDLCIECQANQASATSEECTVAWGVCNHAFHFHCISRWLKTRQVCPLDNREWEFQKYGH",
        "E": "QLQLVESGGGLVQAGGSLRLSGAASGLTDTSTDTYYAYGWFRQAPGKEREFVAAIGSNGGSSQRYADSVKGRFTISRDKSKNTVYLQMNSLKAEDTAVYYGAAGRVNIDLTWASYDYWGQGTQVTVSS",
    }

    assert identify_binder_chains(sequences, "2lgv_complex.pdb") == {"H": "E"}


def test_preferred_chain_map_promotes_single_chain_to_heavy() -> None:
    assert _preferred_chain_map("E") == {"H": "E"}
    assert _preferred_chain_map("E,F") == {"H": "E", "L": "F"}
    assert _preferred_chain_map("H,L") == {"H": "H", "L": "L"}


def test_annotate_and_update_designs_passes_detected_antibody_chain_hints(monkeypatch) -> None:
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult(
                [("design-1", "/tmp/RCSB_2LGV_model_0.pdb", "E")]
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def _fake_async_session():
        return _FakeSession()

    async def _run() -> None:
        captured: dict[str, object] = {}

        def _fake_batch(pdb_paths, batch_size=500, preferred_chains_by_path=None):
            captured["pdb_paths"] = list(pdb_paths)
            captured["batch_size"] = batch_size
            captured["preferred_chains_by_path"] = preferred_chains_by_path or {}
            return {}

        monkeypatch.setattr("services.cdr_annotation_tasks.async_session", _fake_async_session)
        monkeypatch.setattr("services.cdr_annotation_tasks.batch_annotate_pdbs", _fake_batch)

        updated = await annotate_and_update_designs(
            pdb_paths=["/tmp/RCSB_2LGV_model_0.pdb"],
            design_ids=["design-1"],
            job_id="job-1",
        )

        assert updated == 0
        assert captured["pdb_paths"] == ["/tmp/RCSB_2LGV_model_0.pdb"]
        assert captured["batch_size"] == 500
        assert captured["preferred_chains_by_path"] == {
            "/tmp/RCSB_2LGV_model_0.pdb": {"H": "E"}
        }

    asyncio.run(_run())


def test_native_cdr_annotation_retains_source_and_chain_hints(tmp_path, monkeypatch):
    import hashlib
    import json
    scripts = API_ROOT.parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    from trigger_anarcii_annotation import annotate_candidates
    from services.cdr_annotator import CDRAnnotation
    pdb = tmp_path / "candidate.pdb"
    pdb.write_text("MODEL\nENDMDL\n")
    seen = {}

    def native(paths, batch_size, preferred_chains_by_path):
        seen.update(paths=paths, batch_size=batch_size, chains=preferred_chains_by_path)
        return {str(pdb): CDRAnnotation(antibody_type="vhh", binder_length=120)}

    monkeypatch.setattr("services.cdr_annotator.batch_annotate_pdbs", native)
    output = tmp_path / "annotations.json"
    hints = {str(pdb): {"H": "E"}}
    result = annotate_candidates([tmp_path], output, job_id="root", batch_size=23,
                                 preferred_chains_by_path=hints)
    assert seen == {"paths": [str(pdb)], "batch_size": 23, "chains": hints}
    assert result["annotations"][0]["source_sha256"] == hashlib.sha256(pdb.read_bytes()).hexdigest()
    assert json.loads(output.read_text())["status"] == "complete"


def test_native_cdr_annotation_cannot_silently_skip_missing(tmp_path, monkeypatch):
    import pytest
    import json
    monkeypatch.syspath_prepend(str(API_ROOT.parents[1] / "scripts"))
    from trigger_anarcii_annotation import annotate_candidates
    (tmp_path / "candidate.pdb").write_text("MODEL\nENDMDL\n")
    monkeypatch.setattr("services.cdr_annotator.batch_annotate_pdbs", lambda *a, **kw: {})
    output = tmp_path / "annotations.json"
    with pytest.raises(RuntimeError, match="missing"):
        annotate_candidates([tmp_path], output, job_id="root")
    assert json.loads(output.read_text())["status"] == "incomplete"
