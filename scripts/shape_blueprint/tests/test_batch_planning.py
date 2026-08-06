from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "shape_blueprint" / "plan_rfd3_batches.py"


def _module():
    assert MODULE_PATH.is_file(), "RFD3 batch planner is absent"
    spec = importlib.util.spec_from_file_location("plan_rfd3_batches", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(*, count: int, mode: str = "uniform_integer_range", minimum: int = 350, maximum: int = 450) -> dict:
    request = {
        "schema": "bms_shape_design_request_v2",
        "request_id": "shape_test",
        "geometry_sha256": "a" * 64,
        "point_pool_sha256": "b" * 64,
        "sdf_sha256": "c" * 64,
        "length_policy": {
            "mode": mode,
            "min": minimum,
            "max": maximum,
            "allocation_policy_id": "balanced_bucket_v1" if mode != "fixed" else "fixed_length_v1",
        },
        "num_backbones": count,
        "seed": 1234,
        "generator": "rfd3",
    }
    request["request_sha256"] = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return request


class RFD3BatchPlanningTests(unittest.TestCase):
    def test_public_range_is_deterministic_homogeneous_and_fully_accounted(self) -> None:
        module = _module()
        request = _request(count=100)
        first = module.plan_batches(request, gpu_memory_gib=32)
        second = module.plan_batches(request, gpu_memory_gib=32)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "planned")
        self.assertEqual(first["candidate_count_total"], 100)
        self.assertEqual(sum(batch["candidate_count"] for batch in first["batches"]), 100)
        self.assertTrue(all(batch["candidate_count"] <= 32 for batch in first["batches"]))
        self.assertTrue(all(len({candidate["length"] for candidate in batch["candidates"]}) == 1 for batch in first["batches"]))
        lengths = [candidate["length"] for batch in first["batches"] for candidate in batch["candidates"]]
        self.assertEqual(sorted(set(lengths)), [350, 361, 372, 383, 394, 406, 417, 428, 439, 450])
        self.assertEqual({lengths.count(length) for length in set(lengths)}, {10})
        ids = [candidate["candidate_id"] for batch in first["batches"] for candidate in batch["candidates"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids[0]), 64)
        self.assertEqual(first["allocation_policy_id"], "balanced_bucket_v1")
        self.assertEqual(len(first["batch_plan_sha256"]), 64)
        children = module.child_requests(request, first)
        self.assertEqual(len(children), len(first["batches"]))
        self.assertTrue(all(child["parent_request_sha256"] == request["request_sha256"] for child in children))
        self.assertTrue(all(child["length_policy"]["allocation_policy_id"] == "fixed_length_v1" for child in children))
        self.assertTrue(all(child["request_sha256"] == module._sha256({k: v for k, v in child.items() if k != "request_sha256"}) for child in children))

    def test_paper_count_and_fixed_length_are_chunked_at_32(self) -> None:
        module = _module()
        plan = module.plan_batches(_request(count=200, mode="fixed", minimum=350, maximum=350), gpu_memory_gib=32)
        self.assertEqual([batch["candidate_count"] for batch in plan["batches"]], [32, 32, 32, 32, 32, 32, 8])
        self.assertTrue(all(batch["length"] == 350 for batch in plan["batches"]))

    def test_known_16_gb_350_residue_placement_is_rejected(self) -> None:
        module = _module()
        with self.assertRaisesRegex(module.ResourceAdmissionError, "16.*350|unsupported"):
            module.plan_batches(_request(count=1, mode="fixed", minimum=350, maximum=350), gpu_memory_gib=16)

    def test_32_gb_350_residue_placement_is_admitted(self) -> None:
        module = _module()
        plan = module.plan_batches(_request(count=1, mode="fixed", minimum=350, maximum=350), gpu_memory_gib=32)
        self.assertEqual(plan["resource_admission"]["status"], "admitted")
        self.assertEqual(plan["batches"][0]["candidates"][0]["effective_seed"], 1234)


if __name__ == "__main__":
    unittest.main()
