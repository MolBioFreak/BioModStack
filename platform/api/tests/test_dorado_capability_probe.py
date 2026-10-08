from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE = REPO_ROOT / "scripts" / "dorado_supports_option.sh"


def _fake_dorado(tmp_path: Path, *, supports_summary: bool) -> Path:
    fake = tmp_path / ("dorado-supports" if supports_summary else "dorado-no-support")
    option_line = "echo '  --emit-summary  Emit sequencing summary'" if supports_summary else ":"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "test \"${1:-}\" = basecaller\n"
        "test \"${2:-}\" = --help\n"
        f"{option_line}\n"
        "for i in $(seq 1 5000); do echo \"help-line-$i\"; done\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _unsafe_live_probe(fake: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-o",
            "pipefail",
            "-c",
            '"$1" basecaller --help | grep -q -- "$2"',
            "unsafe-probe",
            str(fake),
            "--emit-summary",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_supported_fixture_reproduces_unsafe_pipefail_sigpipe(tmp_path: Path) -> None:
    fake = _fake_dorado(tmp_path, supports_summary=True)
    completed = _unsafe_live_probe(fake)
    assert completed.returncode == 141


def test_probe_accepts_supported_option_without_pipefail_sigpipe(tmp_path: Path) -> None:
    fake = _fake_dorado(tmp_path, supports_summary=True)
    completed = subprocess.run(
        ["bash", str(PROBE), str(fake), "basecaller", "--emit-summary"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_probe_rejects_unsupported_option(tmp_path: Path) -> None:
    fake = _fake_dorado(tmp_path, supports_summary=False)
    completed = subprocess.run(
        ["bash", str(PROBE), str(fake), "basecaller", "--emit-summary"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1