"""Every scheduled capability names a capability the inventory has."""
from __future__ import annotations

import json
from pathlib import Path

from services import ngs_molbio_capabilities as capabilities

INVENTORY = Path(capabilities.__file__).resolve().parents[1] / "config/ngs_molbio/capability_inventory_v2.json"


def test_scheduled_capabilities_all_exist_in_the_inventory() -> None:
    known = {row["capability_id"] for row in json.loads(INVENTORY.read_text(encoding="utf-8"))["capabilities"]}
    assert set(capabilities._PROJECT_SCHEDULED_CAPABILITIES) <= known
