from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_standalone_igv_report.py")


def _run(report: Path, max_bytes: int = 4096) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(report), "--max-bytes", str(max_bytes)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_accepts_network_silent_standalone_igv_report(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text(
        "<!doctype html><html><head><title>Portable plasmid IGV report</title></head>"
        "<body><div id='igvDiv'></div><script>"
        "const embedded='data:application/gzip;base64,H4sIAAAAAAAA';"
        "const tableJson={\"rows\":[[0,\"plasmid\",10,20,\"variant\"]]};"
        "igv.createBrowser(document.getElementById('igvDiv'),{});"
        "</script></body></html>",
        encoding="utf-8",
    )

    result = _run(report)

    assert result.returncode == 0, result.stderr
    assert "standalone IGV report valid" in result.stdout


def test_rejects_server_bound_or_remote_standalone_report(tmp_path: Path) -> None:
    for payload in (
        "<html><script src='https://cdn.jsdelivr.net/igv.js'></script></html>",
        "<html><script>const url='/api/jobs/job-1/artifact';</script></html>",
        "<html><script>const path='file:///tmp/aligned.bam';</script></html>",
    ):
        report = tmp_path / "report.html"
        report.write_text(payload, encoding="utf-8")

        result = _run(report)

        assert result.returncode != 0
        assert "external or host-bound resource" in result.stderr