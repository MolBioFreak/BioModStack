"""Real Nextflow directive rendering; inert shell only, no model/GPU claim."""
from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_bc2_ext_options_resolve_at_actual_task_submission(tmp_path):
    jar = Path(os.environ.get("BMS_TEST_NEXTFLOW_JAR", ""))
    if not jar.is_file():
        pytest.skip("Set BMS_TEST_NEXTFLOW_JAR to the pinned cached Nextflow distribution")
    module = (ROOT / "modules/bindcraft2.nf").read_text()
    directive = next(line for line in module.splitlines() if line.strip().startswith("ext "))
    preamble = next(line for line in module.splitlines() if line.strip().startswith("beforeScript "))
    cache = tmp_path / "cache"
    script = tmp_path / "smoke.nf"
    script.write_text(
        "nextflow.enable.dsl = 2\n"
        "params.bc2_gpu_ids = '0'\nparams.gpu_id = 'fallback-sentinel'\n"
        "params.weights_root = '/fixture/weights'\n"
        + f"params.cache_root = '{cache}'\n"
        + "process RunBindCraft2 {\n    label 'gpu'\n" + directive + "\n" + preamble + "\n"
        "    output:\n    path 'ext.txt'\n    script:\n"
        "    \"\"\"\n    printf '%s\\n' '${task.ext.containerOptions}' > ext.txt\n    \"\"\"\n"
        "}\nworkflow { RunBindCraft2() }\n"
    )
    # Inert container seam checks the host preamble before running only printf.
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake_engine = binary_dir / "singularity"
    fake_engine.write_text(
        "#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n"
        + f"assert (Path({str(cache)!r})/'bindcraft2'/'compile').is_dir(), 'host cache missing before container entry'\n"
        + "index=sys.argv.index('/bin/bash')\nos.execv('/bin/bash',sys.argv[index:])\n"
    )
    fake_engine.chmod(0o700)
    image = tmp_path / "inert-fixture.sif"
    image.write_text("inert container transport fixture; not a runtime image\n")
    config = tmp_path / "smoke.config"
    config.write_text(
        "process { withLabel: gpu { containerOptions = { task.ext.containerOptions ?: '' } } }\n"
        + f"process.container = '{image}'\n"
        + "docker.enabled = false\nsingularity.enabled = true\napptainer.enabled = false\n"
    )
    env = dict(os.environ)
    env.update(NXF_HOME=str(tmp_path / "nxf-home"), NXF_OFFLINE="true", NXF_ANSI_LOG="false",
               BMS_TEST_BC2_CACHE=str(cache), PATH=str(binary_dir) + os.pathsep + env["PATH"])
    result = subprocess.run(
        ["java", "--add-opens=java.base/java.lang=ALL-UNNAMED",
         "--add-opens=java.base/java.util=ALL-UNNAMED",
         "--add-opens=java.base/java.nio=ALL-UNNAMED", "-jar", str(jar),
         "run", str(script), "-c", str(config), "-w", str(tmp_path / "work")],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = list((tmp_path / "work").rglob("ext.txt"))
    assert len(outputs) == 1
    resolved = outputs[0].read_text()
    assert "CUDA_VISIBLE_DEVICES=0" in resolved and "fallback-sentinel" not in resolved
    assert "BINDCRAFT_AF2_PARAMS=/fixture/weights/alphafold/params" in resolved
    assert f"--bind {cache}/bindcraft2/compile:/cache/bindcraft2/compile" in resolved
    wrapper = outputs[0].with_name(".command.run").read_text()
    assert wrapper.index("mkdir -p") < wrapper.index("singularity exec")
