"""CPU native-source tests. Inert model boundary is NOT pretrained accuracy proof."""
from __future__ import annotations

import ast
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from services.frustrampnn.structure import normalize_structure_bytes
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, resolve_effective_settings
from services.frustrampnn.runtime import compile_frustrampnn_command_plan
from services.frustrampnn.analysis import merge_raw_frustrampnn_shards
from test_frustrampnn_structure import _pdb_atom
from test_frustrampnn_predict_batch_adapter import _load_adapter, _manifest, _canonical_json


@pytest.fixture
def native():
    directory = os.environ.get("BMS_TEST_FRUSTRA_NATIVE_SOURCE")
    if not directory:
        pytest.skip("set BMS_TEST_FRUSTRA_NATIVE_SOURCE to read-only pinned extracted source")
    root = Path(directory)
    hashes = {
        "model_pdb_parsing.py": "fffbd35ea8fa5d99d2d85e909c1da2b4c40df90c9063f35a8db90f243fdc7200",
        "inference_predictor.py": "508c35b4f64fd5a39f370a0a04272a83fd0d2f0b5af012c00eabfafbf838452d",
        "constants.py": "f3586340a3f7b5adf556682e5c3f05952a1264e15aed82f14662fa7b974972b7",
    }
    for name, digest in hashes.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location("native_frustra_parser", root / "model_pdb_parsing.py")
    assert spec is not None and spec.loader is not None
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    constants = {}
    exec(compile((root / "constants.py").read_text(), "native_constants", "exec"), constants)
    tree = ast.parse((root / "inference_predictor.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FrustraMPNN")
    methods = {"predict", "predict_batch", "_predict_chain", "_generate_mutations", "_get_chains"}
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    # Execute the exact native control-flow methods, without importing torch,
    # constructing a model, loading a checkpoint, or exposing a GPU.
    namespace = {"Path": Path, "pd": pd, "ALPHABET": constants["ALPHABET"],
                 "Mutation": SimpleNamespace, "alt_parse_PDB": parser.alt_parse_PDB,
                 "torch": SimpleNamespace(no_grad=nullcontext),
                 "tqdm": lambda values, **kwargs: values,
                 "warnings": __import__("warnings")}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "native_predictor_methods", "exec"), namespace)
    return parser, namespace["FrustraMPNN"]


def normalize(tmp_path, residues):
    lines = []
    for chain, number, insertion, residue in residues:
        for atom in ("N", "CA", "C", "O"):
            lines.append(_pdb_atom(len(lines) + 1, atom, residue, chain, number, insertion=insertion))
    payload = ("".join(lines) + "END\n").encode()
    path = tmp_path / "normalized.pdb"
    mapping = normalize_structure_bytes(
        source_bytes=payload, input_path="source.pdb", output_pdb_path=path,
        map_path=tmp_path / "map.json", target_id="fixture", parent_job_id="fixture", candidate_id="fixture",
        identity_authority={"kind": "pdb_self_identity_v1", "identity_domain": "candidate_local",
                            "authority_artifact_sha256": hashlib.sha256(payload).hexdigest()},
        protein_selection={"mode": "all_protein_entities"}, selected_model=1,
        altloc_policy="blank_or_explicit:<blank>",
    )
    return mapping, path


@pytest.mark.parametrize("residues", [
    [("A", 10, "", "GLY"), ("A", 11, "", "ALA")],
    [("A", 10, "A", "GLY"), ("A", 12, "", "ALA")],
    [("A", 10, "", "GLY"), ("A", 10, "A", "ALA"), ("A", 11, "", "SER")],
    [("A", -2, "", "GLY"), ("A", -2, "B", "ALA"), ("A", 1, "", "ALA")],
    [("B", 15, "", "GLY"), ("A", 12, "B", "ALA"), ("A", 10, "", "ALA"), ("A", 12, "A", "SER")],
])
def test_positions_match_exact_native_parser(tmp_path, native, residues):
    parser, _ = native
    mapping, path = normalize(tmp_path, residues)
    for chain in {row["pdb_chain_id"] for row in mapping["rows"]}:
        seq = parser.alt_parse_PDB(str(path), [chain])[0]["seq"]
        rows = sorted((r for r in mapping["rows"] if r["pdb_chain_id"] == chain), key=lambda r: r["model_position"])
        assert [(r["model_position"], r["wt"]) for r in rows] == [(i, aa) for i, aa in enumerate(seq) if aa != "-"]
    resolve_effective_settings(FrustraMPNNRequestedSettings(), mapping)


