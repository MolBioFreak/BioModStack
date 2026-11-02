"""A checkpoint continuation writes into its own generation directory, never the parent's saved out_dir."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path


def test_continuation_out_dir_is_the_generation_directory_not_the_saved_parent_out_dir(tmp_path, monkeypatch):
    from component_runtime import ComponentRuntime, NativeInvocation
    from services import nextflow
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate

    root = tmp_path / "results"
    root.mkdir()
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "one.pdb").write_text("native fixture")
    identity = {"revision": "a" * 40, "tree": "b" * 40}
    # The parent's saved native settings include the out_dir the first generation was launched with.
    parent = dict(id="root", model_id="antibody_design", mode="denovo",
                  params={"out_dir": str(root), "job_id": "root"}, output_dir=str(root))
    context = dict(attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease",
                   source_identity=identity, artifact_root=str(root), parent=parent)
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=root, attempt_id="attempt",
                               root_job_id="root", target_id="worker", lease_id="lease", source_identity=identity)
    checkpoint = open_component_gate(runtime, job_id="root", stage="post_fampnn", payload={},
                                     directories={"candidate": candidates})
    selected = [r["relative_path"] for r in checkpoint["checkpoint"]["artifacts"] if r["relative_path"].endswith(".pdb")]
    seen = []

    def native_compiler(model, mode, params, output, **kwargs):
        seen.append((dict(params), output))
        # Faithful to the real compiler: a params out_dir is emitted a second time and overrides the argument.
        command = ["nextflow", "run", "x.nf", "--out_dir", str(output)]
        native = {**params, "out_dir": str(output)}
        if "out_dir" in params:
            command += ["--out_dir", str(params["out_dir"])]
            native["out_dir"] = params["out_dir"]
        return replace(NativeInvocation.capture(model_id=model, mode=mode, command=command, requested=params,
                                                effective=params, native_parameters=native, entrypoint="fixture.nf"),
                       source_identity=kwargs["source_identity"])

    monkeypatch.setattr(nextflow, "compile_nextflow_invocation", native_compiler)
    invocation = nextflow.compile_component_checkpoint_continuation(context, checkpoint, {"selected_artifacts": selected})
    params, output = seen[0]
    assert "out_dir" not in params
    assert Path(output).is_relative_to(root / ".bms-review" / "selections") and Path(output).name == "continuation"
    assert invocation.native_parameters["out_dir"] == output
    assert invocation.command.count("--out_dir") == 1
    snapshot = nextflow.component_checkpoint_parent_snapshot(invocation, context)
    assert snapshot["output_dir"] == output and Path(output).resolve() != root.resolve()
    assert json.loads(json.dumps(context["parent"]["params"]))["out_dir"] == str(root)  # the saved context is untouched
