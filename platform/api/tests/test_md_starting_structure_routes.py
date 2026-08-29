from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse as StarletteStreamingResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

ONE_AKI = API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb"
ONE_AKI_SHA256 = "c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb"


class _UnusedSession:
    async def get(self, *_args, **_kwargs):
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import main
    from database import get_session
    from routers import molecular_dynamics
    from services.md import starting_structures

    async def override_session():
        yield _UnusedSession()

    monkeypatch.setattr(
        starting_structures, "get_inputs_dir", lambda: tmp_path / "inputs", raising=False
    )
    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: None)
    main.app.dependency_overrides[get_session] = override_session
    test_client = TestClient(main.app)
    try:
        yield test_client
    finally:
        test_client.close()
        main.app.dependency_overrides.pop(get_session, None)


def test_inspect_and_digest_bound_content_routes_are_path_free(client: TestClient) -> None:
    inspected = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "managed_fixture", "id": "1aki-admitted-v1"}},
    )
    assert inspected.status_code == 200, inspected.text
    payload = inspected.json()
    assert payload["schema_version"] == "bms.md.starting-structure-inspection.v1"
    assert "schema" not in payload
    assert payload["identity"]["sha256"] == ONE_AKI_SHA256
    assert "/home/" not in inspected.text and "assets/" not in inspected.text

    content = client.get(payload["viewer"]["url"])
    assert content.status_code == 200
    assert content.content == ONE_AKI.read_bytes()
    assert content.headers["content-type"] == "chemical/x-pdb"
    assert content.headers["etag"] == f'"sha256:{ONE_AKI_SHA256}"'
    assert content.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"] == 'inline; filename="RCSB-1AKI-hen-egg-white-lysozyme.pdb"'

    changed = client.get(payload["viewer"]["url"].replace(ONE_AKI_SHA256, "0" * 64))
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_CHANGED"


def test_governed_content_uses_verified_multichunk_snapshot_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    source = tmp_path / "large-source.pdb"
    remark = b"REMARK   bounded streaming fixture".ljust(79, b" ") + b"\n"
    expected = remark * 40000 + ONE_AKI.read_bytes()
    source.write_bytes(expected)
    expected_sha256 = hashlib.sha256(expected).hexdigest()
    yielded_sizes: list[int] = []

    async def resolve_large(source_ref, _session):
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=source,
            label="large-source.pdb",
        )

    class RecordingStreamingResponse(StarletteStreamingResponse):
        def __init__(self, content, *args, **kwargs):
            def observed_content():
                for chunk in content:
                    yielded_sizes.append(len(chunk))
                    yield chunk

            super().__init__(observed_content(), *args, **kwargs)

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_large)
    monkeypatch.setattr(
        molecular_dynamics,
        "StreamingResponse",
        RecordingStreamingResponse,
        raising=False,
    )
    response = client.get(
        "/api/molecular-dynamics/starting-structures/managed_fixture/large-fixture/content"
        f"?expected_sha256={expected_sha256}"
    )
    assert response.status_code == 200, response.text
    assert len(yielded_sizes) > 1
    assert max(yielded_sizes) < len(expected)
    assert response.content == expected
    assert hashlib.sha256(response.content).hexdigest() == expected_sha256
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["content-type"] == "chemical/x-pdb"
    assert response.headers["etag"] == f'"sha256:{expected_sha256}"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'inline; filename="large-source-pdb.pdb"'


