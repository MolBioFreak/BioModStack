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
import json, os, pathlib, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
args=sys.argv[1:]
script=pathlib.Path(args[0]).name if args else ''
if script == 'stage_reporter.py':
    raise SystemExit(0)
if script == 'publish_frustrampnn_bundle.py':
    marker=pathlib.Path(args[args.index('--marker')+1])
    dest=args[args.index('--destination')+1]
    marker.write_text(json.dumps({{'manifest':dest+'/frustrampnn_result_manifest_v2.json','result':dest+'/workflow_component_result_v2.json','source':dest+'/source.pdb'}},sort_keys=True)+'\\n')
    raise SystemExit(0)
if script == 'run_frustrampnn_parent_fanout.py':
    parent_job_id=args[args.index('--parent-job-id')+1]
    parent_workflow_id=args[args.index('--parent-workflow-id')+1]
    if '--settings-json-file' in args:
        settings_json=pathlib.Path(args[args.index('--settings-json-file')+1]).read_text()
        forwarded=pathlib.Path('/run/forwarded-settings.json')
        forwarded.write_text(settings_json)
        args[args.index('--settings-json-file')+1]=str(forwarded)
    else:
        settings_json=args[args.index('--settings-json')+1]
    pathlib.Path('/run/settings-received.json').write_text(settings_json)
    candidate_dirs=[pathlib.Path(args[index+1]) for index, value in enumerate(args) if value == '--candidate-dir']
    candidates=[]
    for candidate_dir in candidate_dirs:
        metadata=json.loads((candidate_dir/'metadata.json').read_text())
        candidates.append(metadata['candidate_id'])
    candidates.sort(key=lambda candidate_id: next(
        json.loads((candidate_dir/'metadata.json').read_text())['producer_candidate_key']
        for candidate_dir in candidate_dirs
        if json.loads((candidate_dir/'metadata.json').read_text())['candidate_id'] == candidate_id
    ))
    children=[]
    output_roots={{}}
    for ordinal, candidate_id in enumerate(candidates):
        child_id=f'fake-child-{{ordinal}}'
        output_root=pathlib.Path.cwd()/'fake_scheduler_children'/child_id
        bundle=output_root/'frustrampnn'/'results'/candidate_id
        bundle.mkdir(parents=True)
        source=next(
            path for candidate_dir in candidate_dirs
            if json.loads((candidate_dir/'metadata.json').read_text())['candidate_id'] == candidate_id
            for path in candidate_dir.iterdir() if path.name.startswith('source.')
        )
        shutil.copyfile(source, bundle/source.name)
        (bundle/'workflow_component_result_v3.json').write_text(
            json.dumps({{'candidate_id':candidate_id,'job_id':child_id,'status':'succeeded'}},sort_keys=True)+'\\n'
        )
        children.append({{'job_id':child_id,'structure_count':1,'candidates':[{{'candidate_id':candidate_id}}]}})
        output_roots[child_id]=str(output_root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return
        def send_json(self, payload):
            encoded=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def do_POST(self):
            expected=f'/api/frustrampnn/jobs/{{parent_job_id}}/workflow-dataset/analyze'
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path != expected or parent_workflow_id.encode() not in body or any(value.encode() not in body for value in candidates):
                self.send_error(400)
                return
            self.send_json({{
                'schema_name':'bms.structure-dataset-fanout.v1',
                'fanout_id':'a'*64,
                'parent_job_id':parent_job_id,
                'selected_structure_count':len(candidates),
                'structures_per_job':1,
                'effective_structures_per_job':1,
                'replayed':False,
                'child_jobs':children,
            }})
        def do_GET(self):
            if self.path.startswith(f'/api/jobs/{{parent_job_id}}/children/status?'):
                self.send_json({{
                    'total':len(children),'completed':len(children),'failed':0,'cancelled':0,
                    'running':0,'pending':0,'all_done':True,
                    'child_ids':[child['job_id'] for child in children],
                    'children':[{{'job_id':child['job_id'],'status':'completed','output_dir':output_roots[child['job_id']]}} for child in children],
                }})
                return
            child_id=self.path.removeprefix('/api/frustrampnn/jobs/').removesuffix('/receipt')
            child=next((value for value in children if value['job_id'] == child_id),None)
            if child is None:
                self.send_error(404)
                return
            child_candidates=child['candidates']
            self.send_json({{
                'job_id':child_id,'status':'completed','parent_job_id':parent_job_id,
                'candidates':child_candidates,
                'results':[{{'candidate_id':value['candidate_id'],'status':'succeeded','manifest_sha256':'b'*64}} for value in child_candidates],
                'batch_manifest':{{'sha256':'c'*64}},'grouped_terminal_artifact':None,
            }})

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    os.environ['API_BASE_URL']=f'http://127.0.0.1:{{server.server_port}}'
    try:
        client_args=[*args,'--poll-interval','0','--timeout','5']
        completed=subprocess.run([{str(API_RUNTIME)!r}, *client_args], env=os.environ.copy(), timeout=30)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    raise SystemExit(completed.returncode)
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


def _run_full_path(
    root: Path, *, enabled: bool, settings_json_override: str | None = None
) -> dict[str, object]:
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
    if settings_json_override is not None:
        (run / "params.json").write_text(json.dumps({
            "frustrampnn_settings": settings_json_override,
            "frustrampnn_settings_value_origin": "bms_default",
        }), encoding="utf-8")
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
    if settings_json_override is not None:
        command.extend(["-params-file", "/run/params.json"])
    if enabled:
        settings_json = settings_json_override or canonical_json_bytes(
            default_settings().model_dump(
                mode="json",
                exclude_none=False,
                exclude={"settings_value_origin"},
            )
        ).decode("utf-8")
        if settings_json_override is None:
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
    staged_candidates = {
        json.loads(path.read_text(encoding="utf-8"))["candidate_id"]
        for path in (run / "work").rglob("metadata.json")
        if path.parent.name.startswith("candidate_")
    }
    child_bundles = {
        json.loads(path.read_text(encoding="utf-8"))["candidate_id"]
        for path in (run / "work").rglob("workflow_component_result_v3.json")
        if path.parent.parent.name == "frustrampnn_child_bundles"
    }
    trace = (run / "trace.txt").read_text(encoding="utf-8")
    return {
        "rows": rows,
        "staged_candidates": staged_candidates,
        "child_bundles": child_bundles,
        "canonical_tasks": len(re.findall(r"CanonicalFrustraMPNNV2Task", trace)),
        "scheduler_stage_tasks": len(re.findall(r"SchedulerFrustraMPNNParentFanout:StageFrustraMPNNParentCandidate", trace)),
        "scheduler_spawn_tasks": len(re.findall(r"SchedulerFrustraMPNNParentFanout:SpawnWaitFrustraMPNNParentChildren", trace)),
        "scheduler_report_tasks": len(re.findall(r"SchedulerFrustraMPNNParentFanout:ReportFrustraMPNNParentChildrenComplete", trace)),
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
        assert len(result["staged_candidates"]) == 2
        assert result["child_bundles"] == result["staged_candidates"]
        assert result["scheduler_stage_tasks"] == 2
        assert result["scheduler_spawn_tasks"] == 1
        assert result["scheduler_report_tasks"] == 1
        assert result["canonical_tasks"] == 0
    else:
        assert result["staged_candidates"] == set()
        assert result["child_bundles"] == set()
        assert result["scheduler_stage_tasks"] == 0
        assert result["scheduler_spawn_tasks"] == 0
        assert result["scheduler_report_tasks"] == 0
        assert result["canonical_tasks"] == 0


@pytest.mark.runtime_integration
def test_parent_fanout_transports_hostile_canonical_settings_as_data(tmp_path: Path) -> None:
    if not _runtime_ready():
        pytest.skip("pinned Nextflow image or managed API test runtime is unavailable")
    _prepare_full_path_runtime(tmp_path)
    hostile = default_settings().model_dump(
        mode="json", exclude_none=False, exclude={"settings_value_origin"},
    )
    hostile["protein_selection"] = {
        "mode": "selected_entities",
        "entities": [{
            "entity_instance_id": "entity'$(touch /run/settings-injected)' ; $HOME & | < >",
            "source_entity_id": None,
            "label_asym_id": None,
            "auth_asym_id": None,
        }],
        "regions": [],
        "residues": [],
    }
    settings_json = canonical_json_bytes(hostile).decode("utf-8")

    _run_full_path(tmp_path, enabled=True, settings_json_override=settings_json)

    assert not (tmp_path / "enabled" / "settings-injected").exists()
    assert (tmp_path / "enabled" / "settings-received.json").read_text() == settings_json
