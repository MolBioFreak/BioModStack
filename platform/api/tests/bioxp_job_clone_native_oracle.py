"""Native clone differential oracle: compiler/parser only, guarded inert transport.

Run from the pinned native checkout with PYTHONPATH=.:src, passing the
BIOXP_CLONE_DIFFERENTIAL_EXPORT produced by the receiving tests.
"""
import tests.z_stop_offline_guard  # noqa: F401 -- before native imports
from copy import deepcopy
import json
from pathlib import Path
import sys

from bioxp.manual_pipetting import ManualPipettingRequest, compile_manual_pipetting
from bioxp.protocols.models import ProtocolDocument
from bioxp.protocols.validators import validate_protocol_document


def behavior(document):
    value = deepcopy(document)
    del value["protocol_id"]
    for stage in value["stages"]:
        for action in stage["actions"]:
            del action["action_id"]
            del action["metadata"]["manual_step"]
    return value


cases = json.loads(Path(sys.argv[1]).read_text())
for index, case in enumerate(cases):
    source = compile_manual_pipetting(ManualPipettingRequest.model_validate(case["original_request"])).to_payload()
    assert source == case["source"], f"source compiler mismatch: {index}"
    projected = compile_manual_pipetting(ManualPipettingRequest.model_validate(case["projected_request"])).to_payload()
    assert projected == case["recomposed"], f"clone compiler mismatch: {index}"
    assert validate_protocol_document(ProtocolDocument.from_payload(projected)).to_payload() == projected
    assert behavior(source) == behavior(projected), f"clone changed native effects: {index}"
print(json.dumps({"cases": len(cases), "actions": sum(len(c["source"]["stages"][0]["actions"]) for c in cases),
                  "native_source_clone_recomposition": "exact-behavior-match", "hardware_calls": 0}))
