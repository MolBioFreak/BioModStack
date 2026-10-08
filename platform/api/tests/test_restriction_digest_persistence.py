from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import rfc8785
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import (
    IMMUTABLE_TABLES,
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    RestrictionDigestResult,
)
from routers import molbio_restriction
from services.restriction_catalog import CatalogAuthority, catalog_authority
from services.restriction_analysis import reverse_complement
from services.restriction_digest import DigestGeometryError, simulate_digest

API_ROOT = Path(__file__).resolve().parents[1]
CATALOG = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_v1.json"
MANIFEST = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
SCHEMA = API_ROOT.parents[1] / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"
CATALOG_ID = "biopython-rebase-404-bms-v1"
CATALOG_SHA = "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"
UNCUT_LINEAR_CANONICAL_FIXTURE = (
    Path(__file__).parent
    / "fixtures/restriction_digest/uncut_linear_ecori_unsigned.canonical.json"
)


def _simulate(
    sequence: str, enzymes: tuple[str, ...], topology: str = "linear",
    source_receipt: dict[str, object] | None = None,
):
    view = catalog_authority.require()
    catalog_receipt = {
        key: value for key, value in catalog_authority.readiness().items()
        if key not in {"required", "ready", "status"}
    }
    catalog_receipt["digest_enabled"] = True
    return simulate_digest(
        sequence=sequence,
        topology=topology,
        catalog=view,
        records=tuple(view.by_id[item] for item in enzymes),
        selected_enzyme_ids=enzymes,
        source_receipt=source_receipt or {
            "kind": "inline_dna", "name": "fixture", "sequence_id": None,
            "revision_id": None, "revision_number": None,
            "content_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            "content_length": len(sequence), "topology": topology,
        },
        catalog_receipt=catalog_receipt,
    )


def test_no_cut_linear_and_circular_products_are_explicit_and_deterministic() -> None:
    linear = _simulate("ACGTACGT", ("EcoRI",))
    circular = _simulate("ACGTACGT", ("EcoRI",), "circular")
    assert linear.cleavage_state == circular.cleavage_state == "uncut"
    assert len(linear.fragments) == len(circular.fragments) == 1
    assert linear.fragments[0].topology == "linear"
    assert linear.fragments[0].left_end.kind == linear.fragments[0].right_end.kind == "natural"
    assert circular.fragments[0].topology == "circular"
    assert circular.fragments[0].left_end.kind == circular.fragments[0].right_end.kind == "no_cut_circular"
    fixture_bytes = UNCUT_LINEAR_CANONICAL_FIXTURE.read_bytes()
    assert fixture_bytes.endswith(b"\n")
    expected_canonical_bytes = fixture_bytes[:-1]
    assert linear.canonical_unsigned_bytes() == expected_canonical_bytes
    assert linear.simulation_sha256 == "f2607e6952df96bda547faf759b7c194ea59769e94ae8c9ed2713345204a2db3"
    assert hashlib.sha256(expected_canonical_bytes).hexdigest() == (
        "f2607e6952df96bda547faf759b7c194ea59769e94ae8c9ed2713345204a2db3"
    )


def test_one_linear_ecori_cut_makes_two_fragments_with_complementary_exact_ends() -> None:
    result = _simulate("TTGAATTCAA", ("EcoRI",))
    assert result.cleavage_state == "fragmented"
    assert [fragment.top_strand_sequence for fragment in result.fragments] == ["TTG", "AATTCAA"]
    upstream, downstream = result.fragments
    assert upstream.right_end.model_dump()["overhang_sequence_5to3"] == "AATT"
    assert upstream.right_end.protruding_strand == "bottom"
    assert downstream.left_end.overhang_sequence_5to3 == "AATT"
    assert downstream.left_end.protruding_strand == "top"
    assert upstream.left_end.kind == downstream.right_end.kind == "natural"


def test_one_circular_dsb_linearizes_full_length_without_losing_origin() -> None:
    result = _simulate("AATTCCCG", ("EcoRI",), "circular")
    assert result.cleavage_state == "linearized"
    assert len(result.fragments) == 1
    fragment = result.fragments[0]
    assert fragment.topology == "linear"
    assert len(fragment.top_strand_sequence) == 8
    assert fragment.wraps_origin is True
    assert fragment.source_segments == ((0, 8),)
    assert {fragment.left_end.protruding_strand, fragment.right_end.protruding_strand} == {"top", "bottom"}


def test_duplicate_physical_cut_contributors_are_deduplicated_with_evidence() -> None:
    result = _simulate("TTATAA", ("AanI", "PsiI"))
    assert len(result.cleavages) == 1
    assert result.cleavages[0].contributing_enzyme_ids == ("AanI", "PsiI")
    assert len(result.fragments) == 2
    assert {occurrence.enzyme_id for occurrence in result.occurrences} == {"AanI", "PsiI"}


@pytest.mark.parametrize(
    ("sequence", "enzyme", "code"),
    [
        ("GCAAAC", "Aba13301I", "enzyme_geometry_unavailable"),
        ("CCTCAGC", "Nt.BbvCI", "nicking_enzyme_not_digestible"),
        ("GARTTC", "EcoRI", "possible_site_not_digestible"),
        ("GGTCTC", "BsaI", "linear_cut_out_of_bounds"),
    ],
)
def test_digest_rejects_nonphysical_or_uncertain_selected_geometry(sequence, enzyme, code) -> None:
    with pytest.raises(DigestGeometryError) as raised:
        _simulate(sequence, (enzyme,))
    assert raised.value.code == code


def test_multiple_linear_and_circular_cuts_are_ordered_into_physical_fragments() -> None:
    sequence = "GAATTCAAAAGAATTC"
    linear = _simulate(sequence, ("EcoRI",))
    circular = _simulate(sequence, ("EcoRI",), "circular")
    assert [cut.cleavage_index for cut in linear.cleavages] == [0, 1]
    assert len(linear.fragments) == 3
    assert [
        (
            cut.cleavage_index,
            cut.top_boundary, cut.bottom_boundary,
            cut.top_boundary_unwrapped, cut.bottom_boundary_unwrapped,
            cut.top_winding, cut.bottom_winding,
            cut.overhang_kind, cut.overhang_length_nt,
        )
        for cut in circular.cleavages
    ] == [
        (0, 1, 5, 1, 5, 0, 0, "five_prime", 4),
        (1, 11, 15, 11, 15, 0, 0, "five_prime", 4),
    ]
    assert [
        (
            fragment.fragment_index,
            fragment.top_strand_sequence,
            fragment.reference_span_bp,
            fragment.source_segments,
            (
                fragment.top_start_boundary, fragment.top_end_boundary,
                fragment.bottom_start_boundary, fragment.bottom_end_boundary,
            ),
            (
                fragment.top_start_boundary_normalized,
                fragment.top_end_boundary_normalized,
                fragment.bottom_start_boundary_normalized,
                fragment.bottom_end_boundary_normalized,
            ),
            (
                fragment.top_start_winding, fragment.top_end_winding,
                fragment.bottom_start_winding, fragment.bottom_end_winding,
            ),
            fragment.wraps_origin,
        )
        for fragment in circular.fragments
    ] == [
        (
            0, "AATTCAAAAG", 10, ((1, 11),),
            (1, 11, 5, 15), (1, 11, 5, 15), (0, 0, 0, 0), True,
        ),
        (
            1, "AATTCG", 6, ((11, 16), (0, 1)),
            (11, 17, 15, 21), (11, 1, 15, 5), (0, 1, 0, 1), True,
        ),
    ]
    assert [
        (
            fragment.left_end.kind,
            fragment.left_end.overhang_sequence_5to3,
            fragment.left_end.protruding_strand,
            fragment.left_end.top_boundary_unwrapped,
            fragment.left_end.bottom_boundary_unwrapped,
            fragment.right_end.kind,
            fragment.right_end.overhang_sequence_5to3,
            fragment.right_end.protruding_strand,
            fragment.right_end.top_boundary_unwrapped,
            fragment.right_end.bottom_boundary_unwrapped,
        )
        for fragment in circular.fragments
    ] == [
        ("five_prime_overhang", "AATT", "top", 1, 5,
         "five_prime_overhang", "AATT", "bottom", 11, 15),
        ("five_prime_overhang", "AATT", "top", 11, 15,
         "five_prime_overhang", "AATT", "bottom", 1, 5),
    ]
    assert [fragment.lineage_cleavage_group_ids for fragment in circular.fragments] == [
        (
            "sha256:285e9478ea043955f39420aaef77c8f2c42600216686e797a8a540b9071f2284",
            "sha256:4c6401ca6bfbdca8648f89e6e5a82f7352768d02ffda15db351f5223abbdb8cc",
        ),
        (
            "sha256:4c6401ca6bfbdca8648f89e6e5a82f7352768d02ffda15db351f5223abbdb8cc",
            "sha256:285e9478ea043955f39420aaef77c8f2c42600216686e797a8a540b9071f2284",
        ),
    ]


