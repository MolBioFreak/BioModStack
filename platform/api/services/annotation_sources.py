from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ADDGENE_API_ORIGIN = "https://api.developers.addgene.org"
MAX_GENBANK_BYTES = 10 * 1024 * 1024
_ACCESSION_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:\.[0-9]+)?$")
_ADDGENE_GENBANK_PATH = re.compile(r"^/download/genbank/([1-9][0-9]*)/$")


class AnnotationSourceError(RuntimeError):
    """Base class for controlled annotation-source failures."""


class AnnotationSourceValidationError(AnnotationSourceError):
    pass


class AnnotationSourceConfigurationError(AnnotationSourceError):
    pass


class AnnotationSourceAuthenticationError(AnnotationSourceError):
    pass


class AnnotationSourceResponseError(AnnotationSourceError):
    pass


class AnnotationSourceAmbiguityError(AnnotationSourceResponseError):
    pass


@dataclass(frozen=True)
class AnnotationArtifact:
    content: str
    file_name: str
    media_type: str
    source: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "file_name": self.file_name,
            "media_type": self.media_type,
            "source": self.source,
        }


def validate_ncbi_accession(accession: str) -> str:
    normalized = accession.strip().upper()
    if not normalized or len(normalized) > 64 or not _ACCESSION_PATTERN.fullmatch(normalized):
        raise AnnotationSourceValidationError("NCBI accession has an invalid format")
    return normalized


def validate_addgene_plasmid_id(plasmid_id: int) -> int:
    if isinstance(plasmid_id, bool) or plasmid_id < 1 or plasmid_id > 2_147_483_647:
        raise AnnotationSourceValidationError("Addgene plasmid ID must be a positive integer")
    return plasmid_id


def select_addgene_full_sequence(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    sequences = payload.get("sequences")
    if not isinstance(sequences, Mapping):
        raise AnnotationSourceResponseError("Addgene response did not include a sequences object")

    for key, label in (
        ("public_addgene_full_sequences", "Addgene-authored"),
        ("public_user_full_sequences", "user-authored"),
    ):
        candidates = sequences.get(key)
        if not isinstance(candidates, list):
            raise AnnotationSourceResponseError(f"Addgene response field {key} was invalid")
        if len(candidates) > 1:
            raise AnnotationSourceAmbiguityError(
                f"Addgene returned multiple {label} full sequences; choose an authoritative sequence manually"
            )
        if len(candidates) == 1:
            candidate = candidates[0]
            if not isinstance(candidate, Mapping):
                raise AnnotationSourceResponseError("Addgene full sequence record was invalid")
            return candidate

    raise AnnotationSourceResponseError("Addgene plasmid has no full public sequence")


def validate_addgene_genbank_url(url: str) -> int:
    try:
        parsed = httpx.URL(url)
    except Exception as exc:
        raise AnnotationSourceValidationError("Addgene GenBank URL was invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.host != "api.developers.addgene.org"
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.userinfo
    ):
        raise AnnotationSourceValidationError("Addgene GenBank URL was outside the fixed API origin")
    match = _ADDGENE_GENBANK_PATH.fullmatch(parsed.path)
    if not match:
        raise AnnotationSourceValidationError("Addgene GenBank URL had an invalid path")
    return int(match.group(1))


def _is_s3_host(host: str) -> bool:
    return bool(
        host == "s3.amazonaws.com"
        or host.endswith(".s3.amazonaws.com")
        or re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.s3\.[a-z0-9-]+\.amazonaws\.com", host)
    )


def validate_addgene_download_redirect(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
    except Exception as exc:
        raise AnnotationSourceValidationError("Addgene download redirect was invalid") from exc
    host = (parsed.host or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, 443)
        or parsed.fragment
        or parsed.userinfo
        or not _is_s3_host(host)
    ):
        raise AnnotationSourceValidationError("Addgene download redirect was outside documented HTTPS S3 hosts")
    return parsed


def _validated_genbank(response: httpx.Response) -> str:
    raw = response.content
    if len(raw) > MAX_GENBANK_BYTES:
        raise AnnotationSourceResponseError("Annotation source exceeded the GenBank size limit")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnnotationSourceResponseError("Annotation source was not UTF-8 GenBank text") from exc
    normalized = content.lstrip("\ufeff\r\n ")
    if not normalized.startswith("LOCUS") or "\nORIGIN" not in normalized or not normalized.rstrip().endswith("//"):
        raise AnnotationSourceResponseError("Annotation source did not return valid GenBank text")
    return content


def _artifact_source(provider: str, source_id: str, source_url: str, content: str, **extra: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "source_id": source_id,
        "source_url": source_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        **extra,
    }


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        headers={"User-Agent": "BioModStack/annotation-source-retrieval"},
    )


