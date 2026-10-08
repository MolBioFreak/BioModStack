from pathlib import Path
import json

import pytest

from services.ont_ngs_contract import normalize_ont_launch_params
from services import ont_ngs_contract
from services.ont_submission_trust import ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
from services.gpu_orchestrator import VRAM_PROFILES
from routers import ont_runs

ROOT = Path(__file__).resolve().parents[4]


def test_quality_resolves_to_exact_locked_dna_and_rna_models() -> None:
    dna = normalize_ont_launch_params("ont_basecall_dna", {"dorado_quality_mode": "fast"})
    assert dna["ont_molecule_type"] == "dna"
    assert dna["dorado_model"] == "dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
    assert dna["dorado_batch_size"] == 64
    assert dna["modified_bases"] == "none"

    rna = normalize_ont_launch_params("ont_basecall_rna", {"dorado_quality_mode": "sup"})
    assert rna["ont_molecule_type"] == "rna"
    assert rna["dorado_model"] == "rna004_130bps_sup@v5.2.0"


def test_p4_matrix_rejects_unsupported_combinations() -> None:
    with pytest.raises(ValueError, match="quality"):
        normalize_ont_launch_params("ont_basecall_dna", {"dorado_model": "dna_r10.4.1_e8.2_400bps_sup@v5.2.0"})
    with pytest.raises(ValueError, match="RNA duplex"):
        normalize_ont_launch_params("ont_basecall_rna", {"dorado_basecall_mode": "duplex"})
    with pytest.raises(ValueError, match="barcode.*duplex|duplex.*barcode"):
        normalize_ont_launch_params("ont_basecall_dna", {"dorado_basecall_mode": "duplex", "barcode_kit": "SQK-RBK114-96"})
    with pytest.raises(ValueError, match="sample_sheet"):
        normalize_ont_launch_params("ont_basecall_dna", {"sample_sheet": "/tmp/s.csv"})
    with pytest.raises(ValueError, match="modified"):
        normalize_ont_launch_params("ont_basecall_dna", {"dorado_quality_mode": "sup", "modified_bases": "6mA"})
    with pytest.raises(ValueError, match="only supported"):
        normalize_ont_launch_params("ont_basecall_rna", {"barcode_kit": "SQK-RBK114-96"})
    with pytest.raises(ValueError, match="mutually exclusive"):
        normalize_ont_launch_params("ont_basecall_dna", {"dorado_quality_mode": "hac", "barcode_kit": "SQK-RBK114-96", "modified_bases": "6mA"})
    methylation = normalize_ont_launch_params("ont_methylation_analysis", {})
    assert methylation["modified_bases"] == "none"
    assert methylation["run_modkit"] is False


def test_public_ont_paths_are_existing_confined_and_non_symlinked(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pod5 = allowed / "pod5"
    pod5.mkdir()
    sample = pod5 / "sample.csv"
    sample.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ont_runs, "get_allowed_roots", lambda: {"inputs": allowed})
    assert ont_runs._confine_submitted_path(pod5, "pod5_dir", directory=True) == str(pod5.resolve())
    assert ont_runs._confine_submitted_path(sample, "sample_sheet", directory=False) == str(sample.resolve())
    with pytest.raises(ValueError, match="allowed"):
        ont_runs._confine_submitted_path("/etc", "pod5_dir", directory=True)
    link = allowed / "linked"
    link.symlink_to(sample)
    with pytest.raises(ValueError, match="symlink"):
        ont_runs._confine_submitted_path(link, "sample_sheet", directory=False)

    results = tmp_path / "results"
    results.mkdir()
    protected = results / "other-job.bam"
    protected.write_bytes(b"bam")
    monkeypatch.setattr(ont_runs, "get_allowed_roots", lambda: {"inputs": allowed, "data": tmp_path, "bms_results": results})
    with pytest.raises(ValueError, match="authorization"):
        ont_runs._confine_submitted_path(protected, "bam_path", directory=False)
    assert ont_runs._confine_submitted_path(
        protected, "bam_path", directory=False, allow_results=True
    ) == str(protected.resolve())


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "bad/name", ".."])
def test_ont_job_names_cannot_escape_result_roots(name: str) -> None:
    with pytest.raises(ValueError, match="job name"):
        ont_runs._safe_ont_job_name(name, "fallback")