def test_blunt_and_three_prime_end_sequences_follow_end_specific_strands() -> None:
    blunt = _simulate("TTATAA", ("AanI",))
    assert blunt.fragments[0].right_end.kind == "blunt"
    assert blunt.fragments[1].left_end.kind == "blunt"
    assert blunt.fragments[0].right_end.overhang_sequence_5to3 is None

    three_prime = _simulate("AACTGAAG" + "A" * 14 + "GCAA", ("AcuI",))
    upstream, downstream = three_prime.fragments
    assert upstream.right_end.kind == downstream.left_end.kind == "three_prime_overhang"
    assert upstream.right_end.protruding_strand == "top"
    assert downstream.left_end.protruding_strand == "bottom"
    assert upstream.right_end.overhang_sequence_5to3 is not None
    assert downstream.left_end.overhang_sequence_5to3 == reverse_complement(
        upstream.right_end.overhang_sequence_5to3
    )


def test_nonidentical_shared_crossing_and_overlapping_duplex_cuts_fail_closed() -> None:
    from types import SimpleNamespace
    from services.restriction_digest import _physical_cuts

    def analysis(pairs):
        events = []
        for ordinal, (top, bottom) in enumerate(pairs):
            events.append(SimpleNamespace(
                contributor_group_id=f"group-{ordinal}", enzyme_id=f"E{ordinal}",
                occurrence_id=f"occurrence-{ordinal}", event_ordinal=0,
                orientation="forward", status="complete", top_boundary_unwrapped=top,
                bottom_boundary_unwrapped=bottom, top_boundary=top, bottom_boundary=bottom,
                overhang_kind="five_prime" if bottom > top else "three_prime",
                overhang_length_nt=abs(bottom - top),
            ))
        occurrence = SimpleNamespace(certainty="definite", double_strand_events=events)
        return SimpleNamespace(occurrences=[occurrence])

    with pytest.raises(DigestGeometryError) as shared:
        _physical_cuts(analysis(((2, 6), (2, 8))), 20, "linear")
    assert shared.value.code == "unsupported_crossing_cleavage_geometry"
    with pytest.raises(DigestGeometryError) as crossing:
        _physical_cuts(analysis(((2, 8), (4, 6))), 20, "linear")
    assert crossing.value.code == "unsupported_crossing_cleavage_geometry"
    with pytest.raises(DigestGeometryError) as overlap:
        _physical_cuts(analysis(((2, 6), (4, 8))), 20, "linear")
    assert overlap.value.code == "overlapping_cleavage_geometry"


def test_circular_last_to_first_stagger_overlap_fails_closed() -> None:
    from types import SimpleNamespace
    from services.restriction_digest import _physical_cuts

    events = [
        SimpleNamespace(
            contributor_group_id="group-first", enzyme_id="First",
            occurrence_id="occurrence-first", event_ordinal=0,
            orientation="forward", status="complete",
            top_boundary_unwrapped=1, bottom_boundary_unwrapped=0,
            top_boundary=1, bottom_boundary=0,
            top_winding=0, bottom_winding=0,
            overhang_kind="three_prime", overhang_length_nt=1,
        ),
        SimpleNamespace(
            contributor_group_id="group-last", enzyme_id="Last",
            occurrence_id="occurrence-last", event_ordinal=0,
            orientation="forward", status="complete",
            top_boundary_unwrapped=18, bottom_boundary_unwrapped=22,
            top_boundary=18, bottom_boundary=2,
            top_winding=0, bottom_winding=1,
            overhang_kind="five_prime", overhang_length_nt=4,
        ),
    ]
    analysis = SimpleNamespace(
        occurrences=[SimpleNamespace(certainty="definite", double_strand_events=events)]
    )

    with pytest.raises(DigestGeometryError) as overlap:
        _physical_cuts(analysis, 20, "circular")
    assert overlap.value.code == "overlapping_cleavage_geometry"


def test_circular_dedupe_group_rejects_unwrapped_winding_mismatch() -> None:
    from types import SimpleNamespace
    from services.restriction_digest import _physical_cuts

    events = [
        SimpleNamespace(
            contributor_group_id="shared-group", enzyme_id="First",
            occurrence_id="occurrence-first", event_ordinal=0,
            orientation="forward", status="complete",
            top_boundary_unwrapped=2, bottom_boundary_unwrapped=6,
            top_boundary=2, bottom_boundary=6,
            top_winding=0, bottom_winding=0,
            overhang_kind="five_prime", overhang_length_nt=4,
        ),
        SimpleNamespace(
            contributor_group_id="shared-group", enzyme_id="Second",
            occurrence_id="occurrence-second", event_ordinal=0,
            orientation="forward", status="complete",
            top_boundary_unwrapped=2, bottom_boundary_unwrapped=6,
            top_boundary=2, bottom_boundary=6,
            top_winding=0, bottom_winding=1,
            overhang_kind="five_prime", overhang_length_nt=4,
        ),
    ]
    analysis = SimpleNamespace(
        occurrences=[SimpleNamespace(certainty="definite", double_strand_events=events)]
    )

    with pytest.raises(DigestGeometryError) as mismatch:
        _physical_cuts(analysis, 20, "circular")
    assert mismatch.value.code == "unsupported_crossing_cleavage_geometry"


def test_circular_single_cut_rejects_self_spanning_stagger() -> None:
    from types import SimpleNamespace
    from services.restriction_digest import _physical_cuts

    event = SimpleNamespace(
        contributor_group_id="self-spanning", enzyme_id="SelfSpanning",
        occurrence_id="occurrence", event_ordinal=0, orientation="forward",
        status="complete", top_boundary_unwrapped=2, bottom_boundary_unwrapped=22,
        top_boundary=2, bottom_boundary=2, top_winding=0, bottom_winding=1,
        overhang_kind="five_prime", overhang_length_nt=20,
    )
    analysis = SimpleNamespace(
        occurrences=[SimpleNamespace(certainty="definite", double_strand_events=[event])]
    )

    with pytest.raises(DigestGeometryError) as self_spanning:
        _physical_cuts(analysis, 20, "circular")
    assert self_spanning.value.code == "unsupported_crossing_cleavage_geometry"


def test_request_and_simulation_hashes_change_for_every_scientific_authority() -> None:
    first = _simulate("TTGAATTCAA", ("EcoRI",))
    second = _simulate("TTGAATTCAAA", ("EcoRI",))
    third = _simulate("TTGAATTCAA", ("EcoRI", "MboI"))
    assert len({first.request_sha256, second.request_sha256, third.request_sha256}) == 3
    assert len({first.simulation_sha256, second.simulation_sha256, third.simulation_sha256}) == 3


