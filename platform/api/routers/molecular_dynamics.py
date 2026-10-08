from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.md.chemistry_catalog import ChemistryCatalogError, get_chemistry_catalog
from services.md.feature_gate import molecular_dynamics_feature_enabled

router = APIRouter(prefix="/api/molecular-dynamics", tags=["molecular-dynamics"])


def _catalog_view_or_503():
    try:
        return get_chemistry_catalog().view()
    except ChemistryCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics launch service is temporarily unavailable.",
            },
        ) from exc


@router.get("/capabilities")
def molecular_dynamics_capabilities() -> dict:
    view = _catalog_view_or_503()
    profiles = view.list_profiles()
    selectable = [profile["id"] for profile in profiles if profile["states"]["selectable"]]
    return {
        "schema": "bms.md.capabilities.v1",
        "feature_enabled": molecular_dynamics_feature_enabled(),
        "experimental": True,
        "contract_schemas": ["bms.md.job.v1"],
        "catalog_schema": "bms.md.chemistry-profile.v1",
        "catalog_digest": view.catalog_digest,
        "automatic_preparation": {
            "available": bool(selectable),
            "selectable_profile_ids": selectable,
            "scope_limited": True,
        },
        "runtime_probe": view.public_probe_summary(),
        "public_refresh_supported": False,
    }


@router.get("/chemistry-profiles")
def list_molecular_dynamics_chemistry_profiles() -> dict:
    view = _catalog_view_or_503()
    profiles = view.list_profiles()
    return {
        "schema": "bms.md.chemistry-profile-inventory.v1",
        "catalog_digest": view.catalog_digest,
        "profiles": profiles,
        "selectable_profile_ids": [
            profile["id"] for profile in profiles if profile["states"]["selectable"]
        ],
        "count": len(profiles),
        "bounded": True,
    }


@router.get("/chemistry-profiles/{profile_id}")
def get_molecular_dynamics_chemistry_profile(profile_id: str) -> dict:
    view = _catalog_view_or_503()
    profile = view.get_profile(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MD_CHEMISTRY_PROFILE_UNKNOWN",
                "message": f"Unknown molecular-dynamics chemistry profile: {profile_id}",
            },
        )
    return profile
