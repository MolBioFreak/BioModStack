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
    script = tmp_path / "smoke.nf"
    script.write_text(
        "nextflow.enable.dsl = 2\n"
        "params.bc2_gpu_ids = '0'\nparams.gpu_id = 'fallback-sentinel'\n"
        "params.weights_root = '/fixture/weights'\nparams.cache_root = '/fixture/cache'\n"
        "process RunBindCraft2 {\n    label 'gpu'\n" + directive + "\n"
        "    output:\n    path 'ext.txt'\n    script:\n"
        "    \"\"\"\n    printf '%s\\n' '${task.ext.containerOptions}' > ext.txt\n    \"\"\"\n"
        "}\nworkflow { RunBindCraft2() }\n"
    )
    config = tmp_path / "smoke.config"
    config.write_text(
        "process { withLabel: gpu { containerOptions = { task.ext.containerOptions ?: '' } } }\n"
        "docker.enabled = false\nsingularity.enabled = false\napptainer.enabled = false\n"
    )
    env = dict(os.environ)
    env.update(NXF_HOME=str(tmp_path / "nxf-home"), NXF_OFFLINE="true", NXF_ANSI_LOG="false")
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
    assert "--bind /fixture/cache/bindcraft2/compile:/cache/bindcraft2/compile" in resolved
