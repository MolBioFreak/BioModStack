"""Canonical strict typed FrustraMPNN requested and effective settings authority."""

from __future__ import annotations

import copy
import hashlib
import re
from math import isfinite
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

import rfc8785
from jsonschema import Draft202012Validator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from .contracts import (
    ContractValidationError,
    canonical_json_loads,
    canonical_sha256,
    load_schema,
    validate_schema,
)
from .runtime import FRUSTRAMPNN_RUNTIME_IDENTITY, runtime_identity_dict


_CANONICAL_HIGH_MAX = -1.0
_CANONICAL_MINIMAL_MIN = 0.58
_NORMALIZATION_POLICY_ID = "frustrampnn_structure_normalizer"
_NORMALIZATION_POLICY_VERSION = 1
_THRESHOLD_POLICY_ID = "frustrampnn_class_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SHA256_RE = re.compile(_SHA256_PATTERN)
_CAPABILITY_OPTION_KEYS = (
    "pdb",
    "checkpoint",
    "output",
    "chains",
    "positions",
    "device",
    "config",
    "quiet",
    "help",
)
_API_ROOT = Path(__file__).resolve().parents[2]
_CAPABILITY_INVENTORY_PATH = (
    _API_ROOT / "config/models/frustrampnn_capability_inventory_v1.json"
)
NonEmptyString = Annotated[str, Field(min_length=1)]
OptionalNonEmptyString = Annotated[str, Field(min_length=1)] | None
Sha256String = Annotated[str, Field(pattern=_SHA256_PATTERN)]
SettingsValueOrigin = Literal["bms_default", "operator_request"]
ValueSource = SettingsValueOrigin


class SourceResolutionError(ValueError):
    """A structure-map or exact source selector cannot be resolved safely."""

    def __init__(self, message: str, *, location: tuple[str | int, ...]) -> None:
        super().__init__(message)
        self.location = location


class RequestedSettingsPayloadError(ValueError):
    """A supplied launch object omitted part of the normalized typed contract."""

    def __init__(self, message: str, *, location: tuple[str | int, ...]) -> None:
        super().__init__(message)
        self.location = location


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FrustraMPNNEntitySelector(_StrictFrozenModel):
    """Stable source-backed identity for one protein entity/chain instance."""

    entity_instance_id: NonEmptyString
    source_entity_id: OptionalNonEmptyString
    label_asym_id: OptionalNonEmptyString
    auth_asym_id: OptionalNonEmptyString

    def canonical_key(self) -> tuple[str, str, str, str]:
        return (
            self.entity_instance_id,
            self.source_entity_id or "",
            self.label_asym_id or "",
            self.auth_asym_id or "",
        )

    def source_key(self) -> tuple[str, str]:
        return (self.entity_instance_id, self.source_entity_id or "")

    def matches_entity_key(self, key: tuple[str, str, str, str]) -> bool:
        return (
            key[:2] == self.source_key()
            and (self.label_asym_id is None or key[2] == self.label_asym_id)
            and (self.auth_asym_id is None or key[3] == self.auth_asym_id)
        )


class FrustraMPNNRegionSelector(FrustraMPNNEntitySelector):
    """One inclusive source-sequence region on one stable protein entity."""

    sequence_start: Annotated[StrictInt, Field(ge=1)]
    sequence_end: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def _validate_bounds(self) -> FrustraMPNNRegionSelector:
        if self.sequence_start > self.sequence_end:
            raise ValueError("region sequence_start must be <= sequence_end")
        return self

    def region_key(self) -> tuple[str, str, str, str, int, int]:
        return (
            *super().canonical_key(),
            self.sequence_start,
            self.sequence_end,
        )


class FrustraMPNNResidueSelector(_StrictFrozenModel):
    """One unambiguous source residue locator resolvable through structure-map rows."""

    entity_instance_id: NonEmptyString
    source_entity_id: OptionalNonEmptyString
    label_asym_id: OptionalNonEmptyString
    auth_asym_id: NonEmptyString
    auth_seq_id: StrictInt
    insertion_code: Annotated[str, Field(max_length=1)] = ""
    sequence_index: Annotated[StrictInt, Field(ge=1)]

    def canonical_key(self) -> tuple[str, str, str, str, int, str, int]:
        return (
            self.entity_instance_id,
            self.source_entity_id or "",
            self.label_asym_id or "",
            self.auth_asym_id,
            self.auth_seq_id,
            self.insertion_code,
            self.sequence_index,
        )

    def locator_key(self) -> tuple[str, str, int, str]:
        return (
            self.entity_instance_id,
            self.auth_asym_id,
            self.auth_seq_id,
            self.insertion_code,
        )


