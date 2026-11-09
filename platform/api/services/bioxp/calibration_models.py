"""Wire mirror of robot oem_calibration_settings; no calibration math here."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

# OEM positionStruct stores signed System.Int32, including inc_factor. These
# are representational bounds only, not invented travel/calibration limits.
OemInt32 = Annotated[int, Field(strict=True, ge=-2147483648, le=2147483647)]
PositionName = Literal[
    "LOC_MS", "LOC_OC", "LOC_TC", "LOC_RC", "LOC_BSCS", "LOC_BSC", "WASTE_BIN",
    "TECANRACK1", "TECANRACK2", "TECANRACK3", "TECANRACK4", "LOC_STRIP1", "LOC_STRIP2",
    "LOC_STRIP3", "LOC_STRIP4", "LOC_TIP_HOTEL", "LOC_TROUGH", "LOC_OC_COVER",
    "LOC_OC_COVER_STORAGE", "LOC_RC_COVER", "LOC_RC_COVER_STORAGE", "LOC_P_OC", "LOC_P_OC_PRESS",
    "LOC_P_TC", "LOC_P_TC_PRESS", "LOC_P_MS", "LOC_P_MS_PRESS", "LOC_P_RC_PRESS", "LOC_PARK",
    "LOC_GANTRY", "LOC_CHECK_POINT", "CAMERA_OFFSET", "UNKNOWN",
]


def _position_patch_schema(schema: dict) -> None:
    # Omission means unchanged; explicit null is not an OEM integer. Keep the
    # public schema aligned with the runtime validator so typed editors do not
    # seed rejected null defaults into otherwise valid partial updates.
    schema["minProperties"] = 2
    for name, field in schema.get("properties", {}).items():
        if name == "name":
            continue
        branches = field.pop("anyOf", ())
        field.update(next(branch for branch in branches if branch.get("type") != "null"))
        field.pop("default", None)


class PositionCalibrationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_position_patch_schema)
    name: PositionName
    x: OemInt32 | None = None
    y: OemInt32 | None = None
    zLow: OemInt32 | None = None
    zDelta: OemInt32 | None = None
    inc_factor: OemInt32 | None = None

    @model_validator(mode="after")
    def nonempty_values(self):
        fields = self.model_fields_set - {"name"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("supply at least one integer PositionTable field; omit unchanged fields")
        return self


class HolderXY(BaseModel):
    """Operator-aligned XY only; no pipetting depth is implied."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: OemInt32
    y: OemInt32


class BulkReagentHolder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    micro_row_1: HolderXY
    micro_row_2: HolderXY
    pcr_row_1: HolderXY
    pcr_row_2: HolderXY
    pcr_y_pitch: Annotated[int, Field(strict=True, gt=0, le=2147483647)]

    @model_validator(mode="after")
    def derived_coordinates(self):
        for row in (self.pcr_row_1, self.pcr_row_2):
            if not -2147483648 <= row.y + 7 * self.pcr_y_pitch <= 2147483647:
                raise ValueError("PCR well coordinates exceed signed Int32")
        return self


class CalibrationSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    positions: tuple[PositionCalibrationPatch, ...] = ()
    bulk_reagent_holder: BulkReagentHolder | None = None

    @model_validator(mode="after")
    def unique_names(self):
        if not self.positions and self.bulk_reagent_holder is None:
            raise ValueError("supply positions or bulk_reagent_holder")
        if "bulk_reagent_holder" in self.model_fields_set and self.bulk_reagent_holder is None:
            raise ValueError("omit unchanged holder; null is not an XY calibration")
        if len({row.name for row in self.positions}) != len(self.positions):
            raise ValueError("duplicate PositionTable names")
        return self


class CalibrationSettingsRequest(CalibrationSettingsPatch):
    expected_connection_generation: Annotated[int, Field(strict=True, ge=0)]
