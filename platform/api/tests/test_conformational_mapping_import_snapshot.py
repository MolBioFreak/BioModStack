from __future__ import annotations

import hashlib
import httpx
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRequest, ConformationalMappingSource, Job
import routers.conformational_mapping as cm_router
from routers.conformational_mapping import SubmitRequest, _external_import_target_ids, _validated_source_suffix
from services.conformational_mapping.contracts import canonical_sha256, request_sha256
from services.conformational_mapping.import_snapshot import (
    ImportSnapshotError,
    MAX_IMPORT_MMCIF_BYTES,
    build_import_snapshot_from_mmcif,
    build_staged_import_snapshots,
)


MMCIF = b"""data_minimal
_entry.id minimal
loop_
_entity.id
_entity.type
1 polymer
loop_
_struct_asym.id
_struct_asym.entity_id
ASYM_A 1
_entity_poly.entity_id 1
_entity_poly.type 'polypeptide(L)'
_entity_poly.pdbx_seq_one_letter_code_can AG
loop_
_entity_poly_seq.entity_id
_entity_poly_seq.num
_entity_poly_seq.mon_id
_entity_poly_seq.hetero
1 1 ALA n
1 2 GLY n
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . ALA ASYM_A 1 1 ? 0 0 0 1.00 10.00 10 ALA A N 1
ATOM 2 C CA . ALA ASYM_A 1 1 ? 1 0 0 1.00 10.00 10 ALA A CA 1
ATOM 3 C C . ALA ASYM_A 1 1 ? 2 0 0 1.00 10.00 10 ALA A C 1
ATOM 4 O O . ALA ASYM_A 1 1 ? 3 0 0 1.00 10.00 10 ALA A O 1
ATOM 5 N N . GLY ASYM_A 1 2 ? 4 0 0 1.00 10.00 11 GLY A N 1
ATOM 6 C CA . GLY ASYM_A 1 2 ? 5 0 0 1.00 10.00 11 GLY A CA 1
ATOM 7 C C . GLY ASYM_A 1 2 ? 6 0 0 1.00 10.00 11 GLY A C 1
ATOM 8 O O . GLY ASYM_A 1 2 ? 7 0 0 1.00 10.00 11 GLY A O 1
"""


AUDIT_QUOTED_UNDERSCORE_MMCIF = MMCIF.replace(
    b"_entry.id minimal\n",
    b"""loop_
_pdbx_audit_revision_item.ordinal
_pdbx_audit_revision_item.revision_ordinal
_pdbx_audit_revision_item.data_content_type
_pdbx_audit_revision_item.item
1 4 'Structure model' '_database_2.pdbx_DOI'
#
_entry.id minimal
""",
)


REAL_1UBQ_DIR = (
    Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq"
)


def test_real_1ubq_mmcif_is_retained_and_admitted_without_omission() -> None:
    source = REAL_1UBQ_DIR / "1UBQ.protein-only-authoritative.cif"
    payload = source.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == "5674064f6f64c87da2e1f564979c83220d68d3c126dddb19aad229d83b67dc0b"
    snapshot = build_import_snapshot_from_mmcif(
        payload,
        target_id="1ubq-real",
        candidate_id="cm_imp_1ubq_real_000000_deadbeef",
        original_source_path="registered_import/1UBQ.cif",
    )
    assert len(snapshot["entities"][0]["sequence"]) == 76
    assert snapshot["admission"]["atom_count"] == 602
    assert snapshot["admission"]["conversion_omissions"] == []
    assert snapshot["unsupported_fields"] == []



def test_import_snapshot_accepts_quoted_leading_underscore_loop_values() -> None:
    snapshot = build_import_snapshot_from_mmcif(
        AUDIT_QUOTED_UNDERSCORE_MMCIF,
        target_id="quoted-audit-control",
        candidate_id="cm_imp_quoted_audit_000000_deadbeef",
        original_source_path="registered_import/quoted-audit.cif",
    )
    assert snapshot["admission"]["atom_count"] == 8