def test_simulation_authority_receipts_are_closed_against_fully_rehashed_extra_fields() -> None:
    from pydantic import ValidationError
    from services.restriction_digest import DigestSimulation

    baseline = _simulate("TTGAATTCAA", ("EcoRI",)).model_dump(mode="json", by_alias=True)
    for authority in ("source", "catalog"):
        mutant = json.loads(json.dumps(baseline))
        mutant[authority]["fully_rehashed_extra_field"] = True
        unsigned = dict(mutant)
        unsigned.pop("simulation_sha256")
        mutant["simulation_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
        with pytest.raises(ValidationError):
            DigestSimulation.model_validate(mutant)


async def _store(
    tmp_path: Path, *, source_snapshot: dict[str, object] | None = None,
    sequence: str = "TTGAATTCAA",
):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'digest.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    async with sessions.begin() as session:
        session.add(MolecularDocument(
            id="source-document", document_kind="dna", name="source", current_revision_id=None,
        ))
        session.add(MolecularRevision(
            id="source-revision", document_id="source-document", revision_number=1,
            change_kind="created", content_sha256=digest, content_length=len(sequence),
            snapshot=(
                source_snapshot
                if source_snapshot is not None
                else {"sequence_type": "dna", "sequence": sequence, "is_circular": False}
            ),
            provenance={}, operation_id=None, created_by="test",
        ))
        await session.flush()
        document = await session.get(MolecularDocument, "source-document")
        document.current_revision_id = "source-revision"
    return engine, sessions, digest


def _preview_request(digest: str) -> dict[str, object]:
    return {
        "schema": "bms.molbio.restriction-digest-simulation-request.v1",
        "source": {
            "kind": "molecular_revision", "sequence_id": "source-document",
            "revision_id": "source-revision", "expected_content_sha256": digest,
        },
        "catalog": {"catalog_id": CATALOG_ID, "expected_catalog_sha256": CATALOG_SHA},
        "enzyme_ids": ["EcoRI"],
    }


async def _client(sessions):
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    authority = CatalogAuthority(CATALOG, MANIFEST, SCHEMA)
    app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[molbio_restriction.get_molbio_session] = session_dependency
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _immutable_digest_rows(connection, operation_id: str) -> dict[str, tuple[tuple, ...]]:
    statements = {
        "operation": "SELECT * FROM molecular_operations WHERE id=:operation_id ORDER BY id",
        "inputs": (
            "SELECT * FROM molecular_operation_inputs "
            "WHERE operation_id=:operation_id ORDER BY position,id"
        ),
        "outputs": (
            "SELECT * FROM molecular_operation_outputs "
            "WHERE operation_id=:operation_id ORDER BY position,id"
        ),
        "result": (
            "SELECT * FROM restriction_digest_results "
            "WHERE operation_id=:operation_id ORDER BY id"
        ),
        "revisions": (
            "SELECT * FROM molecular_revisions "
            "WHERE id='source-revision' OR operation_id=:operation_id ORDER BY id"
        ),
    }
    return {
        name: tuple(tuple(row) for row in (
            await connection.execute(text(statement), {"operation_id": operation_id})
        ).all())
        for name, statement in statements.items()
    }


@pytest.mark.asyncio
async def test_digest_routes_reject_caller_geometry_unknown_fields_and_stale_authorities(tmp_path: Path) -> None:
    engine, sessions, digest = await _store(tmp_path)
    try:
        async with await _client(sessions) as client:
            request = _preview_request(digest)
            request["site"] = "GAATTC"
            rejected = await client.post("/api/molbio/restriction/digests/simulate", json=request)
            assert rejected.status_code == 422
            assert rejected.json()["detail"]["code"] == "invalid_digest_request"

            unknown = _preview_request(digest)
            unknown["enzyme_ids"] = ["not-an-enzyme"]
            missing = await client.post("/api/molbio/restriction/digests/simulate", json=unknown)
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "enzyme_not_found"

            stale = _preview_request(digest)
            stale_catalog = stale["catalog"]
            assert isinstance(stale_catalog, dict)
            stale_catalog["expected_catalog_sha256"] = "0" * 64
            mismatch = await client.post("/api/molbio/restriction/digests/simulate", json=stale)
            assert mismatch.status_code == 409
            assert mismatch.json()["detail"]["code"] == "catalog_digest_mismatch"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_digest_rejects_caller_topology_when_revision_has_no_authority(
    tmp_path: Path,
) -> None:
    engine, sessions, digest = await _store(
        tmp_path,
        source_snapshot={"sequence_type": "dna", "sequence": "TTGAATTCAA"},
    )
    try:
        request = _preview_request(digest)
        source = request["source"]
        assert isinstance(source, dict)
        source["topology"] = "circular"
        async with await _client(sessions) as client:
            rejected = await client.post(
                "/api/molbio/restriction/digests/simulate", json=request,
            )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "invalid_dna"
    finally:
        await engine.dispose()


def test_digest_openapi_contract_is_closed_and_publishes_runtime_bounds() -> None:
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["DigestSimulationRequest"]["additionalProperties"] is False
    assert schemas["DigestSaveRequest"]["additionalProperties"] is False
    assert schemas["DigestSimulation"]["additionalProperties"] is False
    assert schemas["DigestSimulationRequest"]["properties"]["enzyme_ids"]["maxItems"] == 64
    policy = schemas["DigestResourcePolicy"]["properties"]
    assert policy["physical_cut_maximum"]["default"] == 4096
    assert policy["fragment_maximum"]["default"] == 4097
    assert policy["simulation_response_maximum_bytes"]["default"] == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_preview_emits_no_sqlalchemy_dml_or_orm_mutation_and_save_reruns_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.orm import Session

    engine, sessions, digest = await _store(tmp_path)
    dml: list[str] = []
    flushes: list[bool] = []
    original_pipeline = molbio_restriction._complete_digest_pipeline
    simulations = 0

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            dml.append(statement)

    def before_flush(session, _context, _instances):
        if session.bind is engine.sync_engine:
            flushes.append(True)

    def counted_pipeline(**kwargs):
        nonlocal simulations
        simulations += 1
        return original_pipeline(**kwargs)

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(Session, "before_flush", before_flush)
    monkeypatch.setattr(molbio_restriction, "_complete_digest_pipeline", counted_pipeline)
    try:
        async with sessions() as session:
            before = {
                table.__tablename__: await session.scalar(select(func.count()).select_from(table))
                for table in (
                    MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
                    RestrictionDigestResult, MolecularDocument, MolecularRevision,
                )
            }
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            assert preview.status_code == 200
            assert dml == []
            assert flushes == []
            async with sessions() as session:
                after_preview = {
                    table.__tablename__: await session.scalar(select(func.count()).select_from(table))
                    for table in (
                        MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
                        RestrictionDigestResult, MolecularDocument, MolecularRevision,
                    )
                }
            assert after_preview == before
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": "server-rerun-proof",
                "persistence_mode": "operation_only",
            }
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            assert simulations == 2
    finally:
        event.remove(Session, "before_flush", before_flush)
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_has_no_database_effect_and_save_is_atomic_idempotent(tmp_path: Path) -> None:
    engine, sessions, digest = await _store(tmp_path)
    try:
        tables = (
            MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
            RestrictionDigestResult, MolecularDocument, MolecularRevision,
        )
        async with sessions() as session:
            before = []
            for table in tables:
                before.append((table, await session.scalar(select(func.count()).select_from(table))))
        async with await _client(sessions) as client:
            preview = await client.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
            assert preview.status_code == 200, preview.text
            simulation_sha = preview.json()["simulation_sha256"]
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": simulation_sha,
                "idempotency_key": "phase3-exact-replay",
                "persistence_mode": "operation_and_fragments",
                "fragment_name_prefix": "EcoRI fragment",
            }
            first = await client.post("/api/molbio/restriction/digests", json=save)
            second = await client.post("/api/molbio/restriction/digests", json=save)
            assert first.status_code == second.status_code == 200, (first.text, second.text)
            assert first.json() == second.json()
            assert len(first.json()["outputs"]) == 2
            changed = {**save, "persistence_mode": "operation_only"}
            conflict = await client.post("/api/molbio/restriction/digests", json=changed)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "idempotency_conflict"
            loaded = await client.get(f"/api/molbio/restriction/digests/{first.json()['operation_id']}")
            assert loaded.status_code == 200
            assert loaded.json() == first.json()
        async with sessions() as session:
            after_preview_and_save = []
            for table in tables:
                after_preview_and_save.append(
                    (table, await session.scalar(select(func.count()).select_from(table)))
                )
        assert before[:4] == [(table, 0) for table, _count in before[:4]]
        assert [count for _table, count in after_preview_and_save] == [1, 1, 2, 1, 3, 3]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_post_replay_uses_immutable_result_before_mutable_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions, digest = await _store(tmp_path)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": "immutable-post-replay",
                "persistence_mode": "operation_and_fragments",
            }
            first = await client.post("/api/molbio/restriction/digests", json=save)
            assert first.status_code == 200, first.text

            async with engine.begin() as connection:
                await connection.execute(text(
                    "UPDATE molecular_documents SET name='renamed after save' "
                    "WHERE id='source-document'"
                ))
            replay_after_rename = await client.post(
                "/api/molbio/restriction/digests", json=save,
            )
            assert replay_after_rename.status_code == 200, replay_after_rename.text
            assert replay_after_rename.content == first.content
            async with sessions() as session:
                counts_before = {
                    table: await session.scalar(select(func.count()).select_from(table))
                    for table in (
                        MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
                        RestrictionDigestResult, MolecularDocument, MolecularRevision,
                    )
                }

            async def forbidden_resolver(*_args, **_kwargs):
                raise AssertionError("replay resolved mutable source")

            def forbidden_pipeline(**_kwargs):
                raise AssertionError("replay loaded catalog or simulated")

            monkeypatch.setattr(molbio_restriction, "_resolve_revision_source", forbidden_resolver)
            monkeypatch.setattr(molbio_restriction, "_complete_digest_pipeline", forbidden_pipeline)
            replay = await client.post("/api/molbio/restriction/digests", json=save)
            assert replay.status_code == 200, replay.text
            assert replay.content == first.content
            assert replay.json()["operation_id"] == first.json()["operation_id"]
            assert replay.json()["outputs"] == first.json()["outputs"]

        async with sessions() as session:
            counts_after = {
                table: await session.scalar(select(func.count()).select_from(table))
                for table in counts_before
            }
        assert counts_after == counts_before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_many_output_save_and_reload_have_bounded_database_boundaries(
    tmp_path: Path,
) -> None:
    sequence = "GAATTC" * 64
    engine, sessions, digest = await _store(tmp_path, sequence=sequence)
    flushes = 0
    selects = 0

    from sqlalchemy.orm import Session

    def before_flush(session, _context, _instances):
        nonlocal flushes
        if session.bind is engine.sync_engine:
            flushes += 1

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(Session, "before_flush", before_flush)
    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            assert preview.status_code == 200, preview.text
            assert len(preview.json()["fragments"]) == 65
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": "bounded-many-output-save",
                "persistence_mode": "operation_and_fragments",
            }
            flushes = 0
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            assert flushes <= 5

            selects = 0
            loaded = await client.get(
                f"/api/molbio/restriction/digests/{saved.json()['operation_id']}"
            )
            assert loaded.status_code == 200, loaded.text
            assert loaded.content == saved.content
            assert selects <= 10
    finally:
        event.remove(Session, "before_flush", before_flush)
        event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sequence", "persistence_mode", "expected_outputs"),
    (
        ("ACGTACGT", "operation_only", 0),
        ("ACGTACGT", "operation_and_fragments", 1),
        ("TTGAATTCAA", "operation_and_fragments", 2),
    ),
)
async def test_saved_digest_reload_supports_zero_one_and_many_outputs(
    tmp_path: Path, sequence: str, persistence_mode: str, expected_outputs: int,
) -> None:
    engine, sessions, digest = await _store(tmp_path, sequence=sequence)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": f"reload-cardinality-{expected_outputs}",
                "persistence_mode": persistence_mode,
            }
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            assert len(saved.json()["outputs"]) == expected_outputs
            loaded = await client.get(
                f"/api/molbio/restriction/digests/{saved.json()['operation_id']}"
            )
            assert loaded.status_code == 200, loaded.text
            assert loaded.content == saved.content
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_response_byte_limit_is_sanitized_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions, digest = await _store(tmp_path)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            monkeypatch.setattr(molbio_restriction, "MAX_SIMULATION_RESPONSE_BYTES", 1)
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": "saved-response-limit",
                "persistence_mode": "operation_and_fragments",
            }
            rejected = await client.post("/api/molbio/restriction/digests", json=save)
            assert rejected.status_code == 413
            assert rejected.json() == {"detail": {
                "code": "request_too_large",
                "message": "restriction digest request is too large",
            }}
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(MolecularOperation)) == 0
            assert await session.scalar(select(func.count()).select_from(RestrictionDigestResult)) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_digest_canonical_cpu_work_stays_off_event_loop_and_reuses_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.restriction_digest import DigestSimulation

    engine, sessions, digest = await _store(tmp_path, sequence="GAATTC" * 32)
    event_loop_thread = threading.get_ident()
    observed: list[tuple[str, int]] = []
    canonical_active = threading.Event()
    original_simulation_bytes = DigestSimulation.canonical_bytes
    original_unsigned_bytes = DigestSimulation.canonical_unsigned_bytes
    original_saved_validate = molbio_restriction.SavedDigestResponse.model_validate_json
    original_saved_dump = molbio_restriction.SavedDigestResponse.model_dump

    def simulation_bytes(self):
        observed.append(("simulation_bytes", threading.get_ident()))
        time.sleep(0.002)
        return original_simulation_bytes(self)

    def unsigned_bytes(self):
        observed.append(("unsigned_bytes", threading.get_ident()))
        return original_unsigned_bytes(self)

    def saved_validate(value, *args, **kwargs):
        observed.append(("saved_validate", threading.get_ident()))
        canonical_active.set()
        try:
            time.sleep(0.02)
            return original_saved_validate(value, *args, **kwargs)
        finally:
            canonical_active.clear()

    def saved_dump(self, *args, **kwargs):
        observed.append(("saved_dump", threading.get_ident()))
        time.sleep(0.002)
        return original_saved_dump(self, *args, **kwargs)

    monkeypatch.setattr(DigestSimulation, "canonical_bytes", simulation_bytes)
    monkeypatch.setattr(DigestSimulation, "canonical_unsigned_bytes", unsigned_bytes)
    monkeypatch.setattr(
        molbio_restriction.SavedDigestResponse, "model_validate_json", saved_validate,
    )
    monkeypatch.setattr(molbio_restriction.SavedDigestResponse, "model_dump", saved_dump)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            assert preview.status_code == 200, preview.text
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": "worker-canonical-boundary",
                "persistence_mode": "operation_and_fragments",
            }
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            replay = await client.post("/api/molbio/restriction/digests", json=save)
            responsive_ticks = 0
            stop_ticker = False

            async def ticker() -> None:
                nonlocal responsive_ticks
                while not stop_ticker:
                    if canonical_active.is_set():
                        responsive_ticks += 1
                    await asyncio.sleep(0)

            ticker_task = asyncio.create_task(ticker())
            await asyncio.sleep(0)
            loaded = await client.get(
                f"/api/molbio/restriction/digests/{saved.json()['operation_id']}"
            )
            stop_ticker = True
            await ticker_task
            assert replay.content == loaded.content == saved.content
            assert responsive_ticks > 0

        assert observed
        assert {name for name, _thread in observed} >= {
            "simulation_bytes", "unsigned_bytes", "saved_validate", "saved_dump",
        }
        assert all(thread != event_loop_thread for _name, thread in observed)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "projection_mutation",
    ("source_rename", "output_rename", "output_head_advance", "output_soft_delete"),
)
async def test_saved_digest_reload_ignores_mutable_document_projections(
    tmp_path: Path, projection_mutation: str,
) -> None:
    case_path = tmp_path / projection_mutation
    case_path.mkdir()
    engine, sessions, digest = await _store(case_path)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": f"mutable-projection-{projection_mutation}",
                "persistence_mode": "operation_and_fragments",
                "fragment_name_prefix": "Historical fragment",
            }
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            original = saved.json()
            operation_id = original["operation_id"]
            output = original["outputs"][0]

            async with engine.begin() as connection:
                immutable_before = await _immutable_digest_rows(connection, operation_id)
                if projection_mutation == "source_rename":
                    await connection.execute(text(
                        "UPDATE molecular_documents SET name='renamed source' "
                        "WHERE id='source-document'"
                    ))
                elif projection_mutation == "output_rename":
                    await connection.execute(text(
                        "UPDATE molecular_documents SET name='renamed output' WHERE id=:id"
                    ), {"id": output["document_id"]})
                elif projection_mutation == "output_head_advance":
                    await connection.execute(text(
                        "INSERT INTO molecular_revisions("
                        "id,document_id,revision_number,change_kind,content_sha256,content_length,"
                        "snapshot,provenance,operation_id,created_by,created_at) VALUES ("
                        ":id,:document_id,2,'edited',:sha,4,:snapshot,'{}',NULL,'test',CURRENT_TIMESTAMP)"
                    ), {
                        "id": "later-output-revision",
                        "document_id": output["document_id"],
                        "sha": hashlib.sha256(b"ACGT").hexdigest(),
                        "snapshot": rfc8785.dumps({
                            "sequence_type": "dna", "sequence": "ACGT", "is_circular": False,
                        }).decode(),
                    })
                    await connection.execute(text(
                        "UPDATE molecular_documents SET current_revision_id=:revision_id WHERE id=:id"
                    ), {"revision_id": "later-output-revision", "id": output["document_id"]})
                else:
                    await connection.execute(text(
                        "UPDATE molecular_documents SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id"
                    ), {"id": output["document_id"]})

                immutable_after = await _immutable_digest_rows(connection, operation_id)
            assert immutable_after == immutable_before

            loaded = await client.get(f"/api/molbio/restriction/digests/{operation_id}")
            assert loaded.status_code == 200, loaded.text
            assert loaded.json() == original
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_correct_count_fully_populated_forged_output_graph(
    tmp_path: Path,
) -> None:
    from services.restriction_digest_save_receipt import canonical_save_request_receipt

    engine, _sessions, digest = await _store(tmp_path)
    sequence = "TTGAATTCAA"
    operation_id = "forged-graph-operation"
    simulation = _simulate(
        sequence, ("EcoRI",), source_receipt={
            "kind": "molecular_revision", "name": "source",
            "sequence_id": "source-document", "revision_id": "source-revision",
            "revision_number": 1, "content_sha256": digest,
            "content_length": len(sequence), "topology": "linear",
        },
    )
    outputs = []
    for fragment in simulation.fragments:
        fragment_bytes = fragment.top_strand_sequence.encode("ascii")
        outputs.append({
            "fragment_index": fragment.fragment_index,
            "document_id": f"forged-document-{fragment.fragment_index}",
            "revision_id": f"forged-revision-{fragment.fragment_index}",
            "output_edge_id": f"forged-edge-{fragment.fragment_index}",
            "name": f"forged fragment {fragment.fragment_index + 1}",
            "topology": fragment.topology,
            "content_sha256": hashlib.sha256(fragment_bytes).hexdigest(),
            "content_length": len(fragment_bytes),
        })
    snapshot = {
        "schema": "bms.molbio.restriction-digest-saved-result.v1",
        "operation_id": operation_id,
        "source_revision_id": "source-revision",
        "catalog_id": CATALOG_ID,
        "catalog_sha256": CATALOG_SHA,
        "request_sha256": simulation.request_sha256,
        "result_sha256": simulation.simulation_sha256,
        "simulation": simulation.model_dump(mode="json", by_alias=True),
        "outputs": outputs,
    }
    save_receipt = canonical_save_request_receipt({
        "schema": "bms.molbio.restriction-digest-save-request.v1",
        "source": {
            "kind": "molecular_revision", "sequence_id": "source-document",
            "revision_id": "source-revision", "expected_content_sha256": digest,
            "topology": None,
        },
        "catalog": {
            "catalog_id": CATALOG_ID, "expected_catalog_sha256": CATALOG_SHA,
        },
        "enzyme_ids": ["EcoRI"], "simulation_sha256": simulation.simulation_sha256,
        "idempotency_key": "forged-graph-key",
        "persistence_mode": "operation_and_fragments",
        "fragment_name_prefix": "forged fragment",
    })
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO molecular_operations("
                "id,operation_kind,implementation,implementation_version,status,parameters,"
                "warnings,provenance,idempotency_key,request_fingerprint,created_at"
                ") VALUES ("
                ":id,'restriction_digest','services.restriction_digest.simulate_digest',"
                ":version,'completed',:parameters,:warnings,:provenance,:key,:fingerprint,"
                "CURRENT_TIMESTAMP)"
            ), {
                "id": operation_id,
                "version": simulation.digest_algorithm_version,
                "parameters": rfc8785.dumps({
                    "schema": "bms.molbio.restriction-digest-operation-parameters.v1",
                    "selected_enzyme_ids": ["EcoRI"],
                    "persistence_mode": "operation_and_fragments",
                    "fragment_name_prefix": "forged fragment",
                    "simulation_sha256": simulation.simulation_sha256,
                    "save_request_receipt": save_receipt,
                }).decode(),
                "warnings": rfc8785.dumps(list(simulation.warnings)).decode(),
                "provenance": rfc8785.dumps({
                    "source_revision_id": "source-revision",
                    "catalog_id": CATALOG_ID,
                    "catalog_sha256": CATALOG_SHA,
                    "request_sha256": simulation.request_sha256,
                }).decode(),
                "key": "forged-graph-key",
                "fingerprint": "a" * 64,
            })
            await connection.execute(text(
                "INSERT INTO molecular_operation_inputs("
                "id,operation_id,revision_id,role,position,snapshot"
                ") VALUES ('forged-input',:operation_id,'source-revision','digest_source',0,:snapshot)"
            ), {
                "operation_id": operation_id,
                "snapshot": rfc8785.dumps({
                    "content_sha256": digest,
                    "name": "source",
                    "sequence_id": "source-document",
                }).decode(),
            })
            for ordinal, (fragment, identity) in enumerate(
                zip(simulation.fragments, outputs, strict=True)
            ):
                await connection.execute(text(
                    "INSERT INTO molecular_documents("
                    "id,document_kind,name,current_revision_id,created_at"
                    ") VALUES (:id,'dna',:name,NULL,CURRENT_TIMESTAMP)"
                ), {"id": identity["document_id"], "name": identity["name"]})
                await connection.execute(text(
                    "INSERT INTO molecular_revisions("
                    "id,document_id,revision_number,change_kind,content_sha256,"
                    "content_length,snapshot,provenance,operation_id,created_by,created_at"
                    ") VALUES (:id,:document_id,1,'restriction_digest_fragment',"
                    ":content_sha,:content_length,:snapshot,:provenance,:operation_id,NULL,"
                    "CURRENT_TIMESTAMP)"
                ), {
                    "id": identity["revision_id"],
                    "document_id": identity["document_id"],
                    "content_sha": identity["content_sha256"],
                    "content_length": identity["content_length"],
                    "snapshot": rfc8785.dumps({
                        "sequence_type": "dna",
                        "sequence": fragment.top_strand_sequence,
                        "is_circular": fragment.topology == "circular",
                        "topology": fragment.topology,
                        "name": identity["name"],
                    }).decode(),
                    "provenance": rfc8785.dumps({
                        "schema": "bms.molbio.restriction-digest-fragment-provenance.v1",
                        "source_revision_id": "source-revision",
                        "operation_id": operation_id,
                        "simulation_sha256": simulation.simulation_sha256,
                        "fragment_index": ordinal,
                        "geometry": fragment.model_dump(mode="json", by_alias=True),
                    }).decode(),
                    "operation_id": operation_id,
                })
                await connection.execute(text(
                    "UPDATE molecular_documents SET current_revision_id=:revision_id "
                    "WHERE id=:document_id"
                ), {
                    "revision_id": identity["revision_id"],
                    "document_id": identity["document_id"],
                })
                await connection.execute(text(
                    "INSERT INTO molecular_operation_outputs("
                    "id,operation_id,revision_id,role,position,snapshot"
                    ") VALUES (:id,:operation_id,:revision_id,'digest_fragment',"
                    ":position,:snapshot)"
                ), {
                    "id": identity["output_edge_id"],
                    "operation_id": operation_id,
                    "revision_id": identity["revision_id"],
                    "position": ordinal,
                    "snapshot": rfc8785.dumps({
                        "fragment_index": ordinal,
                        "name": identity["name"],
                        "simulation_sha256": (
                            "0" * 64 if ordinal == 0 else simulation.simulation_sha256
                        ),
                    }).decode(),
                })
            with pytest.raises(IntegrityError, match="restriction digest result integrity"):
                await connection.execute(text(
                    "INSERT INTO restriction_digest_results("
                    "id,operation_id,source_revision_id,catalog_id,catalog_sha256,"
                    "request_sha256,result_sha256,result,created_at"
                    ") VALUES ('forged-result',:operation_id,'source-revision',:catalog_id,"
                    ":catalog_sha,:request_sha,:result_sha,:result,CURRENT_TIMESTAMP)"
                ), {
                    "operation_id": operation_id,
                    "catalog_id": CATALOG_ID,
                    "catalog_sha": CATALOG_SHA,
                    "request_sha": simulation.request_sha256,
                    "result_sha": simulation.simulation_sha256,
                    "result": rfc8785.dumps(snapshot).decode(),
                })
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "receipt_fingerprint",
    "receipt_key",
    "receipt_scientific",
    "receipt_extra",
    "receipt_noncanonical",
    "operation_parameters",
    "operation_provenance",
    "persistence_mode",
    "input_sequence_id",
    "input_snapshot_name",
    "output_edge_snapshot",
    "output_edge_snapshot_name",
    "output_identity_name",
    "output_identity_topology",
    "document_kind",
    "source_revision_document_id",
    "output_revision_document_id",
    "revision_number",
    "revision_change_kind",
    "revision_snapshot",
    "revision_content",
    "revision_topology",
    "output_ordinal",
])
async def test_get_rejects_every_mutated_digest_operation_and_output_binding(
    tmp_path: Path, mutation: str,
) -> None:
    from services.restriction_digest_save_receipt import save_request_fingerprint

    case_path = tmp_path / mutation
    case_path.mkdir()
    engine, sessions, digest = await _store(case_path)
    try:
        async with await _client(sessions) as client:
            preview = await client.post(
                "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
            )
            save = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": f"get-integrity-{mutation}",
                "persistence_mode": "operation_and_fragments",
                "fragment_name_prefix": "Exact fragment",
            }
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            identity = saved.json()["outputs"][0]
            operation_id = saved.json()["operation_id"]

            async with engine.begin() as connection:
                if mutation.startswith("receipt_"):
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_operations_update"
                    ))
                    raw = (await connection.execute(text(
                        "SELECT parameters FROM molecular_operations WHERE id=:id"
                    ), {"id": operation_id})).scalar_one()
                    parameters = json.loads(raw)
                    receipt = json.loads(parameters["save_request_receipt"])
                    fingerprint = save_request_fingerprint(parameters["save_request_receipt"])
                    if mutation == "receipt_fingerprint":
                        fingerprint = "a" * 64
                    elif mutation == "receipt_key":
                        receipt["idempotency_key"] = "different-key"
                    elif mutation == "receipt_scientific":
                        receipt["catalog"]["catalog_id"] = "different-catalog"
                    elif mutation == "receipt_extra":
                        receipt["extra"] = True
                    else:
                        parameters["save_request_receipt"] = json.dumps(receipt, indent=2)
                    if mutation not in {"receipt_fingerprint", "receipt_noncanonical"}:
                        parameters["save_request_receipt"] = rfc8785.dumps(receipt).decode()
                        fingerprint = hashlib.sha256(
                            parameters["save_request_receipt"].encode("utf-8")
                        ).hexdigest()
                    await connection.execute(text(
                        "UPDATE molecular_operations SET parameters=:parameters, "
                        "request_fingerprint=:fingerprint WHERE id=:id"
                    ), {
                        "parameters": rfc8785.dumps(parameters).decode(),
                        "fingerprint": fingerprint,
                        "id": operation_id,
                    })
                elif mutation in {"operation_parameters", "operation_provenance", "persistence_mode"}:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_operations_update"
                    ))
                    column = "parameters" if mutation != "operation_provenance" else "provenance"
                    raw = (await connection.execute(text(
                        f"SELECT {column} FROM molecular_operations WHERE id=:id"
                    ), {"id": operation_id})).scalar_one()
                    document = json.loads(raw)
                    if mutation == "operation_parameters":
                        document["selected_enzyme_ids"] = ["MboI"]
                    elif mutation == "operation_provenance":
                        document["catalog_sha256"] = "0" * 64
                    else:
                        document["persistence_mode"] = "operation_only"
                    await connection.execute(text(
                        f"UPDATE molecular_operations SET {column}=:value WHERE id=:id"
                    ), {"value": rfc8785.dumps(document).decode(), "id": operation_id})
                elif mutation in {"input_sequence_id", "input_snapshot_name"}:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_operation_inputs_update"
                    ))
                    path = "$.sequence_id" if mutation == "input_sequence_id" else "$.name"
                    await connection.execute(text(
                        "UPDATE molecular_operation_inputs SET snapshot=json_set(snapshot,:path,:value) "
                        "WHERE operation_id=:id"
                    ), {"path": path, "value": "forged name", "id": operation_id})
                elif mutation in {"output_edge_snapshot", "output_edge_snapshot_name"}:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_operation_outputs_update"
                    ))
                    path = (
                        "$.simulation_sha256"
                        if mutation == "output_edge_snapshot" else "$.name"
                    )
                    await connection.execute(text(
                        "UPDATE molecular_operation_outputs SET snapshot=json_set(snapshot,:path,:value) "
                        "WHERE id=:id"
                    ), {"path": path, "value": "forged name", "id": identity["output_edge_id"]})
                elif mutation in {"output_identity_name", "output_identity_topology"}:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_restriction_digest_results_update"
                    ))
                    raw = (await connection.execute(text(
                        "SELECT result FROM restriction_digest_results WHERE operation_id=:id"
                    ), {"id": operation_id})).scalar_one()
                    document = json.loads(raw)
                    if mutation == "output_identity_name":
                        document["outputs"][0]["name"] = "forged name"
                    else:
                        document["outputs"][0]["topology"] = "circular"
                    await connection.execute(text(
                        "UPDATE restriction_digest_results SET result=:result WHERE operation_id=:id"
                    ), {"result": rfc8785.dumps(document).decode(), "id": operation_id})
                elif mutation == "document_kind":
                    await connection.execute(text(
                        "UPDATE molecular_documents SET document_kind='rna' WHERE id=:id"
                    ), {
                        "id": identity["document_id"],
                    })
                elif mutation in {"source_revision_document_id", "output_revision_document_id"}:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_revisions_update"
                    ))
                    revision_id = (
                        "source-revision"
                        if mutation == "source_revision_document_id" else identity["revision_id"]
                    )
                    document_id = f"hostile-target-{mutation}"
                    await connection.execute(text(
                        "INSERT INTO molecular_documents("
                        "id,document_kind,name,current_revision_id,created_at) "
                        "VALUES (:id,'dna','hostile target',NULL,CURRENT_TIMESTAMP)"
                    ), {"id": document_id})
                    await connection.execute(text(
                        "UPDATE molecular_revisions SET document_id=:document_id WHERE id=:id"
                    ), {"document_id": document_id, "id": revision_id})
                elif mutation == "output_ordinal":
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_operation_outputs_update"
                    ))
                    await connection.execute(text(
                        "UPDATE molecular_operation_outputs SET position=7 WHERE id=:id"
                    ), {"id": identity["output_edge_id"]})
                else:
                    await connection.execute(text(
                        "DROP TRIGGER molbio_immutable_molecular_revisions_update"
                    ))
                    if mutation == "revision_number":
                        statement = "UPDATE molecular_revisions SET revision_number=2 WHERE id=:id"
                    elif mutation == "revision_change_kind":
                        statement = (
                            "UPDATE molecular_revisions SET change_kind='forged' WHERE id=:id"
                        )
                    elif mutation == "revision_snapshot":
                        statement = (
                            "UPDATE molecular_revisions "
                            "SET snapshot=json_set(snapshot,'$.sequence_type','rna') WHERE id=:id"
                        )
                    elif mutation == "revision_content":
                        statement = (
                            "UPDATE molecular_revisions SET content_sha256='" + "0" * 64
                            + "' WHERE id=:id"
                        )
                    else:
                        statement = (
                            "UPDATE molecular_revisions "
                            "SET snapshot=json_set(snapshot,'$.topology','circular') WHERE id=:id"
                        )
                    await connection.execute(text(statement), {"id": identity["revision_id"]})

            loaded = await client.get(
                f"/api/molbio/restriction/digests/{operation_id}"
            )
            assert loaded.status_code == 409
            assert loaded.json()["detail"]["code"] == "digest_result_integrity_error"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_idempotency_claims_replay_exactly_and_conflict_on_changed_request(
    tmp_path: Path,
) -> None:
    import asyncio

    for case in ("exact", "changed"):
        case_path = tmp_path / case
        case_path.mkdir()
        engine, sessions, digest = await _store(case_path)
        try:
            async with await _client(sessions) as preview_client:
                preview = await preview_client.post(
                    "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
                )
            base = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"],
                "idempotency_key": f"concurrent-{case}",
                "persistence_mode": "operation_and_fragments",
            }
            other = dict(base)
            if case == "changed":
                other["persistence_mode"] = "operation_only"
            async with await _client(sessions) as first_client, await _client(sessions) as second_client:
                first, second = await asyncio.gather(
                    first_client.post("/api/molbio/restriction/digests", json=base),
                    second_client.post("/api/molbio/restriction/digests", json=other),
                )
            if case == "exact":
                assert first.status_code == second.status_code == 200
                assert first.json() == second.json()
            else:
                assert sorted((first.status_code, second.status_code)) == [200, 409]
                conflict = first if first.status_code == 409 else second
                assert conflict.json()["detail"]["code"] == "idempotency_conflict"
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(MolecularOperation)) == 1
                assert await session.scalar(select(func.count()).select_from(RestrictionDigestResult)) == 1
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_save_rollback_injection_leaves_zero_partial_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = ("operation", "document", "revision", "edge", "result")
    for stage in stages:
        stage_path = tmp_path / stage
        stage_path.mkdir()
        engine, sessions, digest = await _store(stage_path)
        try:
            async with await _client(sessions) as client:
                preview = await client.post(
                    "/api/molbio/restriction/digests/simulate", json=_preview_request(digest)
                )
                assert preview.status_code == 200
                save = {
                    **_preview_request(digest),
                    "schema": "bms.molbio.restriction-digest-save-request.v1",
                    "simulation_sha256": preview.json()["simulation_sha256"],
                    "idempotency_key": f"rollback-{stage}",
                    "persistence_mode": "operation_and_fragments",
                }

                def fail_at(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError("injected persistence failure")

                monkeypatch.setattr(molbio_restriction, "_digest_persistence_stage_hook", fail_at)
                with pytest.raises(RuntimeError, match="injected persistence failure"):
                    await client.post("/api/molbio/restriction/digests", json=save)
            async with sessions() as session:
                for table in (
                    MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
                    RestrictionDigestResult,
                ):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0
                assert await session.scalar(select(func.count()).select_from(MolecularDocument)) == 1
                assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 1
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_save_rejects_inline_forged_digest_and_operation_only_has_no_outputs(tmp_path: Path) -> None:
    engine, sessions, digest = await _store(tmp_path)
    try:
        async with await _client(sessions) as client:
            inline = _preview_request(digest)
            inline["source"] = {"kind": "inline_dna", "name": "x", "dna": "GAATTC", "topology": "linear"}
            inline.update({
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": "0" * 64, "idempotency_key": "inline-save",
                "persistence_mode": "operation_only",
            })
            assert (await client.post("/api/molbio/restriction/digests", json=inline)).status_code == 422

            preview = await client.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
            forged = {
                **_preview_request(digest),
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": "0" * 64, "idempotency_key": "forged",
                "persistence_mode": "operation_only",
            }
            mismatch = await client.post("/api/molbio/restriction/digests", json=forged)
            assert mismatch.status_code == 409
            assert mismatch.json()["detail"]["code"] == "simulation_digest_mismatch"
            forged["simulation_sha256"] = preview.json()["simulation_sha256"]
            saved = await client.post("/api/molbio/restriction/digests", json=forged)
            assert saved.status_code == 200
            assert saved.json()["outputs"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_registers_digest_table_guards_and_foreign_keys(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    try:
        versions = await init_molbio_db(engine=engine)
        assert versions[-1] == "0007_restriction_digest_results"
        assert "restriction_digest_results" in IMMUTABLE_TABLES
        async with engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA foreign_key_check"))).all() == []
            triggers = {
                row[0] for row in (await connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='restriction_digest_results'"
                ))).all()
            }
            assert {
                "molbio_immutable_restriction_digest_results_update",
                "molbio_immutable_restriction_digest_results_delete",
                "molbio_restriction_digest_results_integrity_insert",
            } <= triggers
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_rejects_wrong_ledger_identity_and_attests_integrity_trigger(tmp_path: Path) -> None:
    from molbio_database import molbio_health
    from molbio_migrations import (
        RESTRICTION_DIGEST_MIGRATION_CHECKSUM,
        restriction_digest_integrity_trigger_sql,
        restriction_digest_migration_attestation,
    )
    from services.restriction_digest import resource_policy_receipt

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'wrong-ledger.db'}")
    try:
        await init_molbio_db(engine=engine)
        attestation = restriction_digest_migration_attestation()
        assert RESTRICTION_DIGEST_MIGRATION_CHECKSUM == (
            "b0d8186b42c98cf0ecd4fefb914c969fb94f9b1da99e95788256e2b2f4a5728b"
        )
        assert attestation["version"] == "0007_restriction_digest_results"
        assert attestation["objects"] == {
            "tables": ["restriction_digest_results"],
            "indexes": [
                "ix_restriction_digest_results_source_created",
                "sqlite_autoindex_restriction_digest_results_1",
                "sqlite_autoindex_restriction_digest_results_2",
            ],
            "triggers": [
                "molbio_immutable_restriction_digest_results_delete",
                "molbio_immutable_restriction_digest_results_update",
                "molbio_restriction_digest_results_integrity_insert",
            ],
        }
        health = await molbio_health(engine=engine)
        digest_readiness = health["restriction_digest"]
        assert digest_readiness["required"] is True
        assert digest_readiness["ready"] is True
        assert digest_readiness["migration"] == attestation
        policy = resource_policy_receipt().model_dump(mode="json", by_alias=True)
        assert digest_readiness["resource_policy"] == policy
        assert digest_readiness["resource_policy_sha256"] == (
            "6fdeb1cf2d0434aca78a03005c9ae4594a3ca1cc73b6802e0e8c08a5d2783e09"
        )
        assert digest_readiness["routes"] == [
            "POST /api/molbio/restriction/digests/simulate",
            "POST /api/molbio/restriction/digests",
            "GET /api/molbio/restriction/digests/{operation_id}",
        ]
        from readiness import _restriction_digest_readiness_is_exact

        assert _restriction_digest_readiness_is_exact(digest_readiness) is True
        for path, replacement in (
            (("resource_policy", "physical_cut_maximum"), 1),
            (("resource_policy_sha256",), "0" * 64),
            (("migration", "version"), "0006_project_plasmid_metadata"),
            (("routes",), []),
        ):
            mutant = json.loads(json.dumps(digest_readiness))
            cursor = mutant
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = replacement
            assert _restriction_digest_readiness_is_exact(mutant) is False
        assert RESTRICTION_DIGEST_MIGRATION_CHECKSUM != hashlib.sha256(
            restriction_digest_integrity_trigger_sql().encode("utf-8")
        ).hexdigest()
        async with engine.begin() as connection:
            await connection.execute(text(
                "UPDATE molbio_schema_migrations SET description='counterfeit' "
                "WHERE version='0007_restriction_digest_results'"
            ))
        with pytest.raises(RuntimeError, match="migration ledger mismatch"):
            await init_molbio_db(engine=engine)
        async with engine.begin() as connection:
            await connection.execute(text(
                "UPDATE molbio_schema_migrations SET description="
                "'immutable exact restriction digest results' "
                "WHERE version='0007_restriction_digest_results'"
            ))
            await connection.execute(text(
                "DROP TRIGGER molbio_restriction_digest_results_integrity_insert"
            ))
            await connection.execute(text(
                "CREATE TRIGGER molbio_restriction_digest_results_integrity_insert "
                "BEFORE INSERT ON restriction_digest_results BEGIN SELECT 1; END"
            ))
        health = await molbio_health(engine=engine)
        assert health["status"] == "degraded"
        assert health["database_schema_current"] is False
        async with engine.begin() as connection:
            await connection.execute(text(
                "DELETE FROM molbio_schema_migrations "
                "WHERE version='0007_restriction_digest_results'"
            ))
        with pytest.raises(RuntimeError, match="counterfeit restriction digest schema"):
            await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_and_health_reject_quoted_json_path_case_counterfeit(
    tmp_path: Path,
) -> None:
    from molbio_database import molbio_health
    from molbio_migrations import restriction_digest_integrity_trigger_sql

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'quoted-path.db'}")
    try:
        await init_molbio_db(engine=engine)
        counterfeit = restriction_digest_integrity_trigger_sql().replace(
            "$.simulation.simulation_sha256", "$.Simulation.simulation_sha256", 1,
        )
        async with engine.begin() as connection:
            await connection.execute(text(
                "DROP TRIGGER molbio_restriction_digest_results_integrity_insert"
            ))
            await connection.execute(text(counterfeit))

        health = await molbio_health(engine=engine)
        assert health["status"] == "degraded"
        assert health["database_schema_current"] is False

        async with engine.begin() as connection:
            await connection.execute(text(
                "DELETE FROM molbio_schema_migrations "
                "WHERE version='0007_restriction_digest_results'"
            ))
        with pytest.raises(RuntimeError, match="counterfeit restriction digest schema"):
            await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("counterfeit", [
    "wrong_declared_type",
    "wrong_primary_key",
    "missing_immutable_trigger",
    "counterfeit_immutable_trigger",
    "extra_index",
])
async def test_migration_rejects_counterfeit_attested_physical_schema_before_reuse(
    tmp_path: Path, counterfeit: str,
) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / f'{counterfeit}.db'}")
    try:
        await init_molbio_db(engine=engine)
        async with engine.begin() as connection:
            await connection.execute(text(
                "DELETE FROM molbio_schema_migrations "
                "WHERE version='0007_restriction_digest_results'"
            ))
            if counterfeit in {"wrong_declared_type", "wrong_primary_key"}:
                for trigger in (
                    "molbio_immutable_restriction_digest_results_delete",
                    "molbio_immutable_restriction_digest_results_update",
                    "molbio_restriction_digest_results_integrity_insert",
                ):
                    await connection.execute(text(f'DROP TRIGGER "{trigger}"'))
                await connection.execute(text(
                    "DROP INDEX ix_restriction_digest_results_source_created"
                ))
                await connection.execute(text("DROP TABLE restriction_digest_results"))
                id_declaration = (
                    "id VARCHAR(36) PRIMARY KEY NOT NULL"
                    if counterfeit == "wrong_declared_type"
                    else "id VARCHAR(36) NOT NULL UNIQUE"
                )
                result_type = "BLOB" if counterfeit == "wrong_declared_type" else "TEXT"
                await connection.execute(text(
                    "CREATE TABLE restriction_digest_results ("
                    f"{id_declaration},"
                    "operation_id VARCHAR(36) NOT NULL UNIQUE,"
                    "source_revision_id VARCHAR(36) NOT NULL,"
                    "catalog_id VARCHAR(128) NOT NULL,"
                    "catalog_sha256 VARCHAR(64) NOT NULL,"
                    "request_sha256 VARCHAR(64) NOT NULL,"
                    "result_sha256 VARCHAR(64) NOT NULL,"
                    f"result {result_type} NOT NULL,"
                    "created_at DATETIME NOT NULL,"
                    "FOREIGN KEY(operation_id) REFERENCES molecular_operations(id) "
                    "ON DELETE RESTRICT ON UPDATE NO ACTION,"
                    "FOREIGN KEY(source_revision_id) REFERENCES molecular_revisions(id) "
                    "ON DELETE RESTRICT ON UPDATE NO ACTION)"
                ))
                await connection.execute(text(
                    "CREATE INDEX ix_restriction_digest_results_source_created "
                    "ON restriction_digest_results(source_revision_id, created_at)"
                ))
            elif counterfeit == "missing_immutable_trigger":
                await connection.execute(text(
                    "DROP TRIGGER molbio_immutable_restriction_digest_results_update"
                ))
            elif counterfeit == "counterfeit_immutable_trigger":
                await connection.execute(text(
                    "DROP TRIGGER molbio_immutable_restriction_digest_results_update"
                ))
                await connection.execute(text(
                    "CREATE TRIGGER molbio_immutable_restriction_digest_results_update "
                    "BEFORE UPDATE ON restriction_digest_results BEGIN SELECT 1; END"
                ))
            else:
                await connection.execute(text(
                    "CREATE INDEX counterfeit_restriction_digest_catalog "
                    "ON restriction_digest_results(catalog_id)"
                ))

        with pytest.raises(RuntimeError, match="counterfeit restriction digest"):
            await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()


