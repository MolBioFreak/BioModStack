from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import get_registry
from routers import ont_runs
from routers.ont_runs import ONT_WORKFLOW_MODEL_MODES, OntNgsSubmitRequest, _job_create_for_ont_submit
from services.nextflow import WORKFLOW_ENTRYPOINTS, build_nextflow_command
from services.ont_ngs_contract import get_ont_workflow_spec, resolve_ont_workflow_alias
from services.ont_pooled_reference_assignment import ASSIGNMENT_MODE, ASSIGNMENT_WORKFLOW_ID


def test_pooled_assignment_is_bound_across_all_authoritative_registries() -> None:
    spec = get_ont_workflow_spec(ASSIGNMENT_WORKFLOW_ID)

    assert spec.workflow_id == ASSIGNMENT_WORKFLOW_ID
    assert spec.input_modes == ("fastq",)
    assert {
        "assignment_summary",
        "per_read_assignment",
        "occurrence_map",
        "alignment_bam",
        "alignment_bai",
        "igv_track_config",
    } <= set(spec.artifact_kinds)
    assert resolve_ont_workflow_alias(ASSIGNMENT_MODE) == ASSIGNMENT_WORKFLOW_ID
    assert resolve_ont_workflow_alias("pooled-reference-assignment") == ASSIGNMENT_WORKFLOW_ID
    assert ONT_WORKFLOW_MODEL_MODES[ASSIGNMENT_WORKFLOW_ID] == ASSIGNMENT_MODE
    assert WORKFLOW_ENTRYPOINTS[ASSIGNMENT_WORKFLOW_ID] == "workflows/ngs/ont_pooled_reference_assignment.nf"

    nanopore = get_registry().get_model("nanopore")
    assert nanopore is not None
    assert ASSIGNMENT_MODE in {mode.id for mode in nanopore.modes}


def test_dedicated_pooled_submit_route_precedes_the_generic_ont_route() -> None:
    paths = [str(getattr(route, "path", "")) for route in ont_runs.router.routes]
    dedicated = "/ngs/pooled-reference-assignment/submit"
    generic = "/ngs/{workflow_id}/submit"

    assert paths.index(dedicated) < paths.index(generic)


def test_generic_ont_builder_rejects_pooled_reference_assignment() -> None:
    request = OntNgsSubmitRequest(
        name="forged pooled assignment",
        params={
            "fastq_path": "/inputs/pooled.fastq",
            "reference_set_manifest": "/inputs/caller-controlled.json",
        },
    )

    try:
        _job_create_for_ont_submit(ASSIGNMENT_WORKFLOW_ID, request)
    except ValueError as exc:
        assert "dedicated atomic submission endpoint" in str(exc)
    else:
        raise AssertionError("generic ONT submission accepted pooled reference authority")


def test_pooled_assignment_builds_the_canonical_nextflow_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_NEXTFLOW_BIN", "/bin/true")
    command = build_nextflow_command(
        "nanopore",
        ASSIGNMENT_MODE,
        {
            "fastq_path": "/inputs/pooled.fastq",
            "reference_set_manifest": "/inputs/reference_set.json",
            "pooled_assignment_min_mapq": 20,
            "pooled_assignment_min_alignment_score_margin": 10,
            "ont_workflow_id": ASSIGNMENT_WORKFLOW_ID,
        },
        str(tmp_path / "output"),
        "pooled-registry-acceptance",
    )

    assert command[:3] == [
        "/usr/bin/true",
        "run",
        "workflows/ngs/ont_pooled_reference_assignment.nf",
    ]
    profile_index = command.index("-profile")
    assert command[profile_index + 1].split(",", 1)[0] == ASSIGNMENT_WORKFLOW_ID
