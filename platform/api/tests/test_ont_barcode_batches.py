from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import (
    Base,
    Job,
    MolBioNgsReceipt,
    NgsReferenceSetMapping,
    NgsReferenceSetManifest,
)
from routers import jobs, ont_runs
from schemas import JobCreate, JobStatus
from services import alignment_access, molbio_ngs_receipts
from services import ont_barcode_batches as batches


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_tree(root: Path, unit_ids: list[str]) -> tuple[str, dict[str, Any]]:
    basecall = root / "basecall"
    demux = root / "demux"
    units_dir = demux / "demux" / "units"
    manifests_dir = demux / "demux" / "manifests"
    basecall.mkdir(parents=True)
    units_dir.mkdir(parents=True)
    manifests_dir.mkdir(parents=True)

    source_calls_sha256 = "a" * 64
    preflight_path = basecall / "dorado_preflight.json"
    preflight_path.write_text(json.dumps({"schema": "biomodstack.dorado_preflight.v1"}), encoding="utf-8")
    preflight_sha256 = _sha256_bytes(preflight_path.read_bytes())
    unit_payloads: list[dict[str, Any]] = []
    for index, unit_id in enumerate(unit_ids, start=1):
        bam = units_dir / f"{unit_id}.bam"
        bam.write_bytes(f"BAM-{unit_id}".encode("ascii"))
        bam_sha256 = _sha256_bytes(bam.read_bytes())
        unit_payload = {
            "schema": "biomodstack.dorado_barcode_unit.v1",
            "unit_id": unit_id,
            "bam_path": f"demux/units/{unit_id}.bam",
            "bam_sha256": bam_sha256,
            "read_count": index,
            "source_calls_sha256": source_calls_sha256,
            "preflight_sha256": preflight_sha256,
        }
        unit_manifest = manifests_dir / f"{unit_id}.json"
        unit_manifest.write_text(json.dumps(unit_payload), encoding="utf-8")
        unit_payload["unit_manifest_path"] = f"demux/manifests/{unit_id}.json"
        unit_payload["unit_manifest_sha256"] = _sha256_bytes(unit_manifest.read_bytes())
        unit_payloads.append(unit_payload)

    total_reads = sum(item["read_count"] for item in unit_payloads)
    demux_payload = {
        "schema": "biomodstack.dorado_demux.v1",
        "preflight_sha256": preflight_sha256,
        "source_calls": {"sha256": source_calls_sha256, "read_count": total_reads},
        "total_reads": total_reads,
        "units": unit_payloads,
    }
    catalog_payload = {
        "schema": "biomodstack.dorado_barcode_units.v1",
        "units": unit_payloads,
    }
    runtime_payload = {
        "schema": "biomodstack.dorado_runtime_provenance.v1",
        "preflight_sha256": preflight_sha256,
        "calls_bam": {"sha256": source_calls_sha256, "read_count": total_reads},
    }
    demux_path = demux / "demux_manifest.json"
    catalog_path = demux / "per_barcode_units.json"
    runtime_path = basecall / "dorado_runtime_provenance.json"
    demux_path.write_text(json.dumps(demux_payload), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog_payload), encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime_payload), encoding="utf-8")

    products = {
        "demux_manifest": {"path": "demux/demux_manifest.json", "sha256": _sha256_bytes(demux_path.read_bytes())},
        "barcode_units_manifest": {"path": "demux/per_barcode_units.json", "sha256": _sha256_bytes(catalog_path.read_bytes())},
        "dorado_preflight": {"path": "basecall/dorado_preflight.json", "sha256": _sha256_bytes(preflight_path.read_bytes())},
        "dorado_runtime_provenance": {"path": "basecall/dorado_runtime_provenance.json", "sha256": _sha256_bytes(runtime_path.read_bytes())},
    }
    terminal = {
        "schema": "biomodstack.ont_dorado_terminal_products.v1",
        "stage": "dorado_demux",
        "products": products,
    }
    source = Job(
        id="source-job",
        name="completed source",
        model_id="nanopore",
        mode="basecall_dna",
        params={"barcode_kit": "SQK-RBK114-96"},
        status=JobStatus.COMPLETED.value,
        output_dir=str(root),
        completed_stages=["dorado_demux"],
        stage_outputs={"dorado_demux": [str(demux_path)]},
        lineage_root_job_id="source-job",
        provenance={"ont_dorado_terminal_products": terminal},
    )
    return products["demux_manifest"]["sha256"], {"job": source, "terminal": terminal}


