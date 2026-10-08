from __future__ import annotations

import importlib
import importlib.util

import httpx
import pytest


MODULE = "services.annotation_sources"
GENBANK = """LOCUS       TESTSEQ                  12 bp    DNA     linear   SYN 01-JAN-2026
FEATURES             Location/Qualifiers
     misc_feature    2..5
                     /label=verified
ORIGIN
        1 acgtacgtac gt
//
"""


def sources():
    assert importlib.util.find_spec(MODULE) is not None, "annotation source retrieval service is missing"
    return importlib.import_module(MODULE)


def test_ncbi_accession_validation_is_allowlisted() -> None:
    module = sources()
    assert module.validate_ncbi_accession("NC_000001.11") == "NC_000001.11"
    assert module.validate_ncbi_accession(" j01749.1 ") == "J01749.1"
    for invalid in ("", "../etc/passwd", "J01749.1&db=protein", "https://example.test/x", "A" * 65):
        with pytest.raises(module.AnnotationSourceValidationError):
            module.validate_ncbi_accession(invalid)


@pytest.mark.asyncio
async def test_ncbi_fetch_uses_fixed_efetch_contract_and_returns_genbank() -> None:
    module = sources()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=GENBANK, headers={"content-type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        artifact = await module.fetch_ncbi_genbank("J01749.1", client=client)

    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "eutils.ncbi.nlm.nih.gov"
    assert request.url.path == "/entrez/eutils/efetch.fcgi"
    assert dict(request.url.params) == {
        "db": "nuccore",
        "id": "J01749.1",
        "rettype": "gb",
        "retmode": "text",
        "tool": "biomodstack",
    }
    assert artifact.content == GENBANK
    assert artifact.file_name == "ncbi-J01749.1.gb"
    assert artifact.source["provider"] == "ncbi"
    assert artifact.source["source_id"] == "J01749.1"
    assert artifact.source["source_url"].startswith("https://www.ncbi.nlm.nih.gov/nuccore/J01749.1")


@pytest.mark.asyncio
async def test_ncbi_rejects_non_genbank_upstream_payload() -> None:
    module = sources()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="Error: invalid accession"))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(module.AnnotationSourceResponseError, match="valid GenBank"):
            await module.fetch_ncbi_genbank("J01749.1", client=client)


def test_addgene_full_sequence_selection_prefers_addgene_and_fails_ambiguity() -> None:
    module = sources()
    official = {"sequence_description": "Addgene full", "sequence": "ACGT", "genbank_url": "https://api.developers.addgene.org/download/genbank/11/"}
    user = {"sequence_description": "User full", "sequence": "ACGT", "genbank_url": "https://api.developers.addgene.org/download/genbank/12/"}
    payload = {"sequences": {
        "public_addgene_full_sequences": [official],
        "public_user_full_sequences": [user],
        "public_addgene_partial_sequences": [],
        "public_user_partial_sequences": [],
    }}
    assert module.select_addgene_full_sequence(payload) == official

    payload["sequences"]["public_addgene_full_sequences"] = [official, {**official, "genbank_url": "https://api.developers.addgene.org/download/genbank/13/"}]
    with pytest.raises(module.AnnotationSourceAmbiguityError, match="multiple Addgene-authored full sequences"):
        module.select_addgene_full_sequence(payload)


def test_addgene_partial_only_payload_is_rejected() -> None:
    module = sources()
    payload = {"sequences": {
        "public_addgene_full_sequences": [],
        "public_user_full_sequences": [],
        "public_addgene_partial_sequences": [{"genbank_url": "https://api.developers.addgene.org/download/genbank/11/"}],
        "public_user_partial_sequences": [],
    }}
    with pytest.raises(module.AnnotationSourceResponseError, match="full public sequence"):
        module.select_addgene_full_sequence(payload)


