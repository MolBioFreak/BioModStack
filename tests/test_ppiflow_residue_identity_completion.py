"""Lossless mask transport; inert Rosetta fixture, AST-only pinned PPIFlow."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PIN = "000ce45a4411e7b97c1523a22c0f1bece7ede5fc"
SOURCE_HASH = "c38343f08d5f0546da0f1c8df8c21fe816f878a3535ed80ea2fa8ba416865fcc"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


regions = load("identify_anchors")
anchors = load("anchors_to_ppiflow_positions")


@pytest.fixture(scope="module")
def native():
    path = os.environ.get("BMS_TEST_PPIFLOW_PARTIAL_SOURCE")
    if not path:
        pytest.skip("Set BMS_TEST_PPIFLOW_PARTIAL_SOURCE to pinned partial-flow source")
    source = Path(path)
    if source.is_dir():
        source /= "sample_antibody_nanobody_partial.py"
    data = source.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SOURCE_HASH, f"Expected PPIFlow {PIN}"
    tree = ast.parse(data)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in {"expand_ranges", "get_indices_from_spec"}]
    assert len(nodes) == 2
    namespace = {"re": re, "List": list, "PDB": SimpleNamespace(is_aa=lambda *a, **k: True)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def selected(native, identities, spec):
    class Chain(list):
        pass
    chains = []
    for chain in dict.fromkeys(row[0] for row in identities):
        rows = Chain(SimpleNamespace(get_id=lambda n=n, i=i: (" ", n, i or " "))
                     for c, n, i in identities if c == chain)
        rows.id = chain
        chains.append(rows)
    ordered = [row for c in chains for row in identities if row[0] == c.id]
    structure = SimpleNamespace(get_chains=lambda: iter(chains))
    indices = native["get_indices_from_spec"](structure, native["expand_ranges"](spec))
    return {ordered[index] for index in indices}


def pdb_file(path, identities):
    path.write_text("".join(
        f"ATOM  {index:5d}  CA  ALA {chain}{number:4d}{icode or ' '}   "
        f"{0.:8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n"
        for index, (chain, number, icode) in enumerate(identities, 1)) + "END\n")
    return path


@pytest.mark.parametrize("chain", ["H", "h", "1"])
def test_full_enumeration_and_native_negative_controls(tmp_path, native, chain):
    identities = [(chain, n, i) for n, i in [(100, ""), (101, ""), (100, "A"), (100, "B")]]
    pdb = pdb_file(tmp_path / "seed.pdb", identities)
    spec, _, _ = regions.build_ppiflow_region_spec(pdb, [chain], "all_antibody")
    assert selected(native, identities, spec) == set(identities)
    if chain == "1":
        assert "-" not in spec
        assert selected(native, identities, "1100-101") == set()
    else:
        assert f"{chain}100-101" in spec
    assert selected(native, identities, f"{chain}100") == {(chain, 100, "")}
    assert selected(native, identities, f"{chain}100A") == {(chain, 100, "A")}
    assert selected(native, identities, f"{chain}100C") == set()
    fixed = anchors.build_positions([dict(chain=c, resnum=n, icode=i) for c, n, i in identities])
    assert selected(native, identities, fixed) == set(identities)


@pytest.mark.parametrize("chain", ["h", "1"])
def test_explicit_insertions_framework_subtraction_and_precedence(tmp_path, native, chain):
    identities = [(chain, 100, i) for i in ["", "A", "B"]] + [(chain, 101, "")]
    pdb = pdb_file(tmp_path / "seed.pdb", identities)
    primary = tmp_path / "primary.json"
    manual = tmp_path / "manual.json"
    primary.write_text(json.dumps({"H3": [f"{chain}100A"], "H2": [101]}))
    manual.write_text(json.dumps([{"id": "H3", "residues": [f"{chain}100B"]}]))
    kwargs = dict(cdr_positions_by_loop_path=str(primary), manual_cdr_definitions_path=str(manual))
    movable, cdr, _ = regions.build_ppiflow_region_spec(pdb, [chain], "selected_cdrs", ["H3"], **kwargs)
    assert selected(native, identities, movable) == {(chain, 100, "A")}
    assert selected(native, identities, cdr) == {(chain, 100, "A"), (chain, 101, "")}
    framework, _, _ = regions.build_ppiflow_region_spec(pdb, [chain], "framework_only", **kwargs)
    assert selected(native, identities, framework) == {(chain, 100, ""), (chain, 100, "B")}
    manual_only, _, _ = regions.build_ppiflow_region_spec(
        pdb, [chain], "selected_cdrs", ["H3"], manual_cdr_definitions_path=str(manual))
    assert selected(native, identities, manual_only) == {(chain, 100, "B")}


def test_integer_defaults_and_explicit_author_chain_are_not_broadened(tmp_path, native):
    identities = [("h", n, i) for n, i in [(27, ""), (28, ""), (27, "A"), (105, "A")]]
    pdb = pdb_file(tmp_path / "seed.pdb", identities)
    spec, _, _ = regions.build_ppiflow_region_spec(pdb, ["h"], "all_cdrs")
    assert spec == "h27-28,h105"
    assert selected(native, identities, spec) == {("h", 27, ""), ("h", 28, "")}
    mapping = regions.normalize_loop_residue_map({"h3": [100, "101", "h100A", "l100B"]})
    assert mapping == {"H3": [100, 101, "h100A", "l100B"]}
    assert regions.loop_map_to_chain_map(mapping, ["h", "l"]) == {
        "h": {(100, ""), (101, ""), (100, "A")}, "l": {(100, "B")}}
    assert regions.build_default_cdr_positions(["H"]) == "H27-38,H56-65,H105-117"
    assert "-" not in regions.build_default_cdr_positions(["1"])


# No model/Rosetta import or scientific execution: real CLIs see this inert API.
ROSETTA_FIXTURE = r'''
from pathlib import Path
from types import SimpleNamespace as NS
class Point:
    x = y = z = 0.
    def distance(self, other): return 0.
class Residue:
    def name1(self): return 'A'
    def name3(self): return 'ALA'
    def nbr_atom_xyz(self): return Point()
    def natoms(self): return 1
    def atom_name(self, i): return 'CA'
    def xyz(self, i): return Point()
class Pose:
    def __init__(self, path=None):
        self.path = path
        self.rows = [(s[21], int(s[22:26]), s[26].strip()) for s in Path(path).read_text().splitlines() if s.startswith('ATOM')] if path else []
    def assign(self, other): self.rows, self.path = other.rows[:], other.path
    def total_residue(self): return len(self.rows)
    def pdb_info(self): return self
    def chain(self, i): return self.rows[i-1][0]
    def number(self, i): return self.rows[i-1][1]
    def icode(self, i): return self.rows[i-1][2]
    def residue(self, i): return Residue()
    def energies(self): return self
    def residue_total_energy(self, i): return -6.
    def dump_pdb(self, path): Path(path).write_bytes(Path(self.path).read_bytes())
class Energy:
    def dot(self, weights): return -6.
class Score:
    def __call__(self, pose): pass
    def weights(self): return None
    def eval_ci_2b(self, *args): pass
    def eval_cd_2b(self, *args): pass
class MoveMap:
    def set_bb(self, *args): pass
    def set_chi(self, *args): pass
class Relax:
    def __init__(self, *args): pass
    def set_movemap(self, *args): pass
    def apply(self, *args): pass
rosetta = NS(core=NS(pose=NS(Pose=Pose), scoring=NS(EMapVector=Energy), kinematics=NS(MoveMap=MoveMap)), protocols=NS(relax=NS(FastRelax=Relax)))
def init(*args): pass
def pose_from_pdb(path): return Pose(path)
def get_fa_scorefxn(): return Score()
'''


def run_script(name, args, tmp_path, check=True):
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)],
                            env=env, cwd=tmp_path, text=True, capture_output=True)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("chain,repack", [("H", False), ("h", False), ("1", False), ("1", True)])
def test_actual_script_transport_fixed_movable_and_existing_refusals(tmp_path, native, chain, repack):
    (tmp_path / "pyrosetta.py").write_text(ROSETTA_FIXTURE)
    identities = [(chain, 100, i) for i in ["", "A", "B"]] + [(chain, 101, ""), ("T", 10, "")]
    pdb = pdb_file(tmp_path / "seed.pdb", identities)
    loops = tmp_path / "loops.json"
    loops.write_text(json.dumps({"H3": [f"{chain}100A"], "H2": [101]}))
    common = ["--pdb", pdb, "--antibody_chains", chain, "--antigen_chains", "T",
              "--selected_loops", "H3", "--cdr_positions_by_loop_json", loops,
              "--output_anchors", "anchors.json", "--output_score", "score.json",
              "--output_positions", "movable.txt", "--output_cdr_positions", "cdr.txt"]
    run_script("identify_anchors.py", common, tmp_path)
    assert (tmp_path / "movable.txt").read_text().strip() == f"{chain}100A"
    assert json.loads((tmp_path / "anchors.json").read_text())["energy_threshold"] == -5.0
    extra = ["--output_enriched_pdb", "enriched.pdb", "--output_rotamer_enrichment", "enrichment.json",
             "--output_cdr_positions_by_loop", "resolved.json", "--require_anchors"]
    if repack:
        extra += ["--rotamer_enrichment"]
    run_script("prepare_ppiflow_maturation.py", common + extra, tmp_path)
    payload = json.loads((tmp_path / "anchors.json").read_text())
    assert {row["pdb_position"] for row in payload["anchors"]} == {f"{chain}100", f"{chain}100B", f"{chain}101"}
    assert [row["pdb_position"] for row in payload["movable_anchor_candidates"]] == [f"{chain}100A"]
    assert json.loads((tmp_path / "resolved.json").read_text()) == {"H3": [f"{chain}100A"], "H2": [101]}
    score = json.loads((tmp_path / "score.json").read_text())
    assert (score["energy_threshold"], score["distance_cutoff"]) == (-5.0, 12.0)
    enrichment = json.loads((tmp_path / "enrichment.json").read_text())
    assert enrichment["rotamer_enrichment_enabled"] is repack
    assert enrichment["backbone_movement_allowed"] is False
    assert enrichment["repack_shell_distance"] == 20.0
    assert (tmp_path / "enriched.pdb").read_bytes() == pdb.read_bytes()
    run_script("anchors_to_ppiflow_positions.py", ["--anchors_json", "anchors.json", "--output", "fixed.txt"], tmp_path)
    fixed = (tmp_path / "fixed.txt").read_text().strip()
    movable = (tmp_path / "movable.txt").read_text().strip()
    assert selected(native, identities, fixed) == {(chain, 100, ""), (chain, 100, "B"), (chain, 101, "")}
    assert selected(native, identities, movable) == {(chain, 100, "A")}
    run_script("validate_ppiflow_masks.py", ["--fixed_positions", fixed, "--movable_positions", movable], tmp_path)
    overlap = run_script("validate_ppiflow_masks.py", ["--fixed_positions", movable, "--movable_positions", movable], tmp_path, check=False)
    assert overlap.returncode != 0
    assert "overlap" in overlap.stderr
    # Existing threshold/anchor refusal is neither removed nor broadened.
    result = run_script("prepare_ppiflow_maturation.py", common + extra + ["--energy_threshold", "-7"], tmp_path, check=False)
    assert result.returncode != 0
    assert "No antibody anchor residues passed" in result.stderr
