"""Run with native source on PYTHONPATH, from the native checkout.

The native offline guard is imported before the real compiler. Input is the
BIOXP_AUTHORING_DIFFERENTIAL_EXPORT produced by the API suite, not a fixture
claiming native output. Nothing is submitted or executed.
"""
import tests.z_stop_offline_guard  # noqa: F401
import json
import sys
from pathlib import Path
from bioxp.manual_pipetting import compile_manual_pipetting, ManualPipettingRequest
from bioxp.protocols.models import ProtocolDocument
from bioxp.protocols.validators import validate_protocol_document

from bioxp.oem_compat.pathing import LOCATION_ID_TO_NAME

asset = json.loads((Path(__file__).resolve().parents[1] / "schemas/bioxp_workflow_native.json").read_text())
assert asset["request"] == ManualPipettingRequest.model_json_schema()
assert asset["locations"] == {str(k): v for k, v in LOCATION_ID_TO_NAME.items()}
cases = json.loads(Path(sys.argv[1]).read_text())
for index, case in enumerate(cases):
    if case["expected"] is None:
        try:
            compile_manual_pipetting(ManualPipettingRequest.model_validate(case["request"]))
        except ValueError:
            continue
        raise AssertionError(f"Native unexpectedly accepted invalid case {index}")
    actual = compile_manual_pipetting(ManualPipettingRequest.model_validate(case["request"])).to_payload()
    assert actual == case["expected"], f"native compiler mismatch in case {index}"
    parsed = validate_protocol_document(ProtocolDocument.from_payload(case["expected"])).to_payload()
    assert parsed == actual, f"native document parser mismatch in case {index}"
print(json.dumps({"cases": len(cases), "actions": sum(len(c["expected"]["stages"][0]["actions"]) for c in cases if c["expected"] is not None),
                  "native_compile_and_document_parse": "exact-match", "hardware_calls": 0}))
