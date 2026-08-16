from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNArtifact, FrustraMPNNResult, Job, get_session
from routers import frustrampnn as frustrampnn_router
from services.frustrampnn import jobs as child_jobs
from services.frustrampnn import structure as structure_module
from services.frustrampnn.configuration import execution_configuration
from services.frustrampnn.contracts import canonical_sha256
from services.frustrampnn import settings as settings_module
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    resolve_effective_settings,
)


def _row(
    *,
    entity: str,
    source_entity: str,
    label_chain: str,
    auth_chain: str,
    auth_seq: int,
    sequence_index: int,
    normalized_chain: str,
    model_position: int,
    wt: str | None,
    status: str = "mapped",
    selected_altloc: str = "A",
) -> dict[str, object]:
    complete = status == "mapped"
    return {
        "entity_instance_id": entity,
        "source_entity_id": source_entity,
        "label_asym_id": label_chain,
        "auth_asym_id": auth_chain,
        "label_seq_id": sequence_index,
        "auth_seq_id": auth_seq,
        "insertion_code": "",
        "sequence_index": sequence_index,
        "pdb_chain_id": normalized_chain,
        "pdb_residue_id": auth_seq,
        "pdb_insertion_code": "",
        "model_position": model_position,
        "residue_name": {"M": "MET", "G": "GLY", "L": "LEU", None: "MSE"}[wt],
        "wt": wt,
        "selected_model": 2,
        "selected_altloc": selected_altloc,
        "backbone_complete": complete,
        "backbone_atoms": {
            "N": f"{normalized_chain}:{model_position}:N" if complete else None,
            "CA": f"{normalized_chain}:{model_position}:CA" if complete else None,
            "C": f"{normalized_chain}:{model_position}:C" if complete else None,
            "O": f"{normalized_chain}:{model_position}:O" if complete else None,
        },
        "status": status,
        "reason": None if complete else "nonstandard protein residue: MSE",
    }


def structure_map_fixture() -> dict[str, object]:
    rows = [
        _row(
            entity="entity-2",
            source_entity="2",
            label_chain="BB",
            auth_chain="Y",
            auth_seq=7,
            sequence_index=1,
            normalized_chain="B",
            model_position=0,
            wt="G",
            selected_altloc="",
        ),
        _row(
            entity="entity-1",
            source_entity="1",
            label_chain="AA",
            auth_chain="X",
            auth_seq=10,
            sequence_index=1,
            normalized_chain="A",
            model_position=0,
            wt="M",
        ),
        _row(
            entity="entity-1",
            source_entity="1",
            label_chain="AA",
            auth_chain="X",
            auth_seq=11,
            sequence_index=2,
            normalized_chain="A",
            model_position=1,
            wt="L",
        ),
        _row(
            entity="entity-1",
            source_entity="1",
            label_chain="AA",
            auth_chain="X",
            auth_seq=12,
            sequence_index=3,
            normalized_chain="A",
            model_position=2,
            wt=None,
            status="nonstandard_residue",
        ),
    ]
    sequence = "GML"
    return {
        "schema_name": "frustrampnn_structure_map",
        "schema_version": 1,
        "target_id": "target-1",
        "parent_job_id": "job-1",
        "candidate_id": "candidate-1",
        "source_format": "mmcif",
        "source_sha256": "1" * 64,
        "source_bytes": 1234,
        "identity_authority": "mmcif_atom_site_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": "1" * 64,
        "normalized_pdb_sha256": "2" * 64,
        "selected_source_model": 2,
        "altloc_policy": "blank_or_explicit:A",
        "normalizer_version": "frustrampnn_structure_normalizer_v1",
        "model_ready_sequence": sequence,
        "model_ready_sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        "excluded_records": [],
        "rows": rows,
    }


def _entity(entity: str, source: str, label: str, auth: str) -> dict[str, object]:
    return {
        "entity_instance_id": entity,
        "source_entity_id": source,
        "label_asym_id": label,
        "auth_asym_id": auth,
    }


