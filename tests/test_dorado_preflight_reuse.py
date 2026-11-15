"""Preflight and the basecall step hash a file version once, and probe only the tools the run uses."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/ngs/dorado_v2.1.2.lock.json"


def _load():
    spec = importlib.util.spec_from_file_location("dorado_p4_preflight_reuse_test", ROOT / "scripts/dorado_p4_preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shell(start: str, end: str) -> str:
    text = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    begin = text.index(start)
    return text[begin:text.index(end, begin)].replace("\\$", "$").replace("\\\\0", "\\0").replace("\\\\n", "\\n")


def _counting_sha256sum(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A sha256sum that notes each call, so a test can count full-file hashes made by the shell."""
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "sha256sum.calls"
    log.write_text("")
    real = subprocess.run(["bash", "-c", "command -v sha256sum"], capture_output=True, text=True, check=True).stdout.strip()
    (shim / "sha256sum").write_text(f'#!/bin/bash\necho "$*" >> "{log}"\nexec "{real}" "$@"\n')
    (shim / "sha256sum").chmod(0o755)
    return {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}, log


def _run(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-euc", script], cwd=cwd, env=env, capture_output=True, text=True)


def test_plain_basecalling_probes_only_the_tools_it_uses(monkeypatch, tmp_path: Path) -> None:
    module = _load()
    lock = module.load_lock(LOCK)
    required = lock["required_runtime_capabilities"]
    started: list[tuple[str, ...]] = []
    tools = lock["scientific_tools"]
    answers = {
        ("samtools", "--version"): f"samtools {tools['samtools']['version']}\n", ("samtools", "help", "consensus"): "Usage: samtools consensus\n",
        ("modkit", "--version"): f"modkit {tools['modkit']['version']}\n",
        ("/opt/igv-reports/bin/pip", "show", "igv-reports"): f"Version: {tools['igv_reports']['version']}\n",
        ("create_report", "--help"): "usage: create_report\n",
        ("dorado", "basecaller", "--help"): " ".join(required["basecaller"] + required["basecaller_modified_bases"]),
    }

    def run(command, **_kwargs):
        started.append(tuple(command[3:]))
        return SimpleNamespace(returncode=0, stdout=answers[tuple(command[3:])], stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    sif = tmp_path / "runtime.sif"
    plain = module._verify_runtime_capabilities(sif, lock, mode="simplex", barcode_kit=None, modified_bases="none",
                                                scientific_tools={"samtools"})
    assert started == [("samtools", "--version"), ("dorado", "basecaller", "--help")]
    assert set(plain["scientific_tools"]) == {"samtools", "consensus_method"}
    started.clear()
    module._verify_runtime_capabilities(sif, lock, mode="simplex", barcode_kit=None, modified_bases="none",
                                        scientific_tools={"samtools", "modkit"})
    assert ("modkit", "--version") in started and ("create_report", "--help") not in started
    started.clear()
    # No selection given: every tool is probed, as before.
    module._verify_runtime_capabilities(sif, lock, mode="simplex", barcode_kit=None, modified_bases="none")
    assert len(started) == 6
    with pytest.raises(ValueError, match="unknown scientific tool"):
        module._verify_scientific_tools(sif, lock, {"samtools", "nonsense"})
    # A tool the run does use is still checked against the lock.
    answers[("modkit", "--version")] = "modkit 0.0.1\n"
    with pytest.raises(ValueError, match="modkit version mismatch"):
        module._verify_scientific_tools(sif, lock, {"modkit"})


def test_the_workflow_asks_for_unrelated_tools_only_when_their_stage_runs() -> None:
    text = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert "def probedTools = ['samtools']" in text
    assert "if (params.run_modkit != false) probedTools += 'modkit'" in text
    assert "if (params.run_fastq_qc != false) probedTools += ['samtools_consensus', 'igv_reports', 'igv_create_report']" in text
    assert '--scientific-tools ${probedTools.join(\',\')}' in text


def test_a_file_version_is_hashed_once(monkeypatch, tmp_path: Path) -> None:
    module = _load()
    path = tmp_path / "weights.bin"
    path.write_bytes(b"a" * 4096)
    made = {"count": 0}
    real = hashlib.sha256

    def counting(*args):
        made["count"] += 1
        return real(*args)

    monkeypatch.setattr(module.hashlib, "sha256", counting)
    first = module._sha256(path)
    assert module._sha256(path) == first == real(b"a" * 4096).hexdigest() and made["count"] == 1
    path.write_bytes(b"b" * 4096)  # same size, new version
    assert module._sha256(path) == real(b"b" * 4096).hexdigest() and made["count"] == 2


def test_a_retained_pod5_keeps_the_hash_taken_at_acquisition(monkeypatch, tmp_path: Path) -> None:
    module = _load()
    path = tmp_path / "reads.pod5"
    path.write_bytes(b"pod5 bytes")
    info = path.stat()
    read = SimpleNamespace(read_id="read-1", run_info=SimpleNamespace(
        acquisition_id="run-1", sample_rate=5000, flow_cell_product_code="FLO", sequencing_kit="kit",
        protocol_run_id="p", experiment_name="e", flow_cell_id="f", sequencer_position="x"))

    class Reader:
        def __init__(self, _path): pass
        def __enter__(self): return self
        def __exit__(self, *_exc): return False
        def reads(self): return iter([read])

    monkeypatch.setitem(sys.modules, "pod5", SimpleNamespace(Reader=Reader))
    hashed: list[str] = []
    monkeypatch.setattr(module, "_sha256", lambda p: hashed.append(str(p)) or hashlib.sha256(Path(p).read_bytes()).hexdigest())
    known = {"reads.pod5": {"sha256": "c" * 64, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}}
    inventory, _ids = module._read_pod5_inventory([path], tmp_path, known)
    assert hashed == [] and inventory["files"][0]["sha256"] == "c" * 64
    assert inventory["files"][0]["file_version"] == module._file_version(path)
    # A different version, or no acquisition record, is read in full.
    stale = {"reads.pod5": {**known["reads.pod5"], "mtime_ns": info.st_mtime_ns - 1}}
    inventory, _ids = module._read_pod5_inventory([path], tmp_path, stale)
    assert hashed == [str(path)] and inventory["files"][0]["sha256"] == hashlib.sha256(b"pod5 bytes").hexdigest()
    module._read_pod5_inventory([path], tmp_path)
    assert len(hashed) == 2


def test_the_basecall_step_does_not_rehash_models_preflight_already_hashed(tmp_path: Path) -> None:
    module = _load()
    model = tmp_path / "model"
    (model / "sub").mkdir(parents=True)
    (model / "weights.pt").write_bytes(b"w" * 2048)
    (model / "sub" / "config.toml").write_bytes(b"c" * 64)
    observed: dict = {}
    aggregate, files, total = module._model_aggregate(model, None, observed)
    record = module.verify_model_identity({"id": "m", "aggregate_sha256": aggregate, "files": files, "bytes": total}, model)
    assert set(record["file_versions"]) == {"weights.pt", "sub/config.toml"}
    functions = _shell("    verify_model_tree() {", "    snapshot_model() {")
    call = functions + f'\nverify_model_tree "{model}" "{aggregate}" "{files}" "{total}" .model'
    env, log = _counting_sha256sum(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "dorado_preflight.json").write_text(json.dumps({"model": record}))
    result = _run(call, work, env)
    assert result.returncode == 0, result.stderr
    assert log.read_text() == ""  # same versions: no full-file hash
    # A touched file is a new version: it is hashed in full, and the same bytes still pass.
    os.utime(model / "weights.pt", ns=(1, 1))
    result = _run(call, work, env)
    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == 3  # two files and the inventory
    # Changed bytes are still refused.
    (model / "weights.pt").write_bytes(b"x" * 2048)
    result = _run(call, work, env)
    assert result.returncode != 0 and "model identity changed after preflight" in result.stderr
    # A record without versions (an older preflight) hashes as before.
    older = {key: value for key, value in record.items() if key != "file_versions"}
    (model / "weights.pt").write_bytes(b"w" * 2048)
    (work / "dorado_preflight.json").write_text(json.dumps({"model": older}))
    log.write_text("")
    assert _run(call, work, env).returncode == 0 and len(log.read_text().splitlines()) == 3


@pytest.mark.parametrize("retained,expected_hashes", [(True, 0), (False, 1)])
def test_the_basecall_step_hashes_pod5_only_for_the_copy_it_makes(tmp_path: Path, retained: bool, expected_hashes: int) -> None:
    module = _load()
    source = tmp_path / "pod5"
    source.mkdir()
    leaf = source / "reads.pod5"
    leaf.write_bytes(b"signal" * 500)
    leaf.chmod(0o444)
    entry = {"relative_path": "reads.pod5", "sha256": hashlib.sha256(leaf.read_bytes()).hexdigest(),
             "bytes": leaf.stat().st_size, "file_version": module._file_version(leaf)}
    shell = _shell("    retained_pod5=", '    if [[ -n "\\${pair_relative}"')
    script = f'source "{ROOT / "scripts/ngs_input_snapshot.sh"}"; pod5_source="{source}"; ' + shell
    env, log = _counting_sha256sum(tmp_path)

    def consume(name: str, record: dict) -> subprocess.CompletedProcess:
        work = tmp_path / name
        work.mkdir()
        (work / "dorado_preflight.json").write_text(json.dumps({"inputs": {"retained": retained, "files": [record]}}))
        log.write_text("")
        return _run(script, work, env)

    result = consume("same", entry)
    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == expected_hashes
    assert (tmp_path / "same/sealed_pod5/reads.pod5").read_bytes() == leaf.read_bytes()
    # Without a recorded version (an older preflight) both the source and the sealed name are hashed, as before.
    result = consume("older", {key: value for key, value in entry.items() if key != "file_version"})
    assert result.returncode == 0 and len(log.read_text().splitlines()) == 2
    # A source that is not the version preflight saw, with different bytes, is still refused.
    leaf.chmod(0o644)
    leaf.write_bytes(b"other!" * 500)
    result = consume("changed", entry)
    assert result.returncode != 0 and "POD5 input changed after preflight" in result.stderr


def test_the_basecall_step_does_not_rehash_the_runtime_image_preflight_already_hashed() -> None:
    text = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    start = text.index("    runtime_expected=")
    block = text[start:text.index("    source ", start)]
    assert ".runtime.assets.runtime_sif.file_version // empty" in block
    assert "sha256sum /runtime/dorado.sif" in block and "Dorado runtime changed after preflight" in block
