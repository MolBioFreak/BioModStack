"""Gibson/HiFi per-junction overlaps (bottom-up building) through the engine and the real routes."""
from __future__ import annotations

import asyncio
import hashlib
import random
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import (  # noqa: E402
    create_molbio_engine,
    get_molbio_session,
    init_molbio_db,
    make_molbio_session_factory,
)
from routers.molbio_ops import (  # noqa: E402
    AssemblyJunctionResponse,
    GibsonDesignRequest,
    _assembly_request_fingerprint,
    router as molbio_router,
)
from routers.nucleotide_sequences import router as sequence_router  # noqa: E402
from services.assembly.pydna_gibson import design_gibson  # noqa: E402
from services.assembly.types import AssemblyError, AssemblyFragment, JunctionSpec  # noqa: E402


def dna(seed: int, length: int = 180) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def frag(index: int, length: int = 180) -> AssemblyFragment:
    return AssemblyFragment(id=f"fragment-{index}", name=f"Fragment {index}", sequence=dna(index, length))


def request_fragment(index: int, preparation: str = "pcr", sequence: str | None = None, name: str | None = None) -> dict:
    sequence = sequence if sequence is not None else dna(index)
    return {
        "id": f"fragment-{index}", "name": name or f"Fragment {index}", "sequence": sequence,
        "preparation": preparation, "source_sequence_id": None, "source_name": None,
        "source_revision": None, "source_start": 0, "source_end": len(sequence),
    }


def primer(result, fragment_id: str, direction: str):
    return next(p for p in result.primers if p.id == f"{fragment_id}:{direction}")


# --- 1. requests without junctions keep today's exact output -----------------------------------

def test_without_junctions_designs_are_unchanged() -> None:
    three = design_gibson([frag(1), frag(2), frag(3)], preparations=["pcr"] * 3, circular=True, overlap=24)
    assert three.selected_candidate_checksum == "6e212ad1d34988e06fe833eae33715b94b9ff9a0ac1c8e24abb76c96c5177e07"
    assert hashlib.sha256("|".join(p.full_sequence for p in three.primers).encode()).hexdigest()[:20] == "e9dc364edb60eb46bec8"
    assert [j.overlap_length for j in three.candidates[0].product.junctions] == [24, 24, 24]

    two = design_gibson([frag(1), frag(2)], preparations=["pcr", "ready_linear"], circular=False, overlap=24)
    assert two.selected_candidate_checksum == "601cbb7c34a73fe318d68a399f126fce00ba3c2c43492227913ec0b518d59f0e"
    assert hashlib.sha256("|".join(p.full_sequence for p in two.primers).encode()).hexdigest()[:20] == "22b6205c9d16f2d59791"

    linker = AssemblyFragment(id="linker", name="linker", sequence=dna(9, 30))
    mixed = design_gibson([frag(1), linker, frag(2)], preparations=["pcr", "ready_linear", "pcr"], circular=False, overlap=30)
    assert mixed.selected_candidate_checksum == "560c79fe992e1ac441372e36ee910fcb784a95793fe2d46f4f7c3958081c9006"
    assert hashlib.sha256("|".join(p.full_sequence for p in mixed.primers).encode()).hexdigest()[:20] == "b377c322875cea714b4b"


def test_request_fingerprint_is_unchanged_when_junctions_are_absent() -> None:
    linear = {"fragments": [request_fragment(1), request_fragment(2)], "circular": False, "overlap": 24}
    circular = {"fragments": [request_fragment(1), request_fragment(2), request_fragment(3)], "circular": True, "overlap": 24}
    assert _assembly_request_fingerprint(GibsonDesignRequest(**linear)) == (
        "91304ddeb79542bd27eeddb9d7387e4271777579c844f639ecdd5f2fa008e4be")
    assert _assembly_request_fingerprint(GibsonDesignRequest(**circular)) == (
        "e4f08ff08a9aff2d76ba236fdf65bc70b353c109fd4d16aea1a116e581730755")
    assert _assembly_request_fingerprint(GibsonDesignRequest(**linear, junctions=[])) == (
        "91304ddeb79542bd27eeddb9d7387e4271777579c844f639ecdd5f2fa008e4be")
    with_junction = GibsonDesignRequest(**linear, junctions=[{"after_fragment_id": "fragment-1", "overlap": 30}])
    assert _assembly_request_fingerprint(with_junction) != (
        "91304ddeb79542bd27eeddb9d7387e4271777579c844f639ecdd5f2fa008e4be")


