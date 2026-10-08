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
    def no_git(*args, **kwargs):
        raise AssertionError("deployed scalar revision must not scan Git or source files")
    monkeypatch.setattr(build_identity.subprocess, "run", no_git)
    assert build_identity.source_build_revision() == "0123456789abcdef0123456789abcdef01234567"
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


def test_source_metadata_observes_dirty_checkout_and_deployed_revision(monkeypatch, tmp_path):
    import subprocess
    import pytest
    import build_identity as owner
    from component_runtime import SourceIdentity
    from services.remote_execution.bundle import current_source_identity, RemoteBundleError
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    tracked = tmp_path / "source.py"
    tracked.write_text("first")
    git("add", ".")
    git("commit", "-qm", "first")
    first = SourceIdentity.from_checkout(tmp_path)
    tracked.write_text("second")
    git("commit", "-qam", "second")
    second = SourceIdentity.from_checkout(tmp_path)
    tracked.write_text("dirty")
    monkeypatch.setattr(owner, "get_code_root", lambda: tmp_path)
    monkeypatch.delenv("BMS_BUILD_SHA", raising=False)
    assert owner.deployed_source_identity() == (second.revision, second.tree)
    with pytest.raises(RemoteBundleError, match="clean tracked"):
        current_source_identity(tmp_path)
    monkeypatch.setenv("BMS_BUILD_SHA", first.revision)
    assert owner.deployed_source_identity() == (first.revision, first.tree)


def test_gitless_source_metadata_reaches_actual_consumers(monkeypatch, tmp_path):
    import build_identity as owner
    from build_identity import source_build_revision
    from services.ngs_molbio_n5 import _runtime_source_authority
    from services.ngs_alignment_sessions import _creation_authority
    from services.resource_usage_evidence import build_resource_admission_handoff, validate_resource_admission_handoff
    monkeypatch.setattr(owner, "get_code_root", lambda: tmp_path)
    for revision in ("a" * 40, "unknown"):
        monkeypatch.setenv("BMS_BUILD_SHA", revision)
        assert owner.deployed_source_identity() == (revision, "unknown")
        assert source_build_revision() == revision
        assert _creation_authority() == (revision, None)
        source, tree = _runtime_source_authority()
        handoff = build_resource_admission_handoff(admission_id="a", run_attempt_id="r", canonical_job_id="j",
            preparation_id="p", cpu_threads=2, dram_bytes=1024**3, gpu_index=None, gpu_uuid=None,
            policy_source="project-scheduler", policy_version="bms.resource-admission-policy.v1", owner="test",
            lease_token="lease", source_revision=source, source_tree=tree)
        validate_resource_admission_handoff(handoff)
        assert (handoff["source_revision"], handoff["source_tree"]) == (revision, "unknown")



def test_source_identity_endpoint_is_metadata_not_a_runtime_record(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.ngs_molbio_n5 import router
    monkeypatch.setattr("build_identity.get_code_root", lambda: tmp_path)
    monkeypatch.setenv("BMS_BUILD_SHA", "a" * 40)
    app = FastAPI()
    app.include_router(router)
    @app.middleware("http")
    async def operator(request, call_next):
        request.state.authenticated_principal = {"id": "test", "roles": ["operator"]}
        return await call_next(request)
    with TestClient(app) as client:
        response = client.get("/api/operations/ngs-molbio/source-identity")
        assert response.status_code == 200
        assert response.json() == {"source_revision": "a" * 40, "source_tree": "unknown"}
        assert client.get("/api/operations/ngs-molbio/runtime-implementation").status_code == 404
