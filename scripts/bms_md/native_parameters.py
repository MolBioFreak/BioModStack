"""Informational native MDP discovery; never a launch validation allowlist."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def native_parameter_catalog() -> dict[str, Any]:
    # A fresh object prevents callers changing the shared discovery document.
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
