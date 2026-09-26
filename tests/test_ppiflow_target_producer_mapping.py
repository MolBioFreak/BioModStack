"""Exercise pinned native parser/dataset/writer with inert records, not sampling.

BMS_TEST_PPIFLOW_SOURCE points to a source export containing data/ and analysis/.
No model imports, GPU work, external pickle loading, or inferred correspondence.
"""
import ast
import collections
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import types

import numpy as np
import pytest
from Bio import PDB

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("ppiflow_mapping_runner", ROOT / "scripts/run_ppiflow_generation.py")
api = load("ppiflow_mapping_authority", ROOT / "platform/api/services/ppiflow_generation.py")


class Tensor(np.ndarray):
    @property
    def device(self):
        return "cpu"

    def long(self):
        return self.astype(int)

    int = long

    def double(self):
        return self.astype(float)

    def cpu(self):
        return self

    def numpy(self):
        return np.asarray(self)

    def unsqueeze(self, axis):
        return np.expand_dims(self, axis)

    def repeat(self, *reps):
        return np.tile(np.asarray(self), reps).view(Tensor)


def tensor(value, dtype=None):
    return np.array(value, dtype=dtype).view(Tensor)


torch = types.SimpleNamespace(
    tensor=tensor, int=int, int64=int, isnan=np.isnan,
    ones=lambda *shape, dtype=float: np.ones(shape, dtype=dtype).view(Tensor),
    zeros=lambda *shape, dtype=float: np.zeros(shape, dtype=dtype).view(Tensor),
    eye=lambda n: np.eye(n).view(Tensor),
)


def definitions(path, names, namespace, kind=None):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    source = ast.unparse(ast.Module(body=nodes, type_ignores=[]))
    code = runner.instrument(source, str(path), kind) if kind else compile(source, str(path), "exec")
    exec(code, namespace)
    return namespace


@pytest.fixture
def native():
    value = os.environ.get("BMS_TEST_PPIFLOW_SOURCE")
    if not value:
        pytest.skip("Set BMS_TEST_PPIFLOW_SOURCE to the full pinned source export")
    root = Path(value)
    assert (root / "analysis/utils.py").is_file()
    return root