# --- 2. per-junction overlap length ---------------------------------------------------------------

def test_each_junction_gets_its_own_overlap_length() -> None:
    result = design_gibson(
        [frag(1), frag(2), frag(3)], preparations=["pcr"] * 3, circular=True, overlap=24,
        junctions=[JunctionSpec("fragment-1", 40), JunctionSpec("fragment-2", 20)],
    )
    product = result.candidates[0].product
    assert [j.overlap_length for j in product.junctions] == [40, 20, 24]
    designed = {f.id: f.sequence for f in result.designed_fragments}
    first, second = product.junctions[0], product.junctions[1]
    assert designed["fragment-1"].endswith(first.overlap_sequence) and designed["fragment-2"].startswith(first.overlap_sequence)
    assert designed["fragment-2"].endswith(second.overlap_sequence) and designed["fragment-3"].startswith(second.overlap_sequence)
    assert product.sequence == "".join(dna(i) for i in (1, 2, 3))
    # the user's explicit overlaps are not reported as "below preferred" against the request-level value
    assert not any("below preferred" in w for w in result.warnings)


def test_overlap_null_falls_back_to_the_request_level_overlap() -> None:
    result = design_gibson(
        [frag(1), frag(2)], preparations=["pcr", "pcr"], circular=False, overlap=26,
        junctions=[JunctionSpec("fragment-1", None, None)],
    )
    assert [j.overlap_length for j in result.candidates[0].product.junctions] == [26]


def test_circular_closure_overlap_is_addressed_by_the_last_fragment() -> None:
    result = design_gibson(
        [frag(1), frag(2)], preparations=["pcr", "pcr"], circular=True, overlap=24,
        junctions=[JunctionSpec("fragment-2", 36)],
    )
    assert [j.overlap_length for j in result.candidates[0].product.junctions] == [24, 36]


# --- 3. which primer carries the overlap ----------------------------------------------------------

@pytest.mark.parametrize(
    ("distribution", "left_tail", "right_tail"),
    [("left", 24, 0), ("right", 0, 24), ("split", 12, 12)],
)
def test_distribution_places_the_overlap_on_the_chosen_primers(distribution, left_tail, right_tail) -> None:
    result = design_gibson(
        [frag(1), frag(2)], preparations=["pcr", "pcr"], circular=False, overlap=24,
        junctions=[JunctionSpec("fragment-1", 24, distribution)],
    )
    assert len(primer(result, "fragment-1", "reverse").tail_sequence) == left_tail
    assert len(primer(result, "fragment-2", "forward").tail_sequence) == right_tail
    junction = result.candidates[0].product.junctions[0]
    assert junction.overlap_length == 24 and junction.distribution == distribution


def test_distribution_is_reported_for_automatic_and_existing_homology() -> None:
    auto = design_gibson([frag(1), frag(2)], preparations=["pcr", "ready_linear"], circular=False, overlap=24,
                         junctions=[JunctionSpec("fragment-1", 30)])
    assert auto.candidates[0].product.junctions[0].distribution == "left"
    shared = dna(7, 30)
    left = AssemblyFragment(id="a", name="A", sequence=dna(5, 150) + shared)
    right = AssemblyFragment(id="b", name="B", sequence=shared + dna(6, 150))
    homology = design_gibson([left, right], preparations=["ready_linear", "ready_linear"], circular=False)
    assert homology.candidates[0].product.junctions[0].distribution == "homology"
    plain = design_gibson([frag(1), frag(2)], preparations=["pcr", "pcr"], circular=False, overlap=24)
    assert plain.candidates[0].product.junctions[0].distribution == "split"


