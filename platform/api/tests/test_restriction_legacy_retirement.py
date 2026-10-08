from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from routers import molbio_ops


ROOT = Path(__file__).resolve().parents[3]
FRONTEND_CATALOG = (
    ROOT
    / "platform/frontend/src/components/MolBioToolkit/utils/restrictionEnzymes.ts"
)


def _active_source(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def test_frontend_restriction_catalog_and_browser_science_authority_are_absent() -> None:
    assert not FRONTEND_CATALOG.exists()
    frontend_sources = list((ROOT / "platform/frontend/src").rglob("*.ts")) + list(
        (ROOT / "platform/frontend/src").rglob("*.tsx")
    )
    source = _active_source(frontend_sources)
    forbidden = {
        "RESTRICTION_ENZYME_GROUPS",
        "ALL_RESTRICTION_ENZYMES",
        "findRestrictionSiteMatches",
        "findRestrictionSites(",
        "reverseComplementSite",
        "/api/molbio/digest",
        "enzymes: { name: string; site?: string }[]",
    }
    assert forbidden.isdisjoint({token for token in forbidden if token in source})


def test_legacy_digest_fresh_write_route_is_absent() -> None:
    app = FastAPI()
    app.include_router(molbio_ops.router)
    document = app.openapi()
    assert "/api/molbio/digest" not in document["paths"]
    assert "DigestRequest" not in document["components"]["schemas"]
