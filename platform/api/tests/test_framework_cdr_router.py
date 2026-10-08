from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import frameworks as frameworks_router
from services.cdr_annotator import CDRAnnotation


def test_preferred_framework_chains_picks_first_unique_chain_tokens() -> None:
    assert frameworks_router._preferred_framework_chains(
        SimpleNamespace(h_chain="A, B", l_chain="L|M")
    ) == {"H": "A", "L": "L"}

    assert frameworks_router._preferred_framework_chains(
        SimpleNamespace(h_chain="A", l_chain="A;B")
    ) == {"H": "A"}


def test_annotate_framework_cdrs_uses_vhh_heavy_chain_metadata_without_light_chain(monkeypatch, tmp_path: Path) -> None:
    cache_file = tmp_path / "5e7b_imgt.pdb"
    cache_file.write_text("stub")

    captured: dict[str, object] = {}

    class _FakeDb:
        def get_by_pdb(self, pdb_code: str):
            assert pdb_code == "5E7B"
            return [SimpleNamespace(h_chain="A")]

    def _fake_annotate_pdb(pdb_path: str, preferred_chains=None):
        captured["pdb_path"] = pdb_path
        captured["preferred_chains"] = preferred_chains
        return CDRAnnotation(
            antibody_type="vhh",
            binder_length=126,
            cdr_h1="GFTFDDSD",
            cdr_h1_range=(27, 38),
            cdr_h1_seq_range=(25, 32),
        )

    monkeypatch.setattr(frameworks_router, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(frameworks_router, "get_sabdab_db", lambda: _FakeDb())
    monkeypatch.setattr("services.cdr_annotator.annotate_pdb", _fake_annotate_pdb)

    response = asyncio.run(frameworks_router.annotate_framework_cdrs("5E7B", scheme="imgt"))

    assert captured["pdb_path"] == str(cache_file)
    assert captured["preferred_chains"] == {"H": "A"}
    assert response.antibody_type == "vhh"
    assert response.cdr_h1 == "GFTFDDSD"
    assert response.cdr_h1_range == [27, 38]
    assert response.cdr_h1_seq_range == [25, 32]
