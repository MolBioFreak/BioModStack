"""Display classification only: imported source metadata stays in saved records."""
from collections.abc import Mapping
from typing import Any


def is_sequence_annotation(feature: Any) -> bool:
    return isinstance(feature, Mapping) and str(feature.get("type") or "").strip().casefold() != "source"
