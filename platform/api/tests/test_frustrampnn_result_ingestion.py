from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, FrustraMPNNResult, Job
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.manifests import MANIFEST_PATH, build_result_manifest
from services.frustrampnn.persistence import FrustraMPNNPersistenceError
from services import result_ingester
from services.result_ingester import ingest_job_results
from services.result_state_integrity import finalize_successful_job


TESTS_DIR = Path(__file__).resolve().parent


def _fixture_module():
    name = "_frustrampnn_manifest_fixture_for_ingester"
    path = TESTS_DIR / "test_frustrampnn_manifests.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANIFEST_FIXTURE = _fixture_module()


def _bundle(root: Path, *, job_id: str, design_id: str | None = "design-1") -> dict:
    assert job_id == "job-1"
    root.mkdir(parents=True)
    MANIFEST_FIXTURE._bundle(root)
    if design_id is not None:
        path = root / "workflow_component_request_v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source_artifact"]["artifact_id"] = design_id
        path.write_bytes(canonical_json_bytes(payload))
    MANIFEST_FIXTURE._rehash_bundle(root)
    manifest = build_result_manifest(root)
    (root / MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    return json.loads((root / "workflow_component_result_v1.json").read_text(encoding="utf-8"))


def _terminal_outputs(root: Path) -> dict[str, list[str]]:
    return {
        "frustrampnn": [
            str(root / "workflow_component_result_v1.json"),
            str(root),
            str(root / MANIFEST_PATH),
        ]
    }


def _replace_identity(value, replacements: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace_identity(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_identity(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def _parent_bundle(
    root: Path,
    *,
    job_root: Path,
    job_id: str,
    producer_stage: str,
    producer_candidate_key: str,
    parent_workflow_id: str = "structure_prediction",
) -> tuple[str, str, Path]:
    from services.frustrampnn.identity import deterministic_candidate_id

    candidate_id = deterministic_candidate_id(
        parent_job_id=job_id,
        parent_workflow_id=parent_workflow_id,
        producer_stage=producer_stage,
        producer_candidate_key=producer_candidate_key,
    )
    invocation_id = f"frustrampnn:{candidate_id}"
    root.mkdir(parents=True)
    MANIFEST_FIXTURE._bundle(root)
    replacements = {
        "job-1": job_id,
        "candidate-1": candidate_id,
        "target-1": candidate_id,
        "invoke-1": invocation_id,
        "structure_prediction": parent_workflow_id,
    }
    json_names = [
        "workflow_component_request_v1.json",
        "frustrampnn_structure_map_v1.json",
        "frustrampnn_landscape_v1.json",
        "frustrampnn_summary_v1.json",
        "frustrampnn_execution_receipt_v1.json",
        "workflow_component_result_v1.json",
    ]
    for name in json_names:
        path = root / name
        payload = _replace_identity(json.loads(path.read_text(encoding="utf-8")), replacements)
        path.write_bytes(canonical_json_bytes(payload))

    source = job_root / producer_candidate_key
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(MANIFEST_FIXTURE._pdb())
    request_path = root / "workflow_component_request_v1.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["parent_workflow_id"] = parent_workflow_id
    request["source_artifact"] = {
        "relative_path": producer_candidate_key,
        "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
        "media_type": "chemical/x-pdb",
        "producer_stage": producer_stage,
        "artifact_id": candidate_id,
    }
    request_path.write_bytes(canonical_json_bytes(request))
    MANIFEST_FIXTURE._rehash_bundle(root)
    manifest = build_result_manifest(root)
    (root / MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    return candidate_id, invocation_id, source


async def _seed_parent_job(
    sessions: async_sessionmaker,
    *,
    job_id: str,
    job_root: Path,
    manifests: list[Path],
) -> None:
    async with sessions() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="running",
                queue_status="running",
                model_id="boltz2",
                mode="predict",
                params={"run_frustrampnn": True},
                output_dir=str(job_root),
                stage_outputs={
                    "frustrampnn": [
                        str(path)
                        for manifest in manifests
                        for path in (
                            manifest.parent / "workflow_component_result_v1.json",
                            manifest,
                        )
                    ]
                },
                completed_stages=["frustrampnn"],
                awaiting_input=False,
            )
        )
        await session.commit()


async def _seed_numeric_metadata_case(
    sessions: async_sessionmaker,
    *,
    tmp_path: Path,
    numeric_field: str,
    invalid_value: str,
) -> tuple[str, Path, list[tuple[str, str, Path]]]:
    """Seed two canonical candidates plus committed state that must survive rejection."""

    job_id = f"job-protein-numeric-{numeric_field}-{len(invalid_value)}"
    job_root = tmp_path / "job-root"
    bundles = [
        job_root / "frustrampnn" / "results" / name
        for name in ("candidate-a", "candidate-b")
    ]
    candidates = [
        _parent_bundle(
            bundle,
            job_root=job_root,
            job_id=job_id,
            parent_workflow_id="protein_design",
            producer_stage="protein_design:af2_terminal",
            producer_candidate_key=(
                f"frustrampnn/sources/af2/fold-{index}/sample-0/canonical.pdb"
            ),
        )
        for index, bundle in enumerate(bundles)
    ]
    results = job_root / "results"
    results.mkdir(parents=True)
    with (results / "all_designs.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["candidate_id", "description", numeric_field],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": candidates[0][0],
                "description": "valid-first",
                numeric_field: "0",
            }
        )
        writer.writerow(
            {
                "candidate_id": candidates[1][0],
                "description": "invalid-second",
                numeric_field: invalid_value,
            }
        )
    await _seed_parent_job(
        sessions,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH for bundle in bundles],
    )
    sentinel_path = job_root / "sentinel.pdb"
    sentinel_path.write_bytes(MANIFEST_FIXTURE._pdb())
    async with sessions() as session:
        session.add(
            Design(
                id="preexisting-sentinel",
                job_id=job_id,
                name="sentinel-exact",
                pdb_path=str(sentinel_path),
                num_helices=4,
                provenance={"preserve": "exact"},
            )
        )
        await session.commit()
    return job_id, job_root, candidates


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pr_plddt", "NaN"),
        ("pr_plddt", "Infinity"),
        ("pr_plddt", "-Infinity"),
        ("pr_plddt", "1e9999"),
        ("pr_plddt", "not-a-number"),
        ("pr_RoG", "-0.1"),
        ("seq_fampnn_psce", "-0.1"),
    ],
)
async def test_protein_design_float_metadata_is_strictly_prevalidated_before_orm_writes(
    tmp_path: Path,
    db,
    field: str,
    value: str,
) -> None:
    job_id, job_root, candidates = await _seed_numeric_metadata_case(
        db,
        tmp_path=tmp_path,
        numeric_field=field,
        invalid_value=value,
    )
    inserted: list[str] = []

    def capture_insert(_mapper, _connection, target: Design) -> None:
        inserted.append(target.id)

    event.listen(Design, "before_insert", capture_insert)
    try:
        async with db() as session:
            with pytest.raises(FrustraMPNNPersistenceError) as caught:
                await ingest_job_results(job_id, str(job_root), session, commit=True)
    finally:
        event.remove(Design, "before_insert", capture_insert)

    message = str(caught.value)
    assert candidates[1][0] in message
    assert field in message
    assert inserted == []
    async with db() as verification:
        sentinel = await verification.get(Design, "preexisting-sentinel")
        assert sentinel is not None
        assert (sentinel.name, sentinel.num_helices, sentinel.provenance) == (
            "sentinel-exact",
            4,
            {"preserve": "exact"},
        )
        for candidate_id, invocation_id, _source in candidates:
            assert await verification.get(Design, candidate_id) is None
            assert await verification.get(
                FrustraMPNNResult, (job_id, invocation_id)
            ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "3.9",
        "3e0",
        "true",
        "NaN",
        "Infinity",
        "1e9999",
        "-1",
        "9223372036854775808",
    ],
)
async def test_protein_design_count_metadata_requires_canonical_nonnegative_integer(
    tmp_path: Path,
    db,
    value: str,
) -> None:
    field = "pr_helices"
    job_id, job_root, candidates = await _seed_numeric_metadata_case(
        db,
        tmp_path=tmp_path,
        numeric_field=field,
        invalid_value=value,
    )
    inserted: list[str] = []

    def capture_insert(_mapper, _connection, target: Design) -> None:
        inserted.append(target.id)

    event.listen(Design, "before_insert", capture_insert)
    try:
        async with db() as session:
            with pytest.raises(FrustraMPNNPersistenceError) as caught:
                await ingest_job_results(job_id, str(job_root), session, commit=True)
    finally:
        event.remove(Design, "before_insert", capture_insert)

    message = str(caught.value)
    assert candidates[1][0] in message
    assert field in message
    assert inserted == []
    async with db() as verification:
        sentinel = await verification.get(Design, "preexisting-sentinel")
        assert sentinel is not None
        assert (sentinel.name, sentinel.num_helices, sentinel.provenance) == (
            "sentinel-exact",
            4,
            {"preserve": "exact"},
        )
        for candidate_id, invocation_id, _source in candidates:
            assert await verification.get(Design, candidate_id) is None
            assert await verification.get(
                FrustraMPNNResult, (job_id, invocation_id)
            ) is None