def test_import_snapshot_is_deterministic_and_binds_explicit_mmcif_identity() -> None:
    first = build_import_snapshot_from_mmcif(
        MMCIF,
        target_id="1ubq-control",
        candidate_id="cm_imp_1ubq-control_000000_deadbeefdeadbeef",
        original_source_path="registered_import/000000_input.cif",
    )
    second = build_import_snapshot_from_mmcif(
        MMCIF,
        target_id="1ubq-control",
        candidate_id="cm_imp_1ubq-control_000000_deadbeefdeadbeef",
        original_source_path="registered_import/000000_input.cif",
    )

    assert first == second
    assert first["original_source_sha256"] == hashlib.sha256(MMCIF).hexdigest()
    assert first["entities"] == [{
        "entity_type": "protein",
        "source_entity_id": "1",
        "count": 1,
        "ordered_instance_ids": ["ASYM_A"],
        "sequence": "AG",
    }]
    assert first["instance_mappings"] == [{
        "source_entity_id": "1",
        "source_instance_id": "ASYM_A",
        "runtime_target_id": "1ubq-control",
        "runtime_entity_id": "1",
        "runtime_instance_id": "ASYM_A",
        "runtime_order": 0,
        "candidate_id": "cm_imp_1ubq-control_000000_deadbeefdeadbeef",
        "output_entity_id": "1",
        "output_label_asym_id": "ASYM_A",
        "output_auth_asym_id": "A",
        "output_entity_order": 0,
    }]
    assert first["admission"] == {
        "token_count": 2,
        "atom_count": 8,
        "token_limit": 20000,
        "conversion_omissions": [],
    }


def test_import_snapshot_rejects_nonprotein_or_incomplete_mmcif_without_omission() -> None:
    heterogeneous = MMCIF + b"HETATM 9 O O . HOH W 2 ? ? 8 0 0 1.00 10.00 1 HOH W O 1\n"
    with pytest.raises(ImportSnapshotError, match="protein-only|HETATM"):
        build_import_snapshot_from_mmcif(
            heterogeneous,
            target_id="target",
            candidate_id="candidate",
            original_source_path="registered_import/input.cif",
        )

    missing_residue = MMCIF.replace(b"ATOM 8 O O . GLY", b"# ATOM 8 O O . GLY")
    with pytest.raises(ImportSnapshotError, match="backbone"):
        build_import_snapshot_from_mmcif(
            missing_residue,
            target_id="target",
            candidate_id="candidate",
            original_source_path="registered_import/input.cif",
        )


def test_import_snapshot_rejects_atom_names_not_representable_downstream() -> None:
    lines = MMCIF.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("ATOM 2 "):
            fields = line.split()
            fields[3] = "CBLONG"
            fields[-2] = "CBLONG"
            lines[index] = " ".join(fields)
            break
    malformed = ("\n".join(lines) + "\n").encode("ascii")
    with pytest.raises(ImportSnapshotError, match="PDB atom name"):
        build_import_snapshot_from_mmcif(
            malformed,
            target_id="target",
            candidate_id="cm_imp_target_000000_deadbeef",
            original_source_path="registered_import/input.cif",
        )


def test_import_snapshot_rejects_sidechain_atom_element_disagreement() -> None:
    lines = MMCIF.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("ATOM 2 "):
            fields = line.split()
            fields[2] = "N"
            fields[3] = "CB"
            fields[-2] = "CB"
            lines[index] = " ".join(fields)
            break
    malformed = ("\n".join(lines) + "\n").encode("ascii")
    with pytest.raises(ImportSnapshotError, match="atom/element identity"):
        build_import_snapshot_from_mmcif(
            malformed,
            target_id="target",
            candidate_id="cm_imp_target_000000_deadbeef",
            original_source_path="registered_import/input.cif",
        )


def test_import_snapshot_rejects_width_valid_impossible_standard_atom() -> None:
    lines = MMCIF.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("ATOM 2 "):
            fields = line.split()
            fields[3] = "CX"
            fields[-2] = "CX"
            lines[index] = " ".join(fields)
            break
    malformed = ("\n".join(lines) + "\n").encode("ascii")
    with pytest.raises(ImportSnapshotError, match="not valid for standard residue"):
        build_import_snapshot_from_mmcif(
            malformed,
            target_id="target",
            candidate_id="cm_imp_target_000000_deadbeef",
            original_source_path="registered_import/input.cif",
        )


