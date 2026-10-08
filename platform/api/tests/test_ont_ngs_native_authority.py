"""B1–B3 regressions, to be run in the deferred integrated test cycle."""
import asyncio
from copy import deepcopy
import hashlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from ngs_resource_fixture import ngs_resources

pytestmark = [pytest.mark.native_http, pytest.mark.usefixtures("ngs_resources", "native_http")]
import rfc8785

from services import ngs_native_alignment_sources as native_sources
from services import ont_ngs_completion as completion
from services.execution_ownership import attach_scheduler_gpu_assignment, release_scheduler_gpu_assignment
from services.ont_ngs_contract import DORADO_LOCK_PATH, ONT_WORKFLOW_ALIASES, ont_workflow_identity_values
from services.ont_ngs_native_settings import accepted_native_settings, seal_native_settings
from test_ont_ngs_native_completion import isolated_result_root
from test_ont_ngs_native_reference_completion import reference_fixture


@pytest.mark.parametrize("original", [{}, {"gpu_id": 0}, {"gpu_id": None}, {"gpu_id": 7}])
def test_claim_completion_sources_and_delivery_keep_one_receipt(tmp_path, monkeypatch, original):
    job, root = reference_fixture(tmp_path)
    job.params.update(original, reference_topology="linear", ont_request_workflow_id="basecall_dna")
    requested = deepcopy(job.params)
    job.params = attach_scheduler_gpu_assignment(job.params, 0)
    claimed = deepcopy(job.params)
    async def no_managed_reference(unused_job, unused_session):
        return None
    monkeypatch.setattr(native_sources, "reference_binding", no_managed_reference)
    session = SimpleNamespace(info={})
    asyncio.run(completion.validate_and_prepare_ont_native_basecall_completion(job, session=session))
    job.awaiting_input = False
    receipt = deepcopy(job.provenance["result_integrity"])
    before_sources = native_sources.sources(job, root)
    before_artifacts = native_sources.artifact_descriptors(job)
    assert before_sources and before_artifacts
    source = before_sources[0][0]
    assert session.info["ngs_derived_catalog_intents"][job.id][source["session_id"]] == source
    assert receipt["alignment_presentations"][0]["source_authority_sha256"] == native_sources.identity_sha256(source)
    assert receipt["effective_params"] == claimed
    job.params = release_scheduler_gpu_assignment(job.params)
    assert job.params == requested
    assert native_sources.sources(job, root) == before_sources
    assert native_sources.artifact_descriptors(job) == before_artifacts
    assert job.provenance["result_integrity"] == receipt
    assert accepted_native_settings(job.params, receipt) == claimed
    job.params["min_qscore"] += 1
    with pytest.raises(native_sources.storage.AlignmentSessionError, match="settings authority changed"):
        native_sources.artifact_descriptors(job)
    assert job.provenance["result_integrity"] == receipt


@pytest.mark.parametrize("damage", ["science", "gpu", "lease", "snapshot", "digest", "schema"])
def test_sealed_authority_rejects_changes_after_cleanup(damage):
    effective = attach_scheduler_gpu_assignment({"igv_report_flanking_bp": 0, "nested": {"a": 1}}, 0)
    receipt = seal_native_settings(effective)
    current = release_scheduler_gpu_assignment(effective)
    if damage == "science": current["nested"]["a"] = 2
    if damage == "gpu": current["gpu_id"] = 0
    if damage == "lease": current = attach_scheduler_gpu_assignment(current, 1)
    if damage == "snapshot": receipt["effective_params"]["nested"]["a"] = 2
    if damage == "digest": receipt["effective_params_sha256"] = "0" * 64
    if damage == "schema": receipt["effective_params_schema"] = "unknown"
    with pytest.raises(ValueError, match="native effective settings authority"):
        accepted_native_settings(current, receipt)


def test_non_gpu_and_legacy_receipts_remain_strict_and_read_only():
    params = {"min_qscore": 10, "gpu_id": 0}
    sealed = seal_native_settings(params)
    assert accepted_native_settings(params, sealed) == params
    legacy = {"effective_params_sha256": hashlib.sha256(rfc8785.dumps(params)).hexdigest()}
    before = deepcopy(legacy)
    assert accepted_native_settings(params, legacy) == params
    with pytest.raises(ValueError):
        accepted_native_settings({"min_qscore": 11, "gpu_id": 0}, legacy)
    assert legacy == before
    claimed = attach_scheduler_gpu_assignment(params, 1)
    historical = {"effective_params_sha256": hashlib.sha256(rfc8785.dumps(claimed)).hexdigest()}
    with pytest.raises(ValueError):
        accepted_native_settings(release_scheduler_gpu_assignment(claimed), historical)