def test_linker_between_parts_stays_a_short_fragment_and_junction_is_set_on_the_left_part() -> None:
    linker = AssemblyFragment(id="linker", name="linker", sequence=dna(9, 30))
    result = design_gibson(
        [frag(1), linker, frag(2)], preparations=["pcr", "ready_linear", "pcr"], circular=False, overlap=30,
        junctions=[JunctionSpec("fragment-1", 20, "split")],
    )
    junction = result.candidates[0].product.junctions[0]
    assert junction.overlap_length == 20 + 30
    assert result.candidates[0].product.sequence == dna(1) + dna(9, 30) + dna(2)


# --- 4. infeasible choices name the junction -------------------------------------------------------

@pytest.mark.parametrize(
    ("preparations", "spec", "message"),
    [
        (["pcr", "ready_linear"], JunctionSpec("fragment-1", 24, "right"), "Fragment 1"),
        (["ready_linear", "pcr"], JunctionSpec("fragment-1", 24, "left"), "Fragment 1"),
        (["pcr", "ready_linear"], JunctionSpec("fragment-1", 24, "split"), "Fragment 2"),
        (["pcr", "pcr"], JunctionSpec("nope", 24), "nope"),
        (["pcr", "pcr"], JunctionSpec("fragment-2", 24), "no junction after"),
        (["pcr", "pcr"], JunctionSpec("fragment-1", 10), "15"),
        (["pcr", "pcr"], JunctionSpec("fragment-1", 90), "80"),
        (["pcr", "pcr"], JunctionSpec("fragment-1", 24, "middle"), "distribution"),
    ],
)
def test_infeasible_junction_choices_are_actionable(preparations, spec, message) -> None:
    with pytest.raises(AssemblyError, match=message):
        design_gibson([frag(1), frag(2)], preparations=preparations, circular=False, overlap=24, junctions=[spec])


def test_linker_ids_and_duplicates_are_rejected() -> None:
    linker = AssemblyFragment(id="linker", name="linker", sequence=dna(9, 30))
    parts = [frag(1), linker, frag(2)]
    prep = ["pcr", "ready_linear", "pcr"]
    with pytest.raises(AssemblyError, match="linker"):
        design_gibson(parts, preparations=prep, circular=False, junctions=[JunctionSpec("linker", 24)])
    with pytest.raises(AssemblyError, match="more than once"):
        design_gibson(parts, preparations=prep, circular=False,
                      junctions=[JunctionSpec("fragment-1", 24), JunctionSpec("fragment-1", 30)])


# --- 5. observations only: quality notes never gate ----------------------------------------------

def test_weak_overlaps_are_noted_not_refused() -> None:
    weak = "ATTATAATTATATAATTATA"  # 20 nt, 0 % GC, two-letter start
    left = AssemblyFragment(id="a", name="A", sequence=dna(5, 150) + weak)
    right = AssemblyFragment(id="b", name="B", sequence=weak + dna(6, 100) + weak + dna(8, 100))
    result = design_gibson([left, right], preparations=["ready_linear", "ready_linear"], circular=False)
    junction = result.candidates[0].product.junctions[0]
    notes = " | ".join(junction.notes)
    assert junction.overlap_gc == 0.0
    assert "48" in notes and "Tm" in notes
    assert "GC" in notes
    assert "complexity" in notes
    assert "also occurs" in notes
    assert result.selected_candidate_checksum


def test_good_overlaps_have_no_notes() -> None:
    result = design_gibson([frag(1), frag(2)], preparations=["pcr", "pcr"], circular=False, overlap=30)
    junction = result.candidates[0].product.junctions[0]
    assert junction.notes == []
    assert 0 < junction.overlap_gc <= 100


