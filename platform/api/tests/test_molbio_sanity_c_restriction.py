"""Restriction vertical regressions: work counts and source/worker ownership."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from routers import molbio_restriction as routes
from services import restriction_analysis as analysis
from services.restriction_catalog import catalog_authority
from services.restriction_digest import DigestGeometryError, simulate_digest_canonical


def request():
    view = catalog_authority.require()
    return routes.AnalysisRequest.model_validate({
        "schema": "bms.molbio.restriction-analysis-request.v1",
        "source": {"kind": "inline_dna", "name": " example ", "dna": "ttgaattcaa", "topology": "linear"},
        "catalog": {"catalog_id": view.catalog_id, "expected_catalog_sha256": view.content_sha256},
        "scope": {"mode": "explicit", "enzyme_ids": ["EcoRI"], "commercial_only": False},
        "regions": [], "include_possible_sites": True, "methylation_policy": "report_only",
    })


@pytest.mark.parametrize("digest", [False, True])
def test_routed_source_normalizes_and_hashes_dna_once(monkeypatch, digest):
    payload = request()
    normalize_calls = []
    dna_hash_calls = []
    normalize = analysis.normalize_dna
    sha256 = hashlib.sha256

    def counted_normalize(sequence):
        normalize_calls.append(sequence)
        return normalize(sequence)

    def counted_hash(data=b"", *args, **kwargs):
        if data == b"TTGAATTCAA":
            dna_hash_calls.append(data)
        return sha256(data, *args, **kwargs)

    monkeypatch.setattr(routes, "normalize_dna", counted_normalize)
    monkeypatch.setattr(analysis, "normalize_dna", counted_normalize)
    monkeypatch.setattr(hashlib, "sha256", counted_hash)
    if digest:
        payload = routes.DigestSimulationRequest.model_validate({
            "schema": "bms.molbio.restriction-digest-simulation-request.v1",
            "source": payload.source, "catalog": payload.catalog, "enzyme_ids": ["EcoRI"],
        })
        output = routes._complete_digest_pipeline(payload=payload, authority=catalog_authority, resolved_revision=None)
        assert output.simulation.source.name == "example"
    else:
        output = routes._complete_analysis_pipeline(payload=payload, authority=catalog_authority, resolved_revision=None)
        assert output.response.source.name == "example"
    assert normalize_calls == ["ttgaattcaa"]
    assert len(dna_hash_calls) == 1


def test_public_analysis_still_validates_untrusted_dna():
    view = catalog_authority.require()
    with pytest.raises(analysis.InvalidDNAError):
        analysis.analyze_sequence(sequence="TT GAATTC", topology="linear", catalog=view, records=[view.by_id["EcoRI"]])


@pytest.mark.asyncio
async def test_work_budget_413_is_typed_before_scan(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_SCAN_WORK", 1)
    def forbidden_scan(*args):
        raise AssertionError("scan must not run before admission")
    monkeypatch.setattr(analysis, "_scan", forbidden_scan)
    with pytest.raises(HTTPException) as exc:
        await routes.analyze_restriction_sites(request(), catalog_authority, object())
    assert exc.value.status_code == 413
    assert exc.value.detail == {"code": "request_too_large", "message": "restriction analysis request is too large", "budget": "scan_work", "limit": 1, "observed": 30}


@pytest.mark.asyncio
async def test_busy_saved_read_does_zero_sql(monkeypatch):
    capacity = threading.BoundedSemaphore(1)
    capacity.acquire()
    monkeypatch.setattr(routes, "_analysis_capacity", capacity)
    class NoSQL:
        async def execute(self, *_args):
            raise AssertionError("busy saved read must not hydrate evidence")
    with pytest.raises(HTTPException) as exc:
        await routes._load_saved_digest(NoSQL(), "fixture")
    assert exc.value.status_code == 503
    capacity.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("lookup failed"), asyncio.CancelledError()])
async def test_saved_hydration_failure_releases_admission(monkeypatch, error):
    capacity = threading.BoundedSemaphore(1)
    monkeypatch.setattr(routes, "_analysis_capacity", capacity)
    async def failed(*args):
        raise error
    monkeypatch.setattr(routes, "_load_saved_digest_snapshot", failed)
    with pytest.raises(type(error)):
        await routes._load_saved_digest(object(), "fixture")
    assert capacity.acquire(blocking=False)
    capacity.release()


def test_catalog_page_reads_only_page_plus_lookahead(monkeypatch):
    view = catalog_authority.require()
    visits = []
    class CountedRecords:
        def __iter__(self):
            for record in view.ordered_records:
                visits.append(record.enzyme_id)
                yield record
    limited_view = replace(view, ordered_records=CountedRecords())
    monkeypatch.setattr(routes, "_require_view", lambda authority: limited_view)
    page = routes.list_catalog(Request({"type": "http", "query_string": b"limit=2"}), limit=2, authority=catalog_authority)
    assert len(page.items) == 2
    assert page.next_cursor
    assert len(visits) == 3


@pytest.mark.parametrize("topology", ["linear", "circular"])
def test_overlong_digest_is_distinct_from_supported_uncut(topology):
    view = catalog_authority.require()
    with pytest.raises(DigestGeometryError) as exc:
        simulate_digest_canonical(sequence="AAA", topology=topology, catalog=view, records=[view.by_id["EcoRI"]], selected_enzyme_ids=["EcoRI"], source_receipt={"kind": "inline_dna", "name": "short", "sequence_id": None, "revision_id": None, "revision_number": None, "content_sha256": hashlib.sha256(b"AAA").hexdigest(), "content_length": 3, "topology": topology}, catalog_receipt=routes._receipt(catalog_authority).model_dump(mode="json", by_alias=True))
    assert exc.value.code == "recognition_motif_longer_than_molecule"
