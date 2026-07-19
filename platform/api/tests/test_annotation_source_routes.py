from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path
import pytest
import yaml

from routers import molbio_ops
from services.annotation_sources import (
    AnnotationArtifact,
    AnnotationSourceAmbiguityError,
    AnnotationSourceConfigurationError,
    AnnotationSourceValidationError,
)


def client() -> TestClient:
    app = FastAPI()
    app.include_router(molbio_ops.router)
    return TestClient(app)


def artifact(provider: str, source_id: str) -> AnnotationArtifact:
    return AnnotationArtifact(
        content="LOCUS       TEST 4 bp DNA linear SYN 01-JAN-2026\nORIGIN\n        1 acgt\n//\n",
        file_name=f"{provider}-{source_id}.gb",
        media_type="text/plain",
        source={"provider": provider, "source_id": source_id, "source_url": f"https://source.test/{source_id}", "artifact_sha256": "abc"},
    )


def test_core_runtime_passes_annotation_source_configuration_to_api() -> None:
    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "compose.core-runtime.yml").read_text())
    environment = compose["services"]["bms-api"]["environment"]
    assert environment["ADDGENE_API_TOKEN"] == "${ADDGENE_API_TOKEN:-}"
    assert environment["NCBI_API_KEY"] == "${NCBI_API_KEY:-}"
    assert environment["NCBI_EMAIL"] == "${NCBI_EMAIL:-}"
    example = (root / ".env.core-runtime.example").read_text()
    assert "ADDGENE_API_TOKEN=" in example
    assert "NCBI_API_KEY=" in example
    assert "NCBI_EMAIL=" in example


def test_annotation_source_status_never_exposes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADDGENE_API_TOKEN", "top-secret")
    response = client().get("/api/molbio/annotation-sources/status")
    assert response.status_code == 200
    assert response.json() == {"ncbi": {"available": True}, "addgene": {"available": True}}
    assert "secret" not in response.text.lower()


def test_annotation_source_status_reports_missing_addgene_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADDGENE_API_TOKEN", raising=False)
    response = client().get("/api/molbio/annotation-sources/status")
    assert response.status_code == 200
    assert response.json() == {"ncbi": {"available": True}, "addgene": {"available": False}}


def test_ncbi_annotation_source_returns_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(accession: str, **kwargs):
        assert accession == "J01749.1"
        assert kwargs["api_key"] == "ncbi-key"
        assert kwargs["email"] == "ops@example.test"
        return artifact("ncbi", accession)

    monkeypatch.setenv("NCBI_API_KEY", "ncbi-key")
    monkeypatch.setenv("NCBI_EMAIL", "ops@example.test")
    monkeypatch.setattr(molbio_ops, "fetch_ncbi_genbank", fake_fetch, raising=False)
    response = client().get("/api/molbio/annotation-sources/ncbi/J01749.1")
    assert response.status_code == 200
    assert response.json()["source"]["provider"] == "ncbi"
    assert response.json()["content"].startswith("LOCUS")


def test_addgene_annotation_source_uses_server_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(plasmid_id: int, **kwargs):
        assert plasmid_id == 10878
        assert kwargs["token"] == "server-only-token"
        return artifact("addgene", str(plasmid_id))

    monkeypatch.setenv("ADDGENE_API_TOKEN", "server-only-token")
    monkeypatch.setattr(molbio_ops, "fetch_addgene_genbank", fake_fetch, raising=False)
    response = client().get("/api/molbio/annotation-sources/addgene/10878")
    assert response.status_code == 200
    assert response.json()["source"]["provider"] == "addgene"
    assert "server-only-token" not in response.text


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AnnotationSourceValidationError("bad input"), 400),
        (AnnotationSourceAmbiguityError("ambiguous"), 409),
        (AnnotationSourceConfigurationError("not configured"), 503),
    ],
)
def test_annotation_source_errors_are_controlled(monkeypatch: pytest.MonkeyPatch, error: Exception, status: int) -> None:
    async def fake_fetch(*args, **kwargs):
        raise error

    monkeypatch.setenv("ADDGENE_API_TOKEN", "configured")
    monkeypatch.setattr(molbio_ops, "fetch_addgene_genbank", fake_fetch, raising=False)
    response = client().get("/api/molbio/annotation-sources/addgene/10878")
    assert response.status_code == status
    assert response.json() == {"detail": str(error)}
