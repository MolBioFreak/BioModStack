from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

from scripts.shape_blueprint.tests.test_batch_planning import _request


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "shape_blueprint" / "build_rfd3_aggregate.py"
PLANNER_PATH = ROOT / "scripts" / "shape_blueprint" / "plan_rfd3_batches.py"


def _module():
    assert MODULE_PATH.is_file(), "RFD3 aggregate builder is absent"
    spec = importlib.util.spec_from_file_location("build_rfd3_aggregate", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _planner():
    assert PLANNER_PATH.is_file()
    spec = importlib.util.spec_from_file_location("plan_rfd3_batches_for_aggregate", PLANNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RFD3AggregateTests(unittest.TestCase):
    def test_all_candidate_outcomes_are_accounted_and_ranked_globally(self) -> None:
        module = _module()
        planner = _planner()
        request = _request(count=100)
        plan = planner.plan_batches(request, gpu_memory_gib=32)
        records = []
        for index, candidate in enumerate(candidate for batch in plan["batches"] for candidate in batch["candidates"]):
            accepted = index % 5 != 0
            records.append(
                {
                    "schema": "bms_rfd3_initial_admission_v1",
                    "candidate_id": candidate["candidate_id"],
                    "status": "accepted" if accepted else "rejected",
                    "candidate_sha256": f"{index:064x}",
                    "metrics": {
                        "cad_bidirectional_mean_angstrom": float(100 - index),
                        "chainbreak_count": 0 if accepted else 1,
                    },
                    "reason": None if accepted else {"code": "chainbreaks_present"},
                }
            )
        aggregate = module.build_aggregate(plan=plan, admission_records=records)
        self.assertEqual(aggregate["status"], "complete")
        self.assertEqual(aggregate["candidate_count_total"], 100)
        self.assertEqual(aggregate["accounting"]["accepted_count"], 80)
        self.assertEqual(aggregate["accounting"]["rejected_count"], 20)
        self.assertEqual(aggregate["accounting"]["missing_count"], 0)
        self.assertEqual(len(aggregate["ranked_candidates"]), 80)
        self.assertEqual(aggregate["ranked_candidates"][0]["candidate_id"], records[99]["candidate_id"])
        self.assertEqual(len(aggregate["aggregate_sha256"]), 64)
        self.assertEqual(aggregate, module.build_aggregate(plan=plan, admission_records=records))

    def test_missing_candidate_is_not_silently_treated_as_rejected(self) -> None:
        module = _module()
        planner = _planner()
        request = _request(count=1, mode="fixed", minimum=350, maximum=350)
        plan = planner.plan_batches(request, gpu_memory_gib=32)
        aggregate = module.build_aggregate(plan=plan, admission_records=[])
        self.assertEqual(aggregate["status"], "incomplete")
        self.assertEqual(aggregate["accounting"]["missing_count"], 1)
        self.assertEqual(aggregate["accounting"]["rejected_count"], 0)
        self.assertEqual(aggregate["ranked_candidates"], [])

    def test_zero_accepted_is_explicit_no_yield(self) -> None:
        module = _module()
        planner = _planner()
        request = _request(count=1, mode="fixed", minimum=350, maximum=350)
        plan = planner.plan_batches(request, gpu_memory_gib=32)
        candidate = plan["batches"][0]["candidates"][0]
        aggregate = module.build_aggregate(
            plan=plan,
            admission_records=[
                {
                    "candidate_id": candidate["candidate_id"],
                    "status": "rejected",
                    "candidate_sha256": "a" * 64,
                    "metrics": {"chainbreak_count": 3},
                    "reason": {"code": "chainbreaks_present"},
                }
            ],
        )
        self.assertEqual(aggregate["status"], "no_yield")
        self.assertEqual(aggregate["accounting"]["accepted_count"], 0)
        self.assertEqual(aggregate["accounting"]["rejected_count"], 1)


if __name__ == "__main__":
    unittest.main()