@pytest.mark.asyncio
async def test_protein_design_typed_metadata_accepts_zero_and_allowed_negative_without_coercing_strings(
    tmp_path: Path,
    db,
) -> None:
    job_id = "job-protein-typed-numeric-acceptance"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, _source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/fold-0/sample-0/canonical.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    fieldnames = [
        "candidate_id",
        "description",
        "fold_id",
        "seq_id",
        "opaque_key",
        "pr_helices",
        "pr_strands",
        "pr_RoG",
        "rfd_RoG",
        "seq_mpnn_score",
        "seq_fampnn_psce",
        "pr_plddt",
        "plddt",
        "pr_plddt_binder",
        "pr_plddt_target",
        "pr_pae_interaction",
        "pr_pae",
        "pae",
        "pr_rmsd",
        "pr_rmsd_binder",
        "conf_score",
        "ptm",
    ]
    with (results / "all_designs.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": candidate_id,
                "description": "  exact design string  ",
                "fold_id": "007",
                "seq_id": "3e0",
                "opaque_key": "0009",
                "pr_helices": "0",
                "pr_strands": "",
                "pr_RoG": "0",
                "rfd_RoG": "1.25",
                "seq_mpnn_score": "-1.75",
                "seq_fampnn_psce": "0.25",
                "pr_plddt": "0",
                "plddt": "",
                "pr_plddt_binder": "0",
                "pr_plddt_target": "0",
                "pr_pae_interaction": "0",
                "pr_pae": "0",
                "pae": "",
                "pr_rmsd": "0",
                "pr_rmsd_binder": "0",
                "conf_score": "0",
                "ptm": "0",
            }
        )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
    async with db() as verification:
        design = await verification.get(Design, candidate_id)
        result = await verification.get(FrustraMPNNResult, (job_id, invocation_id))
        assert design is not None and result is not None
        assert design.name == "  exact design string  "
        assert (design.num_helices, design.num_strands) == (0, None)
        assert design.rog == pytest.approx(0.0)
        assert design.rfd_rog == pytest.approx(1.25)
        assert design.mpnn_score == pytest.approx(-1.75)
        assert design.fampnn_psce == pytest.approx(0.25)
        assert all(
            value == pytest.approx(0.0)
            for value in (
                design.plddt_overall,
                design.plddt_binder,
                design.plddt_target,
                design.pae_interaction,
                design.pae_overall,
                design.rmsd_overall,
                design.rmsd_binder,
                design.conf_score,
                design.ptm,
            )
        )
        snapshot = result.parent_metadata_json
        assert snapshot == design.provenance["all_designs_metadata"]
        assert snapshot["description"] == "  exact design string  "
        assert snapshot["fold_id"] == "007"
        assert snapshot["seq_id"] == "3e0"
        assert snapshot["opaque_key"] == "0009"
        assert snapshot["pr_helices"] == 0
        assert snapshot["pr_strands"] is None
        assert snapshot["plddt"] is None
        assert snapshot["pae"] is None
        assert isinstance(snapshot["pr_RoG"], float)
        assert isinstance(snapshot["seq_mpnn_score"], float)
        json.dumps(snapshot, allow_nan=False)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingester.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _seed_job(
    sessions: async_sessionmaker,
    *,
    job_id: str,
    root: Path,
    stage_outputs: dict | None,
    design_id: str | None = "design-1",
) -> None:
    async with sessions() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="running",
                queue_status="running",
                model_id="boltz2",
                mode="predict",
                params={},
                output_dir=str(root.parent),
                stage_outputs=stage_outputs or {},
                completed_stages=["frustrampnn"] if stage_outputs else [],
                awaiting_input=False,
            )
        )
        if design_id is not None:
            pdb_path = root.parent / f"{design_id}.pdb"
            pdb_path.write_bytes(MANIFEST_FIXTURE._pdb())
            session.add(
                Design(
                    id=design_id,
                    job_id=job_id,
                    name=design_id,
                    pdb_path=str(pdb_path),
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_explicit_terminal_outputs_use_only_canonical_manifest_ingestion(
    tmp_path: Path, db
) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1")
    await _seed_job(
        db,
        job_id="job-1",
        root=root,
        stage_outputs=_terminal_outputs(root),
    )

    async with db() as session:
        created = await ingest_job_results(
            "job-1", str(tmp_path), session, commit=False
        )
        assert created == 1
        assert await session.get(FrustraMPNNResult, ("job-1", "invoke-1")) is not None
        retired_name = "ingest_frustration_" + "data"
        assert not hasattr(result_ingester, retired_name)


@pytest.mark.asyncio
async def test_discovered_invalid_manifest_fails_without_retired_csv_fallback(
    tmp_path: Path, db
) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1")
    (root / MANIFEST_PATH).write_text("{}\n", encoding="utf-8")
    await _seed_job(
        db,
        job_id="job-1",
        root=root,
        stage_outputs=_terminal_outputs(root),
    )

    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError):
            await ingest_job_results("job-1", str(tmp_path), session)
        retired_name = "ingest_frustration_" + "data"
        assert not hasattr(result_ingester, retired_name)
        count = (
            await session.execute(select(func.count()).select_from(FrustraMPNNResult))
        ).scalar_one()
        assert count == 0


@pytest.mark.asyncio
async def test_terminal_output_outside_exact_job_root_is_rejected(tmp_path: Path, db) -> None:
    job_root = tmp_path / "job-root"; job_root.mkdir()
    outside = tmp_path / "outside" / "candidate_bundle"
    _bundle(outside, job_id="job-1")
    await _seed_job(
        db, job_id="job-1", root=job_root / "unused",
        stage_outputs=_terminal_outputs(outside),
    )
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="exact job root|escapes"):
            await ingest_job_results("job-1", str(job_root), session)