def test_barcode_resubmission_rejects_override_maps() -> None:
    with pytest.raises(ValueError):
        ont_runs.OntBarcodeUnitSubmitRequest.model_validate({
            "target_workflow": "ont_plasmid_qc",
            "reference_fasta": "/allowed/ref.fa",
            "params": {"bam_path": "/etc/passwd"},
        })


def test_nanopore_scheduler_reserves_the_locked_vram_floor() -> None:
    assert VRAM_PROFILES["nanopore"] == {"base": 15360, "scale": 0}


def test_rna_cannot_claim_unsupported_no_trim_mode() -> None:
    with pytest.raises(ValueError, match="always trims"):
        normalize_ont_launch_params("ont_basecall_rna", {"trim_adapters": False})


def test_duplex_cannot_claim_unsupported_no_trim_mode() -> None:
    with pytest.raises(ValueError, match="duplex lacks"):
        normalize_ont_launch_params("ont_basecall_dna", {
            "dorado_basecall_mode": "duplex",
            "dorado_quality_mode": "hac",
            "duplex_pairs": "/inputs/pairs.txt",
            "trim_adapters": False,
        })


@pytest.mark.parametrize(("key", "value"), [("dorado_batch_size", 1.9), ("min_qscore", 7.8)])
def test_fractional_integer_parameters_fail_closed(key: str, value: float) -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        normalize_ont_launch_params("ont_basecall_dna", {key: value})


def test_accepted_lock_identity_cannot_drift_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accepted = normalize_ont_launch_params("ont_basecall_dna", {})
    changed_lock = tmp_path / "dorado.lock.json"
    payload = json.loads(ont_ngs_contract.DORADO_LOCK_PATH.read_text(encoding="utf-8"))
    payload["models"]["dna"]["sup"]["id"] = "replacement-model"
    changed_lock.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ont_ngs_contract, "DORADO_LOCK_PATH", changed_lock)
    with pytest.raises(ValueError, match="lock identity changed"):
        normalize_ont_launch_params("ont_basecall_dna", accepted)


def test_duplex_and_barcode_server_contracts_are_exact() -> None:
    duplex = normalize_ont_launch_params("ont_basecall_dna", {"dorado_quality_mode": "hac", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/run/pairs.txt"})
    assert duplex["dorado_stereo_model"] == "dna_r10.4.1_e8.2_5khz_stereo@v1.4"
    assert duplex["dorado_batch_size"] == 32

    barcoded = normalize_ont_launch_params("ont_basecall_dna", {"dorado_quality_mode": "fast", "barcode_kit": "SQK-RBK114-96"})
    assert barcoded["barcode_kit"] == "SQK-RBK114-96"
    with pytest.raises(ValueError, match="barcode kit"):
        normalize_ont_launch_params("ont_basecall_dna", {"barcode_kit": "SQK-UNKNOWN"})


def test_runtime_paths_and_preflight_identity_are_server_controlled() -> None:
    required = {
        "dorado_lock_manifest",
        "dorado_model_root",
        "dorado_runtime_sif",
        "dorado_preflight",
        "dorado_resolved_model_id",
        "dorado_stereo_model",
        "pod5_python",
    }
    assert required <= ONT_SERVER_CONTROLLED_RUNTIME_PARAMS


def test_api_model_matrix_matches_checked_in_lock() -> None:
    lock = json.loads((ROOT / "config/ngs/dorado_v1.3.1.lock.json").read_text(encoding="utf-8"))
    for molecule in ("dna", "rna"):
        for quality in ("fast", "hac", "sup"):
            workflow = "ont_basecall_rna" if molecule == "rna" else "ont_basecall_dna"
            normalized = normalize_ont_launch_params(workflow, {"dorado_quality_mode": quality})
            assert normalized["dorado_model"] == lock["models"][molecule][quality]["id"]
