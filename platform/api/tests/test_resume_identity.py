from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = API_ROOT.parents[1] / "scripts"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from routers.jobs import (
    _awaiting_stage_to_resume_hint,
    _dedupe_child_attempts,
    _ensure_job_resume_identity,
    _normalize_output_dir_reference,
    _plan_output_dir_cleanup,
    _reconcile_child_jobs_from_history,
    _resume_defaults_from_awaiting_payload,
    _should_spawn_antibody_refinement_on_resume,
)
from services.stage_review import (
    _compute_antibody_ca_rog,
    _rfantibody_cdr_refresh_required,
    _rfantibody_rog_refresh_required,
    nextflow_history_status_for_run_dir,
)
from child_job_utils import (
    apply_child_resume_params,
    child_status_kind,
    find_existing_child,
    preferred_child_gpu,
)


@dataclass
class DummyChild:
    id: str
    name: str
    child_stage: str
    parent_job_id: str | None
    batch_name: str | None
    created_at: datetime
    status: str = "failed"
    queue_status: str = "failed"
    stage_progress: str | None = None
    params: dict[str, Any] | None = None
    output_dir: str | None = None
    child_output_dir: str | None = None
    awaiting_input: bool = False
    awaiting_stage: str | None = None
    awaiting_payload: dict[str, Any] | None = None
    completed_at: datetime | None = None
    current_stage: str | None = None


def test_ensure_job_resume_identity_preserves_root_batch_name() -> None:
    for mode in ("antibody_denovo_pipeline", "antibody_refinement_pipeline"):
        params = {
            "parallel_mode": "full_orchestrator",
            "resume_root_job_id": "root-job-123",
        }

        normalized = _ensure_job_resume_identity(
            job_name="RBX1 beta large",
            job_id="new-job-456",
            model_id="template_antibody_denovo",
            mode=mode,
            params=params,
        )

        assert normalized["job_name"] == "RBX1 beta large"
        assert normalized["resume_root_job_id"] == "root-job-123"
        assert normalized["batch_name"] == "RBX1 beta large_root-job-123"


def test_should_spawn_antibody_refinement_on_resume_for_boltzgen_review_gate() -> None:
    paused_boltzgen_job = DummyChild(
        id="paused-boltzgen",
        name="CD3E boltzgen batch",
        child_stage="boltzgen",
        parent_job_id=None,
        batch_name=None,
        created_at=datetime(2026, 4, 13, 10, 0, 0),
        status="awaiting_input",
        awaiting_input=True,
        awaiting_stage="post_boltzgen",
        params={
            "framework_type": "nanobody",
            "boltzgen_mode": "nanobody_binder",
            "antibody_chains": "H",
        },
    )
    paused_boltzgen_job.model_id = "boltzgen"  # type: ignore[attr-defined]
    paused_boltzgen_job.mode = "nanobody_binder"  # type: ignore[attr-defined]

    assert _should_spawn_antibody_refinement_on_resume(paused_boltzgen_job) is True

    paused_boltzgen_job.awaiting_stage = "post_fampnn"
    assert _should_spawn_antibody_refinement_on_resume(paused_boltzgen_job) is False


def test_should_spawn_antibody_refinement_on_resume_for_ppiflow_generator_review_gate() -> None:
    paused_ppiflow_job = DummyChild(
        id="paused-ppiflow",
        name="EGFR ppiflow seeded batch",
        child_stage="ppiflow",
        parent_job_id=None,
        batch_name=None,
        created_at=datetime(2026, 4, 14, 9, 0, 0),
        status="awaiting_input",
        awaiting_input=True,
        awaiting_stage="post_ppiflow_generator",
        params={
            "framework_type": "nanobody",
            "antibody_chains": "H",
            "stage_family": "ppiflow",
            "stage_mode": "generator_backbone_refine",
        },
    )
    paused_ppiflow_job.model_id = "ppiflow"  # type: ignore[attr-defined]
    paused_ppiflow_job.mode = "generator_backbone_refine"  # type: ignore[attr-defined]

    assert _should_spawn_antibody_refinement_on_resume(paused_ppiflow_job) is True


