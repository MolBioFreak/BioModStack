from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job
from routers.jobs import get_rfd3_generation_result
from services import rfd3_generation as generation_contract
from services.nextflow import build_nextflow_command, resolve_nextflow_entrypoint
from services.rfd3_generation import (
    GenerationContractError,
    materialize_generation_request,
    normalize_generation_params,
)
from services.result_ingester import ingest_job_results


MODEL_ID = "protein_modification_experimental"
MODE = "de_novo_design"


def test_rfd3_is_the_default_general_generation_authority(tmp_path: Path) -> None:
    normalized, request, digest = normalize_generation_params(
        {
            "generator": "rfd3",
            "generation_mode": "unconditional_monomer",
            "min_length": 90,
            "max_length": 130,
            "num_designs": 4,
            "seed": 7,
            "dump_trajectories": False,
        },
        job_name="rfd3-general",
    )

    assert request == {
        "schema": "bms.rfd3.generation.request.v1",
        "request_id": request["request_id"],
        "job_id": "validation-preview",
        "generation": {"min_length": 90, "max_length": 130, "num_designs": 4},
        "execution": {
            "seed": 7,
            "dump_trajectories": False,
        },
    }
    assert digest == hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert normalized["diffusion_method"] == "rfd3"
    assert normalized["rfd_mode"] == "monomer_denovo"
    assert normalized["rfd_contigs"] == "[90-130]"
    assert normalized["run_rfd_only"] is True
    assert normalized["rfd3_batches_per_design"] == 4

    output = tmp_path / "job"
    output.mkdir()
    materialized, request_path = materialize_generation_request(
        normalized,
        output_dir=output,
        job_id="job-rfd3-general",
    )
    written_request = json.loads(request_path.read_text())
    assert written_request["job_id"] == "job-rfd3-general"
    assert written_request["request_id"] == materialized["rfd3_generation_request_id"]
    assert materialized["rfd3_generation_request"] == written_request
    assert materialized["rfd3_generation_request_path"] == str(request_path)
    assert materialized["rfd3_generation_request_sha256"] == hashlib.sha256(
        json.dumps(written_request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    assert resolve_nextflow_entrypoint(
        effective_profile="rfd3_generation",
        model_id=MODEL_ID,
        mode=MODE,
        params=materialized,
    ) == "workflows/protein_design.nf"
    command = build_nextflow_command(
        MODEL_ID,
        MODE,
        materialized,
        str(output),
        job_id="job-rfd3-general",
    )
    joined = " ".join(command)
    assert command[2] == "workflows/protein_design.nf"
    assert "rfd3_generation,workstation_ryzen7960x" in command
    for expected in (
        "--diffusion_method rfd3",
        "--rfd_mode monomer_denovo",
        "--rfd_contigs [90-130]",
        "--rfd_num_designs 4",
        "--rfd3_batches_per_design 4",
        "--run_rfd_only true",
        f"--rfd3_generation_request_path {request_path}",
    ):
        assert expected in joined


def test_general_rfd3_request_rejects_invalid_ranges_and_unknown_fields() -> None:
    with pytest.raises(GenerationContractError, match="min_length"):
        normalize_generation_params(
            {"generator": "rfd3", "min_length": 200, "max_length": 100, "num_designs": 1},
            job_name="bad",
        )
    with pytest.raises(GenerationContractError, match="unsupported"):
        normalize_generation_params(
            {"generator": "rfd3", "min_length": 100, "max_length": 100, "num_designs": 1, "backend": "disco"},
            job_name="bad",
        )


def test_backup_generators_keep_the_deferred_internal_entrypoint() -> None:
    for generator in ("disco", "laproteina"):
        params = {
            "generator": generator,
            "backend": generator,
            "design_task": "unconditional",
            "num_designs": 2,
            "target_lengths": "100,120",
        }
        assert resolve_nextflow_entrypoint(
            effective_profile="protein_cad_experimental",
            model_id=MODEL_ID,
            mode=MODE,
            params=params,
        ) == "workflows/protein_cad_experimental.nf"


def test_result_manifest_is_hash_bound_and_projects_exact_statistics(tmp_path: Path) -> None:
    output = tmp_path / "job"
    artifact_root = output / "run" / "rfd3"
    artifact_root.mkdir(parents=True)
    structure = artifact_root / "candidate_0.cif.gz"
    metadata = artifact_root / "candidate_0.json"
    structure.write_bytes(b"typed-rfd3-structure")
    metadata.write_text('{"native":true}\n', encoding="utf-8")
    request = {
        "schema": "bms.rfd3.generation.request.v1",
        "request_id": "request-1",
        "job_id": "job-1",
        "generation": {"min_length": 90, "max_length": 130, "num_designs": 1},
        "execution": {"seed": 7, "dump_trajectories": False},
    }
    descriptors = [
        {
            "role": "candidate_structure",
            "relative_path": "run/rfd3/candidate_0.cif.gz",
            "sha256": hashlib.sha256(structure.read_bytes()).hexdigest(),
            "bytes": structure.stat().st_size,
            "media_type": "chemical/x-mmcif+gzip",
        },
        {
            "role": "candidate_metadata",
            "relative_path": "run/rfd3/candidate_0.json",
            "sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
            "bytes": metadata.stat().st_size,
            "media_type": "application/json",
        },
    ]
    manifest = {
        "schema": "bms.rfd3.generation.result-manifest.v1",
        "job_id": "job-1",
        "request_id": "request-1",
        "request_sha256": generation_contract.request_sha256(request),
        "aggregate": {
            "requested": 1,
            "generated": 1,
            "accepted": 1,
            "length": {"min": 112, "mean": 112.0, "max": 112},
            "radius_of_gyration": {"min": 15.5, "mean": 15.5, "max": 15.5},
        },
        "candidates": [
            {
                "candidate_id": "candidate_0",
                "accepted": True,
                "metrics": {
                    "residue_count": 112,
                    "chain_count": 1,
                    "radius_of_gyration": 15.5,
                    "helix_count": 4,
                    "strand_count": 2,
                },
                "artifact_manifest_sha256": hashlib.sha256(
                    json.dumps(descriptors, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                ).hexdigest(),
                "artifacts": descriptors,
            }
        ],
    }

    validated = generation_contract.validate_result_manifest(
        manifest,
        request=request,
        output_root=output,
        job_id="job-1",
    )
    assert validated["aggregate"]["length"]["mean"] == 112.0
    assert validated["candidates"][0]["artifacts"][0]["resolved_path"] == str(structure.resolve())

    structure.write_bytes(b"x" * structure.stat().st_size)
    with pytest.raises(GenerationContractError, match="hash"):
        generation_contract.validate_result_manifest(
            manifest,
            request=request,
            output_root=output,
            job_id="job-1",
        )


@pytest.mark.asyncio
async def test_generation_manifest_ingests_and_reopens_as_typed_results(tmp_path: Path) -> None:
    job_id = "job-rfd3-general-ingest"
    output = tmp_path / "job"
    artifact_root = output / "run" / "rfd3"
    manifest_root = output / "collected" / "rfd3_generation"
    artifact_root.mkdir(parents=True)
    manifest_root.mkdir(parents=True)
    structure = artifact_root / "candidate_0.cif.gz"
    metadata = artifact_root / "candidate_0.json"
    structure.write_bytes(b"typed-rfd3-structure")
    metadata.write_text('{"native":true}\n', encoding="utf-8")
    request = {
        "schema": "bms.rfd3.generation.request.v1",
        "request_id": "request-rfd3-general-ingest",
        "job_id": job_id,
        "generation": {"min_length": 90, "max_length": 130, "num_designs": 1},
        "execution": {"seed": 7, "dump_trajectories": False},
    }
    artifacts = [
        {
            "role": "candidate_structure",
            "relative_path": "run/rfd3/candidate_0.cif.gz",
            "sha256": hashlib.sha256(structure.read_bytes()).hexdigest(),
            "bytes": structure.stat().st_size,
            "media_type": "chemical/x-mmcif+gzip",
        },
        {
            "role": "candidate_metadata",
            "relative_path": "run/rfd3/candidate_0.json",
            "sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
            "bytes": metadata.stat().st_size,
            "media_type": "application/json",
        },
    ]
    artifact_digest = hashlib.sha256(
        json.dumps(artifacts, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    manifest = {
        "schema": "bms.rfd3.generation.result-manifest.v1",
        "job_id": job_id,
        "request_id": request["request_id"],
        "request_sha256": generation_contract.request_sha256(request),
        "aggregate": {
            "requested": 1,
            "generated": 1,
            "accepted": 1,
            "length": {"min": 112, "mean": 112.0, "max": 112},
            "radius_of_gyration": {"min": 15.5, "mean": 15.5, "max": 15.5},
        },
        "candidates": [
            {
                "candidate_id": "candidate_0",
                "accepted": True,
                "metrics": {
                    "residue_count": 112,
                    "chain_count": 1,
                    "radius_of_gyration": 15.5,
                    "helix_count": 4,
                    "strand_count": 2,
                },
                "artifact_manifest_sha256": artifact_digest,
                "artifacts": artifacts,
            }
        ],
    }
    (manifest_root / "rfd3_generation_result_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rfd3-generation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                Job(
                    id=job_id,
                    name="RFD3 general ingestion",
                    status="completed",
                    model_id=MODEL_ID,
                    mode=MODE,
                    params={"generator": "rfd3", "rfd3_generation_request": request},
                    output_dir=str(output),
                )
            )
            await session.commit()
            assert await ingest_job_results(job_id, str(output), session) == 1
            design = (await session.execute(select(Design).where(Design.job_id == job_id))).scalar_one()
            assert design.review_profile_id == "de_novo_generation_v1"
            assert design.rfd_rog == 15.5
            read_model = await get_rfd3_generation_result(job_id, session)
            assert read_model["schema"] == "bms.rfd3.generation.read-model.v1"
            assert read_model["counts"] == {"requested": 1, "generated": 1, "accepted": 1}
            assert read_model["candidates"][0]["helix_count"] == 4
    finally:
        await engine.dispose()
