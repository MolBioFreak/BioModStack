"""Lane D isolated scientific semantics and work-count regressions."""
from __future__ import annotations

import asyncio
import gzip
import random
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event

from routers import molbio_ops as api
from services import molbio_ops as ops
from services import sequence_alignment as alignment
from services import annotation_sources as sources
from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import Primer


@pytest.mark.parametrize("circular", [False, True])
def test_ambiguous_pcr_materializes_no_products(monkeypatch, circular):
    products = []
    original = ops.PCRProductResult
    def tracked(*args, **kwargs):
        products.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(ops, "PCRProductResult", tracked)
    with pytest.raises(ValueError, match="Ambiguous PCR"):
        ops.pcr_product("ACGT" * 200, "ACGTACGT", "ACGTACGT", circular=circular)
    assert products == []


def test_unique_pcr_materializes_once_and_preserves_overhangs(monkeypatch):
    template = "ATGCGTACGTTAGCTAGCTAGGCTAACCGGTTACGATCGATCGTACGTTAGC"
    original = ops.PCRProductResult
    products = []
    def tracked(*args, **kwargs):
        products.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(ops, "PCRProductResult", tracked)
    result = ops.pcr_product(template, "AAAA" + template[:12], "CCCC" + ops.reverse_complement(template[-12:]))
    assert result.sequence == "AAAA" + template + "GGGG"
    assert (result.start, result.end, result.wraps_origin) == (0, len(template), False)
    assert len(products) == 1


def test_binding_search_canonicalizes_template_once(monkeypatch):
    original = ops.clean_sequence
    template = "A" * 2000
    template_calls = 0
    def tracked(sequence):
        nonlocal template_calls
        template_calls += sequence == template
        return original(sequence)
    monkeypatch.setattr(ops, "clean_sequence", tracked)
    assert ops.resolve_primer_binding_sites(template, "C" * 30) == []
    assert template_calls == 1


@pytest.mark.parametrize("mode", ["global", "local", "placement"])
@pytest.mark.parametrize("strand,expected", [("auto", 2), ("forward", 1)])
def test_alignment_expands_once_per_orientation(monkeypatch, mode, strand, expected):
    original = alignment._alignment_columns
    calls = []
    def tracked(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(alignment, "_alignment_columns", tracked)
    result = alignment.align_sequences("ATGCCGTAACGTTAGC", "CCGTAACGT", alignment.AlignmentSettings(mode=mode, strand=strand))
    assert result["matches"] >= 8
    assert len(calls) == expected


def test_design_qc_only_shortlisted_candidates(monkeypatch):
    rng = random.Random(114)
    template = "".join(rng.choice("ACGT") for _ in range(420))
    request = api.PrimerDesignRequest(sequence=template, sequence_type="dna", target_start=130,
        target_end=270, flank_search_span=100, primer_min_length=12, primer_max_length=13,
        gc_min_percent=0, gc_max_percent=100, gc_clamp_min=0, max_poly_x=20,
        tm_max_delta_c=100, product_min_length=40, product_max_length=1000)
    original = api._evaluate_primer_qc_canonical
    calls = []
    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(api, "_evaluate_primer_qc_canonical", tracked)
    result = api.design_primer_pairs_for_request(request, "fixture")
    assert result.pair_count > 0
    assert len(calls) == 96
    for pair in result.pairs:
        assert pair.forward.sequence in calls and pair.reverse.sequence in calls
        assert pair.forward.binding_site_count is not None


def test_design_rejects_bad_overhang_even_without_candidates():
    request = api.PrimerDesignRequest(sequence="A" * 100, target_start=0, target_end=100,
                                     overhang_forward="NOT-DNA!")
    with pytest.raises((ValueError, api.HTTPException)):
        api.design_primer_pairs_for_request(request, None)


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False
    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [None, "1000", "1"])
async def test_stream_admission_stops_before_full_body(declared):
    stream = CountingStream([b"A" * 6] * 20)
    headers = {"content-length": declared} if declared else {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers=headers, stream=stream))) as client:
        with pytest.raises(sources.AnnotationSourceResponseError, match="size limit"):
            await sources._bounded_get(client, sources.NCBI_EFETCH_URL, max_bytes=10)
    assert stream.closed
    assert stream.reads == (0 if declared == "1000" else 2)


@pytest.mark.asyncio
async def test_stream_decodes_compression_once():
    content = b"LOCUS TEST\nORIGIN\n 1 acgt\n//\n"
    stream = CountingStream([gzip.compress(content)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream))) as client:
        artifact = await sources.fetch_ncbi_genbank("J01749.1", client=client)
    assert artifact.content == content.decode()


@pytest.mark.asyncio
async def test_qc_batch_preserves_public_metrics_and_pairwise():
    from dataclasses import asdict
    primers = ["ACGTACGTACGT", "TGCATGCATGCA"]
    template = "ACGTACGTACGTNNNNTGCATGCATGCA"
    request = api.PrimerQcRequest(primers=[api.PrimerQcEntry(id=str(i), sequence=primer) for i, primer in enumerate(primers)],
                                 template_sequence=template, template_is_circular=True, include_pairwise=True)
    result = await api.calculate_primer_qc(request)
    for entry, primer in zip(result.primers, primers):
        expected = api.evaluate_primer_qc(primer, template_sequence=template, circular_template=True)
        assert entry.qc.model_dump() == asdict(expected)
    expected_pair = api.evaluate_primer_pair_qc(*primers)
    assert len(result.pairwise) == 1
    assert result.pairwise[0]["heterodimer_complement"] == expected_pair.heterodimer_complement
    assert result.pairwise[0]["three_prime_heterodimer"] == expected_pair.three_prime_heterodimer