class FrustraMPNNProteinSelection(_StrictFrozenModel):
    mode: Literal[
        "all_protein_entities",
        "selected_entities",
        "selected_regions",
        "selected_residues",
    ] = "all_protein_entities"
    entities: tuple[FrustraMPNNEntitySelector, ...] = ()
    regions: tuple[FrustraMPNNRegionSelector, ...] = ()
    residues: tuple[FrustraMPNNResidueSelector, ...] = ()

    @field_validator("entities", "regions", "residues", mode="before")
    @classmethod
    def _sequence_to_tuple(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("selectors must be an array")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_mode_and_canonicalize(self) -> FrustraMPNNProteinSelection:
        if self.mode == "all_protein_entities":
            if self.entities or self.regions or self.residues:
                raise ValueError(
                    "all_protein_entities cannot contain entity, region, or residue selectors"
                )
        elif self.mode == "selected_entities":
            if not self.entities or self.regions or self.residues:
                raise ValueError(
                    "selected_entities requires entities and cannot contain regions or residues"
                )
            identities = [item.entity_instance_id for item in self.entities]
            if len(identities) != len(set(identities)):
                raise ValueError("selected_entities contains a duplicate entity identity")
        elif self.mode == "selected_regions":
            if not self.regions or self.entities or self.residues:
                raise ValueError(
                    "selected_regions requires regions and cannot contain entities or residues"
                )
            ordered_regions = sorted(
                self.regions, key=lambda item: item.region_key()
            )
            by_entity: dict[tuple[str, str, str, str], list[FrustraMPNNRegionSelector]] = {}
            for region in ordered_regions:
                by_entity.setdefault(
                    FrustraMPNNEntitySelector(
                        entity_instance_id=region.entity_instance_id,
                        source_entity_id=region.source_entity_id,
                        label_asym_id=region.label_asym_id,
                        auth_asym_id=region.auth_asym_id,
                    ).canonical_key(),
                    [],
                ).append(region)
            if any(
                current.sequence_start <= previous.sequence_end
                for regions in by_entity.values()
                for previous, current in zip(regions, regions[1:], strict=False)
            ):
                raise ValueError("selected_regions cannot contain overlapping regions")
        else:
            if not self.residues or self.entities or self.regions:
                raise ValueError(
                    "selected_residues requires residues and cannot contain entities or regions"
                )
            locators = [item.locator_key() for item in self.residues]
            if len(locators) != len(set(locators)):
                raise ValueError("selected_residues contains a duplicate residue locator")

        object.__setattr__(
            self,
            "entities",
            tuple(sorted(self.entities, key=lambda item: item.canonical_key())),
        )
        object.__setattr__(
            self,
            "regions",
            tuple(sorted(self.regions, key=lambda item: item.region_key())),
        )
        object.__setattr__(
            self,
            "residues",
            tuple(sorted(self.residues, key=lambda item: item.canonical_key())),
        )
        return self


class FrustraMPNNSourceStructureSettings(_StrictFrozenModel):
    selected_model_number: Annotated[StrictInt, Field(ge=1)] = 1
    preferred_altloc: Annotated[str, Field(pattern=r"^(?:|[A-Za-z0-9])$")] = ""


class FrustraMPNNClassificationPolicy(_StrictFrozenModel):
    mode: Literal["canonical", "custom"] = "canonical"
    high_max: StrictFloat = _CANONICAL_HIGH_MAX
    minimal_min: StrictFloat = _CANONICAL_MINIMAL_MIN

    @model_validator(mode="after")
    def _validate_thresholds(self) -> FrustraMPNNClassificationPolicy:
        if not isfinite(self.high_max) or not isfinite(self.minimal_min):
            raise ValueError("classification thresholds must be finite")
        if self.high_max >= self.minimal_min:
            raise ValueError("classification thresholds require high_max < minimal_min")
        if self.mode == "canonical" and (
            self.high_max != _CANONICAL_HIGH_MAX
            or self.minimal_min != _CANONICAL_MINIMAL_MIN
        ):
            raise ValueError("canonical classification mode requires -1.0 and 0.58")
        return self


class FrustraMPNNRequestedSettings(_StrictFrozenModel):
    schema_name: Literal["frustrampnn_settings"] = "frustrampnn_settings"
    schema_version: Literal[1, 2] = 2
    settings_value_origin: SettingsValueOrigin = Field(
        default="bms_default",
        json_schema_extra={"readOnly": True},
    )
    batching_enabled: bool = False
    structures_per_job: Annotated[StrictInt, Field(ge=1, le=250)] = 1
    protein_selection: FrustraMPNNProteinSelection = Field(
        default_factory=FrustraMPNNProteinSelection
    )
    source_structure: FrustraMPNNSourceStructureSettings = Field(
        default_factory=FrustraMPNNSourceStructureSettings
    )
    classification_policy: FrustraMPNNClassificationPolicy = Field(
        default_factory=FrustraMPNNClassificationPolicy
    )


class FrustraMPNNResolvedResidue(_StrictFrozenModel):
    """Source-author identity resolved to one normalized chain/model position."""

    entity_instance_id: NonEmptyString
    source_entity_id: OptionalNonEmptyString
    label_asym_id: OptionalNonEmptyString
    label_seq_id: Annotated[StrictInt, Field(ge=1)] | None
    auth_asym_id: NonEmptyString
    auth_seq_id: StrictInt
    insertion_code: Annotated[str, Field(max_length=1)] = ""
    sequence_index: Annotated[StrictInt, Field(ge=1)]
    wt: Annotated[str, Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")]
    pdb_chain_id: Annotated[str, Field(min_length=1, max_length=1)]
    pdb_residue_id: Annotated[StrictInt, Field(ge=-999, le=9999)]
    pdb_insertion_code: Annotated[str, Field(max_length=1)]
    model_position: Annotated[StrictInt, Field(ge=0)]
    residue_name: Annotated[str, Field(min_length=3, max_length=3)]

    def source_key(self) -> tuple[str, str, str, str, int, str, int]:
        return (
            self.entity_instance_id,
            self.source_entity_id or "",
            self.label_asym_id or "",
            self.auth_asym_id,
            self.auth_seq_id,
            self.insertion_code,
            self.sequence_index,
        )

    def locator_key(self) -> tuple[str, str, int, str]:
        return (
            self.entity_instance_id,
            self.auth_asym_id,
            self.auth_seq_id,
            self.insertion_code,
        )

    def normalized_key(self) -> tuple[str, int]:
        return (self.pdb_chain_id, self.model_position)


class FrustraMPNNResolvedChainSelection(_StrictFrozenModel):
    """One source entity resolved to normalized residues for execution."""

    entity: FrustraMPNNEntitySelector
    pdb_chain_id: Annotated[str, Field(min_length=1, max_length=1)]
    residues: tuple[FrustraMPNNResolvedResidue, ...]

    @field_validator("residues", mode="before")
    @classmethod
    def _residues_to_tuple(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("resolved residues must be an array")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_and_canonicalize(self) -> FrustraMPNNResolvedChainSelection:
        if not self.residues:
            raise ValueError("resolved chain selection requires at least one residue")
        expected_entity = self.entity.canonical_key()
        source_keys: list[tuple[str, str, str, str, int, str, int]] = []
        normalized_keys: list[tuple[str, int]] = []
        for residue in self.residues:
            residue_entity = (
                residue.entity_instance_id,
                residue.source_entity_id or "",
                residue.label_asym_id or "",
                residue.auth_asym_id,
            )
            if residue_entity != expected_entity:
                raise ValueError("resolved residue entity identity mismatches its chain")
            if residue.pdb_chain_id != self.pdb_chain_id:
                raise ValueError("resolved residue normalized chain identity mismatches its chain")
            source_keys.append(residue.source_key())
            normalized_keys.append(residue.normalized_key())
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("resolved chain contains duplicate source residue identity")
        if len(normalized_keys) != len(set(normalized_keys)):
            raise ValueError("resolved chain contains duplicate normalized model position")
        object.__setattr__(
            self,
            "residues",
            tuple(sorted(self.residues, key=lambda residue: residue.source_key())),
        )
        return self

    def canonical_key(self) -> tuple[str, str, str, str, str]:
        return (self.pdb_chain_id, *self.entity.canonical_key())


class FrustraMPNNResolutionIdentity(_StrictFrozenModel):
    """Exact source/map/normalized-PDB identities used for residue resolution."""

    source_artifact_sha256: Sha256String
    structure_map_schema_name: Literal["frustrampnn_structure_map"] = (
        "frustrampnn_structure_map"
    )
    structure_map_schema_version: Literal[1] = 1
    structure_map_sha256: Sha256String
    normalized_pdb_sha256: Sha256String


class FrustraMPNNProteinSelectionValueSources(_StrictFrozenModel):
    mode: ValueSource
    entities: ValueSource
    regions: ValueSource = "bms_default"
    residues: ValueSource


class FrustraMPNNSourceStructureValueSources(_StrictFrozenModel):
    selected_model_number: ValueSource
    preferred_altloc: ValueSource


class FrustraMPNNClassificationPolicyValueSources(_StrictFrozenModel):
    mode: ValueSource
    high_max: ValueSource
    minimal_min: ValueSource


class FrustraMPNNSettingsValueSources(_StrictFrozenModel):
    batching_enabled: ValueSource = "bms_default"
    structures_per_job: ValueSource = "bms_default"
    protein_selection: FrustraMPNNProteinSelectionValueSources
    source_structure: FrustraMPNNSourceStructureValueSources
    classification_policy: FrustraMPNNClassificationPolicyValueSources


class FrustraMPNNEffectiveSettings(_StrictFrozenModel):
    schema_name: Literal["frustrampnn_effective_settings"] = (
        "frustrampnn_effective_settings"
    )
    schema_version: Literal[1, 2] = 2
    requested_settings: FrustraMPNNRequestedSettings
    settings_value_origin: SettingsValueOrigin
    resolved_chains: tuple[FrustraMPNNResolvedChainSelection, ...]
    normalization_policy_id: Literal[
        "frustrampnn_structure_normalizer"
    ] = _NORMALIZATION_POLICY_ID
    normalization_policy_version: Literal[1] = _NORMALIZATION_POLICY_VERSION
    threshold_policy_id: Literal["frustrampnn_class_v1"] = _THRESHOLD_POLICY_ID
    threshold_policy_sha256: Sha256String
    settings_sha256: Sha256String
    capability_inventory_byte_sha256: Sha256String
    resolution_identity: FrustraMPNNResolutionIdentity
    value_sources: FrustraMPNNSettingsValueSources
    effective_settings_sha256: Sha256String

    @field_validator("resolved_chains", mode="before")
    @classmethod
    def _chains_to_tuple(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("resolved_chains must be an array")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_resolution_and_hashes(self) -> FrustraMPNNEffectiveSettings:
        protein_sources = self.value_sources.protein_selection
        if "regions" not in protein_sources.model_fields_set:
            protein_sources = protein_sources.model_copy(
                update={"regions": self.settings_value_origin}
            )
            object.__setattr__(
                self,
                "value_sources",
                self.value_sources.model_copy(
                    update={"protein_selection": protein_sources}
                ),
            )
        if not self.resolved_chains:
            raise ValueError("effective settings require a non-empty resolved chain selection")
        object.__setattr__(
            self,
            "resolved_chains",
            tuple(sorted(self.resolved_chains, key=lambda chain: chain.canonical_key())),
        )
        chain_keys = [chain.canonical_key() for chain in self.resolved_chains]
        if len(chain_keys) != len(set(chain_keys)):
            raise ValueError("effective settings contain a duplicate resolved chain identity")
        entity_keys = [chain.entity.canonical_key() for chain in self.resolved_chains]
        if len(entity_keys) != len(set(entity_keys)):
            raise ValueError("effective settings contain a duplicate source entity identity")
        normalized_chain_ids = [chain.pdb_chain_id for chain in self.resolved_chains]
        if len(normalized_chain_ids) != len(set(normalized_chain_ids)):
            raise ValueError("effective settings contain a duplicate normalized chain identity")

        residues = [
            residue for chain in self.resolved_chains for residue in chain.residues
        ]
        source_keys = [residue.source_key() for residue in residues]
        normalized_keys = [residue.normalized_key() for residue in residues]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("effective settings contain duplicate resolved residue identity")
        if len(normalized_keys) != len(set(normalized_keys)):
            raise ValueError("effective settings contain duplicate normalized model position")

        selection = self.requested_settings.protein_selection
        resolved_entities = [chain.entity for chain in self.resolved_chains]
        if selection.mode == "selected_entities":
            if len(resolved_entities) != len(selection.entities) or any(
                sum(
                    entity.matches_entity_key(resolved.canonical_key())
                    for resolved in resolved_entities
                )
                != 1
                for entity in selection.entities
            ):
                raise ValueError(
                    "resolved chain entity identities do not match every selected entity"
                )
        elif selection.mode == "selected_regions":
            resolved_region_positions = [
                (*chain.entity.source_key(), residue.sequence_index)
                for chain in self.resolved_chains
                for residue in chain.residues
            ]
            expected_count = sum(
                region.sequence_end - region.sequence_start + 1
                for region in selection.regions
            )
            complete = len(resolved_region_positions) == expected_count
            for region in selection.regions:
                observed = {
                    sequence_index
                    for *source_key, sequence_index in resolved_region_positions
                    if tuple(source_key) == region.source_key()
                    and region.sequence_start <= sequence_index <= region.sequence_end
                }
                span_size = region.sequence_end - region.sequence_start + 1
                complete = complete and (
                    len(observed) == span_size
                    and min(observed, default=0) == region.sequence_start
                    and max(observed, default=0) == region.sequence_end
                )
            if not complete:
                raise ValueError(
                    "resolved region coverage does not match every requested region"
                )
        elif selection.mode == "selected_residues":
            selected_residues = {residue.canonical_key() for residue in selection.residues}
            if set(source_keys) != selected_residues:
                raise ValueError(
                    "resolved residue identities do not match every requested residue"
                )

        if self.settings_value_origin != self.requested_settings.settings_value_origin:
            raise ValueError("settings value origin is not cross-bound")
        if self.settings_sha256 != requested_settings_sha256(
            self.requested_settings
        ):
            raise ValueError("requested settings SHA-256 does not match effective settings")
        if self.threshold_policy_sha256 != classification_policy_sha256(
            self.requested_settings.classification_policy
        ):
            raise ValueError("threshold policy SHA-256 does not match requested settings")
        if self.value_sources != settings_value_sources(self.settings_value_origin):
            raise ValueError("settings value-source metadata does not match requested values")
        _, inventory_sha256 = load_capability_inventory()
        if self.capability_inventory_byte_sha256 != inventory_sha256:
            raise ValueError("capability inventory byte SHA-256 does not match installed bytes")
        if self.effective_settings_sha256 != effective_settings_sha256(self):
            raise ValueError("effective settings SHA-256 does not match content")
        return self


def _canonical_model_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=False)


def _compatible_requested_settings_payload(
    settings: Mapping[str, Any] | FrustraMPNNRequestedSettings,
) -> dict[str, Any]:
    payload = (
        _canonical_model_dump(settings)
        if isinstance(settings, BaseModel)
        else copy.deepcopy(dict(settings))
    )
    selection = payload.get("protein_selection")
    if isinstance(selection, dict) and selection.get("regions") == []:
        selection.pop("regions")
    if payload.get("schema_version") == 1:
        payload.pop("batching_enabled", None)
        payload.pop("structures_per_job", None)
    return payload


def compatible_effective_settings_payload(
    settings: Mapping[str, Any] | BaseModel,
) -> dict[str, Any]:
    payload = (
        _canonical_model_dump(settings)
        if isinstance(settings, BaseModel)
        else copy.deepcopy(dict(settings))
    )
    requested = payload.get("requested_settings")
    regions_empty = False
    if isinstance(requested, dict):
        selection = requested.get("protein_selection")
        if isinstance(selection, dict) and selection.get("regions") == []:
            regions_empty = True
            selection.pop("regions")
        if requested.get("schema_version") == 1:
            requested.pop("batching_enabled", None)
            requested.pop("structures_per_job", None)
            value_sources = payload.get("value_sources")
            if isinstance(value_sources, dict):
                value_sources.pop("batching_enabled", None)
                value_sources.pop("structures_per_job", None)
    if regions_empty:
        value_sources = payload.get("value_sources")
        if isinstance(value_sources, dict):
            protein_sources = value_sources.get("protein_selection")
            if isinstance(protein_sources, dict):
                protein_sources.pop("regions", None)
    return payload


def requested_settings_sha256(settings: FrustraMPNNRequestedSettings) -> str:
    if not isinstance(settings, FrustraMPNNRequestedSettings):
        raise TypeError("settings must be typed FrustraMPNN requested settings")
    return canonical_sha256(_compatible_requested_settings_payload(settings))


def classification_policy_sha256(policy: FrustraMPNNClassificationPolicy) -> str:
    if not isinstance(policy, FrustraMPNNClassificationPolicy):
        raise TypeError("policy must be a typed FrustraMPNN classification policy")
    return canonical_sha256(_canonical_model_dump(policy))


def _effective_payload_sha256(settings: Mapping[str, Any] | BaseModel) -> str:
    payload = compatible_effective_settings_payload(settings)
    payload.pop("effective_settings_sha256", None)
    return canonical_sha256(payload)


def effective_settings_sha256(settings: FrustraMPNNEffectiveSettings) -> str:
    if not isinstance(settings, FrustraMPNNEffectiveSettings):
        raise TypeError("settings must be typed FrustraMPNN effective settings")
    return _effective_payload_sha256(settings)


def runtime_identity_sha256() -> str:
    return canonical_sha256(runtime_identity_dict())


def default_settings() -> FrustraMPNNRequestedSettings:
    """Return typed defaults matching the currently installed BMS behavior."""

    return FrustraMPNNRequestedSettings()


def validate_complete_requested_settings(
    payload: Mapping[str, Any] | FrustraMPNNRequestedSettings,
) -> FrustraMPNNRequestedSettings:
    """Validate one explicitly supplied, complete normalized launch object."""

    if isinstance(payload, FrustraMPNNRequestedSettings):
        return payload
    if not isinstance(payload, Mapping):
        raise RequestedSettingsPayloadError(
            "frustrampnn_settings must be an object",
            location=(),
        )
    source = dict(payload)
    if "settings_value_origin" in source:
        raise RequestedSettingsPayloadError(
            "settings value origin is server-authored and cannot be supplied by callers",
            location=("settings_value_origin",),
        )
    if source.get("schema_version") != 2:
        raise RequestedSettingsPayloadError(
            "fresh FrustraMPNN settings must use schema_version 2",
            location=("schema_version",),
        )
    required_shapes: tuple[tuple[tuple[str | int, ...], frozenset[str]], ...] = (
        (
            (),
            frozenset(
                {
                    "schema_name",
                    "schema_version",
                    "batching_enabled",
                    "structures_per_job",
                    "protein_selection",
                    "source_structure",
                    "classification_policy",
                }
            ),
        ),
        (
            ("protein_selection",),
            frozenset({"mode", "entities", "regions", "residues"}),
        ),
        (
            ("source_structure",),
            frozenset({"selected_model_number", "preferred_altloc"}),
        ),
        (
            ("classification_policy",),
            frozenset({"mode", "high_max", "minimal_min"}),
        ),
    )
    for location, required in required_shapes:
        value: Any = source
        for part in location:
            if not isinstance(value, Mapping) or part not in value:
                value = None
                break
            value = value[part]
        if not isinstance(value, Mapping):
            raise RequestedSettingsPayloadError(
                f"frustrampnn_settings.{'.'.join(map(str, location))} must be an object",
                location=location,
            )
        missing = sorted(required - set(value))
        if missing:
            raise RequestedSettingsPayloadError(
                f"complete frustrampnn_settings is missing: {', '.join(missing)}",
                location=(*location, missing[0]),
            )

    selection = source["protein_selection"]
    selector_shapes = (
        (
            "entities",
            frozenset(
                {
                    "entity_instance_id",
                    "source_entity_id",
                    "label_asym_id",
                    "auth_asym_id",
                }
            ),
        ),
        (
            "regions",
            frozenset(
                {
                    "entity_instance_id",
                    "source_entity_id",
                    "label_asym_id",
                    "auth_asym_id",
                    "sequence_start",
                    "sequence_end",
                }
            ),
        ),
        (
            "residues",
            frozenset(
                {
                    "entity_instance_id",
                    "source_entity_id",
                    "label_asym_id",
                    "auth_asym_id",
                    "auth_seq_id",
                    "insertion_code",
                    "sequence_index",
                }
            ),
        ),
    )
    for collection_name, required in selector_shapes:
        collection = selection.get(collection_name)
        if not isinstance(collection, (list, tuple)):
            raise RequestedSettingsPayloadError(
                f"frustrampnn_settings.protein_selection.{collection_name} must be an array",
                location=("protein_selection", collection_name),
            )
        for index, selector in enumerate(collection):
            if not isinstance(selector, Mapping):
                raise RequestedSettingsPayloadError(
                    f"frustrampnn_settings selector {collection_name}[{index}] must be an object",
                    location=("protein_selection", collection_name, index),
                )
            missing = sorted(required - set(selector))
            if missing:
                raise RequestedSettingsPayloadError(
                    f"complete frustrampnn_settings selector is missing: {', '.join(missing)}",
                    location=("protein_selection", collection_name, index, missing[0]),
                )
    source["settings_value_origin"] = "operator_request"
    return FrustraMPNNRequestedSettings.model_validate(source)


def validate_persisted_requested_settings(
    payload: Any,
) -> FrustraMPNNRequestedSettings:
    """Reparse durable settings with their explicit origin; never infer it."""

    if isinstance(payload, FrustraMPNNRequestedSettings):
        return payload
    if not isinstance(payload, Mapping) or "settings_value_origin" not in payload:
        raise RequestedSettingsPayloadError(
            "persisted settings value origin is required",
            location=("settings_value_origin",),
        )
    return FrustraMPNNRequestedSettings.model_validate(dict(payload))


def complete_requested_settings_schema() -> dict[str, Any]:
    """Publish the same explicit-object completeness required by launch APIs."""

    schema = copy.deepcopy(FrustraMPNNRequestedSettings.model_json_schema())
    schema["required"] = [
        "schema_name",
        "schema_version",
        "batching_enabled",
        "structures_per_job",
        "protein_selection",
        "source_structure",
        "classification_policy",
    ]
    schema["properties"]["schema_version"] = {
        "const": 2,
        "title": "Schema Version",
        "type": "integer",
    }
    schema["properties"].pop("settings_value_origin", None)
    definitions = schema["$defs"]
    definitions["FrustraMPNNProteinSelection"]["required"] = [
        "mode",
        "entities",
        "regions",
        "residues",
    ]
    definitions["FrustraMPNNSourceStructureSettings"]["required"] = [
        "selected_model_number",
        "preferred_altloc",
    ]
    definitions["FrustraMPNNClassificationPolicy"]["required"] = [
        "mode",
        "high_max",
        "minimal_min",
    ]
    residue_required = definitions["FrustraMPNNResidueSelector"]["required"]
    if "insertion_code" not in residue_required:
        insertion_index = residue_required.index("sequence_index")
        residue_required.insert(insertion_index, "insertion_code")
    return schema


def settings_value_sources(
    origin: SettingsValueOrigin,
) -> FrustraMPNNSettingsValueSources:
    """Expand one closed durable request origin to every effective field."""

    if origin not in {"bms_default", "operator_request"}:
        raise ValueError("settings value origin is invalid")
    return FrustraMPNNSettingsValueSources(
        batching_enabled=origin,
        structures_per_job=origin,
        protein_selection=FrustraMPNNProteinSelectionValueSources(
            mode=origin,
            entities=origin,
            regions=origin,
            residues=origin,
        ),
        source_structure=FrustraMPNNSourceStructureValueSources(
            selected_model_number=origin,
            preferred_altloc=origin,
        ),
        classification_policy=FrustraMPNNClassificationPolicyValueSources(
            mode=origin,
            high_max=origin,
            minimal_min=origin,
        ),
    )


def load_capability_inventory() -> tuple[dict[str, Any], str]:
    """Validate Phase 0 inventory and return it with SHA-256 of exact file bytes."""

    try:
        raw_bytes = _CAPABILITY_INVENTORY_PATH.read_bytes()
    except OSError as exc:
        raise ContractValidationError(
            f"cannot read FrustraMPNN capability inventory: {exc}"
        ) from exc
    inventory = canonical_json_loads(raw_bytes)
    if not isinstance(inventory, dict):
        raise ContractValidationError("FrustraMPNN capability inventory is not an object")
    errors = sorted(
        Draft202012Validator(load_schema("capability_inventory_v1")).iter_errors(
            inventory
        ),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        raise ContractValidationError(
            f"FrustraMPNN capability inventory schema validation failed: {errors[0].message}"
        )

    content_preimage = dict(inventory)
    recorded_content_sha256 = content_preimage.pop("content_sha256")
    if recorded_content_sha256 != hashlib.sha256(
        rfc8785.dumps(content_preimage)
    ).hexdigest():
        raise ContractValidationError(
            "FrustraMPNN capability inventory content SHA-256 is invalid"
        )
    option_keys = tuple(
        option["option_key"] for option in inventory["predict_options"]
    )
    if option_keys != _CAPABILITY_OPTION_KEYS:
        raise ContractValidationError(
            "FrustraMPNN capability inventory option surface is not exact"
        )
    runtime = inventory["runtime_identity"]
    expected_runtime = {
        "image_path": FRUSTRAMPNN_RUNTIME_IDENTITY.configured_sif_path,
        "image_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        "executable_path": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path,
        "executable_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256,
        "checkpoint_id": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        "checkpoint_path": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
        "checkpoint_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
        "package_version": FRUSTRAMPNN_RUNTIME_IDENTITY.package_version,
        "source_commit": FRUSTRAMPNN_RUNTIME_IDENTITY.source_commit,
    }
    if runtime != expected_runtime:
        raise ContractValidationError(
            "FrustraMPNN capability inventory runtime identity is not installed runtime"
        )
    return copy.deepcopy(inventory), hashlib.sha256(raw_bytes).hexdigest()


def _build_effective_settings(
    requested: FrustraMPNNRequestedSettings,
    *,
    resolved_chains: tuple[FrustraMPNNResolvedChainSelection, ...],
    resolution_identity: FrustraMPNNResolutionIdentity,
) -> FrustraMPNNEffectiveSettings:
    """Build one effective receipt from validated source-authoritative records."""

    if not isinstance(requested, FrustraMPNNRequestedSettings):
        raise TypeError("requested must be typed FrustraMPNN requested settings")
    if not isinstance(resolved_chains, tuple) or any(
        not isinstance(chain, FrustraMPNNResolvedChainSelection)
        for chain in resolved_chains
    ):
        raise TypeError("resolved_chains must contain only typed resolved chain records")
    if not isinstance(resolution_identity, FrustraMPNNResolutionIdentity):
        raise TypeError("resolution_identity must be a typed resolution identity")
    if not resolved_chains:
        raise ValueError("effective selection cannot contain an empty resolved chain set")

    _, capability_inventory_sha256 = load_capability_inventory()
    payload: dict[str, Any] = {
        "schema_name": "frustrampnn_effective_settings",
        "schema_version": requested.schema_version,
        "requested_settings": requested.model_dump(mode="json", exclude_none=False),
        "settings_value_origin": requested.settings_value_origin,
        "resolved_chains": [
            chain.model_dump(mode="json", exclude_none=False)
            for chain in sorted(resolved_chains, key=lambda item: item.canonical_key())
        ],
        "normalization_policy_id": _NORMALIZATION_POLICY_ID,
        "normalization_policy_version": _NORMALIZATION_POLICY_VERSION,
        "threshold_policy_id": _THRESHOLD_POLICY_ID,
        "threshold_policy_sha256": classification_policy_sha256(
            requested.classification_policy
        ),
        "settings_sha256": requested_settings_sha256(requested),
        "capability_inventory_byte_sha256": capability_inventory_sha256,
        "resolution_identity": resolution_identity.model_dump(
            mode="json", exclude_none=False
        ),
        "value_sources": settings_value_sources(requested.settings_value_origin).model_dump(
            mode="json", exclude_none=False
        ),
    }
    payload["effective_settings_sha256"] = _effective_payload_sha256(payload)
    try:
        return FrustraMPNNEffectiveSettings.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"resolved effective settings are invalid: {exc}") from exc


def _validated_structure_map(
    structure_map: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if not isinstance(structure_map, Mapping):
        raise SourceResolutionError(
            "structure map must be an object",
            location=("structure_map",),
        )
    payload = copy.deepcopy(dict(structure_map))
    try:
        validate_schema("frustrampnn_structure_map_v1", payload)
    except Exception as exc:
        raise SourceResolutionError(
            f"structure map is invalid: {exc}",
            location=("structure_map",),
        ) from exc

    policy = payload["altloc_policy"]
    selected_altloc = policy.removeprefix("blank_or_explicit:")
    if selected_altloc == "<blank>":
        selected_altloc = ""
    allowed_altlocs = {"", selected_altloc}
    if any(row["selected_altloc"] not in allowed_altlocs for row in payload["rows"]):
        raise SourceResolutionError(
            "structure map selected altloc rows disagree with altloc policy",
            location=("structure_map", "rows"),
        )

    entity_chains: dict[tuple[str, str, str, str], set[str]] = {}
    instance_entities: dict[str, set[tuple[str, str, str, str]]] = {}
    chain_entities: dict[str, set[tuple[str, str, str, str]]] = {}
    for row in payload["rows"]:
        entity_key = (
            row["entity_instance_id"],
            row["source_entity_id"] or "",
            row["label_asym_id"] or "",
            row["auth_asym_id"],
        )
        entity_chains.setdefault(entity_key, set()).add(row["pdb_chain_id"])
        instance_entities.setdefault(row["entity_instance_id"], set()).add(entity_key)
        chain_entities.setdefault(row["pdb_chain_id"], set()).add(entity_key)
    if any(len(values) != 1 for values in entity_chains.values()):
        raise SourceResolutionError(
            "structure map has an ambiguous normalized chain for a source entity",
            location=("structure_map", "rows"),
        )
    if any(len(values) != 1 for values in instance_entities.values()):
        raise SourceResolutionError(
            "structure map has mismatched identities for one entity instance",
            location=("structure_map", "rows"),
        )
    if any(len(values) != 1 for values in chain_entities.values()):
        raise SourceResolutionError(
            "structure map has an ambiguous source entity for a normalized chain",
            location=("structure_map", "rows"),
        )
    mapped_positions: dict[str, list[int]] = {}
    for row in payload["rows"]:
        if row["status"] == "mapped":
            mapped_positions.setdefault(row["pdb_chain_id"], []).append(
                row["model_position"]
            )
    if any(
        sorted(positions) != list(range(len(positions)))
        for positions in mapped_positions.values()
    ):
        raise SourceResolutionError(
            "structure map has stale or non-contiguous normalized model positions",
            location=("structure_map", "rows"),
        )
    return payload, selected_altloc


def _row_entity_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["entity_instance_id"]),
        str(row["source_entity_id"] or ""),
        str(row["label_asym_id"] or ""),
        str(row["auth_asym_id"]),
    )


def _row_source_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str, str, int, str, int]:
    return (
        *_row_entity_key(row),
        int(row["auth_seq_id"]),
        str(row["insertion_code"]),
        int(row["sequence_index"]),
    )


def _row_locator_key(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(row["entity_instance_id"]),
        str(row["auth_asym_id"]),
        int(row["auth_seq_id"]),
        str(row["insertion_code"]),
    )


def resolve_effective_settings(
    requested: FrustraMPNNRequestedSettings,
    structure_map: Mapping[str, Any],
) -> FrustraMPNNEffectiveSettings:
    """Resolve typed settings through one exact validated structure-map object."""

    if not isinstance(requested, FrustraMPNNRequestedSettings):
        raise TypeError("requested must be typed FrustraMPNN requested settings")
    validated_map, selected_altloc = _validated_structure_map(structure_map)
    if (
        requested.source_structure.selected_model_number
        != validated_map["selected_source_model"]
    ):
        raise SourceResolutionError(
            "requested source model does not match the structure map",
            location=("source_structure", "selected_model_number"),
        )
    if requested.source_structure.preferred_altloc != selected_altloc:
        raise SourceResolutionError(
            "requested preferred altloc does not match the structure map",
            location=("source_structure", "preferred_altloc"),
        )

    rows = list(validated_map["rows"])
    mapped_rows = [row for row in rows if row["status"] == "mapped"]
    selection = requested.protein_selection
    selected_rows: list[dict[str, Any]] = []
    if selection.mode == "all_protein_entities":
        if len(mapped_rows) != len(rows):
            raise SourceResolutionError(
                "all-protein scope contains excluded or unscoreable residues",
                location=("protein_selection",),
            )
        selected_rows = rows
    elif selection.mode == "selected_entities":
        rows_by_entity: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        rows_by_instance: dict[str, set[tuple[str, str, str, str]]] = {}
        for row in rows:
            key = _row_entity_key(row)
            rows_by_entity.setdefault(key, []).append(row)
            rows_by_instance.setdefault(key[0], set()).add(key)
        for index, entity in enumerate(selection.entities):
            location = ("protein_selection", "entities", index)
            matching_keys = [
                key for key in rows_by_entity if entity.matches_entity_key(key)
            ]
            if len(matching_keys) > 1:
                raise SourceResolutionError(
                    "selected entity identity is ambiguous",
                    location=location,
                )
            matches = rows_by_entity.get(matching_keys[0], []) if matching_keys else []
            if not matches:
                if entity.entity_instance_id in rows_by_instance:
                    raise SourceResolutionError(
                        "selected entity identity is stale or mismatched",
                        location=location,
                    )
                raise SourceResolutionError("selected entity is absent", location=location)
            if any(row["status"] != "mapped" for row in matches):
                raise SourceResolutionError(
                    "selected entity contains excluded or unscoreable residues",
                    location=location,
                )
            selected_rows.extend(matches)
    elif selection.mode == "selected_regions":
        rows_by_entity: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        rows_by_instance: dict[str, set[tuple[str, str, str, str]]] = {}
        for row in rows:
            key = _row_entity_key(row)
            rows_by_entity.setdefault(key, []).append(row)
            rows_by_instance.setdefault(key[0], set()).add(key)
        for index, region in enumerate(selection.regions):
            location = ("protein_selection", "regions", index)
            matching_keys = [
                key for key in rows_by_entity if region.matches_entity_key(key)
            ]
            if len(matching_keys) > 1:
                raise SourceResolutionError(
                    "selected region entity identity is ambiguous",
                    location=location,
                )
            entity_rows = (
                rows_by_entity.get(matching_keys[0], []) if matching_keys else []
            )
            if not entity_rows:
                if region.entity_instance_id in rows_by_instance:
                    raise SourceResolutionError(
                        "selected region entity identity is stale or mismatched",
                        location=location,
                    )
                raise SourceResolutionError(
                    "selected region entity is absent", location=location
                )
            region_rows = [
                row for row in entity_rows
                if region.sequence_start
                <= int(row["sequence_index"])
                <= region.sequence_end
            ]
            observed_positions = {
                int(row["sequence_index"]) for row in region_rows
            }
            span_size = region.sequence_end - region.sequence_start + 1
            if (
                len(region_rows) != span_size
                or len(observed_positions) != span_size
                or min(observed_positions, default=0) != region.sequence_start
                or max(observed_positions, default=0) != region.sequence_end
            ):
                raise SourceResolutionError(
                    "selected region sequence coverage is incomplete",
                    location=location,
                )
            if any(row["status"] != "mapped" for row in region_rows):
                raise SourceResolutionError(
                    "selected region contains excluded or unscoreable residues",
                    location=location,
                )
            selected_rows.extend(region_rows)
    else:
        rows_by_source: dict[
            tuple[str, str, str, str, int, str, int], list[dict[str, Any]]
        ] = {}
        rows_by_locator: dict[
            tuple[str, str, int, str], list[dict[str, Any]]
        ] = {}
        for row in rows:
            rows_by_source.setdefault(_row_source_key(row), []).append(row)
            rows_by_locator.setdefault(_row_locator_key(row), []).append(row)
        for index, residue in enumerate(selection.residues):
            location = ("protein_selection", "residues", index)
            matches = rows_by_source.get(residue.canonical_key(), [])
            if len(matches) > 1:
                raise SourceResolutionError(
                    "selected residue identity is ambiguous",
                    location=location,
                )
            if not matches:
                if residue.locator_key() in rows_by_locator:
                    raise SourceResolutionError(
                        "selected residue identity is stale or mismatched",
                        location=location,
                    )
                raise SourceResolutionError("selected residue is absent", location=location)
            row = matches[0]
            if row["status"] != "mapped":
                raise SourceResolutionError(
                    "selected residue is excluded or not scoreable",
                    location=location,
                )
            selected_rows.append(row)

    if not selected_rows:
        raise SourceResolutionError(
            "effective protein selection has no mapped scoreable residues",
            location=("protein_selection",),
        )

    grouped: dict[
        tuple[str, tuple[str, str, str, str]], list[FrustraMPNNResolvedResidue]
    ] = {}
    for row in selected_rows:
        entity_key = _row_entity_key(row)
        group_key = (str(row["pdb_chain_id"]), entity_key)
        grouped.setdefault(group_key, []).append(
            FrustraMPNNResolvedResidue.model_validate(
                {
                    "entity_instance_id": row["entity_instance_id"],
                    "source_entity_id": row["source_entity_id"],
                    "label_asym_id": row["label_asym_id"],
                    "label_seq_id": row["label_seq_id"],
                    "auth_asym_id": row["auth_asym_id"],
                    "auth_seq_id": row["auth_seq_id"],
                    "insertion_code": row["insertion_code"],
                    "sequence_index": row["sequence_index"],
                    "wt": row["wt"],
                    "pdb_chain_id": row["pdb_chain_id"],
                    "pdb_residue_id": row["pdb_residue_id"],
                    "pdb_insertion_code": row["pdb_insertion_code"],
                    "model_position": row["model_position"],
                    "residue_name": row["residue_name"],
                }
            )
        )

    resolved_chains = tuple(
        FrustraMPNNResolvedChainSelection(
            entity=FrustraMPNNEntitySelector(
                entity_instance_id=entity_key[0],
                source_entity_id=entity_key[1] or None,
                label_asym_id=entity_key[2] or None,
                auth_asym_id=entity_key[3],
            ),
            pdb_chain_id=chain_id,
            residues=tuple(residues),
        )
        for (chain_id, entity_key), residues in sorted(grouped.items())
    )
    resolution_identity = FrustraMPNNResolutionIdentity(
        source_artifact_sha256=validated_map["source_sha256"],
        structure_map_sha256=canonical_sha256(validated_map),
        normalized_pdb_sha256=validated_map["normalized_pdb_sha256"],
    )
    return _build_effective_settings(
        requested,
        resolved_chains=resolved_chains,
        resolution_identity=resolution_identity,
    )


def inspect_structure_map(structure_map: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded selectable projection from one validated structure map."""

    validated_map, selected_altloc = _validated_structure_map(structure_map)
    mapped_rows = [row for row in validated_map["rows"] if row["status"] == "mapped"]
    entities: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    residues: list[dict[str, Any]] = []
    for row in mapped_rows:
        entity_payload = {
            "entity_instance_id": row["entity_instance_id"],
            "source_entity_id": row["source_entity_id"],
            "label_asym_id": row["label_asym_id"],
            "auth_asym_id": row["auth_asym_id"],
            "pdb_chain_id": row["pdb_chain_id"],
        }
        entity_key = (
            str(row["pdb_chain_id"]),
            str(row["entity_instance_id"]),
            str(row["source_entity_id"] or ""),
            str(row["label_asym_id"] or ""),
            str(row["auth_asym_id"]),
        )
        entities[entity_key] = entity_payload
        residues.append(
            {
                "entity_instance_id": row["entity_instance_id"],
                "source_entity_id": row["source_entity_id"],
                "label_asym_id": row["label_asym_id"],
                "auth_asym_id": row["auth_asym_id"],
                "auth_seq_id": row["auth_seq_id"],
                "insertion_code": row["insertion_code"],
                "sequence_index": row["sequence_index"],
                "wt": row["wt"],
            }
        )
    residues.sort(
        key=lambda row: (
            next(
                item["pdb_chain_id"]
                for item in entities.values()
                if item["entity_instance_id"] == row["entity_instance_id"]
            ),
            row["sequence_index"],
            row["auth_seq_id"],
            row["insertion_code"],
        )
    )
    return {
        "source_models": [validated_map["selected_source_model"]],
        "selected_source_model": validated_map["selected_source_model"],
        "observed_altlocs": sorted(
            {str(row["selected_altloc"]) for row in validated_map["rows"]}
        ),
        "selected_altloc": selected_altloc,
        "protein_entities": [entities[key] for key in sorted(entities)],
        "mapped_residues": residues,
    }


__all__ = [
    "FrustraMPNNClassificationPolicy",
    "FrustraMPNNEffectiveSettings",
    "FrustraMPNNEntitySelector",
    "FrustraMPNNProteinSelection",
    "FrustraMPNNRequestedSettings",
    "FrustraMPNNResidueSelector",
    "FrustraMPNNResolutionIdentity",
    "FrustraMPNNResolvedChainSelection",
    "FrustraMPNNResolvedResidue",
    "FrustraMPNNSettingsValueSources",
    "FrustraMPNNSourceStructureSettings",
    "RequestedSettingsPayloadError",
    "SourceResolutionError",
    "classification_policy_sha256",
    "default_settings",
    "effective_settings_sha256",
    "load_capability_inventory",
    "inspect_structure_map",
    "requested_settings_sha256",
    "resolve_effective_settings",
    "runtime_identity_sha256",
    "settings_value_sources",
    "validate_complete_requested_settings",
    "validate_persisted_requested_settings",
]
