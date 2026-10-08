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
