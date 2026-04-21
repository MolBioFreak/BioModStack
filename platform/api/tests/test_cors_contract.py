from __future__ import annotations

import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = API_ROOT / "main.py"


def load_default_origins() -> list[str]:
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"), filename=str(MAIN_PY))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "default_origins" for target in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            raise AssertionError("default_origins must remain a literal list for contract inspection")
        origins: list[str] = []
        for element in node.value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                raise AssertionError("default_origins should contain only string literals")
            origins.append(element.value)
        return origins
    raise AssertionError("Could not find default_origins in main.py")


def test_cors_contract_limits_defaults_to_local_cordova_and_loopback_origins() -> None:
    origins = load_default_origins()

    assert "https://localhost" in origins
    assert "http://localhost" in origins
    assert "https://127.0.0.1" in origins
    assert "http://127.0.0.1:5173" in origins
    assert "https://compute-node.taileb3a90.ts.net" not in origins
