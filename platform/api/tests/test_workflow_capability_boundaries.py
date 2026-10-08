from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FRONTEND_ROOT = REPO_ROOT / "platform" / "frontend"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from template_registry import TemplateRegistry  # noqa: E402


@pytest.mark.parametrize(
    "relative_path",
    [
        "workflows/esmfold2_experimental.nf",
        "workflows/boltzgen_design.nf",
        "platform/api/config/templates/esmfold2.yaml",
        "platform/api/config/templates/esmfold2_experimental.yaml",
        "platform/api/config/templates/boltzgen_design.yaml",
    ],
)
def test_engine_capabilities_have_no_standalone_entrypoint_or_template(relative_path: str) -> None:
    assert not (REPO_ROOT / relative_path).exists(), relative_path


def test_frontend_has_no_dedicated_esmfold2_or_boltzgen_launcher() -> None:
    source = (FRONTEND_ROOT / "src/components/JobSubmission.tsx").read_text()
    inventory = (FRONTEND_ROOT / "src/components/workflowModelInventory.ts").read_text()

    assert "import { BoltzGenTemplate }" not in source
    assert "boltzgen: 'boltzgen_design'" not in source
    assert "esmfold2: 'esmfold2'" not in source
    assert "esmfold2_experimental: 'esmfold2_experimental'" not in source
    assert "Standalone ESMFold2" not in source
    assert "id: 'boltzgen_design'" not in inventory
    assert "id: 'esmfold2'" not in inventory
    assert "id: 'esmfold2_experimental'" not in inventory


def test_standalone_protein_binder_template_is_not_publicly_exposed() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")
    public_ids = {template.id for template in registry.list_templates(enabled_only=True)}
    binder_template = registry.get_template("binder_design")
    templates_router = (API_ROOT / "routers/templates.py").read_text(encoding="utf-8")

    assert binder_template is not None
    assert binder_template.enabled is False
    assert "binder_design" not in public_ids
    assert "if not template or not template.enabled:" in templates_router