@pytest.mark.parametrize(
    ("field_index", "value", "message"),
    [
        (3, "Cα", "ASCII"),
        (3, "ca", "uppercase ASCII"),
        (15, "10000", "residue number"),
        (10, "100000.0", "x coordinate"),
        (14, "1000000.0", "B factor"),
        (9, "α", "insertion code"),
    ],
)
def test_import_snapshot_rejects_reproduced_mandatory_pdb_representation_failures(
    field_index: int, value: str, message: str,
) -> None:
    lines = MMCIF.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("ATOM 2 "):
            fields = line.split()
            fields[field_index] = value
            if field_index == 3:
                fields[-2] = value
            lines[index] = " ".join(fields)
            break
    malformed = ("\n".join(lines) + "\n").encode("utf-8")
    with pytest.raises(ImportSnapshotError, match=message):
        build_import_snapshot_from_mmcif(
            malformed,
            target_id="target",
            candidate_id="cm_imp_target_000000_deadbeef",
            original_source_path="registered_import/input.cif",
        )


def test_normal_structure_registration_is_mmcif_only() -> None:
    assert _validated_source_suffix("structure_upload", "candidate.cif") == ".cif"
    assert _validated_source_suffix("structure_artifact", "candidate.mmcif") == ".mmcif"
    for filename in ("candidate.pdb", "candidate.ent", "candidate"):
        with pytest.raises(HTTPException) as denied:
            _validated_source_suffix("structure_upload", filename)
        assert denied.value.status_code == 422


def test_external_import_targets_come_from_ordered_mmcif_handles_not_manual_snapshot() -> None:
    sources = [
        SimpleNamespace(
            source_id="source-one",
            relative_path="source-one/content.cif",
            metadata_json={"target_id": "first-target"},
        ),
        SimpleNamespace(
            source_id="source-two",
            relative_path="source-two/content.mmcif",
            metadata_json={},
        ),
    ]
    with pytest.raises(HTTPException, match="exactly one"):
        _external_import_target_ids(sources, registered_snapshot_id=None)
    assert _external_import_target_ids(sources[:1], registered_snapshot_id=None) == ["first-target"]
    with pytest.raises(HTTPException, match="derived automatically"):
        _external_import_target_ids(sources, registered_snapshot_id="manual-snapshot")
    sources[0].relative_path = "source-one/content.pdb"
    with pytest.raises(HTTPException, match="mmCIF"):
        _external_import_target_ids(sources[:1], registered_snapshot_id=None)


def test_staged_import_bytes_produce_candidate_bound_ordered_snapshot(tmp_path: Path) -> None:
    staged = tmp_path / "registered_import"
    path = staged / "structures" / "000000_input.cif"
    path.parent.mkdir(parents=True)
    path.write_bytes(MMCIF)
    digest = hashlib.sha256(MMCIF).hexdigest()
    coordinate = {
        "backend": "external_import",
        "target_id": "1ubq-control",
        "staged_index": 0,
        "source_content_sha256": digest,
        "staged_receipt_sha256": "a" * 64,
    }
    snapshots = build_staged_import_snapshots(
        staged_root=staged,
        entries=[{
            "destination_relative_path": "structures/000000_input.cif",
            "source_content_sha256": digest,
        }],
        targets=[{"target_id": "1ubq-control", "target_order": 0}],
        coordinates=[coordinate],
    )
    assert len(snapshots) == 1
    assert snapshots[0]["target_id"] == "1ubq-control"
    assert snapshots[0]["target_order"] == 0
    assert snapshots[0]["instance_mappings"][0]["candidate_id"].startswith("cm_imp_1ubq-control_000000_")
    assert snapshots[0]["original_source_path"] == "registered_import/structures/000000_input.cif"


def test_builder_rejects_duplicate_authoritative_atom_site_tags() -> None:
    duplicate = MMCIF + b"\n_atom_site.group_PDB ATOM\n"
    with pytest.raises(ImportSnapshotError, match="defined more than once"):
        build_import_snapshot_from_mmcif(
            duplicate,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )


def test_builder_rejects_altloc_and_conflicting_author_residue_identity() -> None:
    altloc = MMCIF.replace(b"N N . ALA", b"N N A ALA", 1)
    with pytest.raises(ImportSnapshotError, match="alternate conformations"):
        build_import_snapshot_from_mmcif(
            altloc,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )

    conflicting_auth = MMCIF.replace(b"11 GLY A O 1", b"12 GLY A O 1", 1)
    with pytest.raises(ImportSnapshotError, match="author residue identity is ambiguous"):
        build_import_snapshot_from_mmcif(
            conflicting_auth,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )


