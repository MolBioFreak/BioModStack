"""Pinned Nextflow parent operators + production scoring shell, inert science only.

The contiguous parent scoring branch is extracted unchanged, not reimplemented.
Only native installation paths are relocated; no model inference is performed.
DB finalization belongs to the separate publication-owner regression suite.
"""
import csv
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "25.10.1"


@pytest.mark.parametrize("cutoff,all_missing", [(None, False), (0, False), (0, True)])
def test_parent_optional_scores_real_nextflow(tmp_path, cutoff, all_missing):
    launcher = Path(os.environ.get("BMS_TEST_NEXTFLOW_LAUNCHER", "/usr/local/bin/nextflow"))
    jar = Path(os.environ.get("BMS_TEST_NEXTFLOW_JAR", str(Path.home() / ".nextflow/framework" / VERSION / f"nextflow-{VERSION}-one.jar")))
    if not launcher.is_file() or not jar.is_file():
        pytest.skip("Pinned Nextflow distribution launcher/JAR not installed")
    native = tmp_path / "inert-native"
    analysis = native / "analysis"
    analysis.mkdir(parents=True)
    (native / "models").mkdir()
    (native / "models/thermoMPNN_default.pt").write_text("INERT; NOT MODEL WEIGHTS")
    # Native fixtures deliberately include a nonzero exit after writing a CSV.
    # Success values test the parent's existing first-row/second-column cutoff.
    (analysis / "custom_inference.py").write_text(textwrap.dedent('''
        import argparse
        from pathlib import Path
        p = argparse.ArgumentParser()
        for key in ('pdb', 'model_path', 'out_dir'):
            p.add_argument('--' + key, required=True)
        a = p.parse_args()
        key = Path(a.pdb).stem
        print('INERT TRANSPORT FIXTURE: ' + key)
        if key == 'absent':
            raise SystemExit(0)
        value = {'low': '-1.25', 'high': '2.5', 'edge': '0', 'failed': '-9.0'}[key]
        name = 'ThermoMPNN_inference_' + Path(a.pdb).name.rstrip('.pdb') + '.csv'
        (Path(a.out_dir) / name).write_text('mutation,ddG\\nfixture,' + value + '\\n')
        if key == 'failed':
            raise SystemExit(7)
    '''))
    (analysis / "stale.csv").write_text("mutation,ddG\nstale,0\n")
    if all_missing:
        (analysis / "custom_inference.py").unlink()

    module = (ROOT / "modules/thermompnn.nf").read_text()
    (tmp_path / "thermompnn.nf").write_text(module.replace("/opt/ThermoMPNN", str(native)))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "stage_reporter.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(tmp_path / 'reports')!r}).mkdir(exist_ok=True)\n"
        f"pathlib.Path({str(tmp_path / 'reports')!r}, pathlib.Path(sys.argv[4]).name + '.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    candidates = ["low", "high", "edge", "absent", "failed"]
    source_bytes = {key: f"REMARK INERT PRIMARY {key}; no coordinates\n".encode() for key in candidates}
    for key, content in source_bytes.items():
        (inputs / f"{key}.pdb").write_bytes(content)

    parent = (ROOT / "workflows/antibody_denovo.nf").read_text()
    branch = parent.split("        pdb_designs_for_boltz = pdb_designs\n", 1)[1]
    branch = "        pdb_designs_for_boltz = pdb_designs\n" + branch.split("        if (params.run_af2_backprop == true)", 1)[0]
    assert "thermompnn_with_pdb" in branch
    # Observe the actual join before filtering, including its null padding.
    branch = branch.replace(
        "            if (params.thermompnn_max_ddg != null) {",
        '''            thermompnn_with_pdb.map { meta, pdb, csv ->
                groovy.json.JsonOutput.toJson([id: meta.id, pdb: pdb.toString(), csv: csv?.toString()])
            }.collectFile(name: 'joined.jsonl', newLine: true, storeDir: params.out_dir)
            if (params.thermompnn_max_ddg != null) {''',
    )
    harness = '''
        nextflow.enable.dsl=2
        include { THERMOMPNN } from './thermompnn'
        process PublishPrimary {
            publishDir "${params.out_dir}/primary", mode: 'copy'
            input:
            tuple val(meta), path(pdbs)
            output:
            path 'retained/*'
            script:
            """
            mkdir retained
            cp ${pdbs} retained/
            """
        }
        process PublishScore {
            publishDir "${params.out_dir}/scores", mode: 'copy'
            input:
            tuple val(meta), path(csv)
            output:
            path 'native/*'
            script:
            """
            mkdir native
            cp ${csv} native/
            """
        }
        workflow {
            pdb_designs = Channel.of(tuple([id: 'original_batch'], file(params.inputs + '/*.pdb')))
    ''' + branch + '''
            PublishPrimary(pdb_designs_for_boltz)
            PublishScore(stability_scores_early)
        }
    '''
    (tmp_path / "main.nf").write_text(textwrap.dedent(harness))
    out = tmp_path / "published"
    settings = dict(container_dir=str(tmp_path), weights_root=str(tmp_path / "weights"),
                    code_root=str(tmp_path), job_id="inert-parent", run_thermompnn=True,
                    thermompnn_max_ddg=cutoff, inputs=str(inputs), out_dir=str(out))
    (tmp_path / "params.json").write_text(json.dumps(settings))
    (tmp_path / "nextflow.config").write_text("process.executor = 'local'\nprocess.cpus = 1\nexecutor.queueSize = 4\ndocker.enabled = false\napptainer.enabled = false\nsingularity.enabled = false\n")
    nxf_home = tmp_path / "nxf-home"
    distribution = nxf_home / "framework" / VERSION
    distribution.mkdir(parents=True)
    (distribution / jar.name).symlink_to(jar)
    env = {**os.environ, "NXF_HOME": str(nxf_home), "NXF_VER": VERSION,
           "NXF_OFFLINE": "true", "NXF_DISABLE_CHECK_LATEST": "true", "NXF_PLUGINS_DEFAULT": "",
           "NXF_ANSI_LOG": "false"}
    run = subprocess.run([str(launcher), "run", "main.nf", "-params-file", "params.json",
                          "-with-trace", "trace.tsv", "-work-dir", "work"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
    (tmp_path / "run.stdout").write_text(run.stdout)
    (tmp_path / "run.stderr").write_text(run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr
    assert VERSION in run.stdout
    with (tmp_path / "trace.tsv").open() as handle:
        tasks = list(csv.DictReader(handle, delimiter="\t"))
    assert all(task["status"] == "COMPLETED" and task["exit"] == "0" for task in tasks)
    assert {task["name"] for task in tasks if task["name"].startswith("THERMOMPNN")} == {
        f"THERMOMPNN ({key})" for key in candidates
    }
    joined = [json.loads(line) for line in (out / "joined.jsonl").read_text().splitlines()]
    assert len(joined) == len(candidates)
    by_id = {row["id"]: row for row in joined}
    assert set(by_id) == set(candidates)
    scored = set() if all_missing else {"low", "high", "edge"}
    for key, row in by_id.items():
        assert Path(row["pdb"]).resolve() == (inputs / f"{key}.pdb").resolve()
        assert (row["csv"] is not None) == (key in scored)
    expected = set(candidates) - ({"high"} if cutoff is not None and not all_missing else set())
    published = {p.stem: p.read_bytes() for p in (out / "primary/retained").glob("*.pdb")}
    assert published == {key: source_bytes[key] for key in expected}
    metrics = {p.name: p.read_text() for p in (out / "scores/native").glob("*.csv")}
    assert set(metrics) == {f"{key}_stability.csv" for key in scored}
    for key, value in {"low": "-1.25", "high": "2.5", "edge": "0"}.items():
        if key in scored:
            assert metrics[f"{key}_stability.csv"] == f"mutation,ddG\nfixture,{value}\n"
    reports = list((tmp_path / "reports").glob("*.json"))
    assert {p.name for p in reports} == {f"{key}_stability.csv.json" for key in scored}
    # The process itself completes even when the native command fails/has no CSV.
    logs = list((tmp_path / "work").glob("*/*/thermompnn.log"))
    assert len(logs) == len(candidates)
    assert all("INERT TRANSPORT FIXTURE" in p.read_text() or "not found" in p.read_text() for p in logs)
    if not all_missing:
        assert any("exit status 7" in p.read_text() for p in logs)
        assert any("produced no inference output" in p.read_text() for p in logs)
    assert all((inputs / f"{key}.pdb").read_bytes() == content for key, content in source_bytes.items())
