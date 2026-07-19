"""Environment-gated runtime smoke test for the ONT FASTQ QC workflow.

Skips on lightweight CI/dev containers that don't have the runtime.
Two execution paths:
  1) Container mode — requires apptainer/singularity CLI + dorado.sif
  2) Local mode   — requires minimap2 + samtools on PATH
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

# Path to the dorado.sif container
SIF_PATH = REPO_ROOT / "apptainer" / "dorado.sif"

# Known tool paths (sandbox PATH is limited; tools live outside it)
MICROMAMBA_BIN = Path("/home/dalab/micromamba/bin")
LOCAL_BIN = Path("/home/dalab/.local/bin")

# Directories to add to PATH when running Nextflow in local mode
TOOL_DIRS = [
    p for p in (MICROMAMBA_BIN, LOCAL_BIN)
    if p.exists()
]


def _which(name: str) -> str | None:
    """Like shutil.which but also checks known tool directories."""
    found = shutil.which(name)
    if found:
        return found
    for d in TOOL_DIRS:
        candidate = d / name
        if candidate.exists():
            return str(candidate)
    return None


def _require_runtime_command(*names: str) -> None:
    for name in names:
        if _which(name) is None:
            pytest.skip(f"{name} is not installed in this test environment")


def _detect_execution_mode() -> str:
    """Return 'local', 'container', or 'skip' based on available tooling."""
    # Prefer local mode when minimap2/samtools are available — the Singularity
    # SIF extraction path fails in Alpine-based Nextflow containers (liblzo2).
    if _which("minimap2") is not None and _which("samtools") is not None:
        return "local"
    if MICROMAMBA_BIN.exists():
        if (MICROMAMBA_BIN / "minimap2").exists() and (MICROMAMBA_BIN / "samtools").exists():
            return "local"

    has_container_engine = (
        _which("apptainer") is not None
        or _which("singularity") is not None
    )
    if has_container_engine and SIF_PATH.exists():
        return "container"

    pytest.skip(
        "No apptainer/singularity (container mode) and no minimap2/samtools on PATH "
        "(local mode) — cannot run runtime smoke test"
    )


def test_ont_fastq_qc_runtime_emits_core_artifacts(tmp_path: Path):
    """Run tiny FASTQ+reference through Nextflow and assert advertised outputs."""
    nextflow_bin = Path("/usr/local/bin/nextflow")
    if not (nextflow_bin.is_file() and os.access(nextflow_bin, os.X_OK)):
        pytest.skip(f"direct Nextflow launcher is unavailable: {nextflow_bin}")
    configured_nextflow = str(nextflow_bin)

    mode = _detect_execution_mode()
    assert mode in ("container", "local"), f"unexpected mode: {mode}"

    # Explicit worktree-safe launchers can use pytest's disposable directory.
    # The legacy container wrapper only mounts /tmp and its configured checkout,
    # so retain the repository fixture path for that compatibility mode.
    fixture_dir = tmp_path if configured_nextflow else REPO_ROOT / "platform/api/tests/ngs_runtime_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ref = fixture_dir / f"ref_{ts}.fasta"
    fastq = fixture_dir / f"reads_{ts}.fastq"
    out_dir = fixture_dir / f"out_{ts}"
    work_dir = fixture_dir / f"work_{ts}"

    seq = "ACGT" * 200
    ref.write_text(f">tiny_plasmid\n{seq}\n")
    fastq.write_text(f"@read_1\n{seq}\n+\n{'I' * len(seq)}\n")

    # ── Build command ────────────────────────────────────────────────
    cmd = [
        str(nextflow_bin),
        "run",
        str(REPO_ROOT / "workflows/ngs/ont_fastq_qc.nf"),
        "-w",
        str(work_dir),
        "--fastq_path",
        str(fastq),
        "--reference_fasta",
        str(ref),
        "--out_dir",
        str(out_dir),
        "--code_root",
        str(REPO_ROOT),
        "--expected_plasmid_size",
        str(len(seq)),
        "--dimer_output_mode",
        "core",
    ]

    env = os.environ.copy()
    env.update({
        "NXF_VER": "25.10.1",
        "NXF_OFFLINE": "true",
        "NXF_DISABLE_CHECK_LATEST": "true",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        "CURL_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
    })
    # Add known tool dirs to PATH so subprocess can find nextflow + tools
    extra = ":".join(str(d) for d in TOOL_DIRS)
    if extra:
        env["PATH"] = f"{extra}:{env['PATH']}"

    # Ensure work_dir exists before writing temp config files
    work_dir.mkdir(parents=True, exist_ok=True)

    if mode == "container":
        # Container mode with local SIF — override container_dir to local path,
        # drop the weights bind mount (not needed for FastqAlign), and ensure
        # code_root is bound so process scripts resolve scripts/ paths.
        # We pass --container_dir as a CLI param since CLI params have highest
        # precedence over profiles in nextflow's config loading order.
        sif_dir = SIF_PATH.parent
        cmd.extend(["--container_dir", str(sif_dir)])
        # Override containerOptions to drop the weights bind mount
        override_cfg = tempfile.NamedTemporaryFile(
            mode="w", suffix=".config", delete=False, dir=work_dir
        )
        override_cfg.write(
            f'withLabel: dorado_cpu {{\n'
            f'    containerOptions = "--bind {REPO_ROOT}"\n'
            f'}}\n'
        )
        override_cfg.close()
        profile = "singularity" if _which("singularity") else "apptainer"
        cmd.extend(["-profile", f"{profile},ont_fastq_qc", "-c", override_cfg.name])
    else:
        # Local mode override the container directive so Nextflow
        # doesn't try to pull dorado.sif (which requires apptainer/singularity).
        override_cfg = tempfile.NamedTemporaryFile(
            mode="w", suffix=".config", delete=False, dir=work_dir
        )
        override_cfg.write(
            "process {\n"
            "    withLabel: dorado_cpu { container = null }\n"
            "}\n"
        )
        override_cfg.close()
        cmd.extend(["-profile", "ont_fastq_qc", "-c", override_cfg.name])

    # ── Execute ──────────────────────────────────────────────────────
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout

    # ── Assert expected artifacts ────────────────────────────────────
    expected = [
        out_dir / "align/aligned.bam",
        out_dir / "align/aligned.bam.bai",
        out_dir / "fastq_qc/qc_manifest.json",
        out_dir / "fastq_qc/per_base_support.tsv",
        out_dir / "fastq_qc/fastq_consensus.fasta",
        out_dir / "multimer_qc/dimer_breakpoint_call.tsv",
        out_dir / "multimer_qc/dimer_evidence_by_position.tsv",
        out_dir / "multimer_qc/dimer_read_events.tsv",
    ]
    missing = [str(path) for path in expected if not path.exists()]
    assert missing == [], f"Missing expected artifacts: {missing}"

    assert (out_dir / "fastq_qc/per_base_support.tsv").stat().st_size > 0
    manifest_path = out_dir / "fastq_qc/qc_manifest.json"
    assert manifest_path.stat().st_size > 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_schema_version"] == 2
    assert manifest["consensus"]["fallback"] is False
    assert manifest["consensus"]["provenance"]["source"] == "aligned_reads"
    assert manifest["sequence_digests"]["expected_reference_sha256"]
    assert manifest["sequence_digests"]["observed_consensus_sha256"]
    assert manifest["interpretation"]["verified_construct_status"] == "review_required"

    fastq_dir = manifest_path.parent
    generated_paths = {
        path.name for path in fastq_dir.iterdir() if path.is_file() and path.name != manifest_path.name
    }
    declared_artifacts = [artifact for artifact in manifest["artifacts"] if artifact.get("path")]
    declared_paths = {artifact["path"] for artifact in declared_artifacts}
    assert declared_paths == generated_paths, {
        "generated_but_undeclared": sorted(generated_paths - declared_paths),
        "declared_but_missing": sorted(declared_paths - generated_paths),
    }
    assert all(artifact["state"] == "present" for artifact in declared_artifacts)
    assert all(
        artifact["state"] != "missing_after_workflow" for artifact in manifest["artifacts"]
    )