def test_builder_rejects_duplicate_atom_identity() -> None:
    duplicate_atom = MMCIF.replace(
        b"ATOM 4 O O . ALA ASYM_A 1 1 ? 3 0 0 1.00 10.00 10 ALA A O 1",
        b"ATOM 4 C CA . ALA ASYM_A 1 1 ? 3 0 0 1.00 10.00 10 ALA A CA 1",
        1,
    )
    with pytest.raises(ImportSnapshotError, match="duplicate atom identity"):
        build_import_snapshot_from_mmcif(
            duplicate_atom,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )


def test_builder_rejects_split_loops_trailing_truncation_and_missing_entity_authority() -> None:
    split_loop = MMCIF + b"\nloop_\n_atom_site.group_PDB\nATOM\n"
    trailing_truncation = MMCIF.replace(
        b"_entity_poly.pdbx_seq_one_letter_code_can AG",
        b"_entity_poly.pdbx_seq_one_letter_code_can AGV",
    ).replace(b"1 2 GLY n\n", b"1 2 GLY n\n1 3 VAL n\n")
    missing_entity = MMCIF.replace(b"1 polymer\n", b"1 polymer\n2 polymer\n")
    for payload, message in (
        (split_loop, "multiple definitions|defined more than once"),
        (trailing_truncation, "cover every canonical|complete canonical"),
        (missing_entity, "exactly one protein polymer entity"),
    ):
        with pytest.raises(ImportSnapshotError, match=message):
            build_import_snapshot_from_mmcif(
                payload,
                target_id="target-a",
                candidate_id="candidate-a",
                original_source_path="registered_import/input.cif",
            )


def test_builder_rejects_nonfinite_coordinates_and_duplicate_author_mapping() -> None:
    nonfinite = MMCIF.replace(b"? 0 0 0 1.00", b"? nan 0 0 1.00", 1)
    duplicate_author = MMCIF.replace(b"11 GLY A N 1", b"10 GLY A N 1", 1).replace(
        b"11 GLY A CA 1", b"10 GLY A CA 1", 1
    ).replace(b"11 GLY A C 1", b"10 GLY A C 1", 1).replace(
        b"11 GLY A O 1", b"10 GLY A O 1", 1
    )
    with pytest.raises(ImportSnapshotError, match="finite"):
        build_import_snapshot_from_mmcif(
            nonfinite,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )
    with pytest.raises(ImportSnapshotError, match="one author residue identity"):
        build_import_snapshot_from_mmcif(
            duplicate_author,
            target_id="target-a",
            candidate_id="candidate-a",
            original_source_path="registered_import/input.cif",
        )


def test_staged_builder_rejects_symlinked_path_components(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "structures").mkdir(parents=True)
    (real / "structures/input.cif").write_bytes(MMCIF)
    staged = tmp_path / "registered_import"
    staged.symlink_to(real, target_is_directory=True)
    digest = hashlib.sha256(MMCIF).hexdigest()
    with pytest.raises(ImportSnapshotError, match="opened safely"):
        build_staged_import_snapshots(
            staged_root=staged,
            entries=[{
                "destination_relative_path": "structures/input.cif",
                "source_content_sha256": digest,
            }],
            targets=[{"target_id": "target-a", "target_order": 0}],
            coordinates=[{
                "backend": "external_import",
                "target_id": "target-a",
                "source_content_sha256": digest,
                "staged_receipt_sha256": "a" * 64,
            }],
        )


def test_staged_builder_rejects_oversized_mmcif_before_reading_it(tmp_path: Path) -> None:
    staged = tmp_path / "registered_import"
    structures = staged / "structures"
    structures.mkdir(parents=True)
    source = structures / "000000_input.cif"
    with source.open("wb") as handle:
        handle.truncate(MAX_IMPORT_MMCIF_BYTES + 1)
    with pytest.raises(ImportSnapshotError, match="64 MiB"):
        build_staged_import_snapshots(
            staged_root=staged,
            entries=[{
                "destination_relative_path": "structures/000000_input.cif",
                "source_sha256": "0" * 64,
            }],
            targets=[{"target_id": "too-large", "target_order": 0}],
            coordinates=[{
                "backend": "external_import",
                "target_id": "too-large",
                "seed": 0,
                "sample_index": 0,
            }],
        )