def test_awaiting_stage_to_resume_hint_supports_generator_and_validation_gates() -> None:
    assert _awaiting_stage_to_resume_hint("post_rfantibody") == "rfantibody"
    assert _awaiting_stage_to_resume_hint("post_boltzgen") == "boltzgen"
    assert _awaiting_stage_to_resume_hint("post_ppiflow_generator") == "ppiflow"
    assert _awaiting_stage_to_resume_hint("post_fampnn") == "fampnn"
    assert _awaiting_stage_to_resume_hint("post_caliby") == "caliby"
    assert _awaiting_stage_to_resume_hint("post_structure_validation") == "structure_validation"
    assert _awaiting_stage_to_resume_hint("pre_protenix_msa") == "structure_validation"
    assert _awaiting_stage_to_resume_hint("unknown_stage") is None


def test_dedupe_child_attempts_keeps_latest_attempt_per_slot() -> None:
    base = datetime(2026, 3, 15, 8, 0, 0)
    children = [
        DummyChild(
            id="old-attempt",
            name="antibody_batch - RFA 1/2",
            child_stage="rfantibody",
            parent_job_id="parent-a",
            batch_name="rbx1_root",
            created_at=base,
            params={"job_index": 0},
        ),
        DummyChild(
            id="new-attempt",
            name="RBX1 beta large - RFA 1/2",
            child_stage="rfantibody",
            parent_job_id="parent-b",
            batch_name="rbx1_root",
            created_at=base + timedelta(minutes=5),
            params={"job_index": 0},
        ),
        DummyChild(
            id="other-slot",
            name="RBX1 · RFA 2/2",
            child_stage="rfantibody",
            parent_job_id="parent-a",
            batch_name="rbx1_root",
            created_at=base + timedelta(minutes=1),
            params={"job_index": 1},
        ),
    ]

    deduped = _dedupe_child_attempts(children)
    deduped_ids = {child.id for child in deduped}

    assert deduped_ids == {"new-attempt", "other-slot"}


def test_child_resume_helpers_classify_and_inject_resume_metadata() -> None:
    existing_child = {
        "job_id": "child-123",
        "status": "failed",
        "output_dir": "/tmp/existing-child",
        "stage_work_dir": "/tmp/existing-work",
    }

    updated = apply_child_resume_params({"foo": "bar"}, existing_child)

    assert child_status_kind(existing_child) == "failed"
    assert updated["foo"] == "bar"
    assert updated["resume_job_id"] == "child-123"
    assert updated["resume_source_dir"] == "/tmp/existing-child"
    assert updated["resume_stage_work_dir"] == "/tmp/existing-work"
    assert updated["resume_work_dir"] == "work"


def test_find_existing_child_prefers_slot_identity_over_display_name() -> None:
    payload = {
        "children": [
            {
                "job_id": "old-slot",
                "name": "antibody_batch - RFA 1/10",
                "status": "failed",
                "job_index": 0,
                "assigned_gpu": 2,
            },
            {
                "job_id": "other-slot",
                "name": "antibody_batch - RFA 2/10",
                "status": "failed",
                "job_index": 1,
                "assigned_gpu": 3,
            },
        ]
    }

    existing = find_existing_child(
        payload,
        child_name="RBX1_beta_large - RFA 1/10",
        job_index=0,
    )

    assert existing is not None
    assert existing["job_id"] == "old-slot"
    assert preferred_child_gpu(existing) == 2


def test_dedupe_child_attempts_prefers_deeper_failed_attempt_over_newer_shallow_retry() -> None:
    base = datetime(2026, 3, 15, 8, 0, 0)
    children = [
        DummyChild(
            id="deep-original",
            name="antibody_batch - RFA 2/10",
            child_stage="rfantibody",
            parent_job_id="parent-a",
            batch_name="rbx1_root",
            created_at=base,
            status="failed",
            stage_progress="design 281/500, diffusion t=28",
            params={"job_index": 1},
        ),
        DummyChild(
            id="shallow-retry",
            name="RBX1_beta_large - RFA 2/10",
            child_stage="rfantibody",
            parent_job_id="parent-b",
            batch_name="rbx1_root",
            created_at=base + timedelta(minutes=30),
            status="cancelled",
            stage_progress="design 2/500, diffusion t=2",
            params={"job_index": 1},
        ),
    ]

    deduped = _dedupe_child_attempts(children)

    assert len(deduped) == 1
    assert deduped[0].id == "deep-original"