@pytest.mark.asyncio
async def test_duplicate_explicit_bundle_roots_are_rejected(tmp_path: Path, db) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1")
    outputs = _terminal_outputs(root)
    outputs["frustrampnn"].append(str(root / MANIFEST_PATH))
    await _seed_job(
        db, job_id="job-1", root=root, stage_outputs=outputs,
    )
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="duplicate"):
            await ingest_job_results("job-1", str(tmp_path), session)


@pytest.mark.asyncio
async def test_distinct_roots_with_same_invocation_are_rejected_before_writes(
    tmp_path: Path, db,
) -> None:
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"
    _bundle(first, job_id="job-1", design_id="design-1")
    _bundle(second, job_id="job-1", design_id="design-1")
    outputs = {"frustrampnn": [
        *_terminal_outputs(first)["frustrampnn"],
        *_terminal_outputs(second)["frustrampnn"],
    ]}
    await _seed_job(
        db, job_id="job-1", root=first, stage_outputs=outputs,
        design_id="design-1",
    )
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="duplicate invocation"):
            await ingest_job_results("job-1", str(tmp_path), session, commit=False)
        assert (
            await session.execute(select(func.count()).select_from(FrustraMPNNResult))
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_no_canonical_terminal_output_never_invokes_retired_csv_owner(
    tmp_path: Path, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_job(
        db,
        job_id="job-1",
        root=tmp_path / "unused",
        stage_outputs=None,
    )

    async def loose_files(*_args, **_kwargs):
        return 1

    monkeypatch.setattr("services.result_ingester.ingest_loose_files", loose_files)
    async with db() as session:
        assert await ingest_job_results("job-1", str(tmp_path), session) == 1
        retired_name = "ingest_frustration_" + "data"
        assert not hasattr(result_ingester, retired_name)


@pytest.mark.asyncio
async def test_manifest_ingestion_commit_false_never_commits_independently(
    tmp_path: Path, db
) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1")
    await _seed_job(
        db,
        job_id="job-1",
        root=root,
        stage_outputs=_terminal_outputs(root),
    )

    async with db() as session:
        assert await ingest_job_results("job-1", str(tmp_path), session, commit=False) == 1
        assert await session.get(FrustraMPNNResult, ("job-1", "invoke-1")) is not None
        await session.rollback()

    async with db() as verification:
        assert await verification.get(
            FrustraMPNNResult, ("job-1", "invoke-1")
        ) is None


@pytest.mark.asyncio
async def test_finalizer_fails_closed_on_physical_source_hash_mismatch(
    tmp_path: Path, db
) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1", design_id="design-1")
    await _seed_job(
        db,
        job_id="job-1",
        root=root,
        stage_outputs=_terminal_outputs(root),
        design_id="design-1",
    )
    (root.parent / "design-1.pdb").write_bytes(b"END\n")

    async with db() as session:
        job = await session.get(Job, "job-1")
        assert job is not None
        final = await finalize_successful_job(job, str(tmp_path), session)
        assert final.completed is False
        assert final.integrity_state == "ingestion_failed"
        assert job.status == "failed"
        assert job.queue_status == "failed"
        assert "physical source SHA-256" in (job.error_message or "")
        assert await session.get(FrustraMPNNResult, ("job-1", "invoke-1")) is None


@pytest.mark.asyncio
async def test_finalizer_owns_manifest_transaction_and_marks_replay_idempotent(
    tmp_path: Path, db
) -> None:
    root = tmp_path / "candidate_bundle"
    _bundle(root, job_id="job-1", design_id="design-1")
    await _seed_job(
        db,
        job_id="job-1",
        root=root,
        stage_outputs=_terminal_outputs(root),
        design_id="design-1",
    )

    async with db() as session:
        job = await session.get(Job, "job-1")
        assert job is not None
        first = await finalize_successful_job(job, str(tmp_path), session)
        assert first.completed is True
        assert first.design_count == 1
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is False

    async with db() as session:
        job = await session.get(Job, "job-1")
        assert job is not None
        job.status = "running"
        job.queue_status = "running"
        job.completed_at = None
        await session.commit()
        replay = await finalize_successful_job(job, str(tmp_path), session)
        assert replay.completed is True
        assert replay.design_count == 1
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is True


@pytest.mark.asyncio
async def test_parent_manifest_creates_deterministic_design_before_canonical_ingestion(
    tmp_path: Path, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "job-parent-identity"
    job_root = tmp_path / "job-root"
    candidate_key = "frustrampnn/sources/boltz/rank_0.pdb"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        producer_stage="structure_prediction:boltz",
        producer_candidate_key=candidate_key,
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    from services import result_ingester

    real_ingest = result_ingester.ingest_frustrampnn_result_bundle
    observed_design_ids: list[str] = []

    async def assert_design_first(*args, **kwargs):
        session = kwargs.get("session", args[0])
        design = await session.get(Design, candidate_id)
        assert design is not None, "Design must be flushed before canonical bundle ingestion"
        assert design.pdb_path == str(source)
        observed_design_ids.append(design.id)
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(result_ingester, "ingest_frustrampnn_result_bundle", assert_design_first)
    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=False) == 1
        design = await session.get(Design, candidate_id)
        assert design is not None
        assert design.id == candidate_id
        assert design.job_id == job_id
        assert design.pdb_path == str(source)
        assert await session.get(FrustraMPNNResult, (job_id, invocation_id)) is not None
        assert observed_design_ids == [candidate_id]
        await session.rollback()

    async with db() as verification:
        assert await verification.get(Design, candidate_id) is None
        assert await verification.get(FrustraMPNNResult, (job_id, invocation_id)) is None


@pytest.mark.asyncio
async def test_parent_candidate_set_rolls_back_designs_and_results_on_late_bundle_failure(
    tmp_path: Path, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "job-parent-rollback"
    job_root = tmp_path / "job-root"
    bundle_a = job_root / "frustrampnn" / "results" / "a"
    bundle_b = job_root / "frustrampnn" / "results" / "b"
    candidate_a, invocation_a, _ = _parent_bundle(
        bundle_a,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/rank_0.pdb",
    )
    candidate_b, invocation_b, _ = _parent_bundle(
        bundle_b,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/rank_1.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    (results / "all_designs.csv").write_text(
        "candidate_id,description\n"
        f"{candidate_a},candidate-a\n"
        f"{candidate_b},candidate-b\n",
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle_a / MANIFEST_PATH, bundle_b / MANIFEST_PATH],
    )

    from services import result_ingester

    real_ingest = result_ingester.ingest_frustrampnn_result_bundle
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FrustraMPNNPersistenceError("intentional late bundle failure")
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(result_ingester, "ingest_frustrampnn_result_bundle", fail_second)
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="intentional late bundle failure"):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as verification:
        for candidate_id in (candidate_a, candidate_b):
            assert await verification.get(Design, candidate_id) is None
        for invocation_id in (invocation_a, invocation_b):
            assert await verification.get(FrustraMPNNResult, (job_id, invocation_id)) is None


@pytest.mark.asyncio
async def test_parent_candidate_id_mismatch_is_rejected_before_any_design_write(
    tmp_path: Path, db
) -> None:
    job_id = "job-parent-mismatch"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "bad"
    candidate_id, _invocation_id, _source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        producer_stage="structure_prediction:boltz",
        producer_candidate_key="frustrampnn/sources/boltz/rank_0.pdb",
    )
    request_path = bundle / "workflow_component_request_v1.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["source_artifact"]["producer_stage"] = "structure_prediction:rf3"
    request_path.write_bytes(canonical_json_bytes(request))
    MANIFEST_FIXTURE._rehash_bundle(bundle)
    (bundle / MANIFEST_PATH).unlink()
    (bundle / MANIFEST_PATH).write_bytes(canonical_json_bytes(build_result_manifest(bundle)))
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="deterministic candidate identity"):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as verification:
        assert await verification.get(Design, candidate_id) is None
        assert (
            await verification.execute(select(func.count()).select_from(FrustraMPNNResult))
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_protein_design_canonical_ingestion_precreates_identity_and_enriches_metadata(
    tmp_path: Path, db
) -> None:
    job_id = "job-protein-design"
    job_root = tmp_path / "job-root"
    candidate_key = "frustrampnn/sources/af2/fold-a/sample-0/canonical.pdb"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key=candidate_key,
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    (results / "all_designs.csv").write_text(
        "candidate_id,description,pr_plddt,seq_mpnn_score\n"
        f"{candidate_id},duplicate-basename,91.25,-1.75\n",
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        created = await ingest_job_results(job_id, str(job_root), session, commit=False)
        assert created == 1
        design = await session.get(Design, candidate_id)
        assert design is not None
        assert design.id == candidate_id
        assert design.job_id == job_id
        assert design.pdb_path == str(source)
        assert design.name == "duplicate-basename"
        assert design.plddt_overall == pytest.approx(91.25)
        assert design.mpnn_score == pytest.approx(-1.75)
        assert design.source_stage == "frustrampnn_candidate"
        assert design.source_stage_family == "protein_design"
        assert design.source_stage_mode == "protein_design:af2_terminal"
        assert await session.get(FrustraMPNNResult, (job_id, invocation_id)) is not None
        count = (
            await session.execute(
                select(func.count()).select_from(Design).where(Design.job_id == job_id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_rows", "message"),
    [
        (None, "all_designs.csv"),
        ([], "candidate set is incomplete"),
        (["{candidate_id},first", "{candidate_id},second"], "duplicate candidate_id"),
        (
            ["{candidate_id},candidate", "unmatched-candidate,other"],
            "unmatched candidate_id",
        ),
    ],
)
async def test_protein_design_metadata_set_is_prevalidated_before_any_write(
    tmp_path: Path,
    db,
    metadata_rows: list[str] | None,
    message: str,
) -> None:
    job_id = "job-protein-metadata-preflight"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, _source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/fold-a/sample-0/canonical.pdb",
    )
    if metadata_rows is not None:
        results = job_root / "results"
        results.mkdir(parents=True)
        rendered = [row.format(candidate_id=candidate_id) for row in metadata_rows]
        (results / "all_designs.csv").write_text(
            "candidate_id,description\n" + "\n".join(rendered) + ("\n" if rendered else ""),
            encoding="utf-8",
        )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match=message):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as verification:
        assert await verification.get(Design, candidate_id) is None
        assert await verification.get(FrustraMPNNResult, (job_id, invocation_id)) is None


@pytest.mark.asyncio
async def test_protein_design_replay_is_idempotent_and_keeps_exact_identity(
    tmp_path: Path, db
) -> None:
    job_id = "job-protein-replay"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:boltz_terminal",
        producer_candidate_key="frustrampnn/sources/boltz/fold-a/sample-0/canonical.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    (results / "all_designs.csv").write_text(
        "candidate_id,description,pr_plddt\n"
        f"{candidate_id},stable-design,88.5\n",
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 0
        designs = (
            await session.execute(select(Design).where(Design.job_id == job_id))
        ).scalars().all()
        assert [(design.id, design.name, design.pdb_path) for design in designs] == [
            (candidate_id, "stable-design", str(source))
        ]
        assert designs[0].plddt_overall == pytest.approx(88.5)
        assert await session.get(FrustraMPNNResult, (job_id, invocation_id)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "initial", "replayed"),
    [
        ("pr_RoG", "0", "-0"),
        ("pr_RoG", "-0", "0"),
        ("seq_mpnn_score", "0", "-0"),
        ("seq_mpnn_score", "-0", "0"),
    ],
    ids=[
        "nonnegative-zero-to-negative-zero",
        "nonnegative-negative-zero-to-zero",
        "negative-allowed-zero-to-negative-zero",
        "negative-allowed-negative-zero-to-zero",
    ],
)
async def test_protein_design_replay_distinguishes_signed_zero_in_immutable_snapshot(
    tmp_path: Path,
    db,
    field: str,
    initial: str,
    replayed: str,
) -> None:
    job_id = f"job-protein-signed-zero-{field}-{initial}"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:boltz_terminal",
        producer_candidate_key="frustrampnn/sources/boltz/fold-a/sample-0/canonical.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    csv_path = results / "all_designs.csv"

    def publish(value: str) -> None:
        csv_path.write_text(
            f"candidate_id,description,{field}\n"
            f"{candidate_id},signed-zero-design,{value}\n",
            encoding="utf-8",
        )

    publish(initial)
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 0
        design = await session.get(Design, candidate_id)
        result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
        assert design is not None and result is not None
        assert design.pdb_path == str(source)
        original_result_snapshot = canonical_json_bytes(result.parent_metadata_json)
        original_design_snapshot = canonical_json_bytes(
            design.provenance["all_designs_metadata"]
        )
        expected_sign = -1.0 if initial.startswith("-") else 1.0
        assert math.copysign(1.0, result.parent_metadata_json[field]) == expected_sign

    publish(replayed)
    async with db() as session:
        with pytest.raises(
            FrustraMPNNPersistenceError,
            match="immutable metadata snapshot|metadata replay",
        ):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as session:
        design = await session.get(Design, candidate_id)
        result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
        assert design is not None and result is not None
        assert canonical_json_bytes(result.parent_metadata_json) == original_result_snapshot
        assert canonical_json_bytes(
            design.provenance["all_designs_metadata"]
        ) == original_design_snapshot


@pytest.mark.asyncio
async def test_protein_design_replay_fails_closed_without_legacy_metadata_snapshot(
    tmp_path: Path,
    db,
) -> None:
    job_id = "job-protein-legacy-missing-snapshot"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, _source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/fold-a/sample-0/canonical.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    (results / "all_designs.csv").write_text(
        "candidate_id,description,pr_plddt\n"
        f"{candidate_id},legacy-snapshot,88.5\n",
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )
    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
        result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
        assert result is not None
        result.parent_metadata_json = None
        await session.commit()

    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="immutable metadata snapshot"):
            await ingest_job_results(job_id, str(job_root), session, commit=True)


