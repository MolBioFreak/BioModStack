from __future__ import annotations

import csv
import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.settings import default_settings


REPO_ROOT = Path(__file__).resolve().parents[3]
NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.1"
API_RUNTIME = Path("/home/dalab/.biomodstack-dev/runtime/cm-api-python/current/venv/bin/python")
HOST_RUNTIME = Path("/home/dalab/.biomodstack-dev")


def _runtime_ready() -> bool:
    docker = shutil.which("docker")
    return bool(
        docker
        and API_RUNTIME.is_file()
        and subprocess.run(
            [docker, "image", "inspect", NEXTFLOW_IMAGE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepare_full_path_runtime(root: Path) -> None:
    for directory in ("scripts", "bin", "fakepy"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "fakepy" / "yaml.py").write_text(
        "def dump(value, *args, **kwargs):\n    return 'version: 1\\nsequences: []\\n'\n",
        encoding="utf-8",
    )
    _write_executable(
        root / "bin" / "boltz",
        """#!/bin/sh
set -eu
mkdir -p boltz_results_yamls/predictions/submission
cat > boltz_results_yamls/predictions/submission/model_0.pdb <<'EOF'
ATOM      1  N   GLY A   1      11.000  12.000  13.000  1.00 20.00           N
ATOM      2  CA  GLY A   1      12.000  12.000  13.000  1.00 20.00           C
ATOM      3  C   GLY A   1      13.000  12.000  13.000  1.00 20.00           C
ATOM      4  O   GLY A   1      14.000  12.000  13.000  1.00 20.00           O
END
EOF
""",
    )
    _write_executable(root / "bin" / "python", "#!/bin/sh\nexec /usr/bin/python3 \"$@\"\n")
    _write_executable(
        root / "bin" / "api-python",
        f"""#!/usr/bin/python3
import json, os, pathlib, sys
args=sys.argv[1:]
script=pathlib.Path(args[0]).name if args else ''
if script == 'stage_reporter.py':
    raise SystemExit(0)
if script == 'publish_frustrampnn_bundle.py':
    marker=pathlib.Path(args[args.index('--marker')+1])
    dest=args[args.index('--destination')+1]
    marker.write_text(json.dumps({{'manifest':dest+'/frustrampnn_result_manifest_v2.json','result':dest+'/workflow_component_result_v2.json','source':dest+'/source.pdb'}},sort_keys=True)+'\\n')
    raise SystemExit(0)
os.execv({str(API_RUNTIME)!r}, [{str(API_RUNTIME)!r}, *args])
""",
    )
    _write_executable(
        root / "scripts" / "analyse_best_designs.py",
        """#!/usr/bin/python3
import json, pathlib, sys
args=sys.argv[1:]
out=pathlib.Path(args[args.index('--output')+1])
pdbs=sorted(pathlib.Path('.').glob('*.pdb'))
if len(pdbs) != 1: raise SystemExit(f'expected one staged PDB, got {pdbs}')
out.write_text(json.dumps({'description':pdbs[0].stem,'pr_plddt':'87.5','pr_helices':'1'},sort_keys=True)+'\\n')
""",
    )
    _write_executable(
        root / "scripts" / "metadata_converter.py",
        """#!/usr/bin/python3
import csv, json, pathlib, sys
args=sys.argv[1:]
inputs=[]
pos=args.index('--input_files')+1
while pos < len(args) and not args[pos].startswith('--'):
    inputs.append(pathlib.Path(args[pos])); pos+=1
output=pathlib.Path(args[args.index('--output_file')+1])
rows=[]
for path in inputs:
    for line in path.read_text().splitlines():
        if line.strip(): rows.append(json.loads(line))
fields=[]
for row in rows:
    for key in row:
        if key not in fields: fields.append(key)
with output.open('w',newline='') as handle:
    writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
""",
    )
    _write_executable(
        root / "scripts" / "filter_best_designs.py",
        """#!/usr/bin/python3
import pathlib, shutil, sys
args=sys.argv[1:]
csv_in=pathlib.Path(args[args.index('--csv')+1])
out_csv=pathlib.Path(args[args.index('--output-csv')+1])
out_dir=pathlib.Path(args[args.index('--output-dir')+1]); out_dir.mkdir()
shutil.copyfile(csv_in,out_csv)
for p in pathlib.Path('.').glob('*.pdb'): shutil.copyfile(p,out_dir/p.name)
""",
    )
    _write_executable(
        root / "scripts" / "generate_success_metrics.py",
        """#!/usr/bin/python3
import pathlib, sys
args=sys.argv[1:]
pathlib.Path(args[args.index('--output')+1]).write_text('{}\\n')
""",
    )


def _run_full_path(root: Path, *, enabled: bool) -> dict[str, object]:
    run = root / ("enabled" if enabled else "disabled")
    for directory in ("out", "work", "launch"):
        (run / directory).mkdir(parents=True)
    entries = [
        {"id": "sample-A", "name": "sample-A", "sequence": "ACDEFGHIK"},
        {"id": "sample-B", "name": "sample-B", "sequence": "ACDEFGHIK"},
    ]
    (run / "batch.json").write_text(json.dumps(entries), encoding="utf-8")
    (run / "extra.config").write_text(
        """process.executor='local'
docker.enabled=false
singularity.enabled=false
params.api_python='/probe/bin/api-python'
params.frustrampnn_physical_gpu_id=0
params.container_dir='/probe'
params.zip_pdbs=false
env.PATH='/probe/bin:' + System.getenv('PATH')
env.PYTHONPATH='/probe/fakepy'
""",
        encoding="utf-8",
    )
    command = [
            "docker", "run", "--rm", "--network", "none",
            "-e", "NXF_OFFLINE=true", "-e", "NXF_DISABLE_CHECK_LATEST=true",
            "-v", f"{REPO_ROOT}:/workspace:ro", "-v", f"{root}:/probe:rw",
            "-v", f"{HOST_RUNTIME}:{HOST_RUNTIME}:ro", "-v", f"{run}:/run:rw",
            "-v", f"{root / 'scripts'}:/scripts:ro", "-w", "/run/launch",
            NEXTFLOW_IMAGE, "nextflow", "run", "/workspace/workflows/protein_design.nf",
            "-c", "/workspace/nextflow.config", "-c", "/run/extra.config",
            "-stub-run", "-offline", "-w", "/run/work", "-with-trace", "/run/trace.txt",
            "--code_root", "/workspace", "--out_dir", "/run/out",
            "--job_id", f"phase5c-{'enabled' if enabled else 'disabled'}",
            "--pred_method", "boltz", "--boltz_use_msa", "false",
            "--sequence_batch_json_path", "/run/batch.json",
            "--run_frustrampnn", str(enabled).lower(),
        ]
    if enabled:
        settings_json = canonical_json_bytes(
            default_settings().model_dump(
                mode="json",
                exclude_none=False,
                exclude={"settings_value_origin"},
            )
        ).decode("utf-8")
        command.extend(
            [
                "--frustrampnn_settings",
                settings_json,
                "--frustrampnn_settings_value_origin",
                "bms_default",
            ]
        )
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    csv_path = run / "out" / "results" / "all_designs.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    requests = {
        json.loads(path.read_text(encoding="utf-8"))["candidate_id"]
        for path in (run / "work").rglob("workflow_component_request_v3.json")
    }
    trace = (run / "trace.txt").read_text(encoding="utf-8")
    return {
        "rows": rows,
        "requests": requests,
        "canonical_tasks": len(re.findall(r"CanonicalFrustraMPNNV2Task", trace)),
    }


@pytest.mark.runtime_integration
@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
def test_full_path_equal_byte_boltz_candidates_survive_bind_project_and_publish(
    tmp_path: Path, enabled: bool
) -> None:
    if not _runtime_ready():
        pytest.skip("pinned Nextflow image or managed API test runtime is unavailable")
    _prepare_full_path_runtime(tmp_path)

    result = _run_full_path(tmp_path, enabled=enabled)

    rows = result["rows"]
    assert len(rows) == 2
    assert {row["producer_sample"] for row in rows} == {"sample-A", "sample-B"}
    assert {row["producer_rank"] for row in rows} == {"0"}
    assert len({row["producer_artifact_sha256"] for row in rows}) == 1
    if enabled:
        assert len(result["requests"]) == 2
        assert result["canonical_tasks"] == 2
    else:
        assert result["requests"] == set()
        assert result["canonical_tasks"] == 0
