from services.ont_minknow_client import normalize_flow_cell_info


class _Wrapper:
    """Mimics minknow_api MessageWrapper: unknown fields raise TypeError."""

    has_flow_cell = True
    flow_cell_id = "FAX00001"
    product_code = "FLO-MIN114"

    def __getattr__(self, name):
        raise TypeError(f"object has no attribute {name!r}")


def test_flow_cell_present_when_release_lacks_optional_fields():
    info = normalize_flow_cell_info(_Wrapper())
    assert info["present"] is True
    assert info["product_code"] == "FLO-MIN114"
    assert info["sample_rate"] is None