def test_stream_snapshot_is_immutable_after_response_setup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    source = tmp_path / "mutable-source.pdb"
    expected = ONE_AKI.read_bytes()
    changed = expected.replace(b"1AKI", b"9ZZZ")
    assert changed != expected and len(changed) == len(expected)
    source.write_bytes(expected)
    expected_sha256 = hashlib.sha256(expected).hexdigest()

    async def resolve_mutable(source_ref, _session):
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=source,
            label="mutable-source.pdb",
        )

    class MutatingStreamingResponse(StarletteStreamingResponse):
        def __init__(self, content, *args, **kwargs):
            source.write_bytes(changed)
            super().__init__(content, *args, **kwargs)

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_mutable)
    monkeypatch.setattr(
        molecular_dynamics,
        "StreamingResponse",
        MutatingStreamingResponse,
        raising=False,
    )
    response = client.get(
        "/api/molecular-dynamics/starting-structures/managed_fixture/mutable-fixture/content"
        f"?expected_sha256={expected_sha256}"
    )
    assert source.read_bytes() == changed
    assert response.status_code == 200, response.text
    assert response.content == expected


def test_governed_content_rejects_source_drift_since_inspection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    source = tmp_path / "drifted-source.pdb"
    expected = ONE_AKI.read_bytes()
    changed = expected.replace(b"1AKI", b"9ZZZ")
    source.write_bytes(expected)
    inspected_sha256 = hashlib.sha256(expected).hexdigest()
    source.write_bytes(changed)

    async def resolve_drifted(source_ref, _session):
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=source,
            label="drifted-source.pdb",
        )

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_drifted)
    response = client.get(
        "/api/molecular-dynamics/starting-structures/managed_fixture/drifted/content"
        f"?expected_sha256={inspected_sha256}"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_CHANGED"


def test_governed_content_preserves_cif_media_type_and_canonical_suffix(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    source = tmp_path / "source.mmcif"
    expected = b"data_fixture\n_atom_site.id\n_atom_site.type_symbol\n1 C\n"
    source.write_bytes(expected)
    expected_sha256 = hashlib.sha256(expected).hexdigest()

    async def resolve_cif(source_ref, _session):
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=source,
            label="source.mmcif",
        )

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_cif)
    response = client.get(
        "/api/molecular-dynamics/starting-structures/upload/cif-source/content"
        f"?expected_sha256={expected_sha256}"
    )
    assert response.status_code == 200, response.text
    assert response.content == expected
    assert response.headers["content-type"] == "chemical/x-mmcif"
    assert response.headers["content-disposition"] == 'inline; filename="source-mmcif.cif"'