def test_dedupe_child_attempts_collapses_legacy_and_named_batch_aliases() -> None:
    base = datetime(2026, 3, 15, 8, 0, 0)
    root_id = "620a066c-cc1c-45ee-bb3a-29137c18ccd7"
    children = [
        DummyChild(
            id="legacy-original",
            name="antibody_batch - RFA 2/10",
            child_stage="rfantibody",
            parent_job_id=root_id,
            batch_name=f"antibody_batch_{root_id}",
            created_at=base,
            status="failed",
            stage_progress="design 281/500, diffusion t=28",
            params={"job_index": 1},
        ),
        DummyChild(
            id="named-retry",
            name="RBX1 beta large - RFA 2/10",
            child_stage="rfantibody",
            parent_job_id="932c2f47-f8dc-45df-a87e-6a7e8ce17c9f",
            batch_name=f"RBX1 beta large_{root_id}",
            created_at=base + timedelta(minutes=30),
            status="failed",
            stage_progress="design 1/500, diffusion t=1",
            params={"job_index": 1},
        ),
    ]

    deduped = _dedupe_child_attempts(children)

    assert len(deduped) == 1
    assert deduped[0].id == "legacy-original"


def test_nextflow_history_status_for_run_dir_matches_current_attempt() -> None:
    old_job_id = "11111111-1111-1111-1111-111111111111"
    current_job_id = "22222222-2222-2222-2222-222222222222"

    with TemporaryDirectory() as tmpdir:
        history_dir = Path(tmpdir) / ".nextflow"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / "history"
        history_path.write_text(
            "\n".join(
                [
                    (
                        "2026-03-15 11:56:34\t2m 52s\told_retry\tERR\tdeadbeef\tdeadbeef\t"
                        f"nextflow run main.nf --job_id {old_job_id}"
                    ),
                    (
                        "2026-03-15 12:08:31\t-\tcurrent_retry\t-\tdeadbeef\tdeadbeef\t"
                        f"nextflow run main.nf --job_id {current_job_id}"
                    ),
                ]
            )
        )

        assert nextflow_history_status_for_run_dir(tmpdir, current_job_id) == "-"
        assert nextflow_history_status_for_run_dir(tmpdir, old_job_id) == "ERR"


def test_plan_output_dir_cleanup_preserves_shared_resume_directory() -> None:
    shared = "/mnt/BioModStack/bms_results/RBX1 beta large_20260314_235055"
    unique = "/mnt/BioModStack/bms_results/RBX1 beta large_resumed_refinement_20260315_234903"

    deletable, preserved = _plan_output_dir_cleanup(
        [shared, unique, shared],
        {
            _normalize_output_dir_reference(shared): [
                {
                    "job_id": "64e9555b-fe58-41ba-afe2-21e6579c56e6",
                    "job_name": "RBX1 beta large_resumed",
                    "field": "output_dir",
                }
            ]
        },
    )

    assert deletable == [_normalize_output_dir_reference(unique)]
    assert preserved == [
        {
            "path": _normalize_output_dir_reference(shared),
            "referenced_by": [
                {
                    "job_id": "64e9555b-fe58-41ba-afe2-21e6579c56e6",
                    "job_name": "RBX1 beta large_resumed",
                    "field": "output_dir",
                }
            ],
        }
    ]


def test_plan_output_dir_cleanup_preserves_shared_child_directory_once() -> None:
    shared_child = "/mnt/BioModStack/bms_results/antibody_batch - RFA 1/10_20260314_235105"

    deletable, preserved = _plan_output_dir_cleanup(
        [shared_child, shared_child],
        {
            _normalize_output_dir_reference(shared_child): [
                {
                    "job_id": "599fe24b-ca54-4562-8d3a-2a1b3988ffb8",
                    "job_name": "RBX1_beta_large - RFA 1/10",
                    "field": "child_output_dir",
                }
            ]
        },
    )

    assert deletable == []
    assert preserved == [
        {
            "path": _normalize_output_dir_reference(shared_child),
            "referenced_by": [
                {
                    "job_id": "599fe24b-ca54-4562-8d3a-2a1b3988ffb8",
                    "job_name": "RBX1_beta_large - RFA 1/10",
                    "field": "child_output_dir",
                }
            ],
        }
    ]