def _add_receipts(
    session: AsyncSession,
    inputs_root: Path,
    count: int,
) -> list[str]:
    receipt_ids: list[str] = []
    for index in range(1, count + 1):
        receipt_id = f"receipt-{index:02d}"
        sequence = "ACGT" + "A" * index
        fasta = f">receipt_{index}\n{sequence}\n".encode("ascii")
        path = inputs_root / "molbio_ngs_receipts" / receipt_id / "expected_reference.fasta"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fasta)
        session.add(
            MolBioNgsReceipt(
                id=receipt_id,
                sequence_id=f"sequence-{index:02d}",
                revision_id=f"revision-{index:02d}",
                revision_sha256=_sha256_bytes(sequence.encode("ascii")),
                reference_snapshot_path=str(path.resolve()),
                reference_snapshot_sha256=_sha256_bytes(fasta),
                expires_at=datetime.utcnow() + timedelta(minutes=15),
                created_at=datetime.utcnow(),
            )
        )
        receipt_ids.append(receipt_id)
    return receipt_ids


@pytest_asyncio.fixture
async def batch_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inputs_root = tmp_path / "inputs"
    source_root = tmp_path / "source"
    unit_ids = [f"barcode{index:02d}" for index in range(1, 11)]
    demux_sha256, source_metadata = _source_tree(source_root, unit_ids)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'main.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    session.add(source_metadata["job"])
    receipt_ids = _add_receipts(session, inputs_root, len(unit_ids))
    await session.commit()
    monkeypatch.setattr(batches, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(molbio_ngs_receipts, "get_inputs_dir", lambda: inputs_root)
    yield SimpleNamespace(
        engine=engine,
        session=session,
        source=source_metadata["job"],
        source_id=source_metadata["job"].id,
        source_root=source_root,
        demux_sha256=demux_sha256,
        unit_ids=unit_ids,
        receipt_ids=receipt_ids,
        inputs_root=inputs_root,
    )
    await session.close()
    await engine.dispose()


@pytest.fixture
def canonical_job_spy(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    fail_at: int | None = None

    def build_job(workflow_id: str, submit: ont_runs.OntNgsSubmitRequest, **_kwargs: Any) -> JobCreate:
        return JobCreate(
            name=str(submit.name),
            model_id="nanopore",
            mode="plasmid_qc" if workflow_id == "ont_plasmid_qc" else "construct_screening",
            params=dict(submit.params),
            pinned_gpu=submit.pinned_gpu,
        )

    async def create_job(
        job_data: JobCreate,
        _background_tasks: BackgroundTasks,
        session: AsyncSession,
        _preallocated_job_id: str | None = None,
        _commit: bool = True,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        assert _commit is False
        assert _preallocated_job_id is not None
        calls.append({"job": job_data, "id": _preallocated_job_id})
        child = Job(
            id=_preallocated_job_id,
            name=job_data.name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=dict(job_data.params),
            status=JobStatus.QUEUED.value,
            output_dir=f"/results/{_preallocated_job_id}",
            parent_job_id=job_data.parent_job_id,
            child_stage=job_data.child_stage,
            batch_id=job_data.batch_id,
            batch_name=job_data.batch_name,
            lineage_root_job_id=job_data.params.get("lineage_root_job_id"),
            source_stage_job_id=job_data.params.get("source_stage_job_id"),
            source_stage_family=job_data.params.get("source_stage_family"),
            source_stage_mode=job_data.params.get("source_stage_mode"),
            source_selection_count=job_data.params.get("source_selection_count"),
            selection_source_type=job_data.params.get("selection_source_type"),
            selection_source_job_id=job_data.params.get("selection_source_job_id"),
            provenance={
                alignment_access.PROVENANCE_DIGEST_KEY: "c" * 64,
                alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
            },
        )
        session.add(child)
        await session.flush()
        if fail_at is not None and len(calls) == fail_at:
            raise RuntimeError("forced child flush failure")
        return SimpleNamespace(id=_preallocated_job_id)

    monkeypatch.setattr(ont_runs, "_job_create_for_ont_submit", build_job)
    monkeypatch.setattr(jobs, "create_job", create_job)
    def _set_fail_at(value: int | None) -> None:
        nonlocal fail_at
        fail_at = value

    return calls, _set_fail_at


def _request(
    context: SimpleNamespace,
    *,
    key: str = "batch-key",
    aliases: bool = True,
    mappings: list[dict[str, Any]] | None = None,
) -> batches.BarcodeBatchRequest:
    if mappings is None:
        mappings = [
            {
                "unit_id": unit_id,
                "sample_alias": f"sample-{index:02d}" if aliases else None,
                "molbio_ngs_receipt_id": context.receipt_ids[index - 1],
            }
            for index, unit_id in enumerate(context.unit_ids, start=1)
        ]
    return batches.BarcodeBatchRequest(
        idempotency_key=key,
        target_workflow="ont_plasmid_qc",
        name_prefix="atomic batch",
        pinned_gpu=2,
        mappings=mappings,
    )


def _http_request() -> Any:
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/jobs/source-job/barcode-batches",
            "headers": [],
            "query_string": b"",
        }
    )


async def _create(context: SimpleNamespace, request: batches.BarcodeBatchRequest) -> dict[str, Any]:
    response = Response()
    context.last_response = response
    source = await context.session.get(Job, context.source_id)
    return await batches.create_barcoded_reference_set(
        session=context.session,
        source_job=source,
        source_root=context.source_root,
        source_demux_manifest_sha256=context.demux_sha256,
        request=request,
        background_tasks=BackgroundTasks(),
        http_request=_http_request(),
        response=response,
    )


def test_authoritative_dorado_alias_is_inherited_and_cannot_be_changed() -> None:
    units = [{"unit_id": "barcode01", "sample_alias": "sheet-sample-a"}]
    request = batches.BarcodeBatchRequest(
        idempotency_key="alias-inheritance",
        target_workflow="ont_plasmid_qc",
        mappings=[
            batches.BarcodeBatchRequestMapping(
                unit_id="barcode01",
                sample_alias=None,
                molbio_ngs_receipt_id="receipt-01",
            )
        ],
    )
    normalized = batches._validate_request_mapping(request, units)
    assert normalized[0]["sample_alias"] == "sheet-sample-a"

    changed = request.model_copy(
        update={
            "mappings": [
                request.mappings[0].model_copy(update={"sample_alias": "browser-relabel"})
            ]
        }
    )
    with pytest.raises(batches.BarcodeBatchError, match="authoritative Dorado sample sheet"):
        batches._validate_request_mapping(changed, units)


@pytest.mark.asyncio
async def test_ten_child_atomic_success_preserves_bindings_and_aliases(batch_context, canonical_job_spy) -> None:
    context = batch_context
    calls, _ = canonical_job_spy
    result = await _create(context, _request(context))

    assert len(result["child_job_ids"]) == 10
    assert len(calls) == 10
    assert result["manifest"]["schema"] == "bms.ngs.reference-set.v1"
    assert result["manifest"]["mode"] == "barcoded"
    assert [entry["sample_alias"] for entry in result["manifest"]["entries"]] == [
        f"sample-{index:02d}" for index in range(1, 11)
    ]
    manifests = (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all()
    mappings = (await context.session.execute(select(NgsReferenceSetMapping).order_by(NgsReferenceSetMapping.unit_id))).scalars().all()
    children = (await context.session.execute(select(Job).where(Job.id.in_(result["child_job_ids"])).order_by(Job.id))).scalars().all()
    assert len(manifests) == 1
    assert len(mappings) == 10
    assert len(children) == 10
    assert all(child.parent_job_id == context.source_id for child in children)
    assert all(child.params["bam_force_realign"] is True for child in children)
    assert all(child.params["reference_fasta"].endswith("expected_reference.fasta") for child in children)
    assert all(child.params["reference_set_binding"]["manifest_sha256"] == result["manifest_sha256"] for child in children)
    assert all(child.params["barcode_mapping_binding"]["reference_set_id"] == result["reference_set_id"] for child in children)
    assert len([header for header, _value in context.last_response.raw_headers if header == b"set-cookie"]) == 10
    assert Path(manifests[0].manifest_path).is_file()


@pytest.mark.asyncio
async def test_missing_mapping_is_rejected_before_writes(batch_context, canonical_job_spy) -> None:
    context = batch_context
    request = _request(context, mappings=_request(context).mappings[:-1])
    with pytest.raises(batches.BarcodeBatchError, match="incomplete"):
        await _create(context, request)
    manifest_root = context.inputs_root / batches.REFERENCE_SET_ROOT_NAME
    assert not manifest_root.exists()
    assert (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all() == []
    assert (await context.session.execute(select(NgsReferenceSetMapping))).scalars().all() == []
    receipts = (await context.session.execute(select(MolBioNgsReceipt))).scalars().all()
    assert all(receipt.consumed_at is None for receipt in receipts)


@pytest.mark.asyncio
async def test_unclassified_is_retained_but_cannot_be_mapped(batch_context, canonical_job_spy) -> None:
    context = batch_context
    source_path = context.source_root / "demux" / "demux_manifest.json"
    demux_payload = json.loads(source_path.read_text(encoding="utf-8"))
    unclassified_bam = context.source_root / "demux" / "demux" / "units" / "unclassified.bam"
    unclassified_bam.write_bytes(b"BAM-unclassified")
    unclassified_manifest = context.source_root / "demux" / "demux" / "manifests" / "unclassified.json"
    unclassified_item = {
        "schema": "biomodstack.dorado_barcode_unit.v1",
        "unit_id": "unclassified",
        "bam_path": "demux/units/unclassified.bam",
        "bam_sha256": _sha256_bytes(unclassified_bam.read_bytes()),
        "read_count": 1,
        "source_calls_sha256": "a" * 64,
        "preflight_sha256": demux_payload["preflight_sha256"],
    }
    unclassified_manifest.write_text(json.dumps(unclassified_item), encoding="utf-8")
    unclassified_item["unit_manifest_path"] = "demux/manifests/unclassified.json"
    unclassified_item["unit_manifest_sha256"] = _sha256_bytes(unclassified_manifest.read_bytes())
    demux_payload["units"].append(unclassified_item)
    demux_payload["total_reads"] += 1
    demux_payload["source_calls"]["read_count"] += 1
    source_path.write_text(json.dumps(demux_payload), encoding="utf-8")
    catalog_path = context.source_root / "demux" / "per_barcode_units.json"
    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_payload["units"].append(unclassified_item)
    catalog_path.write_text(json.dumps(catalog_payload), encoding="utf-8")
    runtime_path = context.source_root / "basecall" / "dorado_runtime_provenance.json"
    runtime_payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime_payload["calls_bam"]["read_count"] += 1
    runtime_path.write_text(json.dumps(runtime_payload), encoding="utf-8")
    terminal = dict(context.source.provenance["ont_dorado_terminal_products"])
    products = dict(terminal["products"])
    products["demux_manifest"] = {"path": "demux/demux_manifest.json", "sha256": _sha256_bytes(source_path.read_bytes())}
    products["barcode_units_manifest"] = {"path": "demux/per_barcode_units.json", "sha256": _sha256_bytes(catalog_path.read_bytes())}
    products["dorado_runtime_provenance"] = {"path": "basecall/dorado_runtime_provenance.json", "sha256": _sha256_bytes(runtime_path.read_bytes())}
    terminal["products"] = products
    context.source.provenance = {"ont_dorado_terminal_products": terminal}
    context.demux_sha256 = products["demux_manifest"]["sha256"]
    await context.session.commit()
    with pytest.raises(batches.BarcodeBatchError, match="unclassified"):
        await _create(context, _request(context, mappings=[
            *[item.model_dump() for item in _request(context).mappings],
            {
                "unit_id": "unclassified",
                "sample_alias": "unclassified-sample",
                "molbio_ngs_receipt_id": "receipt-01",
            },
        ]))


@pytest.mark.asyncio
async def test_receipt_digest_failure_is_rejected_before_writes(batch_context, canonical_job_spy) -> None:
    context = batch_context
    receipt_path = context.inputs_root / "molbio_ngs_receipts" / "receipt-01" / "expected_reference.fasta"
    receipt_path.write_text(">tampered\nTTTT\n", encoding="ascii")
    with pytest.raises(batches.BarcodeBatchError, match="digest"):
        await _create(context, _request(context))
    assert (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all() == []
    receipts = (await context.session.execute(select(MolBioNgsReceipt))).scalars().all()
    assert all(receipt.consumed_at is None for receipt in receipts)


@pytest.mark.asyncio
async def test_child_flush_failure_rolls_back_children_and_leaves_receipts_reusable(batch_context, canonical_job_spy) -> None:
    context = batch_context
    calls, set_fail_at = canonical_job_spy
    set_fail_at(5)
    with pytest.raises(batches.BarcodeBatchError) as raised:
        await _create(context, _request(context))
    assert len(calls) == 5
    children = (await context.session.execute(select(Job).where(Job.id != context.source_id))).scalars().all()
    assert children == []
    assert (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all() == []
    assert (await context.session.execute(select(NgsReferenceSetMapping))).scalars().all() == []
    receipts = (await context.session.execute(select(MolBioNgsReceipt))).scalars().all()
    assert all(receipt.consumed_at is None and receipt.consumed_job_id is None for receipt in receipts)
    manifest_root = context.inputs_root / batches.REFERENCE_SET_ROOT_NAME
    assert not any(manifest_root.iterdir())


@pytest.mark.asyncio
async def test_idempotent_replay_returns_exact_children_and_conflict_is_409(batch_context, canonical_job_spy) -> None:
    context = batch_context
    calls, _ = canonical_job_spy
    request = _request(context)
    first = await _create(context, request)
    replay = await _create(context, request)
    assert replay["reference_set_id"] == first["reference_set_id"]
    assert replay["child_job_ids"] == first["child_job_ids"]
    assert len(calls) == 10
    conflicting = request.model_copy(update={"mappings": [
        item.model_copy(update={"sample_alias": "different-alias"}) if item.unit_id == "barcode01" else item
        for item in request.mappings
    ]})
    with pytest.raises(batches.BarcodeBatchError) as raised:
        await _create(context, conflicting)
    assert raised.value.status_code == 409
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
