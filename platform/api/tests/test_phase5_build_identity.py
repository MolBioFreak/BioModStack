from __future__ import annotations

import importlib
import sys
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def test_build_identity_reports_valid_full_revision(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("BMS_BUILD_ID", "release-20260718.1")
    monkeypatch.setenv("BMS_BUILD_TIME", "2026-07-18T03:30:00Z")

    build_identity = importlib.import_module("build_identity")
    assert build_identity.current_build_identity() == {
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "build_id": "release-20260718.1",
        "build_time": "2026-07-18T03:30:00Z",
    }


def test_build_identity_rejects_unverified_revision_shapes(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "0123456")
    monkeypatch.delenv("BMS_BUILD_ID", raising=False)
    monkeypatch.delenv("BMS_BUILD_TIME", raising=False)

    build_identity = importlib.import_module("build_identity")
    assert build_identity.current_build_identity() == {
        "revision": "unknown",
        "build_id": "development",
        "build_time": "unknown",
    }


def test_every_locally_built_compose_service_receives_build_identity_and_stable_image_ref() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    locally_built = {
        name: service for name, service in services.items() if "build" in service
    }
    assert set(locally_built) == {
        "bms-api",
        "bms-host-agent",
        "bms-cpu-power",
        "bms-web",
    }
    for service_name, service in locally_built.items():
        args = service["build"].get("args", {})
        assert args.get("BMS_BUILD_SHA"), service_name
        assert args.get("BMS_BUILD_ID"), service_name
        assert args.get("BMS_BUILD_TIME"), service_name

    expected_images = {
        "bms-api": "${BMS_API_IMAGE:-biomodstack/api:local}",
        "bms-host-agent": "${BMS_HOST_AGENT_IMAGE:-biomodstack/host-agent:local}",
        "bms-cpu-power": "${BMS_CPU_POWER_IMAGE:-biomodstack/cpu-power:local}",
        "bms-web": "${BMS_WEB_IMAGE:-biomodstack/web:local}",
    }
    for service_name, image_ref in expected_images.items():
        assert services[service_name]["image"] == image_ref


def test_core_runtime_forwards_independent_bioxp_connection_policy() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))
    api_environment = compose["services"]["bms-api"]["environment"]

    assert api_environment["BMS_BIOXP_CONNECTION_ENABLED"] == "${BMS_BIOXP_CONNECTION_ENABLED:-0}"
    assert api_environment["BMS_BIOXP_MUTATIONS_ENABLED"] == "${BMS_BIOXP_MUTATIONS_ENABLED:-0}"
    assert api_environment["BMS_BIOXP_ALLOWED_HOSTS"] == "${BMS_BIOXP_ALLOWED_HOSTS:-robot}"
    assert api_environment["BMS_BIOXP_ALLOWED_CIDRS"] == "${BMS_BIOXP_ALLOWED_CIDRS:-}"


def test_built_images_publish_oci_revision_labels() -> None:
    for relative_path in ("docker/api.Dockerfile", "docker/web.Dockerfile"):
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "ARG BMS_BUILD_SHA" in source, relative_path
        assert "ARG BMS_BUILD_ID" in source, relative_path
        assert "ARG BMS_BUILD_TIME" in source, relative_path
        if relative_path == "docker/web.Dockerfile":
            assert "COPY docker/web/nginx.conf " in source
        assert "org.opencontainers.image.revision=$BMS_BUILD_SHA" in source, relative_path
        assert "org.opencontainers.image.created=$BMS_BUILD_TIME" in source, relative_path


def test_api_final_scratch_stage_retains_build_identity() -> None:
    source = (REPO_ROOT / "docker/api.Dockerfile").read_text(encoding="utf-8")
    final_stage = source.split("FROM scratch AS api-runtime", 1)[1]

    for name in ("BMS_BUILD_SHA", "BMS_BUILD_ID", "BMS_BUILD_TIME"):
        assert f"ARG {name}" in final_stage
        assert f"{name}=${name}" in final_stage
    assert "org.opencontainers.image.revision=$BMS_BUILD_SHA" in final_stage
    assert "org.opencontainers.image.created=$BMS_BUILD_TIME" in final_stage
    assert "org.opencontainers.image.version=$BMS_BUILD_ID" in final_stage
