from __future__ import annotations

from biomodstack_runtime_profile import install_feature_enabled


MD_FEATURE_DISABLED = "MD_FEATURE_DISABLED"


class MolecularDynamicsFeatureDisabled(RuntimeError):
    code = MD_FEATURE_DISABLED

    def __init__(self) -> None:
        super().__init__(
            "Molecular dynamics is a default-off experimental workflow; "
            "set BMS_FEATURE_MOLECULAR_DYNAMICS=1 before invoking bms-md."
        )


def require_experimental_md_feature() -> None:
    """Guard every standalone MD CLI command with the same install/runtime flag."""
    if not install_feature_enabled("molecular_dynamics"):
        raise MolecularDynamicsFeatureDisabled()