def _residue(
    entity: str,
    source: str,
    label: str,
    auth: str,
    auth_seq: int,
    sequence_index: int,
) -> dict[str, object]:
    return {
        **_entity(entity, source, label, auth),
        "auth_seq_id": auth_seq,
        "insertion_code": "",
        "sequence_index": sequence_index,
    }


def _settings(selection: dict[str, object] | None = None) -> FrustraMPNNRequestedSettings:
    return FrustraMPNNRequestedSettings.model_validate(
        {
            "schema_name": "frustrampnn_settings",
            "schema_version": 1,
            "protein_selection": selection or {"mode": "all_protein_entities"},
            "source_structure": {
                "selected_model_number": 2,
                "preferred_altloc": "A",
            },
            "classification_policy": {
                "mode": "custom",
                "high_max": -0.75,
                "minimal_min": 0.25,
            },
        }
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(frustrampnn_router.router)
    return TestClient(app)


def _pdb_atom(
    serial: int,
    atom: str,
    residue: str,
    model: int,
    *,
    altloc: str = "",
    x: float = 1.0,
) -> str:
    del model
    element = next(character for character in atom if character.isalpha())
    atom_field = atom if len(atom) == 4 else f" {atom:<3}"
    return (
        f"ATOM  {serial:5d} {atom_field}{altloc or ' '}{residue:>3} A{10:4d} "
        f"   {x:8.3f}{(x + 1):8.3f}{(x + 2):8.3f}{1.0:6.2f}{20.0:6.2f}"
        f"          {element:>2}  \n"
    )


def live_source_bytes() -> bytes:
    lines: list[str] = []
    serial = 1
    for model, residue in ((1, "GLY"), (2, "ALA")):
        lines.append(f"MODEL     {model:4d}\n")
        for atom in ("N", "CA", "C", "O"):
            lines.append(_pdb_atom(serial, atom, residue, model, x=float(serial)))
            serial += 1
        lines.append(_pdb_atom(serial, "CB", residue, model, altloc="B", x=90.0))
        serial += 1
        lines.append(_pdb_atom(serial, "CB", residue, model, altloc="A", x=80.0))
        serial += 1
        lines.append("ENDMDL\n")
    lines.append("END\n")
    return "".join(lines).encode("ascii")


def _public_settings_payload(settings: FrustraMPNNRequestedSettings | None = None) -> dict:
    payload = (settings or _settings()).model_dump(mode="json")
    payload.pop("settings_value_origin", None)
    selection = payload["protein_selection"]
    selection.setdefault("entities", [])
    selection.setdefault("residues", [])
    return payload


def test_no_follow_reader_rechecks_stream_bound_after_fstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "growing-source.pdb"
    source.write_bytes(b"123456789")
    actual = source.stat()
    monkeypatch.setattr(
        structure_module.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(st_mode=actual.st_mode, st_size=8),
    )

    with pytest.raises(structure_module.StructureNormalizationError, match="bound"):
        structure_module.read_structure_bytes(source, max_bytes=8)


def test_pure_resolver_uses_only_mapped_source_rows_and_cross_binds_exact_map() -> None:
    structure_map = structure_map_fixture()

    effective = resolve_effective_settings(_settings(), structure_map)
    configuration = execution_configuration(effective)

    assert [chain.pdb_chain_id for chain in effective.resolved_chains] == ["A", "B"]
    assert [
        (row.auth_asym_id, row.auth_seq_id, row.sequence_index, row.wt, row.model_position)
        for chain in effective.resolved_chains
        for row in chain.residues
    ] == [
        ("X", 10, 1, "M", 0),
        ("X", 11, 2, "L", 1),
        ("Y", 7, 1, "G", 0),
    ]
    assert effective.resolution_identity.model_dump() == {
        "source_artifact_sha256": structure_map["source_sha256"],
        "structure_map_schema_name": "frustrampnn_structure_map",
        "structure_map_schema_version": 1,
        "structure_map_sha256": canonical_sha256(structure_map),
        "normalized_pdb_sha256": structure_map["normalized_pdb_sha256"],
    }
    assert configuration.effective_settings == effective
    assert configuration.structure_map_sha256 == canonical_sha256(structure_map)
    assert resolve_effective_settings(_settings(), structure_map).model_dump() == effective.model_dump()


def test_resolver_requires_exact_entity_and_residue_selector_coverage() -> None:
    structure_map = structure_map_fixture()
    selected_entity = _settings(
        {
            "mode": "selected_entities",
            "entities": [_entity("entity-2", "2", "BB", "Y")],
        }
    )
    entity_effective = resolve_effective_settings(selected_entity, structure_map)
    assert [chain.entity.entity_instance_id for chain in entity_effective.resolved_chains] == [
        "entity-2"
    ]

    selected_residue = _settings(
        {
            "mode": "selected_residues",
            "residues": [_residue("entity-1", "1", "AA", "X", 11, 2)],
        }
    )
    residue_effective = resolve_effective_settings(selected_residue, structure_map)
    assert [row.auth_seq_id for row in residue_effective.resolved_chains[0].residues] == [11]

    stale = _settings(
        {
            "mode": "selected_residues",
            "residues": [_residue("entity-1", "1", "AA", "X", 11, 99)],
        }
    )
    with pytest.raises(settings_module.SourceResolutionError, match="stale|mismatch") as stale_error:
        resolve_effective_settings(stale, structure_map)
    assert stale_error.value.location[-1] == 0

    excluded = _settings(
        {
            "mode": "selected_residues",
            "residues": [_residue("entity-1", "1", "AA", "X", 12, 3)],
        }
    )
    with pytest.raises(settings_module.SourceResolutionError, match="not scoreable|excluded"):
        resolve_effective_settings(excluded, structure_map)


def test_resolver_rejects_malformed_ambiguous_or_policy_mismatched_maps() -> None:
    structure_map = structure_map_fixture()

    wrong_model = _settings().model_copy(
        update={
            "source_structure": _settings().source_structure.model_copy(
                update={"selected_model_number": 1}
            )
        }
    )
    with pytest.raises(settings_module.SourceResolutionError, match="model") as model_error:
        resolve_effective_settings(wrong_model, structure_map)
    assert model_error.value.location == ("source_structure", "selected_model_number")

    wrong_altloc = _settings().model_copy(
        update={
            "source_structure": _settings().source_structure.model_copy(
                update={"preferred_altloc": "B"}
            )
        }
    )
    with pytest.raises(settings_module.SourceResolutionError, match="altloc"):
        resolve_effective_settings(wrong_altloc, structure_map)

    for mutation in ("unknown", "tampered_sequence", "ambiguous", "nonfinite"):
        malformed = copy.deepcopy(structure_map)
        if mutation == "unknown":
            malformed["source_path"] = "/private/source.cif"
        elif mutation == "tampered_sequence":
            malformed["model_ready_sequence"] = "MMM"
        elif mutation == "ambiguous":
            malformed["rows"].append(copy.deepcopy(malformed["rows"][0]))
        else:
            malformed["source_bytes"] = float("nan")
        with pytest.raises(settings_module.SourceResolutionError, match="structure map"):
            resolve_effective_settings(_settings(), malformed)


@pytest_asyncio.fixture
async def governed_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results_root = tmp_path / "owned-results"
    results_root.mkdir()
    monkeypatch.setattr(child_jobs, "get_results_dir", lambda: results_root)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inspection.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    source = live_source_bytes()
    selection = child_jobs.upload_selection(
        filename="multi-model.pdb",
        payload=source,
        expected_sha256=hashlib.sha256(source).hexdigest(),
    )
    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=_settings(),
        )
        root = Path(child.output_dir)
        envelope = child.params[child_jobs.ENVELOPE_KEY]
        lineage = envelope["selection"][0]
        invocation_id = lineage["invocation_id"]
        request_path = root / lineage["component_request_relative_path"]
        request_bytes = request_path.read_bytes()
        bundle = root / "fixture-result"
        bundle.mkdir()
        retained_request = bundle / "workflow_component_request_v2.json"
        retained_request.write_bytes(request_bytes)
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        artifact_contracts = [
            (retained_request.name, "workflow_component_request", 2, None),
            ("normalized_input.pdb", None, None, {"kind": "residues", "count": 1}),
            (
                "frustrampnn_structure_map_v1.json",
                "frustrampnn_structure_map",
                1,
                {"kind": "residues", "count": 1},
            ),
            ("raw_frustrampnn.csv", None, None, {"kind": "rows", "count": 1}),
            (
                "frustrampnn_landscape_v2.json",
                "frustrampnn_landscape",
                2,
                {"kind": "residues", "count": 1},
            ),
            (
                "frustrampnn_summary_v2.json",
                "frustrampnn_summary",
                2,
                {"kind": "records", "count": 1},
            ),
            ("frustrampnn_stdout.log", None, None, None),
            ("frustrampnn_stderr.log", None, None, None),
            (
                "frustrampnn_execution_receipt_v2.json",
                "frustrampnn_execution_receipt",
                2,
                {"kind": "records", "count": 1},
            ),
            (
                "frustrampnn_statistics_v1.json",
                "frustrampnn_statistics",
                1,
                {"kind": "records", "count": 1},
            ),
        ]
        manifest_artifacts = [
            {
                "relative_path": name,
                "schema_name": schema_name,
                "schema_version": schema_version,
                "sha256": request_sha256 if index == 0 else f"{index:x}" * 64,
                "bytes": len(request_bytes) if index == 0 else 1,
                "cardinality": cardinality,
            }
            for index, (name, schema_name, schema_version, cardinality) in enumerate(
                artifact_contracts
            )
        ]
        result_manifest = {
            "schema_name": "frustrampnn_result_manifest",
            "schema_version": 2,
            "invocation_id": invocation_id,
            "parent_job_id": child.id,
            "candidate_id": lineage["candidate_id"],
            "request_sha256": request_sha256,
            "source_artifact_sha256": lineage["sha256"],
            "execution_configuration_sha256": lineage["launch_authority"][
                "configuration_sha256"
            ],
            "statistics_sha256": "f" * 64,
            "comparison_compatibility_id": "0" * 64,
            "artifact_count": 10,
            "artifacts": manifest_artifacts,
        }
        session.add(
            FrustraMPNNResult(
                parent_job_id=child.id,
                invocation_id=invocation_id,
                parent_workflow_id="frustrampnn_analysis",
                candidate_id=lineage["candidate_id"],
                design_id=None,
                requiredness="required",
                request_sha256=request_sha256,
                source_artifact_id=None,
                source_artifact_sha256=lineage["sha256"],
                manifest_sha256=canonical_sha256(result_manifest),
                manifest_json=result_manifest,
                summary_sha256="b" * 64,
                summary_json={},
                runtime_identity_json={},
                assigned_gpu_json={},
                terminal_result_json={"component_contract_version": "2.0"},
                settings_sha256="c" * 64,
                effective_settings_sha256="d" * 64,
                effective_settings_json=lineage["launch_authority"]["effective_settings"],
                capability_inventory_sha256="e" * 64,
                statistics_sha256="f" * 64,
                statistics_json={},
                comparison_compatibility_id="0" * 64,
            )
        )
        session.add(
            FrustraMPNNArtifact(
                artifact_id="owned-request-artifact",
                parent_job_id=child.id,
                invocation_id=invocation_id,
                role="component_request",
                relative_path=retained_request.name,
                storage_path=str(retained_request),
                content_sha256=request_sha256,
                size_bytes=len(request_bytes),
                media_type="application/json",
                metadata_json={"schema_name": "workflow_component_request", "schema_version": 2},
            )
        )
        await session.commit()
        job_id = str(child.id)
        source_path = root / lineage["snapshot_relative_path"]
        map_path = root / lineage["structure_map_relative_path"]

    app = FastAPI()
    app.include_router(frustrampnn_router.router)

    async def override_session():
        async with sessions() as session:
            async def forbidden_commit() -> None:
                raise AssertionError("preview endpoint attempted a database commit")

            session.commit = forbidden_commit  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "sessions": sessions,
                "app": app,
                "job_id": job_id,
                "invocation_id": invocation_id,
                "source_path": source_path,
                "map_path": map_path,
                "request_path": retained_request,
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_inspection_reads_actual_models_altlocs_and_selected_projection() -> None:
    app = FastAPI()
    app.include_router(frustrampnn_router.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/sources/inspect/upload",
            files={"structure_file": ("multi.pdb", live_source_bytes(), "chemical/x-pdb")},
            data={"selected_model_number": "2", "preferred_altloc": "A"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source_models"] == [1, 2]
    assert payload["observed_altlocs"] == ["", "A", "B"]
    assert payload["selected_source_model"] == 2
    assert payload["selected_altloc"] == "A"
    assert [row["wt"] for row in payload["mapped_residues"]] == ["A"]
    assert payload["protein_entities"][0]["entity_instance_id"] == "pdb:A"
    assert "path" not in json.dumps(payload).lower()


@pytest.mark.asyncio
async def test_upload_inspection_rejects_unobserved_explicit_altloc() -> None:
    app = FastAPI()
    app.include_router(frustrampnn_router.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/sources/inspect/upload",
            files={"structure_file": ("multi.pdb", live_source_bytes(), "chemical/x-pdb")},
            data={"selected_model_number": "2", "preferred_altloc": "Z"},
        )

    assert response.status_code == 422
    assert "altloc" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_owned_inspection_loads_exact_manifest_attested_original(governed_api) -> None:
    response = await governed_api["client"].post(
        "/api/frustrampnn/sources/inspect/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "selected_model_number": 2,
            "preferred_altloc": "A",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source_models"] == [1, 2]
    assert payload["observed_altlocs"] == ["", "A", "B"]
    assert [row["wt"] for row in payload["mapped_residues"]] == ["A"]


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact", ["source_path", "map_path", "request_path"])
async def test_owned_inspection_rejects_tampered_attested_artifact(
    governed_api,
    artifact: str,
) -> None:
    path = governed_api[artifact]
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"tamper")
    response = await governed_api["client"].post(
        "/api/frustrampnn/sources/inspect/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "selected_model_number": 2,
            "preferred_altloc": "A",
        },
    )
    assert response.status_code == 409
    assert "identity" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["manifest_hash", "outside_request_path"])
