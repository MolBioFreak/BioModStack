"""Edition integration and nonblocking optional cloning observations."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.types import AssemblyError
from services.restriction_catalog import CatalogUnavailable
from services.restriction_analysis import AnalysisLimitError, InvalidDNAError


def test_cloning_routers_registered_without_restoring_full_product():
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text())
    included = {
        ast.unparse(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router" and node.args
    }
    assert {
        "molbio_ops.router", "molbio_golden_gate_design.router",
        "molbio_gibson_suggest.router", "molbio_end_operations.router",
        "molbio_relatives.router", "molbio_branches.router",
    } <= included
    assert not {"molbio_share.router", "molecular_construction.router",
                "atomworks_studio.router", "molecular_geometry.router"} & included


@pytest.mark.parametrize("error", [AssemblyError("Unknown restriction enzyme 'missing'"),
                                   CatalogUnavailable(), AnalysisLimitError("scan work exceeds analysis limit"),
                                   InvalidDNAError("analysis input cannot be checked")])
def test_optional_junction_check_is_evidence_not_a_ligation_gate(monkeypatch, error):
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr(molbio_ops, "observe_junction_sites", unavailable)
    app = FastAPI()
    app.include_router(molbio_ops.router)
    with TestClient(app) as client:
        response = client.post("/api/molbio/assembly/ligation/simulate", json={
            "circular": False, "observe_enzymes": ["missing"],
            "fragments": [{"id": "a", "name": "a", "sequence": "ACGTACGT",
                           "left_end": {"type": "blunt"}, "right_end": {"type": "blunt"}},
                          {"id": "b", "name": "b", "sequence": "TTGGCCAA",
                           "left_end": {"type": "blunt"}, "right_end": {"type": "blunt"}}],
        })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["product"]["sequence"] == "ACGTACGTTTGGCCAA"
    assert result["junction_sites"] == [{
        "status": "unavailable", "regenerated": [], "destroyed": [],
        "note": f"Junction site check unavailable: {error}",
    }]
    # The missing observation must not invent a site coordinate or a destroyed site.
    assert "position" not in result["junction_sites"][0]
