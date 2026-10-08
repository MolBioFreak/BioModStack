from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import rfc8785
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text

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
    assert linear.simulation_sha256 == hashlib.sha256(linear.canonical_unsigned_bytes()).hexdigest()


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
    assert len(circular.cleavages) == len(circular.fragments) == 2
    assert sum(len(fragment.top_strand_sequence) for fragment in circular.fragments) == len(sequence)
    assert any(fragment.wraps_origin for fragment in circular.fragments)


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


async def _store(tmp_path: Path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'digest.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    sequence = "TTGAATTCAA"
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    async with sessions.begin() as session:
        session.add(MolecularDocument(
            id="source-document", document_kind="dna", name="source", current_revision_id=None,
        ))
        session.add(MolecularRevision(
            id="source-revision", document_id="source-document", revision_number=1,
            change_kind="created", content_sha256=digest, content_length=len(sequence),
            snapshot={"sequence_type": "dna", "sequence": sequence, "is_circular": False},
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
            assert saved.status_code == 200
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
        assert RESTRICTION_DIGEST_MIGRATION_CHECKSUM == hashlib.sha256(
            rfc8785.dumps(attestation)
        ).hexdigest()
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
        assert digest_readiness["resource_policy_sha256"] == hashlib.sha256(
            rfc8785.dumps(policy)
        ).hexdigest()
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
        with pytest.raises(RuntimeError, match="counterfeit restriction digest trigger"):
            await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()


def test_restriction_digest_snapshot_is_canonical_and_direct_sql_mutation_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "direct.db"
    import asyncio
    from molbio_migrations import validate_restriction_digest_result

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
    with sqlite3.connect(database) as connection:
        connection.create_function(
            "bms_restriction_digest_result_valid", 7,
            validate_restriction_digest_result, deterministic=True,
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
        for identity in (operation_id, "other-operation"):
            connection.execute(
                "INSERT INTO molecular_operations(id,operation_kind,implementation,status,parameters,warnings,provenance,created_at) VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (identity, "restriction_digest", "bms", "completed", '{}', '[]', '{}'),
            )
        canonical = rfc8785.dumps(snapshot).decode("utf-8")
        connection.execute(
            "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            ("result-direct", operation_id, source_id, CATALOG_ID, CATALOG_SHA,
             simulation.request_sha256, simulation.simulation_sha256, canonical),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE restriction_digest_results SET catalog_id='mutant' WHERE id='result-direct'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM restriction_digest_results WHERE id='result-direct'")

        mutant = json.loads(canonical)
        mutant["operation_id"] = "other-operation"
        mutant["simulation"]["fully_rehashed_extra_field"] = True
        unsigned = dict(mutant["simulation"])
        unsigned.pop("simulation_sha256")
        mutant_sha = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
        mutant["simulation"]["simulation_sha256"] = mutant_sha
        mutant["result_sha256"] = mutant_sha
        with pytest.raises(sqlite3.IntegrityError, match="integrity"):
            connection.execute(
                "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                ("result-mutant", "other-operation", source_id, CATALOG_ID, CATALOG_SHA,
                 simulation.request_sha256, mutant_sha, rfc8785.dumps(mutant).decode()),
            )

        malformed_outputs = json.loads(canonical)
        malformed_outputs["operation_id"] = "other-operation"
        fragment = simulation.fragments[0]
        sequence_bytes = fragment.top_strand_sequence.encode("ascii")
        malformed_outputs["outputs"] = [{
            "fragment_index": 0,
            "document_id": "forged-document",
            "revision_id": "forged-revision",
            "output_edge_id": "forged-edge",
            "name": "forged fragment",
            "topology": fragment.topology,
            "content_sha256": hashlib.sha256(sequence_bytes).hexdigest(),
            "content_length": len(sequence_bytes),
        }]
        with pytest.raises(sqlite3.IntegrityError, match="integrity"):
            connection.execute(
                "INSERT INTO restriction_digest_results(id,operation_id,source_revision_id,catalog_id,catalog_sha256,request_sha256,result_sha256,result,created_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                ("result-output-mutant", "other-operation", source_id, CATALOG_ID, CATALOG_SHA,
                 simulation.request_sha256, simulation.simulation_sha256,
                 rfc8785.dumps(malformed_outputs).decode()),
            )