def test_restriction_digest_snapshot_is_canonical_and_direct_sql_mutation_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "direct.db"
    import asyncio
    from molbio_migrations import (
        restriction_digest_json_equal,
        validate_restriction_digest_result,
    )
    from services.restriction_digest_save_receipt import (
        validate_persisted_save_request_receipt,
    )

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    asyncio.run(init_molbio_db(engine=engine))
    asyncio.run(engine.dispose())
    sequence = "TTGAATTCAA"
    sequence_sha = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    operation_id = "operation-direct"
    source_id = "source-direct"
    document_id = "document-direct"
    simulation = _simulate(
        sequence, ("EcoRI",), source_receipt={
            "kind": "molecular_revision", "name": "direct",
            "sequence_id": document_id, "revision_id": source_id, "revision_number": 1,
            "content_sha256": sequence_sha, "content_length": len(sequence),
            "topology": "linear",
        },
    )
    snapshot = {
        "schema": "bms.molbio.restriction-digest-saved-result.v1",
        "operation_id": operation_id, "source_revision_id": source_id,
        "catalog_id": CATALOG_ID, "catalog_sha256": CATALOG_SHA,
        "request_sha256": simulation.request_sha256,
        "result_sha256": simulation.simulation_sha256,
        "simulation": simulation.model_dump(mode="json", by_alias=True),
        "outputs": [],
    }
    canonical = rfc8785.dumps(snapshot).decode("utf-8")
    mutant = json.loads(canonical)
    mutant["operation_id"] = "other-operation"
    mutant["simulation"]["fully_rehashed_extra_field"] = True
    unsigned = dict(mutant["simulation"])
    unsigned.pop("simulation_sha256")
    mutant_sha = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    mutant["simulation"]["simulation_sha256"] = mutant_sha
    mutant["result_sha256"] = mutant_sha
    with sqlite3.connect(database) as connection:
        connection.create_function(
            "bms_restriction_digest_result_valid", 7,
            validate_restriction_digest_result, deterministic=True,
        )
        connection.create_function(
            "bms_restriction_digest_json_equal", 2,
            restriction_digest_json_equal, deterministic=True,
        )
        connection.create_function(
            "bms_restriction_digest_save_receipt_valid", 3,
            validate_persisted_save_request_receipt, deterministic=True,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO molecular_documents(id,document_kind,name,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (document_id, "dna", "direct"),
        )
        connection.execute(
            "INSERT INTO molecular_revisions(id,document_id,revision_number,change_kind,content_sha256,content_length,snapshot,provenance,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (source_id, document_id, 1, "created", sequence_sha, len(sequence),
             rfc8785.dumps({"is_circular": False, "sequence": sequence, "sequence_type": "dna"}).decode(), '{}'),
        )
        for identity, result_sha, receipt_mutation, fingerprint_override in (
            (operation_id, simulation.simulation_sha256, None, None),
            ("other-operation", mutant_sha, None, None),
            ("arbitrary-operation", simulation.simulation_sha256, None, "a" * 64),
            ("key-mismatch-operation", simulation.simulation_sha256, "key", None),
            ("scientific-mismatch-operation", simulation.simulation_sha256, "catalog", None),
            ("extra-receipt-operation", simulation.simulation_sha256, "extra", None),
            ("noncanonical-receipt-operation", simulation.simulation_sha256, "noncanonical", None),
        ):
            key = f"key-{identity}"
            receipt_document = {
                "schema": "bms.molbio.restriction-digest-save-request.v1",
                "source": {
                    "kind": "molecular_revision", "sequence_id": document_id,
                    "revision_id": source_id, "expected_content_sha256": sequence_sha,
                    "topology": None,
                },
                "catalog": {
                    "catalog_id": CATALOG_ID,
                    "expected_catalog_sha256": CATALOG_SHA,
                },
                "enzyme_ids": ["EcoRI"], "simulation_sha256": result_sha,
                "idempotency_key": key, "persistence_mode": "operation_only",
                "fragment_name_prefix": None,
            }
            if receipt_mutation == "key":
                receipt_document["idempotency_key"] = "different-key"
            elif receipt_mutation == "catalog":
                receipt_document["catalog"]["catalog_id"] = "different-catalog"
            elif receipt_mutation == "extra":
                receipt_document["extra"] = True
            receipt = (
                json.dumps(receipt_document, indent=2)
                if receipt_mutation == "noncanonical"
                else rfc8785.dumps(receipt_document).decode()
            )
            fingerprint = fingerprint_override or hashlib.sha256(receipt.encode()).hexdigest()
            connection.execute(
                "INSERT INTO molecular_operations("
                "id,operation_kind,implementation,implementation_version,status,parameters,"
                "warnings,provenance,idempotency_key,request_fingerprint,created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    identity,
                    "restriction_digest",
                    "services.restriction_digest.simulate_digest",
                    simulation.digest_algorithm_version,
                    "completed",
                    rfc8785.dumps({
                        "schema": "bms.molbio.restriction-digest-operation-parameters.v1",
                        "selected_enzyme_ids": ["EcoRI"],
                        "persistence_mode": "operation_only",
                        "fragment_name_prefix": None,
                        "simulation_sha256": result_sha,
                        "save_request_receipt": receipt,
                    }).decode(),
                    rfc8785.dumps(list(simulation.warnings)).decode(),
                    rfc8785.dumps({
                        "source_revision_id": source_id,
                        "catalog_id": CATALOG_ID,
                        "catalog_sha256": CATALOG_SHA,
                        "request_sha256": simulation.request_sha256,
                    }).decode(),
                    key,
                    fingerprint,
                ),
            )
            connection.execute(
                "INSERT INTO molecular_operation_inputs("
                "id,operation_id,revision_id,role,position,snapshot"
                ") VALUES (?,?,?,?,?,?)",
                (
                    f"input-{identity}", identity, source_id, "digest_source", 0,
                    rfc8785.dumps({
                        "content_sha256": sequence_sha,
                        "name": "direct",
                        "sequence_id": document_id,
                    }).decode(),
                ),
            )
        connection.execute(
            "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            ("result-direct", operation_id, source_id, CATALOG_ID, CATALOG_SHA,
             simulation.request_sha256, simulation.simulation_sha256, canonical),
        )
        for hostile_operation in (
            "arbitrary-operation",
            "key-mismatch-operation",
            "scientific-mismatch-operation",
            "extra-receipt-operation",
            "noncanonical-receipt-operation",
        ):
            hostile_snapshot = {**snapshot, "operation_id": hostile_operation}
            with pytest.raises(sqlite3.IntegrityError, match="integrity"):
                connection.execute(
                    "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (f"result-{hostile_operation}", hostile_operation, source_id,
                     CATALOG_ID, CATALOG_SHA, simulation.request_sha256,
                     simulation.simulation_sha256,
                     rfc8785.dumps(hostile_snapshot).decode()),
                )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE restriction_digest_results SET catalog_id='mutant' WHERE id='result-direct'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM restriction_digest_results WHERE id='result-direct'")

        with pytest.raises(sqlite3.IntegrityError, match="integrity"):
            connection.execute(
                "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                ("result-mutant", "other-operation", source_id, CATALOG_ID, CATALOG_SHA,
                 simulation.request_sha256, mutant_sha, rfc8785.dumps(mutant).decode()),
            )