async def fetch_ncbi_genbank(
    accession: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    email: str | None = None,
) -> AnnotationArtifact:
    normalized = validate_ncbi_accession(accession)
    params: dict[str, str] = {
        "db": "nuccore",
        "id": normalized,
        "rettype": "gb",
        "retmode": "text",
        "tool": "biomodstack",
    }
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email

    owns_client = client is None
    active_client = client or _default_client()
    try:
        try:
            response = await active_client.get(NCBI_EFETCH_URL, params=params)
        except httpx.HTTPError as exc:
            raise AnnotationSourceResponseError("NCBI EFetch was unreachable or timed out") from exc
        if response.status_code != 200:
            raise AnnotationSourceResponseError(f"NCBI EFetch returned HTTP {response.status_code}")
        content = _validated_genbank(response)
    finally:
        if owns_client:
            await active_client.aclose()

    canonical_url = f"https://www.ncbi.nlm.nih.gov/nuccore/{normalized}"
    return AnnotationArtifact(
        content=content,
        file_name=f"ncbi-{normalized}.gb",
        media_type="text/plain",
        source=_artifact_source("ncbi", normalized, canonical_url, content),
    )


async def fetch_addgene_genbank(
    plasmid_id: int,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> AnnotationArtifact:
    validated_id = validate_addgene_plasmid_id(plasmid_id)
    normalized_token = token.strip()
    if not normalized_token:
        raise AnnotationSourceConfigurationError("Addgene API token is not configured on the server")

    owns_client = client is None
    active_client = client or _default_client()
    auth_headers = {"Authorization": f"Token {normalized_token}"}
    catalog_url = f"{ADDGENE_API_ORIGIN}/catalog/plasmid-with-sequences/{validated_id}/"
    try:
        try:
            catalog_response = await active_client.get(catalog_url, headers=auth_headers)
        except httpx.HTTPError as exc:
            raise AnnotationSourceResponseError("Addgene catalog was unreachable or timed out") from exc
        if catalog_response.status_code in (401, 403):
            raise AnnotationSourceAuthenticationError("Addgene token is invalid or lacks catalog:retrieve-with-sequences scope")
        if catalog_response.status_code == 404:
            raise AnnotationSourceResponseError("Addgene plasmid was not found")
        if catalog_response.status_code != 200:
            raise AnnotationSourceResponseError(f"Addgene catalog returned HTTP {catalog_response.status_code}")
        try:
            catalog_payload = catalog_response.json()
        except ValueError as exc:
            raise AnnotationSourceResponseError("Addgene catalog returned invalid JSON") from exc
        if not isinstance(catalog_payload, Mapping):
            raise AnnotationSourceResponseError("Addgene catalog response was not an object")

        sequence_record = select_addgene_full_sequence(catalog_payload)
        genbank_url = sequence_record.get("genbank_url")
        if not isinstance(genbank_url, str):
            raise AnnotationSourceResponseError("Addgene full sequence did not include a GenBank URL")
        sequence_id = validate_addgene_genbank_url(genbank_url)

        download_response = await active_client.get(genbank_url, headers=auth_headers)
        if download_response.status_code in (401, 403):
            raise AnnotationSourceAuthenticationError("Addgene token cannot download the selected GenBank sequence")
        if download_response.status_code != 302:
            raise AnnotationSourceResponseError("Addgene GenBank endpoint did not return the documented redirect")
        location = download_response.headers.get("location")
        if not location:
            raise AnnotationSourceResponseError("Addgene GenBank redirect omitted its target")
        s3_url = validate_addgene_download_redirect(location)

        artifact_response = await active_client.get(s3_url)
        if artifact_response.status_code != 200:
            raise AnnotationSourceResponseError(f"Addgene GenBank download returned HTTP {artifact_response.status_code}")
        content = _validated_genbank(artifact_response)
    except httpx.HTTPError as exc:
        raise AnnotationSourceResponseError("Addgene GenBank download was unreachable or timed out") from exc
    finally:
        if owns_client:
            await active_client.aclose()

    canonical_url = f"https://www.addgene.org/{validated_id}/"
    return AnnotationArtifact(
        content=content,
        file_name=f"addgene-{validated_id}.gb",
        media_type="text/plain",
        source=_artifact_source(
            "addgene",
            str(validated_id),
            canonical_url,
            content,
            sequence_id=str(sequence_id),
            sequence_description=str(sequence_record.get("sequence_description") or ""),
        ),
    )