@pytest.mark.asyncio
async def test_protein_design_replay_rejects_mutated_metadata_without_any_row_change(
    tmp_path: Path, db
) -> None:
    job_id = "job-protein-replay-metadata-conflict"
    job_root = tmp_path / "job-root"
    bundle = job_root / "frustrampnn" / "results" / "candidate"
    candidate_id, invocation_id, source = _parent_bundle(
        bundle,
        job_root=job_root,
        job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage="protein_design:af2_terminal",
        producer_candidate_key="frustrampnn/sources/af2/fold-a/sample-0/canonical.pdb",
    )
    results = job_root / "results"
    results.mkdir(parents=True)
    csv_path = results / "all_designs.csv"
    csv_path.write_text(
        "candidate_id,description,pr_plddt,fold_id,seq_id\n"
        f"{candidate_id},original-name,88.5,7,3\n",
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH],
    )

    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
    async with db() as session:
        original = await session.get(Design, candidate_id)
        result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
        assert original is not None and result is not None
        original_projection = (
            original.name,
            original.plddt_overall,
            dict(original.provenance or {}),
            original.pdb_path,
            result.created_at,
        )

    csv_path.write_text(
        "candidate_id,description,pr_plddt,fold_id,seq_id\n"
        f"{candidate_id},mutated-on-replay,42.0,8,4\n",
        encoding="utf-8",
    )
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="metadata.*replay|replay.*metadata"):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as session:
        persisted = await session.get(Design, candidate_id)
        result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
        assert persisted is not None and result is not None
        assert (
            persisted.name,
            persisted.plddt_overall,
            persisted.provenance,
            persisted.pdb_path,
            result.created_at,
        ) == original_projection
        assert persisted.pdb_path == str(source)