def test_governed_content_rejects_files_over_100_mib_before_delivery(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    oversized = tmp_path / "oversized.pdb"
    with oversized.open("wb") as handle:
        handle.write(b"HEADER")
        handle.truncate(100 * 1024 * 1024 + 1)

    async def resolve_oversized(source_ref, _session):
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=oversized,
            label="oversized.pdb",
        )

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_oversized)
    response = client.get(
        "/api/molecular-dynamics/starting-structures/managed_fixture/oversized/content"
        f"?expected_sha256={'0' * 64}"
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_TOO_LARGE"


@pytest.mark.parametrize(
    ("kind", "source_id"),
    [
        ("managed_fixture", "1aki-admitted-v1"),
        ("rcsb", "1AKI"),
        ("upload", "opaque-upload"),
        ("server_file", "a" * 64),
        ("prior_md_input", "11111111-1111-4111-8111-111111111111"),
        ("design", "22222222-2222-4222-8222-222222222222"),
    ],
)
def test_content_re_resolves_every_source_kind_before_streaming(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    source_id: str,
) -> None:
    from routers import molecular_dynamics
    from services.md.starting_structures import ResolvedStartingStructure

    resolved_kinds: list[str] = []

    async def record_resolution(source_ref, _session):
        resolved_kinds.append(source_ref.kind)
        return ResolvedStartingStructure(
            source_ref=source_ref,
            path=ONE_AKI,
            label=f"{source_ref.kind}.pdb",
        )

    monkeypatch.setattr(molecular_dynamics, "resolve_source", record_resolution)
    response = client.get(
        f"/api/molecular-dynamics/starting-structures/{kind}/{source_id}/content"
        f"?expected_sha256={ONE_AKI_SHA256}"
    )
    assert response.status_code == 200, response.text
    assert response.content == ONE_AKI.read_bytes()
    assert resolved_kinds == [kind]


def test_upload_publishes_server_issued_immutable_source(client: TestClient) -> None:
    response = client.post(
        "/api/molecular-dynamics/starting-structures/upload",
        files={"file": ("operator-choice.pdb", ONE_AKI.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source_ref"]["kind"] == "upload"
    assert payload["source_ref"]["id"]
    assert payload["source_ref"]["id"] != ONE_AKI_SHA256
    assert payload["identity"]["sha256"] == ONE_AKI_SHA256
    assert "operator-choice" in payload["identity"]["label"]
    assert "/home/" not in response.text and "inputs/" not in response.text

    replay = client.get(payload["viewer"]["url"])
    assert replay.status_code == 200
    assert replay.content == ONE_AKI.read_bytes()


def test_rcsb_inspection_normalizes_accession_and_uses_bounded_fetch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.md import starting_structures

    calls: list[str] = []

    async def fake_fetch(accession: str) -> Path:
        calls.append(accession)
        return ONE_AKI

    monkeypatch.setattr(starting_structures, "fetch_rcsb_entry", fake_fetch)
    response = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "rcsb", "id": "1aki"}},
    )
    assert response.status_code == 200, response.text
    assert calls == ["1AKI"]
    assert response.json()["source_ref"] == {"kind": "rcsb", "id": "1AKI"}
    assert response.json()["identity"]["pdb_id"] == "1AKI"


def test_rcsb_inspection_rejects_cached_bytes_for_another_accession(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.md import starting_structures

    async def fake_fetch(_accession: str) -> Path:
        return ONE_AKI

    monkeypatch.setattr(starting_structures, "fetch_rcsb_entry", fake_fetch)
    response = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "rcsb", "id": "2XYZ"}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_SOURCE_ID_MISMATCH"


def test_server_file_browser_is_hidden_when_capability_is_disabled(client: TestClient) -> None:
    response = client.get("/api/molecular-dynamics/starting-structures/server-files")
    assert response.status_code == 404
    assert "path" not in response.text.lower()


def test_server_file_browser_returns_only_opaque_paginated_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from services.md import starting_structures

    root = tmp_path / "server-files"
    root.mkdir()
    candidate = root / "visible-1aki.pdb"
    candidate.write_bytes(ONE_AKI.read_bytes())
    outside = tmp_path / "outside.pdb"
    outside.write_bytes(ONE_AKI.read_bytes())
    (root / "symlinked.pdb").symlink_to(outside)
    monkeypatch.setenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "1")
    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"inputs": root})
    monkeypatch.setattr(
        starting_structures,
        "to_allowed_relative",
        lambda path: str(Path("inputs") / path.relative_to(root)),
    )

    response = client.get(
        "/api/molecular-dynamics/starting-structures/server-files?search=1aki&limit=1"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "items": [
            {
                "id": payload["items"][0]["id"],
                "label": "visible-1aki.pdb",
                "format": "pdb",
                "bytes": 116397,
            }
        ],
        "next_cursor": None,
        "count": 1,
    }
    assert payload["items"][0]["id"] != "inputs/visible-1aki.pdb"
    decoded = base64.urlsafe_b64decode(
        payload["items"][0]["id"] + "=" * (-len(payload["items"][0]["id"]) % 4)
    )
    assert b"inputs/visible-1aki.pdb" not in decoded
    assert "path" not in response.text.lower()
    assert str(root) not in response.text

    inspected = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "server_file", "id": payload["items"][0]["id"]}},
    )
    assert inspected.status_code == 200, inspected.text
    replay = client.get(inspected.json()["viewer"]["url"])
    assert replay.status_code == 200
    assert replay.content == ONE_AKI.read_bytes()

    unknown = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "server_file", "id": "0" * 64}},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_NOT_FOUND"


