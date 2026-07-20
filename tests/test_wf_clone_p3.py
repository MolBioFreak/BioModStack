from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_wf_clone_runtime.py"
ADAPTER = REPO_ROOT / "scripts" / "adapt_wf_clone_validation.py"
LOCK = REPO_ROOT / "config" / "ngs" / "wf_clone_validation_v1.8.4.lock.json"
MODULE = REPO_ROOT / "modules" / "ngs" / "clone_validation.nf"
WORKFLOW = REPO_ROOT / "workflows" / "ngs" / "wf_clone_validation.nf"
PATCH_FILE = REPO_ROOT / "patches" / "wf-clone-validation-v1.8.4-nextflow25.patch"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_json(script: Path, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(script), *(str(arg) for arg in args)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def runtime_fixture(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "P3 Test")
    (source / "main.nf").write_text("workflow { }\n", encoding="utf-8")
    git(source, "add", "main.nf")
    git(source, "commit", "-qm", "fixture")

    patch = tmp_path / "compat.patch"
    patch.write_text("fixture patch identity\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    images = []
    for index in range(5):
        image = cache / f"image-{index}.img"
        image.write_bytes(f"image {index}\n".encode())
        images.append(
            {
                "uri": f"docker://example.invalid/image:{index}",
                "cache_file": image.name,
                "sha256": sha256(image),
            }
        )
    model_store = tmp_path / "models"
    model_id = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
    (model_store / model_id).mkdir(parents=True)
    nextflow = tmp_path / "nextflow"
    nextflow.write_text("#!/bin/sh\necho 'version 25.10.0 build 10289'\n", encoding="utf-8")
    nextflow.chmod(0o755)
    lock = {
        "schema": "biomodstack.wf_clone_validation_lock.v1",
        "lock_version": 1,
        "upstream": {
            "repository": "https://example.invalid/upstream.git",
            "tag": "v1.8.4",
            "commit": git(source, "rev-parse", "HEAD"),
            "tree": git(source, "rev-parse", "HEAD^{tree}"),
        },
        "patched_source": {
            "path": str(source),
            "commit": git(source, "rev-parse", "HEAD"),
            "tree": git(source, "rev-parse", "HEAD^{tree}"),
        },
        "compatibility_patch": {
            "path": str(patch),
            "sha256": sha256(patch),
            "base_commit": git(source, "rev-parse", "HEAD"),
            "result_commit": git(source, "rev-parse", "HEAD"),
            "result_tree": git(source, "rev-parse", "HEAD^{tree}"),
        },
        "nextflow": {"executable": str(nextflow), "version": "25.10.0", "build": "10289"},
        "containers": {"cache_dir": str(cache), "images": images},
        "models": {"store": str(model_store), "accepted_upstream_ids": [model_id], "default": model_id},
        "runtime_policy": {"network": "forbidden", "nxf_offline": True},
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path, lock


def validate(lock: Path, model: str) -> subprocess.CompletedProcess[str]:
    return run_json(VALIDATOR, "--lock", lock, "--model", model, "--output", "-")


def test_runtime_lock_validator_accepts_exact_fixture(tmp_path: Path) -> None:
    lock, payload = runtime_fixture(tmp_path)
    model = payload["models"]["default"]  # type: ignore[index]
    result = validate(lock, str(model))
    assert result.returncode == 0, result.stderr
    provenance = json.loads(result.stdout)
    assert provenance["schema"] == "biomodstack.wf_clone_validation_runtime_provenance.v1"
    assert provenance["validation_status"] == "valid"
    assert provenance["selected_model_id"] == model
    assert len(provenance["images"]) == 5


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("source_mismatch", "SOURCE_COMMIT_MISMATCH"),
        ("dirty_source", "SOURCE_DIRTY"),
        ("image_mismatch", "IMAGE_SHA256_MISMATCH"),
        ("image_missing", "IMAGE_MISSING"),
        ("runtime_mismatch", "NEXTFLOW_VERSION_MISMATCH"),
        ("model_mismatch", "MODEL_ID_UNSUPPORTED"),
    ],
)
def test_runtime_lock_validator_fails_closed(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    lock_path, lock = runtime_fixture(tmp_path)
    model = str(lock["models"]["default"])  # type: ignore[index]
    if mutation == "source_mismatch":
        lock["patched_source"]["commit"] = "0" * 40  # type: ignore[index]
    elif mutation == "dirty_source":
        (Path(lock["patched_source"]["path"]) / "dirty").write_text("x")  # type: ignore[index]
    elif mutation == "image_mismatch":
        lock["containers"]["images"][0]["sha256"] = "0" * 64  # type: ignore[index]
    elif mutation == "image_missing":
        first = lock["containers"]["images"][0]  # type: ignore[index]
        (Path(lock["containers"]["cache_dir"]) / first["cache_file"]).unlink()  # type: ignore[index]
    elif mutation == "runtime_mismatch":
        lock["nextflow"]["version"] = "24.10.0"  # type: ignore[index]
    elif mutation == "model_mismatch":
        model = "dna_r10.4.1_e8.2_400bps_fast@v5.0.0"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    result = validate(lock_path, model)
    assert result.returncode != 0
    failure = json.loads(result.stderr)
    assert failure["validation_status"] == "invalid"
    assert failure["reason_code"] == reason


def adapter_fixture(root: Path, *, full_reference: bool = True) -> None:
    root.mkdir()
    (root / "sample02.final.fasta").write_text(">sample02\nACGTACGT\n", encoding="utf-8")
    (root / "sample02.assembly_stats.tsv").write_text(
        "read_id\tfilename\trunid\tsample_name\tread_length\tmean_quality\tchannel\tread_number\tstart_time\n"
        "sample02\tassembly.fastq\t\tsample02\t8\t46.75\t0\t0\t\n",
        encoding="utf-8",
    )
    (root / "sample02.bam").write_bytes(b"BAM fixture inventory only\n")
    (root / "sample02.bam.bai").write_bytes(b"BAI fixture inventory only\n")
    (root / "sample_status.txt").write_text(
        "Sample,Assembly completed / failed reason,Length\nsample02,Completed successfully,8\n",
        encoding="utf-8",
    )
    (root / "wf-clone-validation-report.html").write_text("<html>report</html>\n", encoding="utf-8")
    if full_reference:
        (root / "sample02.full_construct.calls.bcf").write_bytes(b"BCF\x02\x02fixture")
        (root / "sample02.full_construct.calls.bcf.csi").write_bytes(b"CSI fixture")
    (root / "plannotate.json").write_text('{"features": []}\n', encoding="utf-8")
    (root / "sample02.annotations.bed").write_text("sample02\t0\t4\tfeature\n", encoding="utf-8")
    (root / "sample02.annotations.gbk").write_text("LOCUS       sample02 8 bp\n", encoding="utf-8")


def run_adapter(root: Path, tmp_path: Path, *, full_reference: bool = True, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    provenance = tmp_path / "runtime-provenance.json"
    provenance.write_text(
        json.dumps({"schema": "biomodstack.wf_clone_validation_runtime_provenance.v1", "validation_status": "valid"}),
        encoding="utf-8",
    )
    args: list[object] = [
        "--result-root", root,
        "--runtime-provenance", provenance,
        "--sample", "sample02",
        "--execution-exit-code", exit_code,
        "--source-bam", root / "sample02.bam",
        "--source-bai", root / "sample02.bam.bai",
        "--output", tmp_path / "adapter.json",
    ]
    if full_reference:
        args.append("--full-reference-provided")
    return run_json(ADAPTER, *args)


def test_adapter_happy_path_inventory_and_non_scientific_execution_status(tmp_path: Path) -> None:
    root = tmp_path / "results"
    adapter_fixture(root)
    result = run_adapter(root, tmp_path)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "adapter.json").read_text())
    assert manifest["schema"] == "biomodstack.wf_clone_validation_adapter.v1"
    assert manifest["execution"]["status"] == "SUCCEEDED"
    assert manifest["upstream_sample_status"]["status"] == "completed"
    assert manifest["scientific_verdict"] == "REVIEW"
    assert "CANONICAL_PHASE2_VERIFICATION_REQUIRED" in manifest["scientific_reason_codes"]
    assert manifest["missing_evidence_reasons"] == []
    assert {item["kind"] for item in manifest["authoritative_inputs"]} == {
        "authoritative_analysis_bam",
        "authoritative_analysis_bai",
    }
    kinds = {artifact["kind"] for artifact in manifest["artifacts"]}
    assert {"final_fasta", "assembly_stats", "bam", "bai", "full_reference_bcf", "full_reference_csi", "upstream_report", "runtime_provenance"} <= kinds
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"])



@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("duplicate", "AMBIGUOUS_ARTIFACT"),
        ("missing_bai", "AUTHORITATIVE_INPUT_INVALID"),
        ("missing_bcf", "REQUIRED_ARTIFACT_MISSING"),
        ("malformed_fasta", "MALFORMED_FASTA"),
        ("status_contradiction", "STATUS_EVIDENCE_CONTRADICTION"),
        ("stats_contradiction", "STATS_EVIDENCE_CONTRADICTION"),
    ],
)
def test_adapter_fails_closed_on_malformed_or_contradictory_outputs(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    root = tmp_path / "results"
    adapter_fixture(root)
    if mutation == "duplicate":
        shutil.copyfile(root / "sample02.final.fasta", root / "other.final.fasta")
    elif mutation == "missing_bai":
        (root / "sample02.bam.bai").unlink()
    elif mutation == "missing_bcf":
        (root / "sample02.full_construct.calls.bcf").unlink()
    elif mutation == "malformed_fasta":
        (root / "sample02.final.fasta").write_text("not fasta\n", encoding="utf-8")
    elif mutation == "status_contradiction":
        (root / "sample_status.txt").write_text(
            "Sample,Assembly completed / failed reason,Length\nsample02,Completed successfully,9\n",
            encoding="utf-8",
        )
    elif mutation == "stats_contradiction":
        stats = root / "sample02.assembly_stats.tsv"
        stats.write_text(stats.read_text().replace("\t8\t46.75", "\t9\t46.75"), encoding="utf-8")
    result = run_adapter(root, tmp_path)
    assert result.returncode != 0
    assert json.loads(result.stderr)["reason_code"] == reason


def test_adapter_rejects_symlink_and_path_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.fasta"
    outside.write_text(">sample02\nACGTACGT\n", encoding="utf-8")
    root = tmp_path / "results"
    adapter_fixture(root)
    (root / "sample02.final.fasta").unlink()
    (root / "sample02.final.fasta").symlink_to(outside)
    result = run_adapter(root, tmp_path)
    assert result.returncode != 0
    assert json.loads(result.stderr)["reason_code"] == "ARTIFACT_SYMLINK_FORBIDDEN"


def test_checked_in_production_lock_and_patch_bind_exact_live_identities() -> None:
    payload = json.loads(LOCK.read_text(encoding="utf-8"))
    schema = json.loads((REPO_ROOT / "schemas/ngs/wf_clone_validation_lock.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
    assert payload["schema"] == "biomodstack.wf_clone_validation_lock.v1"
    assert payload["upstream"] == {
        "repository": "https://github.com/epi2me-labs/wf-clone-validation.git",
        "tag": "v1.8.4",
        "commit": "b3bf4ee47f730bba2239fa7f1d5e8e9bac328b42",
        "tree": "9cc0a24beee74eccdb07765b755fa64e04bd8141",
    }
    assert payload["patched_source"]["commit"] == "7e6b7f0dfe31ee855ec1342c5ea8c5a73021d5a4"
    assert payload["patched_source"]["tree"] == "6d76e709d6ba599f30854fc0478da555c924e18e"
    assert len(payload["containers"]["images"]) == 5
    assert sha256(PATCH_FILE) == payload["compatibility_patch"]["sha256"]
    assert payload["runtime_policy"] == {"network": "forbidden", "nxf_offline": True}


def test_adapter_manifest_conforms_to_checked_in_schema(tmp_path: Path) -> None:
    root = tmp_path / "results"
    adapter_fixture(root)
    result = run_adapter(root, tmp_path)
    assert result.returncode == 0, result.stderr
    schema = json.loads((REPO_ROOT / "schemas/ngs/wf_clone_validation_adapter.schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "adapter.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(manifest)


def test_runtime_wrapper_is_immutable_and_preserves_exact_selections() -> None:
    source = MODULE.read_text(encoding="utf-8")
    forbidden = ("nextflow pull", "cp -a", "git clone", "git apply", "sed -i", "wf_clone_assets", "retrying", "chosen_tool")
    for token in forbidden:
        assert token not in source
    assert "NXF_OFFLINE=true" in source
    assert "validate_wf_clone_runtime.py" in source
    assert "--assembly_tool \"${assemblyTool}\"" in source
    assert "--override_basecaller_cfg \"${basecallerModel}\"" in source
    assert "['flye', 'canu']" in source
    assert "dna_r10.4.1_e8.2_400bps_hac@v5.0.0" in source
    assert "doradoModel == \"fast\"" not in source


def test_workflow_bridges_adapter_consensus_to_phase2_without_upstream_pass() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")
    assert "CloneValidationAdapter" in workflow
    assert "ConstructVerify(" in workflow
    assert "CloneValidationAdapter.out.manifest" in workflow or "CloneValidationAdapter.out.verification_input" in workflow
    assert "sample_status" not in workflow.lower() or "scientific" not in workflow.lower()
    assert "scientific_verdict" not in module
    assert "adapter_manifest.json" in module
    assert "runtime_provenance.json" in module
    assert "fastq -F 2304 -n" in module
    assert "build_construct_verification_input.py" in module
    assert '--result-root "\\$(realpath ${result_root})"' in module
    assert '--source-bam "\\$(realpath ${aligned_bam})"' in module
    assert '--source-bai "\\$(realpath ${aligned_bai})"' in module
    assert "SOURCE_READ_PROVENANCE_UNAVAILABLE" not in module