@pytest.mark.asyncio
async def test_protein_design_multi_candidate_replay_metadata_conflict_rolls_back_whole_set(
    tmp_path: Path, db
) -> None:
    job_id = "job-protein-replay-metadata-whole-set"
    job_root = tmp_path / "job-root"
    bundles = [
        job_root / "frustrampnn" / "results" / name
        for name in ("candidate-a", "candidate-b")
    ]
    candidates = [
        _parent_bundle(
            bundle,
            job_root=job_root,
            job_id=job_id,
            parent_workflow_id="protein_design",
            producer_stage="protein_design:boltz_terminal",
            producer_candidate_key=(
                f"frustrampnn/sources/boltz/fold-{index}/sample-0/canonical.pdb"
            ),
        )
        for index, bundle in enumerate(bundles)
    ]
    results = job_root / "results"
    results.mkdir(parents=True)
    csv_path = results / "all_designs.csv"
    csv_path.write_text(
        "candidate_id,description,pr_plddt\n"
        + "".join(
            f"{candidate_id},stable-{index},{90 - index}.0\n"
            for index, (candidate_id, _invocation_id, _source) in enumerate(candidates)
        ),
        encoding="utf-8",
    )
    await _seed_parent_job(
        db,
        job_id=job_id,
        job_root=job_root,
        manifests=[bundle / MANIFEST_PATH for bundle in bundles],
    )
    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 2

    async with db() as session:
        before = {
            design.id: (design.name, design.plddt_overall, dict(design.provenance or {}))
            for design in (
                await session.execute(select(Design).where(Design.job_id == job_id))
            ).scalars().all()
        }

    csv_path.write_text(
        "candidate_id,description,pr_plddt\n"
        f"{candidates[0][0]},stable-0,90.0\n"
        f"{candidates[1][0]},contradiction,1.0\n",
        encoding="utf-8",
    )
    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match="metadata.*replay|replay.*metadata"):
            await ingest_job_results(job_id, str(job_root), session, commit=True)

    async with db() as session:
        after = {
            design.id: (design.name, design.plddt_overall, dict(design.provenance or {}))
            for design in (
                await session.execute(select(Design).where(Design.job_id == job_id))
            ).scalars().all()
        }
        assert after == before
        assert (
            await session.scalar(
                select(func.count()).select_from(FrustraMPNNResult).where(
                    FrustraMPNNResult.parent_job_id == job_id
                )
            )
        ) == 2


