from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.gpu_orchestrator import (
    _recover_rfantibody_parent_after_child_wait,
    _reconcile_terminal_history_without_process,
)


def test_recover_rfantibody_parent_after_child_wait_opens_post_rf_gate(tmp_path: Path) -> None:
    for mode in ("antibody_denovo_pipeline", "antibody_refinement_pipeline"):
        parent_output = tmp_path / mode / "parent"
        child_output = tmp_path / mode / "child"
        child_rfa_dir = child_output / "run" / "rfantibody" / "output"
        child_rfa_dir.mkdir(parents=True)
        (child_rfa_dir / "001_backbone.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
        (child_rfa_dir / "001_backbone.trb").write_text("{}", encoding="utf-8")

        job = SimpleNamespace(
            mode=mode,
            output_dir=str(parent_output),
            params={
                "interactive_gating": True,
                "interactive_gate_stage": "post_rfantibody",
                "framework_type": "custom",
                "antibody_chains": "H",
                "structure_validator": "boltz2",
            },
            completed_stages=[],
            stage_outputs={},
            awaiting_input=False,
            awaiting_stage=None,
            awaiting_payload={},
            status="running",
            queue_status="running",
            current_stage="waitforchildren",
            stage_progress="1/4",
            error_message="stale launcher",
            completed_at=None,
            assigned_gpu=0,
        )

        recovered = _recover_rfantibody_parent_after_child_wait(
            job,
            {
                "output_dirs": [str(child_output)],
                "completed": 1,
                "total": 1,
            },
        )

        assert recovered is not None
        assert recovered["opened_gate"] is True
        raw_dir = parent_output / "collected" / "rfantibody_raw"
        assert (raw_dir / "job0_001_backbone.pdb").exists()
        assert (raw_dir / "job0_001_backbone.trb").exists()

        manifest = json.loads((parent_output / "collected" / "rfantibody" / "collection_manifest.json").read_text())
        assert manifest["count"] == 1
        assert manifest["recovered_after_child_wait"] is True

        assert job.completed_stages == ["rfantibody"]
        assert job.stage_outputs["rfantibody"] == [str(raw_dir)]
        assert job.awaiting_input is True
        assert job.awaiting_stage == "post_rfantibody"
        assert job.status == "awaiting_input"
        assert job.queue_status == "completed"
        assert job.current_stage == "post_rfantibody"
        assert job.awaiting_payload["candidate_count"] == 1
        assert Path(parent_output / "gates" / "gate_post_rfantibody.json").exists()


def test_reconcile_terminal_history_without_process_marks_err_history_as_failed() -> None:
    job = SimpleNamespace(
        name="CP resume run",
        status="running",
        queue_status="running",
        error_message=(
            "Reconciled as failed: no active process and no terminal .nextflow/history status "
            "(expected OK/ERR)"
        ),
        completed_at=None,
        awaiting_input=False,
        awaiting_stage=None,
        current_stage="boltz2",
        stage_progress="1/1",
        nextflow_run_id="3153244",
        stage_work_dir="/tmp/workdir",
        output_dir="/tmp/output",
    )

    reconciled = _reconcile_terminal_history_without_process(
        job,
        history_status="ERR",
        gate_present=False,
        age_seconds=301,
        stale_fail_after_seconds=300,
    )

    assert reconciled is True
    assert job.status == "failed"
    assert job.queue_status == "failed"
    assert job.error_message == "Reconciled as failed: terminal .nextflow/history status ERR"
    assert job.completed_at is not None
