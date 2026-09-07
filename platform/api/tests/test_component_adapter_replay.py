"""Real adapter replay and publication fault injection, entirely offline."""
import importlib
from pathlib import Path
import shutil

import pytest

from component_runtime import canonical_bytes
from test_frustrampnn_parent_workflow_fanout import _load_client
from test_remote_frustrampnn_self_contained import prepared


@pytest.mark.parametrize("interrupt", [False, True])
def test_planner_exact_replay_and_interrupted_file_publication(tmp_path, monkeypatch, interrupt):
    planner = importlib.import_module("scripts.plan_frustrampnn_groups")
    inputs = [tmp_path / f"input-{i}" for i in range(3)]
    for i, directory in enumerate(inputs):
        prepared(directory, i)
    output = tmp_path / "groups"
    original = planner.durable_write
    writes = 0
    def interrupted_write(path, payload):
        nonlocal writes
        if path.parent.name == ".staging":
            writes += 1
            if writes == 2:
                # Simulate a process dying with an incomplete unpublished file.
                path.write_bytes(payload[:1])
                raise OSError("interrupted copy")
        original(path, payload)
    if interrupt:
        monkeypatch.setattr(planner, "durable_write", interrupted_write)
        with pytest.raises(OSError, match="interrupted copy"):
            planner.materialize_groups(inputs, output, attempt_id="attempt")
        monkeypatch.setattr(planner, "durable_write", original)
    plan = planner.materialize_groups(inputs, output, attempt_id="attempt")
    assert planner.materialize_groups(list(reversed(inputs)), output, attempt_id="attempt") == plan
    # Physical relocation of input directories must not change authority.
    relocated = tmp_path / "relocated"
    shutil.copytree(inputs[0], relocated)
    assert planner.materialize_groups([relocated, *inputs[1:]], output, attempt_id="attempt") == plan
    for group in output.glob("group_*"):
        for member in group.iterdir():
            request = (member / planner.REQUEST).read_bytes()
            source = next(d for d in inputs if (d / planner.REQUEST).read_bytes() == request)
            assert planner.tree_authority(member) == planner.tree_authority(source)


@pytest.mark.parametrize("tamper", ["published", "source", "extra", "link", "plan"])
def test_planner_replay_rejects_altered_materialization(tmp_path, tamper):
    planner = importlib.import_module("scripts.plan_frustrampnn_groups")
    source = tmp_path / "input"
    prepared(source, 0)
    output = tmp_path / "groups"
    planner.materialize_groups([source], output, attempt_id="attempt")
    destination = output / "group_000000/candidate_000000"
    if tamper == "source":
        (source / "unexpected.txt").write_bytes(b"changed")
    elif tamper == "published":
        (destination / planner.REQUEST).write_bytes(b"truncated")
    elif tamper == "extra":
        (destination / "unexpected.txt").write_bytes(b"changed")
    elif tamper == "link":
        (destination / planner.REQUEST).unlink()
        (destination / planner.REQUEST).symlink_to(source / planner.REQUEST)
    else:
        (output / "grouping_plan_v1.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="conflict|unsafe"):
        planner.materialize_groups([source], output, attempt_id="attempt")


@pytest.mark.parametrize("change", ["origin", "type", "filename", "bytes", "settings", "placement"])
def test_local_request_replay_fences_semantics_before_post(tmp_path, monkeypatch, change):
    client = _load_client()
    directory = tmp_path / "candidate"
    directory.mkdir()
    (directory / "metadata.json").write_bytes(canonical_bytes(dict(
        candidate_id="candidate-0", parent_job_id="parent", parent_workflow_id="protein_design",
        producer_stage="protein_design:terminal", producer_candidate_key="terminal/a.pdb",
        requiredness="required")))
    (directory / "source.pdb").write_bytes(b"ATOM\nEND\n")
    args = dict(parent_job_id="parent", parent_workflow_id="protein_design",
                settings_json=canonical_bytes(dict(batching_enabled=True, structures_per_job=2)).decode(),
                candidate_dirs=[directory], output_receipt=tmp_path / "terminal.json",
                output_bundles=tmp_path / "bundles", capability="test-only")
    calls = []
    def post(*_args, **kwargs):
        calls.append(kwargs)
        raise OSError("recorded POST; no network")
    monkeypatch.setattr(client.requests, "post", post)
    with pytest.raises(OSError, match="recorded POST"):
        client.execute_parent_fanout(**args)
    if change == "origin":
        args["settings_value_origin"] = "operator_request"
    elif change in {"type", "filename"}:
        (directory / "source.pdb").rename(directory / ("source.cif" if change == "type" else "source.PDB"))
    elif change == "bytes":
        (directory / "source.pdb").write_bytes(b"changed")
    elif change == "settings":
        args["settings_json"] = canonical_bytes(dict(batching_enabled=True, structures_per_job=3)).decode()
    else:
        relocated = tmp_path / "relocated"
        shutil.copytree(directory, relocated)
        args.update(candidate_dirs=[relocated], capability="rotated-test-only", api_url="http://relocated-api")
    if change == "placement":
        with pytest.raises(OSError, match="recorded POST"):
            client.execute_parent_fanout(**args)
        assert len(calls) == 2
        assert calls[0]["data"] == calls[1]["data"]
        assert calls[0]["files"] == calls[1]["files"]
    else:
        with pytest.raises(ValueError, match="immutable grouping plan conflicts"):
            client.execute_parent_fanout(**args)
        assert len(calls) == 1
