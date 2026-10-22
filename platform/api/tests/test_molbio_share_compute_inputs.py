"""Malformed guest JSON remains a validation error, not a server error."""
import json

import pytest

from share_server import compute as C


@pytest.mark.parametrize("field,value", [("topology", []), ("topology", {}), ("topology", None), ("name", "bad\ud800name")])
@pytest.mark.parametrize("operation", [C.analyze, C.simulate_digest])
def test_invalid_guest_source_is_422(field, value, operation):
    catalog = C._view()
    enzyme = next(row.enzyme_id for row in catalog.ordered_records if row.canonical_name == "EcoRI")
    request = {"schema": "bms.molbio.restriction-digest-simulation-request.v1" if operation is C.simulate_digest else "bms.molbio.restriction-analysis-request.v1",
        "source": {"kind": "inline_dna", "name": "test", "dna": "AAAAGAATTCTTTT", "topology": "linear"},
        "catalog": {"catalog_id": catalog.catalog_id, "expected_catalog_sha256": catalog.content_sha256}}
    if operation is C.analyze:
        request["scope"] = {"mode": "explicit", "enzyme_ids": [enzyme]}
    else:
        request["enzyme_ids"] = [enzyme]
    request["source"][field] = value
    with pytest.raises(C.ComputeError) as error:
        operation(request)
    assert (error.value.status, error.value.code) == (422, "invalid_request")


def test_valid_unicode_name_remains_supported():
    sequence, topology, receipt = C._inline_source({"kind": "inline_dna", "name": "DNA β", "dna": "ACGT", "topology": "linear"})
    assert (sequence, topology, receipt["name"]) == ("ACGT", "linear", "DNA β")
    assert json.loads(json.dumps(receipt, ensure_ascii=False).encode("utf-8"))["name"] == "DNA β"