def test_snapshot_is_detached_and_malformed_lease_fails_closed():
    params = {"nested": {"a": 1}}
    receipt = seal_native_settings(params)
    params["nested"]["a"] = 2
    assert receipt["effective_params"]["nested"]["a"] == 1
    with pytest.raises(ValueError, match="scheduler settings authority"):
        seal_native_settings({"_scheduler_gpu_assignment": {"schema": "unknown"}})


@pytest.mark.parametrize("alias,canonical", sorted(ONT_WORKFLOW_ALIASES.items()))
def test_every_admitted_alias_has_the_canonical_completion_lane(alias, canonical):
    mode = "fastq" if canonical in {"ont_fastq_qc", "ont_pooled_reference_assignment"} else "pod5"
    params = {"ont_workflow_id": canonical, "ont_request_workflow_id": alias,
              "workflow_id": canonical, "ont_input_mode": mode,
              "reference_fasta": "/fixture/ref.fasta", "dorado_basecall_mode": "simplex",
              "modified_bases": "none", "run_fastq_qc": True, "run_modkit": False}
    params["pod5_dir" if mode == "pod5" else "fastq_path"] = "/fixture/input"
    job = SimpleNamespace(model_id="nanopore", params=params)
    before = deepcopy(params)
    assert ont_workflow_identity_values(params) == {canonical}
    lane = completion.ont_completion_lane(job)
    assert params == before
    canonical_job = SimpleNamespace(model_id="nanopore", params={**params, "ont_request_workflow_id": canonical})
    assert lane == completion.ont_completion_lane(canonical_job)
    if canonical != "ont_pooled_reference_assignment":
        assert lane is not None
    else:
        # Pooled completion has its own native entrypoint, ahead of generic dispatch.
        from services import ont_ngs_native_pooled as pooled
        with pytest.MonkeyPatch.context() as mp:
            def reached_root(unused):
                raise RuntimeError("passed pooled identity barrier")
            mp.setattr(pooled, "resolve_persisted_job_result_root", reached_root)
            with pytest.raises(RuntimeError, match="passed pooled identity barrier"):
                pooled._validate_native(job, None, None)


@pytest.mark.parametrize("conflict", ["basecall_rna", "not_a_workflow", "BASECALL_DNA"])
def test_unknown_or_cross_workflow_identity_does_not_normalize_away(conflict):
    job = SimpleNamespace(model_id="nanopore", params={"ont_workflow_id": "ont_basecall_dna",
        "ont_request_workflow_id": conflict, "ont_input_mode": "pod5"})
    with pytest.raises(completion.OntNgsCompletionError, match="identities conflict"):
        completion.ont_completion_lane(job)


def test_external_signal_and_fastq_sibling_selectors_accept_aliases():
    fastq = SimpleNamespace(model_id="nanopore", params={"ont_workflow_id": "ont_fastq_qc",
        "ont_request_workflow_id": "fastq_qc", "ont_input_mode": "fastq"})
    assert completion.is_ont_fastq_qc_job(fastq)
    signal = SimpleNamespace(model_id="nanopore", params={"ont_workflow_id": "ont_plasmid_qc",
        "ont_request_workflow_id": "plasmid_qc", "ont_input_mode": "bam", "run_fastq_qc": False,
        "source_move_source_id": "source", "source_external_move_registration_receipt_id": "receipt"})
    assert completion.is_ont_signal_alignment_job(signal)
    assert completion.ont_completion_lane(signal) == "external_signal_alignment"
    fastq.params["workflow_id"] = "basecall_dna"
    with pytest.raises(completion.OntNgsCompletionError):
        completion.is_ont_fastq_qc_job(fastq)
    signal.params["workflow_id"] = "basecall_dna"
    assert not completion.is_ont_signal_alignment_job(signal)
    with pytest.raises(completion.OntNgsCompletionError):
        completion.ont_completion_lane(signal)


def _flank_declaration():
    source = (DORADO_LOCK_PATH.parents[2] / "modules/ngs/fastq_plasmid_qc.nf").read_text()
    return next(line.strip() for line in source.splitlines() if "def igvReportFlankingBp =" in line)


def test_zero_valid_flank_producer_uses_null_only_default():
    assert _flank_declaration() == (
        "def igvReportFlankingBp = (params.igv_report_flanking_bp != null ? "
        "params.igv_report_flanking_bp : 200) as Integer")


@pytest.mark.parametrize("settings,expected", [("[:]", 200), ("[igv_report_flanking_bp:null]", 200),
    ("[igv_report_flanking_bp:0]", 0), ("[igv_report_flanking_bp:37]", 37)])
def test_actual_groovy_flank_default_expression(settings, expected):
    groovy = shutil.which("groovy")
    if groovy is None:
        pytest.skip("Groovy required to execute the Nextflow emitter expression")
    script = f"def params = {settings}\n{_flank_declaration()}\nassert igvReportFlankingBp == {expected}\n"
    subprocess.run([groovy, "-e", script], check=True, capture_output=True, text=True)
