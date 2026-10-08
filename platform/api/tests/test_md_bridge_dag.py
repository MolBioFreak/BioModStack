"""Execute the real Nextflow join/analysis/seal DAG with native offline bytes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from test_md_bridge_closure import ROOT, fixture_replicas


def nextflow_java(jar):
    # Match the pinned launcher module opens; raw java -jar otherwise silently
    # fails to persist task context on Java 17, defeating -resume.
    packages = ('java.lang', 'java.io', 'java.nio', 'java.net', 'java.util',
                'java.util.concurrent.locks', 'java.util.concurrent.atomic',
                'java.nio.file.spi', 'sun.nio.ch', 'sun.nio.fs',
                'sun.net.www.protocol.http', 'sun.net.www.protocol.https',
                'sun.net.www.protocol.ftp', 'sun.net.www.protocol.file',
                'jdk.internal.misc', 'jdk.internal.vm', 'java.util.regex')
    return ['java', *[f'--add-opens=java.base/{p}=ALL-UNNAMED' for p in packages], '-jar', jar]


@pytest.fixture(autouse=True)
def isolated_nextflow_home(tmp_path, monkeypatch):
    monkeypatch.setenv('NXF_HOME', str(tmp_path / '.nextflow-home'))
    monkeypatch.setenv('NXF_OFFLINE', 'true')


def command(tmp_path, *, missing=False, fail=False, delay=0, count=2):
    jar = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    if not jar:
        pytest.skip("set BMS_TEST_NEXTFLOW_JAR to the pinned offline Nextflow jar")
    config, replicas = fixture_replicas(tmp_path, count=count)
    if missing:
        import shutil
        shutil.rmtree(replicas[1])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "not_applicable.json").write_text('{}')
    parameters = {"config": str(config), "bundle": str(bundle),
                  "replicas": str(tmp_path / "replica_*"), "job_id": "closure-fixture",
                  "code_root": str(ROOT), "python": sys.executable,
                  "out_dir": str(tmp_path / "published"), "fail_analysis": fail,
                  "analysis_delay": delay,
                  "md_analysis_sif_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"}
    params = tmp_path / "params.json"
    params.write_text(json.dumps(parameters))
    return nextflow_java(jar) + ["-C", "/dev/null", "run", str(ROOT / "tests/workflows/md_closure.nf"),
            "-params-file", str(params), "-ansi-log", "false"]


@pytest.mark.parametrize("failure", [None, "missing", "analysis"])
def test_native_nextflow_dag_and_resume(tmp_path, failure):
    args = command(tmp_path, missing=failure == "missing", fail=failure == "analysis")
    run = subprocess.run(args, cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    barrier = tmp_path / "published/md_completion_barrier.json"
    if failure:
        assert run.returncode != 0, run.stdout
        assert ('missing required replicas' if failure == 'missing' else 'Error executing process >') in run.stdout
        assert not barrier.exists()
    else:
        assert run.returncode == 0, run.stdout
        original = barrier.read_bytes()
        resumed = subprocess.run(args + ["-resume"], cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        assert resumed.returncode == 0, resumed.stdout
        (tmp_path / 'first.log').write_text(run.stdout)
        (tmp_path / 'resume.log').write_text(resumed.stdout)
        assert "Cached process" in resumed.stdout
        assert barrier.read_bytes() == original


def test_nextflow_cancel_during_analysis_cannot_publish_success(tmp_path):
    args = command(tmp_path, delay=30)
    log_path = tmp_path / "run.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(args, cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if "Submitted process > NATIVE_FIXTURE_ANALYSIS" in log_path.read_text():
                    break
                if process.poll() is not None:
                    pytest.fail(log_path.read_text())
                time.sleep(0.1)
            else:
                pytest.fail("native analysis did not start: " + log_path.read_text())
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=20)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    assert not (tmp_path / "published/md_completion_barrier.json").exists()


def test_singleton_nextflow_native_closure(tmp_path):
    args = command(tmp_path, count=1)
    run = subprocess.run(args, cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    assert run.returncode == 0, run.stdout
    assert (tmp_path / "published/md_completion_barrier.json").is_file()


@pytest.mark.parametrize('entrypoint', ['orchestrator.nf', 'workflow.nf'])
def test_production_dag_preview_contains_mandatory_closure(tmp_path, entrypoint):
    jar = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    if not jar:
        pytest.skip("set BMS_TEST_NEXTFLOW_JAR")
    config, _ = fixture_replicas(tmp_path)
    run = subprocess.run(nextflow_java(jar) + ["-C", "/dev/null", "run",
                          str(ROOT / 'workflows/experimental/molecular_dynamics' / entrypoint),
                          "-preview", "--md_job_config", str(config), "--job_id", "closure-fixture",
                          "--gpu_id", "0", "--md_analysis_enabled", "true",
                          "--md_analysis_implementation_sha256", "a" * 64,
                          "--code_root", str(ROOT), "--out_dir", str(tmp_path / "out")],
                         cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    assert run.returncode == 0, run.stdout
    for name in ("MD_PREPARE_CONFIG", "MD_GROMACS_REPLICA", "MD_OPENMM_REPLICA", "MD_JOIN_REPLICAS", "MD_ANALYZE_REPLICA", "MD_SEAL_RESULTS"):
        assert name in run.stdout
