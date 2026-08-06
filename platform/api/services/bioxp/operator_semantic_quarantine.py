from __future__ import annotations


EMERGENCY_STOP_QUARANTINE_REASON = (
    "Quarantined: this route records lifecycle emergency state but does not dispatch "
    "the OEM physical aggregate abort sequence."
)

OPERATOR_SEMANTIC_QUARANTINE_BY_PATH: dict[str, str] = {
    "/motion/power/diag": (
        "Quarantined: this diagnostic can enter the same unverified power-enable "
        "sequence and lacks truthful aggregate acknowledgment/readback."
    ),
    "/oem/runtime/emergency_stop": EMERGENCY_STOP_QUARANTINE_REASON,
}

OPERATOR_SEMANTIC_QUARANTINE_BY_ACTION_ID: dict[str, str] = {
    "route.motion_power_diag": OPERATOR_SEMANTIC_QUARANTINE_BY_PATH["/motion/power/diag"],
    "route.runtime_emergency_stop": OPERATOR_SEMANTIC_QUARANTINE_BY_PATH["/oem/runtime/emergency_stop"],
}
