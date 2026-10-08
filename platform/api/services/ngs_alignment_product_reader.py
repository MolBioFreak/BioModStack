"""Observational preview access through existing publication and serving owners."""
from contextlib import contextmanager
from services import ngs_alignment_product_builder as builder
from services import ngs_alignment_sessions as storage
from services.ngs_alignment_catalog_reader import CatalogReadError
from services.ngs_alignment_derived_products import resolve_product_source


@contextmanager
def preview_snapshot(job, catalog, preview, root):
    if (catalog is None or catalog.state != "ready" or preview is None or preview.state != "ready"):
        raise CatalogReadError("NGS_PREVIEW_NOT_READY", "The optional preview is not ready; catalog access is independent.", 409)
    if (preview.product != "preview" or preview.catalog_request_id != catalog.id
            or preview.catalog_authority_sha256 != catalog.authority_sha256
            or preview.source_identity != catalog.source_identity):
        raise storage.AlignmentSessionError("preview catalog binding changed")
    resolve_product_source(job, catalog)
    with builder._namespace(root, preview, create=False) as namespace:
        with storage.open_presentation_authority_root(namespace / "sealed", create=False) as directory:
            manifest = builder._manifest(directory, preview, lambda: None,
                expected_manifest=preview.manifest_sha256, expected_authority=preview.authority_sha256)
            yield directory, manifest


def preview_response(job, catalog, preview, root):
    with preview_snapshot(job, catalog, preview, root) as (_directory, manifest):
        base = "/api/jobs/{}/alignment-sessions/{}/presentation/products/preview/{}/{}".format(
            job.id, catalog.session_id, preview.id, preview.authority_sha256)
        artifacts = {}
        for role in ("bam", "index"):
            item = manifest["authority"]["artifacts"][role]
            artifacts[role] = {"url": base + "/" + role, "sha256": item["sha256"],
                "size_bytes": item["size_bytes"], "mime_type": "application/octet-stream", "range_capable": True}
        statistics = manifest["statistics"]
        return {"schema": "bms.ngs.alignment-preview.v6", "job_id": str(job.id),
            "session_id": catalog.session_id, "source": catalog.source_identity,
            "catalog_authority_sha256": catalog.authority_sha256,
            "preview_request_id": preview.id, "preview_authority_sha256": preview.authority_sha256,
            "policy": manifest["authority"]["policy"],
            "selected_read_count": statistics["selected_read_count"],
            "selected_record_count": statistics["selected_record_count"], **artifacts}
