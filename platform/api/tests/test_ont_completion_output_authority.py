"""ONT completion → results receiving regressions.

Covers MinKNOW run-folder discovery, receipt admission for artifacts sealed
under the run's own MinKNOW roots, and per-session runtime admission failures.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib import ont_minknow_host  # noqa: E402

RUN_ID = "1a2b3c4d-5e6f-4a1b-9c8d-0123456789ab"
OTHER_RUN_ID = "9f8e7d6c-5e6f-4a1b-9c8d-0123456789ab"


def _minknow_tree(root: Path, short_id: str) -> Path:
    run = root / "grp" / "sample1" / f"20261026_1200_MD-101234_FBA12345_{short_id}"
    (run / "pod5").mkdir(parents=True)
    (run / "fastq_pass" / "barcode01").mkdir(parents=True)
    (run / "pod5" / "reads.pod5").write_bytes(b"pod5")
    (run / "fastq_pass" / "barcode01" / "reads.fastq.gz").write_bytes(b"fastq")
    return run


def test_discovery_matches_default_minknow_run_folder_layout(tmp_path: Path) -> None:
    own = _minknow_tree(tmp_path, RUN_ID[:8])
    _minknow_tree(tmp_path, OTHER_RUN_ID[:8])

    found = ont_minknow_host._discover_run_outputs({"output": str(tmp_path)}, RUN_ID)

    assert found["pod5"] == [str(own / "pod5" / "reads.pod5")]
    assert found["fastq"] == [str(own / "fastq_pass" / "barcode01" / "reads.fastq.gz")]
    assert found["bam"] == []


def test_discovery_still_excludes_foreign_full_id_folders(tmp_path: Path) -> None:
    foreign = tmp_path / OTHER_RUN_ID
    foreign.mkdir()
    (foreign / "reads.pod5").write_bytes(b"pod5")
    exact = tmp_path / RUN_ID
    exact.mkdir()
    (exact / "reads.pod5").write_bytes(b"pod5")

    found = ont_minknow_host._discover_run_outputs({"output": str(tmp_path)}, RUN_ID)

    assert found["pod5"] == [str(exact / "reads.pod5")]


def test_short_id_prefix_requires_exact_folder_grammar() -> None:
    assert ont_minknow_host._run_folder_matches("20261026_1200_MD-101234_FBA12345_1a2b3c4d", RUN_ID)
    assert not ont_minknow_host._run_folder_matches("20261026_1200_MD-101234_FBA12345_1a2b3c4e", RUN_ID)
    assert not ont_minknow_host._run_folder_matches("notes_1a2b3c4d", RUN_ID)
    assert not ont_minknow_host._run_folder_matches("20261026_1200_X_1a2b3c4d", "short")


@pytest.mark.asyncio
async def test_receipt_accepts_artifacts_sealed_under_the_runs_minknow_root(tmp_path: Path, monkeypatch) -> None:
    from database import Base, OntInstrumentRun, OntInstrumentRunEvent
    from services import molbio_ngs_member_receipts as receipts
    from services import ont_run_control

    results = tmp_path / "results"
    inputs = tmp_path / "inputs"
    results.mkdir()
    inputs.mkdir()
    monkeypatch.setattr(receipts, "get_results_dir", lambda: results)
    monkeypatch.setattr(receipts, "get_inputs_dir", lambda: inputs)

    minknow_root = tmp_path / "minknow-data"
    run_dir = _minknow_tree(minknow_root, RUN_ID[:8])
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "reads.fastq.gz").write_bytes(b"fastq")

    observed_at = datetime(2026, 10, 26, 12, 0, 0)

    def _record(run_id: str) -> OntInstrumentRun:
        return OntInstrumentRun(
            id=run_id, position_id="MD-101234", minknow_run_id=f"{run_id}-{RUN_ID}", state="completed",
            observed_at=observed_at, observed_generation=2, output_directories={"output": str(minknow_root)},
            output_files={}, handoff_ready=True, created_at=observed_at,
        )

    sealed = _record("ont-run-sealed")
    manifest, digest = ont_run_control._terminal_artifact_manifest(
        sealed, state="completed", observed_generation=2,
        output_files={"fastq": [str(run_dir / "fastq_pass" / "barcode01" / "reads.fastq.gz")], "pod5": [], "bam": []},
        approved_roots=ont_run_control._approved_output_roots(sealed),
    )
    assert manifest["artifacts"], "fixture must seal a MinKNOW-root artifact"
    sealed.terminal_artifact_manifest, sealed.terminal_artifact_manifest_sha256 = manifest, digest

    # A manifest pointing outside every bound root must still be rejected.
    foreign = _record("ont-run-foreign")
    foreign_manifest, _ = ont_run_control._terminal_artifact_manifest(
        foreign, state="completed", observed_generation=2,
        output_files={"fastq": [str(outside / "reads.fastq.gz")], "pod5": [], "bam": []},
        approved_roots=(outside,),
    )
    foreign.terminal_artifact_manifest = foreign_manifest
    foreign.terminal_artifact_manifest_sha256 = ont_run_control._canonical_digest(foreign_manifest)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            for record in (sealed, foreign):
                session.add(record)
                session.add(OntInstrumentRunEvent(
                    id=f"{record.id}-event-2", run_id=record.id, event_type="completed_observed",
                    state="completed", observed_at=observed_at, observed_generation=2,
                    minknow_payload={}, output_files={},
                ))
            await session.commit()

            receipt = await receipts.resolve_ont_instrument_run_receipt(
                session, run_id="ont-run-sealed", observed_generation=2,
            )
            assert receipt.entity_id == "ont-run-sealed"

            with pytest.raises(ValueError, match="outside server-owned roots"):
                await receipts.resolve_ont_instrument_run_receipt(
                    session, run_id="ont-run-foreign", observed_generation=2,
                )
    finally:
        await engine.dispose()


def test_runtime_admission_failure_marks_session_unavailable(monkeypatch, tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as sessions

    def _unavailable():
        raise sessions.AlignmentSessionError("shared pinned NGS runtime snapshot is unsafe: /host/private/path")

    monkeypatch.setattr(sessions, "_samtools_command", _unavailable)
    files = [tmp_path / name for name in ("a.bam", "a.bam.bai", "ref.fasta")]
    for path in files:
        path.write_bytes(b"x")

    valid, reason = sessions._validate_alignment_bundle(*files, "0" * 64)

    assert valid is False
    assert reason == "pinned NGS samtools runtime is unavailable or does not match the Dorado lock"
    assert "/host/private/path" not in reason
