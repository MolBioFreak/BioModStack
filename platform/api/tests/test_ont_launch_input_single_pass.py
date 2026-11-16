"""A FASTQ or BAM input is read once for its snapshot and not hashed again while it is the same file version."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from services import ont_submission_trust as custody

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def submitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "reads.fastq"
    payload = os.urandom(2 * 1024 * 1024 + 11)
    source.write_bytes(payload)
    monkeypatch.setattr(custody, "get_inputs_dir", lambda: inputs)
    getattr(custody, "_HASHED_VERSIONS", {}).clear()
    read = {"bytes": 0}
    real_read = os.read

    def counting(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        read["bytes"] += len(chunk)
        return chunk

    monkeypatch.setattr(custody.os, "read", counting)
    params = {"ont_input_mode": "fastq", "fastq_path": str(source),
              "ont_input_provenance": {"mode": "fastq", "path": str(source), "source": "submitted_path"}}
    return params, payload, read


def test_a_submitted_fastq_is_read_once_from_snapshot_through_launch(submitted) -> None:
    params, payload, read = submitted
    result = custody.materialize_fastq_launch_custody(params)
    assert read["bytes"] == len(payload)  # one pass: hashed while the snapshot is written
    snapshot = Path(result["fastq_path"])
    provenance = result["ont_input_provenance"]
    assert snapshot.read_bytes() == payload and snapshot.stat().st_mode & 0o777 == 0o400
    assert provenance["sha256"] == hashlib.sha256(payload).hexdigest() and provenance["size_bytes"] == len(payload)
    assert snapshot.parent.name == provenance["sha256"]
    assert custody.fastq_launch_snapshot_descriptor(result)["sha256"] == provenance["sha256"]
    # Prepare, launch and completion each check the same snapshot; none reads it again.
    for _ in range(3):
        custody.verify_fastq_launch_custody(result)
    assert read["bytes"] == len(payload)
    # A new process has no memory of it: it reads the snapshot once, then no more.
    custody._HASHED_VERSIONS.clear()
    custody.verify_fastq_launch_custody(result)
    custody.verify_fastq_launch_custody(result)
    assert read["bytes"] == 2 * len(payload)


def test_a_snapshot_whose_bytes_changed_is_still_refused(submitted) -> None:
    params, payload, _read = submitted
    result = custody.materialize_fastq_launch_custody(params)
    custody.verify_fastq_launch_custody(result)
    snapshot = Path(result["fastq_path"])
    snapshot.chmod(0o600)
    changed = bytearray(payload)
    changed[0] ^= 0xFF
    snapshot.write_bytes(bytes(changed))  # same size, different bytes
    with pytest.raises(ValueError, match="managed FASTQ launch custody bytes changed"):
        custody.verify_fastq_launch_custody(result)


def test_a_snapshot_published_against_a_stated_hash_still_refuses_other_bytes(submitted, tmp_path: Path) -> None:
    params, payload, _read = submitted
    source = Path(params["fastq_path"])
    args = dict(source_root=source.parent, family="ont_fastq_launch_snapshots", authority_id="receipt-1",
                expected_size_bytes=len(payload), suffix=".fastq")
    with pytest.raises(ValueError, match="diverged from authority"):
        custody.publish_immutable_launch_snapshot(source, expected_sha256="0" * 64, **args)
    published = custody.publish_immutable_launch_snapshot(source, expected_sha256=hashlib.sha256(payload).hexdigest(), **args)
    assert published.read_bytes() == payload
    with pytest.raises(ValueError, match="launch snapshot authority is invalid"):
        custody.publish_immutable_launch_snapshot(source, expected_sha256=None, **args)


def test_the_task_does_not_rehash_a_snapshot_that_is_still_the_same_version(tmp_path: Path) -> None:
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "calls"
    log.write_text("")
    real = subprocess.run(["bash", "-c", "command -v sha256sum"], capture_output=True, text=True, check=True).stdout.strip()
    (shim / "sha256sum").write_text(f'#!/bin/bash\necho "$*" >> "{log}"\nexec "{real}" "$@"\n')
    (shim / "sha256sum").chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
    snapshot = tmp_path / "source.snapshot.bam"
    snapshot.write_bytes(b"bam bytes")
    before = hashlib.sha256(b"bam bytes").hexdigest()
    script = ('source "$1"; version="$(stat -Lc \'%s:%.9Y:%.9Z\' "$2")"; before="$(sha256sum "$2" | awk \'{print $1}\')"; '
              '$3; ngs_snapshot_sha256_after "$2" "$version" "$before"')

    def run(between: str) -> str:
        log.write_text("")
        result = subprocess.run(["bash", "-euc", script, "test", str(REPO / "scripts/ngs_input_snapshot.sh"), str(snapshot), between],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    assert run("true") == before and len(log.read_text().splitlines()) == 1  # only the first hash
    # A snapshot that changed is hashed again, so the task still sees the difference.
    after = run(f"eval printf x >> {snapshot}")
    assert after == hashlib.sha256(b"bam bytesx").hexdigest() and len(log.read_text().splitlines()) == 2
    for module in ("dorado_align.nf", "bam_prepare.nf", "fastq_align.nf"):
        text = (REPO / "modules/ngs" / module).read_text(encoding="utf-8")
        assert "ngs_snapshot_sha256_after" in text
        assert '_after="\\$(sha256sum' not in text
