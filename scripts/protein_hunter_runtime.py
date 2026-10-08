from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def value_or_default(values: Mapping[str, Any], key: str, default: Any) -> Any:
    """Return an explicit value, including False or zero; default only for absent/None."""
    if key not in values or values[key] is None:
        return default
    return values[key]
