from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from biomodstack_runtime_profile import install_feature_enabled, install_profile_snapshot


def effective_runtime_features(request: Any, configured: Mapping[str, object]) -> dict[str, object]:
    """Return only feature surfaces mounted in this running API process."""
    effective = dict(configured)
    route_paths = {
        str(getattr(route, "path", ""))
        for route in getattr(request.app, "routes", ())
    }
    openapi_paths: set[str] = set()
    openapi_factory = getattr(request.app, "openapi", None)
    if callable(openapi_factory):
        try:
            schema = openapi_factory()
            paths = schema.get("paths") if isinstance(schema, Mapping) else None
            if isinstance(paths, Mapping):
                openapi_paths = {str(path) for path in paths}
        except (AttributeError, TypeError, ValueError):
            openapi_paths = set()
    all_paths = route_paths | openapi_paths
    app_state = getattr(request.app, "state", None)
    mounted_features = getattr(app_state, "mounted_features", None)
    if not isinstance(mounted_features, Mapping):
        mounted_features = {}

    if "bioxp" in effective:
        effective["bioxp"] = any(path.startswith("/api/bioxp") for path in all_paths)
    if "ngs_ont" in effective:
        if "ngs_ont" in mounted_features:
            effective["ngs_ont"] = bool(mounted_features["ngs_ont"])
        else:
            effective["ngs_ont"] = any(
                path.startswith(("/api/ont", "/api/sequence-qc", "/api/ngs", "/api/molbio-ngs"))
                for path in all_paths
            )
    if "protein_compute" in effective:
        if "protein_compute" in mounted_features:
            effective["protein_compute"] = bool(mounted_features["protein_compute"])
        else:
            effective["protein_compute"] = any(
                path.startswith(("/api/frustrampnn", "/api/conformational-mapping", "/api/boltzgen", "/api/jobs/boltz-api", "/api/msa", "/api/blind-pose"))
                for path in all_paths
            )
    if "stats" in effective:
        if "stats" in mounted_features:
            effective["stats"] = bool(mounted_features["stats"])
        else:
            effective["stats"] = install_feature_enabled("stats")
    return effective


__all__ = ["effective_runtime_features", "install_profile_snapshot"]
