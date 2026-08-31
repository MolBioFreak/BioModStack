from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from contract import ContractError, canonical_json, validate_request  # noqa: E402
from build_result_manifest import build_result_manifest  # noqa: E402


def request(*, num_designs: int = 2, dump_trajectories: bool = False) -> dict:
    return {
        "schema": "bms.rfd3.generation.request.v1",
        "request_id": "req-generation-001",
        "job_id": "job-generation-001",
        "generation": {"min_length": 2, "max_length": 3, "num_designs": num_designs},
        "execution": {"seed": 17, "dump_trajectories": dump_trajectories},
    }


def write_cif(path: Path, *, z_offset: float = 0.0) -> None:
    text = f"""data_candidate
_struct_conf.conf_type_id HELX_P
_struct_conf.beg_label_asym_id A
_struct_conf.end_label_asym_id A
_struct_sheet_range.beg_label_asym_id A
_struct_sheet_range.end_label_asym_id A
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
ATOM 1 C CA ALA A 1 0.0 0.0 {z_offset}
ATOM 2 C CA GLY A 2 2.0 0.0 {z_offset}
#
"""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GenerationProducerTests(unittest.TestCase):
    def test_request_is_closed_and_bounded(self) -> None:
        self.assertEqual(validate_request(request())["generation"]["max_length"], 3)

        bad = request()
        bad["extra"] = True
        with self.assertRaisesRegex(ContractError, "exact fields"):
            validate_request(bad)

        bad = request()
        bad["generation"]["min_length"] = 4
        with self.assertRaisesRegex(ContractError, "min_length"):
            validate_request(bad)

    def test_prepare_cli_emits_exact_native_input_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            request_path = tmp_path / "request.json"
            native_path = tmp_path / "native.json"
            receipt_path = tmp_path / "receipt.json"
            request_path.write_text(json.dumps(request()), encoding="utf-8")

            subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "prepare_native_input.py"), "--request", str(request_path),
                 "--expected-min-length", "2", "--expected-max-length", "3",
                 "--expected-num-designs", "2", "--expected-seed", "17",
                 "--expected-dump-trajectories", "false",
                 "--output-native", str(native_path), "--output-receipt", str(receipt_path)],
                check=True,
            )

            native = {"generation_0": {"dialect": 2, "contig": "2-3"}}
            self.assertEqual(json.loads(native_path.read_text()), native)
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["schema"], "bms.rfd3.generation.preparation-receipt.v1")
            self.assertEqual(receipt["request_id"], "req-generation-001")
            self.assertEqual(receipt["request_sha256"], hashlib.sha256(canonical_json(request()).encode()).hexdigest())
            self.assertEqual(receipt["native_input_sha256"], hashlib.sha256(canonical_json(native).encode()).hexdigest())

    def test_prepare_cli_rejects_param_request_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            request_path = tmp_path / "request.json"
            request_path.write_text(json.dumps(request()), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "prepare_native_input.py"), "--request", str(request_path),
                 "--expected-min-length", "9", "--expected-max-length", "3",
                 "--expected-num-designs", "2", "--expected-seed", "17",
                 "--expected-dump-trajectories", "false",
                 "--output-native", str(tmp_path / "native.json"),
                 "--output-receipt", str(tmp_path / "receipt.json")],
                text=True, capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("does not match canonical request", completed.stderr)

    def test_protein_design_routes_generation_request_through_rfd3_only(self) -> None:
        repository = SCRIPT_DIR.parents[1]
        workflow = (repository / "workflows" / "protein_design.nf").read_text(encoding="utf-8")
        module = (repository / "modules" / "rfd3.nf").read_text(encoding="utf-8")
        self.assertIn("params.rfd3_generation_request_path", workflow)
        self.assertIn("PrepareGeneralRFD3Input", workflow)
        self.assertIn("BuildGeneralRFD3ResultManifest", workflow)
        self.assertIn("General RFD3 generation requires run_rfd_only=true", workflow)
        self.assertIn("params.rfd3_generation_num_designs", module)
        self.assertIn("params.rfd3_generation_seed", module)
        self.assertIn("params.rfd3_generation_dump_trajectories", module)

    def test_manifest_hash_binds_candidates_metrics_and_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            request_path = tmp_path / "request.json"
            request_path.write_text(json.dumps(request()), encoding="utf-8")
            structures, metadata = [], []
            for index in range(2):
                candidate_id = f"generation_0_model_{index}"
                cif = tmp_path / f"{candidate_id}.cif.gz"
                sidecar = tmp_path / f"{candidate_id}.json"
                write_cif(cif, z_offset=float(index))
                sidecar.write_text(json.dumps({"candidate": candidate_id}), encoding="utf-8")
                structures.append(cif)
                metadata.append(sidecar)

            manifest = build_result_manifest(
                request_path=request_path, structure_paths=structures, metadata_paths=metadata,
                accepted_candidate_ids={"generation_0_model_0"}, storage_prefix="run/rfd3",
            )

            self.assertEqual(manifest["schema"], "bms.rfd3.generation.result-manifest.v1")
            self.assertEqual(manifest["job_id"], "job-generation-001")
            self.assertEqual(manifest["request_id"], "req-generation-001")
            self.assertEqual(manifest["request_sha256"], hashlib.sha256(canonical_json(request()).encode()).hexdigest())
            self.assertEqual(manifest["aggregate"], {
                "requested": 2, "generated": 2, "accepted": 1,
                "length": {"min": 2, "mean": 2.0, "max": 2},
                "radius_of_gyration": {"min": 1.0, "mean": 1.0, "max": 1.0},
            })
            candidate = manifest["candidates"][0]
            self.assertEqual(candidate["metrics"], {
                "residue_count": 2, "chain_count": 1, "radius_of_gyration": 1.0,
                "helix_count": 1, "strand_count": 1,
            })
            self.assertEqual(candidate["artifacts"][0]["sha256"], sha256(structures[0]))
            self.assertEqual(candidate["artifact_manifest_sha256"], hashlib.sha256(
                canonical_json(candidate["artifacts"]).encode()).hexdigest())

    def test_manifest_rejects_nonexact_candidate_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            request_path = tmp_path / "request.json"
            request_path.write_text(json.dumps(request(num_designs=1)), encoding="utf-8")
            cif = tmp_path / "generation_0_model_0.cif.gz"
            write_cif(cif)
            with self.assertRaisesRegex(ContractError, "metadata must match"):
                build_result_manifest(
                    request_path=request_path, structure_paths=[cif], metadata_paths=[],
                    accepted_candidate_ids=set(), storage_prefix="run/rfd3",
                )


if __name__ == "__main__":
    unittest.main()