@pytest.mark.asyncio
async def test_disabled_frustrampnn_ordinary_protein_design_uses_published_candidate_identity(
    tmp_path: Path, db
) -> None:
    from services.frustrampnn.identity import deterministic_candidate_id

    job_id = "job-protein-disabled"
    producer_stage = "protein_design:af2_terminal"
    producer_candidate_key = "frustrampnn/sources/af2/identity/source.pdb"
    candidate_id = deterministic_candidate_id(
        parent_job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage=producer_stage,
        producer_candidate_key=producer_candidate_key,
    )
    job_root = tmp_path / "job-root"
    results = job_root / "results"
    results.mkdir(parents=True)
    payload = MANIFEST_FIXTURE._pdb()
    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    producer_identity_sha256 = hashlib.sha256(b"ordinary-producer-identity").hexdigest()
    (results / "all_designs.csv").write_text(
        "candidate_id,description,parent_job_id,parent_workflow_id,producer_stage,producer_candidate_key,"
        "producer_method,producer_output_key,producer_identity_sha256,producer_artifact_sha256,source_format,pr_plddt\n"
        f"{candidate_id},candidate,{job_id},protein_design,{producer_stage},{producer_candidate_key},"
        f"af2,producer/candidate.pdb,{producer_identity_sha256},{artifact_sha256},pdb,87.5\n",
        encoding="utf-8",
    )
    structure = job_root / "results" / "best_designs" / "candidate.pdb"
    structure.parent.mkdir(parents=True)
    structure.write_bytes(payload)
    async with db() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="running",
                queue_status="running",
                model_id="rfdiffusion",
                mode="binder_denovo",
                params={"run_frustrampnn": False},
                output_dir=str(job_root),
                stage_outputs={},
                completed_stages=[],
                awaiting_input=False,
            )
        )
        await session.commit()

    async with db() as session:
        assert await ingest_job_results(job_id, str(job_root), session, commit=True) == 1
    async with db() as session:
        designs = (
            await session.execute(select(Design).where(Design.job_id == job_id))
        ).scalars().all()
        assert [(design.id, design.name, design.pdb_path) for design in designs] == [
            (candidate_id, "candidate", str(structure))
        ]