@pytest.mark.asyncio
async def test_qc_offload_uses_existing_bounded_limiter(monkeypatch):
    import anyio.to_thread
    limiter = anyio.to_thread.current_default_thread_limiter()
    prior = limiter.total_tokens
    limiter.total_tokens = 2
    lock = threading.Lock()
    active = maximum = 0
    worker_threads = set()
    def work(request):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            worker_threads.add(threading.get_ident())
        try:
            time.sleep(0.02)
            return "done"
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(api, "_calculate_primer_qc", work)
    try:
        result = await asyncio.gather(*(api.calculate_primer_qc(SimpleNamespace()) for _ in range(6)))
    finally:
        limiter.total_tokens = prior
    assert result == ["done"] * 6
    assert maximum == 2
    assert threading.get_ident() not in worker_threads


@pytest.mark.asyncio
async def test_cancelled_qc_retains_worker_admission_until_completion(monkeypatch):
    import anyio
    import anyio.to_thread
    limiter = anyio.to_thread.current_default_thread_limiter()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    scopes = []
    loop = asyncio.get_running_loop()
    def work(request):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test worker was not released"
        finished.set()
        return "done"
    async def run():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await api.calculate_primer_qc(SimpleNamespace())
    monkeypatch.setattr(api, "_calculate_primer_qc", work)
    prior_borrowed = limiter.borrowed_tokens
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            await asyncio.wait_for(started.wait(), 5)
            scopes[0].cancel()
            await anyio.sleep(0)
            assert limiter.borrowed_tokens == prior_borrowed + 1
            assert not finished.is_set()
            release.set()
    finally:
        release.set()
    assert finished.is_set()
    assert limiter.borrowed_tokens == prior_borrowed


@pytest.mark.asyncio
async def test_rna_fold_offloads_unchanged_arguments(monkeypatch):
    from routers import rna_structure as rna
    main_thread = threading.get_ident()
    def work(name, source_id, sequence, settings, partition):
        assert threading.get_ident() != main_thread
        assert (name, source_id, sequence, partition) == ("draft", None, "ACGU", False)
        return "folded"
    monkeypatch.setattr(rna, "_run_structure_analysis", work)
    assert await rna.fold_rna(rna.RnaFoldRequest(name="draft", sequence="ACGU", include_partition=False), None) == "folded"


@pytest.mark.asyncio
async def test_native_rna_fold_and_partition_match_service():
    from routers import rna_structure as rna
    from services.rna_structure import analyze_rna_structure, RnaStructureSettings
    sequence = "GGGAAACCCGGGAAACCC"
    settings = rna.RnaStructureSettingsSchema(temperature_c=28, no_lonely_pairs=True)
    expected = analyze_rna_structure(sequence, RnaStructureSettings(temperature_c=28, no_lonely_pairs=True), include_partition=True)
    fold = await rna.fold_rna(rna.RnaFoldRequest(sequence=sequence, settings=settings), None)
    partition = await rna.partition_rna(rna.RnaStructureRequest(sequence=sequence, settings=settings), None)
    assert fold.model_dump() == partition.model_dump()
    assert fold.mfe.dot_bracket == expected["mfe"]["dot_bracket"]
    assert fold.mfe.energy_kcal_mol == expected["mfe"]["energy_kcal_mol"]
    assert len(fold.bases) == len(sequence)
    with pytest.raises(api.HTTPException, match="limited"):
        await rna.fold_rna(rna.RnaFoldRequest(sequence="A" * 1201), None)


@pytest.mark.asyncio
async def test_import_error_does_not_reparse(monkeypatch):
    error = api.SequenceImportInputError("immutable content mismatch", code="invalid_existing_revision")
    monkeypatch.setattr(api, "commit_sequence_import", AsyncMock(side_effect=error))
    def forbidden(*args):
        raise AssertionError("must not parse again")
    monkeypatch.setattr(api, "build_sequence_import_preview", forbidden)
    session = SimpleNamespace(rollback=AsyncMock())
    with pytest.raises(api.HTTPException) as caught:
        await api.commit_sequence_import_route(SimpleNamespace(), None, session)
    assert caught.value.detail["code"] == "invalid_existing_revision"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_primer_sql_literal_unicode_filter_and_pages(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'primers.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with sessions() as session:
            for i, name in enumerate(["ÉDIT_%", "édit_%", "other", "EDITxx"]):
                session.add(Primer(id=str(i), name=name, sequence="ACGTACGTACGT", sequence_type="dna", length=12))
            await session.commit()
        async with sessions() as session:
            first = await api.list_primers(search="ÉDIT_%", session=session, limit=1)
            second = await api.list_primers(search="ÉDIT_%", session=session, limit=1, offset=1)
            assert {first[0].name, second[0].name} == {"ÉDIT_%", "édit_%"}
            assert await api.list_primers(search="ÉDIT_%", session=session, limit=1, offset=2) == []
            assert len(await api.list_primers(session=session)) == 4
        assert len(queries) == 4
        assert all("LIMIT" in query and "OFFSET" in query for query in queries[:3])
        assert all("bms_unicode_lower" in query for query in queries[:3])
    finally:
        await engine.dispose()