def test_junction_response_omits_gibson_only_fields_for_other_assemblies() -> None:
    other = AssemblyJunctionResponse(
        left_fragment_id="a", right_fragment_id="b", left_fragment_name="A", right_fragment_name="B",
        mode="golden_gate", junction_sequence="AATT",
    ).model_dump()
    assert "overlap_gc" not in other and "distribution" not in other
    gibson = AssemblyJunctionResponse(
        left_fragment_id="a", right_fragment_id="b", left_fragment_name="A", right_fragment_name="B",
        mode="gibson", junction_sequence="AATT", overlap_gc=45.5, distribution="split",
    ).model_dump()
    assert gibson["overlap_gc"] == 45.5 and gibson["distribution"] == "split"


def test_golden_gate_solution_payload_omits_the_gibson_only_junction_keys() -> None:
    """Golden Gate junction payloads stay byte-identical: the two Gibson-only keys never appear."""
    from services.assembly.golden_gate_design_types import Solution
    from services.assembly.types import AssemblyJunction

    junction = AssemblyJunction(
        left_fragment_id="a", right_fragment_id="b", left_fragment_name="A", right_fragment_name="B",
        mode="golden_gate", overhang_sequence="AATT", junction_sequence="AATT",
    )
    solution = Solution.model_construct(
        id="s", sequence="ACGT", topology="circular", display_origin=0, part_material_ids=[], mappings=[],
        features=[], junctions=[junction], occurrences=[], exact_target_match=None, warnings=[],
    )
    row = solution.model_dump(mode="json")["junctions"][0]
    assert "overlap_gc" not in row and "distribution" not in row
    assert row["overhang_sequence"] == "AATT" and row["overlap_tm"] is None


def test_retained_gibson_result_keeps_overlap_gc_and_distribution() -> None:
    """Saving reloads the retained result through the declared types; the Gibson-only keys must survive."""
    from pydantic import TypeAdapter

    from services.assembly.types import GibsonDesignResult

    result = design_gibson([frag(1), frag(2)], preparations=["pcr"] * 2, circular=True, overlap=24,
                           junctions=[JunctionSpec("fragment-1", 30, "left")])
    reloaded = TypeAdapter(GibsonDesignResult).validate_python(
        TypeAdapter(GibsonDesignResult).dump_python(result, mode="json"))
    first = reloaded.candidates[0].product.junctions[0]
    assert (first.overlap_length, first.distribution) == (30, "left") and first.overlap_gc is not None
    assert reloaded == result


# --- 6. the real routes ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'junctions.db'}")
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override_session():
        async with sessions() as session:
            yield session

    application = FastAPI()
    application.include_router(molbio_router)
    application.include_router(sequence_router)
    application.dependency_overrides[get_molbio_session] = override_session
    try:
        yield application
    finally:
        asyncio.run(engine.dispose())


def payload(**extra) -> dict:
    return {"fragments": [request_fragment(1), request_fragment(2), request_fragment(3)], "circular": True,
            "overlap": 24, **extra}


def test_design_route_honours_junctions_and_reports_quality(app) -> None:
    junctions = [{"after_fragment_id": "fragment-1", "overlap": 40, "distribution": "left"},
                 {"after_fragment_id": "fragment-2", "overlap": 20}]
    with TestClient(app) as client:
        response = client.post("/api/molbio/assembly/gibson/design", json=payload(junctions=junctions))
    assert response.status_code == 200, response.text
    body = response.json()
    rows = body["selected_product"]["junctions"]
    assert [row["overlap_length"] for row in rows] == [40, 20, 24]
    assert [row["distribution"] for row in rows] == ["left", "split", "split"]
    assert all(0 <= row["overlap_gc"] <= 100 for row in rows)
    tails = {p["id"]: len(p["tail_sequence"]) for p in body["primers"]}
    assert tails["fragment-1:reverse"] == 40 and tails["fragment-2:forward"] == 0


