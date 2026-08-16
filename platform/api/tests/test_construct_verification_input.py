from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_construct_verification_input.py"
PYTHON = Path("/home/dalab/biomodstack/biomodstack/platform/api/.venv/bin/python")


def run_builder(tmp_path: Path, *, consensus: str | None) -> tuple[subprocess.CompletedProcess[str], Path]:
    reference = tmp_path / "expected.fasta"
    reads = tmp_path / "reads.fastq"
    observed = tmp_path / "consensus.fasta"
    out_dir = tmp_path / "verification_input"
    reference.write_text(">expected\nACGTACGT\n", encoding="utf-8")
    reads.write_text("@r1\nACGTACGT\n+\nIIIIIIII\n", encoding="utf-8")
    if consensus is not None:
        observed.write_text(f">observed\n{consensus}\n", encoding="utf-8")
    command = [
        str(PYTHON),
        str(SCRIPT),
        "--reference-fasta",
        str(reference),
        "--expected-reference-sha256",
        hashlib.sha256(b"ACGTACGT").hexdigest(),
        "--source-reads",
        str(reads),
        "--consensus-fasta",
        str(observed),
        "--consensus-method",
        "samtools_consensus",
        "--out-dir",
        str(out_dir),
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False), out_dir


def test_present_consensus_is_bound_to_independent_reads(tmp_path: Path) -> None:
    result, out_dir = run_builder(tmp_path, consensus="ACGTACGT")
    assert result.returncode == 0, result.stderr
    state = json.loads((out_dir / "observed_state.json").read_text(encoding="utf-8"))
    copied = out_dir / "observed_consensus.fasta"
    retained_reads = out_dir / state["source_reads_path"]
    assert state["state"] == "present"
    assert state["independent_from_expected"] is False
    assert state["independence_assertion"] == "pending_verifier_recomputation"
    assert state["source_kind"] == "read_derived_consensus_candidate"
    assert state["observed_sha256"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert retained_reads.read_bytes() == (tmp_path / "reads.fastq").read_bytes()
    assert state["source_reads_sha256"] == hashlib.sha256(retained_reads.read_bytes()).hexdigest()
    assert state["source_read_provenance"]["binding_method"] == "qname_and_sequence_against_primary_bam"


def test_missing_consensus_emits_state_but_no_observed_fasta(tmp_path: Path) -> None:
    result, out_dir = run_builder(tmp_path, consensus=None)
    assert result.returncode == 0, result.stderr
    state = json.loads((out_dir / "observed_state.json").read_text(encoding="utf-8"))
    assert state["state"] == "missing"
    assert state["reason"] == "CONSENSUS_NOT_PRODUCED"
    assert not (out_dir / "observed_consensus.fasta").exists()


def test_nextflow_module_resolves_observed_files_from_staged_verification_input() -> None:
    module = (REPO_ROOT / "modules" / "ngs" / "construct_verify.nf").read_text(encoding="utf-8")
    assert 'publishDir "${params.out_dir}", mode: \'copy\'' in module
    assert '${verification_input}/observed_state.json' in module
    assert '${verification_input}/observed_consensus.fasta' in module
    assert '${observed_input}' not in module