async def test_owned_inspection_rejects_broken_manifest_or_artifact_closure(
    governed_api,
    authority: str,
) -> None:
    async with governed_api["sessions"]() as session:
        if authority == "manifest_hash":
            result = await session.get(
                FrustraMPNNResult,
                (governed_api["job_id"], governed_api["invocation_id"]),
            )
            result.manifest_sha256 = "0" * 64
        else:
            artifact = await session.get(FrustraMPNNArtifact, "owned-request-artifact")
            owned_root = governed_api["source_path"].parents[2]
            outside = owned_root.parent / "outside" / artifact.relative_path
            outside.parent.mkdir()
            outside.write_bytes(governed_api["request_path"].read_bytes())
            artifact.storage_path = str(outside)
        await session.commit()

    response = await governed_api["client"].post(
        "/api/frustrampnn/sources/inspect/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "selected_model_number": 2,
            "preferred_altloc": "A",
        },
    )
    assert response.status_code == 409
    assert "authority" in response.json()["detail"].lower() or "identity" in response.json()[
        "detail"
    ].lower()


@pytest.mark.asyncio
async def test_owned_historical_v1_source_authority_is_explicitly_unavailable(governed_api) -> None:
    async with governed_api["sessions"]() as session:
        result = await session.get(
            FrustraMPNNResult,
            (governed_api["job_id"], governed_api["invocation_id"]),
        )
        result.manifest_json = {"schema_name": "frustrampnn_result_manifest", "schema_version": 1}
        result.effective_settings_json = None
        await session.commit()

    response = await governed_api["client"].post(
        "/api/frustrampnn/sources/inspect/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "selected_model_number": 2,
            "preferred_altloc": "A",
        },
    )
    assert response.status_code == 409
    assert "historical" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_validation_preview_reinspects_source_and_returns_exact_custom_policy_without_mutation(
    governed_api,
) -> None:
    async with governed_api["sessions"]() as session:
        before_values: list[int] = []
        for model in (Job, FrustraMPNNResult, FrustraMPNNArtifact):
            count = await session.execute(select(func.count()).select_from(model))
            before_values.append(int(count.scalar_one()))
        before = tuple(before_values)

    response = await governed_api["client"].post(
        "/api/frustrampnn/settings/validate/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "settings": _public_settings_payload(),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation_scope"] == "preview_only"
    assert payload["normalized_requested_settings"]["settings_value_origin"] == "operator_request"
    assert payload["effective_settings"]["requested_settings"]["classification_policy"] == {
        "mode": "custom",
        "high_max": -0.75,
        "minimal_min": 0.25,
    }
    assert payload["execution_configuration"]["effective_settings"] == payload["effective_settings"]
    assert payload["hashes"]["structure_map_sha256"] == payload["effective_settings"][
        "resolution_identity"
    ]["structure_map_sha256"]
    assert "path" not in json.dumps(payload).lower()
    assert "command" not in json.dumps(payload).lower()

    async with governed_api["sessions"]() as session:
        after_values: list[int] = []
        for model in (Job, FrustraMPNNResult, FrustraMPNNArtifact):
            count = await session.execute(select(func.count()).select_from(model))
            after_values.append(int(count.scalar_one()))
        after = tuple(after_values)
    assert after == before