@pytest.fixture
def native_io(native):
    # Execute the actual source's residue alphabet/constants without its tree import.
    constant_names = {"restypes", "restype_1to3", "restype_3to1", "restype_order", "restype_num",
                      "atom_types", "atom_order", "atom_type_num"}
    constants = {}
    tree = ast.parse((native / "data/residue_constants.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constant_names for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "native-constants", "exec"), constants)
    rc = types.SimpleNamespace(**{k: constants[k] for k in constant_names})
    protein = types.SimpleNamespace(Protein=lambda **kw: types.SimpleNamespace(**kw))
    ns = dict(np=np, os=os, re=re, protein=protein, Protein=protein.Protein,
              residue_constants=rc, PDB_CHAIN_IDS="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
              PDB_MAX_CHAINS=62, Chain=PDB.Chain.Chain, _bms_export=runner)
    definitions(native / "data/protein.py", {"to_pdb", "_chain_end"}, ns, "pdb_writer")
    protein.to_pdb = ns["to_pdb"]
    definitions(native / "analysis/utils.py", {"swap_index", "create_full_prot", "write_prot_to_pdb"}, ns, "writer")
    definitions(native / "data/parsers.py", {"process_chain"}, ns)
    return ns


def target_pdb():
    # Gapped author numbering, insertion code, and an unmodeled residue cropped
    # by the native modeled_idx selection. Coordinates are inert test records.
    rows = [(7, " ", "ALA"), (19, "A", "GLY"), (20, " ", "UNK"), (31, " ", "SER")]
    return "".join(f"ATOM  {i:5d}  CA  {aa:3s} R{number:4d}{icode}   {float(i):8.3f}{2.:8.3f}{3.:8.3f}  1.00  0.00           C\n"
                   for i, (number, icode, aa) in enumerate(rows, 1)) + "END\n"


def prepare_features(native, native_io):
    ns = native_io
    structure = PDB.PDBParser(QUIET=True).get_structure("independent", io.StringIO(target_pdb()))
    # Actual native parser followed by actual native extract_features annotation.
    # dataclasses.asdict stand-in only adapts our inert Protein record.
    du_ns = dict(np=np, residue_constants=ns["residue_constants"], collections=collections,
                 List=list, Dict=dict)
    definitions(native / "data/utils.py", {"parse_chain_feats", "concat_np_features"}, du_ns)
    du = types.SimpleNamespace(**du_ns, chain_str_to_int=lambda c: ns["PDB_CHAIN_IDS"].index(c),
                              INT_TO_CHAIN=dict(enumerate(ns["PDB_CHAIN_IDS"])))
    extract_ns = dict(np=np, du=du, parsers=types.SimpleNamespace(process_chain=ns["process_chain"]),
                      dataclasses=types.SimpleNamespace(asdict=lambda x: vars(x).copy()), _bms_export=runner)
    definitions(native / "sample_antibody_nanobody.py", {"extract_features"}, extract_ns, "entrypoint")
    target = extract_ns["extract_features"](structure, {"R": structure[0]["R"]})[0]
    binder = {k: v[:1].copy() for k, v in target.items()}
    binder["chain_index"][:] = 2
    binder["bms_source_chain"][:] = ord("C")
    binder["residue_index"][:] = 1
    features = du.concat_np_features([target, binder], False)
    features.update(modeled_idx=np.array([0, 1, 3, 4]), target_interface_residues=[], binder_interface_residues=[])
    return features, du


def native_dataset_output(native, native_io, features, du, *, csv_only=False, antibody=False):
    if csv_only:
        # Ordinary existing feature files have no new author-identity annotation.
        features.pop("bms_source_icode")
        features.pop("bms_source_chain")
    du.read_pkl = lambda path: copy.deepcopy(features)  # inert records, never unpickle
    rigid = types.SimpleNamespace(get_rots=lambda: types.SimpleNamespace(get_rot_mats=lambda: tensor(np.zeros((4, 3, 3)))),
                                  get_trans=lambda: tensor(np.zeros((4, 3))))
    class RigidArray:
        def __getitem__(self, key):
            return rigid
    native_numpy = types.SimpleNamespace(**{name: getattr(np, name) for name in dir(np)})
    native_numpy.nonzero = lambda value: np.argwhere(value) if isinstance(value, Tensor) else np.nonzero(value)
    ns = dict(np=native_numpy, torch=torch, du=du, pd=types.SimpleNamespace(isnull=lambda v: v is None),
              tree=types.SimpleNamespace(map_structure=lambda fn, d: {k: fn(v) for k, v in d.items()}),
              data_transforms=types.SimpleNamespace(atom37_to_frames=lambda f: {**f, "rigidgroups_gt_frames": None}),
              rigid_utils=types.SimpleNamespace(Rigid=types.SimpleNamespace(from_tensor_4x4=lambda x: RigidArray())),
              _bms_export=runner)
    row = dict(processed_path="inert-records-only", num_chains=2, binder_id="C", pdb_name="opaque-key", original_index=0)
    if antibody:
        features["chain_groups"] = np.array([0, 0, 0, 0, 1])
        features["cdr_mask"] = np.array([0, 0, 0, 0, 1])
        row["hotspots"] = []
        tree = ast.parse((native / "data/datasets_antibody.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AntibodyTestDataset")
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {"process_csv_row", "__getitem__"}]
        exec(runner.instrument(ast.unparse(cls), "native-antibody-dataset", "antibody_dataset"), ns)
        dataset = ns["AntibodyTestDataset"]()
        dataset.all_sample_ids = [(row, 0)]
        dataset.setup_antibody_mask = lambda f: tensor(f["chain_group_idx"] != 0)
        dataset.setup_target_hotspots = lambda f: tensor(np.zeros(len(f["chain_idx"])))
        dataset.post_process_feats = lambda f: f
        return dataset.__getitem__(0)
    definitions(native / "data/datasets.py", {"_process_csv_row"}, ns, "dataset")
    processed = ns["_process_csv_row"](row)
    # Execute native __getitem__, retaining its target extraction and composition.
    tree = ast.parse((native / "data/datasets.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PpiTestDataset")
    cls.bases = []
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__getitem__"]
    exec(runner.instrument(ast.unparse(cls), "native-dataset-getitem", "dataset"), ns)
    dataset = ns["PpiTestDataset"]()
    dataset.all_sample_ids = [(row, 0, 2)]
    dataset._dataset_cfg = types.SimpleNamespace(sample_original_binder_len=False)
    dataset.process_csv_row = lambda row: {**processed, "pdb_name": "opaque-key", "original_index": "0"}
    dataset.setup_binder_mask = lambda f: tensor(f["chain_idx"] != 0)
    dataset.setup_target_hotspots = lambda f: tensor(np.zeros(len(f["chain_idx"])))
    dataset.post_process_feats = lambda f: f  # geometry centering is not tested
    return dataset.__getitem__(0)


@pytest.mark.parametrize("csv_only", [False, True])
def test_actual_parser_crop_dataset_writer_and_projection(tmp_path, native, native_io, csv_only):
    features, du = prepare_features(native, native_io)
    batch_row = native_dataset_output(native, native_io, features, du, csv_only=csv_only)
    target = tmp_path / "independent-target.pdb"
    target.write_text(target_pdb())
    if csv_only:
        feature_file = tmp_path / "features.pkl"
        feature_file.write_bytes(b"inert feature binding; never unpickled")
        csv_path = tmp_path / "input.csv"
        csv_path.write_text("pdb_name,processed_path\nopaque-key,features.pkl\n")
        params = {"input_csv": str(csv_path)}
    else:
        params = {"target_pdb": str(target), "target_chain": "R", "binder_chain": "C"}
    prepared = api.materialize_ppiflow_generation_request("protein_binder", params, tmp_path / "request",
                                                         source_identity={"target": {"target_state": "state-A"}})
    context, _ = runner.prepare_invocation(prepared["ppiflow_generation_request"], tmp_path / "result", native)
    runner._CONTEXT = context
    path = tmp_path / "result/opaque-key_0.pdb"
    batch = {k: tensor([v]) if isinstance(v, (np.ndarray, int)) else [v] for k, v in batch_row.items()}
    positions = np.ones((5, 37, 3))
    # Execute the native producer method as well as its actual writer. Only the
    # sampler and scientific metrics are replaced with explicit inert fixtures.
    producer_ns = dict(Any=object, torch=torch, os=os, np=np, _bms_export=runner,
        au=types.SimpleNamespace(write_prot_to_pdb=native_io["write_prot_to_pdb"]),
        residue_constants=native_io["residue_constants"],
        metrics=types.SimpleNamespace(calc_mdtraj_metrics=lambda p: {},
            calc_ca_ca_metrics=lambda p: {}, calc_other_metrics=lambda p: {}))
    tree = ast.parse((native / "models/flow_module_binder.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FlowModule")
    cls.bases = []
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "test_step"]
    exec(runner.instrument(ast.unparse(cls), "native-producer", "producer"), producer_ns)
    producer = producer_ns["FlowModule"]()
    producer.model = None
    producer._data_cfg = types.SimpleNamespace(task="binder")
    producer._exp_cfg = types.SimpleNamespace(testing_model=types.SimpleNamespace(save_dir=str(path.parent)))
    producer.interpolant = types.SimpleNamespace(set_device=lambda d: None,
        sample=lambda *a, **kw: ([tensor([positions])], None))
    producer.test_epoch_metrics = []
    producer.test_step(batch, 0)
    original_writer = native_io.copy()
    definitions(native / "analysis/utils.py", {"swap_index", "create_full_prot", "write_prot_to_pdb"}, original_writer)
    original_path = tmp_path / "result/uninstrumented.pdb"
    original_writer["write_prot_to_pdb"](positions, str(original_path),
        chain_index=batch_row["chain_idx"], aatype=tensor([0, 7, 15, 0, 0]), binder=True, no_indexing=True,
        b_factors=batch_row["hotspot_mask"] + batch_row["target_interface_mask"])
    assert original_path.read_bytes() == path.read_bytes()
    record = json.loads((tmp_path / "result/samples.jsonl").read_text())
    job = types.SimpleNamespace(id="job", retry_count=0, lineage_root_job_id=None, mode="protein_binder")
    fields = api._generation_design_fields(job, record, types.SimpleNamespace(id="artifact", storage_path=str(path)))
    assert hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]
    assert fields['provenance']['binder_chains'] == ['A']
    assert fields['provenance']['target_chains'] == ['B']
    assert fields["provenance"]["source_identity"]["target"]["target_state"] == "state-A"
    if csv_only:
        contract = record["independent_target"]
        assert contract["document"]["chains"][0]["sequence"] == "AGS"
        assert contract["document"]["chains"][0]["native_residues"][1]["insertion_code"] is None
        assert contract["document"]["source_feature_sha256"] == hashlib.sha256(feature_file.read_bytes()).hexdigest()
        assert contract["sha256"] == hashlib.sha256((json.dumps(contract["document"], sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
        assert "target_residue_mapping" not in record  # no invented author insertion codes
        assert fields["provenance"]["independent_target"] == contract
    else:
        mapping = record["target_residue_mapping"]
        assert mapping["source_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert [(r["source"]["auth_seq_id"], r["source"]["insertion_code"]) for r in mapping["residues"]] == [(7, ""), (19, "A"), (31, "")]
        assert [r["output"] for r in mapping["residues"]] == [
            {"chain_id": "B", "auth_seq_id": i, "insertion_code": ""} for i in (1, 2, 3)]
        emitted = PDB.PDBParser(QUIET=True).get_structure("output", path)
        assert [r.id[1] for r in emitted[0]["B"]] == [1, 2, 3]
        assert fields["provenance"]["target_residue_mapping"] == mapping
    historical = {k: v for k, v in record.items() if k not in {"target_residue_mapping", "independent_target"}}
    previous = api._generation_design_fields(job, historical, types.SimpleNamespace(id="artifact", storage_path=str(path)))
    assert "target_residue_mapping" not in previous["provenance"]
    assert "independent_target" not in previous["provenance"]


@pytest.mark.parametrize("zero_target_atom", [False, True])
def test_antibody_native_target_arrays_and_writer(tmp_path, native, native_io, zero_target_atom):
    features, du = prepare_features(native, native_io)
    row = native_dataset_output(native, native_io, features, du, antibody=True)
    target = tmp_path / "target.pdb"
    target.write_text(target_pdb())
    framework = tmp_path / "independent-framework.pdb"
    framework.write_text("ATOM      1  CA  ALA C   1       1.000   2.000   3.000  1.00  0.00           C\nEND\n")
    request = api.materialize_ppiflow_generation_request("nanobody_binder", {
        "target_pdb": str(target), "framework_pdb": str(framework),
        "antigen_chain": "R", "heavy_chain": "C", "specified_hotspots": "R7",
    }, tmp_path / "request")
    context, _ = runner.prepare_invocation(request["ppiflow_generation_request"], tmp_path / "out", native)
    runner._CONTEXT = context
    runner.record_preprocessed({"pdb_name": "opaque-key", "id": "native-id"}, 0)
    positions = np.ones((4, 37, 3))
    if zero_target_atom:
        positions[1] = 0
    batch = {"bms_target_residues": [row["bms_target_residues"]], "pdb_name": ["opaque-key"]}
    path = tmp_path / "out/native.pdb"
    runner.write_sample(native_io["write_prot_to_pdb"], batch, 0, positions, str(path),
                        chain_index=row["chain_idx"], aatype=tensor([0, 7, 15, 0]), no_indexing=True)
    runner.record_sample(dict(batch=batch, i=0, attempt=0, pdb_path=str(path), test_metric={}))
    record = json.loads((tmp_path / "out/samples.jsonl").read_text())
    mapping = record["target_residue_mapping"]
    assert mapping["source_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert [r["output"] for r in mapping["residues"]] == [
        {"chain_id": "R", "auth_seq_id": i, "insertion_code": ""}
        for i in ([1, 3] if zero_target_atom else [1, 2, 3])]
    assert [r["source"]["auth_seq_id"] for r in mapping["residues"]] == ([7, 31] if zero_target_atom else [7, 19, 31])


def test_optional_absent_writer_evidence_is_not_a_generation_gate():
    assert runner.target_metadata({"target_writes": {}}, 0, Path("unused")) == {}
    with pytest.warns(RuntimeWarning, match="unavailable"):
        runner.capture_target({})
