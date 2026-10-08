from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Design, Job
from services.result_ingester import ingest_job_results


async def _build_session_factory(tmp_path: Path) -> tuple[sessionmaker, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _write_minimal_cif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "data_conformer\n"
        "#\n"
        "loop_\n"
        "_atom_site.group_PDB\n"
        "_atom_site.id\n"
        "_atom_site.type_symbol\n"
        "_atom_site.label_atom_id\n"
        "_atom_site.label_comp_id\n"
        "_atom_site.label_asym_id\n"
        "_atom_site.label_seq_id\n"
        "_atom_site.Cartn_x\n"
        "_atom_site.Cartn_y\n"
        "_atom_site.Cartn_z\n"
        "_atom_site.B_iso_or_equiv\n"
        "ATOM 1 C CA GLY A 1 0.0 0.0 0.0 91.0\n"
        "#\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_confornets_ingests_only_final_conformer_artifacts(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "tdt_confornets_mse_finalpub_20260426_230518"
    final_root = output_root / "final" / "confornets"
    final_conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    raw_duplicate = final_root / "raw" / "bms_tdt_confornets" / "tdt_p04053_af_v6" / "alphafold_v6" / "run_0" / "sample_0.cif"
    work_duplicate = output_root / "work" / "aa" / "bb" / "confornets_results" / "conformers" / "cn_00000_sample_0.cif"
    _write_minimal_cif(final_conformer)
    _write_minimal_cif(raw_duplicate)
    _write_minimal_cif(work_duplicate)
    (final_root / "samples.json").write_text(
        '[{"sample_id":"sample_0","conformer_path":"conformers/cn_00000_sample_0.cif","loss":1.25}]',
        encoding="utf-8",
    )
    (final_root / "landscape.json").write_text('{"objective":"mse","sample_count":1}', encoding="utf-8")
    (final_root / "provenance.json").write_text('{"workflow":"confornets_experimental"}', encoding="utf-8")
    (final_root / "ensemble_manifest.json").write_text(
        '{"conformers":[{"sample_id":"sample_0","path":"conformers/cn_00000_sample_0.cif"}]}',
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets",
                name="tdt-confornets-smoke",
                model_id="confornets_experimental",
                mode="design",
                params={"cn_objective": "mse"},
                output_dir=str(output_root),
                status="completed",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-confornets", str(output_root), session)
        assert created == 1

        designs = (await session.execute(select(Design).where(Design.job_id == "job-confornets"))).scalars().all()
        assert len(designs) == 1
        design = designs[0]
        assert design.name == "cn_00000_sample_0"
        assert Path(design.pdb_path) == final_conformer
        assert design.json_path == str(final_root / "samples.json")
        assert design.provenance["artifact_group"] == "confornets"
        assert design.provenance["model_id"] == "confornets_experimental"
        assert design.provenance["landscape_json"] == str(final_root / "landscape.json")
        assert design.confidence_metrics["confornets_sample"]["sample_id"] == "sample_0"
        assert design.confidence_metrics["confornets_landscape"]["objective"] == "mse"

    await engine.dispose()
