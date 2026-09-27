"""Camera illumination command evidence, never an optical readback."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class IlluminationChannel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: StrictInt = Field(ge=1, le=3)
    on: StrictBool | None


RgbByte = Annotated[StrictInt, Field(ge=0, le=255)]


class CameraRgbResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    rgb: list[RgbByte] = Field(min_length=3, max_length=3)
    tmcl: list[StrictInt] = Field(min_length=3, max_length=3)
    acks: dict[Literal["r", "g", "b"], dict[str, Any] | None]
    sent: StrictInt = Field(ge=0)
    elapsed_ms: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_channels(self):
        if set(self.acks) != {"r", "g", "b"}:
            raise ValueError("Expected all RGB acknowledgements")
        return self


class CameraIlluminationState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.camera_illumination.v1"]
    provider_generation: StrictInt = Field(ge=0)
    channels: list[IlluminationChannel] = Field(min_length=3, max_length=3)
    state_source: Literal["last_successful_command"]
    physical_effect_verified: StrictBool

    @model_validator(mode="after")
    def validate_evidence(self):
        if sorted(row.channel for row in self.channels) != [1, 2, 3]:
            raise ValueError("Expected exactly channels 1, 2, 3")
        if self.physical_effect_verified is not False:
            raise ValueError("Illumination is command evidence only")
        return self


class CameraIlluminationCommand(CameraIlluminationState):
    ok: StrictBool
    channel: StrictInt = Field(ge=1, le=3)
    on: StrictBool
    delivery_attempted: StrictBool

    @model_validator(mode="after")
    def validate_command(self):
        if self.ok is not True or self.delivery_attempted is not True:
            raise ValueError("Expected successful delivered command")
        if next(row.on for row in self.channels if row.channel == self.channel) is not self.on:
            raise ValueError("Command evidence disagrees with channel state")
        return self
