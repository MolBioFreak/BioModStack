"""Read-only compound-transfer observations, never motion admission authority."""
import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .operator_models import OperatorControlCatalogV2


class ReferenceRow(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    axis: str
    state: str
    source: Annotated[str, Field(min_length=1)]
    updated_at: Annotated[str, Field(min_length=1)]
    state_version: Annotated[StrictInt, Field(ge=0)]


class ReferenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    ok: StrictBool
    persisted: StrictBool
    verified: StrictBool
    durable_clean: StrictBool
    rows: dict[str, ReferenceRow]

    @model_validator(mode="after")
    def authoritative(self):
        if not all((self.ok, self.persisted, self.verified, self.durable_clean)):
            raise ValueError("Robot reference authority is not verified and durable")
        for axis in ("x", "y", "z", "g"):
            row = self.rows.get(axis)
            if row is None or row.axis != axis or row.state != "referenced":
                raise ValueError(f"Robot reference missing or unverified: {axis}")
        return self


def transfer_preflight(reference, catalog, generation):
    ReferenceSnapshot.model_validate(reference)
    parsed = OperatorControlCatalogV2.model_validate(catalog)
    deck = parsed.dashboard.deck
    if deck is None or not deck.position_table_revision or not deck.destination_catalog_revision:
        raise ValueError("Robot deck revision authority is unavailable")
    digest = hashlib.sha256(json.dumps(reference, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {
        "connection_generation": generation,
        "ownership_generation": parsed.dashboard.ownership_generation,
        "observed_deck": deck.model_dump(mode="json"),
        "preflight": {
            "reference_snapshot": reference,
            "artifact_refs": [f"robot:position-table:sha256:{deck.position_table_revision}",
                              f"robot:destination-catalog:sha256:{deck.destination_catalog_revision}",
                              f"robot:reference-snapshot:sha256:{digest}"],
        },
    }
