"""Offline real-robot contract check for mounted Axios exports.

Run with the candidate robot's src on PYTHONPATH. Imports no API/server/device;
validates native documents and compiles each positioning/physical finite plan.
"""
import json
import sys
from pathlib import Path

from bioxp.manual_pipetting import (
    ManualPipettingRequest,
    compile_manual_pipetting,
    manual_physical_plan,
    manual_position_plan,
)
from bioxp.protocols.models import ProtocolDocument
from bioxp.protocols.validators import validate_protocol_document


def validate(path):
    exports = json.loads(Path(path).read_text())["requests"]
    assert exports, "No mounted requests exported"
    actions_count = 0
    plans_count = 0
    operations = set()
    diagnostics = set()
    selections = set()
    for exported in exports:
        document = exported["request"]["document"]
        parsed = validate_protocol_document(ProtocolDocument.from_payload(document))
        steps = []
        for stage_index, stage in enumerate(parsed.stages):
            for action_index, action in enumerate(stage.actions):
                actions_count += 1
                kind = action.kind.value
                params = document["stages"][stage_index]["actions"][action_index]["params"]
                if kind == "pipette_manual_physical":
                    assert manual_physical_plan(params)
                    plans_count += 1
                    steps.append(params)
                    operations.add(params["operation"])
                    if params["operation"] == "diagnostic_pipette":
                        diagnostics.add(params["diagnostic"]["action"])
                    if params["operation"] == "source_load_tips":
                        selections.add(params["pipette"])
                elif kind == "pipette_position":
                    assert manual_position_plan(params)
                    plans_count += 1
                    steps.append(params)
                elif kind in {"pipette_aspirate", "pipette_dispense"}:
                    steps.append({"operation": kind.removeprefix("pipette_"), **{
                        key: params[key] for key in ("channels", "volume_ul", "speed")}})
                else:
                    raise AssertionError(f"Unexpected action kind {kind}")
        request = ManualPipettingRequest.model_validate({"protocol_id": document["protocol_id"], "steps": steps})
        assert compile_manual_pipetting(request)
    assert selections == {-1, 0, 1, 2, 3}, selections
    assert diagnostics == {"aspirate", "dispense", "eject", "plunger_up", "plunger_down", "dispense_all", "diagnoses", "initialize", "get_data", "last_error"}, diagnostics
    assert {"source_load_tips", "source_mix", "source_aspirate_air", "source_dispense_air", "source_purge"} <= operations
    return {"requests": len(exports), "actions": actions_count, "finite_plans": plans_count,
            "source_selections": sorted(selections), "diagnostics": sorted(diagnostics),
            "operations": sorted(operations), "hardware_executed": False}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1]), indent=2))
