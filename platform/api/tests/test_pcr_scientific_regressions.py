"""PCR prediction acceptance: independently checked geometry, sequence and custody."""
from dataclasses import asdict

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from pydna.amplify import Anneal
from pydna.dseqrecord import Dseqrecord
from pydna.primer import Primer

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory
from molbio_models import PCRExperimentRevision, NucleotideSequence
from routers import molbio_ops as api
from services.molbio_ops import pcr_product, resolve_primer_binding_sites, reverse_complement as rc

T = 'AAGACTTCATTATGAGGCACAGAGCCACTTGGTTCGCACTTCGATGCCCCGAGATCTAGGTGGTATCCGATCATAAAAAACGTGTTTAGGCTGCGTTATTTGAAAGCAGTCTGCGTCTAC'


@pytest.mark.parametrize('forward,reverse,circular,expected', [
    (T[:20], rc(T[-20:]), False, T),
    ('GGGGG'+T[:20], 'AAAAA'+rc(T[-20:]), False, 'GGGGG'+T+'TTTTT'),
    (T[100:], rc(T[40:60]), True, T[100:]+T[:60]),
    (T[20:40], rc(T[15:35]), True, T[20:]+T[:35]),
    (T[:20], rc(T[-20:]), True, T),
])
def test_exact_products_agree_with_independent_native_model(forward, reverse, circular, expected):
    product = pcr_product(T, forward, reverse, circular=circular)
    assert product.sequence == expected
    assert product.length == len(expected)
    assert product.sequence.startswith(forward) and product.sequence.endswith(rc(reverse))
    native = Anneal([Primer(forward), Primer(reverse)], Dseqrecord(T, circular=circular), limit=8).products
    assert [str(p.seq) for p in native] == [expected]
    if circular and expected == T:
        assert (product.start, product.end, product.wraps_origin) == (0, len(T), False)


def test_outward_linear_primers_do_not_fabricate_a_short_product():
    with pytest.raises(ValueError, match='3′ ends do not face'):
        pcr_product(T, T[20:40], rc(T[15:35]))
    assert Anneal([Primer(T[20:40]), Primer(rc(T[15:35]))], Dseqrecord(T), limit=8).products == []


def test_primer_bases_are_incorporated_and_internal_ambiguity_is_preserved():
    template = 'N'+T[1:50]+'N'+T[51:-1]+'N'
    product = pcr_product(template, T[:20], rc(T[-20:]))
    assert product.sequence == T[:50]+'N'+T[51:]
    # A degenerate 5′ primer base represents the primer pool, not a template edit.
    assert pcr_product(T, 'N'+T[1:20], rc(T[-20:])).sequence == 'N'+T[1:]


def test_per_site_suffixes_are_enumerated_and_explicit_selection_is_validated():
    f = 'AG'+T[:20]
    template = f+'TTTTT'+'CC'+T[:20]+'GGGGG'+T[20:]
    sites = resolve_primer_binding_sites(template, f, min_anneal_length=20)
    assert [(s.start, s.anneal_length) for s in sites] == [(0,22),(29,20)]
    with pytest.raises(ValueError, match='Ambiguous'):
        pcr_product(template, f, rc(T[-20:]), min_anneal_length=20)
    expected = ['AG'+template[29:], template]
    native = Anneal([Primer(f), Primer(rc(T[-20:]))], Dseqrecord(template), limit=20).products
    assert sorted(str(p.seq) for p in native) == sorted(expected)
    selected = pcr_product(template, f, rc(T[-20:]), min_anneal_length=20, forward_binding=(29,20))
    assert selected.sequence == expected[0] and selected.length == 127
    assert pcr_product(template, f, rc(T[-22:]), min_anneal_length=21).sequence == template
    with pytest.raises(ValueError, match='not a maximal qualifying'):
        pcr_product(template, f, rc(T[-20:]), forward_binding=(28,20))
    # A threshold is never silently reduced to fit a shorter primer.
    assert resolve_primer_binding_sites(T, T[:12], min_anneal_length=15) == []