def test_server_file_resolution_rejects_ancestor_symlink_swap_after_handle_issue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from services.md import starting_structures

    root = tmp_path / "server-files"
    nested = root / "nested"
    nested.mkdir(parents=True)
    candidate = nested / "visible-1aki.pdb"
    candidate.write_bytes(ONE_AKI.read_bytes())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / candidate.name).write_bytes(
        ONE_AKI.read_bytes().replace(b"1AKI", b"9ZZZ")
    )
    monkeypatch.setenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "1")
    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"inputs": root})
    monkeypatch.setattr(
        starting_structures,
        "to_allowed_relative",
        lambda path: str(Path("inputs") / path.relative_to(root)),
    )

    listed = client.get("/api/molecular-dynamics/starting-structures/server-files")
    assert listed.status_code == 200, listed.text
    handle = listed.json()["items"][0]["id"]
    pinned = root / "pinned-original"
    nested.rename(pinned)
    nested.symlink_to(outside, target_is_directory=True)

    inspected = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "server_file", "id": handle}},
    )
    assert inspected.status_code == 404, inspected.text
    assert inspected.json()["detail"]["code"] == "MD_STARTING_STRUCTURE_NOT_FOUND"
    assert "9ZZZ" not in inspected.text


def test_server_file_resolution_carries_one_pinned_file_through_every_consumer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import jobs, molecular_dynamics
    from services.md import starting_structures

    root = tmp_path / "server-files"
    nested = root / "nested"
    nested.mkdir(parents=True)
    candidate = nested / "visible-1aki.pdb"
    original = ONE_AKI.read_bytes()
    replacement = original.replace(b"1AKI", b"9ZZZ")
    candidate.write_bytes(original)
    monkeypatch.setenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "1")
    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"inputs": root})
    monkeypatch.setattr(
        starting_structures,
        "to_allowed_relative",
        lambda path: str(Path("inputs") / path.relative_to(root)),
    )

    class _View:
        catalog_digest = "b" * 64

        @staticmethod
        def get_profile(_profile_id: str) -> dict:
            return {
                "id": "profile-v1",
                "profile_sha256": "a" * 64,
                "states": {"selectable": True},
                "launch_constraints": {
                    "structure_sha256": ONE_AKI_SHA256,
                    "engine": "gromacs",
                    "replicas": 1,
                    "padding_nm": 1.0,
                    "salt_molar": 0.15,
                    "temperature_k": 300.0,
                    "pressure_bar": 1.0,
                    "timestep_fs": 2.0,
                    "max_minimization_steps": 100,
                    "max_nvt_steps": 1000,
                    "max_npt_steps": 1000,
                    "max_production_steps": 1000,
                },
                "scientific_validation": {"scope": {"launch_scope": "test"}},
            }

    class _Catalog:
        @staticmethod
        def view() -> _View:
            return _View()

    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _Catalog())
    listed = client.get("/api/molecular-dynamics/starting-structures/server-files")
    assert listed.status_code == 200, listed.text
    handle = listed.json()["items"][0]["id"]
    real_resolve = starting_structures.resolve_source

    def restore_original() -> None:
        candidate.unlink(missing_ok=True)
        candidate.write_bytes(original)

    def replace_candidate() -> None:
        swapped = nested / ".replacement.pdb"
        swapped.write_bytes(replacement)
        swapped.replace(candidate)

    async def resolve_then_replace(source_ref, session):
        resolved = await real_resolve(source_ref, session)
        replace_candidate()
        return resolved

    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_then_replace)

    restore_original()
    inspected = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "server_file", "id": handle}},
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["identity"]["sha256"] == ONE_AKI_SHA256

    restore_original()
    content = client.get(
        f"/api/molecular-dynamics/starting-structures/server_file/{handle}/content"
        f"?expected_sha256={ONE_AKI_SHA256}"
    )
    assert content.status_code == 200, content.text
    assert content.content == original

    restore_original()
    preview = client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": _server_file_intent(handle),
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source"]["sha256"] == ONE_AKI_SHA256

    monkeypatch.setattr(molecular_dynamics, "resolve_source", real_resolve)
    restore_original()
    launch_preview = client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": _server_file_intent(handle),
        },
    )
    assert launch_preview.status_code == 200, launch_preview.text
    monkeypatch.setattr(molecular_dynamics, "resolve_source", resolve_then_replace)
    restore_original()
    launch_bytes: list[bytes] = []

    async def fake_create_job(job_data, _background_tasks, _session, **kwargs):
        token = job_data.params["md_job_spec"]["input"]["structure"]
        launch_bytes.append(Path(kwargs["_md_input_resolver"](token)).read_bytes())
        return "created"

    monkeypatch.setattr(jobs, "create_job", fake_create_job)
    request = starting_structures.MdLaunchRequest.model_validate(
        {
            "schema_version": "bms.md.launch-request.v1",
            "intent": _server_file_intent(handle),
            "preview_digest": launch_preview.json()["preview_digest"],
        }
    )
    result = asyncio.run(molecular_dynamics.launch_typed_md_job(request, _UnusedSession()))
    assert result == "created"
    assert launch_bytes == [original]