def test_addgene_genbank_and_s3_urls_are_strictly_allowlisted() -> None:
    module = sources()
    assert module.validate_addgene_genbank_url("https://api.developers.addgene.org/download/genbank/438456/") == 438456
    assert module.validate_addgene_download_redirect("https://bucket.s3.us-east-1.amazonaws.com/path/file.gb?X-Amz-Signature=x").host.endswith("amazonaws.com")
    for invalid in (
        "http://api.developers.addgene.org/download/genbank/1/",
        "https://evil.test/download/genbank/1/",
        "https://api.developers.addgene.org/download/genbank/1/?next=x",
        "https://api.developers.addgene.org/download/genbank/not-an-id/",
    ):
        with pytest.raises(module.AnnotationSourceValidationError):
            module.validate_addgene_genbank_url(invalid)
    for invalid in (
        "http://bucket.s3.amazonaws.com/file.gb",
        "https://amazonaws.com.evil.test/file.gb",
        "https://127.0.0.1/file.gb",
        "https://bucket.s3.amazonaws.com/file.gb#fragment",
    ):
        with pytest.raises(module.AnnotationSourceValidationError):
            module.validate_addgene_download_redirect(invalid)


@pytest.mark.asyncio
async def test_addgene_fetch_keeps_token_off_s3_and_returns_authoritative_genbank() -> None:
    module = sources()
    requests: list[httpx.Request] = []
    catalog_url = "https://api.developers.addgene.org/catalog/plasmid-with-sequences/10878/"
    genbank_url = "https://api.developers.addgene.org/download/genbank/438456/"
    s3_url = "https://addgene-files.s3.us-east-1.amazonaws.com/438456.gb?X-Amz-Signature=signed"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == catalog_url:
            return httpx.Response(200, json={"id": 10878, "name": "pTest", "sequences": {
                "public_addgene_full_sequences": [{"sequence_description": "verified", "sequence": "ACGTACGTACGT", "genbank_url": genbank_url}],
                "public_user_full_sequences": [],
                "public_addgene_partial_sequences": [],
                "public_user_partial_sequences": [],
            }})
        if str(request.url) == genbank_url:
            return httpx.Response(302, headers={"location": s3_url})
        if str(request.url) == s3_url:
            assert "authorization" not in request.headers
            return httpx.Response(200, text=GENBANK)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        artifact = await module.fetch_addgene_genbank(10878, token="secret-token", client=client)

    assert [request.headers.get("authorization") for request in requests] == ["Token secret-token", "Token secret-token", None]
    assert artifact.content == GENBANK
    assert artifact.file_name == "addgene-10878.gb"
    assert artifact.source["provider"] == "addgene"
    assert artifact.source["source_id"] == "10878"
    assert artifact.source["sequence_id"] == "438456"
    assert artifact.source["source_url"] == "https://www.addgene.org/10878/"


@pytest.mark.asyncio
async def test_addgene_requires_server_token_before_network() -> None:
    module = sources()
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(module.AnnotationSourceConfigurationError, match="not configured"):
            await module.fetch_addgene_genbank(10878, token="", client=client)
    assert called is False


@pytest.mark.asyncio
async def test_upstream_redirect_and_payload_size_fail_closed() -> None:
    module = sources()
    catalog_url = "https://api.developers.addgene.org/catalog/plasmid-with-sequences/10878/"
    genbank_url = "https://api.developers.addgene.org/download/genbank/438456/"

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == catalog_url:
            return httpx.Response(200, json={"sequences": {
                "public_addgene_full_sequences": [{"sequence_description": "verified", "sequence": "ACGT", "genbank_url": genbank_url}],
                "public_user_full_sequences": [], "public_addgene_partial_sequences": [], "public_user_partial_sequences": [],
            }})
        return httpx.Response(302, headers={"location": "https://evil.test/file.gb"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler), follow_redirects=False) as client:
        with pytest.raises(module.AnnotationSourceValidationError):
            await module.fetch_addgene_genbank(10878, token="token", client=client)

    oversized = "LOCUS       BIG\nORIGIN\n" + ("a" * (module.MAX_GENBANK_BYTES + 1)) + "\n//\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=oversized))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(module.AnnotationSourceResponseError, match="size limit"):
            await module.fetch_ncbi_genbank("J01749.1", client=client)