def test_reconcile_child_jobs_ignores_stale_err_for_previous_attempt() -> None:
    old_job_id = "11111111-1111-1111-1111-111111111111"
    current_job_id = "22222222-2222-2222-2222-222222222222"

    with TemporaryDirectory() as tmpdir:
        history_dir = Path(tmpdir) / ".nextflow"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / "history"
        history_path.write_text(
            "\n".join(
                [
                    (
                        "2026-03-15 11:56:34\t2m 52s\told_retry\tERR\tdeadbeef\tdeadbeef\t"
                        f"nextflow run main.nf --job_id {old_job_id}"
                    ),
                    (
                        "2026-03-15 12:08:31\t-\tcurrent_retry\t-\tdeadbeef\tdeadbeef\t"
                        f"nextflow run main.nf --job_id {current_job_id}"
                    ),
                ]
            )
        )

        child = DummyChild(
            id=current_job_id,
            name="RBX1_beta_large - RFA 1/10",
            child_stage="rfantibody",
            parent_job_id="parent-123",
            batch_name="antibody_batch_root",
            created_at=datetime(2026, 3, 15, 12, 8, 31),
            status="running",
            queue_status="running",
            output_dir=tmpdir,
            child_output_dir=tmpdir,
            current_stage="rfantibody",
            awaiting_payload={},
        )

        updated = _reconcile_child_jobs_from_history([child])

        assert updated == 0
        assert child.status == "running"
        assert child.queue_status == "running"
        assert child.completed_at is None


def test_rfantibody_cdr_refresh_required_when_pdb_labels_exist() -> None:
    with TemporaryDirectory() as tmpdir:
        pdb_path = Path(tmpdir) / "rfantibody_child_0.pdb"
        pdb_path.write_text(
            "\n".join(
                [
                    "ATOM      1  N   VAL H   1      -1.827  23.530  13.673  1.00  1.00",
                    "REMARK PDBinfo-LABEL:   25 H1",
                    "REMARK PDBinfo-LABEL:   26 H1",
                    "REMARK PDBinfo-LABEL:   63 H2",
                    "REMARK PDBinfo-LABEL:  118 H3",
                ]
            )
        )

        assert _rfantibody_cdr_refresh_required(str(pdb_path)) is True
        assert _rfantibody_cdr_refresh_required(None) is False


def test_compute_antibody_ca_rog_uses_antibody_chain_subset() -> None:
    with TemporaryDirectory() as tmpdir:
        pdb_path = Path(tmpdir) / "rfantibody_child_0.pdb"
        pdb_path.write_text(
            "\n".join(
                [
                    "ATOM      1  CA  GLY H   1       0.000   0.000   0.000  1.00  1.00           C",
                    "ATOM      2  CA  GLY H   2       2.000   0.000   0.000  1.00  1.00           C",
                    "ATOM      3  CA  GLY H   3       4.000   0.000   0.000  1.00  1.00           C",
                    "ATOM      4  CA  GLY T   1      50.000   0.000   0.000  1.00  1.00           C",
                    "ATOM      5  CA  GLY T   2      52.000   0.000   0.000  1.00  1.00           C",
                    "TER",
                    "END",
                ]
            )
        )

        rog = _compute_antibody_ca_rog(pdb_path, "H")

        assert rog is not None
        assert math.isclose(rog, math.sqrt(8.0 / 3.0), rel_tol=1e-6)
        assert _rfantibody_rog_refresh_required(str(pdb_path), "H") is True
        assert _rfantibody_rog_refresh_required(None, "H") is False


def test_resume_defaults_from_awaiting_payload_extracts_direct_continue_settings() -> None:
    overrides, from_stage, name_suffix = _resume_defaults_from_awaiting_payload(
        {
            "resume_param_overrides": {
                "protenix_allow_cpu_msa_fallback": True,
            },
            "resume_from_stage": "structure_validation",
            "resume_name_suffix": "continued",
        }
    )

    assert overrides == {"protenix_allow_cpu_msa_fallback": True}
    assert from_stage == "structure_validation"
    assert name_suffix == "continued"
