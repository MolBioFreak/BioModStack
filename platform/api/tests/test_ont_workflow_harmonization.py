"""Shared DNA/plasmid contracts and real Nextflow graph compilation (not GPU inference)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from services.ont_ngs_contract import basecall_device_options, normalize_ont_launch_params

ROOT = Path(__file__).resolve().parents[3]


def test_remote_device_choices_do_not_probe_controller(monkeypatch):
    import shutil
    monkeypatch.setenv("BMS_ONT_DORADO_DEVICE", "cpu")
    monkeypatch.setattr(shutil, "which", lambda _: pytest.fail("controller probe on remote request"))
    result = basecall_device_options(execution_target_id="selected-worker")
    assert result["default"] == "cuda:0"
    assert result["cuda_available"] is None
    assert [x["value"] for x in result["options"]] == ["cpu", "cuda:0", "cuda:all"]


@pytest.mark.parametrize("workflow", ["ont_basecall_dna", "ont_plasmid_qc"])
@pytest.mark.parametrize("device", ["cpu", "cuda:0", "cuda:all", "cuda:2"])
def test_tandem_choices_survive_normalization(workflow, device):
    requested = dict(pod5_dir="/reads", reference_fasta="/reference.fa", dorado_device=device,
                     dorado_quality_mode="hac", modified_bases="6mA", run_modkit=True,
                     modkit_filter_threshold=0.0, run_fastq_qc=False, dorado_batch_size=0)
    result = normalize_ont_launch_params(workflow, requested)
    for key, value in requested.items():
        assert result[key] == value
    assert normalize_ont_launch_params(workflow, {})["modified_bases"] == "none"


@pytest.mark.parametrize("explicit,expected", [(None, "cuda:0"), ("cpu", "cpu"), ("cuda:2", "cuda:2")])
def test_remote_api_default_is_target_scoped(monkeypatch, explicit, expected):
    from routers import ont_runs
    monkeypatch.setenv("BMS_ONT_DORADO_DEVICE", "cpu")
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, *args, **kwargs: value)
    params = {"pod5_dir": "/reads"}
    if explicit is not None:
        params["dorado_device"] = explicit
    request = ont_runs.OntNgsSubmitRequest(name="target-device", execution_target_id="worker", params=params)
    job = ont_runs._job_create_for_ont_submit("ont_basecall_dna", request)
    assert job.params["dorado_device"] == expected
    assert job.execution_target_id == "worker"


def test_every_capability_schema_digest_resolves():
    from services.ngs_molbio_capabilities import capability_inventory, capability_parameter_schema
    for row in capability_inventory()["capabilities"]:
        schema = capability_parameter_schema(row["capability_id"])
        assert isinstance(schema, dict) and schema
    for capability in ("ngs.ont.basecall_dna", "ngs.ont.plasmid_qc"):
        fields = capability_parameter_schema(capability)["properties"]
        assert {"run_modkit", "modified_bases", "modkit_filter_threshold", "dorado_device"} <= fields.keys()
        assert fields["run_modkit"]["default"] is False
        assert fields["modified_bases"]["default"] == "none"


@pytest.mark.parametrize("mode", ["pod5", "bam", "fastq"])
def test_dna_input_contract_accepts_existing_reads(mode):
    from routers.ont_runs import _validate_ont_input_contract
    key = {"pod5": "pod5_dir", "bam": "bam_path", "fastq": "fastq_path"}[mode]
    assert _validate_ont_input_contract("ont_basecall_dna", {key: "/reads", "reference_fasta": "/ref"}) == (mode, "/reads")


@pytest.mark.parametrize("workflow", ["ont_basecall_dna", "ont_plasmid_qc"])
def test_fastq_does_not_invent_modification_probabilities(workflow):
    from routers.ont_runs import _validate_ont_input_contract
    with pytest.raises(ValueError, match="MM/ML"):
        _validate_ont_input_contract(workflow, dict(fastq_path="/reads", reference_fasta="/ref", run_modkit=True))


@pytest.mark.parametrize("workflow", ["ont_basecall_dna", "ont_plasmid_qc"])
@pytest.mark.parametrize("mode,tandem", [("pod5", True), ("bam", True), ("fastq", False)])
def test_native_workflow_graph_preview(tmp_path, workflow, mode, tandem):
    launcher = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    if not launcher.is_file():
        pytest.skip("installed Nextflow unavailable; no graph qualification")
    reads = tmp_path / ("pod5" if mode == "pod5" else f"reads.{mode}")
    if mode == "pod5":
        reads.mkdir()
    else:
        reads.write_bytes(b"")  # Graph-only fixture, not claimed to be scientific input.
    reference = tmp_path / "reference.fa"
    reference.write_text(">ref\nACGT\n")
    env = dict(os.environ, NXF_OFFLINE="true", NXF_DISABLE_CHECK_LATEST="true")
    result = subprocess.run([
        str(launcher), "-log", str(tmp_path / "nextflow.log"), "run",
        str(ROOT / f"workflows/ngs/{workflow}.nf"), "-preview", "-offline",
        "-w", str(tmp_path / "work"), "--code_root", str(ROOT),
        "--out_dir", str(tmp_path / "out"),
        "--" + {"pod5": "pod5_dir", "bam": "bam_path", "fastq": "fastq_path"}[mode], str(reads),
        "--reference_fasta", str(reference), "--run_modkit", str(tandem).lower(),
        "--run_fastq_qc", "true", "-ansi-log", "false",
    ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "nextflow.log").read_text()
    assert "ERROR" not in log, log
    if tandem:
        assert "Creating process" in log and "ModkitAnalysis:ModkitPileup" in log
        assert "ModkitAnalysis:ModkitSummary" in log
        assert ("Pod5PlasmidQC" if mode == "pod5" else "BamPlasmidQC") in log
