"""Advisory-only assembly repair; legacy geometry is deliberately unchanged."""
from dataclasses import asdict
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.golden_gate import resolve_golden_gate_enzyme, simulate_golden_gate
from services.assembly.ligation import simulate_ligation
from services.assembly.types import AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from services.restriction_digest import simulate_digest


def _binding():
    catalog = catalog_authority.require()
    return dict(enzyme_id="BsaI", catalog_id=catalog.catalog_id,
                expected_catalog_sha256=catalog.content_sha256)


def _fragments(junctions, circular=False):
    # Deliberately absent from the top strings: end metadata is not a new
    # sequence-membership gate (including for historical saved metadata).
    count = len(junctions) if circular else len(junctions) + 1
    return [AssemblyFragment(
        id=str(i), name=f"part {i}", sequence="CCCCCCCC",
        left_end=FragmentEnd("sticky_5", junctions[i-1] if i or circular else "ACGA"),
        right_end=FragmentEnd("sticky_5", junctions[i] if i < len(junctions) else "TCGA"),
        metadata={"legacy_saved": {"end_label": "retained", "unknown": [1, 2]}},
    ) for i in range(count)]


@pytest.mark.parametrize("circular", [False, True])
@pytest.mark.parametrize("junctions,duplicates,palindromes", [
    (["AATG", "AGAC"], 0, 0),
    (["AATG", "AATG"], 1, 0),
    (["AATG", "CATT"], 1, 0),
    (["ATAT", "AGAC"], 0, 1),
    (["ATAT", "ATAT"], 1, 1),
])
def test_warnings_do_not_block_or_change_product(junctions, duplicates, palindromes, circular):
    fragments = _fragments(junctions, circular)
    before = [asdict(fragment) for fragment in fragments]
    reference = simulate_ligation(fragments, circular=circular, mode="golden_gate")
    result = simulate_golden_gate(fragments, enzyme=resolve_golden_gate_enzyme(**_binding()), circular=circular)
    assert result.sequence == reference.sequence
    assert result.circular == reference.circular
    assert result.fragments == reference.fragments
    assert result.junctions == reference.junctions
    assert [asdict(fragment) for fragment in fragments] == before
    assert sum("is reused" in warning for warning in result.warnings) == duplicates
    assert sum("is palindromic" in warning for warning in result.warnings) == palindromes
    assert all("may occur" in warning for warning in result.warnings)
    again = simulate_golden_gate(fragments, enzyme=resolve_golden_gate_enzyme(**_binding()), circular=circular)
    assert again.warnings == result.warnings


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(molbio_ops.router)
    # No lifespan or database access: simulation routes are pure computation.
    with TestClient(app) as result:
        yield result


def test_public_golden_gate_keeps_legacy_metadata_and_no_sequence_gate(client):
    fragments = _fragments(["ATAT", "ATAT"])
    response = client.post("/api/molbio/assembly/golden-gate/simulate", json={
        "fragments": [asdict(fragment) for fragment in fragments],
        "circular": False, **_binding(),
    })
    assert response.status_code == 200, response.text
    product = response.json()["product"]
    assert product["sequence"] == "CCCCCCCC" * 3
    assert len(product["warnings"]) == 2
    assert [fragment["metadata"] for fragment in product["fragments"]] == [fragment.metadata for fragment in fragments]


def test_real_bsai_digest_physical_ends_religate_through_public_adapter(client):
    sequence = "AAAAAGGTCTCAAATGCCCCCCCC"
    catalog = catalog_authority.require()
    receipt = {key: value for key, value in catalog_authority.readiness().items()
               if key not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    digest = simulate_digest(
        sequence=sequence, topology="linear", catalog=catalog,
        records=(catalog.by_id["BsaI"],), selected_enzyme_ids=("BsaI",),
        source_receipt=dict(kind="inline_dna", name="fixture", sequence_id=None,
                            revision_id=None, revision_number=None,
                            content_sha256=hashlib.sha256(sequence.encode()).hexdigest(),
                            content_length=len(sequence), topology="linear"),
        catalog_receipt=receipt,
    )
    left, right = digest.fragments
    assert (left.right_end.overhang_sequence_5to3, left.right_end.protruding_strand) == ("CATT", "bottom")
    assert (right.left_end.overhang_sequence_5to3, right.left_end.protruding_strand) == ("AATG", "top")
    # Pass real digest top strings and protruding strands unchanged into the
    # existing public schema-to-AssemblyFragment adapter, not hand-picked ends.
    fragments = []
    for fragment in digest.fragments:
        fragments.append(dict(id=str(fragment.fragment_index), name="digest part",
                              sequence=fragment.top_strand_sequence,
                              left_end=None if fragment.left_end.kind == "natural" else dict(type="sticky_5", overhang=fragment.left_end.overhang_sequence_5to3),
                              right_end=None if fragment.right_end.kind == "natural" else dict(type="sticky_5", overhang=fragment.right_end.overhang_sequence_5to3)))
    response = client.post("/api/molbio/assembly/ligation/simulate", json={"fragments": fragments, "circular": False})
    assert response.status_code == 200, response.text
    assert response.json()["product"]["sequence"] == sequence
    assert response.json()["product"]["junctions"][0]["overhang_sequence"] == "CATT"


def test_retired_refusal_routes_and_private_models_only(client):
    schema = client.get("/openapi.json").json()
    for route in ("ligate", "gibson"):
        assert f"/api/molbio/{route}" not in schema["paths"]
        assert client.post(f"/api/molbio/{route}", json={"fragments": ["ACGT"]}).status_code == 404
    for name in ("LigationRequest", "GibsonRequest"):
        assert name not in schema["components"]["schemas"]
        assert not hasattr(molbio_ops, name)
    for mode in ("ligation", "gibson", "golden-gate"):
        for action in ("simulate", "save"):
            assert f"/api/molbio/assembly/{mode}/{action}" in schema["paths"]