@pytest.mark.asyncio
async def test_local_application_upload_and_submit_materializes_snapshot_and_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(cm_router, "get_results_dir", lambda: tmp_path / "results")
    request = Request({
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/api/conformational-mapping/sources", "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 42000), "server": ("127.0.0.1", 8000),
    })
    request.state.authenticated_principal = {"subject": "test-operator", "roles": ["operator"]}
    async with factory() as session:
        registered = await cm_router.register_source(
            request=request,
            source_kind="structure_upload",
            metadata_json=json.dumps({"target_id": "minimal-protein"}),
            file=UploadFile(filename="minimal.cif", file=io.BytesIO(MMCIF)),
            session=session,
        )
        body = SubmitRequest.model_validate({
            "name": "minimal imported mmCIF",
            "idempotency_key": "minimal-import-v1",
            "backend": "external_import",
            "registered_artifact_ids": [registered["source_id"]],
            "ordered_seeds": [0],
            "samples_per_seed": 1,
            "feature_policy": {"mode": "features_disabled_control_v1"},
            "runtime_policy": {"use_default_params": True},
            "analysis_policy": {
                "sign_zero_epsilon": 1e-6,
                "clash_detector_id": "bms_clash",
                "clash_detector_version": "1",
                "outer_support_minimum": 1.0,
                "inner_support_minimum": 1.0,
                "sign_consistency_minimum": 1.0,
                "clash_free_minimum": 1.0,
                "rank_stability_minimum": 1.0,
                "minimum_common_ranked_universe_size": 3,
            },
        })
        response = Response()
        receipt = await cm_router.submit_request(body, request, response, session)
        assert receipt["status"] == "queued"
        assert receipt["expected_cardinality"] == 1
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
        job = await session.get(Job, receipt["job_id"])
        assert job is not None
        assert job.vram_estimate_mb == 12_000
        assert job.sequence_length == 2
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingRequest)) == 1
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingSource)) == 1

    root = tmp_path / "results" / f"conformational_mapping_{receipt['request_id']}"
    snapshots = json.loads((root / "cm_complex_snapshots_v1.json").read_text())
    assert snapshots[0]["entities"][0]["sequence"] == "AG"
    assert snapshots[0]["target_id"] == "minimal-protein"
    persisted_request = json.loads((root / "cm_request_v1.json").read_text())
    plan = json.loads((root / "cm_coordinate_plan_v1.json").read_text())
    assert persisted_request["source_snapshot_sha256"] == canonical_sha256(snapshots[0])
    assert persisted_request["request_sha256"] == request_sha256(persisted_request)
    assert plan["request_sha256"] == persisted_request["request_sha256"]
    assert plan["coordinate_plan_sha256"] == canonical_sha256({
        key: value for key, value in plan.items() if key != "coordinate_plan_sha256"
    })
    assert (root / "registered_import" / "cm_import_receipt_v1.json").is_file()
    runtime_registry = json.loads((root / "cm_runtime_registry_v1.json").read_text())
    assert runtime_registry["analysis_runtime"] == {
        "container_name": "frustrampnn.sif",
        "container_sha256": "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da",
    }
    await engine.dispose()


def test_rcsb_mmcif_bridge_rejects_non_accession_before_network() -> None:
    request = Request({
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/api/conformational-mapping/sources/rcsb/not-valid", "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 42000), "server": ("127.0.0.1", 8000),
    })
    with pytest.raises(HTTPException, match="four letters or digits") as error:
        import asyncio
        asyncio.run(cm_router.register_rcsb_mmcif_source("not-valid", request, None))
    assert error.value.status_code == 422


def _rcsb_request(accession: str = "1ubq") -> Request:
    request = Request({
        "type": "http", "method": "POST", "scheme": "http",
        "path": f"/api/conformational-mapping/sources/rcsb/{accession}", "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 42000), "server": ("127.0.0.1", 8000),
    })
    request.state.authenticated_principal = {"subject": "test-operator", "roles": ["operator"]}
    return request


