from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from model_registry import ModelRegistry  # noqa: E402
from services.md.feature_gate import (  # noqa: E402
    MD_FEATURE_DISABLED,
    MolecularDynamicsFeatureDisabled,
    ensure_molecular_dynamics_feature_enabled,
    molecular_dynamics_feature_enabled,
)
from scripts.bms_md.feature_gate import (  # noqa: E402
    MolecularDynamicsFeatureDisabled as CliMolecularDynamicsFeatureDisabled,
    require_experimental_md_feature,
)


def test_md_feature_defaults_off_and_registry_hides_public_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BMS_FEATURE_MOLECULAR_DYNAMICS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-config"))
    registry = ModelRegistry()

    assert molecular_dynamics_feature_enabled() is False
    assert registry.get_model("molecular_dynamics") is None
    assert "molecular_dynamics" not in {model.id for model in registry.list_models()}
    assert "molecular_dynamics" not in registry.get_categories()

    internal = registry.get_internal_model_definition("molecular_dynamics")
    assert internal is not None
    assert internal.experimental is True


def test_md_feature_explicit_enable_exposes_experimental_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    registry = ModelRegistry()

    assert molecular_dynamics_feature_enabled() is True
    model = registry.get_model("molecular_dynamics")
    assert model is not None
    assert model.experimental is True


def test_disabled_gate_raises_stable_code_before_callers_can_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "0")

    with pytest.raises(MolecularDynamicsFeatureDisabled) as exc_info:
        ensure_molecular_dynamics_feature_enabled("molecular_dynamics")

    assert exc_info.value.code == MD_FEATURE_DISABLED
    assert ensure_molecular_dynamics_feature_enabled("rfdiffusion") is None


def test_standalone_md_cli_uses_same_default_off_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BMS_FEATURE_MOLECULAR_DYNAMICS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-config"))
    with pytest.raises(CliMolecularDynamicsFeatureDisabled) as exc_info:
        require_experimental_md_feature()
    assert exc_info.value.code == MD_FEATURE_DISABLED

    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    assert require_experimental_md_feature() is None


def test_public_ingress_calls_gate_before_db_or_command_side_effects() -> None:
    jobs_source = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    create_job_source = jobs_source[jobs_source.index("async def create_job(") :]
    assert create_job_source.index("require_molecular_dynamics_feature") < create_job_source.index(
        "_raise_if_workflow_launches_disabled"
    )
    assert create_job_source.index("require_molecular_dynamics_feature") < create_job_source.index("registry.reload()")
    assert create_job_source.index("require_molecular_dynamics_feature") < create_job_source.index("await session.execute(")

    adapter_source = (API_ROOT / "routers" / "workflow_adapter.py").read_text(encoding="utf-8")
    launch_source = adapter_source[adapter_source.index("async def workflow_adapter_launch(") :]
    assert launch_source.index("require_molecular_dynamics_feature") < launch_source.index(
        "nextflow.launch_nextflow_job_detached("
    )


def test_install_and_compose_defaults_keep_experimental_md_off() -> None:
    profile_source = (REPO_ROOT / "biomodstack_runtime_profile.py").read_text(encoding="utf-8")
    compose_source = (REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8")

    assert '"molecular_dynamics": False' in profile_source
    assert '"molecular_dynamics": "BMS_FEATURE_MOLECULAR_DYNAMICS"' in profile_source
    assert "BMS_FEATURE_MOLECULAR_DYNAMICS: ${BMS_FEATURE_MOLECULAR_DYNAMICS:-0}" in compose_source
