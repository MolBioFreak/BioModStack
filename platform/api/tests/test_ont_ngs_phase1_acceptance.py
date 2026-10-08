from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "platform" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from database import Base, Job
from model_registry import get_registry
from routers.jobs import create_job
import routers.ont_runs as ont_runs
from routers.ont_runs import (
    ONT_WORKFLOW_MODEL_MODES,
    OntNgsSubmitRequest,
    _job_create_for_ont_submit,
)
from services.ont_ngs_contract import (
    CANONICAL_ONT_WORKFLOWS,
    ONT_WORKFLOW_ALIASES,
)
from services import alignment_access, ont_submission_trust


def _load_manifest_builder():
    path = ROOT / "scripts" / "build_sequence_qc_manifest.py"
    spec = importlib.util.spec_from_file_location("build_sequence_qc_manifest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(params: dict[str, object]) -> OntNgsSubmitRequest:
    return OntNgsSubmitRequest(name="acceptance", params=params)


@pytest.fixture(autouse=True)
def _unit_contract_paths_are_prevalidated(monkeypatch):
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))


def test_explicit_canonical_model_mapping_is_complete_and_registered() -> None:
    canonical_ids = set(CANONICAL_ONT_WORKFLOWS)
    assert set(ONT_WORKFLOW_MODEL_MODES) == canonical_ids

    registry = get_registry()
    for canonical_id, model_mode in ONT_WORKFLOW_MODEL_MODES.items():
        nanopore = registry.get_model("nanopore")
        assert nanopore is not None
        assert model_mode in {mode.id for mode in nanopore.modes}
        if canonical_id == "ont_pooled_reference_assignment":
            with pytest.raises(ValueError, match="dedicated atomic submission endpoint"):
                _job_create_for_ont_submit(
                    canonical_id,
                    _request({"fastq_path": "/tmp/reads.fastq"}),
                )
            continue
        params: dict[str, object]
        if canonical_id == "ont_basecall_dna":
            params = {"pod5_dir": "/tmp/run.pod5"}
        elif canonical_id == "ont_basecall_rna":
            params = {"pod5_dir": "/tmp/run.pod5"}
        elif canonical_id in {"ont_methylation_analysis"}:
            params = {"bam_path": "/tmp/aligned.bam", "reference_fasta": "/tmp/ref.fa"}
        else:
            params = {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"}
        job = _job_create_for_ont_submit(canonical_id, _request(params))
        assert job.mode == model_mode
        assert registry.validate_job_params("nanopore", model_mode, job.params) == []


def test_real_registry_rejects_shell_syntax_in_dorado_model() -> None:
    registry = get_registry()
    errors = registry.validate_job_params(
        "nanopore",
        "basecall_dna",
        {"dorado_model": "sup; printf INJECTED >&2 #"},
    )
    assert errors == ["dorado_model does not match required pattern"]
    assert registry.validate_job_params(
        "nanopore",
        "basecall_dna",
        {"dorado_model": "dna_r10.4.1_e8.2_400bps_sup@v5.0.0"},
    ) == []

    module = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert "def doradoShellQuote(value)" in module
    assert "process DoradoPreflight" in module
    assert "biomodstack.dorado_preflight.v1" in module
    assert 'base_model="\\$PWD/sealed_models/\\${model_id}"' in module
    assert "--modified-bases-models" in module
    assert "command=(dorado)" in module
    assert "eval " not in module


@pytest.mark.parametrize(
    ("param_name", "payload"),
    [
        ("min_qscore", "1; touch /tmp/biomodstack-ngs-shell-injection-proof"),
        ("dorado_batch_size", "64; touch /tmp/biomodstack-ngs-batch-injection-proof"),
    ],
)
def test_dorado_direct_nextflow_rejects_noninteger_command_fragments(
    tmp_path: Path,
    param_name: str,
    payload: str,
) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")

    pod5_dir = tmp_path / "pod5"
    pod5_dir.mkdir()
    proof = Path(payload.rsplit(" ", 1)[-1])
    proof.unlink(missing_ok=True)
    harness = tmp_path / "dorado-validation-harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        f"include {{ DoradoPreflight }} from '{(ROOT / 'modules/ngs/dorado_basecall.nf').as_posix()}'\n"
        f"params.pod5_dir=null; params.out_dir=null; params.code_root='{ROOT.as_posix()}'; params.weights_root='/mnt/BioModStack/models'; params.container_dir='/home/dalab/biomodstack/biomodstack/apptainer'; params.pod5_python='{Path(sys.executable).as_posix()}'\n"
        "workflow { DoradoPreflight(Channel.of(file(params.pod5_dir))) }\n",
        encoding="utf-8",
    )
    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: local_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["NXF_OFFLINE"] = "true"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)
    completed = subprocess.run(
        [
            str(nextflow),
            "run",
            str(harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "work"),
            "--pod5_dir",
            str(pod5_dir),
            "--code_root",
            str(ROOT),
            "--weights_root",
            "/mnt/BioModStack/models",
            "--container_dir",
            "/home/dalab/biomodstack/biomodstack/apptainer",
            "--dorado_lock_manifest",
            str(ROOT / "config/ngs/dorado_v1.3.1.lock.json"),
            "--dorado_model_root",
            "/mnt/BioModStack/models/dorado/1.3.1",
            "--dorado_runtime_sif",
            "/home/dalab/biomodstack/biomodstack/apptainer/dorado.sif",
            "--pod5_python",
            sys.executable,
            f"--{param_name}",
            payload,
            "--out_dir",
            str(tmp_path / "out"),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "invalid int value" in combined or f"{param_name} must be an integer" in combined
    assert not proof.exists()


def test_methylation_pod5_is_reference_aligned_before_modkit() -> None:
    workflow = (ROOT / "workflows/ngs/ont_methylation_analysis.nf").read_text(encoding="utf-8")
    assert "DoradoAlign as Pod5DoradoAlign" in workflow
    assert "Pod5DoradoAlign(DoradoBasecall.out.bam, Channel.of(reference_file))" in workflow
    assert "Pod5ValidateModifiedBaseBam(Pod5DoradoAlign.out.aligned)" in workflow
    assert "Pod5PrepareBamForAnalysis(DoradoBasecall.out.bam)" not in workflow


def test_every_workflow_alias_crosses_the_real_registry_boundary() -> None:
    registry = get_registry()
    for alias, canonical_id in ONT_WORKFLOW_ALIASES.items():
        if canonical_id == "ont_pooled_reference_assignment":
            with pytest.raises(ValueError, match="dedicated atomic submission endpoint"):
                _job_create_for_ont_submit(
                    alias,
                    _request({"fastq_path": "/tmp/reads.fastq"}),
                )
            continue
        params: dict[str, object]
        if canonical_id in {"ont_basecall_dna", "ont_basecall_rna"}:
            params = {"pod5_dir": "/tmp/run.pod5"}
        elif canonical_id == "ont_methylation_analysis":
            params = {"bam_path": "/tmp/aligned.bam", "reference_fasta": "/tmp/ref.fa"}
        else:
            params = {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"}
        job = _job_create_for_ont_submit(alias, _request(params))
        assert job.params["ont_request_workflow_id"] == alias
        assert job.params["ont_workflow_id"] == canonical_id
        assert registry.validate_job_params("nanopore", job.mode, job.params) == []


def test_input_contract_requires_exactly_one_primary_input() -> None:
    with pytest.raises(ValueError, match="exactly one primary ONT input"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request({"reference_fasta": "/tmp/ref.fa"}),
        )

    with pytest.raises(ValueError, match="exactly one primary ONT input"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request(
                {
                    "fastq_path": "/tmp/reads.fastq",
                    "bam_path": "/tmp/aligned.bam",
                    "reference_fasta": "/tmp/ref.fa",
                }
            ),
        )

def test_wf_clone_vendor_insert_and_host_inputs_are_confined_and_semantically_bound(monkeypatch) -> None:
    confined: list[str] = []

    def confine(value, label, **_kwargs):
        confined.append(label)
        return str(value)

    monkeypatch.setattr(ont_runs, "_confine_submitted_path", confine)
    job = _job_create_for_ont_submit(
        "wf_clone_validation",
        _request({
            "fastq_path": "/tmp/reads.fastq",
            "reference_fasta": "/tmp/reference.fa",
            "wf_clone_primers": "/tmp/primers.tsv",
            "wf_clone_insert_reference": "/tmp/insert.fa",
            "wf_clone_host_reference": "/tmp/host.fa",
            "wf_clone_regions_bedfile": "/tmp/masked.bed",
        }),
    )
    assert {"wf_clone_primers", "wf_clone_insert_reference", "wf_clone_host_reference", "wf_clone_regions_bedfile"} <= set(confined)
    assert job.params["wf_clone_primers"] == "/tmp/primers.tsv"
    assert job.params["wf_clone_insert_reference"] == "/tmp/insert.fa"
    assert job.params["wf_clone_host_reference"] == "/tmp/host.fa"
    assert job.params["wf_clone_regions_bedfile"] == "/tmp/masked.bed"

    with pytest.raises(ValueError, match="wf_clone_insert_reference requires wf_clone_primers"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request({"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/reference.fa", "wf_clone_insert_reference": "/tmp/insert.fa"}),
        )
    with pytest.raises(ValueError, match="wf_clone_regions_bedfile requires wf_clone_host_reference"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request({"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/reference.fa", "wf_clone_regions_bedfile": "/tmp/masked.bed"}),
        )

@pytest.mark.parametrize(
    "server_controlled_param",
    [
        "bam_reference_sha256",
        "bam_source_sha256",
        "code_root",
        "container_dir",
        "data_root",
        "out_dir",
        "output_dir",
        "work_dir",
        "nxf_home",
        "singularity_cache",
        "wf_clone_nxf_home",
        "wf_clone_singularity_cache",
    ],
)
def test_generic_submit_rejects_server_controlled_provenance_and_runtime_roots(
    server_controlled_param: str,
) -> None:
    with pytest.raises(ValueError, match="server-controlled ONT provenance"):
        _job_create_for_ont_submit(
            "ont_methylation_analysis",
            _request(
                {
                    "bam_path": "/tmp/aligned.bam",
                    "reference_fasta": "/tmp/ref.fa",
                    server_controlled_param: "0" * 64,
                }
            ),
        )


def test_verification_workflow_requires_expected_reference() -> None:
    with pytest.raises(ValueError, match="requires reference_fasta"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request({"fastq_path": "/tmp/reads.fastq"}),
        )


def test_fastq_submission_cannot_claim_modkit_evidence() -> None:
    with pytest.raises(ValueError, match="modkit requires"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            _request(
                {
                    "fastq_path": "/tmp/reads.fastq",
                    "reference_fasta": "/tmp/ref.fa",
                    "run_modkit": True,
                }
            ),
        )


def test_real_registry_accepts_canonical_submission_and_job_records_identity() -> None:
    job_data = _job_create_for_ont_submit(
        "wf_clone_validation",
        OntNgsSubmitRequest(
            name="phase1-real-boundary",
            params={
                "fastq_path": "/tmp/reads.fastq",
                "reference_fasta": "/tmp/reference.fasta",
                "run_clone_validation": True,
            },
        ),
    )
    registry = get_registry()
    assert registry.validate_job_params(job_data.model_id, job_data.mode, job_data.params) == []

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            trust_token = ont_submission_trust.begin_trusted_ont_job_creation("0" * 64)
            try:
                created = await create_job(job_data, BackgroundTasks(), session)
            finally:
                ont_submission_trust.end_trusted_ont_job_creation(trust_token)
            row = await session.get(Job, created.id)
            assert row is not None
            assert row.mode == "clone_validation"
            assert row.params["ont_request_workflow_id"] == "wf_clone_validation"
            assert row.params["ont_workflow_id"] == "wf_clone_validation"
            assert row.params["ont_model_mode"] == "clone_validation"
            assert row.params["ont_input_mode"] == "fastq"
            assert row.params["ont_input_provenance"] == {
                "mode": "fastq",
                "path": "/tmp/reads.fastq",
                "source": "submitted_path",
            }
            assert row.provenance["ont_request_workflow_id"] == "wf_clone_validation"
            assert row.provenance["ont_workflow_id"] == "wf_clone_validation"
            assert row.provenance["ont_model_mode"] == "clone_validation"
            assert row.provenance["ont_input_mode"] == "fastq"
            assert row.provenance["ont_input_provenance"]["path"] == "/tmp/reads.fastq"
            assert row.provenance[alignment_access.PROVENANCE_DIGEST_KEY] == "0" * 64
            assert row.provenance[alignment_access.PROVENANCE_SCHEME_KEY] == alignment_access.SCHEME
        await engine.dispose()

    asyncio.run(scenario())


def test_manifest_distinguishes_expected_and_observed_sequence_digests(tmp_path: Path) -> None:
    module = _load_manifest_builder()
    reference = tmp_path / "reference.fasta"
    consensus = tmp_path / "consensus.fasta"
    output = tmp_path / "qc_manifest.json"
    reference.write_text(">expected\nACGTACGT\n", encoding="utf-8")
    consensus.write_text(">observed\nACGTTCGT\n", encoding="utf-8")

    payload = module.build_manifest(
        out=output,
        job_id="job-1",
        sample_name="sample-1",
        reference_fasta=reference,
        expected_sha256="b28b7e7e6b70661dfee15d5290c4bca097ca145f721c4fbc4de73ad1d1660b8b",
        consensus_fasta=consensus,
        consensus_status="ok",
        artifacts=[],
    )

    expected_digest = hashlib.sha256(b"ACGTACGT").hexdigest()
    observed_digest = hashlib.sha256(b"ACGTTCGT").hexdigest()
    assert payload["sequence_digests"]["expected_reference_sha256"] == expected_digest
    assert payload["sequence_digests"]["observed_consensus_sha256"] == observed_digest
    assert payload["reference"]["expected_sha256"] == expected_digest
    assert "observed_sha256" not in payload["reference"]
    assert payload["consensus"]["observed_sha256"] == observed_digest
    assert payload["consensus"]["provenance"]["source"] == "aligned_reads"
    assert expected_digest != observed_digest


def test_manifest_rejects_reference_copy_fallback(tmp_path: Path) -> None:
    module = _load_manifest_builder()
    reference = tmp_path / "reference.fasta"
    output = tmp_path / "qc_manifest.json"
    reference.write_text(">expected\nACGT\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fallback status labels are forbidden"):
        module.build_manifest(
            out=output,
            job_id="job-1",
            sample_name="sample-1",
            reference_fasta=reference,
            expected_sha256="1dff3e84fe7877e0673b69bbddcf40124e396e3f9943dd890c91b6a09adb9af0",
            consensus_fasta=reference,
            consensus_status="reference_copy_fallback",
            artifacts=[],
        )

def test_methylation_stage_reports_only_real_module_outputs() -> None:
    workflow = (ROOT / "workflows/ngs/ont_methylation_analysis.nf").read_text(encoding="utf-8")
    assert "modified_sites.tsv" not in workflow
    assert "modkit_pileup.log" not in workflow
    assert 'methylation/pileup.log' in workflow
    assert workflow.count('methylation/modified_base_input.bam"') == 2
    assert workflow.count('methylation/modified_base_input.bam.bai"') == 2
    assert workflow.count('methylation/modified_base_tag_check.log"') == 2
    assert 'methylation/modkit_summary.tsv' in workflow
    assert 'methylation/summary.log' in workflow
    assert workflow.count("ModkitPileup.out.log.subscribe { _ignored ->") == 2
    assert workflow.count("ModkitSummary.out.log.subscribe { _ignored ->") == 2


def test_nextflow_contracts_forbid_reference_consensus_and_guard_bam_modkit() -> None:
    plasmid_qc = (ROOT / "modules" / "ngs" / "fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    bam_prepare = (ROOT / "modules" / "ngs" / "bam_prepare.nf").read_text(encoding="utf-8")
    modkit = (ROOT / "modules" / "ngs" / "modkit_pileup.nf").read_text(encoding="utf-8")

    assert "cp reference_qc.fasta fastq_consensus.fasta" not in plasmid_qc
    assert "consensus --mode bayesian -f fasta" in plasmid_qc
    assert "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_EMPTY" in plasmid_qc
    assert "mpileup" not in plasmid_qc
    assert "create_report" in plasmid_qc
    assert 'samtools quickcheck -v "${bam}"' in bam_prepare
    assert "samtools index" in bam_prepare and "aligned.bam.bai" in bam_prepare
    assert "stageAs: 'source.bam'" in bam_prepare
    assert "BAM @SQ M5 does not match expected reference" in bam_prepare
    assert "bam_reference_sha256" in bam_prepare
    assert "MM:Z:" in modkit and "ML:B:" in modkit
    assert "no meaningful paired MM/ML modified-base tags" in modkit


@pytest.mark.parametrize(
    "input_basename", ["aligned.bam", "prepared.bam", "arbitrary-name.bam"]
)
def test_bam_prepare_runtime_handles_output_and_arbitrary_input_basenames(
    tmp_path: Path,
    input_basename: str,
) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")
    if not (samtools.is_file() and os.access(samtools, os.X_OK)):
        pytest.skip(f"samtools unavailable: {samtools}")

    sam = tmp_path / "input.sam"
    bam = tmp_path / input_basename
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n"
        "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tACGTACGTACGT\tIIIIIIIIIIII\n",
        encoding="utf-8",
    )
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    source_digest_before = hashlib.sha256(bam.read_bytes()).hexdigest()

    harness = tmp_path / "bam_prepare_harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n\n"
        f"include {{ PrepareBamForAnalysis }} from '{(ROOT / 'modules/ngs/bam_prepare.nf').as_posix()}'\n\n"
        "params.bam = null\n"
        "params.out_dir = null\n"
        "params.bam_min_mapq = 0\n\n"
        "workflow {\n"
        "    PrepareBamForAnalysis(Channel.value(file(params.bam)))\n"
        "}\n",
        encoding="utf-8",
    )
    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "    executor = 'local'\n"
        "    withLabel: dorado_cpu {\n"
        "        container = null\n"
        "        cpus = 1\n"
        "        memory = '1 GB'\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{samtools.parent}:{nextflow.parent}:{env.get('PATH', '')}"
    completed = subprocess.run(
        [
            str(nextflow),
            "run",
            str(harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "work"),
            "--bam",
            str(bam),
            "--out_dir",
            str(out_dir),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout
    assert hashlib.sha256(bam.read_bytes()).hexdigest() == source_digest_before

    provenance_log = out_dir / "align" / "bam_prepare.log"
    provenance_text = provenance_log.read_text(encoding="utf-8")
    assert f"source_sha256_before={source_digest_before}" in provenance_text
    assert f"source_sha256_after={source_digest_before}" in provenance_text
    assert "source_immutable=true" in provenance_text

    prepared = out_dir / "align" / "aligned.bam"
    index = out_dir / "align" / "aligned.bam.bai"
    assert prepared.stat().st_size > 0
    assert index.stat().st_size > 0
    subprocess.run([str(samtools), "quickcheck", "-v", str(prepared)], check=True)
    record_count = subprocess.run(
        [str(samtools), "view", "-c", str(prepared)],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert record_count == "1"


def test_nextflow_runtime_rejects_wrong_reference_and_untagged_modkit_bam(tmp_path: Path) -> None:
    nextflow_bin = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools_bin = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    if not nextflow_bin.is_file() or not samtools_bin.is_file():
        pytest.skip("Nextflow runtime gate requires executable Nextflow and samtools")

    env = os.environ.copy()
    env["PATH"] = f"{samtools_bin.parent}:{nextflow_bin.parent}:{env.get('PATH', '')}"
    env.pop("SSL_CERT_FILE", None)
    env.pop("CURL_CA_BUNDLE", None)
    env.pop("REQUESTS_CA_BUNDLE", None)

    sam = tmp_path / "input.sam"
    bam = tmp_path / "input.bam"
    bai = tmp_path / "input.bam.bai"
    reference = tmp_path / "wrong-reference.fasta"
    expected_sequence = "ACGTACGTACGT"
    expected_m5 = hashlib.md5(expected_sequence.encode("ascii")).hexdigest()
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        f"@SQ\tSN:plasmid\tLN:12\tM5:{expected_m5}\n"
        "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tACGTACGTACGT\tIIIIIIIIIIII\n",
        encoding="utf-8",
    )
    reference.write_text(">plasmid\nACGTACGTACGA\n", encoding="utf-8")
    subprocess.run(
        [str(samtools_bin), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(samtools_bin), "index", str(bam), str(bai)], check=True)

    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )

    validate_harness = tmp_path / "validate-mapped-bam.nf"
    validate_harness.write_text(
        "nextflow.enable.dsl=2\n"
        f"include {{ ValidateMappedBam }} from '{(ROOT / 'modules/ngs/bam_prepare.nf').as_posix()}'\n"
        "params.bam=null; params.bai=null; params.reference=null; params.out_dir=null\n"
        "workflow {\n"
        "  ValidateMappedBam(\n"
        "    Channel.of(tuple(file(params.bam), file(params.bai))),\n"
        "    Channel.of(file(params.reference))\n"
        "  )\n"
        "}\n",
        encoding="utf-8",
    )
    wrong_reference = subprocess.run(
        [
            str(nextflow_bin),
            "run",
            str(validate_harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "validate-work"),
            "--bam",
            str(bam),
            "--bai",
            str(bai),
            "--reference",
            str(reference),
            "--out_dir",
            str(tmp_path / "validate-out"),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert wrong_reference.returncode != 0
    wrong_reference_details = wrong_reference.stdout + wrong_reference.stderr
    wrong_reference_details += "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "validate-work").rglob(".command.err")
    )
    assert "BAM @SQ M5 does not match expected reference" in wrong_reference_details

    matching_reference = tmp_path / "matching-reference.fasta"
    matching_reference.write_text(f">plasmid\n{expected_sequence}\n", encoding="utf-8")
    matching = subprocess.run(
        [
            str(nextflow_bin),
            "run",
            str(validate_harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "matching-work"),
            "--bam",
            str(bam),
            "--bai",
            str(bai),
            "--reference",
            str(matching_reference),
            "--out_dir",
            str(tmp_path / "matching-out"),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert matching.returncode == 0, matching.stdout + matching.stderr

    modkit_harness = tmp_path / "validate-modified-base-bam.nf"
    modkit_harness.write_text(
        "nextflow.enable.dsl=2\n"
        f"include {{ ValidateModifiedBaseBam }} from '{(ROOT / 'modules/ngs/modkit_pileup.nf').as_posix()}'\n"
        "params.bam=null; params.bai=null; params.out_dir=null\n"
        "workflow {\n"
        "  ValidateModifiedBaseBam(Channel.of(tuple(file(params.bam), file(params.bai))))\n"
        "}\n",
        encoding="utf-8",
    )
    untagged = subprocess.run(
        [
            str(nextflow_bin),
            "run",
            str(modkit_harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "modkit-work"),
            "--bam",
            str(bam),
            "--bai",
            str(bai),
            "--out_dir",
            str(tmp_path / "modkit-out"),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert untagged.returncode != 0
    combined_output = untagged.stdout + untagged.stderr
    combined_output += "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "modkit-work").rglob(".command.err")
    )
    assert "no meaningful paired MM/ML modified-base tags" in combined_output
    assert not (tmp_path / "modkit-out/methylation/methylation.bed").exists()


def test_fastq_runtime_fails_closed_without_fake_consensus_or_manifest(tmp_path: Path) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")
    if not (samtools.is_file() and os.access(samtools, os.X_OK)):
        pytest.skip(f"samtools unavailable: {samtools}")

    reference = tmp_path / "reference_qc.fasta"
    reference.write_text(">plasmid\nACGTACGTACGT\n", encoding="utf-8")
    reference_digest_before = hashlib.sha256(reference.read_bytes()).hexdigest()
    fastq = tmp_path / "empty.fastq"
    fastq.write_text("", encoding="utf-8")
    sam = tmp_path / "unmapped.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n",
        encoding="utf-8",
    )
    bam = tmp_path / "unmapped.bam"
    bai = tmp_path / "unmapped.bam.bai"
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [str(samtools), "index", str(bam), str(bai)],
        check=True,
        text=True,
        capture_output=True,
    )

    harness = tmp_path / "fastq-incomplete-harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        f"include {{ FastqPlasmidQC }} from '{(ROOT / 'modules/ngs/fastq_plasmid_qc.nf').as_posix()}'\n"
        "params.bam=null; params.bai=null; params.reference=null; params.fastq=null\n"
        "params.out_dir=null; params.code_root=null; params.job_id='missing-consensus-control'\n"
        "params.expected_plasmid_size=12\n"
        "workflow {\n"
        "  FastqPlasmidQC(Channel.of(tuple(file(params.bam), file(params.bai))), Channel.of(file(params.reference)), Channel.of(file(params.fastq)))\n"
        "}\n",
        encoding="utf-8",
    )
    config = tmp_path / "fastq-local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: local_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "  withLabel: fastq_qc_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{samtools.parent}:{env.get('PATH', '')}"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)
    completed = subprocess.run(
        [
            str(nextflow), "run", str(harness), "-c", str(config),
            "-w", str(tmp_path / "work"), "--bam", str(bam), "--bai", str(bai),
            "--reference", str(reference), "--fastq", str(fastq),
            "--out_dir", str(out_dir), "--code_root", str(ROOT),
            "-ansi-log", "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == reference_digest_before
    command_output = completed.stdout + completed.stderr
    command_output += "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "work").rglob(".command.*")
    )
    assert "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_EMPTY" in command_output
    assert not (out_dir / "fastq_qc/qc_manifest.json").exists()
    assert not (out_dir / "fastq_qc/fastq_consensus.fasta").exists()


def test_nanopore_registry_has_exactly_one_authoritative_definition_per_mode() -> None:
    model = get_registry().get_model("nanopore")
    assert model is not None
    mode_ids = [mode.id for mode in model.modes]
    assert mode_ids == [
        "basecall_dna",
        "basecall_rna",
        "plasmid_qc",
        "construct_screening",
        "fastq_qc",
        "pooled_reference_assignment",
        "methylation_analysis",
        "clone_validation",
    ]
    assert len(mode_ids) == len(set(mode_ids))

    mode_params = {mode.id: set(mode.params) for mode in model.modes}
    assert {"pod5_dir", "reference_fasta", "dorado_batch_size"} <= mode_params["basecall_dna"]
    assert {"pod5_dir", "reference_fasta", "dorado_batch_size"} <= mode_params["basecall_rna"]
    assert {"pod5_dir", "bam_path", "fastq_path", "run_fastq_qc"} <= mode_params["plasmid_qc"]
    assert {"pod5_dir", "bam_path", "fastq_path", "run_fastq_qc", "run_assembly"} <= mode_params["construct_screening"]


def test_ont_workflows_do_not_accept_undocumented_multimer_qc_alias() -> None:
    for relative_path in (
        "workflows/ngs/wf_clone_validation.nf",
        "workflows/ngs/ont_construct_screening.nf",
    ):
        assert "run_multimer_qc" not in (ROOT / relative_path).read_text(encoding="utf-8")
