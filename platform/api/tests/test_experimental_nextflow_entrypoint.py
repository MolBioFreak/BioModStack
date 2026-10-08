from __future__ import annotations

from pathlib import Path

import re
import sys

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import build_nextflow_command


EXPERIMENTAL_WORKFLOW_ENTRYPOINTS = {
    "protein_local_redesign": ("local_redesign", "workflows/protein_local_redesign.nf", "PROTEIN_LOCAL_REDESIGN"),
    "protein_cad_experimental": ("design", "workflows/protein_cad_experimental.nf", "PROTEIN_CAD_EXPERIMENTAL"),


    "boltz_cp_experimental": ("design", "workflows/boltz_cp_experimental.nf", "BOLTZ_CP_EXPERIMENTAL"),
    "confornets_experimental": ("design", "workflows/confornets_experimental.nf", "CONFORNETS_EXPERIMENTAL"),
}

MINIMAL_PARAMS = {
    "protein_local_redesign": {"input_pdb": "/tmp/input.pdb", "design_chains": "A", "redesign_ranges": "1-5"},
    "protein_cad_experimental": {},


    "boltz_cp_experimental": {"input_path": "/tmp/complex.yaml"},
    "confornets_experimental": {"sequence": "ACDEFGHIK"},
}


def test_no_aggregate_experimental_entrypoint_bucket_exists() -> None:
    assert not (REPO_ROOT / "experimental.nf").exists()


def test_experimental_tab_workflows_are_individually_direct_runnable() -> None:
    for workflow_id, (_mode, rel_path, symbol) in EXPERIMENTAL_WORKFLOW_ENTRYPOINTS.items():
        workflow_path = REPO_ROOT / rel_path
        assert workflow_path.exists(), workflow_id

        text = workflow_path.read_text(encoding="utf-8")
        assert re.search(rf"\bworkflow\s+{re.escape(symbol)}\s*\{{", text), workflow_id
        assert re.search(r"(?m)^\s*workflow\s*\{", text), f"{workflow_id} must expose its own unnamed entry workflow"
        assert f"{symbol}()" in text, workflow_id


def test_main_entrypoint_no_longer_dispatches_experimental_tab_workflows() -> None:
    main_text = (REPO_ROOT / "main.nf").read_text(encoding="utf-8")

    for workflow_id, (_mode, rel_path, symbol) in EXPERIMENTAL_WORKFLOW_ENTRYPOINTS.items():
        assert rel_path not in main_text
        assert f"./{rel_path}" not in main_text
        assert symbol not in main_text
        assert f"params.rfd_mode == '{workflow_id}'" not in main_text


def test_api_routes_experimental_tab_workflows_to_workflow_specific_entrypoints() -> None:
    for model_id, (mode, rel_path, _symbol) in EXPERIMENTAL_WORKFLOW_ENTRYPOINTS.items():
        cmd = build_nextflow_command(
            model_id,
            mode,
            dict(MINIMAL_PARAMS[model_id]),
            "/tmp/out",
            job_id=f"job-{model_id}",
        )

        assert cmd[1:4] == ["run", rel_path, "-profile"]
        assert "main.nf" not in cmd[:4]
        assert "experimental.nf" not in cmd[:4]


def test_api_resume_routes_experimental_tab_workflows_to_workflow_specific_entrypoint() -> None:
    cmd = build_nextflow_command(
        "protein_local_redesign",
        "local_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "redesign_ranges": "1-5",
            "resume_work_dir": "/tmp/nxf-work",
        },
        "/tmp/out",
        job_id="job-plr-resume",
    )

    assert cmd[1:4] == ["run", "workflows/protein_local_redesign.nf", "-profile"]
    assert "-resume" in cmd
    assert cmd[cmd.index("-w") + 1] == "/tmp/nxf-work"