def test_design_route_without_junctions_keeps_the_same_shape(app) -> None:
    with TestClient(app) as client:
        response = client.post("/api/molbio/assembly/gibson/design", json=payload())
    assert response.status_code == 200
    # captured from the untouched 122b496 tree through the same route (route default min_anneal is 13)
    assert response.json()["selected_candidate_checksum"] == (
        "2050572e29108ad13f16a3134b631d4e641ded12bfa863359f749c83a200b42e")


def test_design_route_rejects_infeasible_choices_with_the_junction_named(app) -> None:
    body = payload(junctions=[{"after_fragment_id": "fragment-1", "overlap": 24, "distribution": "right"}])
    body["fragments"][1]["preparation"] = "ready_linear"
    with TestClient(app) as client:
        response = client.post("/api/molbio/assembly/gibson/design", json=body)
        unknown = client.post("/api/molbio/assembly/gibson/design",
                              json=payload(junctions=[{"after_fragment_id": "zzz", "overlap": 24}]))
        bad = client.post("/api/molbio/assembly/gibson/design",
                          json=payload(junctions=[{"after_fragment_id": "fragment-1", "overlap": 24, "distribution": "x"}]))
    assert response.status_code == 400 and "Fragment 1" in response.json()["detail"] and "Fragment 2" in response.json()["detail"]
    assert unknown.status_code == 400 and "zzz" in unknown.json()["detail"]
    assert bad.status_code == 422


def test_junction_choices_change_the_candidate_checksum(app) -> None:
    with TestClient(app) as client:
        plain = client.post("/api/molbio/assembly/gibson/design", json=payload()).json()
        custom = client.post("/api/molbio/assembly/gibson/design",
                             json=payload(junctions=[{"after_fragment_id": "fragment-1", "overlap": 40}])).json()
    assert plain["selected_candidate_checksum"] != custom["selected_candidate_checksum"]


def test_save_roundtrip_keeps_the_junction_choices(app) -> None:
    junctions = [{"after_fragment_id": "fragment-1", "overlap": 40, "distribution": "split"}]
    body = payload(junctions=junctions)
    with TestClient(app) as client:
        design = client.post("/api/molbio/assembly/gibson/design", json=body)
        assert design.status_code == 200
        common = {"computation_id": design.json()["computation_id"],
                  "selected_candidate_checksum": design.json()["selected_candidate_checksum"], "new_name": "Junction design"}
        other = client.post("/api/molbio/assembly/gibson/design/save", json={
            **payload(junctions=[{"after_fragment_id": "fragment-1", "overlap": 30}]), **common})
        saved = client.post("/api/molbio/assembly/gibson/design/save", json={**body, **common})
    assert other.status_code == 400
    assert saved.status_code == 200, saved.text
    request = saved.json()["saved_sequence"]["operation_params"]["design_request"]
    assert request["junctions"] == junctions
    assert [row["overlap_length"] for row in saved.json()["selected_product"]["junctions"]] == [40, 24, 24]


# --- overlap length against what NEBuilder accepts (observation only) ---------------------------

@pytest.mark.parametrize("length", [15, 19, 76, 80])
def test_overlap_outside_the_range_nebuilder_accepts_is_noted_and_still_designed(length: int) -> None:
    result = design_gibson([frag(1, 400), frag(2, 400)], preparations=["pcr"] * 2, circular=True, overlap=30,
                           junctions=[JunctionSpec("fragment-1", length, None)])
    first = result.candidates[0].product.junctions[0]
    assert first.overlap_length == length
    assert any("NEBuilder" in note for note in first.notes)


@pytest.mark.parametrize("length", [20, 25, 75])
def test_overlap_inside_the_range_nebuilder_accepts_has_no_length_note(length: int) -> None:
    result = design_gibson([frag(1, 400), frag(2, 400)], preparations=["pcr"] * 2, circular=True, overlap=30,
                           junctions=[JunctionSpec("fragment-1", length, None)])
    first = result.candidates[0].product.junctions[0]
    assert first.overlap_length == length
    assert not any("NEBuilder" in note for note in first.notes)