@pytest.mark.parametrize("mode", ["selected_residues", "selected_entities", "selected_regions"])
def test_grouped_selection_executes_native_plan_with_full_backbone(tmp_path, native, mode):
    _, predictor_type = native
    mapping, path = normalize(tmp_path, [("A", 10, "", "GLY"), ("A", 12, "", "ALA"),
                                        ("B", 1, "", "SER"), ("B", 2, "", "ALA")])
    selected_rows = [mapping["rows"][1], mapping["rows"][2]]
    fields = ("entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id", "auth_seq_id", "insertion_code", "sequence_index")
    selection = {"mode": "selected_residues", "residues": [{k: r[k] for k in fields} for r in selected_rows]}
    if mode == "selected_entities":
        selection = {"mode": mode, "entities": [{k: selected_rows[0][k] for k in fields[:4]}]}
    elif mode == "selected_regions":
        # Native region schema is built from the same source-authoritative rows.
        # Explicit source selectors, not dense native residue assumptions.
        selection = {"mode": mode, "regions": [{"entity_instance_id": "pdb:A", "source_entity_id": None,
            "label_asym_id": None, "auth_asym_id": "A", "sequence_start": 2, "sequence_end": 2}]}
    settings = FrustraMPNNRequestedSettings.model_validate({"batching_enabled": True, "structures_per_job": 2, "protein_selection": selection})
    effective = resolve_effective_settings(settings, mapping)
    plan = compile_frustrampnn_command_plan(effective)
    model_calls = []
    predictor = predictor_type()
    def inert_model(pdb, mutations):
        mut = mutations[0]
        model_calls.append((pdb[0]["seq"], mut.position, mut.wildtype, mut.mutation))
        return [{"frustration": SimpleNamespace(item=lambda: 0.125)}], None
    predictor.model = inert_model
    loads = []
    def load(*args, **kwargs):
        loads.append((args, kwargs))
        return predictor
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    for record in manifest["records"]:
        Path(record["staged_pdb_path"]).write_bytes(path.read_bytes())
        record["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["command_plan"] = [{"chains": list(e.chains) if e.chains else None, "positions": list(e.positions) if e.positions else None} for e in plan.entries]
    manifest_path.write_bytes(_canonical_json(manifest))
    evidence = adapter.run_batch(manifest_path=manifest_path, output_dir=tmp_path / "output",
                                frustrampnn_module=SimpleNamespace(FrustraMPNN=SimpleNamespace(from_pretrained=load)))
    assert len(loads) == evidence["model_load_count"] == 1
    assert evidence["method_identity"] == "frustrampnn.FrustraMPNN.predict"
    for record in evidence["records"]:
        assert record["status"] == "succeeded"
        raw = (tmp_path / "output" / record["output_csv"]).read_bytes()
        merge_raw_frustrampnn_shards((raw,), effective)
        assert record["row_count"] == sum(len(chain.residues) for chain in effective.resolved_chains) * 20
    assert any(seq == "G-A" for seq, *_ in model_calls)
    assert len(model_calls) == sum(record["row_count"] for record in evidence["records"])


def test_current_selected_group_preparation_native_control_and_bundle_receiving(tmp_path, native):
    from scripts import run_frustrampnn_grouped_batch as grouped
    from scripts import run_frustrampnn_component as component
    from services.frustrampnn import runtime
    from services.frustrampnn.contracts import canonical_json_bytes
    from services.frustrampnn.persistence import load_and_validate_result_bundle
    from test_frustrampnn_component_phase3 import _v2_inputs

    request, source, structure_map, _ = _v2_inputs(
        tmp_path, residues=[("A", 10), ("A", 12), ("B", 1), ("B", 2)],
        selected=[("A", 2), ("B", 0)], request_generation=3,
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(canonical_json_bytes(request))
    candidate = grouped.PreparedCandidate({}, request_path, source, structure_map, source)
    selection = grouped._selected_command_plan(candidate)
    assert selection == {"command_plan": [{"chains": ["B"], "positions": [0]}, {"chains": ["A"], "positions": [2]}]}
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    for record in manifest["records"]:
        Path(record["staged_pdb_path"]).write_bytes(source.read_bytes())
        record["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        record.update(selection)
    manifest["records"][0].update(candidate_id=request["candidate_id"], invocation_id=request["invocation_id"])
    manifest_path.write_bytes(_canonical_json(manifest))
    _, predictor_type = native
    predictor = predictor_type()
    predictor.model = lambda *args: ([{"frustration": SimpleNamespace(item=lambda: 0.125)}], None)
    output = tmp_path / "native-control-output"
    evidence = adapter.run_batch(manifest_path=manifest_path, output_dir=output,
        frustrampnn_module=SimpleNamespace(FrustraMPNN=SimpleNamespace(from_pretrained=lambda *a, **k: predictor)))
    # Render hardened argv, but never execute a container or expose a GPU.
    invocation = runtime.build_frustrampnn_predict_batch_command(
        apptainer="apptainer", container="/proc/self/fd/99", manifest=manifest_path,
        output_root=output, adapter=Path(adapter.__file__), physical_gpu_id=0,
    )
    stdout, stderr = tmp_path / "stdout.log", tmp_path / "stderr.log"
    stdout.write_bytes(b"CPU native-control fixture; inert model boundary\n")
    stderr.write_bytes(b"")
    terminal = evidence["records"][0]
    bundle_path = component.finalize_batched_component(
        request_path=request_path, source_structure=source, structure_map=structure_map,
        raw_csv=output / terminal["output_csv"], terminal_evidence=terminal,
        batch_argv=invocation.argv, batch_argv_sha256=invocation.argv_sha256,
        stdout_log=stdout, stderr_log=stderr, output_dir=tmp_path / "bundle", physical_gpu_id=0,
    )
    result = json.loads((bundle_path / "workflow_component_result_v3.json").read_bytes())
    received = load_and_validate_result_bundle(bundle_path, expected_parent_job_id=request["parent_job_id"], terminal_envelope=result)
    assert received.contract_version == 3
    receipt = json.loads((bundle_path / "frustrampnn_execution_receipt_v3.json").read_bytes())
    assert receipt["execution_method"] == "predict_selected_batch"
    assert receipt["command_plan"]["entries"][0]["positions"] == [0]
    landscape = json.loads((bundle_path / "frustrampnn_landscape_v3.json").read_bytes())
    from services.conformational_mapping.state_landscape_analysis import _source_by_candidate
    assert _source_by_candidate([landscape], schema_key="cm_frustration_landscape_v1", label="landscape")[request["candidate_id"]] == landscape


@pytest.mark.parametrize("failure", ["source", "import", "load", "predict"])
def test_load_receipts_count_only_successful_loads(tmp_path, monkeypatch, failure):
    adapter = _load_adapter()
    manifest, path = _manifest(tmp_path, count=2)
    def load(*args, **kwargs):
        if failure == "load":
            raise RuntimeError("inert load failure")
        return SimpleNamespace(predict_batch=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("inert prediction failure")))
    if failure == "source":
        Path(manifest["records"][0]["staged_pdb_path"]).write_bytes(b"changed")
    module = SimpleNamespace(FrustraMPNN=SimpleNamespace(from_pretrained=load))
    if failure == "import":
        module = None
        monkeypatch.setattr(adapter.importlib, "import_module", lambda *a: (_ for _ in ()).throw(ImportError("inert import failure")))
    with pytest.raises(adapter.BatchAdapterError):
        adapter.run_batch(manifest_path=path, output_dir=tmp_path / "out", frustrampnn_module=module)
    receipt = json.loads((tmp_path / "out" / adapter.EVIDENCE_FILENAME).read_bytes())
    assert receipt["model_load_count"] == (1 if failure == "predict" else 0)
    assert all(record["status"] == "failed" for record in receipt["records"])