async def _seed_disabled_canonical_metadata_case(
    sessions: async_sessionmaker,
    *,
    tmp_path: Path,
    values: dict[str, str],
) -> tuple[str, str, Path]:
    """Seed the real disabled protein-design publication path and a sentinel."""

    from services.frustrampnn.identity import deterministic_candidate_id

    case_id = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12]
    job_id = f"job-protein-disabled-strict-{case_id}"
    producer_stage = "protein_design:early_sequence_prediction"
    producer_candidate_key = (
        "frustrampnn/sources/boltz/producer-identity/artifact.normalized.pdb"
    )
    candidate_id = deterministic_candidate_id(
        parent_job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage=producer_stage,
        producer_candidate_key=producer_candidate_key,
    )
    job_root = tmp_path / case_id / "job-root"
    published = job_root / "results" / "best_designs" / "candidate.pdb"
    published.parent.mkdir(parents=True)
    payload = MANIFEST_FIXTURE._pdb()
    published.write_bytes(payload)
    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    producer_identity_sha256 = hashlib.sha256(b"producer-identity").hexdigest()
    row = {
        "candidate_id": candidate_id,
        "description": "candidate",
        "parent_job_id": job_id,
        "parent_workflow_id": "protein_design",
        "producer_stage": producer_stage,
        "producer_candidate_key": producer_candidate_key,
        "producer_method": "boltz",
        "producer_sample": "submission-entry-a",
        "producer_rank": "0",
        "producer_output_key": "results/best_designs/candidate.pdb",
        "producer_identity_sha256": producer_identity_sha256,
        "producer_artifact_sha256": artifact_sha256,
        "source_format": "pdb",
        "pr_plddt": "87.5",
        "pr_helices": "3",
        "pr_strands": "",
        "pr_RoG": "",
    }
    row.update(values)
    csv_path = job_root / "results" / "all_designs.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    sentinel_path = job_root / "sentinel.pdb"
    sentinel_path.write_bytes(payload)
    async with sessions() as session:
        session.add_all(
            [
                Job(
                    id=job_id,
                    name=job_id,
                    status="completed",
                    queue_status="completed",
                    model_id="rfdiffusion",
                    mode="binder_denovo",
                    params={"run_frustrampnn": False},
                    output_dir=str(job_root),
                    stage_outputs={},
                    completed_stages=[],
                    awaiting_input=False,
                ),
                Design(
                    id=f"sentinel-{case_id}",
                    job_id=job_id,
                    name="sentinel-exact",
                    pdb_path=str(sentinel_path),
                    source_stage="preexisting-review-sentinel",
                    num_helices=4,
                    provenance={"preserve": "exact"},
                ),
            ]
        )
        await session.commit()
    return job_id, candidate_id, published


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"pr_plddt": "NaN", "pr_helices": "3.5"},
        {"pr_plddt": "Infinity"},
        {"pr_plddt": "1e9999"},
        {"pr_helices": "true"},
        {"pr_helices": "-1"},
        {"pr_helices": "9223372036854775808"},
    ],
    ids=[
        "reviewer-nan-and-fractional-count",
        "infinity",
        "float-overflow",
        "bool-like-count",
        "negative-count",
        "integer-overflow",
    ],
)
async def test_disabled_canonical_metadata_is_strictly_prevalidated_before_any_write(
    tmp_path: Path,
    db,
    values: dict[str, str],
) -> None:
    job_id, candidate_id, _published = await _seed_disabled_canonical_metadata_case(
        db,
        tmp_path=tmp_path,
        values=values,
    )
    inserted: list[str] = []

    def capture_insert(_mapper, _connection, target: Design) -> None:
        inserted.append(target.id)

    event.listen(Design, "before_insert", capture_insert)
    try:
        async with db() as session:
            with pytest.raises(FrustraMPNNPersistenceError, match="canonical protein_design metadata"):
                await ingest_job_results(job_id, str(tmp_path / values_case(values) / "job-root"), session)
    finally:
        event.remove(Design, "before_insert", capture_insert)

    assert inserted == []
    case_id = values_case(values)
    async with db() as verification:
        sentinel = await verification.get(Design, f"sentinel-{case_id}")
        assert sentinel is not None
        assert (sentinel.name, sentinel.num_helices, sentinel.provenance) == (
            "sentinel-exact",
            4,
            {"preserve": "exact"},
        )
        assert await verification.get(Design, candidate_id) is None


def values_case(values: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12]


def _rewrite_authoritative_csv_header_case(csv_path: Path, case: str) -> None:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows and rows[0]
    header = rows[0]
    candidate_index = header.index("candidate_id")
    if case == "duplicate-pr-plddt":
        header.append("pr_plddt")
        for row in rows[1:]:
            row.append("91.5")
    elif case == "duplicate-candidate-id":
        header.append("candidate_id")
        for row in rows[1:]:
            row.append(row[candidate_index])
    elif case == "empty":
        header.append("")
        for row in rows[1:]:
            row.append("")
    elif case == "bom":
        header[candidate_index] = "\ufeffcandidate_id"
    elif case == "confusable":
        header[candidate_index] = "cand\u0456date_id"
    elif case == "whitespace-collision":
        header.append(" candidate_id")
        for row in rows[1:]:
            row.append(row[candidate_index])
    else:  # pragma: no cover - test helper contract
        raise AssertionError(case)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", ["enabled", "disabled"])