@pytest.mark.asyncio
async def test_upload_validation_rejects_partial_unknown_suffix_media_and_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frustrampnn_router, "_MAX_MULTIPART_STRUCTURE_BYTES", 8)
    app = FastAPI()
    app.include_router(frustrampnn_router.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        partial = _public_settings_payload()
        partial.pop("protein_selection")
        response = await client.post(
            "/api/frustrampnn/settings/validate/upload",
            files={"structure_file": ("source.pdb", b"1234", "chemical/x-pdb")},
            data={"settings": json.dumps(partial)},
        )
        assert response.status_code == 422

        response = await client.post(
            "/api/frustrampnn/sources/inspect/upload",
            files={"structure_file": ("source.txt", b"1234", "text/plain")},
            data={"selected_model_number": "1", "preferred_altloc": ""},
        )
        assert response.status_code == 422

        response = await client.post(
            "/api/frustrampnn/sources/inspect/upload",
            files={"structure_file": ("source.pdb", b"1234", "chemical/x-mmcif")},
            data={"selected_model_number": "1", "preferred_altloc": ""},
        )
        assert response.status_code == 422

        response = await client.post(
            "/api/frustrampnn/sources/inspect/upload",
            files={"structure_file": ("source.pdb", b"123456789", "chemical/x-pdb")},
            data={"selected_model_number": "1", "preferred_altloc": ""},
        )
        assert response.status_code == 413


@pytest.mark.asyncio
async def test_arbitrary_map_routes_are_gone_and_owned_models_are_closed(governed_api) -> None:
    client = governed_api["client"]
    assert (await client.post("/api/frustrampnn/sources/inspect", json={"structure_map": {}})).status_code == 404
    assert (await client.post("/api/frustrampnn/settings/validate", json={"structure_map": {}})).status_code == 404
    response = await client.post(
        "/api/frustrampnn/sources/inspect/owned",
        json={
            "job_id": governed_api["job_id"],
            "invocation_id": governed_api["invocation_id"],
            "selected_model_number": 2,
            "preferred_altloc": "A",
            "structure_map": structure_map_fixture(),
        },
    )
    assert response.status_code == 422

    schema = governed_api["app"].openapi()
    expected = {
        "/api/frustrampnn/sources/inspect/owned",
        "/api/frustrampnn/sources/inspect/upload",
        "/api/frustrampnn/settings/validate/owned",
        "/api/frustrampnn/settings/validate/upload",
    }
    assert expected <= set(schema["paths"])
    assert "/api/frustrampnn/sources/inspect" not in schema["paths"]
    assert "/api/frustrampnn/settings/validate" not in schema["paths"]
    for path in expected:
        operation = schema["paths"][path]["post"]
        assert "responses" in operation
        assert "200" in operation["responses"]
