from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / "workflows" / "antibody_denovo.nf"
OPENMM = REPO_ROOT / "modules" / "openmm.nf"
FRUSTRAMPNN_PARENT = REPO_ROOT / "modules" / "antibody_frustrampnn_parent.nf"
ANTIBODY_OPENMM = REPO_ROOT / "modules" / "antibody_openmm_refinement.nf"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _canonical_slice() -> str:
    return FRUSTRAMPNN_PARENT.read_text(encoding="utf-8") + "\n" + _workflow()


def test_antibody_uses_only_public_canonical_frustrampnn() -> None:
    source = _workflow()
    assert "include { CanonicalFrustraMPNN } from '../modules/frustrampnn'" in source
    assert "from '../modules/antibody_frustrampnn_parent'" in source
    assert source.count("workflow ANTIBODY_DENOVO {") == 1
    assert "FrustrampnnQC" not in source
    assert "AggregateFrustrationReports" not in source
    for process_name in (
        "PrepareAntibodyFrustraMPNNCandidate",
        "PublishAntibodyFrustraMPNNCandidate",
        "AggregateAndReportAntibodyFrustraMPNN",
        "ReportAntibodyFrustraMPNNNotRequested",
    ):
        assert f"process {process_name} {{" not in source
        assert FRUSTRAMPNN_PARENT.read_text(encoding="utf-8").count(
            f"process {process_name} {{"
        ) == 1
    assert "CanonicalFrustraMPNN(PrepareAntibodyFrustraMPNNCandidate.out.prepared)" in source
    assert "PublishAntibodyFrustraMPNNCandidate(CanonicalFrustraMPNN.out.result)" in source


def test_antibody_final_adapter_never_mints_identity_from_path_or_order() -> None:
    source = _workflow()
    helper = source.split("def antibodyTerminalCandidate", 1)[1].split(
        "def ensureParamDefault", 1
    )[0]
    assert "rawPath.baseName" not in helper
    assert "rawPath.getName" not in helper
    assert "producer_artifact_key" in helper
    assert "producer_output_key" in helper
    assert "producer_identity_sha256" in helper
    assert "producer_artifact_sha256" in helper
    assert "transformation_lineage" in helper
    assert "arrival" not in helper.lower()
    final_adapter = source.split("typed_terminal_candidates =", 1)[1].split(
        "PrepareAntibodyFrustraMPNNCandidate", 1
    )[0]
    assert ".baseName" not in final_adapter
    assert "structures.size() != 1" in final_adapter
    assert "ambiguous_terminal_candidate_metadata" in final_adapter


def test_antibody_prepare_publish_and_terminal_report_are_scheduled_and_strict() -> None:
    source = _canonical_slice()
    assert "prepare_frustrampnn_candidate.py" in source
    assert "publish_frustrampnn_bundle.py" in source
    assert "frustrampnn/results/${candidateId}" in source
    assert "AggregateAndReportAntibodyFrustraMPNN" in source
    assert "antibody_frustrampnn_terminal_manifest.json" in source
    assert "frustrampnn_requiredness_must_be_required" in source
    assert "frustrampnn_required_candidate_failed" in source
    assert "frustrampnn_ambiguous_candidate" in source
    assert "stage_reporter.py" in source
    assert "frustrampnn complete" in source
    assert "|| true" not in source.split("process AggregateAndReportAntibodyFrustraMPNN", 1)[1].split(
        "process ReportAntibodyFrustraMPNNNotRequested", 1
    )[0]


def test_antibody_disabled_branch_is_scheduled_and_emits_typed_status() -> None:
    source = _workflow()
    disabled = FRUSTRAMPNN_PARENT.read_text(encoding="utf-8").split(
        "process ReportAntibodyFrustraMPNNNotRequested", 1
    )[1]
    assert "status: 'not_requested'" in disabled
    assert "requiredness: 'not_requested'" in disabled
    assert "candidate_count: 0" in disabled
    assert "tuple val(parent_status)" in disabled
    assert "frustrampnn not_requested" in disabled
    assert "frustrampnn_results = ReportAntibodyFrustraMPNNNotRequested.out.result" in source
    assert "frustrampnn_results = frustrampnn_results" in source


def test_antibody_iggm_sequence_change_fails_before_canonical_invocation() -> None:
    source = _workflow()
    guard = source.index("antibody_denovo:frustrampnn_stale_post_iggm_structure")
    iggm = source.index("IGGM_AFFINITY_MATURATION(maturation_input)")
    canonical = source.index("CanonicalFrustraMPNN(PrepareAntibodyFrustraMPNNCandidate.out.prepared)")
    assert guard < iggm < canonical
    assert "run_affinity_maturation == true && params.run_frustrampnn == true" in source


def test_antibody_openmm_preserves_task_coupled_producer_metadata() -> None:
    source = _workflow()
    assert "include { AntibodyOpenMMRefinement } from '../modules/antibody_openmm_refinement'" in source
    assert "AntibodyOpenMMRefinement(validated_structures)" in source
    openmm_slice = ANTIBODY_OPENMM.read_text(encoding="utf-8")
    assert "openmm_records" in openmm_slice
    assert "relaxed_with_batch" in openmm_slice
    assert ".join(openmm_lineage)" in openmm_slice
    assert "producer_artifact_key: openmmArtifact" in openmm_slice
    assert "producer_method: 'openmm'" in openmm_slice
    assert "transformation_lineage: lineage" in openmm_slice
    assert ".baseName.replace('_relaxed'" not in openmm_slice
    module = OPENMM.read_text(encoding="utf-8")
    assert 'tuple val(batch_id), path("relaxed/*.pdb"), emit: relaxed_with_batch' in module


def test_antibody_enabled_results_follow_actual_publisher_outputs() -> None:
    source = _workflow()
    reporter = FRUSTRAMPNN_PARENT.read_text(encoding="utf-8")
    assert "frustrampnn_results = PublishAntibodyFrustraMPNNCandidate.out.published" in source
    assert "PublishAntibodyFrustraMPNNCandidate.out.published\n                .map" in source
    assert "marker['result']" in reporter
    assert "marker['manifest']" in reporter
    assert "marker['source']" in reporter
    assert "Path('${workflow.launchDir}')" in reporter
    assert "relative_to(job_root)" in reporter
    assert "parts = raw_value.split('/')" in reporter
    assert "any(part in {'', '.', '..'} for part in parts)" in reporter
    assert "or '\\\\\\\\' in raw_value" in reporter
    assert "cursor.is_symlink()" in reporter
    assert "result_path = resolve_job_output(marker['result'])" in reporter