@pytest.mark.parametrize(
    "header_case",
    [
        "duplicate-pr-plddt",
        "duplicate-candidate-id",
        "empty",
        "bom",
        "confusable",
        "whitespace-collision",
    ],
)
async def test_canonical_protein_design_csv_rejects_ambiguous_headers_before_any_write(
    tmp_path: Path,
    db,
    execution: str,
    header_case: str,
) -> None:
    numeric_value = "NaN" if header_case == "duplicate-pr-plddt" else "87.5"
    if execution == "enabled":
        job_id, job_root, candidates = await _seed_numeric_metadata_case(
            db,
            tmp_path=tmp_path,
            numeric_field="pr_plddt",
            invalid_value=numeric_value,
        )
        candidate_ids = [candidate_id for candidate_id, _invocation_id, _source in candidates]
        sentinel_id = "preexisting-sentinel"
    else:
        values = {"pr_plddt": numeric_value}
        job_id, candidate_id, published = await _seed_disabled_canonical_metadata_case(
            db,
            tmp_path=tmp_path,
            values=values,
        )
        job_root = published.parents[2]
        candidate_ids = [candidate_id]
        sentinel_id = f"sentinel-{values_case(values)}"

    _rewrite_authoritative_csv_header_case(
        job_root / "results" / "all_designs.csv", header_case
    )
    inserted: list[str] = []

    def capture_insert(_mapper, _connection, target: Design) -> None:
        inserted.append(target.id)

    event.listen(Design, "before_insert", capture_insert)
    try:
        async with db() as session:
            with pytest.raises(FrustraMPNNPersistenceError, match="header"):
                await ingest_job_results(job_id, str(job_root), session, commit=True)
    finally:
        event.remove(Design, "before_insert", capture_insert)

    assert inserted == []
    async with db() as verification:
        sentinel = await verification.get(Design, sentinel_id)
        assert sentinel is not None
        assert (sentinel.name, sentinel.num_helices, sentinel.provenance) == (
            "sentinel-exact",
            4,
            {"preserve": "exact"},
        )
        for candidate_id in candidate_ids:
            assert await verification.get(Design, candidate_id) is None


@pytest.mark.asyncio
async def test_disabled_canonical_metadata_persists_the_same_strict_typed_snapshot(
    tmp_path: Path,
    db,
) -> None:
    values = {
        "pr_plddt": "0",
        "pr_helices": "0",
        "pr_strands": "",
        "pr_RoG": "",
    }
    job_id, candidate_id, published = await _seed_disabled_canonical_metadata_case(
        db,
        tmp_path=tmp_path,
        values=values,
    )

    async with db() as session:
        assert await ingest_job_results(job_id, str(published.parents[2]), session) == 1
    async with db() as verification:
        design = await verification.get(Design, candidate_id)
        assert design is not None
        assert design.pdb_path == str(published)
        assert (design.plddt_overall, design.num_helices, design.num_strands, design.rog) == (
            0.0,
            0,
            None,
            None,
        )
        snapshot = design.provenance["all_designs_metadata"]
        assert snapshot["pr_plddt"] == 0.0
        assert snapshot["pr_helices"] == 0
        assert snapshot["pr_strands"] is None
        assert snapshot["pr_RoG"] is None
        assert snapshot["producer_rank"] == 0
        json.dumps(snapshot, allow_nan=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publication_case", "message"),
    [
        ("missing", "missing"),
        ("ambiguous", "ambiguous"),
        ("symlink", "symlink|unsafe"),
        ("hash-mismatch", "SHA-256|identity"),
    ],
)
async def test_disabled_protein_design_rejects_unsafe_or_ambiguous_published_structure(
    tmp_path: Path,
    db,
    publication_case: str,
    message: str,
) -> None:
    from services.frustrampnn.identity import deterministic_candidate_id

    job_id = f"job-protein-disabled-{publication_case}"
    producer_stage = "protein_design:af2_terminal"
    producer_candidate_key = "frustrampnn/sources/af2/identity/source.pdb"
    candidate_id = deterministic_candidate_id(
        parent_job_id=job_id,
        parent_workflow_id="protein_design",
        producer_stage=producer_stage,
        producer_candidate_key=producer_candidate_key,
    )
    payload = MANIFEST_FIXTURE._pdb()
    artifact_sha256 = __import__("hashlib").sha256(payload).hexdigest()
    producer_identity_sha256 = __import__("hashlib").sha256(
        b"unsafe-publication-producer-identity"
    ).hexdigest()
    if publication_case == "hash-mismatch":
        artifact_sha256 = "0" * 64
    job_root = tmp_path / "job-root"
    results = job_root / "results"
    results.mkdir(parents=True)
    (results / "all_designs.csv").write_text(
        "candidate_id,description,parent_job_id,parent_workflow_id,producer_stage,"
        "producer_candidate_key,producer_method,producer_output_key,producer_identity_sha256,"
        "producer_artifact_sha256,source_format,pr_plddt\n"
        f"{candidate_id},candidate,{job_id},protein_design,{producer_stage},"
        f"{producer_candidate_key},af2,producer/candidate.pdb,{producer_identity_sha256},"
        f"{artifact_sha256},pdb,87.5\n",
        encoding="utf-8",
    )
    published = results / "best_designs" / "candidate.pdb"
    if publication_case in {"ambiguous", "hash-mismatch"}:
        published.parent.mkdir(parents=True)
        published.write_bytes(payload)
    if publication_case == "ambiguous":
        historical = job_root / "best_designs" / "candidate.pdb"
        historical.parent.mkdir(parents=True)
        historical.write_bytes(payload)
    if publication_case == "symlink":
        published.parent.mkdir(parents=True)
        physical = job_root / "physical-candidate.pdb"
        physical.write_bytes(payload)
        published.symlink_to(physical)

    async with db() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="running",
                queue_status="running",
                model_id="rfdiffusion",
                mode="binder_denovo",
                params={"run_frustrampnn": False, "zip_pdbs": False},
                output_dir=str(job_root),
                stage_outputs={},
                completed_stages=[],
                awaiting_input=False,
            )
        )
        await session.commit()

    async with db() as session:
        with pytest.raises(FrustraMPNNPersistenceError, match=message):
            await ingest_job_results(job_id, str(job_root), session, commit=True)
    async with db() as session:
        assert await session.get(Design, candidate_id) is None
