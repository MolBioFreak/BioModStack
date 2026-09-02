"""Closed canonical authority for restriction-digest save request fingerprints."""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal, Mapping

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ClosedReceiptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SaveRevisionSourceReceipt(_ClosedReceiptModel):
    kind: Literal["molecular_revision"]
    sequence_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision_id: Annotated[str, Field(min_length=1, max_length=128)]
    expected_content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    topology: Literal["linear", "circular"] | None = None


class SaveCatalogReceipt(_ClosedReceiptModel):
    catalog_id: Annotated[str, Field(min_length=1, max_length=128)]
    expected_catalog_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RestrictionDigestSaveReceipt(_ClosedReceiptModel):
    """Every normalized input to the save-request idempotency fingerprint."""

    schema_: Literal["bms.molbio.restriction-digest-save-request.v1"] = Field(alias="schema")
    source: SaveRevisionSourceReceipt
    catalog: SaveCatalogReceipt
    enzyme_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    simulation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255, pattern=r".*\S.*")]
    persistence_mode: Literal["operation_only", "operation_and_fragments"]
    fragment_name_prefix: Annotated[
        str | None, Field(default=None, min_length=1, max_length=128, pattern=r".*\S.*")
    ]

    @field_validator("enzyme_ids")
    @classmethod
    def validate_enzyme_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("enzyme IDs must be unique bounded nonempty strings")
        return value


def normalized_save_request(value: Mapping[str, Any]) -> RestrictionDigestSaveReceipt:
    """Normalize only the optional fragment prefix, then validate the closed request."""

    normalized = dict(value)
    enzyme_ids = normalized.get("enzyme_ids")
    if isinstance(enzyme_ids, (list, tuple)):
        normalized["enzyme_ids"] = tuple(enzyme_ids)
    prefix = normalized.get("fragment_name_prefix")
    if isinstance(prefix, str):
        normalized["fragment_name_prefix"] = prefix.strip()
    return RestrictionDigestSaveReceipt.model_validate(normalized, strict=True)


def canonical_save_request_receipt(value: Mapping[str, Any]) -> str:
    model = normalized_save_request(value)
    return rfc8785.dumps(model.model_dump(mode="json", by_alias=True)).decode("utf-8")


def load_canonical_save_request_receipt(receipt_text: object) -> RestrictionDigestSaveReceipt:
    if not isinstance(receipt_text, str):
        raise ValueError("save request receipt is not text")
    model = RestrictionDigestSaveReceipt.model_validate_json(receipt_text, strict=True)
    if rfc8785.dumps(model.model_dump(mode="json", by_alias=True)).decode("utf-8") != receipt_text:
        raise ValueError("save request receipt is not canonical")
    return model


def save_request_fingerprint(receipt: RestrictionDigestSaveReceipt | str) -> str:
    if isinstance(receipt, str):
        model = load_canonical_save_request_receipt(receipt)
    else:
        model = receipt
    canonical = rfc8785.dumps(model.model_dump(mode="json", by_alias=True))
    return hashlib.sha256(canonical).hexdigest()


def validate_persisted_save_request_receipt(
    receipt_text: object, idempotency_key: object, request_fingerprint: object,
) -> int:
    """SQLite UDF: validate canonical bytes and bind their exact key and SHA-256."""

    try:
        model = load_canonical_save_request_receipt(receipt_text)
        return int(
            isinstance(idempotency_key, str)
            and model.idempotency_key == idempotency_key
            and isinstance(request_fingerprint, str)
            and save_request_fingerprint(model) == request_fingerprint
        )
    except Exception:
        return 0
