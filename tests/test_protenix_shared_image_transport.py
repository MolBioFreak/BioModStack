"""Offline Nextflow transport acceptance; fake Apptainer, no scientific execution."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_nextflow_reuses_shared_image_without_staging_sif(tmp_path: Path) -> None:
    configured = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    jars = [Path(configured)] if configured else sorted((Path.home() / ".nextflow/framework").glob("*/nextflow-*-one.jar"))
    if not jars or not shutil.which("java"):
        pytest.skip("installed Nextflow JAR and Java required; never download test dependencies")
    module = (REPO / "modules/conformational_mapping_protenix.nf").read_text()
    # Keep real preflight, channel mapping, container/input/beforeScript directives
    # and execution-boundary verification. Replace only scientific inference/output.
    prefix, canonical = module.split("process CanonicalProtenixEnsemble {", 1)
    directives = canonical.split("    output:", 1)[0]
    script = canonical.split("    script:\n", 1)[1].split("    mkdir -p native_protenix", 1)[0]
    transport_output = '''
    python3 - "${runtime_image}" <<'PY'
import json, os, sys
from pathlib import Path
image = Path(sys.argv[1])
info = image.stat()
Path('transport.json').write_text(json.dumps({'path':str(image), 'device':info.st_dev, 'inode':info.st_ino, 'ctime_ns':info.st_ctime_ns, 'local_images':[str(p) for p in Path.cwd().rglob('*.sif')]}))
PY
    """
}
'''
    mapping = module.split("    PrepareProtenixExecution(request_tuples)", 1)[1].split("    emit:", 1)[0]
    harness = prefix + "process CanonicalProtenixEnsemble {" + directives + '''    output:
    path('transport.json')

    script:
''' + script + transport_output + '''
workflow {
    request_tuples = Channel.of(tuple('first', file(params.request)), tuple('second', file(params.request)))
    PrepareProtenixExecution(request_tuples)
''' + mapping + "}\n"
    (tmp_path / "main.nf").write_text(harness)
    source = tmp_path / "source.sif"
    source.write_bytes(b"offline SIF transport fixture: never mounted or scientifically executed")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    weights = tmp_path / "weights"
    weights.mkdir()
    checkpoint = weights / "checkpoint.pt"
    checkpoint.write_bytes(b"offline checkpoint transport fixture")
    request = tmp_path / "request"
    request.mkdir()
    registry = {"container_digest": "sha256:" + digest, "checkpoint_relative_path": "checkpoint.pt",
                "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    (request / "cm_runtime_registry_v1.json").write_text(json.dumps(registry))
    (request / "cm_request_v1.json").write_text("{}")
    (request / "cm_complex_snapshots_v1.json").write_text("{}")
    store = tmp_path / "store"
    params = {"request": str(request), "api_python": sys.executable, "code_root": str(REPO),
              "container_dir": str(tmp_path), "runtime_image_store": str(store),
              "protenix_container_path": str(source), "protenix_weights": str(weights),
              "memory_gpu": "256 MB", "memory_cpu": "256 MB", "cpus_per_gpu": 1}
    (tmp_path / "params.json").write_text(json.dumps(params))
    (tmp_path / "nextflow.config").write_text((REPO / 'nextflow.config').read_text() + '\n' + '''
apptainer.enabled = true
apptainer.autoMounts = true
process.executor = 'local'
process.cpus = 1
process.memory = '256 MB'
process.shell = ['/bin/bash', '-euo', 'pipefail']
process { withName: PrepareProtenixExecution { container = null } }
''')
    # This executable records the image Nextflow actually selected then executes
    # only the transport shell. It deliberately does not emulate model output.
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "apptainer"
    fake.write_text(f'''#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if '--version' in args or 'version' in args:
    print('apptainer version 1.4.0')
    raise SystemExit(0)
index = next(i for i, value in enumerate(args) if value.endswith('.sif'))
image = str(Path(args[index]).absolute())
with open({str(tmp_path / 'apptainer.jsonl')!r}, 'a') as handle:
    handle.write(json.dumps({{'image':image, 'bind':' '.join(args[:index])}}) + '\\n')
raise SystemExit(subprocess.run(args[index+1:], env=os.environ | {{'APPTAINER_CONTAINER':image}}).returncode)
''')
    fake.chmod(0o755)
    log = tmp_path / "apptainer.jsonl"
    env = os.environ | {"PATH": str(binary) + os.pathsep + os.environ["PATH"],
                        "NXF_OFFLINE": "true", "NXF_HOME": str(tmp_path / "nxf-home"),
                        "NXF_ANSI_LOG": "false", "BMS_TEST_APPTAINER_LOG": str(log),
                        "BMS_DATA": str(tmp_path / 'data'), "XDG_CACHE_HOME": str(tmp_path / 'cache'),
                        "BMS_HOME": str(REPO), "BMS_CONTAINER_DIR": str(tmp_path)}
    run = subprocess.run(["java", "-jar", str(jars[-1]), "-log", str(tmp_path / "nextflow.log"),
                          "run", "main.nf", "-profile", "workstation_ryzen7960x", "-offline", "-params-file", "params.json"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    results = [json.loads(path.read_text()) for path in (tmp_path / "work").rglob("transport.json")]
    assert len(results) == 2
    shared = store / "objects/sha256" / digest / "runtime.sif"
    assert results[0] == results[1]
    assert results[0]["path"] == str(shared)
    assert results[0]["inode"] == shared.stat().st_ino
    assert results[0]["local_images"] == []
    assert not list((tmp_path / "work").rglob("*.sif"))
    assert list(store.rglob("*.sif")) == [shared]
    launches = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(launches) == 2
    assert all(launch["image"] == str(shared) for launch in launches)
    assert all(f"{shared.parent}:{shared.parent}:ro" in launch["bind"] for launch in launches)
    # Reusing an existing preflight must still validate the reference before
    # selecting any container. A forged small reference must not launch an image.
    preflight_refs = [path for path in (tmp_path / "work").rglob("runtime-image-reference.json")
                      if (path.parent.parent / "request").is_dir()]
    assert len(preflight_refs) == 2
    for reference in preflight_refs:
        forged = json.loads(reference.read_text())
        forged["path"] = str(source)
        reference.chmod(0o644)
        reference.write_text(json.dumps(forged))
    replay_mapping = mapping.replace("PrepareProtenixExecution.out.prepared", "Channel.of(tuple('reused', file(params.reused_preflight)))")
    replay = harness.split("\nworkflow {", 1)[0] + "\nworkflow {\n" + replay_mapping + "}\n"
    (tmp_path / "replay.nf").write_text(replay)
    (tmp_path / "replay.json").write_text(json.dumps(params | {"reused_preflight": str(preflight_refs[0].parent)}))
    resumed = subprocess.run(["java", "-jar", str(jars[-1]), "-log", str(tmp_path / "replay.log"),
                              "run", "replay.nf", "-profile", "workstation_ryzen7960x", "-offline", "-params-file", "replay.json"],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert resumed.returncode != 0
    assert "reference shape/path/receipt mismatch" in resumed.stdout + resumed.stderr
    assert len(log.read_text().splitlines()) == 2
    print(f"Nextflow proof: launches={len(launches)}, shared_inode={shared.stat().st_ino}, task_local_SIFs=0")