@pytest.mark.parametrize('extra', [{'unknown_setting': 1}, {'min_anneal_length': 7}, {'schema_version':'pcr-v1'}, {'tm_settings': {'unsupported_parameter': 7}}, {'reaction_settings': {'guess': 3}}, {'cycling_assumptions': {'cycles': 0}}, {'forward_binding': {'start': 0, 'anneal_length':20, 'ignored': True}}])
def test_prediction_settings_fail_closed(extra):
    with pytest.raises(ValidationError):
        api.PCRRequest(sequence=T, primer_fwd=T[:20], primer_rev=rc(T[-20:]), **extra)


@pytest.mark.asyncio
async def test_http_rejects_invalid_geometry_rna_and_ambiguous_sites():
    app = FastAPI(); app.include_router(api.router)
    async def no_db(): yield None
    app.dependency_overrides[get_molbio_session] = no_db
    f = 'AG'+T[:20]
    payloads = [
        ({'sequence':T, 'primer_fwd':T[20:40], 'primer_rev':rc(T[15:35])}, '3′ ends'),
        ({'sequence':T.replace('T','U'), 'sequence_type':'rna', 'primer_fwd':T[:20], 'primer_rev':rc(T[-20:])}, 'cDNA'),
        ({'sequence':f+'TTTTTCC'+T[:20]+'GGGGG'+T[20:], 'primer_fwd':f, 'primer_rev':rc(T[-20:])}, 'Ambiguous'),
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for payload, message in payloads:
            response = await client.post('/api/molbio/pcr', json={**payload,'save':False,'persist_experiment':False})
            assert response.status_code == 400, response.text
            assert message in response.json()['detail']


@pytest.mark.asyncio
async def test_preview_save_and_exact_reopen_preserve_footprints_tm_and_product(tmp_path):
    engine = create_molbio_engine(f'sqlite+aiosqlite:///{tmp_path / "pcr.db"}')
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    app = FastAPI(); app.include_router(api.router)
    async def isolated_session():
        async with sessions() as session: yield session
    app.dependency_overrides[get_molbio_session] = isolated_session
    f = 'GCGCGCGCGCGCGCGCGCGC'+T[:20]
    payload = {'sequence':'N'+T[1:], 'primer_fwd':f, 'primer_rev':rc(T[-20:]), 'min_anneal_length':20, 'forward_binding':{'start':0,'anneal_length':20}}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            preview_response = await client.post('/api/molbio/pcr', json={**payload,'save':False,'persist_experiment':False})
            saved_response = await client.post('/api/molbio/pcr', json={**payload,'save':True,'persist_experiment':True,'idempotency_key':'pcr-science'})
            assert preview_response.status_code == saved_response.status_code == 200, saved_response.text
            preview, saved = preview_response.json(), saved_response.json()
            assert preview['product'] == saved['product']
            assert saved['product']['sequence'] == f+T[20:]
            primer = saved['product']['forward_primer']
            assert primer['annealing_sequence'] == T[:20]
            assert primer['annealing_tm']['tm'] == api.calculate_primer_tm_result(T[:20], 'dna').tm
            assert primer['full_oligo_tm']['tm'] == api.calculate_primer_tm_result(f, 'dna').tm
            assert primer['annealing_tm']['tm'] != primer['full_oligo_tm']['tm']
            exact = await client.get(f"/api/molbio/pcr-experiments/{saved['experiment_id']}/revisions/{saved['experiment_revision_id']}")
            assert exact.status_code == 200, exact.text
            receipt = exact.json()
            assert receipt['forward_primer_snapshot']['annealing_tm'] == primer['annealing_tm']
            assert receipt['forward_primer_snapshot']['full_oligo_tm'] == primer['full_oligo_tm']
            assert receipt['tm_snapshot']['search_settings']['min_anneal_length'] == 20
            assert receipt['product_snapshot']['sequence'] == saved['product']['sequence']
            replay = await client.post('/api/molbio/pcr', json={**payload,'save':True,'persist_experiment':True,'idempotency_key':'pcr-science'})
            assert replay.json()['reused'] and replay.json()['product'] == preview['product']
    finally:
        await engine.dispose()


def test_public_request_uses_closed_canonical_tm_and_metadata_schemas():
    schema = api.PCRRequest.model_json_schema()
    assert schema['properties']['tm_settings']['$ref'].endswith('/PrimerTmSettings')
    assert schema['$defs']['PrimerTmSettings']['additionalProperties'] is False
    assert schema['$defs']['PCRReactionMetadata']['additionalProperties'] is False
    assert schema['$defs']['PCRCyclingMetadata']['additionalProperties'] is False