def _server_file_intent(handle: str) -> dict:
    return {
        "schema_version": "bms.md.launch-intent.v1",
        "name": "server-file capability check",
        "source_ref": {"kind": "server_file", "id": handle},
        "expected_source_sha256": ONE_AKI_SHA256,
        "chemistry_profile_id": "profile-v1",
        "chemistry_profile_sha256": "a" * 64,
        "catalog_digest": "b" * 64,
        "requested_settings": {
            "replicas": 1,
            "random_seed": 1,
            "padding_nm": 1.0,
            "salt_molar": 0.15,
            "neutralize": True,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
            "timestep_fs": 2.0,
            "minimization_steps": 1,
            "nvt_ps": 1.0,
            "npt_ps": 1.0,
            "production_ns": 0.001,
            "trajectory_interval_ps": 1.0,
            "energy_interval_ps": 0.2,
            "checkpoint_interval_minutes": 1.0,
            "ntomp": 1,
        },
        "launch_context_id": None,
    }


def test_server_file_capability_gates_inspect_content_preview_and_launch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from routers import molecular_dynamics
    from services.md import starting_structures

    root = tmp_path / "server-files"
    root.mkdir()
    candidate = root / "visible-1aki.pdb"
    candidate.write_bytes(ONE_AKI.read_bytes())
    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"inputs": root})
    monkeypatch.setattr(
        starting_structures,
        "to_allowed_relative",
        lambda path: str(Path("inputs") / path.relative_to(root)),
    )

    class _View:
        catalog_digest = "b" * 64

        @staticmethod
        def get_profile(_profile_id: str) -> dict:
            return {
                "id": "profile-v1",
                "profile_sha256": "a" * 64,
                "states": {"selectable": True},
                "launch_constraints": {
                    "structure_sha256": ONE_AKI_SHA256,
                    "engine": "gromacs",
                    "replicas": 1,
                    "padding_nm": 1.0,
                    "salt_molar": 0.15,
                    "temperature_k": 300.0,
                    "pressure_bar": 1.0,
                    "timestep_fs": 2.0,
                    "max_minimization_steps": 100,
                    "max_nvt_steps": 1000,
                    "max_npt_steps": 1000,
                    "max_production_steps": 1000,
                },
                "scientific_validation": {"scope": {"launch_scope": "test"}},
            }

    class _Catalog:
        @staticmethod
        def view() -> _View:
            return _View()

    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _Catalog())
    monkeypatch.setenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED", "1")
    listed = client.get("/api/molecular-dynamics/starting-structures/server-files")
    assert listed.status_code == 200, listed.text
    handle = listed.json()["items"][0]["id"]
    inspected = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={"source_ref": {"kind": "server_file", "id": handle}},
    )
    assert inspected.status_code == 200, inspected.text
    content_url = inspected.json()["viewer"]["url"]

    monkeypatch.delenv("BMS_MD_SERVER_FILE_BROWSER_ENABLED")
    for response in (
        client.post(
            "/api/molecular-dynamics/starting-structures/inspect",
            json={"source_ref": {"kind": "server_file", "id": handle}},
        ),
        client.get(content_url),
        client.post(
            "/api/molecular-dynamics/launch-preview",
            json={
                "schema_version": "bms.md.launch-preview-request.v1",
                "intent": _server_file_intent(handle),
            },
        ),
        client.post(
            "/api/molecular-dynamics/launch",
            json={
                "schema_version": "bms.md.launch-request.v1",
                "intent": _server_file_intent(handle),
                "preview_digest": "0" * 64,
            },
        ),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "MD_SERVER_FILE_BROWSER_DISABLED"


def test_inspect_request_forbids_client_paths_and_urls(client: TestClient) -> None:
    response = client.post(
        "/api/molecular-dynamics/starting-structures/inspect",
        json={
            "source_ref": {
                "kind": "upload",
                "id": "opaque",
                "path": "/tmp/secret.pdb",
                "url": "https://attacker.invalid/file.pdb",
            }
        },
    )
    assert response.status_code == 422


def test_prediction_source_candidates_are_closed_owned_and_path_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import main
    from database import Base, Design, Job, get_session
    from services.md import starting_structures

    database_path = tmp_path / "candidate.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    structure_path = tmp_path / "results" / "candidate.pdb"
    structure_path.parent.mkdir(parents=True)
    structure_path.write_bytes(ONE_AKI.read_bytes())
    job_id = "11111111-1111-4111-8111-111111111111"
    design_id = "22222222-2222-4222-8222-222222222222"
    created_at = datetime(2026, 8, 25, 12, 0, 0)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            session.add(
                Job(
                    id=job_id,
                    name="Prediction candidate job",
                    status="completed",
                    queue_status="completed",
                    model_id="boltz2",
                    mode="predict",
                    params={"secret_input_path": "/managed/hidden/input.yaml"},
                    output_dir=str(tmp_path / "results"),
                    error_message="internal worker detail",
                    created_at=created_at,
                    completed_at=created_at,
                )
            )
            session.add(
                Design(
                    id=design_id,
                    job_id=job_id,
                    name="candidate-1",
                    pdb_path=str(structure_path),
                    source_pdb_path="/managed/hidden/source.pdb",
                    plddt_overall=91.5,
                    ptm=0.82,
                    iptm=0.71,
                    conf_score=0.87,
                )
            )
            await session.commit()

    asyncio.run(prepare())

    async def override_session():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(starting_structures, "get_allowed_roots", lambda: {"results": tmp_path / "results"})
    main.app.dependency_overrides[get_session] = override_session
    test_client = TestClient(main.app)
    try:
        response = test_client.get(
            f"/api/molecular-dynamics/prediction-jobs/{job_id}/source-candidates?limit=24"
        )
    finally:
        test_client.close()
        main.app.dependency_overrides.pop(get_session, None)
        asyncio.run(engine.dispose())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "bms.md.prediction-source-candidates.v1"
    assert "schema" not in payload
    assert payload["job"] == {
        "id": job_id,
        "name": "Prediction candidate job",
        "status": "completed",
        "model_id": "boltz2",
        "mode": "predict",
        "created_at": "2026-08-25T12:00:00",
        "started_at": None,
        "completed_at": "2026-08-25T12:00:00",
        "failure": None,
    }
    assert payload["candidates"] == [
        {
            "source_ref": {"kind": "design", "id": design_id},
            "name": "candidate-1",
            "format": "pdb",
            "eligible": True,
            "blocker_code": None,
            "metrics": {"plddt": 91.5, "ptm": 0.82, "iptm": 0.71, "confidence": 0.87},
            "created_at": "2026-08-25T12:00:00",
        }
    ]
    assert payload["next_cursor"] is None
    assert "params" not in response.text
    assert "output_dir" not in response.text
    assert "pdb_path" not in response.text
    assert "/managed/" not in response.text