def _mock_rcsb_client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "status_code", "message"),
    [
        (lambda request: httpx.Response(404, request=request), 404, "not found"),
        (lambda request: httpx.Response(503, request=request), 502, "unexpected status"),
        (lambda request: httpx.Response(200, request=request, content=b"<html>not mmcif</html>"), 502, "not raw mmCIF"),
    ],
)
async def test_rcsb_mmcif_bridge_fails_closed_for_remote_status_and_payload(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    status_code: int,
    message: str,
) -> None:
    monkeypatch.setattr(cm_router, "_rcsb_http_client", _mock_rcsb_client(handler))
    with pytest.raises(HTTPException, match=message) as error:
        await cm_router.register_rcsb_mmcif_source("1ubq", _rcsb_request(), None)
    assert error.value.status_code == status_code


@pytest.mark.asyncio
async def test_rcsb_mmcif_bridge_maps_timeout_without_registering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(cm_router, "_rcsb_http_client", _mock_rcsb_client(timeout))
    with pytest.raises(HTTPException, match="timed out") as error:
        await cm_router.register_rcsb_mmcif_source("1ubq", _rcsb_request(), None)
    assert error.value.status_code == 504


@pytest.mark.asyncio
async def test_rcsb_mmcif_bridge_stops_at_streaming_source_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(cm_router._SOURCE_MAX_BYTES, "structure_upload", 8)
    monkeypatch.setattr(
        cm_router,
        "_rcsb_http_client",
        _mock_rcsb_client(lambda request: httpx.Response(200, request=request, content=b"data_x\n" + b"A" * 64)),
    )
    with pytest.raises(HTTPException, match="source limit") as error:
        await cm_router.register_rcsb_mmcif_source("1ubq", _rcsb_request(), None)
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_rcsb_mmcif_bridge_streams_success_into_normal_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_register_source(**kwargs):
        captured.update(kwargs)
        captured["payload"] = await kwargs["file"].read()
        return {"source_id": "cm_src_rcsb", "source_kind": "structure_upload"}

    monkeypatch.setattr(
        cm_router,
        "_rcsb_http_client",
        _mock_rcsb_client(lambda request: httpx.Response(200, request=request, content=b"  \n" + MMCIF)),
    )
    monkeypatch.setattr(cm_router, "register_source", fake_register_source)
    receipt = await cm_router.register_rcsb_mmcif_source("1ubq", _rcsb_request(), object())
    assert receipt["source_id"] == "cm_src_rcsb"
    assert captured["payload"] == b"  \n" + MMCIF
    assert captured["source_kind"] == "structure_upload"
    metadata = json.loads(str(captured["metadata_json"]))
    assert metadata["rcsb_accession"] == "1UBQ"
    assert metadata["source"] == "rcsb_raw_mmcif"


@pytest.mark.asyncio
async def test_registered_source_deduplicates_and_commit_failure_removes_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dedup.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path / "data")
    request = _rcsb_request()
    async with factory() as session:
        first = await cm_router.register_source(
            request=request, source_kind="structure_upload", metadata_json="{}",
            file=UploadFile(filename="1ubq.cif", file=io.BytesIO(MMCIF)), session=session,
        )
        second = await cm_router.register_source(
            request=request, source_kind="structure_upload", metadata_json="{}",
            file=UploadFile(filename="1ubq.cif", file=io.BytesIO(MMCIF)), session=session,
        )
        assert first["source_id"] == second["source_id"]
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingSource)) == 1
    source_dirs = [path for path in (tmp_path / "data" / "conformational_mapping_sources").iterdir() if path.is_dir()]
    assert len(source_dirs) == 1
    await engine.dispose()

    class FailingSession:
        rolled_back = False
        added = None

        async def get(self, model, identity):
            return None

        def add(self, value):
            self.added = value

        async def commit(self):
            raise RuntimeError("commit failed")

        async def rollback(self):
            self.rolled_back = True

    failing = FailingSession()
    with pytest.raises(RuntimeError, match="commit failed"):
        await cm_router.register_source(
            request=request, source_kind="structure_upload", metadata_json="{}",
            file=UploadFile(filename="different.cif", file=io.BytesIO(MMCIF + b"# different\n")),
            session=failing,
        )
    assert failing.rolled_back is True
    assert failing.added is not None
    failed_id = failing.added.source_id
    assert not (tmp_path / "data" / "conformational_mapping_sources" / failed_id).exists()
