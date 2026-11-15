"""The standalone IGV page is an optional export: when it cannot be made the QC result still completes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/ngs/fastq_plasmid_qc.nf"


def _igv_block() -> str:
    text = MODULE.read_text(encoding="utf-8")
    start = text.index("    : > igv_report.log")
    end = text.index("    {\n        echo \"igv_report_cli_available=", start)
    block = text[start:end]
    return (block.replace("\\$", "$").replace("${igvReportFlankingBp}", "100").replace("${codeRoot}", "CODE_ROOT"))


def _run_block(tmp_path: Path, create_report: str | None, validator_exit: int) -> tuple[subprocess.CompletedProcess, Path]:
    work = tmp_path / "work"
    work.mkdir()
    shim = tmp_path / "shim"
    shim.mkdir()
    if create_report is not None:
        (shim / "create_report").write_text("#!/bin/bash\n" + create_report + "\n")
        (shim / "create_report").chmod(0o755)
    code = tmp_path / "code/scripts"
    code.mkdir(parents=True)
    (code / "validate_standalone_igv_report.py").write_text(f"import sys\nsys.exit({validator_exit})\n")
    script = ("PYTHON_CMD=(python3)\n" + _igv_block().replace("CODE_ROOT", str(tmp_path / "code"))
              + '\necho "STATUS=${igv_report_status} CLI=${igv_report_cli_available}"\n')
    # Only the shim directory and the system tools: a host create_report must not be found.
    env = {**os.environ, "PATH": f"{shim}:/usr/bin:/bin"}
    return subprocess.run(["bash", "-euc", script], cwd=work, env=env, capture_output=True, text=True), work


def test_a_good_export_is_kept(tmp_path: Path) -> None:
    result, work = _run_block(tmp_path, 'while [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done; echo "<html>ok</html>" > "$out"', 0)
    assert result.returncode == 0, result.stderr
    assert "STATUS=created CLI=1" in result.stdout
    assert (work / "igv_report.html").read_text().startswith("<html>")


def test_a_failed_export_is_recorded_and_the_step_goes_on(tmp_path: Path) -> None:
    cases = {
        "missing tool": (None, 0, "unavailable_CREATE_REPORT_UNAVAILABLE", "CLI=0"),
        "tool fails": ("exit 3", 0, "unavailable_CREATE_REPORT_FAILED", "CLI=1"),
        "empty page": ('while [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done; : > "$out"', 0, "unavailable_ARTIFACT_EMPTY", "CLI=1"),
        "page fails its check": ('while [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done; echo x > "$out"', 1, "unavailable_VALIDATE_FAILED", "CLI=1"),
    }
    for index, (name, (tool, validator_exit, status, cli)) in enumerate(cases.items()):
        sub = tmp_path / str(index)
        sub.mkdir()
        result, work = _run_block(sub, tool, validator_exit)
        assert result.returncode == 0, (name, result.stderr)
        assert f"STATUS={status} {cli}" in result.stdout, (name, result.stdout)
        assert not (work / "igv_report.html").exists(), name
        assert f"IGV_REPORT_UNAVAILABLE: {status.removeprefix('unavailable_')}" in (work / "igv_report.log").read_text(), name


def test_the_module_declares_the_page_optional_and_tells_the_manifest() -> None:
    text = MODULE.read_text(encoding="utf-8")
    assert 'path "igv_report.html", emit: igv_report, optional: true' in text
    assert "CRITICAL_FAILURE: IGV_REPORT" not in text
    assert '--igv-report-unavailable "\\${igv_report_status}"' in text
    # The scientific outputs keep their required declarations.
    assert 'path "fastq_consensus.fasta", emit: consensus\n' in text
    assert 'path "per_base_support.tsv", emit: per_base_support\n' in text
    for workflow in ("ont_fastq_qc.nf", "ont_plasmid_qc.nf"):
        body = (ROOT / "workflows/ngs" / workflow).read_text(encoding="utf-8")
        assert 'igvReportIfCreated(_ignored, "${params.out_dir}/fastq_qc/igv_report.html")' in body
        assert "igvReportIfCreated" in body.split("workflow ", 1)[0]


def test_the_manifest_records_an_unavailable_page_without_a_path(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_sequence_qc_manifest_igv_test", ROOT / "scripts/build_sequence_qc_manifest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    reference = tmp_path / "reference_qc.fasta"
    reference.write_text(">ref\nACGTACGT\n")
    present = tmp_path / "igv_report.html"
    present.write_text("<html></html>")

    def build(argv: list[str]) -> list[dict]:
        out = tmp_path / "qc_manifest.json"
        old = sys.argv
        sys.argv = ["build_sequence_qc_manifest.py", "--out", str(out), "--job-id", "job-1", "--workflow-id", "ont_fastq_qc",
                    "--input-mode", "fastq", "--sample-name", "s", "--reference-fasta", str(reference),
                    "--expected-sha256", hashlib.sha256(b"ACGTACGT").hexdigest(), "--consensus-status", "ok", "--workflow-status", "completed", "--verification-status", "review_required", *argv]
        try:
            assert module.main() == 0
        finally:
            sys.argv = old
        return [a for a in json.loads(out.read_text())["artifacts"] if a["kind"] == "igv_report"]

    made = build(["--igv-report", str(present)])
    assert len(made) == 1 and made[0]["state"] == "present" and made[0]["required"] is True
    missing = build(["--igv-report-unavailable", "unavailable_VALIDATE_FAILED"])
    assert len(missing) == 1
    assert missing[0]["state"] == "not_produced" and missing[0]["required"] is False
    assert missing[0]["unavailable_reason"] == "unavailable_VALIDATE_FAILED" and not missing[0].get("path")
